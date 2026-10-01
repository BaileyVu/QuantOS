"""Application orchestration for the separate event-driven HFT paper path."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import time
from typing import Any, Awaitable, Callable

from quantos.domain.alpha.hft import (
    HftIntentAction,
    HftSide,
    VampOrderFlowAlpha,
)
from quantos.domain.evaluation.hft import HftEvaluation
from quantos.domain.execution.hft_paper import HftPaperAccount
from quantos.domain.features.hft import HftFeatureEngine, HftFeatureError, HftFeatures
from quantos.domain.market_data.futures import FuturesRuleError, FuturesSymbolRules, parse_usdm_exchange_info
from quantos.domain.market_data.hft import (
    AggregateTrade,
    BookTicker,
    DepthDelta,
    DepthSnapshot,
    HftBookError,
    L2OrderBook,
    MarkPriceEvent,
)
from quantos.domain.risk.hft import HftRiskEngine, HftRiskState, cost_hurdle
HftEvent = DepthDelta | BookTicker | AggregateTrade | MarkPriceEvent


class HftPaperRuntimeError(RuntimeError):
    pass


class _DurationExpired(Exception):
    pass


class _BootstrapFailure(Exception):
    def __init__(self, stage: str) -> None:
        super().__init__(stage)
        self.stage = stage


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _queue_item(
    queue: asyncio.Queue, timeout: float
) -> HftEvent | BaseException:
    return await asyncio.wait_for(queue.get(), timeout=timeout)


async def _snapshot(client: Any, symbol: str) -> DepthSnapshot:
    return await asyncio.to_thread(client.depth_snapshot, symbol)


def select_hft_contract(
    exchange_info: dict, config: Any
) -> FuturesSymbolRules:
    errors = []
    for symbol in (config.preferred_symbol, config.fallback_symbol):
        try:
            rules = parse_usdm_exchange_info(exchange_info, symbol)
            if rules.status != "TRADING":
                raise FuturesRuleError(f"{symbol} is not TRADING")
            reference_price = min(
                max(Decimal("100000"), rules.minimum_price),
                rules.maximum_price,
            )
            rules.validate_market_order(
                rules.round_quantity_up(
                    max(
                        rules.minimum_quantity,
                        rules.minimum_notional / reference_price,
                    )
                ),
                reference_price,
            )
            return rules
        except (FuturesRuleError, ArithmeticError) as error:
            errors.append(f"{symbol}: {error}")
    raise HftPaperRuntimeError(
        "neither configured HFT contract is eligible: " + "; ".join(errors)
    )


def hft_state_identity(config: Any, rules: FuturesSymbolRules) -> str:
    payload = json.dumps(
        {
            "config": repr(config),
            "rules": {key: str(value) for key, value in asdict(rules).items()},
            "version": "hft-paper-v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()


class HftPaperEngine:
    def __init__(
        self,
        config: Any,
        rules: FuturesSymbolRules,
        account: HftPaperAccount,
        evaluation: HftEvaluation,
    ) -> None:
        self.config = config
        self.rules = rules
        self.book = L2OrderBook(rules.symbol)
        self.features = HftFeatureEngine(config.feature)
        self.alpha = VampOrderFlowAlpha(rules.symbol, config.alpha)
        self.risk = HftRiskEngine(config.risk, config.fees[rules.symbol], rules)
        self.account = account
        self.evaluation = evaluation
        self.last_features: HftFeatures | None = None
        self.last_mark_price: Decimal | None = None
        self.last_funding_rate = Decimal("0")
        self.next_funding_time: datetime | None = None
        self.applied_funding_times: set[datetime] = set()
        self.reconstruction_count = 0
        self.adverse_fills = 0

    def bootstrap(
        self, snapshot: DepthSnapshot, buffered: tuple[DepthDelta, ...]
    ) -> int:
        self.account.cancel("book_reconstruction")
        applied = self.book.bootstrap(snapshot, buffered)
        self.reconstruction_count += 1
        return applied

    def on_event(self, event: HftEvent, now: datetime) -> dict:
        started = datetime.now(timezone.utc)
        try:
            if isinstance(event, DepthDelta):
                self.book.apply(event)
                result = self._on_book(now)
            elif isinstance(event, AggregateTrade):
                self.features.on_trade(event)
                fill = self.account.on_trade(event)
                result = {"event": "aggregate_trade", "fill": fill}
                if fill is not None and self.last_features is not None:
                    self.evaluation.register_passive_fill(
                        fill,
                        alpha_bps=self.last_features.alpha_bps,
                        obi_z=self.last_features.standardized_obi,
                        volatility_bps=self.last_features.realized_volatility_bps,
                    )
            elif isinstance(event, BookTicker):
                if event.bid_price >= event.ask_price:
                    raise HftBookError("crossed bookTicker")
                result = {"event": "book_ticker"}
            elif isinstance(event, MarkPriceEvent):
                if (
                    self.account.inventory is not None
                    and self.next_funding_time is not None
                    and event.exchange_time >= self.next_funding_time
                    and self.next_funding_time not in self.applied_funding_times
                ):
                    quantity = self.account.inventory.quantity
                    payment = (
                        -abs(quantity) * event.mark_price
                        * self.last_funding_rate
                        * (Decimal("1") if quantity > 0 else Decimal("-1"))
                    )
                    self.account.apply_funding(payment)
                    self.applied_funding_times.add(self.next_funding_time)
                self.last_mark_price = event.mark_price
                self.last_funding_rate = event.funding_rate
                self.next_funding_time = event.next_funding_time
                result = {"event": "mark_price"}
            else:
                raise TypeError("unsupported HFT event")
        except (HftBookError, HftFeatureError) as error:
            self.fail_closed(str(error), now)
            raise
        completed = datetime.now(timezone.utc)
        feed_ms = Decimal(str((event.received_at - event.exchange_time).total_seconds() * 1000))
        processing_ms = Decimal(str((completed - started).total_seconds() * 1000))
        self.evaluation.record_latency(feed_ms, max(processing_ms, Decimal("0")), Decimal("0"))
        mark = self.last_mark_price
        if mark is None and self.book.bids and self.book.asks:
            mark = (self.book.best_bid + self.book.best_ask) / Decimal("2")
        if mark is not None:
            self.evaluation.record_equity(self.account.marked_equity(mark), now)
        return result

    def _on_book(self, now: datetime) -> dict:
        self.book.require_fresh(now, self.config.maximum_staleness)
        current = self.account.working_order
        if current is not None:
            age_ms = Decimal(str((now - current.placed_at).total_seconds() * 1000))
            if age_ms > Decimal(self.config.alpha.maximum_quote_age_ms):
                self.account.cancel("quote_age")
                current = None
        features = self.features.compute(
            self.book, now, self.account.inventory_quantity
        )
        self.last_features = features
        observations = self.evaluation.on_mid(now, features.mid_price)
        for observation in observations:
            if observation.horizon_seconds == 1:
                if observation.markout_bps < 0:
                    self.adverse_fills += 1
                else:
                    self.adverse_fills = 0
        equity = self.account.marked_equity(features.mid_price)
        mark_to_market_loss = max(
            Decimal("0"), self.account.starting_equity - equity
        )
        if (
            self.account.inventory is not None
            and mark_to_market_loss
            >= equity * self.config.risk.maximum_account_loss_fraction
        ):
            price = (
                self.book.best_bid
                if self.account.inventory.quantity > 0
                else self.book.best_ask
            )
            fill = self.account.emergency_exit(price, now, "account_loss_breaker")
            self.evaluation.kill_switch_events += 1
            return {"event": "safety_exit", "fill": fill}
        hurdle = cost_hurdle(
            self.config.fees[self.rules.symbol], features, self.config.risk
        )
        intent = self.alpha.decide(
            features,
            hurdle.admission_hurdle_bps,
            self.account.inventory_quantity,
            current.side if current is not None else None,
        )
        if intent.action is HftIntentAction.CANCEL:
            self.account.cancel(intent.rationale)
            return {"event": "quote_cancelled", "reason": intent.rationale}
        if intent.action is HftIntentAction.SAFETY_EXIT:
            if self.account.inventory is not None:
                price = (
                    self.book.best_bid
                    if self.account.inventory.quantity > 0
                    else self.book.best_ask
                )
                fill = self.account.emergency_exit(price, now, intent.rationale)
                return {"event": "safety_exit", "fill": fill}
        if intent.action is HftIntentAction.MAKER_EXIT:
            if current is None and intent.side is not None and intent.price is not None:
                ack = now + timedelta(milliseconds=self.config.simulated_acknowledgement_ms)
                order = self.account.place_exit(
                    intent.side, intent.price, self.book, now, ack
                )
                self.evaluation.record_latency(
                    features.feed_latency_ms, Decimal("0"),
                    Decimal(self.config.simulated_acknowledgement_ms),
                )
                return {"event": "maker_exit_quoted", "order": order}
        if intent.action is HftIntentAction.QUOTE:
            if current is not None:
                if current.side is intent.side and current.price == intent.price:
                    return {"event": "quote_resting", "order": current}
                self.account.cancel("reprice")
            state = HftRiskState(
                starting_equity=self.account.starting_equity,
                realized_pnl=self.account.balance - self.account.starting_equity,
                mark_to_market_loss=max(
                    Decimal("0"),
                    self.account.starting_equity
                    - self.account.marked_equity(features.mid_price),
                ),
                consecutive_adverse_fills=self.adverse_fills,
                kill_switch=False,
            )
            decision = self.risk.evaluate(
                intent, features, state,
                self.account.marked_equity(features.mid_price),
                self.account.inventory_quantity,
            )
            if decision.approved and intent.side is not None and intent.price is not None:
                ack = now + timedelta(milliseconds=self.config.simulated_acknowledgement_ms)
                order = self.account.place_entry(
                    decision, intent.side, intent.price, self.book, now, ack
                )
                self.evaluation.record_latency(
                    features.feed_latency_ms, Decimal("0"),
                    Decimal(self.config.simulated_acknowledgement_ms),
                )
                return {
                    "event": "quote_submitted",
                    "order": order,
                    "hurdle": decision.hurdle,
                }
            return {
                "event": "risk_rejected",
                "reason": decision.reason,
                "hurdle": decision.hurdle,
            }
        return {"event": "hft_hold", "reason": intent.rationale}

    def fail_closed(self, reason: str, now: datetime) -> None:
        self.account.cancel(reason)
        if self.account.inventory is not None and self.book.bids and self.book.asks:
            price = (
                self.book.best_bid
                if self.account.inventory.quantity > 0
                else self.book.best_ask
            )
            self.account.emergency_exit(price, now, reason)
            self.evaluation.kill_switch_events += 1
        self.book.invalidate(reason)


async def run_hft_paper_session(
    *,
    config: Any,
    rules: FuturesSymbolRules,
    account: HftPaperAccount,
    evaluation: HftEvaluation,
    state_store: Any,
    identity: str,
    runtime: dict,
    cycles: int,
    duration_seconds: int,
    client: Any,
    stream_factory: Callable[[], Any],
    recorder: Any,
    emit: Callable[[dict], None] = lambda value: None,
    monotonic_clock: Callable[[], float] = time.monotonic,
    utc_clock: Callable[[], datetime] = _utc_now,
    wait_for_item: Callable[
        [asyncio.Queue, float], Awaitable[HftEvent | BaseException]
    ] = _queue_item,
    snapshot_loader: Callable[[Any, str], Awaitable[DepthSnapshot]] = _snapshot,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict:
    if cycles < 0 or duration_seconds < 0:
        raise ValueError("cycles and duration_seconds must be non-negative")
    engine = HftPaperEngine(config, rules, account, evaluation)
    started_monotonic = monotonic_clock()
    deadline = (
        started_monotonic + duration_seconds
        if duration_seconds > 0 else None
    )
    heartbeat_seconds = config.heartbeat_interval.total_seconds()
    next_heartbeat = started_monotonic + heartbeat_seconds
    read_timeout = config.websocket_read_timeout.total_seconds()
    processed = 0
    reconnect_count = 0
    sequence_gap_count = 0
    events_received = {
        "depth": 0,
        "trade": 0,
        "bookticker": 0,
        "mark_price": 0,
    }
    last_seen: dict[str, float | None] = {
        key: None for key in events_received
    }
    current_producer: asyncio.Task | None = None
    accepting_new_decisions = True
    shutdown_reason = "normal_completion"

    def remaining_seconds(now: float | None = None) -> float | None:
        if deadline is None:
            return None
        value = deadline - (monotonic_clock() if now is None else now)
        return max(value, 0.0)

    def deadline_expired(now: float | None = None) -> bool:
        return (
            deadline is not None
            and (monotonic_clock() if now is None else now) >= deadline
        )

    def observe(event: HftEvent) -> None:
        observed = monotonic_clock()
        if isinstance(event, DepthDelta):
            key = "depth"
        elif isinstance(event, AggregateTrade):
            key = "trade"
        elif isinstance(event, BookTicker):
            key = "bookticker"
        else:
            key = "mark_price"
        events_received[key] += 1
        last_seen[key] = observed

    def age_ms(key: str, now: float) -> Decimal | None:
        observed = last_seen[key]
        if observed is None:
            return None
        return Decimal(str(max((now - observed) * 1000, 0.0)))

    def heartbeat_payload(now: float) -> dict:
        bid = ask = spread = None
        if engine.book.valid and engine.book.bids and engine.book.asks:
            bid, ask = engine.book.best_bid, engine.book.best_ask
            mid = (bid + ask) / Decimal("2")
            spread = (ask - bid) / mid * Decimal("10000")
        quote = account.working_order
        inventory = account.inventory
        mark = engine.last_mark_price
        if mark is None and bid is not None and ask is not None:
            mark = (bid + ask) / Decimal("2")
        equity = (
            account.marked_equity(mark) if mark is not None else account.balance
        )
        return {
            "event": "hft_heartbeat",
            "symbol": rules.symbol,
            "uptime_seconds": Decimal(str(max(now - started_monotonic, 0.0))),
            "book_ready": engine.book.valid,
            "last_depth_event_age_ms": age_ms("depth", now),
            "last_trade_event_age_ms": age_ms("trade", now),
            "last_bookticker_event_age_ms": age_ms("bookticker", now),
            "current_best_bid": bid,
            "current_best_ask": ask,
            "spread_bps": spread,
            "current_vamp": (
                engine.last_features.vamp_price
                if engine.last_features is not None else None
            ),
            "alpha_bps": (
                engine.last_features.alpha_bps
                if engine.last_features is not None else None
            ),
            "quote_state": (
                {
                    "order_id": quote.order_id,
                    "side": quote.side,
                    "price": quote.price,
                    "remaining_quantity": quote.remaining_quantity,
                    "queue_ahead": quote.queue_ahead,
                    "role": quote.role,
                }
                if quote is not None else None
            ),
            "inventory": (
                {
                    "quantity": inventory.quantity,
                    "entry_price": inventory.entry_price,
                    "opened_at": inventory.opened_at,
                }
                if inventory is not None else None
            ),
            "equity": equity,
            "sequence_gap_count": sequence_gap_count,
            "reconnect_count": reconnect_count,
            "events_received": dict(events_received),
            "quotes_submitted": account.quotes_submitted,
            "fills": len(account.fills),
            "remaining_duration_seconds": remaining_seconds(now),
        }

    def check_control() -> None:
        nonlocal next_heartbeat
        now = monotonic_clock()
        if deadline_expired(now):
            raise _DurationExpired
        if now >= next_heartbeat:
            emit(heartbeat_payload(now))
            next_heartbeat = now + heartbeat_seconds

    def bounded_timeout(stage_deadline: float | None = None) -> float:
        now = monotonic_clock()
        candidates = [read_timeout, max(next_heartbeat - now, 0.001)]
        remaining = remaining_seconds(now)
        if remaining is not None:
            candidates.append(max(remaining, 0.001))
        if stage_deadline is not None:
            candidates.append(max(stage_deadline - now, 0.001))
        return max(min(candidates), 0.001)

    async def await_stage(
        task: asyncio.Task,
        stage: str,
        stage_timeout: float,
        producer: asyncio.Task,
    ):
        stage_deadline = monotonic_clock() + stage_timeout
        try:
            while not task.done():
                check_control()
                if monotonic_clock() >= stage_deadline:
                    raise _BootstrapFailure(stage)
                if producer.done():
                    raise _BootstrapFailure(stage)
                await asyncio.wait(
                    {task}, timeout=bounded_timeout(stage_deadline)
                )
            check_control()
            if monotonic_clock() >= stage_deadline:
                raise _BootstrapFailure(stage)
            try:
                return task.result()
            except (TimeoutError, asyncio.TimeoutError) as error:
                raise _BootstrapFailure(stage) from error
            except asyncio.CancelledError:
                raise
            except BaseException as error:
                raise _BootstrapFailure(stage) from error
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def next_item(
        queue: asyncio.Queue,
        *,
        stage: str,
        stage_deadline: float | None = None,
    ) -> HftEvent | BaseException:
        timeout_reported = False
        while True:
            check_control()
            if stage_deadline is not None and monotonic_clock() >= stage_deadline:
                raise _BootstrapFailure(stage)
            try:
                item = await wait_for_item(
                    queue, bounded_timeout(stage_deadline)
                )
            except (TimeoutError, asyncio.TimeoutError):
                if not timeout_reported:
                    emit({
                        "event": "websocket_timeout",
                        "stage": stage,
                        "timeout_seconds": read_timeout,
                    })
                    timeout_reported = True
                check_control()
                if (
                    stage == "event_loop"
                    and last_seen["depth"] is not None
                    and monotonic_clock() - last_seen["depth"]
                    > config.maximum_staleness.total_seconds()
                ):
                    raise HftPaperRuntimeError("depth stream is stale")
                continue
            check_control()
            return item

    async def stop_producer(producer: asyncio.Task | None) -> None:
        if producer is None:
            return
        producer.cancel()
        done, pending = await asyncio.wait(
            {producer}, timeout=config.shutdown_timeout.total_seconds()
        )
        for task in pending:
            task.cancel()
        for task in done:
            try:
                task.result()
            except BaseException:
                pass

    try:
        while (
            (cycles == 0 or processed < cycles)
            and not deadline_expired()
        ):
            queue: asyncio.Queue[HftEvent | BaseException] = asyncio.Queue(
                maxsize=4096
            )
            stream = stream_factory()

            async def produce() -> None:
                try:
                    async for event in stream.events(rules.symbol):
                        await queue.put(event)
                except asyncio.CancelledError:
                    raise
                except BaseException as error:
                    await queue.put(error)

            producer = asyncio.create_task(produce())
            current_producer = producer
            try:
                if hasattr(stream, "wait_public_ready"):
                    ready_task = asyncio.create_task(
                        stream.wait_public_ready()
                    )
                    await await_stage(
                        ready_task,
                        "websocket_public_connect",
                        config.bootstrap_timeout.total_seconds(),
                        producer,
                    )
                if hasattr(stream, "wait_market_ready"):
                    market_ready_task = asyncio.create_task(
                        stream.wait_market_ready()
                    )
                    await await_stage(
                        market_ready_task,
                        "websocket_market_connect",
                        config.bootstrap_timeout.total_seconds(),
                        producer,
                    )
                emit({
                    "event": "depth_bootstrap_started",
                    "symbol": rules.symbol,
                })
                snapshot_task = asyncio.create_task(
                    snapshot_loader(client, rules.symbol)
                )
                snapshot = await await_stage(
                    snapshot_task,
                    "depth_snapshot",
                    config.snapshot_timeout.total_seconds(),
                    producer,
                )
                emit({
                    "event": "depth_snapshot_received",
                    "symbol": rules.symbol,
                    "last_update_id": snapshot.last_update_id,
                })
                recorder.append(snapshot, utc_clock())
                buffered: list[DepthDelta] = []
                bridge_deadline = (
                    monotonic_clock()
                    + config.bootstrap_timeout.total_seconds()
                )
                while True:
                    item = await next_item(
                        queue,
                        stage="depth_buffer_reconcile",
                        stage_deadline=bridge_deadline,
                    )
                    if isinstance(item, BaseException):
                        raise _BootstrapFailure(
                            "depth_buffer_reconcile"
                        ) from item
                    observe(item)
                    recorder.append(item, utc_clock())
                    if isinstance(item, DepthDelta):
                        buffered.append(item)
                        if (
                            item.first_update_id <= snapshot.last_update_id
                            <= item.final_update_id
                        ):
                            break
                applied = engine.bootstrap(snapshot, tuple(buffered))
                emit({
                    "event": "depth_buffer_reconciled",
                    "symbol": rules.symbol,
                    "buffered_depth_events": len(buffered),
                    "applied_depth_events": applied,
                    "last_update_id": engine.book.last_update_id,
                })
                emit({
                    "event": "order_book_ready",
                    "symbol": rules.symbol,
                    "last_update_id": engine.book.last_update_id,
                    "best_bid": engine.book.best_bid,
                    "best_ask": engine.book.best_ask,
                })
                runtime.update({
                    "book_valid": True,
                    "last_update_id": engine.book.last_update_id,
                    "reconstruction_identity": (
                        f"{rules.symbol}:{snapshot.last_update_id}"
                    ),
                })

                while cycles == 0 or processed < cycles:
                    item = await next_item(queue, stage="event_loop")
                    if isinstance(item, BaseException):
                        raise item
                    observe(item)
                    processing_time = utc_clock()
                    recorder.append(item, processing_time)
                    result = engine.on_event(item, processing_time)
                    processed += 1
                    runtime.update({
                        "last_event_at": item.received_at,
                        "last_update_id": engine.book.last_update_id,
                        "book_valid": engine.book.valid,
                        "alpha_state": (
                            asdict(engine.last_features)
                            if engine.last_features else {}
                        ),
                    })
                    if processed % config.checkpoint_events == 0:
                        recorder.checkpoint(
                            rules.symbol,
                            engine.book.last_update_id or 0,
                            engine.book.bid_levels(20),
                            engine.book.ask_levels(20),
                            processing_time,
                        )
                        state_store.save(
                            account, evaluation, runtime, identity,
                            processing_time,
                        )
                    emit(result)
                    if (
                        last_seen["depth"] is not None
                        and monotonic_clock() - last_seen["depth"]
                        > config.maximum_staleness.total_seconds()
                    ):
                        raise HftPaperRuntimeError("depth stream is stale")
            except _DurationExpired:
                shutdown_reason = "duration_expired"
                break
            except asyncio.CancelledError:
                shutdown_reason = "cancelled"
                raise
            except _BootstrapFailure as error:
                engine.fail_closed(
                    f"bootstrap_timeout:{error.stage}", utc_clock()
                )
                emit({
                    "event": "hft_bootstrap_failed",
                    "stage": error.stage,
                    "symbol": rules.symbol,
                    "error": (
                        str(error.__cause__)
                        if error.__cause__ is not None else "timeout"
                    ),
                })
                reconnect_count += 1
            except BaseException as error:
                if isinstance(error, HftBookError) and "sequence gap" in str(error):
                    sequence_gap_count += 1
                engine.fail_closed("reconnect_ambiguity", utc_clock())
                emit({
                    "event": "websocket_disconnected",
                    "stage": "runtime",
                    "error": str(error),
                })
                reconnect_count += 1
            finally:
                await stop_producer(producer)
                current_producer = None
            if deadline_expired():
                shutdown_reason = "duration_expired"
                break
            if cycles != 0 and processed >= cycles:
                shutdown_reason = "finite_cycle_completion"
                break
            emit({
                "event": "reconnecting",
                "symbol": rules.symbol,
                "reconnect_count": reconnect_count,
            })
            delay = config.reconnect_delay.total_seconds()
            remaining = remaining_seconds()
            if remaining is not None:
                delay = min(delay, remaining)
            if delay > 0:
                await sleep(delay)
            if deadline_expired():
                shutdown_reason = "duration_expired"
                break
            check_control()
        if deadline_expired():
            shutdown_reason = "duration_expired"
    except asyncio.CancelledError:
        shutdown_reason = "cancelled"
        raise
    finally:
        accepting_new_decisions = False
        runtime["accepting_new_decisions"] = accepting_new_decisions
        runtime["shutdown_reason"] = shutdown_reason
        runtime["book_valid"] = False
        runtime["reconnect_count"] = reconnect_count
        runtime["sequence_gap_count"] = sequence_gap_count
        runtime["events_received"] = dict(events_received)
        account.cancel("runtime_shutdown")
        recorder.flush()
        state_store.save(
            account, evaluation, runtime, identity, utc_clock()
        )
        await stop_producer(current_producer)
        emit({
            "event": "hft_runtime_shutdown_complete",
            "symbol": rules.symbol,
            "reason": shutdown_reason,
        })
    elapsed = Decimal(str(max(
        monotonic_clock() - started_monotonic, 0.0
    )))
    return evaluation.report(account, elapsed)

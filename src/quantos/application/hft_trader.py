"""Application orchestration for the separate event-driven HFT paper path."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from typing import Any, Callable

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
    stream: Any,
    recorder: Any,
    emit: Callable[[dict], None] = lambda value: None,
) -> dict:
    if cycles < 0 or duration_seconds < 0:
        raise ValueError("cycles and duration_seconds must be non-negative")
    engine = HftPaperEngine(config, rules, account, evaluation)
    started = datetime.now(timezone.utc)
    processed = 0
    queue: asyncio.Queue[HftEvent | BaseException] = asyncio.Queue(maxsize=4096)

    async def produce() -> None:
        try:
            async for event in stream.events(rules.symbol):
                await queue.put(event)
        except BaseException as error:
            await queue.put(error)

    producer = asyncio.create_task(produce())
    try:
        snapshot = await asyncio.to_thread(client.depth_snapshot, rules.symbol)
        recorder.append(snapshot, datetime.now(timezone.utc))
        buffered: list[DepthDelta] = []
        pending: list[HftEvent] = []
        while True:
            item = await asyncio.wait_for(
                queue.get(), timeout=config.maximum_staleness.total_seconds()
            )
            if isinstance(item, BaseException):
                raise item
            recorder.append(item, datetime.now(timezone.utc))
            pending.append(item)
            if isinstance(item, DepthDelta):
                buffered.append(item)
                if (
                    item.first_update_id <= snapshot.last_update_id
                    <= item.final_update_id
                ):
                    break
        engine.bootstrap(snapshot, tuple(buffered))
        runtime.update({
            "book_valid": True,
            "last_update_id": engine.book.last_update_id,
            "reconstruction_identity": f"{rules.symbol}:{snapshot.last_update_id}",
        })
        # Pre-bootstrap trades/tickers are recorded but cannot authorize a fill
        # or quote because no contemporaneously valid local book existed.
        deadline = (
            started + timedelta(seconds=duration_seconds)
            if duration_seconds else None
        )
        while cycles == 0 or processed < cycles:
            now = datetime.now(timezone.utc)
            if deadline is not None and now >= deadline:
                break
            timeout = config.maximum_staleness.total_seconds()
            if deadline is not None:
                timeout = min(timeout, max((deadline - now).total_seconds(), 0.001))
            try:
                item = await asyncio.wait_for(queue.get(), timeout=timeout)
            except TimeoutError as error:
                if deadline is not None and datetime.now(timezone.utc) >= deadline:
                    break
                engine.fail_closed("stale_stream", datetime.now(timezone.utc))
                raise HftPaperRuntimeError("HFT stream became stale") from error
            if isinstance(item, BaseException):
                engine.fail_closed("reconnect_ambiguity", datetime.now(timezone.utc))
                raise item
            processing_time = datetime.now(timezone.utc)
            recorder.append(item, processing_time)
            result = engine.on_event(item, processing_time)
            processed += 1
            runtime.update({
                "last_event_at": item.received_at,
                "last_update_id": engine.book.last_update_id,
                "book_valid": engine.book.valid,
                "alpha_state": (
                    asdict(engine.last_features) if engine.last_features else {}
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
                state_store.save(account, evaluation, runtime, identity, processing_time)
            emit(result)
    except (HftBookError, HftFeatureError, asyncio.TimeoutError):
        engine.fail_closed("market_data_failure", datetime.now(timezone.utc))
        raise
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        recorder.flush()
        now = datetime.now(timezone.utc)
        runtime["book_valid"] = False
        state_store.save(account, evaluation, runtime, identity, now)
    elapsed = Decimal(str((datetime.now(timezone.utc) - started).total_seconds()))
    return evaluation.report(account, elapsed)

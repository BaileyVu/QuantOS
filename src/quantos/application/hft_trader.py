"""Application orchestration for the separate event-driven HFT paper path."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import time
import traceback
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
from quantos.domain.market_data.hft_clock import (
    HftClockCalibration,
    HftClockHealth,
    HftClockMonitor,
    HftClockSample,
    HftFeedLatency,
    robust_clock_calibration,
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


class _RecalibrationDue(Exception):
    pass


class _ClockCalibrationFailed(Exception):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _queue_item(
    queue: asyncio.Queue, timeout: float
) -> HftEvent | BaseException:
    return await asyncio.wait_for(queue.get(), timeout=timeout)


async def _snapshot(client: Any, symbol: str) -> DepthSnapshot:
    return await asyncio.to_thread(client.depth_snapshot, symbol)


async def _clock_calibration(
    client: Any,
    sample_count: int,
    lowest_rtt_sample_count: int,
    utc_clock: Callable[[], datetime],
    monotonic_clock: Callable[[], float],
) -> HftClockCalibration:
    samples = []
    for _ in range(sample_count):
        local_send_wall = utc_clock()
        local_send_monotonic = monotonic_clock()
        server_wall = await asyncio.to_thread(client.server_time)
        local_receive_monotonic = monotonic_clock()
        local_receive_wall = utc_clock()
        samples.append(HftClockSample(
            local_send_wall=local_send_wall,
            local_receive_wall=local_receive_wall,
            server_wall=server_wall,
            local_send_monotonic=local_send_monotonic,
            local_receive_monotonic=local_receive_monotonic,
        ))
    return robust_clock_calibration(
        tuple(samples), lowest_rtt_sample_count
    )


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

    def on_event(
        self,
        event: HftEvent,
        now: datetime,
        *,
        monotonic_now: Decimal,
        latency: HftFeedLatency,
        allow_quoting: bool,
    ) -> dict:
        try:
            if isinstance(event, DepthDelta):
                self.book.apply(event)
                result = self._on_book(
                    now,
                    monotonic_now,
                    latency,
                    allow_quoting,
                )
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
            if "stale" in str(error).lower():
                self.evaluation.record_stale_candidate()
            self.fail_closed(str(error), now)
            raise
        mark = self.last_mark_price
        if mark is None and self.book.bids and self.book.asks:
            mark = (self.book.best_bid + self.book.best_ask) / Decimal("2")
        if mark is not None:
            self.evaluation.record_equity(self.account.marked_equity(mark), now)
        return result

    def _on_book(
        self,
        now: datetime,
        monotonic_now: Decimal,
        latency: HftFeedLatency,
        allow_quoting: bool,
    ) -> dict:
        monotonic_ns = int(monotonic_now * Decimal("1000000000"))
        self.book.require_fresh_monotonic(
            monotonic_ns, self.config.maximum_staleness
        )
        current = self.account.working_order
        if current is not None:
            age_ms = (
                (monotonic_now - current.submitted_monotonic)
                * Decimal("1000")
                if current.submitted_monotonic is not None
                else Decimal(str(
                    (now - current.placed_at).total_seconds() * 1000
                ))
            )
            if age_ms > Decimal(self.config.alpha.maximum_quote_age_ms):
                self.account.cancel("quote_age")
                current = None
        received_monotonic_ns = self.book.last_received_monotonic_ns
        if received_monotonic_ns is None:
            raise HftFeatureError("monotonic receive timestamp is missing")
        event_age_ms = (
            monotonic_now
            - Decimal(received_monotonic_ns) / Decimal("1000000000")
        ) * Decimal("1000")
        features = self.features.compute(
            self.book,
            now,
            self.account.inventory_quantity,
            feed_latency_ms=latency.observed_feed_latency_ms,
            event_age_ms=max(event_age_ms, Decimal("0")),
            now_monotonic_ns=monotonic_ns,
        )
        self.last_features = features
        observations = self.evaluation.on_mid(
            now, features.mid_price, monotonic_now
        )
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
            fill = self.account.emergency_exit(
                price, now, "account_loss_breaker", monotonic_now
            )
            self.evaluation.kill_switch_events += 1
            return {"event": "safety_exit", "fill": fill}
        if not allow_quoting:
            self.account.cancel("clock_health_not_healthy")
            return {
                "event": "hft_clock_hold",
                "clock_health": "UNSAFE_OR_DEGRADED",
            }
        hurdle = cost_hurdle(
            self.config.fees[self.rules.symbol], features, self.config.risk
        )
        intent = self.alpha.decide(
            features,
            hurdle.normal_hurdle_bps,
            self.account.inventory_quantity,
            current.side if current is not None else None,
        )
        economic_hurdle_passed = (
            abs(features.alpha_bps) > hurdle.normal_hurdle_bps
        )

        def record_candidate(
            *,
            quote_passed: bool,
            rejection_reason: str | None,
            funnel_rejection: str | None = None,
        ) -> None:
            self.evaluation.record_quote_evaluation(
                features,
                hurdle,
                quote_passed=quote_passed,
                rejection_reason=rejection_reason,
                economic_hurdle_passed=economic_hurdle_passed,
                funnel_rejection=funnel_rejection,
            )

        if intent.action is HftIntentAction.CANCEL:
            reason = intent.rationale
            funnel_rejection = None
            if reason == "regime_filter":
                if features.spread_bps > self.config.alpha.maximum_spread_bps:
                    reason = "spread_above_maximum"
                    funnel_rejection = "spread"
                else:
                    reason = "volatility_above_maximum"
                    funnel_rejection = "volatility"
            elif reason == "alpha_below_hurdle":
                if not economic_hurdle_passed:
                    reason = "alpha_below_normal_hurdle"
                    funnel_rejection = reason
                else:
                    reason = "alpha_confirmation_failed"
                    funnel_rejection = "alpha_confirmation"
            record_candidate(
                quote_passed=False,
                rejection_reason=reason,
                funnel_rejection=funnel_rejection,
            )
            cancelled = self.account.cancel(intent.rationale)
            if cancelled is None:
                return {"event": "hft_hold", "reason": intent.rationale}
            return {"event": "quote_cancelled", "reason": intent.rationale}
        if intent.action is HftIntentAction.SAFETY_EXIT:
            record_candidate(
                quote_passed=False,
                rejection_reason=intent.rationale,
                funnel_rejection="inventory",
            )
            if self.account.inventory is not None:
                price = (
                    self.book.best_bid
                    if self.account.inventory.quantity > 0
                    else self.book.best_ask
                )
                fill = self.account.emergency_exit(
                    price, now, intent.rationale, monotonic_now
                )
                return {"event": "safety_exit", "fill": fill}
        if intent.action is HftIntentAction.MAKER_EXIT:
            record_candidate(
                quote_passed=False,
                rejection_reason=intent.rationale,
                funnel_rejection="inventory",
            )
            if current is None and intent.side is not None and intent.price is not None:
                ack = now + timedelta(milliseconds=self.config.simulated_acknowledgement_ms)
                order = self.account.place_exit(
                    intent.side,
                    intent.price,
                    self.book,
                    now,
                    ack,
                    monotonic_now,
                    monotonic_now + Decimal(
                        self.config.simulated_acknowledgement_ms
                    ) / Decimal("1000"),
                )
                self.evaluation.record_acknowledgement_latency(
                    Decimal(self.config.simulated_acknowledgement_ms),
                )
                return {"event": "maker_exit_quoted", "order": order}
        if intent.action is HftIntentAction.QUOTE:
            if current is not None:
                if current.side is intent.side and current.price == intent.price:
                    record_candidate(
                        quote_passed=True,
                        rejection_reason=None,
                    )
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
                    decision,
                    intent.side,
                    intent.price,
                    self.book,
                    now,
                    ack,
                    monotonic_now,
                    monotonic_now + Decimal(
                        self.config.simulated_acknowledgement_ms
                    ) / Decimal("1000"),
                )
                self.evaluation.record_acknowledgement_latency(
                    Decimal(self.config.simulated_acknowledgement_ms),
                )
                record_candidate(
                    quote_passed=True,
                    rejection_reason=None,
                )
                return {
                    "event": "quote_submitted",
                    "order": order,
                    "hurdle": decision.hurdle,
                }
            funnel_rejection = (
                "inventory" if "inventory" in decision.reason else "risk"
            )
            record_candidate(
                quote_passed=False,
                rejection_reason=decision.reason,
                funnel_rejection=funnel_rejection,
            )
            return {
                "event": "risk_rejected",
                "reason": decision.reason,
                "hurdle": decision.hurdle,
            }
        if intent.rationale == "alpha_below_hurdle":
            reason = (
                "alpha_below_normal_hurdle"
                if not economic_hurdle_passed
                else "alpha_confirmation_failed"
            )
            funnel_rejection = (
                "alpha_below_normal_hurdle"
                if not economic_hurdle_passed
                else "alpha_confirmation"
            )
        elif intent.rationale == "inventory_open":
            reason = intent.rationale
            funnel_rejection = "inventory"
        else:
            reason = intent.rationale
            funnel_rejection = None
        record_candidate(
            quote_passed=False,
            rejection_reason=reason,
            funnel_rejection=funnel_rejection,
        )
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
    calibration_loader: Callable[
        [Any, int, int, Callable[[], datetime], Callable[[], float]],
        Awaitable[HftClockCalibration],
    ] = _clock_calibration,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    application_queue_maxsize: int = 4096,
) -> dict:
    if cycles < 0 or duration_seconds < 0:
        raise ValueError("cycles and duration_seconds must be non-negative")
    if application_queue_maxsize < 1:
        raise ValueError("application queue size must be positive")
    engine = HftPaperEngine(config, rules, account, evaluation)
    started_monotonic = monotonic_clock()
    deadline = (
        started_monotonic + duration_seconds
        if duration_seconds > 0 else None
    )
    heartbeat_seconds = config.heartbeat_interval.total_seconds()
    next_heartbeat = started_monotonic + heartbeat_seconds
    read_timeout = config.websocket_read_timeout.total_seconds()
    clock_monitor = HftClockMonitor(
        config.clock_negative_latency_tolerance_ms,
        config.clock_excessive_negative_limit,
        config.clock_recalibration_interval,
    )
    processed = 0
    reconnect_count = 0
    transport_reconnect_count = 0
    sequence_gap_count = 0
    backpressure_overflow_count = 0
    stale_trading_disarm_count = 0
    recovery_without_reconnect_count = 0
    stale_trading_disarmed = False
    consumer_lag_ms: Decimal | None = None
    application_queue_high_water = 0
    active_application_queue: asyncio.Queue | None = None
    active_stream: Any | None = None
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
    transport_stale_seconds = max(
        read_timeout * 3,
        config.maximum_staleness.total_seconds() * 2,
    )

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
        received_monotonic_ns = getattr(
            event, "received_monotonic_ns", None
        )
        observed = (
            received_monotonic_ns / 1_000_000_000
            if received_monotonic_ns is not None
            else monotonic_clock()
        )
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
        payload = {
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
            "actual_transport_reconnect_count": transport_reconnect_count,
            "consumer_lag_ms": consumer_lag_ms,
            "application_queue_current": (
                active_application_queue.qsize()
                if active_application_queue is not None else 0
            ),
            "application_queue_high_water": application_queue_high_water,
            "backpressure_overflow_count": backpressure_overflow_count,
            "stale_trading_disarm_count": stale_trading_disarm_count,
            "recovery_without_reconnect_count": recovery_without_reconnect_count,
            "events_received": dict(events_received),
            "quotes_submitted": account.quotes_submitted,
            "fills": len(account.fills),
            "remaining_duration_seconds": remaining_seconds(now),
            "quote_economics": evaluation.quote_economics(account),
        }
        if active_stream is not None and hasattr(active_stream, "telemetry"):
            payload.update(active_stream.telemetry(int(now * 1_000_000_000)))
        else:
            payload.update({
                "socket_receive_age_by_route_ms": {
                    "public": None, "market": None,
                },
                "depth_socket_receive_age_ms": None,
                "stream_queue_current": 0,
                "stream_queue_high_water": 0,
            })
        payload.update(clock_monitor.payload(now))
        return payload

    def producer_failure(producer: asyncio.Task | None) -> None:
        if producer is None or not producer.done():
            return
        if producer.cancelled():
            raise asyncio.CancelledError
        error = producer.exception()
        if error is not None:
            raise error
        raise HftPaperRuntimeError("market_data_stream_ended")

    def update_feed_safety(now: float) -> None:
        nonlocal stale_trading_disarmed
        nonlocal stale_trading_disarm_count
        nonlocal recovery_without_reconnect_count
        usable_depth_age = age_ms("depth", now)
        is_usable_stale = (
            usable_depth_age is not None
            and usable_depth_age
            > Decimal(str(
                config.maximum_staleness.total_seconds() * 1000
            ))
        )
        if is_usable_stale and not stale_trading_disarmed:
            stale_trading_disarmed = True
            stale_trading_disarm_count += 1
            account.cancel("stale_usable_market_data")
            emit({
                "event": "hft_trading_disarmed",
                "symbol": rules.symbol,
                "reason": "stale_usable_market_data",
                "usable_depth_age_ms": usable_depth_age,
                "consumer_lag_ms": consumer_lag_ms,
            })
        elif not is_usable_stale and stale_trading_disarmed:
            stale_trading_disarmed = False
            recovery_without_reconnect_count += 1
            emit({
                "event": "hft_trading_rearmed",
                "symbol": rules.symbol,
                "reason": "fresh_sequenced_depth_recovered",
            })
        if active_stream is None or not hasattr(active_stream, "telemetry"):
            return
        telemetry = active_stream.telemetry(int(now * 1_000_000_000))
        depth_socket_age = telemetry.get("depth_socket_receive_age_ms")
        if (
            depth_socket_age is not None
            and depth_socket_age
            > Decimal(str(transport_stale_seconds * 1000))
        ):
            raise HftPaperRuntimeError("transport_ingest_stale:depth")

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
        if clock_monitor.calibration is not None:
            candidates.append(max(
                clock_monitor.seconds_until_calibration(now), 0.001
            ))
        return max(min(candidates), 0.001)

    async def await_stage(
        task: asyncio.Task,
        stage: str,
        stage_timeout: float,
        producer: asyncio.Task | None,
    ):
        stage_deadline = monotonic_clock() + stage_timeout
        try:
            while not task.done():
                check_control()
                if monotonic_clock() >= stage_deadline:
                    raise _BootstrapFailure(stage)
                if producer is not None and producer.done():
                    try:
                        producer_failure(producer)
                    except BaseException as error:
                        raise _BootstrapFailure(stage) from error
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
        producer: asyncio.Task | None = None,
    ) -> HftEvent | BaseException:
        timeout_reported = False
        while True:
            check_control()
            producer_failure(producer)
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
                producer_failure(producer)
                if stage == "event_loop":
                    update_feed_safety(monotonic_clock())
                if (
                    stage == "event_loop"
                    and clock_monitor.calibration_due(monotonic_clock())
                ):
                    raise _RecalibrationDue
                continue
            check_control()
            return item

    async def stop_producer(producer: asyncio.Task | None) -> None:
        if producer is None:
            return

        producer.cancel()

        done, pending = await asyncio.wait(
            {producer},
            timeout=config.shutdown_timeout.total_seconds(),
        )

        for task in pending:
            task.cancel()

        if pending:
            await asyncio.gather(
                *pending,
                return_exceptions=True,
            )

        for task in done:
            try:
                task.result()
            except BaseException:
                pass

    async def perform_clock_calibration(reason: str) -> bool:
        emit({
            "event": "exchange_clock_calibration_started",
            "symbol": rules.symbol,
            "reason": reason,
            "sample_count": config.clock_calibration_samples,
        })
        task = asyncio.create_task(calibration_loader(
            client,
            config.clock_calibration_samples,
            config.clock_low_rtt_samples,
            utc_clock,
            monotonic_clock,
        ))
        try:
            calibration = await await_stage(
                task,
                "exchange_clock_calibration",
                config.clock_calibration_timeout.total_seconds(),
                None,
            )
        except _BootstrapFailure as error:
            clock_monitor.mark_calibration_failed()
            account.cancel("clock_calibration_failed")
            runtime["clock"] = clock_monitor.payload(monotonic_clock())
            emit({
                "event": "exchange_clock_calibration_failed",
                "symbol": rules.symbol,
                "reason": reason,
                "stage": error.stage,
                "clock_health": clock_monitor.health.value,
                "error": (
                    str(error.__cause__)
                    if error.__cause__ is not None else "timeout"
                ),
            })
            return False
        clock_monitor.apply_calibration(calibration)
        runtime["clock"] = {
            **clock_monitor.payload(monotonic_clock()),
            **calibration.payload(),
        }
        emit({
            "event": "exchange_clock_calibrated",
            "symbol": rules.symbol,
            "reason": reason,
            "clock_health": clock_monitor.health.value,
            **calibration.payload(),
        })
        return True

    try:
        if not await perform_clock_calibration("startup"):
            shutdown_reason = "clock_calibration_failed"
        while (
            clock_monitor.calibration is not None
            and clock_monitor.health is not HftClockHealth.UNSAFE
            and (cycles == 0 or processed < cycles)
            and not deadline_expired()
        ):
            queue: asyncio.Queue[HftEvent | BaseException] = asyncio.Queue(
                maxsize=application_queue_maxsize
            )
            stream = stream_factory()
            active_application_queue = queue
            active_stream = stream

            async def produce() -> None:
                nonlocal application_queue_high_water
                try:
                    async for event in stream.events(rules.symbol):
                        try:
                            queue.put_nowait(event)
                        except asyncio.QueueFull as error:
                            raise HftPaperRuntimeError(
                                "application_queue_overflow"
                            ) from error
                        application_queue_high_water = max(
                            application_queue_high_water,
                            queue.qsize(),
                        )
                except asyncio.CancelledError:
                    raise

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
                await asyncio.to_thread(
                    recorder.append,
                    snapshot,
                    utc_clock(),
                    int(monotonic_clock() * 1_000_000_000),
                )
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
                        producer=producer,
                    )
                    if isinstance(item, BaseException):
                        raise _BootstrapFailure(
                            "depth_buffer_reconcile"
                        ) from item
                    observe(item)
                    await asyncio.to_thread(
                        recorder.append,
                        item,
                        utc_clock(),
                        int(monotonic_clock() * 1_000_000_000),
                    )
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
                    if clock_monitor.calibration_due(monotonic_clock()):
                        reason = (
                            "negative_latency"
                            if clock_monitor.recalibration_requested
                            else "periodic"
                        )
                        if not await perform_clock_calibration(reason):
                            shutdown_reason = "clock_calibration_failed"
                            raise _ClockCalibrationFailed
                    try:
                        item = await next_item(
                            queue,
                            stage="event_loop",
                            producer=producer,
                        )
                    except _RecalibrationDue:
                        continue
                    if isinstance(item, BaseException):
                        raise item
                    observe(item)
                    received_monotonic_ns = getattr(
                        item, "received_monotonic_ns", None
                    )
                    if received_monotonic_ns is None:
                        consumer_lag_ms = Decimal("0")
                    else:
                        consumer_lag_ms = Decimal(str(max(
                            (
                                monotonic_clock()
                                - received_monotonic_ns / 1_000_000_000
                            ) * 1000,
                            0.0,
                        )))
                    update_feed_safety(monotonic_clock())
                    previous_clock_health = clock_monitor.health
                    latency = clock_monitor.observe(
                        item.exchange_time, item.received_at
                    )
                    if clock_monitor.health is not previous_clock_health:
                        emit({
                            "event": "hft_clock_health_changed",
                            "symbol": rules.symbol,
                            "previous_clock_health": (
                                previous_clock_health.value
                            ),
                            "clock_health": clock_monitor.health.value,
                            "corrected_feed_latency_ms": (
                                latency.corrected_signed_latency_ms
                            ),
                        })
                    if not clock_monitor.quoting_allowed:
                        account.cancel("clock_health_not_healthy")
                    processing_started_monotonic = monotonic_clock()
                    processing_monotonic = Decimal(str(
                        processing_started_monotonic
                    ))
                    processing_time = utc_clock()
                    await asyncio.to_thread(
                        recorder.append,
                        item,
                        processing_time,
                        int(
                            processing_started_monotonic
                            * 1_000_000_000
                        ),
                    )
                    result = engine.on_event(
                        item,
                        processing_time,
                        monotonic_now=processing_monotonic,
                        latency=latency,
                        allow_quoting=(
                            clock_monitor.quoting_allowed
                            and not stale_trading_disarmed
                        ),
                    )
                    processing_completed_monotonic = monotonic_clock()
                    processing_latency_ms = Decimal(str(max(
                        (
                            processing_completed_monotonic
                            - processing_started_monotonic
                        ) * 1000,
                        0.0,
                    )))
                    evaluation.record_event_latency(
                        latency.observed_feed_latency_ms,
                        processing_latency_ms,
                    )
                    processed += 1
                    runtime.update({
                        "last_event_at": item.received_at,
                        "last_update_id": engine.book.last_update_id,
                        "book_valid": engine.book.valid,
                        "alpha_state": (
                            asdict(engine.last_features)
                            if engine.last_features else {}
                        ),
                        "clock": clock_monitor.payload(
                            processing_completed_monotonic
                        ) | (
                            clock_monitor.calibration.payload()
                            if clock_monitor.calibration is not None else {}
                        ),
                    })
                    if processed % config.checkpoint_events == 0:
                        await asyncio.to_thread(
                            recorder.checkpoint,
                            rules.symbol,
                            engine.book.last_update_id or 0,
                            engine.book.bid_levels(20),
                            engine.book.ask_levels(20),
                            processing_time,
                        )
                        await asyncio.to_thread(
                            state_store.save,
                            account, evaluation, runtime, identity,
                            processing_time,
                        )
                    await asyncio.to_thread(emit, result)
                    update_feed_safety(monotonic_clock())
            except _ClockCalibrationFailed:
                shutdown_reason = "clock_calibration_failed"
                break
            except _DurationExpired:
                shutdown_reason = "duration_expired"
                break
            except asyncio.CancelledError:
                shutdown_reason = "cancelled"
                raise
            except _BootstrapFailure as error:
                cause_text = (
                    str(error.__cause__)
                    if error.__cause__ is not None else "timeout"
                )
                if "queue_overflow" in cause_text:
                    backpressure_overflow_count += 1
                    bootstrap_reason = "backpressure_overflow"
                elif (
                    "disconnected ambiguously" in cause_text
                    or "stream_ended" in cause_text
                ):
                    transport_reconnect_count += 1
                    bootstrap_reason = "transport_failure"
                else:
                    bootstrap_reason = f"bootstrap_timeout:{error.stage}"
                engine.fail_closed(
                    bootstrap_reason, utc_clock()
                )
                emit({
                    "event": "hft_bootstrap_failed",
                    "stage": error.stage,
                    "symbol": rules.symbol,
                    "error": cause_text,
                    "reason": bootstrap_reason,
                })
                reconnect_count += 1
            except BaseException as error:
                error_text = str(error)
                if isinstance(error, HftBookError) and "sequence gap" in error_text:
                    sequence_gap_count += 1
                if "queue_overflow" in error_text:
                    backpressure_overflow_count += 1
                    failure_reason = "backpressure_overflow"
                elif (
                    "transport_ingest_stale" in error_text
                    or "disconnected ambiguously" in error_text
                    or "market_data_stream_ended" in error_text
                ):
                    transport_reconnect_count += 1
                    failure_reason = "transport_failure"
                else:
                    failure_reason = "reconnect_ambiguity"
                engine.fail_closed(failure_reason, utc_clock())
                emit({
                    "event": "websocket_disconnected",
                    "stage": "runtime",
                    "error": error_text,
                    "error_type": type(error).__name__,
                    "error_repr": repr(error),
                    "traceback": "".join(
                        traceback.format_exception(
                            type(error),
                            error,
                            error.__traceback__,
                        )
                    )[-6000:],
                    "reason": failure_reason,
                })
                reconnect_count += 1
            finally:
                await stop_producer(producer)
                current_producer = None
                active_application_queue = None
                active_stream = None
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
    except _DurationExpired:
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
        runtime["actual_transport_reconnect_count"] = transport_reconnect_count
        runtime["sequence_gap_count"] = sequence_gap_count
        runtime["consumer_lag_ms"] = consumer_lag_ms
        runtime["application_queue_high_water"] = application_queue_high_water
        runtime["backpressure_overflow_count"] = backpressure_overflow_count
        runtime["stale_trading_disarm_count"] = stale_trading_disarm_count
        runtime["recovery_without_reconnect_count"] = (
            recovery_without_reconnect_count
        )
        runtime["events_received"] = dict(events_received)
        runtime["clock"] = clock_monitor.payload(monotonic_clock()) | (
            clock_monitor.calibration.payload()
            if clock_monitor.calibration is not None else {}
        )
        account.cancel("runtime_shutdown")
        await asyncio.to_thread(recorder.flush)
        await asyncio.to_thread(
            state_store.save,
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

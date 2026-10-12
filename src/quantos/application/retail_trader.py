"""Public-feed orchestration for isolated retail directional paper trading."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import time
from typing import Any, Callable, Protocol

from quantos.domain.alpha.hft import HftSide
from quantos.domain.alpha.retail import RetailDirectionalAlpha, ProtectedRetailSignal, RetailSignal
from quantos.domain.alpha.retail_edge import RetailEdgeAlpha
from quantos.domain.alpha.reclaim import LiquidityReclaimAlpha
from quantos.domain.features.intelligence import IntelligenceFeatureEngine
from quantos.domain.evaluation.retail import RetailEvaluation
from quantos.domain.execution.retail_paper import RetailPaperAccount
from quantos.domain.features.hft import HftFeatureEngine, HftFeatureError
from quantos.domain.market_data.hft import (
    AggregateTrade, BookTicker, DepthDelta, HftBookError,
    L2OrderBook, MarkPriceEvent,
)
from quantos.domain.market_data.hft_clock import HftClockMonitor
from quantos.domain.risk.retail import RetailRiskEngine, RetailRiskPolicy, FullEquityRiskPolicy


class RetailAlphaProvider(Protocol):
    def reset(self) -> None: ...
    def observe(self, features, observed_ns: int) -> ProtectedRetailSignal | None: ...


class RetailPaperEngine:
    def __init__(
        self, config: Any, rules: Any, account: RetailPaperAccount,
        evaluation: RetailEvaluation, policy: RetailRiskPolicy,
        processing_clock_ns: Callable[[], int] = time.monotonic_ns,
        unified: bool = False,
        reclaim: bool = False,
        reliability: bool = False,
        alpha_provider: RetailAlphaProvider | None = None,
    ) -> None:
        if alpha_provider is not None and (not isinstance(policy, FullEquityRiskPolicy) or not callable(getattr(alpha_provider, "reset", None))):
            raise ValueError("Alpha integration requires full-equity profile and reset contract")
        self.alpha_provider = alpha_provider
        self.reclaim = reclaim
        self.reliability = reliability
        self.last_disarm_reason = None
        self.unified = unified or reclaim
        self.reclaim_alpha = LiquidityReclaimAlpha()
        self.intelligence = IntelligenceFeatureEngine()
        self.edge_alpha = RetailEdgeAlpha()
        self.decision_records: list[dict] = []
        self.confirmed_signal_ids: set[str] = set()
        self.config, self.rules = config, rules
        self.account, self.evaluation, self.policy = account, evaluation, policy
        self.book = L2OrderBook(rules.symbol)
        self.features = HftFeatureEngine(config.feature)
        self.alpha = RetailDirectionalAlpha()
        self.risk = RetailRiskEngine(policy, config.fees[rules.symbol], rules)
        self.last_trade_id: int | None = None
        self.funding_received_ns: int | None = None
        self.last_received_ns: int | None = None
        self.disarmed = True
        self.processing_clock_ns = processing_clock_ns
        self.force_exit_on_recovery = account.position is not None

    def bootstrap(self, snapshot: Any, buffered: tuple[DepthDelta, ...]) -> None:
        if self.alpha_provider is not None:
            self.alpha_provider.reset()
        self.account.cancel_pending("book_reconstruction", snapshot.received_at)
        self.book.bootstrap(snapshot, buffered)
        self.intelligence.invalidate()
        self.edge_alpha = RetailEdgeAlpha()
        self.reclaim_alpha = LiquidityReclaimAlpha()
        self.last_trade_id = None
        self.funding_received_ns = None
        self.features = HftFeatureEngine(self.config.feature)
        self.alpha = RetailDirectionalAlpha()
        self.last_received_ns = self.book.last_received_monotonic_ns
        self.disarmed = False
        self.last_disarm_reason = None

    def disarm(self, reason: str, timestamp: datetime) -> None:
        if not self.disarmed:
            self.evaluation.disarms[reason] += 1
        self.disarmed = True
        self.last_disarm_reason = reason
        if self.evaluation.reliability:
            self.evaluation.reliability.close_window()
        if self.account.position is not None:
            self.force_exit_on_recovery = True
        self.account.cancel_pending(reason, timestamp)

    def on_event(
        self, event: Any, *, feed_latency_ms: Decimal,
        clock_healthy: bool,
    ) -> dict | None:
        self.evaluation.funnel["received"] += 1
        if feed_latency_ms < 0 or feed_latency_ms > self.policy.maximum_event_age_ms:
            self.disarm("stale_or_ambiguous_feed_latency", event.received_at)
            return None
        now_ns = event.received_monotonic_ns
        if now_ns is None or (
            self.last_received_ns is not None and now_ns < self.last_received_ns
        ):
            self.disarm("ambiguous_event_clock", event.received_at)
            return None
        if event.symbol != self.rules.symbol:
            self.disarm("wrong_target_symbol", event.received_at)
            return None
        self.last_received_ns = now_ns
        self.account.observe_day(event.received_at)
        self.account.settle_funding(event.exchange_time)
        if isinstance(event, AggregateTrade):
            self.evaluation.events_received["trade"] += 1
            if self.last_trade_id is not None and event.aggregate_trade_id <= self.last_trade_id:
                self.evaluation.rejections["duplicate_or_regressed_trade"] += 1
                return None
            self.last_trade_id = event.aggregate_trade_id
            self.features.on_trade(event)
        elif isinstance(event, BookTicker):
            self.evaluation.events_received["bookticker"] += 1
        elif isinstance(event, MarkPriceEvent):
            self.evaluation.events_received["mark_price"] += 1
            self.account.observe_funding(event)
            self.funding_received_ns = now_ns
        elif isinstance(event, DepthDelta):
            self.evaluation.events_received["depth"] += 1
            try:
                applied = self.book.apply(event)
            except HftBookError:
                self.disarm("invalid_depth_sequence", event.received_at)
                raise
            if not applied:
                return None
        else:
            raise TypeError("unsupported retail market event")
        try:
            self.book.require_fresh_monotonic(
                now_ns, self.config.maximum_staleness
            )
        except HftBookError:
            self.disarm("stale_or_invalid_book", event.received_at)
            return None
        if not clock_healthy:
            self.disarm("clock_health_not_healthy", event.received_at)
            return None
        if self.disarmed:
            return None
        if not isinstance(event, DepthDelta):
            if isinstance(self.policy, FullEquityRiskPolicy) and isinstance(event, AggregateTrade):
                self.account.observe_protective_price(event.price, event.received_at)
            return None
        self.evaluation.funnel["valid_book"] += 1
        age_ms = Decimal(self.processing_clock_ns() - now_ns) / Decimal("1000000")
        if age_ms < 0 or age_ms > self.policy.maximum_event_age_ms:
            self.disarm("stale_processing", event.received_at)
            return None
        funding_missing = self.unified and (self.funding_received_ns is None or now_ns - self.funding_received_ns > 5_000_000_000 or self.account.funding_due is None)
        if self.account.pending is not None and self.account.pending.kind == "ENTRY" and (self.account.risk_off or funding_missing):
            self.account.cancel_pending("risk_or_funding_invalidated", event.received_at)
        if funding_missing and self.account.position is not None:
            self.force_exit_on_recovery = True
        if isinstance(self.policy, FullEquityRiskPolicy) and self.account.position is not None:
            decision_ns = self.processing_clock_ns()
            self.account.manage_protection(self.book, event.received_at, now_ns, decision_ns,
                force=self.force_exit_on_recovery or funding_missing)
            fill = self.account.on_depth(self.book, event.received_at, now_ns)
            self.evaluation.observe_equity(self.account, self.account.marked_equity(self.book.best_bid, self.book.best_ask))
            if self.account.position is None:
                self.force_exit_on_recovery = False
            return {"event": "retail_fill", "fill": fill} if fill else None
        if self.account.position is not None and self.account.pending is not None and self.account.pending.kind == "ENTRY":
            # A partially filled entry must never block an urgent risk exit.
            position = self.account.position
            mark = self.book.best_bid if position.side is HftSide.BUY else self.book.best_ask
            direction = 1 if position.side is HftSide.BUY else -1
            if self.reclaim_exit_reason() or self.force_exit_on_recovery or self.account.risk_off or (mark / position.entry_price - 1) * 10000 * direction <= -self.policy.stop_loss_bps:
                self.account.cancel_pending("entry_invalidated_while_partial", event.received_at)
        try:
            features = self.features.compute(
                self.book, event.received_at,
                self.account.position.quantity if self.account.position else Decimal("0"),
                feed_latency_ms=feed_latency_ms,
                event_age_ms=age_ms,
                now_monotonic_ns=now_ns,
            )
            self.evaluation.funnel["complete_features"] += 1
            signal = self.alpha.observe(features, now_ns)
            self.evaluation.alpha_reasons[self.alpha.last_reason] += 1
            self.evaluation.funnel["qos03_raw_candidates" if self.unified else "raw_candidates"] += int(self.alpha.raw_candidate)
            self.evaluation.funnel["qos03_confirmations" if self.unified else "confirmations"] += int(signal is not None)
            if self.unified:
                choices = ((self.reclaim_alpha.observe(features, now_ns),) if self.reclaim else
                           self.edge_alpha.observe(features, self.intelligence.compute(now_ns), now_ns))
                if self.evaluation.reliability and self.reclaim:
                    self.evaluation.reliability.observe_feature(
                        now_ns, self.reclaim_alpha.features.last_range_bps)
                if self.alpha_provider is not None:
                    from quantos.domain.alpha.retail_edge import CandidateDecision
                    proposed = self.alpha_provider.observe(features, now_ns)
                    if proposed is not None and not isinstance(proposed, RetailSignal):
                        raise ValueError("invalid Alpha decision contract")
                    choices = (CandidateDecision(proposed.strategy_id if proposed else "integration", "proposed" if proposed else "no_trade", proposed is not None, proposed),)
                signals = []
                for choice in choices:
                    self.evaluation.candidate_reasons[f"{choice.strategy}:{choice.reason}"] += 1
                    self.evaluation.candidate_funnel[f"{choice.strategy}:evaluated"] += 1
                    self.evaluation.candidate_funnel[f"{choice.strategy}:raw"] += int(choice.raw_candidate)
                    self.evaluation.candidate_funnel[f"{choice.strategy}:confirmed"] += int(choice.signal is not None)
                    record = {"strategy": choice.strategy, "reason": choice.reason,
                        "book_update_id": features.book_update_id, "available_ns": now_ns,
                        "signal": choice.signal}
                    if self.reliability and self.reclaim:
                        record["prior_range_bps"] = self.reclaim_alpha.features.last_range_bps
                    self.decision_records.append(record)
                    self.evaluation.funnel["raw_candidates"] += int(choice.raw_candidate)
                    self.evaluation.funnel["confirmations"] += int(choice.signal is not None)
                    if choice.signal is not None:
                        signals.append(choice.signal)
                        self.confirmed_signal_ids.add(choice.signal.signal_id)
                signal = max(signals, key=lambda s: (s.expected_move_bps - s.uncertainty_bps, s.strategy_id), default=None)
        except (HftFeatureError, ValueError):
            self.disarm("invalid_features_or_signal_clock", event.received_at)
            return None
        if self.unified and self.account.pending is not None and self.account.pending.kind == "ENTRY":
            pending = self.account.pending
            reconfirmed = next((c.signal for c in choices if c.signal is not None
                               and c.signal.strategy_id == pending.strategy_id
                               and c.signal.side is pending.side
                               and (not self.reclaim or c.signal.signal_id == pending.signal_id)), None)
            # Revalidate the same original intent, including any partial inventory;
            # this cannot submit or grow a second position.
            review = self.risk.evaluate(reconfirmed, features, self.account.balance,
                self.account.peak_equity, False,
                funding_bps=abs(self.account.funding_rate) * 10000,
                daily_start_equity=self.account.daily_start_equity,
                emergency_risk_off=self.account.risk_off)
            committed = pending.remaining + (self.account.position.quantity if self.account.position else Decimal("0"))
            if not review.approved or committed > review.quantity:
                self.account.cancel_pending("alpha_or_risk_invalidated_before_fill", event.received_at)
        fill = self.account.on_depth(self.book, event.received_at, now_ns)
        equity = self.account.marked_equity(features.best_bid, features.best_ask)
        self.evaluation.observe_equity(self.account, equity)
        if equity <= self.account.peak_equity * (1 - self.policy.maximum_drawdown_fraction) or equity <= self.account.daily_start_equity * (1 - self.policy.maximum_daily_loss_fraction):
            self.account.risk_off = True
        if isinstance(self.policy, FullEquityRiskPolicy) and self.account.position is not None:
            return {"event": "retail_fill", "fill": fill} if fill else None
        if fill is not None and fill.kind == "EXIT":
            return {"event": "retail_fill", "fill": fill}
        if self.account.position is not None:
            position = self.account.position
            direction = Decimal("1") if position.side is HftSide.BUY else Decimal("-1")
            mark = features.best_bid if position.side is HftSide.BUY else features.best_ask
            move_bps = (mark - position.entry_price) / position.entry_price * Decimal("10000") * direction
            reason = None
            if self.account.risk_off:
                reason = "account_risk_off"
            elif self.force_exit_on_recovery:
                reason = "invalid_data_recovery"
            elif equity <= self.account.peak_equity * (1 - self.policy.maximum_drawdown_fraction):
                reason = "drawdown_stop"
            elif move_bps <= -self.policy.stop_loss_bps:
                reason = "stop_loss"
            elif self.reclaim_exit_reason() is not None:
                reason = self.reclaim_exit_reason()
            elif now_ns - position.opened_ns >= position.horizon_seconds * 1_000_000_000:
                reason = "horizon_expired"
            elif signal is not None and signal.side is not position.side:
                reason = "signal_reversal"
            if reason is not None and self.account.pending is None:
                decision_ns = self.processing_clock_ns()
                if decision_ns < now_ns:
                    self.disarm("ambiguous_submission_clock", event.received_at)
                    return None
                submitted_at = event.received_at + timedelta(microseconds=(decision_ns - now_ns) // 1000)
                order = self.account.submit_exit(
                    reason, submitted_at, decision_ns, features.book_update_id
                )
                self.force_exit_on_recovery = False
                return {"event": "retail_exit_submitted", "order_id": order.order_id,
                        "signal_id": order.signal_id, "reason": reason}
            return {"event": "retail_fill", "fill": fill} if fill else None
        if self.account.pending is not None:
            return {"event": "retail_fill", "fill": fill} if fill else None
        if signal is None:
            return None
        self.evaluation.signal_candidates += 1
        if self.unified and (self.funding_received_ns is None or now_ns - self.funding_received_ns > 5_000_000_000 or self.account.funding_due is None):
            self.evaluation.rejections["missing_or_stale_funding"] += 1
            return {"event": "retail_rejected", "reason": "missing_or_stale_funding", "signal_id": signal.signal_id}
        decision = self.risk.evaluate(
            signal, features, self.account.balance, self.account.peak_equity, False,
            funding_bps=abs(self.account.funding_rate) * 10000,
            daily_start_equity=self.account.daily_start_equity,
            emergency_risk_off=self.account.risk_off,
        )
        self.evaluation.candidate_funnel[f"{signal.strategy_id}:selected"] += 1
        self.evaluation.candidate_reasons[f"{signal.strategy_id}:{decision.reason}"] += 1
        self.decision_records.append({"strategy": signal.strategy_id, "signal": signal, "risk": decision})
        if signal.expected_move_bps - signal.uncertainty_bps > decision.hurdle_bps:
            self.evaluation.funnel["cost_admitted"] += 1
        if not decision.approved:
            self.evaluation.rejections[decision.reason] += 1
            return {"event": "retail_rejected", "reason": decision.reason,
                    "signal_id": signal.signal_id, "hurdle_bps": decision.hurdle_bps}
        self.evaluation.funnel["risk_approved"] += 1
        self.evaluation.risk_approved += 1
        decision_ns = self.processing_clock_ns()
        if not signal.observed_ns <= decision_ns < signal.expires_ns:
            self.evaluation.rejections["signal_expired_or_submission_clock_ambiguous"] += 1
            return {"event": "retail_rejected", "signal_id": signal.signal_id,
                    "reason": "signal_expired_or_submission_clock_ambiguous"}
        submitted_at = event.received_at + timedelta(microseconds=(decision_ns - now_ns) // 1000)
        order = self.account.submit_entry(
            decision, signal, submitted_at, decision_ns,
            features.best_ask if signal.side is HftSide.BUY else features.best_bid,
        )
        return {"event": "retail_entry_submitted", "order_id": order.order_id,
                "signal_id": signal.signal_id, "quantity": order.quantity,
                "hurdle_bps": decision.hurdle_bps}


    def reclaim_exit_reason(self):
        position = self.account.position
        if not self.reclaim or position is None or position.strategy_id != "Q05-LR-v1":
            return None
        context = next((row for row in reversed(self.account.lifecycle)
                        if row["event"] == "alpha_context" and row["signal_id"] == position.signal_id), None)
        if context is None:
            return "missing_reclaim_context"
        direction = 1 if position.side is HftSide.BUY else -1
        mark = self.book.best_bid if direction > 0 else self.book.best_ask
        if (mark - context["invalidation_price"]) * direction <= 0:
            return "reclaim_boundary_lost"
        if (mark - context["target_price"]) * direction >= 0:
            return "reclaim_target_reached"
        return None


async def run_retail_paper_session(
    *, config: Any, rules: Any, account: RetailPaperAccount,
    evaluation: RetailEvaluation, policy: RetailRiskPolicy,
    store: Any, identity: str, duration_seconds: int,
    client: Any, emit: Callable[[dict], None],
    stream_factory: Callable[[], Any],
) -> dict:
    if duration_seconds <= 0:
        raise ValueError("retail duration must be positive and bounded")
    from quantos.application.hft_trader import _clock_calibration

    engine = RetailPaperEngine(config, rules, account, evaluation, policy)
    monitor = HftClockMonitor(
        config.clock_negative_latency_tolerance_ms,
        config.clock_excessive_negative_limit,
        config.clock_recalibration_interval,
    )
    deadline = time.monotonic() + duration_seconds
    reconnects = 0
    sequence_gaps = 0
    shutdown_reason = "duration_expired"
    runtime: dict = {}

    async def calibrate() -> None:
        calibration = await asyncio.wait_for(
            _clock_calibration(
                client, config.clock_calibration_samples,
                config.clock_low_rtt_samples,
                lambda: datetime.now(timezone.utc), time.monotonic,
            ), timeout=config.clock_calibration_timeout.total_seconds(),
        )
        monitor.apply_calibration(calibration)
        emit({"event": "retail_clock_calibrated", "clock_health": monitor.health.value})

    try:
        await calibrate()
        while time.monotonic() < deadline:
            stream = stream_factory()
            queue: asyncio.Queue = asyncio.Queue(maxsize=4096)

            async def produce() -> None:
                async for item in stream.events(rules.symbol):
                    queue.put_nowait(item)

            producer = asyncio.create_task(produce())
            try:
                await asyncio.wait_for(
                    stream.wait_public_ready(),
                    timeout=config.bootstrap_timeout.total_seconds(),
                )
                await asyncio.wait_for(
                    stream.wait_market_ready(),
                    timeout=config.bootstrap_timeout.total_seconds(),
                )
                snapshot = await asyncio.wait_for(
                    asyncio.to_thread(client.depth_snapshot, rules.symbol),
                    timeout=config.snapshot_timeout.total_seconds(),
                )
                buffered: list[DepthDelta] = []
                bridge_deadline = min(
                    deadline, time.monotonic() + config.bootstrap_timeout.total_seconds()
                )
                while time.monotonic() < bridge_deadline:
                    item = await asyncio.wait_for(
                        queue.get(), timeout=min(1, bridge_deadline - time.monotonic())
                    )
                    if isinstance(item, DepthDelta):
                        buffered.append(item)
                        if item.first_update_id <= snapshot.last_update_id <= item.final_update_id:
                            break
                else:
                    raise RuntimeError("retail depth snapshot bridge timed out")
                engine.bootstrap(snapshot, tuple(buffered))
                emit({"event": "retail_book_ready", "symbol": rules.symbol,
                      "update_id": engine.book.last_update_id})
                last_checkpoint = time.monotonic()
                while time.monotonic() < deadline:
                    if producer.done():
                        producer.result()
                        raise RuntimeError("retail public stream ended")
                    now = time.monotonic()
                    if monitor.calibration_due(now):
                        engine.disarm("clock_recalibration", datetime.now(timezone.utc))
                        await calibrate()
                        engine.disarmed = False
                    try:
                        item = await asyncio.wait_for(
                            queue.get(), timeout=min(1, max(deadline - now, 0.001))
                        )
                    except TimeoutError:
                        if engine.book.valid and engine.book.last_received_monotonic_ns is not None:
                            try:
                                engine.book.require_fresh_monotonic(
                                    time.monotonic_ns(), config.maximum_staleness
                                )
                            except HftBookError:
                                engine.disarm("stale_depth", datetime.now(timezone.utc))
                                raise
                        continue
                    latency = monitor.observe(item.exchange_time, item.received_at)
                    if latency.corrected_signed_latency_ms < -config.clock_negative_latency_tolerance_ms:
                        engine.disarm("negative_feed_latency", item.received_at)
                    result = engine.on_event(
                        item, feed_latency_ms=latency.observed_feed_latency_ms,
                        clock_healthy=monitor.quoting_allowed,
                    )
                    if result is not None:
                        emit(result)
                    if engine.disarmed:
                        raise RuntimeError("retail engine disarmed; reconstructing book")
                    if time.monotonic() - last_checkpoint >= 5:
                        runtime = {"reconnects": reconnects,
                                   "sequence_gaps": sequence_gaps,
                                   "metrics": evaluation.report(account)}
                        store.save(identity, account, runtime)
                        last_checkpoint = time.monotonic()
            except (HftBookError, HftFeatureError) as error:
                sequence_gaps += 1
                engine.disarm("invalid_market_data", datetime.now(timezone.utc))
                emit({"event": "retail_resync", "reason": str(error)})
            except (TimeoutError, OSError, RuntimeError, ValueError) as error:
                engine.disarm("feed_or_bootstrap_failure", datetime.now(timezone.utc))
                reconnects += 1
                emit({"event": "retail_reconnect", "reason": str(error)})
            finally:
                producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)
                store.save(identity, account, {
                    "reconnects": reconnects, "sequence_gaps": sequence_gaps,
                    "metrics": evaluation.report(account),
                })
            if time.monotonic() < deadline:
                await asyncio.sleep(min(0.5, deadline - time.monotonic()))
    except Exception as error:
        shutdown_reason = f"startup_or_runtime_failure:{type(error).__name__}:{error}"
        engine.disarm(shutdown_reason, datetime.now(timezone.utc))
        raise
    finally:
        engine.disarm("runtime_shutdown", datetime.now(timezone.utc))
        store.save(identity, account, {
            "reconnects": reconnects, "sequence_gaps": sequence_gaps,
            "shutdown_reason": shutdown_reason,
            "metrics": evaluation.report(account),
        })
    return {**evaluation.report(account), "reconnects": reconnects,
            "sequence_gaps": sequence_gaps, "shutdown_reason": shutdown_reason}

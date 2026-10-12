"""Final pre-paper acceptance tests for the autonomous Futures runtime."""
from __future__ import annotations

from dataclasses import replace
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from quantos.application.futures_trader import (
    AutonomousFuturesPaperTrader, run_futures_replay,
)
from quantos.domain.alpha.futures import (
    Direction, MarketRegime, RegimeState, StrategySignal, TradeCandidate,
    evaluate_strategies_with_diagnostics,
)
from quantos.domain.execution.futures_paper import (
    ExitReason, FuturesPaperExecution, PositionManagementState,
)
from quantos.domain.market_data import Candle
from quantos.domain.market_data.futures import FuturesSymbolRules
from quantos.domain.risk.futures import (
    ConsecutiveLossBreakerState, FuturesAccountState,
    FuturesRiskDecision, assess_candidate_economics, evaluate_futures_risk,
)
from quantos.infrastructure.binance.futures import BinanceFuturesError
from quantos.infrastructure.configuration.futures import load_futures_config
from quantos.infrastructure.storage.futures_paper import (
    FuturesPaperStateError, FuturesPaperStateStore,
)
from quantos.interfaces.futures import _retry_public_call, _validate_live_batch


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
CONFIG = Path("configs/futures_100usdt_paper.toml")


def rules() -> FuturesSymbolRules:
    return FuturesSymbolRules(
        symbol="BTCUSDT", status="TRADING",
        minimum_price=D(".1"), maximum_price=D("1000000"),
        price_tick=D(".1"), minimum_quantity=D(".001"),
        maximum_quantity=D("1000"), quantity_step=D(".001"),
        minimum_notional=D("5"),
    )


def signal(strategy="trend_continuation", timestamp=START,
           entry="60000", stop="59750", target="61000",
           context=()) -> TradeCandidate:
    regime = (
        MarketRegime.BREAKOUT_OR_EXPANSION
        if strategy == "breakout_expansion" else MarketRegime.TREND_UP
    )
    return TradeCandidate(StrategySignal(
        timestamp=timestamp, symbol="BTCUSDT", direction=Direction.LONG,
        strategy_id=strategy, regime=regime, entry=D(entry), stop=D(stop),
        target=D(target), strength=D(".8"), evidence=("fixture",),
        rationale="fixture", timeframe="1m",
        higher_timeframe_context=tuple(context),
        signal_id=f"{strategy}|{timestamp.isoformat()}", atr=D("500"),
    ))


def candle(minute: int, close="60000", high="60020", low="59980") -> Candle:
    opened = START + timedelta(minutes=minute)
    return Candle(
        "BTCUSDT", "1m", opened,
        opened + timedelta(minutes=1) - timedelta(milliseconds=1),
        D(close), D(high), D(low), D(close), D("1"), D("60000"), 1,
    )


def small_candidate(context=()) -> TradeCandidate:
    return signal(
        entry="100", stop="99", target="102", context=context
    )


def manual_approval(identifier="qv1-pre-paper") -> FuturesRiskDecision:
    return FuturesRiskDecision(
        True, "approved", D(".1"), 1, D("10"), D("10"), D(".1"),
        D("1"), identifier,
    )


class CanonicalPaperPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_futures_config(CONFIG)

    def test_canonical_capital_risk_leverage_and_strategy_set(self):
        self.assertEqual(self.config.starting_equity, D("100"))
        self.assertEqual(self.config.risk.risk_fraction, D(".01"))
        self.assertEqual(self.config.risk.maximum_risk_fraction, D(".02"))
        self.assertEqual(self.config.risk.leverage_ceiling, 5)
        self.assertEqual(
            self.config.enabled_strategies,
            ("trend_continuation", "breakout_expansion"),
        )

    def test_range_and_pullback_are_diagnostic_only(self):
        trader = AutonomousFuturesPaperTrader(self.config, rules())
        disabled = (
            signal("range_mean_reversion").signal,
            signal("trend_pullback").signal,
        )
        with patch(
            "quantos.application.futures_trader.evaluate_strategies_with_diagnostics",
            return_value=(disabled, ()),
        ):
            for minute in range(30):
                trader.on_candle(candle(minute))
        metrics = trader.metrics()
        self.assertEqual(metrics["number_of_trades"], 0)
        self.assertEqual(metrics["rejections"]["strategy_disabled"], 2)
        for strategy in ("range_mean_reversion", "trend_pullback"):
            self.assertEqual(
                metrics["strategy_candidate_funnel"][strategy][
                    "rejection_reasons"
                ]["strategy_disabled"],
                1,
            )

    def test_trend_and_breakout_are_economically_executable(self):
        account = FuturesAccountState(D("100"), D("100"))
        for strategy in ("trend_continuation", "breakout_expansion"):
            with self.subTest(strategy=strategy):
                decision = evaluate_futures_risk(
                    signal(strategy), account, rules(), self.config.risk
                )
                self.assertTrue(decision.approved, decision.reason)
                self.assertLessEqual(decision.leverage, 5)

    def test_small_deterministic_replay_executes_canonical_trend(self):
        def trend_signal(candles, state, timeframe="1m", higher_context=()):
            item = signal(timestamp=candles[-1].close_time).signal
            return (item,), ()

        candles = [candle(minute) for minute in range(30)]
        candles.append(candle(30, close="61100", high="61200", low="59980"))
        with patch(
            "quantos.application.futures_trader.evaluate_strategies_with_diagnostics",
            side_effect=trend_signal,
        ):
            metrics, _ = run_futures_replay(candles, self.config, rules())
        self.assertEqual(metrics["number_of_trades"], 1)
        self.assertEqual(
            metrics["strategy_attribution"]["trend_continuation"]["trades"], 1
        )

    def test_runtime_warmup_builds_history_without_opening_positions(self):
        trader = AutonomousFuturesPaperTrader(self.config, rules())

        def trend_signal(candles, state, timeframe="1m", higher_context=()):
            return (signal(timestamp=candles[-1].close_time).signal,), ()

        with patch(
            "quantos.application.futures_trader.evaluate_strategies_with_diagnostics",
            side_effect=trend_signal,
        ):
            for minute in range(30):
                decision = trader.on_candle(
                    candle(minute), allow_new_entries=False
                )
        self.assertIsNone(trader.execution.position)
        self.assertEqual(trader.metrics()["number_of_trades"], 0)
        self.assertEqual(decision["reason"], "runtime_entry_disabled")

    def test_net_r_below_two_is_rejected(self):
        result = assess_candidate_economics(
            signal(target="60600"), FuturesAccountState(D("100"), D("100")),
            rules(), self.config.risk,
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "insufficient_net_opportunity")
        self.assertLess(result.net_reward_risk, D("2"))

    def test_exact_nominal_two_r_reproduces_cost_adjusted_deadlock(self):
        candidate = signal(target="60500")
        result = assess_candidate_economics(
            candidate, FuturesAccountState(D("100"), D("100")),
            rules(), self.config.risk,
        )
        self.assertEqual(result.gross_projected_r, D("2"))
        self.assertLess(result.net_projected_r, D("2"))
        self.assertFalse(result.approved)
        self.assertEqual(result.rejection_category, "net_reward_risk")

    def test_causal_trend_measured_move_passes_without_soft_structure_cap(self):
        history = [candle(i) for i in range(29)]
        history[-1] = candle(28, close="59980", high="60000", low="59950")
        history.append(candle(29, close="60100", high="60120", low="60020"))
        state = RegimeState(
            MarketRegime.TREND_UP, D("60020"), D("59750"), D("100"),
            D("60000"), D("59000"), "entry-time fixture",
        )
        signals, _ = evaluate_strategies_with_diagnostics(history, state)
        candidate = TradeCandidate(next(
            item for item in signals
            if item.strategy_id == "trend_continuation"
        ))
        self.assertEqual(candidate.signal.soft_structure_reference, D("60000"))
        self.assertEqual(candidate.signal.continuation_objective, D("61200"))
        result = assess_candidate_economics(
            candidate, FuturesAccountState(D("100"), D("100")),
            rules(), self.config.risk,
        )
        self.assertLess(result.first_structure_r, D("2"))
        self.assertGreater(result.continuation_objective_r, D("2"))
        self.assertGreater(result.net_projected_r, D("2"))
        self.assertTrue(result.approved, result.reason)

        approval = evaluate_futures_risk(
            candidate, FuturesAccountState(D("100"), D("100")),
            rules(), self.config.risk,
        )
        engine = FuturesPaperExecution(
            D("100"), self.config.execution, D(".1")
        )
        engine.open(candidate, approval)
        not_terminal = candle(
            30, close="60400", high="60500", low="60050"
        )
        self.assertIsNone(engine.process_candle(not_terminal))
        self.assertIsNotNone(engine.position)

    def test_causal_breakout_range_projection_passes(self):
        history = [
            candle(i, close="59950", high="59970", low="59930")
            for i in range(29)
        ]
        history.append(candle(
            29, close="60100", high="60200", low="59800"
        ))
        state = RegimeState(
            MarketRegime.BREAKOUT_OR_EXPANSION,
            D("59950"), D("59950"), D("100"),
            D("60000"), D("59000"), "entry-time fixture",
        )
        signals, _ = evaluate_strategies_with_diagnostics(history, state)
        candidate = TradeCandidate(next(
            item for item in signals
            if item.strategy_id == "breakout_expansion"
        ))
        self.assertEqual(candidate.signal.soft_structure_reference, D("60000"))
        self.assertEqual(candidate.signal.continuation_objective, D("61100"))
        result = assess_candidate_economics(
            candidate, FuturesAccountState(D("100"), D("100")),
            rules(), self.config.risk,
        )
        self.assertLess(result.first_structure_r, D("2"))
        self.assertGreater(result.net_projected_r, D("2"))
        self.assertTrue(result.approved, result.reason)

    def test_reward_to_cost_below_five_is_rejected(self):
        policy = replace(
            self.config.risk,
            minimum_net_reward_risk=D(".1"),
            minimum_expected_movement_to_cost_multiple=D(".1"),
        )
        result = assess_candidate_economics(
            signal(entry="100", stop="99.99", target="100.5"),
            FuturesAccountState(D("100"), D("100")), rules(), policy,
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "insufficient_net_opportunity")
        self.assertLess(result.reward_to_execution_cost_multiple, D("5"))

    def test_valid_greater_than_two_net_r_is_admitted(self):
        result = assess_candidate_economics(
            signal(), FuturesAccountState(D("100"), D("100")),
            rules(), self.config.risk,
        )
        self.assertTrue(result.approved, result.reason)
        self.assertGreaterEqual(result.net_reward_risk, D("2"))
        self.assertGreaterEqual(result.reward_to_execution_cost_multiple, D("5"))

    def test_current_equity_controls_one_percent_budget(self):
        result = assess_candidate_economics(
            signal(), FuturesAccountState(D("95"), D("100")),
            rules(), self.config.risk,
        )
        self.assertEqual(result.risk_budget, D(".95"))


class ExitAndCostPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_futures_config(CONFIG)

    def engine(self, context=()) -> FuturesPaperExecution:
        engine = FuturesPaperExecution(
            D("100"), self.config.execution, D(".1")
        )
        engine.open(small_candidate(context), manual_approval())
        return engine

    def test_cost_adjusted_breakeven_waits_for_one_r(self):
        engine = self.engine()
        below = candle(1, close="100.7", high="100.9", low="99.8")
        engine.process_candle(below)
        engine.manage_completed_candle(
            below, D(".5"), MarketRegime.TREND_UP
        )
        self.assertNotEqual(
            engine.position.state, PositionManagementState.BREAKEVEN_PROTECTED
        )
        above = candle(2, close="101.2", high="101.3", low="100.5")
        engine.process_candle(above)
        engine.manage_completed_candle(
            above, D(".5"), MarketRegime.TREND_UP
        )
        self.assertEqual(
            engine.position.state, PositionManagementState.BREAKEVEN_PROTECTED
        )
        self.assertGreater(engine.position.stop, engine.position.entry_price)

    def test_stop_never_widens(self):
        engine = self.engine()
        original = engine.position.stop
        self.assertFalse(engine._tighten_stop(D("98"), START, "fixture"))
        self.assertEqual(engine.position.stop, original)

    def test_aligned_runner_can_continue_beyond_two_r(self):
        engine = self.engine(("15m:TREND_UP", "30m:TREND_UP"))
        item = candle(1, close="102.5", high="103", low="99.8")
        closed = engine.process_candle(item)
        self.assertIsNone(closed)
        self.assertEqual(engine.position.state, PositionManagementState.TREND_RUNNER)

    def test_strategy_invalidation_exits(self):
        engine = self.engine()
        item = candle(1, close="100", high="100.2", low="99.8")
        engine.process_candle(item)
        trade = engine.manage_completed_candle(
            item, D(".5"), MarketRegime.TREND_DOWN
        )
        self.assertEqual(trade.exit_reason, ExitReason.REGIME_INVALIDATION)

    def test_conservative_fills_report_taker_and_unfilled_maker_attempts(self):
        engine = self.engine()
        trade = engine.close_at(D("102"), START, ExitReason.FIXED_TARGET)
        self.assertIsNotNone(trade)
        self.assertEqual(engine.maker_fills, 0)
        self.assertEqual(engine.taker_fills, 2)
        self.assertEqual(engine.maker_attempts_not_filled, 2)
        self.assertEqual(engine.maker_fees, D("0"))
        self.assertEqual(engine.taker_fees, engine.total_fees)


class DurablePaperStateTests(unittest.TestCase):
    def setUp(self):
        self.config = load_futures_config(CONFIG)
        self.rules = rules()

    def test_state_survives_reload_with_position_breaker_pnl_and_idempotency(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "paper.db"
            store = FuturesPaperStateStore(path)
            trader = store.reset(self.config, self.rules, START)
            first = signal(timestamp=START)
            approval = evaluate_futures_risk(
                first, trader._account_state(D("60000")), self.rules,
                self.config.risk,
            )
            trader.execution.open(first, approval)
            closed = trader.execution.close_at(D("61000"), START + timedelta(minutes=1))
            trader._record_closed_trade(closed)
            second = signal(timestamp=START + timedelta(minutes=2))
            approval = evaluate_futures_risk(
                second, trader._account_state(D("60000")), self.rules,
                self.config.risk,
            )
            trader.execution.open(second, approval)
            trader.execution.apply_funding(
                D(".0001"), D("60000"), START + timedelta(minutes=3),
                "funding-fixture",
            )
            expected_funding = trader.execution.total_funding
            trader.breaker_state = ConsecutiveLossBreakerState(
                consecutive_losses=2, trigger_count=1
            )
            trader.last_candle = candle(10)
            runtime = {
                "last_healthy_runtime_timestamp": START,
                "last_runtime_timestamp": START,
                "retry_count": 2,
            }
            store.save(trader, runtime, START + timedelta(minutes=10))

            restored, restored_runtime = FuturesPaperStateStore(path).load(
                self.config, self.rules
            )
            self.assertEqual(restored.execution.balance, trader.execution.balance)
            self.assertEqual(restored.execution.position, trader.execution.position)
            self.assertEqual(restored.execution.trades, trader.execution.trades)
            self.assertEqual(restored.breaker_state, trader.breaker_state)
            self.assertEqual(restored.execution.total_funding, expected_funding)
            self.assertEqual(restored_runtime["retry_count"], 2)
            duplicate = restored.on_candle(candle(10))
            self.assertEqual(duplicate["reason"], "duplicate_candle_ignored")
            restored.execution.close_at(D("60000"), START + timedelta(minutes=11))
            with self.assertRaisesRegex(Exception, "duplicate"):
                restored.execution.open(second, approval)

    def test_missing_or_corrupt_state_fails_closed_and_reset_is_explicit(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "paper.db"
            store = FuturesPaperStateStore(path)
            with self.assertRaisesRegex(FuturesPaperStateError, "paper-reset"):
                store.load(self.config, self.rules)
            store.reset(self.config, self.rules, START)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "UPDATE futures_paper_state SET checksum = 'corrupt'"
                )
            with self.assertRaisesRegex(FuturesPaperStateError, "checksum"):
                FuturesPaperStateStore(path).load(self.config, self.rules)


class LivePaperSafetyTests(unittest.TestCase):
    def test_transient_failure_retries_without_state_mutation(self):
        calls = []
        sleeps = []

        def operation():
            calls.append(1)
            if len(calls) < 3:
                raise BinanceFuturesError("temporary", retryable=True)
            return "ok"

        result, retries = _retry_public_call(
            operation, 3, 1, sleep=sleeps.append
        )
        self.assertEqual(result, "ok")
        self.assertEqual(retries, 2)
        self.assertEqual(sleeps, [1, 2])

    def test_exhausted_transient_failure_preserves_durable_account(self):
        config = load_futures_config(CONFIG)
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "paper.db"
            store = FuturesPaperStateStore(path)
            trader = store.reset(config, rules(), START)
            expected = trader.execution.balance

            def unavailable():
                raise BinanceFuturesError("temporary", retryable=True)

            with self.assertRaises(BinanceFuturesError):
                _retry_public_call(unavailable, 0, 1, sleep=lambda _: None)
            restored, _ = FuturesPaperStateStore(path).load(config, rules())
            self.assertEqual(restored.execution.balance, expected)
            self.assertIsNone(restored.execution.position)

    def test_stale_or_gapped_data_is_rejected_before_trading(self):
        previous = candle(0)
        with self.assertRaisesRegex(Exception, "stale"):
            _validate_live_batch(
                (), previous, START + timedelta(minutes=5), 90
            )
        with self.assertRaisesRegex(Exception, "gap"):
            _validate_live_batch(
                (candle(2),), previous, START + timedelta(minutes=3), 90
            )

    def test_paper_engine_has_no_real_order_submission_capability(self):
        engine = FuturesPaperExecution(D("100"))
        self.assertFalse(hasattr(engine, "submit_order"))
        self.assertFalse(hasattr(engine, "place_order"))


if __name__ == "__main__":
    unittest.main()

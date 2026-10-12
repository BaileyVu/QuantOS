"""Focused entry-economics and stateful Futures exit-management tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quantos.application.futures_trader import (
    AutonomousFuturesPaperTrader, filter_economically_feasible_candidates,
)
from quantos.domain.alpha.futures import (
    Direction, MarketRegime, StrategySignal, TradeCandidate, select_candidate,
)
from quantos.domain.execution.futures_paper import (
    ExitReason, FuturesPaperExecution, FuturesPaperPolicy,
    PositionManagementPolicy, PositionManagementState,
)
from quantos.domain.market_data import Candle
from quantos.domain.market_data.futures import FuturesSymbolRules
from quantos.domain.risk.futures import (
    FuturesAccountState, FuturesRiskDecision, FuturesRiskPolicy,
    assess_candidate_economics, evaluate_futures_risk,
)
from quantos.infrastructure.configuration.futures import load_futures_config
from quantos.interfaces.futures import _candle_record, _json, _read_candles


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def rules(minimum_notional: str = "5") -> FuturesSymbolRules:
    return FuturesSymbolRules(
        symbol="BTCUSDT", status="TRADING",
        minimum_price=D(".1"), maximum_price=D("1000000"), price_tick=D(".1"),
        minimum_quantity=D(".001"), maximum_quantity=D("1000"),
        quantity_step=D(".001"), minimum_notional=D(minimum_notional),
    )


def signal(direction=Direction.LONG, entry="100", stop="99", target="102",
           timeframe="1m", strategy="trend_continuation", strength=".7",
           signal_id="fixture", context=()):
    return StrategySignal(
        START, "BTCUSDT", direction, strategy, MarketRegime.TREND_UP,
        D(entry), D(stop), D(target), D(strength), ("fixture",), "fixture",
        timeframe, tuple(context), signal_id, D(".5"),
    )


def candle(minute: int, close="100", high="100.2", low="99.8",
           interval="1m") -> Candle:
    opened = START + timedelta(minutes=minute)
    return Candle(
        "BTCUSDT", interval, opened,
        opened + timedelta(minutes=1) - timedelta(milliseconds=1),
        D(close), D(high), D(low), D(close), D("1"), D("100"), 1,
    )


def approval(quantity=".1", risk=".1", leverage=1) -> FuturesRiskDecision:
    return FuturesRiskDecision(
        True, "approved", D(quantity), leverage, D(quantity) * D("100"),
        D(quantity) * D("100") / D(leverage), D(risk), D("1"), "qv1-test",
    )


class CandidateEconomicsTests(unittest.TestCase):
    def setUp(self):
        self.account = FuturesAccountState(D("20"), D("20"))
        self.policy = FuturesRiskPolicy()

    def test_minimum_executable_quantity_is_calculated_before_selection(self):
        result = assess_candidate_economics(
            TradeCandidate(signal()), self.account, rules(), self.policy
        )
        self.assertEqual(result.minimum_executable_quantity, D(".050"))
        self.assertTrue(result.approved)
        self.assertLessEqual(result.cost_adjusted_stop_loss, D(".2"))

    def test_economically_impossible_minimum_is_rejected(self):
        result = assess_candidate_economics(
            TradeCandidate(signal()), self.account, rules("100"), self.policy
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.rejection_category, "minimum_executable_risk")

    def test_alternate_feasible_timeframe_can_be_selected(self):
        infeasible = signal(target="100.1", timeframe="15m", strength=".9",
                            signal_id="infeasible")
        feasible = signal(timeframe="5m", strength=".7", signal_id="feasible")
        strict = replace(self.policy, leverage_ceiling=1)
        eligible, economics = filter_economically_feasible_candidates(
            (infeasible, feasible), self.account, rules(), strict
        )
        selected = select_candidate(eligible)
        self.assertFalse(economics["infeasible"].approved)
        self.assertEqual(selected.candidate.signal.signal_id, "feasible")

    def test_fee_and_slippage_are_in_net_reward_risk(self):
        result = assess_candidate_economics(
            TradeCandidate(signal()), self.account, rules(), self.policy
        )
        self.assertGreater(result.gross_reward_risk, result.net_reward_risk)
        self.assertEqual(
            result.expected_net_reward,
            result.expected_gross_reward - result.total_execution_costs,
        )

    def test_cost_dominated_candidate_is_rejected(self):
        costly = replace(self.policy, minimum_reward_to_cost_multiple=D("10"))
        result = assess_candidate_economics(
            TradeCandidate(signal(target="100.2")), self.account, rules(), costly
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.rejection_category, "cost")

    def test_expected_movement_must_materially_exceed_round_trip_cost(self):
        selective = replace(
            self.policy,
            minimum_expected_movement_to_cost_multiple=D("100"),
        )
        result = assess_candidate_economics(
            TradeCandidate(signal()), self.account, rules(), selective
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.rejection_category, "expected_movement_cost")
        self.assertGreater(result.expected_movement_rate, 0)
        self.assertGreater(result.estimated_round_trip_cost_rate, 0)

    def test_leverage_solves_margin_without_increasing_loss_budget(self):
        candidate = TradeCandidate(signal(stop="99.99"))
        low = evaluate_futures_risk(
            candidate, self.account, rules(),
            replace(self.policy, leverage_ceiling=1),
        )
        higher = evaluate_futures_risk(
            candidate, self.account, rules(),
            replace(self.policy, leverage_ceiling=5),
        )
        self.assertGreater(higher.quantity, low.quantity)
        self.assertLessEqual(higher.risk_amount, D(".2"))
        self.assertEqual(higher.leverage, 5)


class CachedReplayWindowTests(unittest.TestCase):
    def test_one_cached_file_is_filtered_by_inclusive_exclusive_window(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "btc-365d.jsonl"
            path.write_text(
                "".join(
                    _json(_candle_record(candle(minute))) + "\n"
                    for minute in range(6)
                ),
                encoding="ascii",
            )
            selected = _read_candles(
                path,
                START + timedelta(minutes=2),
                START + timedelta(minutes=5),
            )
        self.assertEqual(
            [item.open_time for item in selected],
            [START + timedelta(minutes=value) for value in (2, 3, 4)],
        )


class PositionManagementTests(unittest.TestCase):
    def engine(self, direction=Direction.LONG, **signal_kwargs):
        engine = FuturesPaperExecution(D("20"), price_tick=D(".1"))
        candidate = TradeCandidate(signal(direction=direction, **signal_kwargs))
        engine.open(candidate, approval())
        return engine

    def manage(self, engine, item, regime=MarketRegime.TREND_UP,
               context=(), recent=()):
        engine.process_candle(item)
        return engine.manage_completed_candle(
            item, D(".5"), regime, tuple(context), tuple(recent)
        )

    def test_tiny_move_does_not_trigger_breakeven(self):
        engine = self.engine(target="110")
        self.manage(engine, candle(1, close="100.2", high="100.3", low="99.8"))
        self.assertEqual(engine.position.state,
                         PositionManagementState.PROFIT_DEVELOPING)
        self.assertEqual(engine.position.stop, D("99"))

    def test_breakeven_activates_and_includes_costs(self):
        engine = self.engine(target="110")
        self.manage(engine, candle(1, close="101", high="101.2", low="99.8"))
        self.assertEqual(engine.position.state,
                         PositionManagementState.BREAKEVEN_PROTECTED)
        self.assertGreater(engine.position.stop, engine.position.entry_price)

    def test_stop_never_widens_and_long_short_are_symmetric(self):
        long = self.engine(target="110")
        self.manage(long, candle(1, close="102", high="102.2", low="99.8"))
        first_long = long.position.stop
        self.manage(long, candle(2, close="102", high="102.1", low="101.8"))
        self.assertGreaterEqual(long.position.stop, first_long)

        short = self.engine(
            direction=Direction.SHORT, stop="101", target="90"
        )
        down = candle(1, close="99", high="100.2", low="98.8")
        self.manage(short, down, MarketRegime.TREND_DOWN)
        self.assertEqual(short.position.state,
                         PositionManagementState.BREAKEVEN_PROTECTED)
        self.assertLess(short.position.stop, short.position.entry_price)

    def test_profit_lock_and_hybrid_atr_structure_trailing(self):
        engine = self.engine(target="110")
        recent = [
            candle(i, close="101.5", high="101.8", low=str(D("100.5") + D(i) / 10))
            for i in range(1, 7)
        ]
        self.manage(
            engine, candle(7, close="102", high="102.2", low="101"),
            recent=recent,
        )
        self.assertEqual(engine.position.state,
                         PositionManagementState.PROFIT_LOCKED)
        self.assertGreater(engine.position.stop, engine.position.entry_price)
        self.assertTrue(any(
            event.get("source") == "HYBRID_ATR_STRUCTURE"
            for event in engine.audit_events
        ))

    def test_trend_runner_requires_two_aligned_higher_timeframes(self):
        engine = self.engine(
            target="110",
            context=("15m:TREND_UP", "30m:TREND_UP"),
        )
        self.manage(
            engine, candle(1, close="102.5", high="102.7", low="99.8"),
            context=(("15m", MarketRegime.TREND_UP),
                     ("30m", MarketRegime.TREND_UP)),
        )
        self.assertEqual(engine.position.state,
                         PositionManagementState.TREND_RUNNER)

    def test_runner_does_not_revert_to_fixed_target(self):
        engine = self.engine(
            target="102",
            context=("15m:TREND_UP", "30m:TREND_UP"),
        )
        first = engine.process_candle(
            candle(1, close="102.2", high="102.5", low="99.8")
        )
        self.assertIsNone(first)
        self.assertEqual(engine.position.state,
                         PositionManagementState.TREND_RUNNER)
        second = engine.process_candle(
            candle(2, close="102.3", high="102.6", low="102.1")
        )
        self.assertIsNone(second)
        self.assertIsNotNone(engine.position)

    def test_time_stop_and_regime_invalidation(self):
        policy = FuturesPaperPolicy(management=replace(
            PositionManagementPolicy(), time_stop_bars_1m=1
        ))
        stale = FuturesPaperExecution(D("20"), policy, D(".1"))
        stale.open(TradeCandidate(signal(target="110")), approval())
        trade = self.manage(
            stale, candle(1, close="100", high="100.1", low="99.8")
        )
        self.assertEqual(trade.exit_reason, ExitReason.TIME_STOP)

        invalid = self.engine(target="110")
        trade = self.manage(
            invalid, candle(1, close="100", high="100.2", low="99.8"),
            MarketRegime.TREND_DOWN,
        )
        self.assertEqual(trade.exit_reason, ExitReason.REGIME_INVALIDATION)

    def test_mfe_mae_and_explicit_exit_attribution(self):
        engine = self.engine(target="110")
        engine.process_candle(candle(1, close="100.2", high="100.8", low="99.5"))
        trade = engine.close_at(D("100.2"), START + timedelta(minutes=2))
        self.assertGreater(trade.mfe_price, 0)
        self.assertGreater(trade.mae_price, 0)
        self.assertGreater(trade.mfe_r, 0)
        self.assertEqual(trade.exit_reason, ExitReason.PAPER_END)
        self.assertEqual(trade.initial_stop, D("99"))
        self.assertEqual(trade.management_state_at_exit,
                         PositionManagementState.INITIAL_RISK)

    def test_stop_first_candle_does_not_credit_same_candle_target_mfe(self):
        engine = self.engine(target="102")
        trade = engine.process_candle(
            candle(1, close="100", high="103", low="98")
        )
        self.assertEqual(trade.exit_reason, ExitReason.INITIAL_STOP)
        self.assertEqual(trade.mfe_price, D("0"))
        self.assertEqual(trade.mae_price, D("1.02"))

    def test_legacy_paper_end_alias_is_normalized(self):
        engine = self.engine(target="110")
        trade = engine.close_at(D("100"), START, "end_of_data")
        self.assertEqual(trade.exit_reason, ExitReason.PAPER_END)

    def test_replay_and_live_paper_use_same_management_engine(self):
        config = load_futures_config(Path("configs/futures.toml"))
        trader = AutonomousFuturesPaperTrader(config, rules())
        self.assertIsInstance(trader.execution, FuturesPaperExecution)
        self.assertIs(
            trader.execution.manage_completed_candle.__func__,
            FuturesPaperExecution.manage_completed_candle,
        )
        self.assertEqual(config.entry_timeframes, ("1m", "3m", "5m", "15m"))
        self.assertEqual(config.context_timeframes, ("15m", "30m", "1h"))

    def test_experimental_10x_config_preserves_loss_budget(self):
        conservative = load_futures_config(Path("configs/futures.toml"))
        experimental = load_futures_config(
            Path("configs/futures_replay_10x.toml")
        )
        self.assertEqual(experimental.risk.leverage_ceiling, 10)
        self.assertEqual(
            experimental.risk.risk_fraction,
            conservative.risk.risk_fraction,
        )
        self.assertEqual(
            experimental.risk.maximum_risk_fraction,
            conservative.risk.maximum_risk_fraction,
        )


if __name__ == "__main__":
    unittest.main()

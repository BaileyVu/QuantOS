"""Deterministic regime, strategy-eligibility, and candidate-funnel tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import unittest

from quantos.application.futures_trader import (
    AutonomousFuturesPaperTrader, FuturesTraderConfig,
    filter_economically_feasible_candidates,
)
from quantos.domain.alpha.futures import (
    Direction, MarketRegime, RegimeState, TradeCandidate, classify_regime,
    evaluate_strategies_with_diagnostics, select_candidate,
)
from quantos.domain.execution.futures_paper import (
    FuturesPaperExecution, FuturesPaperPolicy,
)
from quantos.domain.market_data import Candle
from quantos.domain.market_data.futures import FuturesSymbolRules
from quantos.domain.risk.futures import (
    FuturesAccountState, FuturesRiskPolicy, evaluate_futures_risk,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bar(index: int, close: D, *, spread: D = D(".2"),
        low: D | None = None, high: D | None = None) -> Candle:
    opened = START + timedelta(minutes=index)
    return Candle(
        "BTCUSDT", "1m", opened,
        opened + timedelta(minutes=1) - timedelta(milliseconds=1),
        close, high if high is not None else close + spread,
        low if low is not None else close - spread, close,
        D("1"), close, 1,
    )


def exchange_rules() -> FuturesSymbolRules:
    return FuturesSymbolRules(
        symbol="BTCUSDT", status="TRADING",
        quantity_step=D(".001"), minimum_quantity=D(".001"),
        maximum_quantity=D("1000"), price_tick=D(".1"),
        minimum_price=D(".1"), maximum_price=D("1000000"),
        minimum_notional=D("5"),
    )


class RegimeReachabilityTests(unittest.TestCase):
    def test_clear_uptrend(self):
        candles = tuple(
            bar(i, D("60000") + D(i) * D("50"), spread=D("20"))
            for i in range(40)
        )
        self.assertEqual(classify_regime(candles).regime, MarketRegime.TREND_UP)

    def test_clear_downtrend(self):
        candles = tuple(
            bar(i, D("62000") - D(i) * D("50"), spread=D("20"))
            for i in range(40)
        )
        self.assertEqual(classify_regime(candles).regime, MarketRegime.TREND_DOWN)

    def test_range(self):
        closes = (
            D("59950"), D("60050"), D("60000"), D("59980"), D("60020")
        )
        candles = tuple(
            bar(i, closes[i % len(closes)], spread=D("20"))
            for i in range(40)
        )
        self.assertEqual(classify_regime(candles).regime, MarketRegime.RANGE)

    def test_volatility_expansion_breakout(self):
        candles = [bar(
            i, D("60000") + D(i % 3 - 1) * D("10"), spread=D("20")
        )
                   for i in range(39)]
        candles.append(
            bar(39, D("60300"), low=D("59950"), high=D("60350"))
        )
        self.assertEqual(
            classify_regime(candles).regime,
            MarketRegime.BREAKOUT_OR_EXPANSION,
        )

    def test_uncertain_noisy_transition(self):
        closes = [D("60000") + D(i) * D("50") for i in range(35)]
        closes.extend((
            D("61600"), D("61500"), D("61400"), D("61300"), D("61200")
        ))
        candles = tuple(
            bar(i, value, spread=D("20"))
            for i, value in enumerate(closes)
        )
        self.assertEqual(classify_regime(candles).regime, MarketRegime.UNCERTAIN)


class StrategyPipelineReachabilityTests(unittest.TestCase):
    account = FuturesAccountState(D("20"), D("20"))
    policy = FuturesRiskPolicy()

    def assert_reaches_trade(self, candles, state, strategy_id):
        signals, evaluations = evaluate_strategies_with_diagnostics(candles, state)
        matching = [item for item in signals if item.strategy_id == strategy_id]
        self.assertTrue(matching, (strategy_id, evaluations))
        eligible, economics = filter_economically_feasible_candidates(
            matching, self.account, exchange_rules(), self.policy
        )
        self.assertTrue(eligible, economics)
        selection = select_candidate(eligible)
        self.assertEqual(selection.candidate.signal.strategy_id, strategy_id)
        decision = evaluate_futures_risk(
            selection.candidate, self.account, exchange_rules(), self.policy
        )
        self.assertTrue(decision.approved, decision.reason)
        execution = FuturesPaperExecution(D("20"), price_tick=D(".1"))
        position = execution.open(selection.candidate, decision)
        self.assertEqual(position.strategy_id, strategy_id)

    def test_trend_continuation_reaches_trade(self):
        candles = [bar(i, D("100")) for i in range(29)]
        candles.append(bar(29, D("101"), low=D("100.7"), high=D("101.2")))
        state = RegimeState(
            MarketRegime.TREND_UP, D("100.5"), D("99.5"), D("1"),
            D("100.5"), D("99"), "fixture",
        )
        self.assert_reaches_trade(candles, state, "trend_continuation")

    def test_trend_pullback_reaches_trade(self):
        candles = [bar(i, D("100")) for i in range(29)]
        candles.append(bar(29, D("100.5"), low=D("99.5"), high=D("100.8")))
        state = RegimeState(
            MarketRegime.TREND_UP, D("100"), D("99"), D("1"),
            D("102"), D("98"), "fixture",
        )
        self.assert_reaches_trade(candles, state, "trend_pullback")

    def test_breakout_expansion_reaches_trade(self):
        candles = [bar(i, D("100")) for i in range(29)]
        candles.append(bar(29, D("102"), low=D("99.5"), high=D("102.5")))
        state = RegimeState(
            MarketRegime.BREAKOUT_OR_EXPANSION, D("100"), D("100"), D("1"),
            D("100.5"), D("99.5"), "fixture",
        )
        self.assert_reaches_trade(candles, state, "breakout_expansion")

    def test_range_mean_reversion_reaches_trade_only_in_range(self):
        candles = [bar(i, D("100")) for i in range(29)]
        candles.append(bar(29, D("98.5"), low=D("98.2"), high=D("98.8")))
        range_state = RegimeState(
            MarketRegime.RANGE, D("100"), D("100"), D(".5"),
            D("102"), D("98"), "fixture",
        )
        self.assert_reaches_trade(candles, range_state, "range_mean_reversion")

        trend_state = RegimeState(
            MarketRegime.TREND_DOWN, D("99"), D("100"), D(".5"),
            D("102"), D("98"), "fixture",
        )
        signals, evaluations = evaluate_strategies_with_diagnostics(
            candles, trend_state
        )
        self.assertFalse(any(
            item.strategy_id == "range_mean_reversion" for item in signals
        ))
        range_attempt = next(
            item for item in evaluations
            if item.strategy_id == "range_mean_reversion"
        )
        self.assertEqual(range_attempt.rejection_reason, "regime_incompatible")

    def test_selection_has_no_range_strategy_bias(self):
        candles = [bar(i, D("100")) for i in range(29)]
        candles.append(bar(29, D("102"), low=D("99.5"), high=D("102.5")))
        breakout_state = RegimeState(
            MarketRegime.BREAKOUT_OR_EXPANSION, D("100"), D("100"), D("1"),
            D("100.5"), D("99.5"), "fixture",
        )
        breakout = evaluate_strategies_with_diagnostics(
            candles, breakout_state
        )[0][0]
        range_signal = breakout.__class__(
            breakout.timestamp, breakout.symbol, breakout.direction,
            "range_mean_reversion", MarketRegime.RANGE, breakout.entry,
            breakout.stop, breakout.target, D(".60"), breakout.evidence,
            breakout.rationale, breakout.timeframe,
            breakout.higher_timeframe_context, "range-fixture", breakout.atr,
        )
        selected = select_candidate((range_signal, breakout))
        self.assertEqual(
            selected.candidate.signal.strategy_id, "breakout_expansion"
        )


class FunnelReportingTests(unittest.TestCase):
    def test_replay_reports_all_regimes_strategies_and_stages(self):
        config = FuturesTraderConfig(
            "BTCUSDT", D("20"), 120, ("1m",),
            FuturesRiskPolicy(), FuturesPaperPolicy(),
            ("1m",), (),
        )
        trader = AutonomousFuturesPaperTrader(config, exchange_rules())
        for i in range(40):
            trader.on_candle(bar(i, D("100") + D(i) * D(".5")))
        metrics = trader.finish()
        self.assertEqual(
            set(metrics["regime_evaluation_periods"]),
            {item.value for item in MarketRegime},
        )
        funnel = metrics["strategy_candidate_funnel"]["trend_continuation"]
        self.assertGreater(int(funnel["raw_signals_generated"]), 0)
        self.assertGreater(int(funnel["regime_compatible"]), 0)
        self.assertGreaterEqual(
            int(funnel["decision_reached"]), int(funnel["trades"])
        )
        diagnostics = metrics["strategy_diagnostics"]
        self.assertIn("average_mfe_r", diagnostics["trend_continuation"])
        self.assertIn("initial_stop_percentage", diagnostics["range_mean_reversion"])
        self.assertIn(
            "trend_continuation",
            metrics[
                "candidate_funnel_rejections_by_strategy_timeframe_regime"
            ],
        )


if __name__ == "__main__":
    unittest.main()

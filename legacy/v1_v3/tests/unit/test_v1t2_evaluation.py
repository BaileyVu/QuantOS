"""Focused V1-T2 historical evaluation acceptance tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D, localcontext
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from quantos.application.evaluation import EvaluationError, run_backtest, run_walk_forward
from quantos.domain.alpha import AlphaAction, AlphaDecision
from quantos.domain.evaluation import (
    AlphaEvaluation,
    BacktestConfig,
    BuiltAlpha,
    CompletedTrade,
    EquityPoint,
    MonteCarloConfig,
    WalkForwardConfig,
    calculate_metrics,
    maximum_drawdown,
    run_monte_carlo,
)
from quantos.domain.execution import AccountSnapshot
from quantos.domain.market_data import Candle, DatasetIdentity, ValidatedCandleSequence, validate_candle_sequence
from quantos.domain.risk.engine import RiskPolicy
from quantos.domain.runtime_contracts import TransactionCosts, arithmetic
from quantos.infrastructure.storage.execution_ledger import JsonlExecutionLedger


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
COSTS = TransactionCosts(D("0.001"), D("0.002"))
POLICY = RiskPolicy(
    order_notional=D("5"),
    quantity_step=D("0.00001"),
    sell_fraction=D("1"),
    max_position_quantity=D("1"),
    max_position_notional=D("18"),
    max_exposure_fraction=D("0.95"),
    daily_loss_fraction=D("0.5"),
    drawdown_fraction=D("0.8"),
    stale_seconds=60,
    safety_margin_rate=D("0"),
    costs=COSTS,
)


def dataset(symbol: str = "BTCUSDT", count: int = 45, start: datetime = START):
    base = D("100") if symbol == "BTCUSDT" else D("50")
    candles = []
    for index in range(count):
        opened = start + timedelta(minutes=index)
        open_price = base + D(index) * D("0.5")
        close = open_price + (D("0.1") if index % 2 == 0 else D("0.2"))
        high = close + D("0.3")
        low = open_price - D("0.3")
        volume = D("1") + D(index) / D("100")
        candles.append(
            Candle(
                symbol,
                "1m",
                opened,
                opened + timedelta(minutes=1),
                open_price,
                high,
                low,
                close,
                volume,
                close * volume,
                10 + index,
            )
        )
    identity = DatasetIdentity(
        symbol, "1m", candles[0].open_time, candles[-1].open_time,
        "fixture", "canonical-v1", "fixture-v1",
    )
    return validate_candle_sequence(identity, candles)


def account(timestamp: datetime = START) -> AccountSnapshot:
    return AccountSnapshot(timestamp, {"USDT": D("20"), "BTC": D("0"), "ETH": D("0")}, ())


class FixtureAlpha:
    def __init__(self, actions=None, *, gross_edge=D("0.05")):
        self.actions = actions or {}
        self.gross_edge = gross_edge
        self.seen = []

    def __call__(self, feature):
        self.seen.append(feature)
        action = self.actions.get((feature.symbol, feature.timestamp), AlphaAction.HOLD)
        return AlphaEvaluation(
            AlphaDecision(
                timestamp=feature.timestamp,
                symbol=feature.symbol,
                strategy_version="strategy-fixture-v1",
                model_version="model-fixture-v1",
                feature_version=feature.feature_version,
                action=action,
                reason="fixture decision",
                strategy_state="fixture",
                model_score=D("0.25"),
            ),
            gross_edge_rate=self.gross_edge,
        )


class BacktestTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = dataset()
        self.buy_time = self.data.candles[20].close_time
        self.sell_time = self.data.candles[25].close_time

    def ledger(self, name="execution.jsonl"):
        return JsonlExecutionLedger(Path(self.temp.name) / name)

    def run_report(self, alpha=None, *, datasets=None, policy=POLICY, name="execution.jsonl", config=None):
        return run_backtest(
            datasets=datasets or (self.data,),
            initial_account=account(),
            risk_policy=policy,
            alpha_decider=alpha or FixtureAlpha(),
            config=config or BacktestConfig("test-code", "fixture-alpha"),
            ledger=self.ledger(name),
        )

    def test_complete_buy_sell_uses_real_execution_and_exact_costs(self):
        alpha = FixtureAlpha({
            ("BTCUSDT", self.buy_time): AlphaAction.BUY,
            ("BTCUSDT", self.sell_time): AlphaAction.SELL,
        })
        report = self.run_report(alpha)
        executions = [item for item in report.decisions if item.execution_status == "FILLED"]
        self.assertEqual([item.action for item in executions], ["BUY", "SELL"])
        self.assertEqual(report.result.trade_count, 1)
        self.assertEqual(len(report.completed_trades), 1)
        trade = report.completed_trades[0]
        self.assertEqual(trade.net_pnl, trade.gross_pnl - trade.allocated_entry_fee - trade.exit_fee)
        self.assertEqual(report.result.fees, sum((item.fee for item in executions), D("0")))
        self.assertEqual(report.result.slippage, sum((item.slippage for item in executions), D("0")))
        self.assertEqual(report.final_equity, report.equity_curve[-1].equity)
        records = self.ledger().read()
        self.assertEqual(len(records), 3)

    def test_features_are_causal_and_future_candles_do_not_reach_callback(self):
        alpha = FixtureAlpha()
        self.run_report(alpha)
        first = alpha.seen[0]
        self.assertEqual(first.timestamp, self.data.candles[20].close_time)
        self.assertEqual(first.symbol, "BTCUSDT")
        self.assertTrue(all(candle.close_time <= first.timestamp for candle in self.data.candles[:21]))
        self.assertEqual([item.timestamp for item in alpha.seen], sorted(item.timestamp for item in alpha.seen))

    def test_risk_rejection_never_reaches_execution(self):
        alpha = FixtureAlpha({("BTCUSDT", self.buy_time): AlphaAction.BUY}, gross_edge=D("0"))
        report = self.run_report(alpha)
        rejected = next(item for item in report.decisions if item.timestamp == self.buy_time)
        self.assertFalse(rejected.risk_approved)
        self.assertIsNone(rejected.execution_status)
        self.assertIn("edge", rejected.rejection_reason)
        self.assertEqual(len(self.ledger().read()), 1)
        self.assertTrue(all(point.cash == D("20") for point in report.equity_curve))

    def test_deterministic_across_repeated_runs_and_input_symbol_order(self):
        eth = dataset("ETHUSDT")
        first = self.run_report(datasets=(self.data, eth), name="one.jsonl")
        second = self.run_report(datasets=(eth, self.data), name="two.jsonl")
        self.assertEqual(first, second)

    def test_rejects_forged_ordering_and_bad_close_boundary(self):
        forged = dataset(count=25)
        candles = list(forged.candles)
        candles[2], candles[3] = candles[3], candles[2]
        object.__setattr__(forged, "candles", tuple(candles))
        with self.assertRaisesRegex(EvaluationError, "canonical dataset"):
            self.run_report(datasets=(forged,), name="forged.jsonl")

        malformed = dataset(count=25)
        changed = list(malformed.candles)
        object.__setattr__(changed[0], "close_time", changed[0].open_time + timedelta(minutes=2))
        with self.assertRaisesRegex(EvaluationError, "one-minute"):
            self.run_report(datasets=(malformed,), name="malformed.jsonl")

    def test_undefined_metrics_are_explicit(self):
        report = self.run_report()
        self.assertEqual(report.result.trade_count, 0)
        self.assertEqual(report.result.expected_value, D("0"))
        self.assertIn("profit_factor", report.result.undefined_metrics)
        self.assertIn("expected_value", report.result.undefined_metrics)

    def test_partial_sell_allocates_entry_fee_and_keeps_open_exposure(self):
        partial = replace(POLICY, sell_fraction=D("0.5"))
        alpha = FixtureAlpha({
            ("BTCUSDT", self.buy_time): AlphaAction.BUY,
            ("BTCUSDT", self.sell_time): AlphaAction.SELL,
        })
        report = self.run_report(alpha, policy=partial)
        trade = report.completed_trades[0]
        buy_fee = next(item.fee for item in report.decisions if item.action == "BUY" and item.risk_approved)
        buy_quantity = D(json.loads(self.ledger().read()[1])["intent"]["request"]["quantity"])
        self.assertEqual(trade.allocated_entry_fee, buy_fee * trade.quantity / buy_quantity)
        final = report.equity_curve[-1]
        self.assertGreater(final.gross_exposure, D("0"))
        self.assertEqual(final.equity, final.cash + final.gross_exposure)
        running = report.initial_equity
        for point in report.equity_curve:
            self.assertGreaterEqual(point.running_peak_equity, point.equity)
            self.assertGreaterEqual(point.running_peak_equity, running)
            running = point.running_peak_equity

    def test_utc_day_start_equity_resets_causally(self):
        late = dataset(count=45, start=datetime(2026, 1, 1, 23, 35, tzinfo=timezone.utc))
        report = self.run_report(datasets=(late,), name="day.jsonl")
        before = [point for point in report.equity_curve if point.timestamp.date().day == 1]
        after = [point for point in report.equity_curve if point.timestamp.date().day == 2]
        self.assertTrue(before and after)
        self.assertEqual(after[0].day_start_equity, after[0].equity)
        self.assertEqual({point.day_start_equity for point in after}, {after[0].day_start_equity})

    def test_fresh_ledger_is_required(self):
        first = self.ledger()
        self.run_report()
        with self.assertRaisesRegex(EvaluationError, "fresh"):
            run_backtest(
                datasets=(self.data,), initial_account=account(), risk_policy=POLICY,
                alpha_decider=FixtureAlpha(), config=BacktestConfig("test-code", "fixture-alpha"),
                ledger=first,
            )


class WalkForwardAndMonteCarloTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = dataset(count=100)

    def test_walk_forward_uses_past_only_training_and_aggregates_folds(self):
        windows = []

        def build(window):
            windows.append(window)
            return BuiltAlpha(f"fixture-{len(windows)}", FixtureAlpha())

        report = run_walk_forward(
            datasets=(self.data,),
            initial_account=account(),
            risk_policy=POLICY,
            base_config=BacktestConfig("test-code", "base"),
            walk_config=WalkForwardConfig(30, 20, 20),
            alpha_builder=build,
            ledger_factory=lambda index: JsonlExecutionLedger(Path(self.temp.name) / f"fold-{index}.jsonl"),
        )
        self.assertGreaterEqual(len(report.folds), 3)
        self.assertEqual(len(windows), len(report.folds))
        for window, fold in zip(windows, report.folds, strict=True):
            self.assertLess(max(c.close_time for c in window.candles_by_symbol["BTCUSDT"]), fold.boundary.validation_start)
            self.assertGreaterEqual(fold.report.evaluation_start, fold.boundary.validation_start)
            self.assertLess(fold.report.evaluation_end, fold.boundary.validation_end_exclusive)
        self.assertEqual(report.aggregate.trade_count, sum(f.report.result.trade_count for f in report.folds))

    def test_seeded_monte_carlo_is_reproducible_and_uses_observed_indices(self):
        buy = self.data.candles[20].close_time
        sell = self.data.candles[30].close_time
        source = run_backtest(
            datasets=(self.data,), initial_account=account(), risk_policy=POLICY,
            alpha_decider=FixtureAlpha({
                ("BTCUSDT", buy): AlphaAction.BUY,
                ("BTCUSDT", sell): AlphaAction.SELL,
            }),
            config=BacktestConfig("test-code", "fixture-alpha"),
            ledger=JsonlExecutionLedger(Path(self.temp.name) / "source.jsonl"),
        )
        config = MonteCarloConfig(25, 12345, 5)
        first = run_monte_carlo(source, config)
        second = run_monte_carlo(source, config)
        different = run_monte_carlo(source, replace(config, seed=54321))
        self.assertEqual(first, second)
        self.assertNotEqual(first.run_id, different.run_id)
        self.assertNotEqual(
            tuple(item.sampled_indices for item in first.outcomes),
            tuple(item.sampled_indices for item in different.outcomes),
        )
        self.assertEqual(len(first.outcomes), 25)
        self.assertTrue(all(
            len(outcome.sampled_indices) == len(source.period_returns)
            and all(0 <= index < len(source.period_returns) for index in outcome.sampled_indices)
            for outcome in first.outcomes
        ))
        short_start = self.data.candles[25].close_time
        too_short = run_backtest(
            datasets=(self.data,), initial_account=account(), risk_policy=POLICY,
            alpha_decider=FixtureAlpha(),
            config=BacktestConfig(
                "test-code", "fixture-alpha", short_start, short_start + timedelta(minutes=1),
            ),
            ledger=JsonlExecutionLedger(Path(self.temp.name) / "short.jsonl"),
        )
        self.assertEqual(len(too_short.period_returns), 0)
        with self.assertRaisesRegex(ValueError, "at least two"):
            run_monte_carlo(too_short, config)


class MetricTests(unittest.TestCase):
    def test_trade_metrics_drawdown_and_defined_ratios(self):
        t1 = START + timedelta(minutes=1)
        t2 = START + timedelta(minutes=2)
        t3 = START + timedelta(minutes=3)
        curve = (
            EquityPoint(t1, D("20"), D("20"), D("0"), D("20"), D("20")),
            EquityPoint(t2, D("18"), D("8"), D("10"), D("20"), D("20")),
            EquityPoint(t3, D("21"), D("21"), D("0"), D("20"), D("21")),
        )
        trades = (
            CompletedTrade(t2, "BTCUSDT", D("1"), D("10"), D("12.2"), D("0.1"), D("0.1"), D("2.2"), D("2")),
            CompletedTrade(t3, "BTCUSDT", D("1"), D("12"), D("11.2"), D("0.1"), D("0.1"), D("-0.8"), D("-1")),
        )
        returns = (D("-0.1"), D("0.1666666666666666666666666667"))
        result = calculate_metrics(
            run_id="metric-fixture", timestamp=t3, initial_equity=D("20"), final_equity=D("21"),
            equity_curve=curve, period_returns=returns, completed_trades=trades,
            fees=D("0.4"), slippage=D("0.3"), annualization_periods=365 * 24 * 60,
        )
        self.assertEqual(result.expected_value, D("0.5"))
        self.assertEqual(result.net_profit, D("1"))
        self.assertEqual(result.maximum_drawdown, D("0.1"))
        self.assertEqual(result.profit_factor, D("2"))
        self.assertEqual(result.win_rate, D("0.5"))
        self.assertEqual(result.trade_count, 2)
        self.assertEqual(result.average_trade, D("0.5"))
        with localcontext(arithmetic()):
            expected_exposure = (D("10") / D("18")) / D("3")
        self.assertEqual(result.exposure, expected_exposure)
        self.assertEqual(result.fees, D("0.4"))
        self.assertEqual(result.slippage, D("0.3"))
        self.assertNotIn("sharpe", result.undefined_metrics)
        self.assertNotIn("sortino", result.undefined_metrics)
        self.assertNotEqual(result.sharpe, D("0"))
        self.assertNotEqual(result.sortino, D("0"))

    def test_maximum_drawdown_known_path(self):
        self.assertEqual(maximum_drawdown(D("100"), (D("120"), D("90"), D("110"))), D("0.25"))


if __name__ == "__main__":
    unittest.main()

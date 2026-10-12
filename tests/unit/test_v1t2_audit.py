"""Narrow regression evidence for the V1-T2 final audit; fixtures are not Alpha."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D, localcontext
from pathlib import Path
from random import Random
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from quantos.application.evaluation import EvaluationError, _TradeBook, run_backtest, run_walk_forward
from quantos.application.risk_execution import TradingStep
from quantos.domain.alpha import AlphaAction
from quantos.domain.evaluation import (
    BacktestConfig, BuiltAlpha, EquityPoint, MonteCarloConfig, WalkForwardConfig,
    calculate_metrics, equity_returns, run_monte_carlo,
)
from quantos.domain.evaluation.metrics import _ratios
from quantos.domain.execution import OrderSide
from quantos.domain.execution.core import ExecutionEngine
from quantos.domain.features import FeatureVector, compute_feature_vector
from quantos.domain.market_data import validate_candle_sequence
from quantos.domain.risk.engine import RiskEngine
from quantos.domain.runtime_contracts import arithmetic, canonical, identity
from quantos.infrastructure.storage.execution_ledger import JsonlExecutionLedger
from tests.unit.test_v1t2_evaluation import START, POLICY, COSTS, FixtureAlpha, account, dataset
from tests.unit.test_v1t1 import alpha, context, T


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = dataset()
        self.counter = 0

    def run_report(self, *, data=None, callback=None, policy=POLICY, config=None):
        self.counter += 1
        ledger = JsonlExecutionLedger(self.root / f"run-{self.counter}.jsonl")
        report = run_backtest(
            datasets=data or (self.data,), initial_account=account(), risk_policy=policy,
            alpha_decider=callback or FixtureAlpha(),
            config=config or BacktestConfig("audit-code", "audit-alpha"), ledger=ledger,
        )
        return report, ledger

    def test_alpha_boundary_rejects_mismatched_or_invalid_results(self):
        cases = (
            lambda result: replace(result, decision=replace(result.decision, timestamp=START)),
            lambda result: replace(result, decision=replace(result.decision, symbol="ETHUSDT")),
            lambda result: replace(result, decision=replace(result.decision, feature_version="wrong")),
            lambda result: replace(result, decision=replace(result.decision, strategy_version="")),
            lambda result: replace(result, gross_edge_rate=D("NaN")),
            lambda result: replace(result, gross_edge_rate=0.1),
            lambda result: replace(result, volatility=D("-1")),
            lambda result: replace(result, volatility=D("Infinity")),
        )
        for change in cases:
            with self.subTest(change=cases.index(change)), self.assertRaises(ValueError):
                self.run_report(callback=lambda feature: change(FixtureAlpha()(feature)))
        calls = []
        def changed_version(feature):
            calls.append(feature)
            result = FixtureAlpha()(feature)
            return replace(result, decision=replace(result.decision, model_version=str(len(calls))))
        with self.assertRaisesRegex(EvaluationError, "version changed"):
            self.run_report(callback=changed_version)

    def test_model_score_never_supplies_edge_and_hold_cannot_execute(self):
        def decide(feature):
            result = FixtureAlpha()(feature)
            return replace(result, decision=replace(result.decision, action=AlphaAction.BUY,
                                                   model_score=D("999")), gross_edge_rate=D("0"))
        report, ledger = self.run_report(callback=decide)
        self.assertTrue(all(not item.risk_approved for item in report.decisions))
        self.assertEqual(len(ledger.read()), 1)
        held, ledger = self.run_report()
        self.assertTrue(all(item.execution_status is None for item in held.decisions))
        self.assertEqual(len(ledger.read()), 1)

    def test_feature_arguments_are_causal_and_warmup_never_calls_alpha(self):
        fixture = FixtureAlpha()
        seen = []
        def features(candles, *, decision_time):
            self.assertTrue(all(item.close_time <= decision_time for item in candles))
            seen.append((len(candles), decision_time))
            return compute_feature_vector(candles, decision_time=decision_time)
        with patch("quantos.application.evaluation.compute_feature_vector", side_effect=features):
            self.run_report(callback=fixture)
        self.assertEqual(seen[0][0], 1)
        self.assertEqual(fixture.seen[0].timestamp, self.data.candles[20].close_time)
        self.assertTrue(all(type(item) is FeatureVector for item in fixture.seen))

    def test_shifted_opens_mixed_completion_grids_and_forged_identity_fail(self):
        shifted = dataset(start=START + timedelta(seconds=10))
        with self.assertRaisesRegex(EvaluationError, "UTC minutes"):
            self.run_report(data=(shifted,))
        inclusive = dataset("ETHUSDT")
        inclusive = replace(inclusive, candles=tuple(replace(c, close_time=c.close_time-timedelta(milliseconds=1)) for c in inclusive.candles))
        self.run_report(data=(inclusive,))  # A consistent inclusive-end grid is valid.
        with self.assertRaisesRegex(EvaluationError, "completion grid"):
            self.run_report(data=(self.data, inclusive))
        forged = dataset()
        object.__setattr__(forged.identity, "source", "")
        with self.assertRaises(ValueError):
            self.run_report(data=(forged,))

    def test_same_minute_second_decision_uses_first_fill_and_current_marks(self):
        eth = dataset("ETHUSDT")
        buy_time = self.data.candles[20].close_time
        actions = {(symbol, buy_time): AlphaAction.BUY for symbol in ("BTCUSDT", "ETHUSDT")}
        captured = []
        evaluate = RiskEngine.evaluate
        def observe(engine, decision, ctx):
            if decision.timestamp == buy_time:
                captured.append((decision, ctx))
            return evaluate(engine, decision, ctx)
        policy = replace(POLICY, max_exposure_fraction=D("0.4"))
        with patch.object(RiskEngine, "evaluate", observe):
            report, ledger = self.run_report(data=(eth, self.data), callback=FixtureAlpha(actions), policy=policy)
        self.assertEqual([d.symbol for d, _ in captured], ["BTCUSDT", "ETHUSDT"])
        first, second = (ctx for _, ctx in captured)
        self.assertLess(second.account.balances["USDT"], first.account.balances["USDT"])
        self.assertEqual(len(second.account.positions), 1)
        self.assertEqual({m.candle.symbol for m in second.markets}, {"BTCUSDT", "ETHUSDT"})
        self.assertTrue(all(m.candle.close_time == buy_time for m in second.markets))
        self.assertEqual(first.day_start_equity, second.day_start_equity)
        results = [d for d in report.decisions if d.timestamp == buy_time]
        self.assertTrue(results[0].risk_approved)
        self.assertIn("exposure", results[1].rejection_reason)
        self.assertEqual(len(ledger.read()), 2)

    def test_accumulation_partial_and_final_sell_reconcile_exact_costs(self):
        ledger = JsonlExecutionLedger(self.root / "accounting.jsonl")
        engine = ExecutionEngine(account(T), COSTS, ledger)
        book = _TradeBook()
        fills = []
        for index, (action, price, notional, fraction) in enumerate((
            (AlphaAction.BUY, D("100"), D("5"), D("1")),
            (AlphaAction.BUY, D("120"), D("6"), D("1")),
            (AlphaAction.SELL, D("130"), D("5"), D("0.5")),
            (AlphaAction.SELL, D("90"), D("5"), D("1")),
        )):
            timestamp = T + timedelta(minutes=index)
            before = engine.snapshot
            ctx = replace(context(before, timestamp, price), peak_equity=D("30"))
            policy = replace(POLICY, order_notional=notional, sell_fraction=fraction)
            result = TradingStep(RiskEngine(policy), engine).run(alpha(action, timestamp), ctx)
            self.assertTrue(result.risk.approved, result.risk.rejection_reason)
            fills.append(result.execution)
            book.observe(before=before, result=result.execution, side=OrderSide(action.value), symbol="BTCUSDT")
            if index == 1:
                self.assertEqual(engine.snapshot.positions[0].average_entry_price, D("110.22"))
                self.assertEqual(book.completed, [])
        self.assertEqual(engine.snapshot.positions, ())
        self.assertEqual(book._entry_fees.get("BTCUSDT", D("0")), D("0"))
        with localcontext(arithmetic()):
            self.assertEqual(sum((t.allocated_entry_fee for t in book.completed), D("0")), fills[0].fee + fills[1].fee)
            self.assertEqual(sum((t.exit_fee for t in book.completed), D("0")), fills[2].fee + fills[3].fee)
            self.assertEqual(sum((t.net_pnl for t in book.completed), D("0")), engine.snapshot.balances["USDT"] - D("20"))
        self.assertEqual(len(book.completed), 2)
        self.assertGreater(book.completed[0].net_pnl, 0)
        self.assertLess(book.completed[1].net_pnl, 0)
        engine.reconcile()

    def test_reports_and_ledger_are_independent_of_ambient_decimal_context(self):
        actions = {("BTCUSDT", self.data.candles[i].close_time): action for i, action in (
            (20, AlphaAction.BUY), (22, AlphaAction.BUY), (25, AlphaAction.SELL))}
        expected, ledger = self.run_report(callback=FixtureAlpha(actions))
        mc_config = MonteCarloConfig(7, 72, 3)
        expected_mc = run_monte_carlo(expected, mc_config)
        with localcontext() as ambient:
            ambient.prec = 5
            actual, other = self.run_report(callback=FixtureAlpha(actions))
            actual_mc = run_monte_carlo(actual, mc_config)
        self.assertEqual(canonical(actual), canonical(expected))
        self.assertEqual(ledger.read(), other.read())
        self.assertEqual(canonical(actual_mc), canonical(expected_mc))

    def test_identity_is_stable_across_processes_and_sensitive_to_inputs(self):
        first, _ = self.run_report()
        code = '''from tempfile import TemporaryDirectory
from pathlib import Path
from tests.unit.test_v1t2_evaluation import *
with TemporaryDirectory() as d:
    report = run_backtest(datasets=(dataset(),), initial_account=account(), risk_policy=POLICY,
        alpha_decider=FixtureAlpha(), config=BacktestConfig("audit-code", "audit-alpha"),
        ledger=JsonlExecutionLedger(Path(d)/"elsewhere.jsonl"))
    print(report.run_id)
'''
        child = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
        self.assertEqual(first.run_id, child.stdout.strip())
        for config in (
            BacktestConfig("other-code", "audit-alpha"), BacktestConfig("audit-code", "other-alpha"),
            BacktestConfig("audit-code", "audit-alpha", decision_start=self.data.candles[1].close_time),
            BacktestConfig("audit-code", "audit-alpha", annualization_periods=100),
        ):
            self.assertNotEqual(first.run_id, self.run_report(config=config)[0].run_id)
        for policy in (replace(POLICY, order_notional=D("6")), replace(POLICY, costs=replace(COSTS, fee_rate=D("0.01")))):
            self.assertNotEqual(first.run_id, self.run_report(policy=policy)[0].run_id)
        for name in ("strategy_version", "model_version"):
            def decide(feature):
                value = FixtureAlpha()(feature)
                return replace(value, decision=replace(value.decision, **{name: "other-version"}))
            self.assertNotEqual(first.run_id, self.run_report(callback=decide)[0].run_id)
        with patch("quantos.application.evaluation.FEATURE_VERSION", "other-feature"):
            self.assertNotEqual(first.run_id, self.run_report()[0].run_id)
        changed = validate_candle_sequence(replace(self.data.identity, source="other-source"), self.data.candles)
        self.assertNotEqual(first.run_id, self.run_report(data=(changed,))[0].run_id)

    def test_returns_omit_first_and_sortino_uses_all_observations(self):
        curve = tuple(EquityPoint(START+timedelta(minutes=i), x, x, D("0"), D("100"), max(D("100"), x))
                      for i, x in enumerate((D("100"), D("110"), D("99"), D("99"))))
        self.assertEqual(equity_returns(D("120"), curve), (D("0.1"), D("-0.1"), D("0")))
        values = (D("0.2"), D("-0.1"), D("0"))
        sharpe, sortino, undefined = _ratios(values, 525600)
        with localcontext(arithmetic()):
            mean = D("0.1") / 3
            expected_sortino = mean / (D("0.01") / 3).sqrt() * D(525600).sqrt()
            expected_sharpe = mean / (sum(((x-mean)**2 for x in values), D("0"))/3).sqrt() * D(525600).sqrt()
        self.assertEqual(sortino, expected_sortino)
        self.assertEqual(sharpe, expected_sharpe)
        self.assertEqual(undefined, ())
        self.assertEqual(_ratios((D("0"), D("0")), 525600)[2], ("sharpe", "sortino"))

    def test_open_position_marked_drawdown_and_utc_rollover(self):
        data = dataset(start=START + timedelta(hours=23, minutes=35))
        changed = []
        for i, candle in enumerate(data.candles):
            price = D("100") if i < 21 else (D("80") if i < 27 else D("110"))
            changed.append(replace(candle, open=price, close=price, high=price+D("1"), low=price-D("1")))
        # Keep varying warmup prices so Features become available for the BUY.
        changed[:20] = data.candles[:20]
        data = replace(data, candles=tuple(changed))
        actions = {("BTCUSDT", changed[20].close_time): AlphaAction.BUY}
        report, ledger = self.run_report(data=(data,), callback=FixtureAlpha(actions), policy=replace(POLICY, costs=replace(COSTS, fee_rate=D("0"), slippage_rate=D("0"))))
        self.assertEqual(len(ledger.read()), 2)
        self.assertEqual(report.result.trade_count, 0)
        self.assertEqual(report.result.maximum_drawdown, D("0.05"))
        midnight = next(p for p in report.equity_curve if p.timestamp.hour == 0 and p.timestamp.minute == 0)
        self.assertEqual(midnight.day_start_equity, D("19"))
        self.assertEqual(report.final_equity, D("20.5"))

    def test_monte_carlo_compounding_and_drawdown_match_sampled_path(self):
        template, _ = self.run_report()
        curve = tuple(EquityPoint(START+timedelta(minutes=i), x, x, D("0"), D("100"), max(D("110"), x))
                      for i, x in enumerate((D("100"), D("110"), D("88"), D("110"))))
        returns = equity_returns(D("100"), curve)
        self.assertEqual(returns, (D("0.1"), D("-0.2"), D("0.25")))
        run_id = identity(curve)
        result = calculate_metrics(run_id=run_id, timestamp=curve[-1].timestamp,
            initial_equity=D("100"), final_equity=D("110"), equity_curve=curve,
            period_returns=returns, completed_trades=(), fees=D("0"), slippage=D("0"),
            annualization_periods=525600)
        source = replace(template, run_id=run_id, result=result, equity_curve=curve,
            initial_equity=D("100"), final_equity=D("110"), period_returns=returns,
            evaluation_start=curve[0].timestamp, evaluation_end=curve[-1].timestamp,
            decisions=(), completed_trades=())
        config = MonteCarloConfig(9, 4, 3)
        report = run_monte_carlo(source, config)
        rng = Random(config.seed)
        for outcome in report.outcomes:
            index = rng.randrange(len(source.period_returns))
            indices = []
            with localcontext(arithmetic()):
                equity = peak = source.initial_equity
                dd = D("0")
                for offset in range(len(source.period_returns)):
                    if offset and rng.randrange(config.mean_block_length) == 0:
                        index = rng.randrange(len(source.period_returns))
                    indices.append(index)
                    equity *= 1 + source.period_returns[index]
                    peak = max(peak, equity)
                    dd = max(dd, (peak-equity)/peak)
                    index = (index+1) % len(source.period_returns)
                self.assertEqual(outcome.terminal_pnl, equity-source.initial_equity)
                self.assertEqual(outcome.maximum_drawdown, dd)
            self.assertEqual(outcome.sampled_indices, tuple(indices))
        malformed = replace(source)
        object.__setattr__(malformed, "period_returns", tuple(D("0.5") for _ in source.period_returns))
        with self.assertRaisesRegex(ValueError, "equity evidence"):
            run_monte_carlo(malformed, config)

    def test_walk_forward_pools_evidence_and_rejects_overlapping_validation(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            WalkForwardConfig(30, 20, 10)
        windows = []
        def build(window):
            windows.append(window)
            self.assertTrue(all(c.close_time < window.boundary.validation_start for candles in window.candles_by_symbol.values() for c in candles))
            start = window.boundary.validation_start
            return BuiltAlpha("fold-fixture", FixtureAlpha({
                ("BTCUSDT", start): AlphaAction.BUY,
                ("BTCUSDT", start+timedelta(minutes=5)): AlphaAction.SELL,
            }))
        data = dataset(count=90)
        report = run_walk_forward(datasets=(data,), initial_account=account(), risk_policy=POLICY,
            base_config=BacktestConfig("audit", "fold"), walk_config=WalkForwardConfig(30, 20, 20),
            alpha_builder=build, ledger_factory=lambda i: JsonlExecutionLedger(self.root/f"fold-{i}.jsonl"))
        self.assertEqual(len(report.folds), 3)
        pooled = tuple(r for f in report.folds for r in f.report.period_returns)
        s, so, _ = _ratios(pooled, 525600)
        self.assertEqual(report.aggregate.sharpe, s)
        self.assertEqual(report.aggregate.sortino, so)
        self.assertEqual(report.aggregate.maximum_drawdown, max(f.report.result.maximum_drawdown for f in report.folds))
        self.assertIn("independent flat accounts", report.aggregation_semantics)
        with localcontext(arithmetic()):
            pnls = [t.net_pnl for f in report.folds for t in f.report.completed_trades]
            self.assertEqual(report.aggregate.expected_value, sum(pnls)/len(pnls))
            self.assertEqual(report.aggregate.net_profit, sum(f.report.result.net_profit for f in report.folds))
        with localcontext() as ambient:
            ambient.prec = 5
            repeated = run_walk_forward(datasets=(data,), initial_account=account(), risk_policy=POLICY,
                base_config=BacktestConfig("audit", "fold"), walk_config=WalkForwardConfig(30, 20, 20),
                alpha_builder=build, ledger_factory=lambda i: JsonlExecutionLedger(self.root/f"repeat-{i}.jsonl"))
        self.assertEqual(canonical(report), canonical(repeated))


if __name__ == "__main__":
    unittest.main()

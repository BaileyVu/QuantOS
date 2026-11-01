"""Backtest -> walk-forward -> Monte Carlo integration through V1 boundaries."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quantos.application.evaluation import run_backtest, run_walk_forward
from quantos.domain.alpha import AlphaAction
from quantos.domain.evaluation import BacktestConfig, BuiltAlpha, MonteCarloConfig, WalkForwardConfig, run_monte_carlo
from quantos.infrastructure.storage.execution_ledger import JsonlExecutionLedger
from tests.unit.test_v1t2_evaluation import FixtureAlpha, POLICY, account, dataset


class EvaluationVerticalTests(unittest.TestCase):
    def test_complete_evaluation_lifecycle(self):
        data = dataset(count=100)
        buy = data.candles[20].close_time
        sell = data.candles[30].close_time
        with TemporaryDirectory() as directory:
            root = Path(directory)
            backtest = run_backtest(
                datasets=(data,), initial_account=account(), risk_policy=POLICY,
                alpha_decider=FixtureAlpha({
                    ("BTCUSDT", buy): AlphaAction.BUY,
                    ("BTCUSDT", sell): AlphaAction.SELL,
                }),
                config=BacktestConfig("test-code", "fixture-alpha"),
                ledger=JsonlExecutionLedger(root / "backtest.jsonl"),
            )
            self.assertEqual(backtest.result.trade_count, 1)
            self.assertEqual(len([d for d in backtest.decisions if d.execution_status == "FILLED"]), 2)

            walk = run_walk_forward(
                datasets=(data,), initial_account=account(), risk_policy=POLICY,
                base_config=BacktestConfig("test-code", "walk-base"),
                walk_config=WalkForwardConfig(30, 20, 20),
                alpha_builder=lambda window: BuiltAlpha("past-only-fixture", FixtureAlpha()),
                ledger_factory=lambda index: JsonlExecutionLedger(root / f"fold-{index}.jsonl"),
            )
            self.assertGreaterEqual(len(walk.folds), 3)

            monte_carlo = run_monte_carlo(backtest, MonteCarloConfig(20, 7, 4))
            self.assertEqual(monte_carlo.source_run_id, backtest.run_id)
            self.assertEqual(len(monte_carlo.outcomes), 20)


if __name__ == "__main__":
    unittest.main()

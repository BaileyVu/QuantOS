import unittest
from decimal import Decimal as D

from quantos.paper.risk import Account, Policy, evaluate
from quantos.paper.strategies.fixtures import lifecycle_snapshots


class V4LifecycleFixtureTests(unittest.TestCase):
    def test_every_library_fixture_signals_and_passes_default_risk(self):
        pairs = lifecycle_snapshots(1_800_000_123_456)
        self.assertEqual(len(pairs), 6)
        ids = []
        for strategy, snapshot in pairs:
            signal = strategy.evaluate(snapshot)
            self.assertIsNotNone(signal, strategy.strategy_id)
            ids.append(signal.strategy_id)
            decision = evaluate(signal, snapshot.instrument, snapshot.quote,
                                Account(D("10000"), D("10000"), D("10000")),
                                Policy(), snapshot.at_ms)
            self.assertTrue(decision.approved, f"{strategy.strategy_id}: {decision.reason}")
            self.assertGreater(decision.quantity, 0)
        self.assertEqual(ids, [
            "ema_atr_limit_pullback", "donchian_atr_stop_breakout", "tsmom_4h",
            "turtle_55", "vol_squeeze_breakout", "delist_short_e17",
        ])


if __name__ == "__main__":
    unittest.main()

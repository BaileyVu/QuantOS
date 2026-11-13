from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quantos.paper.engine import PaperEngine
from quantos.paper.journal import Journal
from quantos.paper.risk import Account, Demo200Policy, LivePolicy, evaluate
from quantos.paper.runtime import dispatch
from quantos.paper.strategies.ml5m_alts import Ml5mAltsStrategy
from quantos.paper.types import Candle, D, Snapshot
from tests.unit.test_v3_engine import INS, T, quote


class Ml5mAltsTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "signals.json"
        self.strategy = Ml5mAltsStrategy(self.path)
        self.instrument = replace(INS, symbol="ETHUSDC")
        self.close = T + 299_999
        self.now = self.close + 1
        self.candle = Candle(T, self.close, D("100"), D("101"), D("99"), D("100"), D("100"))
        self.snapshot = Snapshot(self.now, self.instrument, self.quote(self.now), (), (self.candle,))
        self.payload = {"bar_close_ms": self.close, "thr": .57,
                        "signals": [{"symbol": "ETHUSDC", "side": "LONG", "score": .57}]}
        self.path.write_text(json.dumps(self.payload))

    def quote(self, time):
        return replace(quote(time=time), symbol="ETHUSDC")

    def test_only_current_closed_bar_and_valid_usdc_twin(self):
        proposal = self.strategy.evaluate(self.snapshot)
        self.assertEqual(proposal.strategy_id, "ml5m_alts")
        self.assertEqual(proposal.entry_type, "MARKET")
        self.assertEqual(proposal.stop_loss, D("96.00"))
        self.assertEqual(proposal.take_profit, D("102.02"))
        self.assertEqual(proposal.maximum_hold_seconds, 86_400)
        self.assertIsNone(self.strategy.evaluate(replace(self.snapshot, at_ms=self.now + 300_000)))
        self.assertIsNone(self.strategy.evaluate(replace(self.snapshot, at_ms=self.close - 1)))
        self.payload["signals"][0]["score"] = float("nan")
        self.path.write_text(json.dumps(self.payload))
        self.assertIsNone(self.strategy.evaluate(self.snapshot))
        self.assertIn("ml5m_alts", Demo200Policy.enabled_strategies)
        self.assertNotIn("ml5m_alts", LivePolicy.enabled_strategies)

    def test_only_ml5m_uses_the_lower_reward_risk_floor(self):
        proposal = self.strategy.evaluate(self.snapshot)
        account = Account(D("200"), D("200"), D("200"))
        policy = Demo200Policy()
        self.assertEqual(policy.min_net_rr, D("1.2"))
        check = lambda signal: evaluate(signal, self.instrument, self.snapshot.quote, account, policy, self.now)
        self.assertTrue(check(proposal).approved)
        other = replace(proposal, strategy_id="vol_squeeze_breakout", strategy_version="1.0.0",
                        maximum_hold_seconds=14_400, entry_type="STOP", entry_price=D("100.02"))
        self.assertEqual(check(other).reason, "NET_REWARD_RISK")
        self.assertTrue(check(replace(other, take_profit=D("106"))).approved)
        self.assertEqual(check(replace(proposal, take_profit=D("101"))).reason, "NET_REWARD_RISK")

    def test_pd_dispatch_and_completed_bar_soft_stop(self):
        journal = Journal(self.path.parent / "journal.db", "pD", "synthetic", initial_balance="200")
        self.addCleanup(journal.close)
        engine = PaperEngine(journal, Demo200Policy())
        engine.recover(self.now)
        engine.connection(True, self.now)
        trace = {}
        dispatch([engine], "scan", [self.snapshot], self.now, strategies=[self.strategy], external_trace=trace)
        self.assertIn("ETHUSDC", trace["ml5m_alts"])
        filled_at = self.now + 1_000
        engine.quote(self.quote(filled_at), filled_at)
        order = journal.orders()[0]
        self.assertEqual(order["state"], "PROTECTED")
        threshold = D(order["average"]) * D(".98")
        next_candle = Candle(T + 300_000, self.close + 300_000, D("100"), D("101"),
                             D("95"), threshold, D("100"))
        snapshot = replace(self.snapshot, at_ms=next_candle.close_ms - 1, candles_5m=(self.candle, next_candle))
        engine.bar_close_stops(snapshot, snapshot.at_ms)
        self.assertEqual(journal.orders()[0]["state"], "PROTECTED")
        snapshot = replace(snapshot, at_ms=next_candle.close_ms + 1)
        dispatch([engine], "scan", [snapshot], snapshot.at_ms)
        self.assertEqual(journal.orders()[0]["close_reason"], "SOFT_STOP_5M")


class ScoreSummaryTests(unittest.TestCase):
    def test_bar_summary_reports_tradeable_max_and_gap(self):
        from quantos.paper.ml5m_scoring.__main__ import bar_summary
        s = bar_summary({"SOLUSDT": 0.41, "ETHUSDT": 0.2, "XYZUSDT": 0.7}, 0.561)
        self.assertEqual((s["max_score"], s["max_symbol"]), (0.41, "SOLUSDC"))
        self.assertEqual(s["line"], "thr=0.561 max=0.410@SOLUSDC gap=-0.151 max_all=0.700@XYZUSDT")
        self.assertEqual(bar_summary({}, None)["line"], "thr=na max=na@None gap=na max_all=na@None")

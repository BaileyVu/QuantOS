from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from quantos.live.bridge import ExchangeMirror
from quantos.paper.engine import PaperEngine
from quantos.paper.journal import Journal
from quantos.paper.risk import Demo200Policy
from quantos.paper.runtime import Runtime, dispatch
from quantos.paper.sentiment import SentimentShadow
from quantos.paper.types import D, Snapshot
from tests.unit.test_live_bridge import FakeExchange, FlakyExchange, VoidingEngine, order
from tests.unit.test_v3_engine import INS, T, quote, signal


class LeverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.e = VoidingEngine()
        self.e.orders = [order()]

    def mirror(self, ex):
        return ExchangeMirror(ex, "r", Path(self.tmp.name), lambda *a: None)

    def test_silent_leverage_mismatch_closes_before_any_stop(self):
        ex = FakeExchange()
        ex.set_leverage = lambda s, lev: ex.leverage.update({s: "5"})
        m = self.mirror(ex)
        m.sync(self.e, 1000)
        self.assertTrue(m.kill.active)
        self.assertEqual(ex.pos["SOLUSDC"], 0)
        self.assertEqual(ex.algo_posts, 0)

    def test_position_query_failure_after_entry_closes_before_stop(self):
        ex = FakeExchange()
        def unavailable(symbol=None): raise ConnectionError("offline")
        ex.position_risk = unavailable
        m = self.mirror(ex)
        m.sync(self.e, 1000)
        self.assertTrue(m.kill.active)
        self.assertEqual(ex.pos["SOLUSDC"], 0)
        self.assertEqual(ex.algo_posts, 0)

    def test_pending_query_fails_repeatedly_then_succeeds_without_resubmit(self):
        ex = FlakyExchange()
        m = self.mirror(ex)
        for now in (1000, 3000, 5000, 9000, 15000):
            m.sync(self.e, now)
            self.assertEqual(m.book["a" * 32]["state"], "PENDING")
            self.assertFalse(m.kill.active)
        self.assertEqual(len(ex.orders), 1)
        ex.down = False
        m.sync(self.e, 19000)
        self.assertEqual(m.book["a" * 32]["state"], "OPEN")
        self.assertEqual((len(ex.orders), ex.algo_posts), (1, 1))
        self.assertEqual(self.e.voided, [])

    def test_pending_mismatch_flattens_instead_of_adopting(self):
        ex = FlakyExchange()
        m = self.mirror(ex)
        m.sync(self.e, 1000)
        ex.down = False
        ex.leverage["SOLUSDC"] = "5"
        m.sync(self.e, 3000)
        self.assertTrue(m.kill.active)
        self.assertEqual(ex.pos["SOLUSDC"], 0)
        self.assertEqual(ex.algo_posts, 0)


class SentimentFlagTests(unittest.TestCase):
    def test_missing_feed_is_opt_in_and_only_blocks_gated_strategies(self):
        for fail_closed in (False, True):
            with self.subTest(fail_closed=fail_closed), tempfile.TemporaryDirectory() as directory:
                j = Journal(Path(directory) / "pD.db", "pD", "synthetic", initial_balance="200")
                try:
                    e = PaperEngine(j, Demo200Policy())
                    e.recover(T); e.connection(True, T)
                    s = signal(strategy_id="tsmom_4h", strategy_version="1.0.0", entry_type="MARKET",
                               take_profit=None, maximum_hold_seconds=259200)
                    strategy = SimpleNamespace(strategy_id=s.strategy_id, evaluate=lambda _: s)
                    dispatch([e], "scan", [Snapshot(T, INS, quote(), (), ())], T, strategies=[strategy],
                             sentiment=SentimentShadow(None), sentiment_fail_closed=fail_closed)
                    self.assertEqual(bool(j.orders()), not fail_closed)
                    if fail_closed:
                        row = j.db.execute("SELECT payload FROM events WHERE kind='REJECTED_RISK'").fetchone()
                        self.assertIn("SENTIMENT_UNAVAILABLE", row[0])
                finally: j.close()

    def test_flag_without_file_still_creates_unavailable_feed(self):
        with tempfile.TemporaryDirectory() as d:
            r = Runtime(Path(d), sentiment_fail_closed=True)
            self.assertEqual(r.sentiment.read(T)["reason"], "NOT_CONFIGURED")
            self.assertIsNone(Runtime(Path(d)).sentiment)

    def test_invalid_nested_data_and_nonfinite_score_are_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sentiment.jsonl"
            at = datetime.fromtimestamp(T / 1000, timezone.utc).isoformat()
            for raw in ({"sentiment_raw": [1]}, {"sentiment_raw": {"news": {"BTCUSDT": "NaN"}}}):
                path.write_text(json.dumps({"time": at, **raw}))
                self.assertEqual(SentimentShadow(path).judge(signal(), T)["verdict"], "UNAVAILABLE")

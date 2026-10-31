import json
from pathlib import Path
import tempfile
import unittest

from quantos.paper.engine import PaperEngine
from quantos.paper.journal import Journal
from quantos.paper.live_inputs import OI_AVAILABILITY_MS, OpenInterestCache
from quantos.paper.risk import LivePolicy
from quantos.paper.selfcheck import self_check
from quantos.paper.sentiment import SentimentShadow
from quantos.paper.strategies import live_library
from quantos.paper.strategies.candidate_features import HOUR_MS
from quantos.paper.types import D, Instrument, OpenInterestValue, Quote, Snapshot
from tests.unit.test_v3_engine import T, signal

H = T // HOUR_MS * HOUR_MS


class FakeOI:
    def __init__(self): self.calls = []

    def open_interest_value(self, symbol, end_ms=None, limit=500):
        self.calls.append((symbol, end_ms))
        end = H if end_ms is None else end_ms // HOUR_MS * HOUR_MS
        return [(end - i * HOUR_MS, D(1000 + i)) for i in range(499, -1, -1)]


def snap(symbol, oi=(), cutoff=None):
    ins = Instrument(symbol, "USDC", "TRADING", D(".01"), D(".01"), D(".01"), D(1000), D(5))
    q = Quote(symbol, T, T, D(100), D("100.01"), D(10), D(10), D(100))
    return Snapshot(T, ins, q, (), (), oi_values=oi, quarterly_return_cutoff=cutoff,
                    calibration_end_ms=None if cutoff is None else 1)


class InputTests(unittest.TestCase):
    def test_oi_cache_backfills_once_and_marks_availability(self):
        m = FakeOI()
        cache = OpenInterestCache(m, ["SOLUSDT"])
        cache.refresh(H + 1000)
        cache.refresh(H + 2000)  # not yet due
        self.assertEqual(len(m.calls), 2)
        values = cache.values("SOLUSDT")
        self.assertEqual(len(values), 32 * 24)  # pruned to 32 days
        self.assertTrue(all(v.available_ms == v.time_ms + OI_AVAILABILITY_MS for v in values))

    def test_self_check_is_loud_when_inputs_missing(self):
        checks = self_check(live_library(), [snap("SOLUSDC"), snap("BTCUSDC")])
        self.assertEqual(checks["crash_flush_e2"]["level"], "critical")
        self.assertIn("2/2 majors", checks["crash_flush_e2"]["detail"])
        self.assertEqual(checks["alt_dip_reversal"]["level"], "critical")
        self.assertEqual(checks["tsmom_4h"]["level"], "critical")
        self.assertTrue(self_check(live_library(), [snap("SOLUSDC", cutoff=D("-.08"))])["alt_dip_reversal"]["ok"])

    def test_live_library_disables_reference_strategies(self):
        ids = {s.strategy_id for s in live_library()}
        self.assertTrue({"crash_flush_e2", "alt_dip_reversal", "tsmom_4h"} <= ids)
        self.assertFalse(ids & {"ema_atr_ref", "donchian_atr_ref"})


class SentimentTests(unittest.TestCase):
    def test_shadow_verdicts_and_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.jsonl"
            self.assertEqual(SentimentShadow(path).judge(signal(), T)["verdict"], "UNAVAILABLE")
            from datetime import datetime, timezone
            iso = datetime.fromtimestamp(T / 1000, timezone.utc).isoformat()
            path.write_text("old\n" + json.dumps({"time": iso, "sentiment_raw": {"news": {"BTCUSDT": -0.2}}}) + "\n")
            s = SentimentShadow(path)
            self.assertEqual(s.judge(signal(), T)["verdict"], "WOULD_BLOCK")
            self.assertEqual(s.judge(signal(side="SHORT"), T)["verdict"], "WOULD_ALLOW")
            self.assertEqual(s.judge(signal(), T + 4 * HOUR_MS)["verdict"], "UNAVAILABLE")


class KillTests(unittest.TestCase):
    def test_kill_pauses_and_cancels(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d) / "journal_pL.db", "pL", initial_balance="50")
            try:
                e = PaperEngine(j, LivePolicy())
                e.kill(T)
                e.kill(T + 1)
                self.assertEqual(j.get("paused"), "KILL_SWITCH")
                n = j.db.execute("SELECT COUNT(*) FROM events WHERE kind='KILL_SWITCH'").fetchone()[0]
                self.assertEqual(n, 1)
            finally:
                j.close()

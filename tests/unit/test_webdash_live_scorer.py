import os
import tempfile
import time
import unittest
from pathlib import Path

from quantos.paper.webdash import live_scorer_checks


class LiveScorerChecksTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.now_ms = int(time.time() * 1000)
        self.fresh = Path(self.dir.name) / "fresh.json"; self.fresh.write_text("{}")
        self.stale = Path(self.dir.name) / "stale.json"; self.stale.write_text("{}")
        old = time.time() - 3 * 3600
        os.utime(self.stale, (old, old))

    def tearDown(self):
        self.dir.cleanup()

    def check(self, scorers):
        bot = {"ok": False, "level": "critical", "detail": "scorer signals 469 min old"}
        sc = {"time_ms": 1, "checks": {"ml1h_pattern": dict(bot), "turtle_55": {"ok": True, "level": "ok", "detail": "x"}}}
        return live_scorer_checks(sc, self.now_ms, scorers)["checks"]

    def test_fresh_file_overrides_stale_bot_verdict(self):
        c = self.check({"ml1h_pattern": (str(self.fresh), 7200)})["ml1h_pattern"]
        self.assertTrue(c["ok"]); self.assertEqual(c["state"], "Scanning")
        self.assertFalse(c["bot"]["ok"]); self.assertEqual(c["bot_time_ms"], 1)

    def test_stale_and_missing_files_fail(self):
        self.assertEqual(self.check({"ml1h_pattern": (str(self.stale), 7200)})["ml1h_pattern"]["state"], "Stale")
        c = self.check({"ml1h_pattern": (str(self.fresh) + ".nope", 7200)})["ml1h_pattern"]
        self.assertEqual((c["ok"], c["state"]), (False, "Missing"))

    def test_other_and_absent_strategies_untouched(self):
        checks = self.check({"ml1h_pattern": (str(self.fresh), 7200), "ml5m_alts": (str(self.fresh), 1200)})
        self.assertNotIn("ml5m_alts", checks)
        self.assertEqual(checks["turtle_55"]["detail"], "x")


if __name__ == "__main__":
    unittest.main()

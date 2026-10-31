import json
from pathlib import Path
import tempfile
import unittest

from quantos.paper.journal import Journal
from quantos.paper.webdash import snapshot
from tests.unit.test_v3_engine import T


class WebdashTests(unittest.TestCase):
    def test_state_contract_reads_journal_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "run"
            state.mkdir()
            j = Journal(state / "journal_pL.db", "pL", initial_balance="50")
            with j.transaction():
                j.event(T, "SIGNAL", {"signal_id": "s1", "strategy_id": "crash_flush_e2", "symbol": "SOLUSDC", "side": "LONG"})
                j.event(T, "REJECTED_RISK", {"signal_id": "s1", "reason": "BELOW_EXCHANGE_MINIMUM", "detail": "required_notional=20.40"})
            j.close()
            (state / "alerts.jsonl").write_text(json.dumps({"key": "stale:feed", "level": "critical"}) + "\n")
            out = snapshot([("paper", state), ("missing", Path(d) / "none")], None)
            run = out["runs"][0]
            self.assertEqual(run["reject_reasons"], {"BELOW_EXCHANGE_MINIMUM": 1})
            self.assertEqual(run["feed"][0]["strategy"], "crash_flush_e2")
            self.assertEqual(run["feed"][0]["detail"], "required_notional=20.40")
            self.assertEqual(run["slots"]["crash"], {"used": 0, "max": 1})
            self.assertEqual(len(run["alerts"]), 1)
            self.assertIsNone(out["runs"][1]["heartbeat_age_s"])
            json.dumps(out, default=str)

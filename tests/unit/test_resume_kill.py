import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "resume_kill", Path(__file__).resolve().parents[2] / "scripts" / "resume_kill.py")
resume_kill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume_kill)


class ResumeKillTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.state = Path(self.dir.name) / "run"
        self.state.mkdir()
        self.db = self.state / "journal_pD.db"
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def tearDown(self): self.dir.cleanup()

    def paused(self, value=None):
        with sqlite3.connect(self.db) as db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO meta VALUES('paused', ?)", (json.dumps(value),))
            return json.loads(db.execute("SELECT value FROM meta WHERE key='paused'").fetchone()[0])

    def test_clears_kill_file_and_kill_pause_with_backup(self):
        (self.state / "KILL").write_text("two consecutive reconciliation failures\n")
        self.paused("KILL_SWITCH")
        resume_kill.resume(self.state, "pD")
        self.assertFalse((self.state / "KILL").exists())
        self.assertIs(self.paused(), False)
        self.assertEqual(len(list(self.state.parent.glob("KILL.run.*.bak"))), 1)

    def test_leaves_other_pauses_alone(self):
        self.paused("LOSS_BREAKER")
        resume_kill.resume(self.state, "pD")
        self.assertEqual(self.paused(), "LOSS_BREAKER")

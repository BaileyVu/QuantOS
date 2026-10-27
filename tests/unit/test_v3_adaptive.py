from pathlib import Path
import tempfile
import unittest

from quantos.paper.adaptive import ACTIVE, PROBATION, SHADOW, AdaptiveSizer, Edge, dd_multiplier
from quantos.paper.engine import PaperEngine
from quantos.paper.journal import Journal
from quantos.paper.risk import Policy
from quantos.paper.types import D

from tests.unit.test_v3_engine import INS, T, quote, signal


def closed(journal, oid, net, risk="2", strategy="seed", at=T):
    o = {"id": oid, "state": "CLOSED", "closed_ms": at, "quantity": "1",
         "signal": {"strategy_id": strategy}, "decision": {"achievable_risk": risk}}
    with journal.transaction():
        journal.save(o)
        journal.db.execute("INSERT INTO fills VALUES(?,?,?,?,?,?,?,?)", (oid + ":E", oid, at, "ENTRY", "1", "100", "0", "0"))
        journal.db.execute("INSERT INTO fills VALUES(?,?,?,?,?,?,?,?)", (oid + ":X", oid, at, "EXIT", "1", "100", "0", str(net)))


class AdaptiveTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.p1 = Journal(root / "journal_p1.db", "p1", "synthetic")
        self.pa = Journal(root / "journal_pA.db", "pA", "synthetic")
        self.sizer = AdaptiveSizer(self.pa, self.p1)

    def tearDown(self):
        self.p1.close(); self.pa.close(); self.dir.cleanup()

    def test_dd_multiplier(self):
        self.assertEqual(dd_multiplier(0.02), 1.0)
        self.assertEqual(dd_multiplier(0.30), 0.25)
        self.assertTrue(0.25 < dd_multiplier(0.12) < 1)

    def test_probe_until_learned_then_kelly(self):
        risk, d = self.sizer.risk_fraction("seed", D(200), D(200))
        self.assertEqual((risk, d["mode"]), (D("0.0025"), "LEARNING"))
        for i in range(40):  # +2R / -1R alternating: strong edge
            closed(self.p1, f"o{i}", "4" if i % 2 else "-2", at=T + i)
        with self.pa.transaction(): self.sizer.learn(T)
        risk, d = self.sizer.risk_fraction("seed", D(200), D(200))
        self.assertEqual(d["mode"], "KELLY")
        self.assertTrue(D("0.0025") < risk <= D("0.05"))
        with self.pa.transaction(): self.sizer.learn(T)  # idempotent
        self.assertEqual(self.sizer.edge("seed").n, 40)

    def test_losing_streak_demotes_to_shadow(self):
        e = Edge(prior_mu=0.2)
        states = []
        for _ in range(40):
            e.update(-1.06); states.append(e.state)
        self.assertIn(PROBATION, states)
        self.assertEqual(e.state, SHADOW)
        for _ in range(30):
            e.update(1.94); e.update(-1.06)
        self.assertIn(e.state, {PROBATION, ACTIVE})

    def test_engine_rejects_when_no_edge(self):
        for i in range(40):
            closed(self.p1, f"o{i}", "-2", at=T + i)
        engine = PaperEngine(self.pa, Policy(risk_fraction=D(".05")), adaptive=self.sizer)
        with self.pa.transaction(): self.sizer.learn(T)
        self.assertIsNone(engine.propose(signal(), INS, quote(), T))
        reasons = [r[0] for r in self.pa.db.execute("SELECT payload FROM events WHERE kind='REJECTED_RISK'")]
        self.assertTrue(any("EDGE_SHADOW_ONLY" in r or "NO_EDGE" in r for r in reasons), reasons)

    def test_referee(self):
        self.assertEqual(self.sizer.referee(T, errors=1)[0], "NEED_HUMAN")
        self.assertEqual(self.sizer.referee(T, errors=0)[0], "WAIT")
        for i in range(20):
            closed(self.p1, f"r{i}", "4" if i % 2 else "-2", at=T + 1 + i)
        self.assertEqual(self.sizer.referee(T, errors=0)[0], "AUTO_RESUME")


if __name__ == "__main__":
    unittest.main()

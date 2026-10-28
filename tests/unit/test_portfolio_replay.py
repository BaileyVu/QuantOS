from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest

from quantos.paper.cli import _strategy_listing, _strategy_from_config
from quantos.paper.replay import replay, snapshot
from quantos.paper.risk import Account, Policy, ResearchPolicy, evaluate
from quantos.paper.strategies import library
from quantos.paper.strategies.alt_dip_reversal import AltDipReversalStrategy
from quantos.paper.types import D, dumps
from tests.unit.test_portfolio_alt_dip import dip_snapshot


class CandidateIntegrationTests(unittest.TestCase):
    def test_library_opt_in_and_frozen_configs(self):
        self.assertEqual(len(library()), 6)
        candidates = library(True)[6:]
        self.assertEqual(len(candidates), 3)
        for s in candidates:
            path = Path("configs/strategies") / f"{s.strategy_id}.toml"
            self.assertEqual(_strategy_from_config(path), s.strategy_id)
        with tempfile.TemporaryDirectory() as tmp:
            rows = _strategy_listing(Path(tmp)/"absent", True)
            self.assertEqual([r["pA"]["state"] for r in rows[-3:]], ["RESEARCH_ONLY"]*3)

    def test_extended_snapshot_roundtrip_and_candidate_risk(self):
        snap = dip_snapshot()
        snap = replace(snap, quote=replace(snap.quote, bid=D("89.99"), ask=D("90.01"),
                                          bid_qty=D(10000), ask_qty=D(10000)))
        decoded = snapshot(json.loads(dumps(asdict(snap))))
        self.assertEqual(decoded, snap)
        s = AltDipReversalStrategy(D(".01"), 24).evaluate(snap)
        account = Account(D(200), D(200), D(200))
        decision = evaluate(s, snap.instrument, snap.quote, account, ResearchPolicy(), snap.at_ms)
        self.assertTrue(decision.approved, decision.reason)
        self.assertEqual(decision.leverage, 1)  # unchanged twice-stop liquidation buffer
        self.assertFalse(evaluate(s, snap.instrument, snap.quote, account, Policy(), snap.at_ms).approved)

    def test_research_replay_has_separate_identity_and_reports(self):
        snap = dip_snapshot()
        q = replace(snap.quote, bid=D("89.99"), ask=D("90.01"), bid_qty=D(10000), ask_qty=D(10000))
        snap = replace(snap, quote=q)
        t = snap.at_ms
        rows = [
            {"schema": 2, "received_ms": t, "kind": "feed", "payload": [["quote", asdict(q)]]},
            {"schema": 2, "received_ms": t, "kind": "scan", "payload": [asdict(snap)]},
            {"schema": 2, "received_ms": t+1000, "kind": "feed", "payload": [["quote", asdict(
                replace(q, time_ms=t+1000, received_ms=t+1000))]]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "inputs.jsonl"
            source.write_text("\n".join(dumps(r) for r in rows)+"\n")
            result = replay([source], root/"out", "alt_dip_reversal", research=True)
            self.assertEqual(list(result["profiles"]), ["pR"])
            self.assertTrue((root/"out"/"journal_pR.db").is_file())
            self.assertFalse((root/"out"/"journal_p1.db").exists())
            self.assertTrue((root/"out"/"reports"/"latest.json").is_file())


if __name__ == "__main__": unittest.main()

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from quantos.paper.engine import PaperEngine
from quantos.paper.journal import Journal
from quantos.paper.risk import Policy
from quantos.paper.runtime import dispatch
from quantos.paper.shadow import ShadowBook, summary
from quantos.paper.types import D, Trade, Snapshot, Funding
from tests.unit.test_thin_book import INS, T, quote, signal


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.j = Journal(self.path / "journal_p1.db", "p1", "synthetic")
        self.addCleanup(self.j.close)
        self.e = PaperEngine(self.j, Policy())
        self.e.recover(T); self.e.connection(True, T)
        self.book = ShadowBook(self.path / "shadow_p1.db", self.e, T)
        self.addCleanup(lambda: self.book.close())
        self.e.shadow = self.book

    def proposal(self, s=None, reason="TAKEN"):
        s = s or signal()
        oid = self.e.propose(s, INS, quote(), T) if reason == "TAKEN" else None
        self.book.propose(s, INS, quote(), T, reason, oid)
        return oid

    def feed(self, now, price, trade=None):
        batch = [("quote", quote(price, book=now))]
        if trade: batch.append(("trade", trade))
        dispatch([self.e], "feed", batch, now)

    def test_taken_limit_parity_including_stop_depth_costs_and_fees(self):
        self.proposal()
        self.feed(T+1000, "100", Trade("AAVEUSDC", 1, T+1000, D("99.99"), D(1000), True))
        self.feed(T+2000, "97.9")
        self.feed(T+3000, "97.9")
        report = summary(self.path / "shadow_p1.db", self.j.path)
        self.assertEqual(report["counts"], {"CLOSED": 1})
        self.assertEqual(D(report["taken_comparisons"][0]["difference"]), 0)
        self.assertEqual(report["groups"][0]["n"], 1)
        self.assertIsNone(report["groups"][0]["ci90_bp"])

    def test_feed_batches_do_not_grow_or_rewrite_open_shadow_images(self):
        # 11-11: one OBSERVATION row per batch per shadow + a rewrite of every image on every batch
        # stalled the main loop. Open shadows stay small and persist at most once a minute on feed.
        self.proposal()
        self.feed(T+1000, "100", Trade("AAVEUSDC", 1, T+1000, D("99.99"), D(1000), True))
        e = next(iter(self.book.engines.values()))
        size = lambda: len(self.book.db.execute("SELECT image FROM proposals").fetchone()[0])
        updated = lambda: self.book.db.execute("SELECT updated_ms FROM proposals").fetchone()[0]
        first, stamp = size(), updated()
        for i in range(2, 200): self.feed(T+i*100, "100")
        self.assertEqual(e.journal.db.execute("SELECT COUNT(*) FROM events WHERE kind='OBSERVATION'").fetchone()[0], 198)
        self.assertEqual(updated(), stamp)  # < 60s of feed: no rewrite
        self.feed(T+62000, "100")
        self.assertGreater(updated(), stamp)
        self.assertLessEqual(size(), first + 4096)
        self.assertEqual(e.journal.db.execute("SELECT COUNT(*) FROM events WHERE kind='OBSERVATION'").fetchone()[0], 0)
        self.feed(T+63000, "97.9"); self.feed(T+64000, "97.9")  # exit on feed persists at once
        self.assertEqual(summary(self.path / "shadow_p1.db")["counts"], {"CLOSED": 1})

    def test_slot_blocked_virtual_trade_does_not_change_main_books(self):
        self.proposal()
        other = signal(signal_id="blocked")
        self.assertIsNone(self.e.propose(other, INS, quote(), T))
        before = self.j.db.serialize()
        self.book.propose(other, INS, quote(), T, "EXISTING_EXPOSURE")
        self.assertEqual(len(self.book.engines), 2)
        self.assertEqual(self.j.db.serialize(), before)
        self.assertEqual(self.book.symbols(), {"AAVEUSDC"})

    def test_unpriceable_retained_without_fake_pnl_or_duplicate(self):
        s = signal(stop_loss=D("102"))
        self.proposal(s, "INVALID_BRACKET")
        self.proposal(s, "INVALID_BRACKET")
        report = summary(self.path / "shadow_p1.db")
        self.assertEqual(report["counts"], {"UNPRICEABLE": 1})
        self.assertTrue(all(r["n"] == 0 for r in report["groups"]))

    def test_durable_restart_retains_filled_shadow_position(self):
        self.proposal()
        self.feed(T+1000, "100", Trade("AAVEUSDC", 1, T+1000, D("99.99"), D(1000), True))
        self.book.close()
        self.book = ShadowBook(self.path / "shadow_p1.db", self.e, T+1500)
        self.e.shadow = self.book
        self.feed(T+2000, "97.9"); self.feed(T+3000, "97.9")
        self.assertEqual(summary(self.path / "shadow_p1.db")["counts"], {"CLOSED": 1})

    def test_time_exit_and_late_funding_use_engine_accounting(self):
        self.proposal(signal(maximum_hold_seconds=2))
        self.feed(T+1000, "100", Trade("AAVEUSDC", 1, T+1000, D("99.99"), D(1000), True))
        dispatch([self.e], "tick", {"finish": False}, T+3001)
        self.feed(T+4000, "101")
        funding = [Funding("AAVEUSDC", T+2000, D(".001"), D(100))]
        dispatch([self.e], "funding", funding, T+5000)
        dispatch([self.e], "funding", funding, T+6000)
        report = summary(self.path / "shadow_p1.db", self.j.path)
        self.assertEqual(report["counts"], {"CLOSED": 1})
        self.assertEqual(D(report["taken_comparisons"][0]["difference"]), 0)

    def test_report_ci_and_block_reason_groups_exclude_unfilled(self):
        self.proposal()
        self.proposal(signal(signal_id="other"), "SLOT_FULL_TREND")
        self.feed(T+1000, "100", Trade("AAVEUSDC", 1, T+1000, D("99.99"), D(1000), True))
        self.feed(T+2000, "97.9"); self.feed(T+3000, "97.9")
        report = summary(self.path / "shadow_p1.db")
        all_group = next(g for g in report["groups"] if g["reason"] == "ALL")
        self.assertEqual(all_group["n"], 2)
        self.assertEqual(len(all_group["ci90_bp"]), 2)
        self.assertEqual(all_group["hit_rate"], 0)

    def test_dispatch_records_all_post_arbitration_proposals_and_sentiment(self):
        class Strategy:
            strategy_id = "seed"
            def evaluate(self, snapshot): return signal()
        class Sentiment:
            def judge(self, proposal, now): return {"verdict": "WOULD_BLOCK"}
        class Gated(Policy): sentiment_gate = ("seed",)
        self.e.policy = Gated()
        snap = Snapshot(T, INS, quote(), (), ())
        dispatch([self.e], "scan", [snap], T, strategies=[Strategy()], sentiment=Sentiment())
        self.assertEqual(self.j.orders(), [])
        self.assertEqual(self.book.db.execute("SELECT reason FROM proposals").fetchone()[0], "SENTIMENT_BLOCK")
        self.assertEqual(len(self.book.engines), 1)

    def test_shadow_failure_is_visible_and_does_not_stop_real_feed(self):
        self.book.advance = lambda *a: (_ for _ in ()).throw(OSError("disk full"))
        self.feed(T+1000, "100")
        self.assertIsNone(self.e.shadow)
        self.assertEqual(self.e.quotes["AAVEUSDC"].time_ms, T+1000)
        self.assertEqual(self.j.db.execute("SELECT COUNT(*) FROM events WHERE kind='SHADOW_ERROR'").fetchone()[0], 1)
        # The callback already closed it; sqlite close is harmless a second time.

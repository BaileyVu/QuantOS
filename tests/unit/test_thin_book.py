"""A quiet thin book must not break the feed or stop exits, and must never allow new exposure."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from quantos.paper.engine import PaperEngine
from quantos.paper.journal import Journal
from quantos.paper.risk import Account, Policy, evaluate, usable_quote, valid_quote
from quantos.paper.runtime import dispatch
from quantos.paper.types import D, Instrument, Quote, Signal, Trade

T = 1800000000000
INS = Instrument("AAVEUSDC", "USDC", "TRADING", D(".01"), D(".001"),
                 D(".001"), D("100"), D("5"), market_step=D(".001"))


def quote(price="100", book=T, seen=None, mark_at=None, mark=None, symbol="AAVEUSDC"):
    """Book last changed at `book`; received and marked at `seen` unless overridden."""
    p, seen = D(price), book if seen is None else seen
    return Quote(symbol, book, seen, p, p + D(".01"), D("100"), D("100"), D(mark) if mark else p,
                 seen if mark_at is None else mark_at)


def signal(**changes):
    base = Signal("seed", "1", "seed-1", "AAVEUSDC", "LONG", T, T + 90000,
                  "LIMIT", D("100"), D("98"), D("106"), 3600, "synthetic")
    return replace(base, **changes)


class QuoteRuleTests(unittest.TestCase):
    def test_quiet_book_is_usable_but_not_valid_for_entries(self):
        q = quote(book=T, seen=T + 20_000)
        self.assertTrue(usable_quote(q, T + 20_000))
        self.assertFalse(valid_quote(q, T + 20_000))

    def test_fresh_book_passes_both(self):
        q = quote(book=T, seen=T + 4_000)
        self.assertTrue(usable_quote(q, T + 4_000))
        self.assertTrue(valid_quote(q, T + 4_000))

    def test_still_fail_closed(self):
        now = T + 20_000
        for name, q in {"book over 60s": quote(book=now - 61_000, seen=now),
                        "stale receipt": quote(book=T, seen=now - 6_000, mark_at=now),
                        "stale mark": quote(book=T, seen=now, mark_at=now - 6_000),
                        "mark off 3%": quote(book=T, seen=now, mark="103"),
                        "future book": quote(book=now + 1, seen=now)}.items():
            with self.subTest(name):
                self.assertFalse(usable_quote(q, now))
                self.assertFalse(valid_quote(q, now))

    def test_risk_rejects_entry_on_quiet_book(self):
        a = Account(D(200), D(200), D(200))
        decision = evaluate(signal(), INS, quote(book=T - 20_000, seen=T), a, Policy(), T)
        self.assertEqual((decision.approved, decision.reason), (False, "INVALID_DATA"))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.j = Journal(Path(self.tmp.name) / "journal_p1.db", "p1", "synthetic")
        self.addCleanup(self.j.close)
        self.e = PaperEngine(self.j, Policy())
        self.assertTrue(self.e.recover(T))
        self.e.connection(True, T)

    def held(self):
        self.assertIsNotNone(self.e.propose(signal(), INS, quote(), T))
        self.e.trade(Trade("AAVEUSDC", 1, T + 1000, D("99.99"), D("1000"), True), T + 1000)
        self.assertGreater(D(self.j.orders()[0]["filled"]), 0)

    def test_stop_and_exit_work_while_book_is_quiet(self):
        self.held()
        book = T + 2000           # top of book never changes again
        self.e.quote(quote("97.9", book=book, seen=T + 20_000), T + 20_000)
        o = self.j.orders()[0]
        self.assertEqual((o["state"], o["close_reason"]), ("CLOSING", "SL"))
        self.assertTrue(self.e.connected)
        self.e.quote(quote("97.9", book=book, seen=T + 25_000), T + 25_000)
        self.assertEqual(self.j.orders()[0]["state"], "CLOSED")

    def test_pending_market_entry_waits_for_a_moving_book(self):
        s = signal(entry_type="MARKET")
        self.assertIsNotNone(self.e.propose(s, INS, quote(), T))
        self.e.quote(quote(book=T + 1000, seen=T + 8_000), T + 8_000)   # book 7s old
        o = self.j.orders()[0]
        self.assertEqual(o["filled"], "0")
        self.assertTrue(self.e.connected)
        self.e.quote(quote(book=T + 9_000, seen=T + 9_500), T + 9_500)  # book moved
        self.assertGreater(D(self.j.orders()[0]["filled"]), 0)


class ExitImpactTests(EngineTests):
    def test_thin_ask_cannot_simulate_a_wild_exit(self):
        self.assertIsNotNone(self.e.propose(signal(side="SHORT", stop_loss=D("102"), take_profit=D("94"),
                                                   entry_type="MARKET"), INS, quote(), T))
        self.e.quote(quote(book=T + 1000, seen=T + 1000), T + 1000)
        o = self.j.orders()[0]
        self.assertGreater(D(o["filled"]), 0)
        self.e.kill(T + 2000)
        thin = replace(quote(book=T + 9000, seen=T + 9000), ask_qty=D(".001"))
        self.e.quote(thin, T + 9000)
        row = self.j.db.execute("SELECT price FROM fills WHERE kind='EXIT'").fetchone()
        self.assertLessEqual(D(row[0]), thin.ask * D("1.01"))


class DispatchTests(EngineTests):
    def batch(self, *quotes): return [("quote", q) for q in quotes]

    def test_one_quiet_book_does_not_fail_the_batch(self):
        now = T + 20_000
        btc = quote(book=now, seen=now, symbol="BTCUSDC")
        aave = quote(book=T, seen=now)
        self.assertTrue(dispatch([self.e], "feed", self.batch(btc, aave), now))
        self.assertTrue(self.e.connected)
        self.assertEqual(self.e.quotes["BTCUSDC"], btc)
        self.assertEqual(self.e.quotes["AAVEUSDC"], aave)

    def test_stale_mark_still_fails_the_batch(self):
        now = T + 20_000
        bad = quote(book=T, seen=now, mark_at=now - 6_000)
        self.assertFalse(dispatch([self.e], "feed", self.batch(quote(book=now, seen=now, symbol="BTCUSDC"), bad), now))
        self.assertFalse(self.e.connected)


if __name__ == "__main__":
    unittest.main()


class RealFillBookingTests(EngineTests):
    def test_books_take_exchange_entry_and_exit_and_cash_follows(self):
        self.held()
        self.e.quote(quote("97.9", book=T + 2000, seen=T + 20_000), T + 20_000)
        self.e.quote(quote("97.9", book=T + 2000, seen=T + 25_000), T + 25_000)
        o = self.j.orders()[0]
        qty = self.j.db.execute("SELECT quantity FROM fills WHERE kind='EXIT'").fetchone()[0]
        r = self.e.book_real_fills(o["id"], T + 30_000, entry=(D("100.05"), D("0.02")))
        self.assertTrue(r["changed"] and r["has_exit"])
        r = self.e.book_real_fills(o["id"], T + 31_000, exit=(D("97.80"), D("0.03")))
        expected_pnl = D(qty) * (D("97.80") - D("100.05"))
        self.assertEqual(D(r["new_pnl"]), expected_pnl)
        self.assertEqual(self.j.cash(), D(200) + expected_pnl - D("0.05"))
        self.assertEqual(self.j.orders()[0]["average"], "100.05")
        closed = [e for e in self.j.db.execute("SELECT payload FROM events WHERE kind='CLOSED'")]
        self.assertIn('"97.80"', closed[0][0])
        self.assertFalse(self.e.book_real_fills(o["id"], T + 32_000, exit=(D("97.80"), D("0.03")))["changed"])
        kinds = [k for (k,) in self.j.db.execute("SELECT kind FROM events WHERE kind='BOOKS_CORRECTION'")]
        self.assertEqual(len(kinds), 2)

    def test_open_position_entry_correction_and_unknown_order(self):
        self.held()
        o = self.j.orders()[0]
        r = self.e.book_real_fills(o["id"], T + 2000, entry=(D("100.10"), None))
        self.assertFalse(r["has_exit"])
        self.assertEqual(D(self.j.orders()[0]["average"]), D("100.10"))
        self.assertIsNone(self.e.book_real_fills("nope", T + 2000, entry=(D(1), None)))

    def test_booking_inside_the_runtime_transaction(self):
        """The runtime calls mirror.sync inside journal.transaction(); booking must nest, not fail."""
        self.held()
        oid = self.j.orders()[0]["id"]
        with self.j.transaction():
            self.assertTrue(self.e.book_real_fills(oid, T + 2000, entry=(D("100.20"), None))["changed"])
            with self.assertRaises(RuntimeError):
                with self.j.transaction():
                    self.j.set("probe", 1)
                    raise RuntimeError("inner failure")
        self.assertEqual(D(self.j.orders()[0]["average"]), D("100.20"))
        self.assertIsNone(self.j.get("probe"))


class ManualPauseTests(EngineTests):
    def test_pause_blocks_new_entries_only(self):
        self.assertTrue(self.e.entry_allowed())
        self.e.manual_pause = True
        self.assertFalse(self.e.entry_allowed())
        self.e.manual_pause = False
        self.assertTrue(self.e.entry_allowed())

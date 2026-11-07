"""HyperGrok vectors (MIT) adapted to Binance steps plus actual-fill risk regressions.

Upstream commit: 01c2a46c8a36656a2f958d9063201a77556cae5d
scripts/test_stop_cost.py; copyright notice: licenses/HyperGrok-MIT.txt.
"""
from pathlib import Path
import tempfile
import unittest

from quantos.paper.stop_cost import price_stop
from quantos.paper.types import D
from quantos.paper.engine import PaperEngine
from quantos.paper.journal import Journal
from quantos.paper.risk import Policy
from quantos.live.bridge import ExchangeMirror
from tests.unit.test_v3_engine import INS, T, quote, signal
from quantos.paper.types import Trade
from tests.unit.test_live_bridge import FillsExchange


class StopCostTests(unittest.TestCase):
    def test_hypergrok_worked_example(self):
        c = price_stop(D(3000), D(2900), "LONG", D(3), D(".00045"), D(".00045"))
        self.assertEqual(c.stop_fill, D(2897))
        self.assertEqual(c.fees_per_unit, D("2.65365"))
        self.assertEqual(c.loss_per_unit, D("105.65365"))
        self.assertEqual(c.quantity(D(51), D(".0001")), D(".4827"))
        self.assertEqual((c.loss_per_unit * D(".4827")).quantize(D(".01")), D("51.00"))

    def test_short_and_binance_non_power_of_ten_step(self):
        c = price_stop(D(3000), D(3100), "SHORT", D(3), D(".0004"), D(".0004"))
        self.assertEqual(c.stop_fill, D(3103))
        self.assertEqual(c.fees_per_unit, D("2.4412"))
        q = c.quantity(D(51), D(".005"))
        self.assertEqual(q, D(".480"))
        self.assertLessEqual(q * c.loss_per_unit, D(51))
        self.assertGreater((q + D(".005")) * c.loss_per_unit, D(51))

    def test_upstream_rounding_vectors_never_exceed_budget(self):
        for e, s in (("3000", "2900"), ("3000", "3100"), (".009", ".0085"), ("117234.5", "115000")):
            with self.subTest(entry=e, stop=s):
                e, s = D(e), D(s)
                c = price_stop(e, s, "LONG" if s < e else "SHORT", e * D(".001"), D(".00045"), D(".00045"))
                q = c.quantity(D(51), D(".01"))
                self.assertEqual(q % D(".01"), 0)
                self.assertLessEqual(q * c.loss_per_unit, D(51))

    def test_invalid_or_unpriceable_inputs_fail_closed(self):
        for price, stop, slip in (("NaN", "90", "1"), ("100", "1", "2"), ("100", "90", "-1")):
            with self.subTest(price=price, stop=stop, slip=slip), self.assertRaises(ValueError):
                price_stop(D(price), D(stop), "LONG", D(slip), D(".0006"), D(".0006"))


class RealisedRiskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.j = Journal(Path(self.tmp.name) / "j.db", "p1", "synthetic")
        self.addCleanup(self.j.close)
        self.e = PaperEngine(self.j, Policy())
        self.e.recover(T)
        self.e.connection(True, T)
        self.oid = self.e.propose(signal(), INS, quote(), T)
        self.e.trade(Trade(INS.symbol, 1, T + 1000, D("99.99"), D(1000), True), T + 1000)
        self.qty = D(self.e.query_order(self.oid)["filled"])

    def test_actual_price_breach_closes_exchange_same_sync(self):
        ex = FillsExchange()
        ex.price, ex.t0 = D(110), T + 2000
        original = ex.new_order
        def fill(p):
            o = original(p)
            o["avgPrice"] = str(ex.price)
            return o
        ex.new_order = fill
        m = ExchangeMirror(ex, "test", Path(self.tmp.name), lambda *a: None)
        with self.j.transaction():
            m.sync(self.e, T + 2000)
        self.assertEqual(ex.pos[INS.symbol], 0)
        self.assertEqual(ex.algo, {})
        self.assertEqual(m.book[self.oid]["state"], "CLOSED")
        self.assertEqual(self.e.query_order(self.oid)["close_reason"], "REALISED_RISK_EXCEEDED")

    def test_actual_commission_breach_preserves_corrected_books_and_closing(self):
        # A large real commission alone breaches risk; the state must survive the
        # book_real_fills save, and duplicate booking must not emit another close.
        before = self.j.cash()
        self.e.book_real_fills(self.oid, T + 2000, entry=(D(100), D(1)))
        o = self.e.query_order(self.oid)
        self.assertEqual(o["state"], "CLOSING")
        self.assertEqual(D(o["average"]), D(100))
        self.assertLess(self.j.cash(), before)
        self.e.book_real_fills(self.oid, T + 2001, entry=(D(100), D(1)))
        self.assertEqual(self.j.db.execute("SELECT count(*) FROM events WHERE kind='REALISED_RISK_EXCEEDED'").fetchone()[0], 1)

    def test_threshold_is_strictly_greater_and_exposure_reservation_updates(self):
        c = price_stop(D(100), D(98), "LONG", D(".3"), D(".0006"), D(".0006"))
        with self.j.transaction():
            o = self.e.query_order(self.oid)
            o["decision"]["requested_risk"] = str(self.qty * c.loss_per_unit / D("1.2"))
            self.j.save(o)
        self.assertTrue(self.e.check_realised_risk(self.oid, T + 1000, D(100), self.qty))
        self.assertNotEqual(self.e.query_order(self.oid)["state"], "CLOSING")
        self.assertFalse(self.e.check_realised_risk(self.oid, T + 1001, D("100.0001"), self.qty))

    def test_missing_average_holds_then_kills_without_duplicate_entry(self):
        ex = FillsExchange()
        m = ExchangeMirror(ex, "test", Path(self.tmp.name), lambda *a: None)
        m.sync(self.e, T + 2000)
        self.assertTrue(self.e.exchange_hold)
        self.assertEqual(len(ex.orders), 1)
        self.assertTrue(ex.algo)
        m.sync(self.e, T + 92_000)
        self.assertTrue(m.kill.active)
        self.assertEqual(ex.pos[INS.symbol], 0)

    def test_durable_risk_close_survives_journal_rollback(self):
        ex = FillsExchange()
        ex.price, ex.t0 = D(100), T + 2000
        original = ex.new_order
        def fill(p):
            o = original(p)
            o["avgPrice"] = str(ex.price)
            return o
        ex.new_order = fill
        m = ExchangeMirror(ex, "test", Path(self.tmp.name), lambda *a: None)
        m.sync(self.e, T + 2000)
        # Model a crash after mirror risk_close persisted but before the journal
        # transaction committed its CLOSING state. A restart must still close.
        m.book[self.oid]["risk_close"] = True
        m._save()
        fresh = ExchangeMirror(ex, "test", Path(self.tmp.name), lambda *a: None)
        fresh.sync(self.e, T + 4000)
        self.assertEqual(ex.pos[INS.symbol], 0)
        self.assertEqual(self.e.query_order(self.oid)["close_reason"], "REALISED_RISK_EXCEEDED")

    def test_partial_commissions_do_not_complete_booking(self):
        ex = FillsExchange()
        ex.t0 = T + 2000
        m = ExchangeMirror(ex, "test", Path(self.tmp.name), lambda *a: None)
        ex.hide = True
        m.sync(self.e, T + 2000)
        ex.hide = False
        ex.trades[0]["qty"] = str(self.qty / 2)
        m.sync(self.e, T + 8000)
        self.assertEqual(m.book[self.oid]["booked"], "")
        self.assertTrue(self.e.exchange_hold)

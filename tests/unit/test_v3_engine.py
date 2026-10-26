from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quantos.paper.engine import Faults, PaperEngine
from quantos.paper.journal import Journal
from quantos.paper.risk import Account, Policy, evaluate
from quantos.paper.types import D, Funding, Instrument, Quote, Signal, Trade

T = 1800000000000
INS = Instrument("BTCUSDC", "USDC", "TRADING", D(".01"), D(".001"),
                 D(".001"), D("100"), D("5"), market_step=D(".001"))


def quote(price="100", time=T):
    p = D(price)
    return Quote("BTCUSDC", time, time, p, p + D(".01"), D("100"), D("100"), p)


def signal(**changes):
    base = Signal("seed", "1", "seed-1", "BTCUSDC", "LONG", T, T + 90000,
                  "LIMIT", D("100"), D("98"), D("106"), 3600, "synthetic")
    return replace(base, **changes)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.j = Journal(Path(self.tmp.name) / "journal_p1.db", "p1", "synthetic")
        self.addCleanup(self.j.close)
        self.e = PaperEngine(self.j, Policy())
        self.assertTrue(self.e.recover(T))
        self.e.connection(True, T)

    def submit(self, s=None):
        return self.e.propose(s or signal(), INS, quote(), T)

    def fill(self, quantity="1000", time=T + 1000, tid=1):
        self.e.trade(Trade("BTCUSDC", tid, time, D("99.99"), D(quantity), True), time)

    def close_at(self, p, time=T + 2000):
        self.e.quote(quote(p, time), time)
        self.e.quote(quote(p, time + 1000), time + 1000)

    def test_limit_fill_target_reconciles_fees_and_no_reverse(self):
        self.submit(); self.fill(); self.close_at("106")
        o = self.j.orders()[0]
        self.assertEqual((o["state"], o["close_reason"], o["filled"]), ("CLOSED", "TP", "0"))
        self.assertFalse(o["stop_active"] or o["target_active"])
        cash = self.j.cash()
        self.assertGreater(cash, D(200))
        self.fill(time=T + 5000, tid=2)
        self.assertEqual(cash, self.j.cash())
        self.assertTrue(self.e.recover(T + 6000))

    def test_risk_rejection_creates_no_order(self):
        self.assertIsNone(self.submit(signal(stop_loss=D("102"))))
        self.assertEqual(self.j.orders(), [])

    def test_duplicate_and_timeout_after_accept_do_not_duplicate(self):
        self.e.faults.timeout_after_accept = True
        with patch.object(self.e, "query_order", wraps=self.e.query_order) as query:
            identity = self.submit()
            query.assert_called_once_with(identity)
        self.assertIsNotNone(identity)
        self.assertIsNone(self.submit())
        self.assertEqual(len(self.j.orders()), 1)

    def test_unfilled_expiry(self):
        self.submit(); self.e.quote(quote(time=T + 90000), T + 90000)
        self.assertEqual(self.j.orders()[0]["state"], "EXPIRED")

    def test_partial_then_expiry_keeps_protection(self):
        self.submit()
        qty = D(self.j.orders()[0]["quantity"])
        self.fill(str(qty * 150))
        before = self.j.orders()[0]
        self.assertGreater(D(before["filled"]), 0)
        self.assertLess(D(before["filled"]), qty)
        self.e.quote(quote(time=T + 90000), T + 90000)
        after = self.j.orders()[0]
        self.assertEqual(after["remaining"], "0")
        self.assertTrue(after["stop_active"])
        self.assertEqual(before["filled"], after["filled"])

    def test_stop_and_cleanup(self):
        self.submit(); self.fill(); self.close_at("97")
        self.assertEqual(self.j.orders()[0]["close_reason"], "SL")
        self.assertLess(self.j.cash(), D(200))
        self.assertFalse(self.j.orders()[0]["target_active"])

    def test_funding_loss_breaker_prevents_remaining_partial_fill(self):
        self.submit()
        qty = D(self.j.orders()[0]["quantity"])
        self.fill(str(qty*150))
        filled = self.j.orders()[0]["filled"]
        self.e.funding(Funding("BTCUSDC", T+1500, D("1"), D(100)), T+2000)
        self.assertEqual(self.j.get("paused"), "LOSS_BREAKER")
        self.fill("1000", T+3000, 2)
        self.assertEqual(self.j.orders()[0]["filled"], filled)
        self.assertEqual(self.j.orders()[0]["state"], "CLOSING")

    def test_hold_deadline_exit(self):
        self.submit(signal(maximum_hold_seconds=1)); self.fill(); self.close_at("100", T + 3000)
        self.assertEqual(self.j.orders()[0]["close_reason"], "TIME_EXIT")

    def test_cancel_fill_race_queries_authoritative_state(self):
        identity = self.submit()
        self.e.cancel(identity, T + 1000, Trade("BTCUSDC", 1, T+1000, D("99.99"), D(1000), True))
        o = self.j.orders()[0]
        self.assertEqual(o["state"], "PROTECTED")
        self.assertGreater(D(o["filled"]), 0)

    def test_durable_crash_fill_recovered_with_protection(self):
        self.submit(); self.e.faults.crash_after_fill = True
        with self.assertRaisesRegex(RuntimeError, "SYNTHETIC_CRASH"):
            self.fill()
        self.assertFalse(self.j.orders()[0]["stop_active"])
        self.e = PaperEngine(self.j, Policy())
        self.assertTrue(self.e.recover(T + 2000))
        self.assertTrue(self.j.orders()[0]["stop_active"])

    def test_disconnect_cancels_remaining_and_blocks_new_exposure(self):
        self.submit(); self.e.connection(False, T + 1000)
        self.fill(time=T + 2000)
        self.assertEqual(self.j.orders()[0]["filled"], "0")
        self.assertIsNone(self.submit(signal(signal_id="other")))

    def test_invalid_quote_and_filter_fail_closed(self):
        a = Account(D(200), D(200), D(200))
        for ins, q in [(replace(INS, tick=D(0)), quote()), (INS, quote(time=T-10000))]:
            self.assertFalse(evaluate(signal(), ins, q, a, Policy(), T).approved)

    def test_loss_breaker(self):
        with self.j.transaction():
            self.j.set("day", "2027-01-15")
            self.j.set("day_equity", "200")
            self.j.set("high_water", "300")
        self.assertIsNone(self.submit())

    def test_manual_position_blocks_recovery(self):
        self.e.faults.manual_position = True
        self.assertFalse(self.e.recover(T))
        self.assertIsNone(self.submit())

    def test_rejected_protection_closes_safely(self):
        for leg in ("reject_stop", "reject_target"):
            with self.subTest(leg=leg):
                self.setUp()
                setattr(self.e.faults, leg, True)
                self.submit(); self.fill()
                self.assertEqual(self.j.orders()[0]["state"], "CLOSING")
                self.assertFalse(self.j.orders()[0]["stop_active"])
                self.e.quote(quote(time=T + 2000), T + 2000)
                self.assertEqual(self.j.orders()[0]["state"], "CLOSED")

    def test_usdt_incompatible(self):
        self.assertFalse(evaluate(signal(), replace(INS, margin_asset="USDT"), quote(),
                                  Account(D(200), D(200), D(200)), Policy(), T).approved)

    def test_completed_trade_restart_cannot_reenter(self):
        self.submit(); self.fill(); self.close_at("106")
        self.e = PaperEngine(self.j, Policy()); self.e.recover(T + 4000)
        self.e.connection(True, T + 4000)
        self.assertIsNone(self.submit())

    def test_funding_actual_time_once_including_late_arrival(self):
        self.submit(); self.fill(); self.close_at("106")
        before = self.j.cash()
        f = Funding("BTCUSDC", T + 1500, D(".001"), D(100))
        self.e.funding(f, T + 5000)
        after = self.j.cash()
        self.assertLess(after, before)
        self.e.funding(f, T + 6000)
        self.assertEqual(after, self.j.cash())

    def test_touch_old_trade_wrong_side_not_fill(self):
        self.submit()
        for trade in [Trade("BTCUSDC", 1, T-1, D(99), D(1000), True),
                      Trade("BTCUSDC", 2, T+1000, D(100), D(1000), True),
                      Trade("BTCUSDC", 3, T+1001, D(99), D(1000), False)]:
            self.e.trade(trade, T+1001)
        self.assertEqual(self.j.orders()[0]["filled"], "0")

    def test_repeated_trade_does_not_add_fill(self):
        self.submit(); self.fill("150")
        before = self.j.cash(); self.fill("150")
        self.assertEqual(self.j.cash(), before)

    def test_recovery_mismatch_pauses(self):
        self.submit(); self.fill()
        with self.j.transaction():
            o = self.j.orders()[0]; o["filled"] = "9"; self.j.save(o)
        self.assertFalse(self.e.recover(T + 2000))

    def test_partial_restart_cancels_remainder_and_never_adds_exposure(self):
        self.submit()
        qty = D(self.j.orders()[0]["quantity"])
        self.fill(str(qty*150))
        filled = self.j.orders()[0]["filled"]
        self.e = PaperEngine(self.j, Policy())
        self.assertTrue(self.e.recover(T+2000))
        self.e.connection(True, T+2000)
        self.fill("1000", T+3000, 2)
        self.assertEqual(self.j.orders()[0]["remaining"], "0")
        self.assertEqual(self.j.orders()[0]["filled"], filled)
        self.assertTrue(self.j.orders()[0]["stop_active"])

    def test_pending_entry_cannot_fill_when_paused_or_unreconciled(self):
        self.submit()
        with self.j.transaction(): self.j.set("paused", "MISMATCH")
        self.fill()
        self.assertEqual(self.j.orders()[0]["filled"], "0")
        with self.j.transaction():
            self.j.set("paused", False)
            self.j.set("reconciled", False)
        self.fill("1000", T+2000, 2)
        self.assertEqual(self.j.orders()[0]["filled"], "0")

    def test_recovery_detects_accounting_average_mismatch(self):
        self.submit(); self.fill()
        with self.j.transaction():
            o = self.j.orders()[0]; o["average"] = "90"; self.j.save(o)
        self.assertFalse(self.e.recover(T+2000))
        self.assertIn("AVERAGE_PRICE_MISMATCH", self.j.get("paused"))

    def test_p5_gross_cap_and_independent_minimum(self):
        a = Account(D(200), D(200), D(200))
        p1 = evaluate(signal(), INS, quote(), a, Policy(), T)
        p5 = evaluate(signal(), INS, quote(), a, Policy(risk_fraction=D(".05")), T)
        self.assertTrue(p1.approved and p5.approved)
        self.assertEqual(p5.binding_constraint, "gross_notional")
        self.assertLessEqual(p5.notional, D(200))
        self.assertLess(p5.achievable_risk, p5.requested_risk)
        ins = replace(INS, min_notional=D(150))
        self.assertFalse(evaluate(signal(), ins, quote(), a, Policy(), T).approved)
        self.assertTrue(evaluate(signal(), ins, quote(), a, Policy(risk_fraction=D(".05")), T).approved)

    def test_hard_ceilings_cannot_increase(self):
        for kwargs in ({"risk_fraction": D(".051")}, {"leverage": D(4)}, {"gross_fraction": D("1.1")}):
            with self.assertRaises(ValueError): Policy(**kwargs)

    def test_stop_waits_for_next_executable_quote(self):
        self.submit(signal(entry_type="STOP", entry_price=D("100.10")))
        self.e.quote(quote("100.10", T + 1000), T + 1000)
        self.assertEqual(self.j.orders()[0]["filled"], "0")
        self.e.quote(quote("100.10", T + 2000), T + 2000)
        self.assertGreater(D(self.j.orders()[0]["filled"]), 0)

    def test_market_adverse_gap_unfilled(self):
        self.submit(signal(entry_type="MARKET"))
        self.e.quote(quote("102", T+1000), T+1000)
        self.assertEqual(self.j.orders()[0]["state"], "CANCELED")


if __name__ == "__main__":
    unittest.main()

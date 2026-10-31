from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from quantos.paper.engine import PaperEngine
from quantos.paper.journal import Journal
from quantos.paper.risk import Account, LivePolicy, Policy, evaluate
from quantos.paper.strategies.candidate_features import (
    HOUR_MS, TimedValue, oi_value_4h_change_zscore, oi_value_4h_change_zscore_tolerant)
from quantos.paper.strategies.usdc_mapped import PAPER_VERSION, UsdcMapped
from quantos.paper.types import D, Instrument, Quote, Snapshot
from tests.unit.test_v3_engine import INS, T, quote, signal

H0 = 1790000000000 // HOUR_MS * HOUR_MS


def series(n=760, bad=()):
    out = []
    for i in range(n):
        value = D(1000) + D(i % 7) * D(3) + D(i % 11)
        out.append(TimedValue(H0 + i * HOUR_MS, D(0) if i in bad else value))
    out[-1] = TimedValue(out[-1].time_ms, D(900))  # a sharp final OI drop
    return tuple(out)


def event(strategy="crash_flush_e2", **changes):
    return signal(strategy_id=strategy, strategy_version=PAPER_VERSION, entry_type="MARKET",
                  entry_price=D(100), stop_loss=D(75), take_profit=None, maximum_hold_seconds=86400, **changes)


def account(**changes):
    return replace(Account(D(50), D(50), D(50)), **changes)


class TolerantOiTests(unittest.TestCase):
    def test_equals_strict_rule_without_gaps(self):
        s = series()
        self.assertEqual(oi_value_4h_change_zscore_tolerant(s), oi_value_4h_change_zscore(s)[-1])

    def test_isolated_zero_day_no_longer_blinds_the_window(self):
        s = series(bad=range(300, 324))
        self.assertIsNone(oi_value_4h_change_zscore(s))
        score = oi_value_4h_change_zscore_tolerant(s)
        self.assertIsNotNone(score)
        self.assertLess(score.value, D(-2))

    def test_fails_closed_on_bad_latest_value_or_low_coverage(self):
        s = series()
        self.assertIsNone(oi_value_4h_change_zscore_tolerant(s[:-1] + (TimedValue(s[-1].time_ms, D(0)),)))
        self.assertIsNone(oi_value_4h_change_zscore_tolerant(series(bad=range(100, 200))))
        # The API serves ~30 days: 716 points still clear 90% coverage.
        self.assertIsNotNone(oi_value_4h_change_zscore_tolerant(series(n=716)))
        self.assertIsNone(oi_value_4h_change_zscore_tolerant(series(n=640)))


class LivePolicyTests(unittest.TestCase):
    def decide(self, s, a=None, ins=INS):
        return evaluate(s, ins, quote(), a or account(), LivePolicy(), T)

    def test_wide_stop_event_bumped_to_exchange_minimum_within_cap(self):
        d = self.decide(event())
        self.assertTrue(d.approved, d)
        self.assertEqual(d.binding_constraint, "EXCHANGE_MINIMUM_BUMP")
        self.assertGreaterEqual(d.notional, D(5))
        self.assertLessEqual(d.achievable_risk, D(50) * D(".03"))
        self.assertLessEqual(d.leverage, 3)

    def test_skip_with_reason_when_minimum_needs_too_much_risk(self):
        d = self.decide(event(), ins=replace(INS, min_notional=D(20)))
        self.assertEqual(d.reason, "BELOW_EXCHANGE_MINIMUM")
        self.assertIn("required_notional=20.40", d.detail)
        self.assertIn("required_risk=0.10", d.detail)

    def test_reserved_slots(self):
        trend = account(active_orders=1, gross_notional=D(10), margin_used=D(5), long_risk=D(".5"),
                        open_symbols=("ETHUSDC",), open_strategies=("tsmom_4h",))
        self.assertTrue(self.decide(event(), trend).approved)
        tsmom = signal(strategy_id="tsmom_4h", strategy_version="1.0.0", entry_type="MARKET",
                       take_profit=None, maximum_hold_seconds=259200, stop_loss=D("98.32"))
        self.assertEqual(self.decide(tsmom, trend).reason, "SLOT_FULL_TREND")
        events = account(active_orders=2, gross_notional=D(10), margin_used=D(5), long_risk=D(1),
                         open_symbols=("ETHUSDC", "SOLUSDC"), open_strategies=("crash_flush_e2", "alt_dip_reversal"))
        self.assertEqual(self.decide(event(), events).reason, "SLOT_FULL_CRASH")
        self.assertEqual(self.decide(event("alt_dip_reversal"), events).reason, "SLOT_FULL_EVENT")
        self.assertTrue(self.decide(tsmom, events).approved)
        alt = account(active_orders=1, gross_notional=D(10), margin_used=D(5), long_risk=D(".5"),
                      open_symbols=("FILUSDC",), open_strategies=("alt_dip_reversal",))
        self.assertTrue(self.decide(event(), alt).approved)  # an alt_dip trade cannot starve crash_flush

    def test_disabled_reference_strategies_and_breakers(self):
        self.assertEqual(self.decide(signal(strategy_id="ema_atr_ref", strategy_version="1.0.0")).reason,
                         "STRATEGY_DISABLED")
        self.assertEqual(self.decide(event(), account(equity=D(46))).reason, "DAILY_LOSS_BREAKER")
        self.assertEqual(self.decide(event(), account(equity=D(39), day_start_equity=D(39), high_water=D(50))).reason,
                         "DRAWDOWN_BREAKER")
        with self.assertRaises(ValueError):
            LivePolicy(leverage=D(4))

    def test_limit_entries_refused(self):
        lp_cls = type("AllLimit", (LivePolicy,), {"enabled_strategies": None})
        d = evaluate(signal(), INS, quote(), account(), lp_cls(), T)
        self.assertEqual(d.reason, "LIMIT_ENTRY_UNSUPPORTED")

    def test_default_policy_unchanged_below_minimum(self):
        self.assertEqual(evaluate(event(), INS, quote(), account(), Policy(), T).reason, "BELOW_EXCHANGE_MINIMUM")


class Inner:
    strategy_id, version = "crash_flush_e2", "research-1.0.0"

    def __init__(self): self.seen = None

    def evaluate(self, snapshot):
        self.seen = snapshot
        return replace(event(), strategy_version=self.version, symbol=snapshot.instrument.symbol,
                       entry_price=D("99.50"), stop_loss=D("74.62"),
                       features=(("catastrophe_stop_fraction", D(".25")),))


class MappingTests(unittest.TestCase):
    def test_maps_usdt_rule_onto_usdc_quote_and_tick(self):
        ins = Instrument("SOLUSDC", "USDC", "TRADING", D(".01"), D(".01"), D(".01"), D(1000), D(5))
        q = Quote("SOLUSDC", T, T, D("100.00"), D("100.03"), D(10), D(10), D("100.01"))
        inner = Inner()
        s = UsdcMapped(inner, frozenset({"SOLUSDT"})).evaluate(Snapshot(T, ins, q, (), ()))
        self.assertEqual(inner.seen.instrument.symbol, "SOLUSDT")
        self.assertEqual(inner.seen.instrument.margin_asset, "USDT")
        self.assertEqual((s.symbol, s.strategy_version, s.entry_price, s.stop_loss),
                         ("SOLUSDC", PAPER_VERSION, D("100.03"), D("75.02")))
        self.assertTrue(s.signal_id.endswith(":SOLUSDC"))
        other = replace(ins, symbol="XYZUSDC")
        self.assertIsNone(UsdcMapped(Inner(), frozenset({"SOLUSDT"})).evaluate(
            Snapshot(T, other, replace(q, symbol="XYZUSDC"), (), ())))


class CapitalTests(unittest.TestCase):
    def test_fifty_dollar_journal(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d) / "journal_pL.db", "pL", initial_balance="50")
            try:
                e = PaperEngine(j, LivePolicy())
                with j.transaction():
                    a = e.account(T)
                self.assertEqual((a.equity, a.high_water), (D(50), D(50)))
            finally:
                j.close()


class R5Tests(unittest.TestCase):
    def test_five_percent_profile_takes_twenty_dollar_minimum_it_used_to_skip(self):
        from quantos.paper.risk import LiveR5Policy
        ins = replace(INS, min_notional=D(20))
        self.assertEqual(evaluate(event(), ins, quote(), account(), LivePolicy(), T).reason, "BELOW_EXCHANGE_MINIMUM")
        # 5% of $50 = $2.50 risk at a 25% stop buys ~$9.7: still below $20 -> skipped with reason
        self.assertEqual(evaluate(event(), ins, quote(), account(), LiveR5Policy(), T).reason, "BELOW_EXCHANGE_MINIMUM")
        d = evaluate(event(), INS, quote(), account(), LiveR5Policy(), T)
        self.assertTrue(d.approved)
        self.assertEqual(d.binding_constraint, "risk")          # no bump needed at 5%
        self.assertLessEqual(d.achievable_risk, D("2.50"))
        self.assertGreater(d.notional, D(8))                    # sized at 90% of budget (headroom)
        self.assertEqual(d.requested_risk, D("2.50"))           # fill check may use the full budget

    def test_breakers_scaled(self):
        from quantos.paper.risk import LiveR5Policy
        self.assertEqual(evaluate(event(), INS, quote(), account(equity=D("42.5")), LiveR5Policy(), T).reason,
                         "DAILY_LOSS_BREAKER")
        self.assertTrue(evaluate(event(), INS, quote(), account(equity=D("43"), day_start_equity=D(43), high_water=D(50)),
                                 LiveR5Policy(), T).approved)
        with self.assertRaises(ValueError):
            LiveR5Policy(daily_loss=D(".20"))


class R5ScopeTests(unittest.TestCase):
    def test_five_percent_profile_is_crash_and_dip_only(self):
        from quantos.paper.risk import LiveR5Policy
        tsmom = signal(strategy_id="tsmom_4h", strategy_version="1.0.0", entry_type="MARKET",
                       take_profit=None, maximum_hold_seconds=259200, stop_loss=D("98.32"))
        self.assertEqual(evaluate(tsmom, INS, quote(), account(), LiveR5Policy(), T).reason, "STRATEGY_DISABLED")
        self.assertTrue(evaluate(tsmom, INS, quote(), account(), LivePolicy(), T).approved)
        self.assertTrue(evaluate(event(), INS, quote(), account(), LiveR5Policy(), T).approved)

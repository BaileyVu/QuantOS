"""pD (Demo200Policy) shared slot caps and risk-based sizing (2026-11-13)."""
from dataclasses import replace
from decimal import Decimal
import unittest

from quantos.paper.risk import Account, Demo200Policy, evaluate, policy_slot, portfolio_risk_caps
from quantos.paper.types import D
from tests.unit.test_v3_engine import INS, T, quote, signal
from tests.unit.test_v4_live_ready import event

P = Demo200Policy()


def trend(stop="98.32", strategy="tsmom_4h"):
    return signal(strategy_id=strategy, strategy_version="1.0.0", entry_type="MARKET",
                  take_profit=None, maximum_hold_seconds=259200, stop_loss=D(stop))


def acct(equity="200", strategies=(), long_risk="0", **kw):
    n = len(strategies)
    return replace(Account(D(equity), D(equity), D(equity)), active_orders=n, gross_notional=D(50) * n,
                   margin_used=D(5) * n, open_symbols=tuple(f"X{i}USDC" for i in range(n)),
                   open_strategies=tuple(strategies), long_risk=D(long_risk), cash=D(equity), **kw)


def liq_ok(d, s, policy=P):
    """Isolated liquidation distance (1/L minus maintenance buffer) is at least twice the stop (+slippage)."""
    stop_pct = abs(s.entry_price - s.stop_loss) / s.entry_price + policy.slippage
    return 1 / d.leverage - policy.maintenance_buffer >= 2 * stop_pct


class SlotTests(unittest.TestCase):
    def test_family_mapping(self):
        self.assertEqual([policy_slot(P, s) for s in ("tsmom_4h", "turtle_55", "vol_squeeze_breakout", "ml1h_pattern",
                                                     "crash_flush_e2", "delist_short_e17", "alt_dip_reversal", "ml5m_alts")],
                         ["trend"] * 4 + ["crash", "event", "alt", "alt"])
        self.assertEqual(dict(P.slots), {"trend": 3, "event": 2, "crash": 2, "alt": 3})
        self.assertEqual(P.max_positions, 6)

    def test_trend_holds_three(self):
        two = ("turtle_55", "ml1h_pattern")
        self.assertTrue(evaluate(trend(), INS, quote(), acct(strategies=two), P, T).approved)
        r = evaluate(trend(), INS, quote(), acct(strategies=two + ("vol_squeeze_breakout",)), P, T)
        self.assertEqual(r.reason, "SLOT_FULL_TREND")

    def test_full_trend_does_not_block_other_families(self):
        self.assertTrue(evaluate(event(), INS, quote(), acct(strategies=("tsmom_4h",) * 3), P, T).approved)

    def test_crash_two(self):
        self.assertTrue(evaluate(event(), INS, quote(), acct(strategies=("crash_flush_e2",)), P, T).approved)
        self.assertEqual(evaluate(event(), INS, quote(), acct(strategies=("crash_flush_e2",) * 2), P, T).reason,
                         "SLOT_FULL_CRASH")

    def test_alt_ml_three_shared(self):
        full = ("alt_dip_reversal", "ml5m_alts", "ml5m_alts")
        self.assertEqual(evaluate(event("alt_dip_reversal"), INS, quote(), acct(strategies=full), P, T).reason,
                         "SLOT_FULL_ALT")

    def test_event_two(self):
        self.assertEqual(evaluate(event("delist_short_e17"), INS, quote(),
                                  acct(strategies=("delist_short_e17",) * 2), P, T).reason, "SLOT_FULL_EVENT")

    def test_shared_cap_six_includes_pending(self):
        # 6 reserved orders (filled or pending) across families: nothing else opens, even in a free family.
        six = ("tsmom_4h", "tsmom_4h", "crash_flush_e2", "delist_short_e17", "alt_dip_reversal", "ml5m_alts")
        self.assertEqual(evaluate(event(), INS, quote(), acct(strategies=six), P, T).reason, "EXISTING_EXPOSURE")

    def test_one_position_per_symbol(self):
        a = replace(acct(strategies=("tsmom_4h",)), open_symbols=("BTCUSDC",))
        self.assertEqual(evaluate(event(), INS, quote(), a, P, T).reason, "EXISTING_SYMBOL_EXPOSURE")

    def test_shared_slots_must_cover_max_positions(self):
        with self.assertRaises(ValueError):
            type("Bad", (Demo200Policy,), {"slots": (("trend", 3), ("event", 2)), "__annotations__": {}})()


class SizingTests(unittest.TestCase):
    def test_wide_stop_risks_full_budget_at_1x(self):
        s = event()  # 25% stop
        d = evaluate(s, INS, quote(), acct(), P, T)
        self.assertTrue(d.approved, d)
        self.assertEqual(d.leverage, 1)
        self.assertEqual(d.requested_risk, D(3))                       # 1.5% of $200
        self.assertLessEqual(d.achievable_risk, D(3)); self.assertGreater(d.achievable_risk, D("2.95"))
        self.assertLessEqual(d.margin, D(200) / 15)
        self.assertTrue(liq_ok(d, s))

    def test_mid_stop_picks_smallest_leverage_within_margin_cap(self):
        s = trend("95.00")  # 5% stop: notional ~$55 -> margin $13.33 needs 5x
        d = evaluate(s, INS, quote(), acct(), P, T)
        self.assertTrue(d.approved, d)
        self.assertEqual(d.leverage, 5)
        self.assertLessEqual(d.margin, D(200) / 15)
        self.assertGreater(d.notional / (d.leverage - 1), D(200) / 15)  # one less would breach the margin cap
        self.assertGreater(d.achievable_risk, D("2.95")); self.assertLessEqual(d.achievable_risk, D(3))
        self.assertTrue(liq_ok(d, s))

    def test_tight_stop_caps_at_10x_and_shrinks(self):
        s = trend("98.32")  # 1.68% stop wants ~$140 notional, ~11x on $13.33 margin
        d = evaluate(s, INS, quote(), acct(), P, T)
        self.assertTrue(d.approved, d)
        self.assertEqual(d.leverage, 10)
        self.assertEqual(d.binding_constraint, "margin_cap")
        self.assertLessEqual(d.margin, D(200) / 15)
        self.assertLess(d.achievable_risk, D(3))                      # shrunk, never above the budget
        self.assertTrue(liq_ok(d, s))

    def test_liquidation_rule_limits_leverage_below_10(self):
        policy = Demo200Policy(margin_cap_fraction=D(".02"))  # $4 margin: 5% stop would want ~14x
        s = trend("95.00")
        d = evaluate(s, INS, quote(), acct(), policy, T)
        self.assertTrue(d.approved, d)
        self.assertEqual(d.leverage, 8)   # floor(1/(2*(0.05+0.0015)+0.02))
        self.assertTrue(liq_ok(d, s, policy))
        self.assertLessEqual(d.margin, D(4))
        self.assertFalse(liq_ok(replace(d, leverage=D(9)), s, policy))

    def test_ten_x_hard_ceiling(self):
        with self.assertRaises(ValueError): Demo200Policy(leverage=D("11"))

    def test_below_min_notional_is_skipped_not_bumped(self):
        big_min = replace(INS, min_notional=D("150"))
        d = evaluate(trend("98.32"), big_min, quote(), acct(), P, T)  # $133 position < $150 minimum
        self.assertFalse(d.approved)
        self.assertEqual(d.reason, "BELOW_EXCHANGE_MINIMUM")
        self.assertIn("required_notional", d.detail)

    def test_total_open_risk_capped_at_9pct(self):
        self.assertEqual(portfolio_risk_caps(P), (D(".09"), D(".09")))
        d = evaluate(trend("95.00"), INS, quote(), acct(strategies=("crash_flush_e2",), long_risk="17"), P, T)
        self.assertTrue(d.approved, d)
        self.assertLessEqual(d.achievable_risk, D(1))                 # only $1 of the $18 cap left
        r = evaluate(event(), INS, quote(), acct(strategies=("tsmom_4h",), long_risk="18"), P, T)
        self.assertEqual(r.reason, "PORTFOLIO_RISK_CAP")

    def test_risk_tracks_current_equity(self):
        d = evaluate(event(), INS, quote(), acct(equity="150"), P, T)
        self.assertEqual(d.requested_risk, D("2.25"))


if __name__ == "__main__":
    unittest.main()

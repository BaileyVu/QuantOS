from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quantos.paper.engine import PaperEngine
from quantos.paper.journal import Journal
from quantos.paper.risk import Account, Policy, ResearchPolicy, evaluate, portfolio_risk_caps
from quantos.paper.types import D
from tests.unit.test_v3_engine import INS, T, quote, signal


def inputs(symbol="BTCUSDT", **kwargs):
    ins = replace(INS, symbol=symbol, margin_asset="USDT")
    q = replace(quote(), symbol=symbol)
    s = signal(symbol=symbol, signal_id=symbol, **kwargs)
    return s, ins, q


class ResearchRiskTests(unittest.TestCase):
    def test_opt_in_does_not_relax_old_profiles(self):
        for kwargs in ({"risk_fraction": D(".08")}, {"drawdown": D(".50")}):
            with self.assertRaises(ValueError): Policy(**kwargs)
        self.assertEqual(evaluate(*inputs(), Account(D(200), D(200), D(200)), Policy(), T).reason,
                         "INCOMPATIBLE_MARGIN_ASSET")
        self.assertEqual(ResearchPolicy().taker_fee, D(".0005"))
        self.assertEqual(ResearchPolicy().max_positions, 3)
        with self.assertRaises(ValueError): ResearchPolicy(risk_fraction=D(".081"))

    def test_same_direction_positions_share_a_budget(self):
        a = Account(D(200), D(200), D(200), active_orders=2,
                    gross_notional=D(100), margin_used=D(40), long_risk=D(4),
                    open_symbols=("ETHUSDT", "SOLUSDT"))
        self.assertEqual(evaluate(*inputs(), a, ResearchPolicy(), T).reason, "PORTFOLIO_RISK_CAP")
        short = inputs(side="SHORT", entry_price=D(101), stop_loss=D(103), take_profit=D(95))
        self.assertTrue(evaluate(*short, a, ResearchPolicy(), T).approved)
        self.assertEqual(evaluate(*short, replace(a, active_orders=3), ResearchPolicy(), T).reason,
                         "EXISTING_EXPOSURE")

    def test_mixed_profile_caps_are_fixed_before_signal_order(self):
        low = ResearchPolicy(risk_fraction=D(".01"), portfolio_risk_fraction=D(".08"))
        high = replace(low, risk_fraction=D(".08"))
        self.assertEqual(portfolio_risk_caps(low), (D(".16"), D(".24")))
        self.assertEqual(portfolio_risk_caps(low), portfolio_risk_caps(high))
        a = Account(D(200), D(200), D(200), active_orders=1,
                    gross_notional=D(50), margin_used=D(50), long_risk=D(12),
                    open_symbols=("ETHUSDT",))
        decision = evaluate(*inputs(), a, low, T)
        self.assertTrue(decision.approved, decision.reason)
        self.assertLessEqual(decision.requested_risk, D(2))
        with self.assertRaises(ValueError):
            ResearchPolicy(risk_fraction=D(".08"), portfolio_risk_fraction=D(".01"))
        with self.assertRaises(ValueError):
            ResearchPolicy(portfolio_risk_fraction=D(".081"))

    def test_existing_symbol_and_incomplete_exposure_are_rejected(self):
        a = Account(D(200), D(200), D(200), active_orders=1)
        self.assertEqual(evaluate(*inputs(), a, ResearchPolicy(), T).reason, "INVALID_EXPOSURE")
        a = replace(a, gross_notional=D(20), margin_used=D(20), open_symbols=("BTCUSDT",))
        self.assertEqual(evaluate(*inputs(), a, ResearchPolicy(), T).reason, "EXISTING_SYMBOL_EXPOSURE")

    def test_quantity_and_leverage_fit_remaining_margin_with_costs(self):
        a = Account(D(200), D(200), D(200), active_orders=1,
                    gross_notional=D(190), margin_used=D(190), open_symbols=("ETHUSDT",))
        decision = evaluate(*inputs(), a, ResearchPolicy(risk_fraction=D(".08")), T)
        self.assertTrue(decision.approved, decision.reason)
        self.assertLessEqual(decision.margin + decision.quantity * D("100") * D(".004"), D(10))
        self.assertLessEqual(decision.notional + a.gross_notional, D(600))

    def test_drawdown_and_daily_breakers_remain_independent(self):
        args = inputs()
        a = Account(D(100), D(100), D(200))
        self.assertEqual(evaluate(*args, a, ResearchPolicy(), T).reason, "DRAWDOWN_BREAKER")
        a = Account(D(180), D(200), D(200))
        self.assertEqual(evaluate(*args, a, ResearchPolicy(), T).reason, "DAILY_LOSS_BREAKER")

    def test_engine_reserves_pending_orders_before_any_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = Journal(Path(tmp) / "research.db", "pR", "synthetic")
            try:
                e = PaperEngine(j, ResearchPolicy())
                e.recover(T); e.connection(True, T)
                for sym in ("BTCUSDT", "ETHUSDT"):
                    self.assertIsNotNone(e.propose(*inputs(sym), T))
                with j.transaction():
                    a = e.account(T)
                self.assertEqual(a.active_orders, 2)
                self.assertGreater(a.margin_used, 0)
                self.assertGreater(a.long_risk, 0)
                self.assertEqual(a.open_symbols, ("BTCUSDT", "ETHUSDT"))
                # Rounding leaves a tiny budget which cannot meet exchange minimums.
                self.assertIsNone(e.propose(*inputs("SOLUSDT"), T))
                self.assertEqual(len(e.active()), 2)
            finally:
                j.close()

    def test_research_state_cannot_reuse_baseline_or_change_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "research.db"
            j = Journal(path, "pR", "synthetic")
            PaperEngine(j, ResearchPolicy())
            with self.assertRaises(ValueError): PaperEngine(j, Policy())
            with self.assertRaises(ValueError):
                PaperEngine(j, ResearchPolicy(risk_fraction=D(".02")))
            j.close()
            with self.assertRaises(ValueError): Journal(path, "p1", "synthetic")

    def test_fill_fee_cannot_push_reserved_risk_over_portfolio_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = Journal(Path(tmp) / "risk-cap.db", "pR", "synthetic")
            try:
                e = PaperEngine(j, ResearchPolicy())
                e.recover(T); e.connection(True, T)
                self.assertIsNotNone(e.propose(*inputs(), T))
                order = e.active()[0]
                with j.transaction():
                    account = e.account(T)
                    # A fully reserved 2% direction bucket fits before the fee;
                    # execution must not spend any of its cost reserve as risk.
                    exact_boundary = replace(account, long_risk=account.equity*D(".02"))
                    with patch.object(e, "account", return_value=exact_boundary):
                        e._entry_fill(order, D(order["remaining"]), D(100), T+1000, "fee-boundary", False)
                self.assertEqual(j.db.execute("SELECT count(*) FROM fills").fetchone()[0], 0)
                reasons = [r[0] for r in j.db.execute(
                    "SELECT json_extract(payload,'$.reason') FROM events WHERE kind='CANCELED'")]
                self.assertIn("FILL_EXCEEDS_PORTFOLIO_RISK_CAP", reasons)
            finally: j.close()

    def test_other_pending_orders_are_counted_again_at_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            j = Journal(Path(tmp) / "research.db", "pR", "synthetic")
            try:
                e = PaperEngine(j, ResearchPolicy())
                e.recover(T); e.connection(True, T)
                for sym in ("BTCUSDT", "ETHUSDT"):
                    self.assertIsNotNone(e.propose(*inputs(sym), T))
                # The other pending obligation is repriced before this fill.
                # A check of BTC alone would still pass the 3x gross limit.
                e.quotes["ETHUSDT"] = replace(quote("10000"), symbol="ETHUSDT")
                order = e.active()[0]
                with j.transaction():
                    e._entry_fill(order, D(order["remaining"]), D(100), T+1000, "cap-test", True)
                self.assertEqual(j.db.execute("SELECT count(*) FROM fills").fetchone()[0], 0)
                self.assertEqual(e.query_order(order["id"])["state"], "CANCELED")
                reasons = [row[0] for row in j.db.execute(
                    "SELECT json_extract(payload,'$.reason') FROM events WHERE kind='CANCELED'")]
                self.assertIn("FILL_EXCEEDS_PORTFOLIO_CAP", reasons)
            finally:
                j.close()


if __name__ == "__main__": unittest.main()

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from quantos.paper.engine import PaperEngine
from quantos.paper.journal import Journal
from quantos.paper.risk import Account, Policy, ResearchPolicy, evaluate
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
            j = Journal(Path(tmp) / "research.db", "p1", "synthetic")
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


if __name__ == "__main__": unittest.main()

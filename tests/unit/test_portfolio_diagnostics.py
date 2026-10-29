import unittest
from collections import Counter
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import patch
import json

from quantos.paper.research.diagnostics import _engine_run, _json_counts, _stop_variant, chosen_cells
from quantos.paper.types import Signal


class TrainingDiagnosticsPlanTests(unittest.TestCase):
    def test_uses_saved_individual_and_picked_cells_without_rescoring(self):
        saved = {
            "individual_choices": {
                "crash_flush_e2": {"members": [{"strategy_id": "crash_flush_e2", "parameters": {"holding_hours": "48", "stop_fraction": "0.25"}, "risk_fraction": "0.04"}], "leverage": "1"},
                "alt_dip_reversal": {"members": [{"strategy_id": "alt_dip_reversal", "parameters": {"holding_hours": "24", "cutoff_quantile": "0.005", "stop_fraction": "0.25"}, "risk_fraction": "0.04"}], "leverage": "1"},
                "ema_level_2h": {"members": [{"strategy_id": "ema_level_2h", "parameters": {}, "risk_fraction": "0.005"}], "leverage": "1"},
            },
            "selection": {"cell": {"members": [
                {"strategy_id": "alt_dip_reversal", "parameters": {"holding_hours": "24", "cutoff_quantile": "0.005", "stop_fraction": "0.25"}, "risk_fraction": "0.04"},
                {"strategy_id": "crash_flush_e2", "parameters": {"holding_hours": "48", "stop_fraction": "0.25"}, "risk_fraction": "0.04"}], "leverage": "1"}},
        }
        cells = chosen_cells(saved)
        self.assertEqual(len(cells), 4)
        self.assertEqual(cells[-1], saved["selection"]["cell"])

    def test_stop_sensitivity_changes_only_wide_members(self):
        cell = {"members": [
            {"strategy_id": "crash_flush_e2", "parameters": {"stop_fraction": "0.25", "holding_hours": "48"}},
            {"strategy_id": "alt_dip_reversal", "parameters": {"stop_fraction": "0.25", "cutoff_quantile": "0.005"}},
            {"strategy_id": "ema_level_2h", "parameters": {}}], "leverage": "1"}
        changed = _stop_variant(cell)
        self.assertEqual(cell["members"][0]["parameters"]["stop_fraction"], "0.25")
        self.assertEqual(changed["members"][0]["parameters"]["stop_fraction"], "0.15")
        self.assertEqual(changed["members"][1]["parameters"]["stop_fraction"], "0.15")
        self.assertNotIn("stop_fraction", changed["members"][2]["parameters"])
        self.assertEqual(changed["members"][0]["parameters"]["holding_hours"], "48")

    def test_rejects_missing_chosen_candidate_instead_of_filling_from_grid(self):
        with self.assertRaises(ValueError):
            chosen_cells({"individual_choices": {}, "selection": {"cell": {}}})

    def test_cash_alternative_skips_only_the_missing_portfolio_pick(self):
        saved = {"individual_choices": {
            "crash_flush_e2": {"members": [{"strategy_id": "crash_flush_e2"}]},
            "alt_dip_reversal": {"members": [{"strategy_id": "alt_dip_reversal"}]},
            "ema_level_2h": {"members": [{"strategy_id": "ema_level_2h"}]},
        }, "selection": {"cell": None}}
        self.assertEqual(len(chosen_cells(saved)), 3)

    def test_matched_counts_are_stable_json_records(self):
        rows = _json_counts(Counter({("ETHUSDT", "2024-01"): 2,
                                     ("BTCUSDT", "2024-02"): 1}))
        self.assertEqual(rows, [
            {"symbol": "BTCUSDT", "month": "2024-02", "count": 1},
            {"symbol": "ETHUSDT", "month": "2024-01", "count": 2},
        ])
        self.assertEqual(json.loads(json.dumps(rows)), rows)

    def test_engine_materializes_signal_iterable_and_persists_equity_paths(self):
        signal = Signal("crash_flush_e2", "research-1.0.0", "id", "BTCUSDT", "LONG",
                        1_640_995_200_000, 1_640_995_290_000, "MARKET", D(100), D(75),
                        None, 86400, "test")
        simulation = SimpleNamespace(points=[(1, D(200))], adverse_points=[(1, D(190))],
            trades=[], rejections={}, approvals={}, paused_at_ms=None, funding_events=[],
            assumptions=("scenario",))
        cell = {"members": [{"strategy_id": "crash_flush_e2", "risk_fraction": "0.01"}],
                "leverage": "1"}
        with patch("quantos.paper.research.diagnostics.parse_exchange_info", return_value={}), \
             patch("quantos.paper.research.diagnostics.Simulator") as simulator, \
             patch("quantos.paper.research.diagnostics.summarize", return_value={}), \
             patch("quantos.paper.research.diagnostics.max_drawdown", return_value=0):
            simulator.return_value.run.return_value = simulation
            index = SimpleNamespace(bar_by_open_ms={}, mark_by_open_ms={}, funding={})
            result = _engine_run(index, {}, cell, iter((signal,)))
        self.assertEqual(result["signal_count"], 1)
        self.assertEqual(result["points"], [[1, "200"]])
        self.assertEqual(result["adverse_points"], [[1, "190"]])


if __name__ == "__main__":
    unittest.main()

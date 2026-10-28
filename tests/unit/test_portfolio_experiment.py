import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from quantos.paper.research.experiment import (TRAIN_START, TRAIN_END, individual_grid,
    combined_grid, recommendation, run_cell, run_training, select_individuals)


def result(cell, growth=.1, dd=.1, half=.1):
    return {"cell": cell, "status": "COMPLETE", "metrics": {
        "cagr": growth, "net_return": growth, "max_drawdown": dd},
        "kelly": {"half_fraction": half}}


class ExperimentTests(unittest.TestCase):
    def test_grid_is_fixed_without_stop_sensitivity_or_heldout_selection(self):
        grid = individual_grid()
        self.assertEqual(len(grid), 147)
        self.assertEqual(len({json.dumps(c, sort_keys=True) for c in grid}), 147)
        counts = {name: sum(c["members"][0]["strategy_id"] == name for c in grid)
                  for name in ("crash_flush_e2", "alt_dip_reversal", "ema_level_2h")}
        self.assertEqual(counts, {"crash_flush_e2": 45, "alt_dip_reversal": 90, "ema_level_2h": 12})
        for cell in grid:
            self.assertNotEqual(cell["members"][0]["parameters"].get("stop_fraction"), "0.15")
        with self.assertRaises(ValueError): run_cell(None, None, {}, grid[0], start_ms=TRAIN_END, end_ms=TRAIN_END+3600000)

    def test_cash_alternative_and_half_kelly_are_binding(self):
        grid = individual_grid()
        for rows in ([result(grid[0], growth=-.1)], [result(grid[0], half=None)],
                     [result(grid[0], half=.005)], [result(grid[0], dd=.5)]):
            pick = recommendation(rows, select_individuals(rows))
            self.assertEqual(pick["status"], "NO_ELIGIBLE_TRAINING_PORTFOLIO")
        pick = recommendation([result(grid[0])], {})
        self.assertEqual(pick["cell"], grid[0])

    def test_training_ties_choose_lower_risk_then_leverage(self):
        grid = individual_grid()
        high_risk = next(c for c in grid if c["members"][0]["risk_fraction"] == "0.08")
        rows = [result(grid[2]), result(high_risk), result(grid[0])]
        self.assertEqual(recommendation(rows, {})["cell"], grid[0])

    def test_combined_cells_use_only_training_selected_individual_settings(self):
        grid = individual_grid()
        rows = [result(next(c for c in grid if c["members"][0]["strategy_id"] == name))
                for name in ("crash_flush_e2", "alt_dip_reversal", "ema_level_2h")]
        chosen = select_individuals(rows)
        combos = combined_grid(chosen)
        self.assertEqual(len(combos), 12)
        for cell in combos:
            for member in cell["members"]:
                self.assertEqual(member, chosen[member["strategy_id"]]["cell"]["members"][0])
        bad = dict(chosen)
        bad["alt_dip_reversal"] = result(chosen["alt_dip_reversal"]["cell"], half=0)
        all3 = next(c for c in combos if len(c["members"]) == 3)
        self.assertEqual(recommendation([result(all3, growth=2)], bad)["status"],
                         "NO_ELIGIBLE_TRAINING_PORTFOLIO")

    def test_resume_reuses_cells_only_under_identical_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); filters = root / "filters.json"
            filters.write_text('{"symbols":[]}')
            cache = SimpleNamespace(data_hash="d", source_hash="s", filter_hash=hashlib.sha256(filters.read_bytes()).hexdigest())
            index = SimpleNamespace(content_hash="d")
            def fake(index, cache, filters, cell): return result(cell)
            with patch("quantos.paper.research.experiment.source_content_hash", return_value="a"*64), patch(
                    "quantos.paper.research.experiment.run_cell", side_effect=fake) as run:
                first = run_training(index, cache, filters, root / "out")
                self.assertEqual(run.call_count, 159)
                run.reset_mock()
                self.assertEqual(first, run_training(index, cache, filters, root / "out"))
                run.assert_not_called()
                cache.source_hash = "changed"
                with self.assertRaises(ValueError): run_training(index, cache, filters, root / "out")
            self.assertFalse((root / "out" / "evaluation_claim.json").exists())


if __name__ == "__main__": unittest.main()

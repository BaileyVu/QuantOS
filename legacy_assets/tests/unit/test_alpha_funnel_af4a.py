"""AF4A research-only regression checks; synthetic rows are never published as AF3."""
from copy import deepcopy
from decimal import Decimal, ROUND_DOWN, localcontext
import ast
import inspect
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from research import alpha_funnel_af4a as af


def synthetic():
    c = af.frozen_config()
    with af.af1_decimal_context():
        gross = Decimal(c["costs"][0]["round_trip_rate"]) + Decimal("0.0001")
        cost = {v["name"]: str(gross - Decimal(v["round_trip_rate"])) for v in c["costs"]}
    row = {
        "hypothesis_id": "synthetic.only", "symbol": "BTCUSDT", "horizon_minutes": 3,
        "hypothesis_family": "synthetic", "provisional_classification": "WATCH",
        "cost_adjusted_expectancy_unit": af.UNIT, "cost_adjusted_expectancy": cost,
        "gross_expectancy": str(gross), "deoverlapped_event_count": 60,
        "deoverlapped_event_frequency_per_day": "0.09", "descriptive_t_statistic": "2.1",
        "raw_p_value": "0.03",
        "research_viability_score": {"M": 1, "S": 3, "F": 0, "R": 0, "X": 4, "RVS": 8},
        "temporal_stability": {
            "robustness_evidence_ratio": None, "qualified_block_coverage": "0.375",
            "positive_qualified_block_count": 2,
        },
        "multiple_testing": {"q_value": "0.27", "passes_fdr_threshold": False},
    }
    return row, c, {"parameters": {"label": "synthetic"}, "human_explainability_score": 4}


class AF4AForensicsTests(unittest.TestCase):
    def setUp(self):
        self.row, self.config, self.hypothesis = synthetic()

    def diagnose(self):
        return af.diagnose(self.row, self.config, self.hypothesis)

    def population(self):
        return [dict(self.row, hypothesis_id=f"synthetic.{i:02}") for i in range(17)]

    def test_exactly_17_unique_watches(self):
        rows = self.population()
        self.assertEqual(len(af.validate_watch(rows)), 17)
        for invalid in (rows[:16], rows + [rows[0]], [rows[0]] * 17):
            with self.assertRaises(ValueError):
                af.validate_watch(invalid)

    def test_kill_rejected(self):
        self.row["provisional_classification"] = "KILL"
        with self.assertRaisesRegex(ValueError, "non-WATCH"):
            self.diagnose()

    def test_promote_rejected(self):
        rows = self.population()
        rows[5]["provisional_classification"] = "PROMOTE"
        with self.assertRaisesRegex(ValueError, "non-WATCH"):
            af.validate_watch(rows)

    def test_source_identity_and_hash_fail_closed(self):
        for run, digest in (("bad", af.SOURCE_HASH), (af.SOURCE_RUN, "bad"),
                            (af.SOURCE_RUN, af.SOURCE_HASH)):
            artifact = SimpleNamespace(run_id=run, results_sha256=digest,
                                       results_bytes=lambda: b"tampered")
            with patch.object(af, "AlphaFunnelArtifactStore") as store:
                store.return_value.load_verified.return_value = artifact
                with self.assertRaisesRegex(ValueError, "identity/hash"):
                    af.verify_source(Path("unused"))
                store.return_value.load_verified.assert_called_once_with(af.SOURCE_RUN)

    def test_missing_source_is_not_replaced_or_downloaded(self):
        with TemporaryDirectory() as directory:
            with self.assertRaises(Exception):
                af.verify_source(Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_frozen_gate_thresholds(self):
        gates = self.diagnose()["promotion_gates"]
        self.assertEqual(gates["COST"]["threshold"], self.config["classification"][
            "promote_min_base_cost_expectancy"])
        self.assertEqual(gates["SAMPLE_SUFFICIENCY"]["threshold"], 100)
        self.assertEqual(gates["MULTIPLE_TESTING"]["threshold"], "0.05")
        self.assertEqual(gates["RVS_COMPOSITE_EVIDENCE"]["threshold"], 13)

    def test_no_replacement_thresholds_or_caller_override(self):
        for key in self.config["classification"]:
            altered = deepcopy(self.config)
            altered["classification"][key] = "-1"
            with self.assertRaisesRegex(ValueError, "frozen AF3"):
                af.diagnose(self.row, altered, self.hypothesis)
        self.assertNotIn("threshold", inspect.signature(af.analyze).parameters)
        with self.assertRaises(TypeError):
            af.analyze(Path("unused"), threshold="0")

    def test_exact_cost_units_and_gap(self):
        out = self.diagnose()
        with af.af1_decimal_context():
            self.assertEqual(Decimal(out["cost_gap_from_promotion"]), Decimal("0.0001") -
                             Decimal(self.config["classification"]["promote_min_base_cost_expectancy"]))
        self.assertEqual(out["source_row"]["cost_adjusted_expectancy_unit"], af.UNIT)
        self.row["cost_adjusted_expectancy_unit"] = "percent"
        with self.assertRaisesRegex(ValueError, "unit"):
            self.diagnose()

    def test_cost_arithmetic_corruption_rejected(self):
        self.row["cost_adjusted_expectancy"]["base"] = "0.00010000000000000000001"
        with self.assertRaisesRegex(ValueError, "arithmetic"):
            self.diagnose()

    def test_exact_horizon_and_no_composition(self):
        rows = []
        for horizon in self.config["horizons_minutes"]:
            self.row["horizon_minutes"] = horizon
            out = self.diagnose()
            self.assertEqual(out["key"][2], horizon)
            rows.append(deepcopy(out))
        result = af.aggregate(rows, self.config)
        self.assertEqual({k: v["WATCH"] for k, v in result["horizon"].items()},
                         {str(h): 1 for h in self.config["horizons_minutes"]})
        self.assertNotIn("pooled_expectancy", result)

    def test_unsupported_horizon_fails(self):
        self.row["horizon_minutes"] = 2
        with self.assertRaisesRegex(ValueError, "horizon"):
            self.diagnose()

    def test_multiple_binding_failures_retained(self):
        self.assertEqual(self.diagnose()["binding_failures"], [
            "COST", "MULTIPLE_TESTING", "RVS_COMPOSITE_EVIDENCE", "SAMPLE_SUFFICIENCY"])

    def test_secondary_statistics_and_coverage_are_not_mandatory(self):
        out = self.diagnose()
        self.assertNotIn("TEMPORAL_ROBUSTNESS", out["promotion_gates"])
        self.assertNotIn("STATISTICAL_STRENGTH", out["promotion_gates"])
        self.assertFalse(out["temporal_component_checks"]["coverage"]["pass"])
        self.assertEqual({w["category"] for w in out["secondary_weaknesses"]},
                         {"COST", "SAMPLE_SUFFICIENCY", "STATISTICAL_STRENGTH",
                          "TEMPORAL_ROBUSTNESS"})

    def test_nominal_p_does_not_replace_closed_universe_q(self):
        out = self.diagnose()
        self.assertTrue(out["nominal_p_le_fdr_threshold_but_BH_fails"])
        self.assertFalse(out["promotion_gates"]["MULTIPLE_TESTING"]["pass"])

    def test_undefined_statistic_and_q_fail_closed(self):
        self.row["descriptive_t_statistic"] = None
        self.row["research_viability_score"].update(S=0, RVS=5)
        self.row["multiple_testing"]["q_value"] = None
        out = self.diagnose()
        self.assertEqual(out["components"]["S"]["score"], 0)
        self.assertFalse(out["promotion_gates"]["MULTIPLE_TESTING"]["pass"])

    def test_stored_component_corruption_fails(self):
        self.row["research_viability_score"]["R"] = 4
        with self.assertRaisesRegex(ValueError, "score mismatch"):
            self.diagnose()

    def test_stored_fdr_flag_corruption_fails(self):
        self.row["multiple_testing"]["passes_fdr_threshold"] = True
        with self.assertRaisesRegex(ValueError, "FDR"):
            self.diagnose()

    def test_counterfactual_does_not_mutate_predicates(self):
        out = self.diagnose()
        before = af.canonical_json_bytes(out["promotion_gates"])
        remaining = af.remaining_failures(out["promotion_gates"])
        self.assertEqual(len(remaining["COST"]), 3)
        remaining["COST"].clear()
        self.assertEqual(af.canonical_json_bytes(out["promotion_gates"]), before)

    def test_input_permutation_and_categories_deterministic(self):
        a = af.validate_watch(self.population())
        b = af.validate_watch(list(reversed(self.population())))
        self.assertEqual(af.canonical_json_bytes(a), af.canonical_json_bytes(b))
        self.assertEqual(self.diagnose(), self.diagnose())

    def test_input_metadata_unchanged(self):
        inputs = deepcopy((self.row, self.config, self.hypothesis))
        self.diagnose()
        self.assertEqual(inputs, (self.row, self.config, self.hypothesis))

    def test_decimal_environment_does_not_change_output(self):
        expected = af.canonical_json_bytes(self.diagnose())
        with localcontext() as context:
            context.prec = 6
            context.rounding = ROUND_DOWN
            actual = af.canonical_json_bytes(self.diagnose())
        self.assertEqual(actual, expected)

    def test_gate_equality_is_inclusive_without_rounding(self):
        self.assertTrue(af._gate("0.05", "0.05", "<=")["pass"])
        self.assertFalse(af._gate("0.050000000000000000000001", "0.05", "<=")["pass"])
        self.assertTrue(af._gate(100, 100, ">=")["pass"])
        self.assertFalse(af._gate(99, 100, ">=")["pass"])

    def test_serialized_output_and_identity_deterministic(self):
        first = af.canonical_json_bytes(self.diagnose())
        second = af.canonical_json_bytes(self.diagnose())
        self.assertEqual(first, second)
        self.assertEqual(af.digest(first), af.digest(second))
        altered = self.diagnose()
        altered["key"][2] = 5
        self.assertNotEqual(af.digest(first), af.digest(af.canonical_json_bytes(altered)))

    def minimal_artifact(self):
        # Publication mechanics only; deliberately not claiming this fixture is AF3.
        return {"interpretation": {}, "inventory": [], "aggregate": {},
                "code_identity": {"files_sha256": {
                    "research/alpha_funnel_af4a.py": af.digest(Path(af.__file__).read_bytes())}}}

    def test_immutable_publication_and_identical_rerun(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "sentinel").write_bytes(b"untouched")
            result = self.minimal_artifact()
            run, manifest = af.publish(result, root / "output", source)
            first = {p.name: p.read_bytes() for p in (root / "output/runs" / run).iterdir()}
            self.assertEqual(af.publish(result, root / "output", source), (run, manifest))
            self.assertEqual(first, {p.name: p.read_bytes()
                                    for p in (root / "output/runs" / run).iterdir()})
            self.assertEqual((source / "sentinel").read_bytes(), b"untouched")
            (root / "output/runs" / run / "analysis.json").write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "immutable"):
                af.publish(result, root / "output", source)

    def test_source_destination_overlap_rejected_without_writes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "disjoint"):
                af.publish(self.minimal_artifact(), root, root)
            self.assertEqual(list(root.iterdir()), [])

    def test_no_network_or_market_query_dependencies(self):
        allowed = {"argparse", "collections", "decimal", "hashlib", "json", "pathlib",
                   "subprocess", "tempfile", "__future__"}
        tree = ast.parse(Path(af.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], allowed)
            if isinstance(node, ast.ImportFrom):
                self.assertTrue(node.module.split(".")[0] in allowed or node.module in {
                    "quantos.domain.evaluation.alpha_funnel",
                    "quantos.infrastructure.storage.alpha_funnel"})
        source = inspect.getsource(af.verify_source)
        self.assertIn(".load_verified(SOURCE_RUN)", source)
        self.assertNotIn("screen_alpha_funnel(", Path(af.__file__).read_text(encoding="utf-8"))

    def test_forbidden_data_roles_not_accepted_as_source(self):
        # The API accepts a pinned run only, not dataset roles or date parameters.
        parameters = set(inspect.signature(af.analyze).parameters)
        self.assertEqual(parameters, {"source_root", "repo"})
        for role in ("sealed_oos", "screening_validation", "2026"):
            with self.assertRaises(TypeError):
                af.analyze(Path("unused"), dataset_role=role)

    def test_old_references_and_frozen_docs_unchanged_by_diagnostics(self):
        paths = list((af.REPO / "docs").glob("0[0-1][0-9]*.md"))
        paths += list((af.REPO / "research/alpha-funnel").rglob("*.json"))
        paths += [af.REPO / "research/phase-4d-mf1/run.json"]
        before = {p: af.digest(p.read_bytes()) for p in paths}
        self.diagnose()
        self.assertEqual(before, {p: af.digest(p.read_bytes()) for p in paths})

    def test_production_paths_do_not_depend_on_af4a(self):
        for path in (af.REPO / "src").rglob("*.py"):
            self.assertNotIn("alpha_funnel_af4a", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

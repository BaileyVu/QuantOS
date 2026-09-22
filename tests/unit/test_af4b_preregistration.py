"""AF4B preregistration tests; no market observation or experiment is loaded."""
from copy import deepcopy
import ast
import inspect
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from research import alpha_funnel_af4b_preregistration as af


def reidentify(document):
    value = deepcopy(document)
    value.pop("catalog_id", None)
    value["catalog_id"] = af.digest(af.canonical_json_bytes(value))
    return value


class AF4BPreregistrationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = af.build_catalog()

    def reject(self, mutate):
        value = deepcopy(self.catalog)
        mutate(value)
        with self.assertRaises(ValueError):
            af.validate_catalog(reidentify(value))

    def test_01_exact_hypothesis_count(self):
        self.assertEqual(len(self.catalog["hypotheses"]), 6)
        self.assertEqual(
            self.catalog["universe_limits"]["registered_primary_hypotheses"], 6
        )

    def test_02_hard_primary_hypothesis_maximum_enforced(self):
        def mutate(value):
            for index in range(3):
                extra = deepcopy(value["hypotheses"][0])
                extra["hypothesis_id"] = f"af4b.extra.{index}"
                value["hypotheses"].append(extra)
        self.reject(mutate)

    def test_03_hard_hypothesis_parameter_combination_maximum_enforced(self):
        def mutate(value):
            value["hypotheses"][0]["parameter_sets"].extend(
                {"parameter_set_id": f"extra-{index}"} for index in range(7)
            )
        self.reject(mutate)

    def test_04_one_parameterization_per_hypothesis(self):
        self.assertEqual(
            sum(len(row["parameter_sets"]) for row in self.catalog["hypotheses"]), 6
        )
        self.assertTrue(
            all(len(row["parameter_sets"]) == 1 for row in self.catalog["hypotheses"])
        )

    def test_05_stable_deterministic_catalog_id(self):
        first = af.build_catalog()
        second = af.build_catalog()
        self.assertEqual(first, second)
        self.assertEqual(
            first["catalog_id"],
            "7bf403626ef205c2b4026d2b9ceecb7ff6c7ab6bd19dbbfcfc726625d5034346",
        )
        without_id = {key: value for key, value in first.items() if key != "catalog_id"}
        self.assertEqual(first["catalog_id"], af.digest(af.canonical_json_bytes(without_id)))

    def test_06_canonical_serialization_is_unique(self):
        canonical = af.canonical_json_bytes(self.catalog)
        self.assertTrue(canonical.endswith(b"\n"))
        self.assertEqual(canonical, af.canonical_json_bytes(json.loads(canonical)))
        self.assertNotEqual(
            canonical,
            json.dumps(self.catalog, indent=2, sort_keys=True).encode("utf-8"),
        )

    def test_07_checked_in_catalog_is_canonical_and_valid(self):
        self.assertEqual(af.load_catalog(), self.catalog)
        self.assertEqual(
            af.CATALOG_PATH.read_bytes(), af.canonical_json_bytes(self.catalog)
        )

    def test_08_catalog_identity_tampering_rejected(self):
        value = deepcopy(self.catalog)
        value["catalog_id"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "catalog ID"):
            af.validate_catalog(value)

    def test_09_unknown_formula_input_rejected(self):
        self.reject(
            lambda value: value["hypotheses"][0]["required_inputs"].append(
                "candle.unknown_future_field"
            )
        )

    def test_10_raw_aggregate_trade_input_rejected(self):
        self.reject(
            lambda value: value["hypotheses"][0]["required_inputs"].append(
                "raw.aggregate_trade.price"
            )
        )
        prohibited = " ".join(self.catalog["information_family"]["prohibited_inputs"])
        self.assertIn("raw AggregateTrade", prohibited)

    def test_11_completed_minute_state_only(self):
        self.assertTrue(
            all(
                row["state_status"]
                == "validated completed-minute state finalized under DE1G policy"
                for row in self.catalog["hypotheses"]
            )
        )
        self.assertEqual(
            self.catalog["information_family"]["minute_semantics"], "PT1M:[start,end)"
        )

    def test_12_partial_or_live_open_state_rejected(self):
        self.reject(
            lambda value: value["hypotheses"][0].__setitem__(
                "state_status", "partial/live-open"
            )
        )
        prohibited = " ".join(self.catalog["information_family"]["prohibited_inputs"])
        self.assertIn("partial/open minute state", prohibited)

    def test_13_execution_occurs_after_de1g_knowability(self):
        outcome = self.catalog["execution_outcome_contract"]
        self.assertEqual(
            outcome["earliest_knowable_time"],
            "t+1m+5s, conditional on DE1G finalization and healthy source conditions",
        )
        self.assertEqual(outcome["entry_minute_offset"], 2)
        self.assertIn("t+2m", outcome["entry_proxy"])

    def test_14_t_plus_one_open_rejected(self):
        outcome = self.catalog["execution_outcome_contract"]
        self.assertFalse(outcome["t_plus_1_open_allowed"])
        self.reject(
            lambda value: value["execution_outcome_contract"].update(
                entry_minute_offset=1, t_plus_1_open_allowed=True
            )
        )

    def test_15_exact_symbol_scope(self):
        rows = self.catalog["evaluations"]
        pairs = [(row["source_symbol"], row["target_symbol"]) for row in rows]
        self.assertEqual(pairs.count(("BTCUSDT", "BTCUSDT")), 5)
        self.assertEqual(pairs.count(("ETHUSDT", "ETHUSDT")), 5)
        self.assertEqual(pairs.count(("BTCUSDT", "ETHUSDT")), 1)
        self.assertNotIn(("ETHUSDT", "BTCUSDT"), pairs)
        self.reject(
            lambda value: value["evaluations"][0].__setitem__(
                "target_symbol", "SOLUSDT"
            )
        )

    def test_16_exact_horizon_scope(self):
        by_hypothesis = {
            row["hypothesis_id"]: row["horizons_minutes"]
            for row in self.catalog["hypotheses"]
        }
        self.assertEqual(set(map(tuple, by_hypothesis.values())), {(1,), (5,)})
        self.assertNotIn(15, {h for values in by_hypothesis.values() for h in values})
        self.reject(
            lambda value: value["hypotheses"][0].__setitem__(
                "horizons_minutes", [1, 5]
            )
        )

    def test_17_undeclared_parameter_set_rejected(self):
        self.reject(
            lambda value: value["hypotheses"][0]["parameter_sets"].append(
                {"parameter_set_id": "post-hoc"}
            )
        )

    def test_18_undeclared_threshold_change_rejected(self):
        self.reject(
            lambda value: value["hypotheses"][0]["parameter_sets"][0].__setitem__(
                "imbalance_threshold", "0.1"
            )
        )

    def test_19_denominator_behavior_frozen(self):
        policies = [row["denominator_policy"] for row in self.catalog["hypotheses"]]
        self.assertTrue(
            all("no epsilon" in value or "no division" in value
                for value in policies)
        )
        self.reject(
            lambda value: value["hypotheses"][0].__setitem__(
                "denominator_policy", "replace zero denominator with epsilon"
            )
        )

    def test_20_no_individual_trade_inference(self):
        self.assertTrue(
            all(not row["individual_trade_inference"]
                for row in self.catalog["hypotheses"])
        )
        warning = next(
            row["semantic_warning"]
            for row in self.catalog["hypotheses"]
            if row["hypothesis_id"].startswith("af4b.h5.")
        )
        self.assertIn("AggregateTrade record", warning)
        self.assertIn("never per individual execution", warning)
        self.reject(
            lambda value: value["hypotheses"][4].__setitem__(
                "individual_trade_inference", True
            )
        )

    def test_21_development_role_only(self):
        data = self.catalog["data_contract"]
        self.assertEqual(data["allowed_role"], "DEVELOPMENT")
        self.assertTrue(
            all(row["dataset_role"] == "DEVELOPMENT"
                for row in self.catalog["evaluations"])
        )
        self.reject(
            lambda value: value["data_contract"].__setitem__(
                "allowed_role", "SCREENING_VALIDATION"
            )
        )

    def test_22_screening_validation_rejected(self):
        self.assertIn(
            "SCREENING_VALIDATION",
            self.catalog["data_contract"]["prohibited_roles"],
        )
        self.reject(
            lambda value: value["data_contract"]["prohibited_roles"].remove(
                "SCREENING_VALIDATION"
            )
        )

    def test_23_sealed_oos_rejected(self):
        self.assertIn("SEALED_OOS", self.catalog["data_contract"]["prohibited_roles"])
        self.reject(
            lambda value: value["data_contract"]["prohibited_roles"].remove(
                "SEALED_OOS"
            )
        )

    def test_24_2026_research_data_rejected(self):
        data = self.catalog["data_contract"]
        self.assertIn("2026_RESEARCH", data["prohibited_roles"])
        self.assertEqual(
            data["development_interval"]["end_exclusive"], af.DEVELOPMENT_END
        )
        self.assertFalse(any("2026-" in str(value)
                             for value in data["required_de1_daily_partition_dates"].values()))
        self.reject(
            lambda value: value["data_contract"]["prohibited_roles"].remove(
                "2026_RESEARCH"
            )
        )

    def test_25_fdr_universe_is_closed_and_complete(self):
        family = self.catalog["fdr_family"]
        evaluations = self.catalog["evaluations"]
        self.assertTrue(family["closed"])
        self.assertEqual(family["registered_evaluation_count"], 11)
        self.assertEqual(
            family["evaluation_ids"],
            [row["evaluation_id"] for row in evaluations],
        )
        self.reject(
            lambda value: value["fdr_family"]["evaluation_ids"].pop()
        )

    def test_26_undefined_tests_remain_in_multiplicity(self):
        policy = self.catalog["fdr_family"]["undefined_test_policy"]
        self.assertIn("p=1", policy)
        self.assertIn("never drop", policy)
        self.reject(
            lambda value: value["fdr_family"].__setitem__(
                "undefined_test_policy", "drop undefined rows"
            )
        )

    def test_27_costs_are_exactly_frozen(self):
        self.assertEqual(
            self.catalog["cost_contract"]["scenarios"],
            [
                {"name": "base", "round_trip_rate": af.BASE_COST},
                {"name": "stress_2c", "round_trip_rate": af.STRESS_COST},
            ],
        )
        self.assertFalse(self.catalog["cost_contract"]["optimization_allowed"])
        self.reject(
            lambda value: value["cost_contract"]["scenarios"][0].__setitem__(
                "round_trip_rate", "0"
            )
        )

    def test_28_temporal_robustness_rules_frozen(self):
        temporal = self.catalog["statistical_contract"]["temporal_robustness"]
        self.assertEqual(
            {
                "block_count": temporal["block_count"],
                "minimum_events_per_block": temporal["minimum_events_per_block"],
                "minimum_qualified_block_coverage":
                    temporal["minimum_qualified_block_coverage"],
                "minimum_positive_blocks_for_maximum_robustness":
                    temporal["minimum_positive_blocks_for_maximum_robustness"],
            },
            {
                "block_count": 8,
                "minimum_events_per_block": 10,
                "minimum_qualified_block_coverage": "0.75",
                "minimum_positive_blocks_for_maximum_robustness": 6,
            },
        )
        self.reject(
            lambda value: value["statistical_contract"]["temporal_robustness"].__setitem__(
                "block_count", 7
            )
        )

    def test_29_deterministic_data_boundaries(self):
        data = self.catalog["data_contract"]
        self.assertEqual(
            data["required_de1_daily_partition_dates"],
            {"first_inclusive": "2024-01-01", "last_inclusive": "2025-09-30"},
        )
        self.assertEqual(
            data["required_candle_interval"],
            {"start_inclusive": af.DEVELOPMENT_START,
             "end_exclusive": af.DEVELOPMENT_END},
        )
        self.assertEqual(
            data["required_de1_state_interval"], data["required_candle_interval"]
        )
        self.assertEqual(
            data["common_last_eligible_state_minute"],
            "2025-09-30T23:53:00Z",
        )
        self.reject(
            lambda value: value["data_contract"]["required_de1_daily_partition_dates"].__setitem__(
                "last_inclusive", "2025-10-01"
            )
        )

    def test_30_exact_join_and_missing_data_policy(self):
        alignment = self.catalog["alignment_contract"]
        self.assertEqual(alignment["join_keys"], ["symbol", "exact_UTC_minute"])
        self.assertEqual(alignment["missing_side_policy"], "observation ineligible")
        self.assertFalse(alignment["nearest_neighbor"])
        self.assertFalse(alignment["forward_fill"])
        self.assertFalse(alignment["interpolation"])

    def test_31_no_network_or_market_data_dependency(self):
        tree = ast.parse(Path(af.__file__).read_text(encoding="utf-8"))
        allowed = {
            "__future__", "copy", "decimal", "hashlib", "json", "pathlib", "typing"
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], allowed)
            elif isinstance(node, ast.ImportFrom):
                self.assertIn((node.module or "").split(".")[0], allowed)
        source = Path(af.__file__).read_text(encoding="utf-8").lower()
        for forbidden in (
            "import requests", "urllib", "httpx", "websocket",
            "read_parquet", "duckdb", "binance_client",
        ):
            self.assertNotIn(forbidden, source)
        self.assertFalse(self.catalog["data_contract"]["network_acquisition_in_this_phase"])

    def test_32_docs_000_through_010_unchanged(self):
        root = Path(af.__file__).resolve().parents[1]
        actual = {
            path: af.digest((root / path).read_bytes())
            for path in af.SPEC_HASHES
        }
        self.assertEqual(actual, af.SPEC_HASHES)

    def test_33_production_code_and_feature_engine_unchanged(self):
        root = Path(af.__file__).resolve().parents[1]
        result = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", "src/quantos"],
            cwd=root,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        for path in (root / "src/quantos").rglob("*.py"):
            self.assertNotIn(
                "alpha_funnel_af4b_preregistration",
                path.read_text(encoding="utf-8"),
            )

    def test_34_preregistration_has_no_execution_entrypoint(self):
        parameters = set(inspect.signature(af.build_catalog).parameters)
        self.assertEqual(parameters, set())
        for name in (
            "execute", "evaluate", "screen", "download", "acquire",
            "materialize", "predict", "train",
        ):
            self.assertFalse(hasattr(af, name))

    def test_35_immutable_catalog_write_and_conflict_detection(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            identity = af.write_catalog(path)
            payload = path.read_bytes()
            self.assertEqual(identity, self.catalog["catalog_id"])
            self.assertEqual(af.write_catalog(path), identity)
            self.assertEqual(path.read_bytes(), payload)
            path.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "immutable"):
                af.write_catalog(path)

    def test_36_document_matches_catalog_id_family_and_hypotheses(self):
        root = Path(af.__file__).resolve().parents[1]
        document = (root / "docs/011_ALPHA_DISCOVERY_FUNNEL_AF4B_DE1.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(self.catalog["catalog_id"], document)
        self.assertIn(self.catalog["fdr_family"]["fdr_family_id"], document)
        for hypothesis in self.catalog["hypotheses"]:
            self.assertIn(hypothesis["hypothesis_id"], document)

    def test_37_parent_research_hashes_match_authoritative_artifacts(self):
        root = Path(af.__file__).resolve().parents[1]
        for parent in af.PARENT_RESEARCH:
            value = json.loads((root / parent["path"]).read_bytes())
            self.assertEqual(
                af.digest(af.canonical_json_bytes(value)), parent["sha256"]
            )


if __name__ == "__main__":
    unittest.main()

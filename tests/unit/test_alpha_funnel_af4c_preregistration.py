"""Preregistration-only assertions: no market fixtures, readers, or outcomes."""
import ast
from copy import deepcopy
from decimal import Decimal, localcontext
import inspect
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from research import alpha_funnel_af4b_preregistration as af4b
from research import alpha_funnel_af4c_preregistration as af


ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/012_ALPHA_DISCOVERY_FUNNEL_AF4C_DE1.md"
EXPECTED = [
    ("af4c.h1.flow-confirmed-positive-impact", "flow-impact-sign-v1", 1, 1,
     "I_t > 0 AND D_t > 0", "unconditional eligible long outcome"),
    ("af4c.h2.buy-sell-vwap-separation", "side-vwap-order-v1", 5, 1,
     "V_buy_t > V_sell_t AND D_t > 0", "close_t > open_t"),
    ("af4c.h3.positive-impact-acceleration", "impact-change-one-minute-v1", 1, 2,
     "D_t > 0 AND D_t > D_{t-1}", "R_t > 0 AND R_t > R_{t-1}"),
    ("af4c.h4.five-minute-flow-impact-persistence", "five-minute-flow-impact-sign-v1", 5, 5,
     "I_5t > 0 AND close_t > V_5t", "close_t > open_{t-4}"),
    ("af4c.h5.btc-impact-led-eth-catchup", "btc-impact-eth-lag-v1", 5, 1,
     "I_BTC,t > 0 AND D_BTC,t > 0 AND D_ETH,t <= 0",
     "BTC close_t > BTC open_t AND ETH close_t <= ETH open_t"),
]


def reidentify(value):
    """Rehash even nested definitions so rejection cannot rely on stale IDs."""
    result = deepcopy(value)

    def identify(row, field):
        old = row.pop(field, None)
        row[field] = af.digest(af.canonical_json_bytes(row))
        return old, row[field]

    definitions = dict(identify(h, "hypothesis_definition_sha256")
                       for h in result.get("hypotheses", []))
    evaluations = {}
    for row in result.get("evaluations", []):
        field = "hypothesis_definition_sha256"
        if field in row:
            row[field] = definitions.get(row[field], row[field])
        old, new = identify(row, "evaluation_id")
        evaluations[old] = new
    family = result.get("fdr_family")
    if family is not None:
        if "evaluation_ids" in family:
            family["evaluation_ids"] = [evaluations.get(e, e) for e in family["evaluation_ids"]]
        identify(family, "fdr_family_id")
    identify(result, "catalog_id")
    return result


def leaves(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from leaves(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from leaves(child, (*path, index))
    else:
        yield path, value


class AF4CPreregistrationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = af.build_catalog()

    def reject(self, mutate):
        altered = deepcopy(self.catalog)
        mutate(altered)
        with self.assertRaises(ValueError):
            af.validate_catalog(reidentify(altered))

    def test_exact_five_single_parameter_definitions_and_formulas(self):
        rows = self.catalog["hypotheses"]
        self.assertEqual(len(rows), 5)
        self.assertEqual(sum(len(h["parameter_sets"]) for h in rows), 5)
        for row, (identity, parameter, horizon, lookback, formula, comparator) in zip(rows, EXPECTED):
            with self.subTest(identity=identity):
                self.assertEqual(row["hypothesis_id"], identity)
                self.assertEqual(row["parameter_sets"], [{"parameter_set_id": parameter}])
                self.assertEqual(row["horizons_minutes"], [horizon])
                self.assertEqual(row["lookback_minutes"], lookback)
                self.assertEqual(row["formula"], formula)
                self.assertEqual(row["comparator_formula"], comparator)
                self.assertEqual(row["direction"], "UP")
                self.assertFalse(row["production_short_authorized"])
                self.assertEqual(row["human_explainability_score"], 4)

    def test_exact_nine_expansions_and_unique_ids(self):
        expected = []
        for identity, parameter, horizon, *_ in EXPECTED[:4]:
            for symbol in ("BTCUSDT", "ETHUSDT"):
                expected.append((identity, parameter, (symbol,), symbol, horizon))
        expected.append((EXPECTED[4][0], EXPECTED[4][1], ("BTCUSDT", "ETHUSDT"), "ETHUSDT", 5))
        actual = [(e["hypothesis_id"], e["parameter_set_id"], tuple(e["source_symbols"]),
                   e["target_symbol"], e["horizon_minutes"]) for e in self.catalog["evaluations"]]
        self.assertEqual(actual, expected)
        self.assertEqual(len({e["evaluation_id"] for e in self.catalog["evaluations"]}), 9)
        self.assertTrue(all(e["evaluator_id"] == "af4c-impact-screen-tplus2-v1"
                            for e in self.catalog["evaluations"]))

    def test_fdr_closed_nine_member_family(self):
        family = self.catalog["fdr_family"]
        self.assertEqual(family["method"], "Benjamini-Hochberg")
        self.assertEqual(family["alpha"], "0.05")
        self.assertIs(family["closed"], True)
        self.assertEqual(family["registered_evaluation_count"], 9)
        self.assertEqual(family["evaluation_ids"], [e["evaluation_id"] for e in self.catalog["evaluations"]])
        self.assertIn("p=1", family["undefined_test_policy"])
        self.assertIn("never drop", family["undefined_test_policy"])
        self.assertFalse(family["comparator_tests_in_family"])
        self.assertNotEqual(family["fdr_family_id"], af4b.build_catalog()["fdr_family"]["fdr_family_id"])

    def test_development_interval_partitions_and_protected_roles(self):
        data = self.catalog["data_contract"]
        interval = {"start_inclusive": "2024-01-01T00:00:00Z", "end_exclusive": "2025-10-01T00:00:00Z"}
        self.assertEqual(data["allowed_role"], "DEVELOPMENT")
        for key in ("development_interval", "required_candle_interval", "required_de1_state_interval"):
            self.assertEqual(data[key], interval)
        self.assertEqual(data["required_symbols"], ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(data["required_de1_daily_partition_dates"],
                         {"first_inclusive": "2024-01-01", "last_inclusive": "2025-09-30"})
        self.assertEqual(data["prohibited_roles"], ["SCREENING_VALIDATION", "SEALED_OOS", "RESERVE", "2026_RESEARCH"])
        self.assertFalse(data["lookback_before_development_allowed"])
        self.assertFalse(data["data_after_development_allowed"])
        self.assertFalse(data["network_acquisition_in_this_phase"])
        self.assertEqual(data["common_last_eligible_state_minute"], "2025-09-30T23:53:00Z")
        self.assertEqual([h["first_eligible_state_minute"] for h in self.catalog["hypotheses"]], [
            "2024-01-01T00:00:00Z", "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z",
            "2024-01-01T00:04:00Z", "2024-01-01T00:00:00Z"])

    def test_exact_parent_causal_contract_except_evaluator_identity(self):
        contract = self.catalog["execution_outcome_contract"]
        original = af4b.build_catalog()["execution_outcome_contract"]
        for key in original:
            if key not in ("evaluator_id", "af1_compatibility"):
                self.assertEqual(contract[key], original[key], key)
        self.assertEqual(contract["entry_minute_offset"], 2)
        self.assertFalse(contract["t_plus_1_open_allowed"])
        self.assertEqual(contract["gross_return_formula"], "close[t+(H+1)m]/open[t+2m]-1")
        self.assertIn("t+1m+5s", contract["earliest_knowable_time"])

    def test_exact_allowed_inputs_and_no_raw_triggers(self):
        expected = {
            "de1.total_base_quantity", "de1.total_quote_notional",
            "de1.aggressive_buy_base_quantity", "de1.aggressive_sell_base_quantity",
            "de1.aggressive_buy_quote_notional", "de1.aggressive_sell_quote_notional",
            "candle.open", "candle.close",
        }
        self.assertEqual(set(self.catalog["information_family"]["allowed_formula_inputs"]), expected)
        self.assertEqual(set().union(*(set(h["required_inputs"]) for h in self.catalog["hypotheses"])), expected)
        self.assertIn("raw AggregateTrade events as signal triggers", self.catalog["information_family"]["prohibited_inputs"])
        self.assertTrue(all(not h["individual_trade_inference"] for h in self.catalog["hypotheses"]))

    def test_exact_derived_definitions_and_denominators(self):
        defs = self.catalog["derived_definitions"]
        expected = {
            "V_t": ("Q_t / B_t", "B_t > 0 AND Q_t > 0"),
            "I_t": ("(Q_buy_t - Q_sell_t) / Q_t", "Q_t > 0"),
            "D_t": ("(close_t - V_t) / V_t", "V_t > 0"),
            "V_buy_t": ("Q_buy_t / B_buy_t", "B_buy_t > 0 AND Q_buy_t > 0"),
            "V_sell_t": ("Q_sell_t / B_sell_t", "B_sell_t > 0 AND Q_sell_t > 0"),
            "R_t": ("close_t / open_t - 1", "validated completed candle with open_t > 0"),
        }
        for name, (formula, condition) in expected.items():
            self.assertEqual(defs[name], {"formula": formula, "defined_when": condition})
        self.assertEqual(defs["undefined_required_value_policy"],
                         "observation ineligible; no epsilon, zero substitution, or imputation")
        self.assertIn("exact Decimal-compatible", defs["arithmetic"])

    def test_five_minute_window_frozen(self):
        self.assertEqual(self.catalog["derived_definitions"]["five_minute"], {
            "window": "t-4 through t inclusive",
            "Q_5t": "sum(Q_j)", "B_5t": "sum(B_j)",
            "NetQ_5t": "sum(Q_buy_j - Q_sell_j)",
            "V_5t": "Q_5t / B_5t", "I_5t": "NetQ_5t / Q_5t",
            "defined_when": "all five completed minutes and required candles exist inside DEVELOPMENT; Q_5t > 0 AND B_5t > 0",
            "alternative_window_allowed": False,
        })

    def test_h5_alignment_and_no_symmetric_duplicate(self):
        h5 = self.catalog["hypotheses"][4]
        self.assertEqual(h5["symbol_bindings"], [{"source_symbols": ["BTCUSDT", "ETHUSDT"], "target_symbol": "ETHUSDT"}])
        self.assertFalse(h5["symmetric_eth_to_btc_allowed"])
        self.assertEqual(h5["alignment"], "exact same UTC state minute only; no lag search, nearest-neighbor alignment, forward-fill, or interpolation")
        alignment = self.catalog["alignment_contract"]
        self.assertEqual(alignment["join_keys"], ["symbol", "exact_UTC_minute"])
        for key in ("lag_search", "nearest_neighbor", "forward_fill", "interpolation"):
            self.assertFalse(alignment[key])

    def test_costs_exact_and_inherited_without_change(self):
        costs = self.catalog["cost_contract"]
        self.assertEqual(costs, af4b.build_catalog()["cost_contract"])
        self.assertEqual([s["round_trip_rate"] for s in costs["scenarios"]], [
            "0.002503128284573645913980997751940", "0.005006256569147291827961995503880"])
        with localcontext() as context:
            context.prec = 80
            self.assertEqual(Decimal(costs["scenarios"][1]["round_trip_rate"]),
                             2 * Decimal(costs["scenarios"][0]["round_trip_rate"]))

    def test_statistics_score_bands_and_gates_unchanged(self):
        stat = self.catalog["statistical_contract"]
        self.assertEqual(stat, af4b.build_catalog()["statistical_contract"])
        self.assertEqual(stat["promotion_gates"], {
            "base_cost_adjusted_expectancy_at_least": "0.002503128284573645913980997751940",
            "deoverlapped_event_count_at_least": 100, "BH_q_value_at_most": "0.05",
            "research_viability_score_at_least": 13})
        self.assertEqual(stat["deterministic_bootstrap"], {"samples": 200, "confidence": "0.95", "random_seed": 20260914})
        self.assertEqual(stat["gross_mean_test_null"], "mean directional gross return <= 0")
        self.assertTrue(stat["descriptive_t_statistic"])
        temporal = stat["temporal_robustness"]
        self.assertEqual((temporal["block_count"], temporal["minimum_events_per_block"],
                          temporal["minimum_qualified_block_coverage"],
                          temporal["minimum_positive_blocks_for_maximum_robustness"]), (8, 10, "0.75", 6))

    def test_incremental_comparator_contract(self):
        contract = self.catalog["incremental_information_contract"]
        parent = af4b.build_catalog()["incremental_information_contract"]
        self.assertEqual({k: v for k, v in contract.items() if k != "question"},
                         {k: v for k, v in parent.items() if k != "question"})
        self.assertIn("strictly positive aggregate gross-expectancy difference", contract["new_information_rule"])
        self.assertIn("cannot advance", contract["candle_restatement_rule"])
        for row in self.catalog["hypotheses"]:
            self.assertIn("identical target, horizon, eligibility chronology and common support", row["comparator_support"])

    def test_shadow_policy_and_independence(self):
        policy = self.catalog["shadow_policy"]
        self.assertTrue(policy["execution_requires_separate_human_authorization"])
        self.assertFalse(policy["automatic_unsealing"])
        self.assertFalse(policy["policy_changes_after_af4c_outcomes_allowed"])
        self.assertIn("zero PROMOTE", policy["case_a"])
        self.assertIn("explicit human-approved", policy["case_a"])
        self.assertIn("one or more PROMOTE", policy["case_b"])
        self.assertIn("remain sealed", policy["case_b"])
        self.assertIn("remain sealed", policy["unknown_or_nonterminal_af4b"])
        self.assertEqual(policy["normal_stdout_and_final_reports_prohibit"], [
            "signal counts", "returns", "expectancies", "p-values", "q-values", "rankings", "classifications", "candidate identities"])
        independence = self.catalog["af4b_independence"]
        self.assertTrue(independence["af4b_preregistration_known"])
        self.assertTrue(independence["frozen_while_af4b_pending"])
        self.assertFalse(independence["af4b_performance_or_outcomes_inspected"])
        self.assertFalse(independence["outcome_dependent_af4b_modification"])
        self.assertIn("not fresh independent confirmation", independence["generation_interpretation"])
        self.assertIn("untouched validation", self.catalog["promotion_interpretation"])

    def test_lineage_hashes_and_no_outcome_references(self):
        lineage = self.catalog["lineage"]
        self.assertEqual(lineage["starting_head"], "3722019f461ea28eb9e49c2de27e6cd259dfb609")
        self.assertEqual(len(lineage["specifications"]), 12)
        self.assertEqual([p["path"][5:8] for p in lineage["specifications"]], [f"{i:03}" for i in range(12)])
        for item in lineage["specifications"]:
            self.assertEqual(af.digest((ROOT / item["path"]).read_bytes()), item["sha256"])
        self.assertEqual(len(lineage["parent_research"]), 1)
        parent = lineage["parent_research"][0]
        self.assertEqual(parent["path"], "research/alpha-funnel/af4b/catalog.json")
        self.assertEqual(af.digest((ROOT / parent["path"]).read_bytes()), parent["sha256"])
        self.assertEqual(parent["catalog_id"], af4b.load_catalog()["catalog_id"])
        serialized = af.canonical_json_bytes(self.catalog).decode()
        for forbidden in ("run.json", "results/", "output/", "G:\\", "alpha-funnel-af4b"):
            self.assertNotIn(forbidden, serialized)

    def test_af4b_formulas_not_duplicated(self):
        original = {h["formula"] for h in af4b.build_catalog()["hypotheses"]}
        self.assertTrue(original.isdisjoint(h["formula"] for h in self.catalog["hypotheses"]))

    def test_deterministic_canonical_catalog_and_all_content_ids(self):
        self.assertEqual(self.catalog, af.build_catalog())
        self.assertEqual(self.catalog, reidentify(self.catalog))
        self.assertEqual(af.load_catalog(), self.catalog)
        self.assertEqual(af.CATALOG_PATH.read_bytes(), af.canonical_json_bytes(self.catalog))
        identified = [(self.catalog, "catalog_id"), (self.catalog["fdr_family"], "fdr_family_id")]
        identified += [(h, "hypothesis_definition_sha256") for h in self.catalog["hypotheses"]]
        identified += [(e, "evaluation_id") for e in self.catalog["evaluations"]]
        for value, field in identified:
            without = {k: v for k, v in value.items() if k != field}
            self.assertEqual(value[field], af.digest(af.canonical_json_bytes(without)))
        self.assertEqual(self.catalog["schema_version"], "af4c-de1-preregistration-v1")
        self.assertEqual(self.catalog["catalog_id"], "71b244b9843bc6c105f9b761f23437984e4ee87ee6c5156f300e72c8bb785350")
        self.assertEqual(self.catalog["fdr_family"]["fdr_family_id"], "f0d87b0935d56a8c53f3939304c3c071cc4d6ec33d59ef9029a775322432bd42")

    def test_every_leaf_alteration_fails_even_with_recomputed_ids(self):
        for path, value in leaves(self.catalog):
            if path[-1] in ("catalog_id", "evaluation_id", "fdr_family_id", "hypothesis_definition_sha256"):
                continue
            with self.subTest(path=path):
                altered = deepcopy(self.catalog)
                target = altered
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = not value if type(value) is bool else value + 1 if type(value) is int else value + " altered"
                with self.assertRaises(ValueError):
                    af.validate_catalog(reidentify(altered))

    def test_unknown_and_missing_fields_at_every_object_fail(self):
        def objects(value, path=()):
            if isinstance(value, dict):
                yield path, value
                for key, child in value.items():
                    yield from objects(child, (*path, key))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    yield from objects(child, (*path, index))
        for path, original in objects(self.catalog):
            for action in ("add", "remove"):
                with self.subTest(path=path, action=action):
                    altered = deepcopy(self.catalog)
                    target = altered
                    for key in path:
                        target = target[key]
                    if action == "add":
                        target["unknown_field"] = True
                    else:
                        target.pop(next(iter(original)))
                    with self.assertRaises(ValueError):
                        af.validate_catalog(reidentify(altered))

    def test_extra_hypothesis_parameter_horizon_and_evaluation_fail(self):
        mutations = [
            lambda d: d["hypotheses"].append(deepcopy(d["hypotheses"][0])),
            lambda d: d["hypotheses"][0]["parameter_sets"].append({"parameter_set_id": "extra"}),
            lambda d: d["hypotheses"][0]["horizons_minutes"].append(5),
            lambda d: d["evaluations"].append(deepcopy(d["evaluations"][0])),
            lambda d: d["evaluations"].pop(),
            lambda d: d["fdr_family"]["evaluation_ids"].pop(),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.reject(mutate)

    def test_altered_content_ids_fail_without_reidentification(self):
        for path in (("catalog_id",), ("fdr_family", "fdr_family_id"),
                     ("hypotheses", 0, "hypothesis_definition_sha256"),
                     ("evaluations", 0, "evaluation_id")):
            with self.subTest(path=path):
                altered = deepcopy(self.catalog)
                target = altered
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = "0" * 64
                with self.assertRaises(ValueError):
                    af.validate_catalog(altered)

    def test_type_confusion_and_malformed_documents_fail(self):
        for value in ([], None, "catalog", 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                af.validate_catalog(value)
        self.reject(lambda d: d["execution_outcome_contract"].update(entry_minute_offset=2.0))
        self.reject(lambda d: d["scope"].update(research_only=1))
        for payload in (b"{", b"NaN", b"[]", b"{}", b"{\"a\":1,\"a\":1}",
                        b'{"nested":{"a":1,"a":1}}'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                af.parse_catalog(payload)
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            af.parse_catalog(b'{"schema_version":"first","schema_version":"last"}')
        for payload in (json.dumps(self.catalog, indent=2).encode(), af.canonical_json_bytes(self.catalog).rstrip()):
            with self.assertRaisesRegex(ValueError, "canonical"):
                af.parse_catalog(payload)

    def test_immutable_writer_roundtrip_and_conflict(self):
        with TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "catalog.json"
            identity = af.write_catalog(path)
            self.assertEqual(identity, self.catalog["catalog_id"])
            self.assertEqual(af.write_catalog(path), identity)
            self.assertEqual(af.load_catalog(path), self.catalog)
            path.write_bytes(b"conflict")
            with self.assertRaisesRegex(ValueError, "immutable"):
                af.write_catalog(path)
            self.assertEqual(path.read_bytes(), b"conflict")

    def test_return_values_do_not_mutate_frozen_builder(self):
        returned = af.validate_catalog(self.catalog)
        returned["hypotheses"][0]["formula"] = "altered"
        self.assertEqual(self.catalog, af.build_catalog())
        self.assertNotEqual(returned, self.catalog)

    def test_no_data_or_evaluator_dependency(self):
        source = Path(af.__file__).read_text(encoding="utf-8")
        allowed = {"__future__", "copy", "decimal", "hashlib", "json", "pathlib", "typing"}
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                self.assertTrue(all(alias.name in allowed for alias in node.names))
            elif isinstance(node, ast.ImportFrom):
                self.assertIn(node.module, allowed)
        self.assertFalse(inspect.signature(af.build_catalog).parameters)
        for name in ("execute", "evaluate", "screen", "download", "acquire", "materialize", "predict", "train"):
            self.assertFalse(hasattr(af, name))
        for fragment in ("read_parquet", "duckdb", "requests", "urllib", "subprocess"):
            self.assertNotIn(fragment, source)

    def test_document_binds_final_catalog_and_hypotheses(self):
        document = DOC.read_text(encoding="utf-8")
        for value in (self.catalog["catalog_id"], self.catalog["fdr_family"]["fdr_family_id"], af.STARTING_HEAD):
            self.assertIn(value, document)
        for identity, parameter, *_ in EXPECTED:
            self.assertIn(identity, document)
            self.assertIn(parameter, document)


if __name__ == "__main__":
    unittest.main()

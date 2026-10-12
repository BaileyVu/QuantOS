"""AF2A fixed-catalog, causal-formula, and artifact identity tests."""

from __future__ import annotations

from dataclasses import fields, replace
from decimal import Decimal, ROUND_DOWN, localcontext
from hashlib import sha256
import json
from pathlib import Path
import unittest

from quantos.domain.evaluation.alpha_catalog import (
    ALLOWED_INPUTS,
    APPROVED_FEATURE_INPUTS,
    CANONICAL_CANDLE_INPUTS,
    AF1_HORIZONS_MINUTES,
    AF1_SYMBOLS,
    AF2A_FDR_PLAN_SCHEMA_VERSION,
    CATALOG_SOURCE,
    CONSERVATIVE_RESEARCH_MARGIN,
    CONSERVATIVE_RESEARCH_MARGIN_MULTIPLE,
    ONE_WAY_FEE_RATE,
    ONE_WAY_SLIPPAGE_RATE,
    ROUND_TRIP_COST_FLOOR,
    CatalogError,
    CatalogStatus,
    ExpectedDirection,
    HypothesisFamily,
    ParameterSet,
    activity_concentration,
    average_trade_notional,
    break_even_win_rate,
    c3_tail_direction,
    close_location,
    derive_fdr_plan,
    gross_expectancy,
    load_and_verify_fdr_plan,
    load_catalog_bytes,
    multiplicative_net_expectancy,
    nearest_rank_quantile,
    path_efficiency,
    quote_volume_per_absolute_return_proxy,
    relative_activity,
    reversal_from_prior_pressure,
    root_mean_square_return,
    safe_ratio,
    simple_return,
    wick_fractions,
)
from quantos.domain.evaluation.alpha_funnel import (
    Direction,
    HypothesisMetadata,
    af1_decimal_context,
    canonical_json_bytes,
)
from quantos.domain.features import FEATURE_NAMES
from quantos.domain.market_data.contracts import Candle


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = ROOT / "research" / "alpha-funnel" / "af2a"
EXPECTED_IDS = (
    "af2a.a1.negative-return-shock-reversal-up",
    "af2a.a2.positive-return-shock-reversal-down",
    "af2a.a3.lower-wick-after-negative-move-reversal-up",
    "af2a.a4.upper-wick-after-positive-move-reversal-down",
    "af2a.a5.large-range-weak-close-failed-continuation",
    "af2a.a6.overextension-deteriorating-efficiency-reversal",
    "af2a.b1.high-efficiency-positive-return-continuation-up",
    "af2a.b2.high-efficiency-negative-return-continuation-down",
    "af2a.b3.range-expansion-close-high-continuation-up",
    "af2a.b4.range-expansion-close-low-continuation-down",
    "af2a.b5.directional-move-high-participation-continuation",
    "af2a.b6.multi-horizon-return-alignment-continuation",
    "af2a.c1.short-long-volatility-compression",
    "af2a.c2.short-long-volatility-expansion",
    "af2a.c3.compression-directional-range-expansion",
    "af2a.c4.positive-shock-high-volatility-reversal-down",
    "af2a.c5.negative-shock-high-volatility-reversal-up",
    "af2a.c6.volatility-expansion-low-directional-efficiency",
    "af2a.d1.base-volume-surprise",
    "af2a.d2.quote-volume-surprise",
    "af2a.d3.trade-count-surprise",
    "af2a.d4.average-trade-notional-surprise",
    "af2a.d5.volume-per-range-activity-concentration",
    "af2a.d6.quote-volume-per-absolute-return-proxy",
    "af2a.e1.high-base-volume-weak-return",
    "af2a.e2.high-quote-volume-weak-return",
    "af2a.e3.high-trade-count-narrow-range",
    "af2a.e4.high-activity-declining-efficiency",
    "af2a.e5.large-return-low-participation",
    "af2a.e6.large-return-high-participation",
    "af2a.f1.one-minute-opposed-fifteen-minute-state",
    "af2a.f2.one-minute-aligned-fifteen-minute-state",
    "af2a.f3.completed-five-minute-aligned-fifteen-minute-state",
    "af2a.f4.high-versus-low-efficiency-conditioning",
    "af2a.f5.high-versus-low-volatility-conditioning",
    "af2a.f6.upside-downside-shock-asymmetry",
)


def catalog():
    return load_catalog_bytes((ARTIFACT_ROOT / "catalog.json").read_bytes())


def reverse_mappings(value):
    if type(value) is dict:
        return {key: reverse_mappings(value[key]) for key in reversed(tuple(value))}
    if type(value) is list:
        return [reverse_mappings(item) for item in value]
    return value


class AlphaCatalogTests(unittest.TestCase):
    def test_exact_human_directed_catalog_universe(self) -> None:
        actual = catalog()
        self.assertEqual(
            actual.catalog_id,
            "060147848ab0d3aa3deb4a77fe0cf5af6bdd10a374587c24e5ff7a640a0a5d74",
        )
        self.assertEqual(tuple(item.stable_id for item in actual.entries), EXPECTED_IDS)
        self.assertEqual(len(set(EXPECTED_IDS)), 36)
        self.assertEqual(CATALOG_SOURCE, "human_directed_fixed_catalog")
        self.assertEqual(
            {
                family: sum(item.family is family for item in actual.entries)
                for family in HypothesisFamily
            },
            {family: 6 for family in HypothesisFamily},
        )

    def test_interface_repair_changed_only_three_input_declarations(self) -> None:
        current = json.loads((ARTIFACT_ROOT / "catalog.json").read_bytes())
        self.assertEqual(
            current["catalog_id"],
            "060147848ab0d3aa3deb4a77fe0cf5af6bdd10a374587c24e5ff7a640a0a5d74",
        )
        previous_inputs = {
            "af2a.c4.positive-shock-high-volatility-reversal-down": [
                "close", "feature:realized_volatility_20m"
            ],
            "af2a.c5.negative-shock-high-volatility-reversal-up": [
                "close", "feature:realized_volatility_20m"
            ],
            "af2a.f1.one-minute-opposed-fifteen-minute-state": [
                "feature:return_1m", "feature:return_15m"
            ],
        }
        reconstructed_previous = json.loads(json.dumps(current))
        for entry in reconstructed_previous["hypotheses"]:
            if entry["stable_id"] in previous_inputs:
                self.assertEqual(entry["causal_inputs"], ["close"])
                entry["causal_inputs"] = previous_inputs[entry["stable_id"]]
        identity = dict(reconstructed_previous)
        identity.pop("catalog_id")
        self.assertEqual(
            sha256(canonical_json_bytes(identity)).hexdigest(),
            "8292efbe030f3dff7a542efc6d4e841c73ec93b7c2116b363de688cc483290b9",
        )

    def test_retained_inputs_are_af1_candle_contract_compatible(self) -> None:
        actual = catalog()
        by_id = {entry.stable_id: entry for entry in actual.entries}
        for stable_id in (
            "af2a.c4.positive-shock-high-volatility-reversal-down",
            "af2a.c5.negative-shock-high-volatility-reversal-up",
            "af2a.f1.one-minute-opposed-fifteen-minute-state",
        ):
            self.assertEqual(by_id[stable_id].causal_inputs, ("close",))

        retained = actual.af3_entries()
        self.assertEqual(len(retained), 15)
        self.assertFalse(
            any(value.startswith("feature:") for entry in retained for value in entry.causal_inputs)
        )
        plan = derive_fdr_plan(actual)
        self.assertEqual(plan["planned_definition_count"], 44)
        self.assertEqual(plan["planned_registered_test_count"], 616)
        self.assertFalse(
            any(
                value.startswith("feature:")
                for row in plan["planned_definitions"]
                for value in row["required_inputs"]
            )
        )
        for index, row in enumerate(plan["planned_definitions"]):
            with self.subTest(index=index, definition=row["planned_definition_sha256"]):
                direction = (
                    Direction.UP if row["direction"].endswith("_up") else Direction.DOWN
                )
                metadata = HypothesisMetadata(
                    stable_id=f"af3.input-contract-probe.{index}",
                    implementation_id="af3-input-contract-probe-v1",
                    family=row["family"],
                    description="AF2A to AF1 required-input compatibility probe.",
                    direction=direction,
                    interpretation="Validates only the AF1 Candle input contract.",
                    required_inputs=tuple(row["required_inputs"]),
                    parameters={},
                    causal_lookback=row["causal_lookback"],
                )
                self.assertEqual(metadata.required_inputs, tuple(row["required_inputs"]))

    def test_retained_feature_inputs_fail_closed_but_nonretained_remain_supported(self) -> None:
        actual = catalog()
        retained = actual.af3_entries()[0]
        with self.assertRaisesRegex(
            CatalogError, "retained AF3 causal_inputs must be canonical Candle fields"
        ):
            replace(retained, causal_inputs=("feature:return_1m",))

        nonretained = next(
            entry
            for entry in actual.entries
            if entry.status is not CatalogStatus.RETAIN_AF3
            and any(value.startswith("feature:") for value in entry.causal_inputs)
        )
        rebuilt = replace(nonretained)
        self.assertTrue(any(value.startswith("feature:") for value in rebuilt.causal_inputs))

    def test_cost_model_and_predeclared_parameter_sets_are_fixed(self) -> None:
        actual = catalog()
        reloaded_by_id = {entry.stable_id: entry for entry in catalog().entries}
        self.assertEqual(ONE_WAY_FEE_RATE, Decimal("0.001"))
        self.assertEqual(ONE_WAY_SLIPPAGE_RATE, Decimal("0.00025"))
        self.assertEqual(
            ROUND_TRIP_COST_FLOOR,
            Decimal("0.002503128284573645913980997751940"),
        )
        self.assertEqual(CONSERVATIVE_RESEARCH_MARGIN_MULTIPLE, Decimal("2"))
        self.assertEqual(
            CONSERVATIVE_RESEARCH_MARGIN,
            Decimal("0.005006256569147291827961995503880"),
        )
        self.assertFalse(actual.cost_assumption["research_margin_is_mathematical_break_even"])
        self.assertEqual(
            actual.cost_assumption["mathematical_gross_break_even_hurdle"],
            str(ROUND_TRIP_COST_FLOOR),
        )
        for entry in actual.entries:
            with self.subTest(entry=entry.stable_id):
                self.assertLessEqual(len(entry.parameter_sets), 3)
                with af1_decimal_context():
                    minimum_required_effect = 2 * ROUND_TRIP_COST_FLOOR
                self.assertGreaterEqual(entry.minimum_gross_effect, minimum_required_effect)
                self.assertEqual(
                    tuple(parameter.as_dict() for parameter in entry.parameter_sets),
                    tuple(
                        parameter.as_dict()
                        for parameter in reloaded_by_id[entry.stable_id].parameter_sets
                    ),
                )

    def test_declared_parameters_must_appear_in_the_causal_formula(self) -> None:
        entry = catalog().entries[0]
        with self.assertRaisesRegex(
            CatalogError, "causal_formula must reference every declared parameter"
        ):
            replace(entry, causal_formula="event=true")

    def test_mapping_order_does_not_change_catalog_or_parameter_identity(self) -> None:
        payload = (ARTIFACT_ROOT / "catalog.json").read_bytes()
        document = json.loads(payload)
        reordered = reverse_mappings(document)
        loaded = type(catalog()).from_document(reordered)
        self.assertEqual(loaded.catalog_id, document["catalog_id"])
        self.assertEqual(loaded.canonical_bytes(), payload)
        first = ParameterSet("fixed", {"a": 1, "b": "2"}, "Fixed before outcomes.")
        second = ParameterSet("fixed", {"b": "2", "a": 1}, "Fixed before outcomes.")
        self.assertEqual(
            canonical_json_bytes(first.as_dict()), canonical_json_bytes(second.as_dict())
        )

    def test_inputs_are_canonical_and_formulas_are_causal(self) -> None:
        self.assertEqual(
            APPROVED_FEATURE_INPUTS,
            frozenset(f"feature:{name}" for name in FEATURE_NAMES),
        )
        self.assertTrue(
            CANONICAL_CANDLE_INPUTS.issubset({item.name for item in fields(Candle)})
        )
        for entry in catalog().entries:
            with self.subTest(entry=entry.stable_id):
                self.assertTrue(set(entry.causal_inputs).issubset(ALLOWED_INPUTS))
                formula = entry.causal_formula.replace(" ", "").lower()
                self.assertNotIn("t+", formula)
                self.assertNotIn("future", formula)
                self.assertNotIn("taker", formula)
                self.assertNotIn("order_book", formula)
                self.assertNotIn("funding", formula)

    def test_status_lists_are_deterministic_and_disjoint(self) -> None:
        actual = catalog()
        retained = actual.entries_with_status(CatalogStatus.RETAIN_AF3)
        killed = actual.entries_with_status(CatalogStatus.KILL_PRE_SCREEN)
        deferred = actual.entries_with_status(CatalogStatus.DEFER)
        self.assertEqual((len(retained), len(killed), len(deferred)), (15, 11, 10))
        self.assertEqual(actual.af3_entries(), retained)
        sets = tuple({item.stable_id for item in group} for group in (retained, killed, deferred))
        self.assertFalse(sets[0] & sets[1])
        self.assertFalse(sets[0] & sets[2])
        self.assertFalse(sets[1] & sets[2])
        self.assertEqual(set().union(*sets), set(EXPECTED_IDS))
        for status, filename in (
            (CatalogStatus.RETAIN_AF3, "retained_af3.json"),
            (CatalogStatus.KILL_PRE_SCREEN, "killed_pre_screen.json"),
            (CatalogStatus.DEFER, "deferred.json"),
        ):
            with self.subTest(status=status):
                expected = actual.status_document(status)
                stored = json.loads((ARTIFACT_ROOT / filename).read_bytes())
                self.assertEqual(stored, expected)
                identity = dict(stored)
                claimed = identity.pop("list_id")
                self.assertEqual(claimed, sha256(canonical_json_bytes(identity)).hexdigest())

    def test_catalog_and_human_artifacts_are_consistent(self) -> None:
        actual = catalog()
        self.assertEqual(actual.canonical_bytes(), (ARTIFACT_ROOT / "catalog.json").read_bytes())
        self.assertEqual(
            (ARTIFACT_ROOT / "catalog.sha256").read_text(encoding="ascii"),
            actual.catalog_id + "\n",
        )
        ledger = (ARTIFACT_ROOT / "ledger.md").read_text(encoding="utf-8")
        prescreen = (ARTIFACT_ROOT / "math_prescreen.md").read_text(encoding="utf-8")
        self.assertIn(actual.catalog_id, ledger)
        self.assertIn(actual.catalog_id, prescreen)
        self.assertIn("AF1 broad empirical screening has not run", ledger)
        self.assertIn(str(ROUND_TRIP_COST_FLOOR), prescreen)
        for document in (ledger, prescreen):
            self.assertIn("qualitative research judgments", document)
            self.assertIn("not calibrated probabilities", document)
            self.assertIn("decision heuristic, not a statistical estimate", document)
            self.assertIn("No retained hypothesis is currently STRONG", document)
            self.assertIn("AF3 materialization gate", document)
            self.assertIn("exact one-to-one coverage of all 44", document)
            self.assertIn("genuine AF1 `HypothesisMetadata.definition_sha256`", document)
            self.assertIn("616 registered tests", document)
        for stable_id in EXPECTED_IDS:
            self.assertIn(stable_id, ledger)
            self.assertIn(stable_id, prescreen)
        lanes = json.loads((ARTIFACT_ROOT / "deferred_research_lanes.json").read_bytes())
        self.assertEqual(len(lanes["lanes"]), 8)
        self.assertEqual(
            {item["lane"] for item in lanes["lanes"]},
            {
                "btc_eth_lead_lag", "taker_buy_sell_imbalance",
                "order_book_imbalance", "liquidation_and_funding",
                "futures_and_perpetuals", "hyperliquid", "on_chain",
                "sentiment_and_news",
            },
        )

    def test_decimal_context_does_not_change_catalog_or_formula_results(self) -> None:
        baseline = catalog()
        baseline_effects = tuple(entry.minimum_gross_effect for entry in baseline.entries)
        baseline_efficiency = path_efficiency(
            (Decimal("100"), Decimal("102"), Decimal("101"))
        )
        with localcontext() as context:
            context.prec = 6
            context.rounding = ROUND_DOWN
            altered = catalog()
            self.assertEqual(altered.catalog_id, baseline.catalog_id)
            self.assertEqual(
                tuple(entry.minimum_gross_effect for entry in altered.entries),
                baseline_effects,
            )
            self.assertEqual(
                path_efficiency((Decimal("100"), Decimal("102"), Decimal("101"))),
                baseline_efficiency,
            )

    def test_zero_denominators_fail_events_cleanly(self) -> None:
        zero = Decimal(0)
        one = Decimal(1)
        self.assertIsNone(safe_ratio(one, zero))
        self.assertIsNone(simple_return(one, zero))
        self.assertIsNone(close_location(one, one, one))
        self.assertIsNone(wick_fractions(one, one, one, one))
        self.assertIsNone(path_efficiency((one, one, one)))
        self.assertIsNone(average_trade_notional(one, 0))
        self.assertIsNone(relative_activity(one, (zero, zero)))
        self.assertIsNone(activity_concentration(one, (zero, zero), one, (one, one)))
        self.assertIsNone(activity_concentration(one, (one, one), zero, (one, one)))
        self.assertIsNone(quote_volume_per_absolute_return_proxy(one, zero))
        self.assertIsNone(root_mean_square_return((zero, one)))

    def test_formula_helpers_use_exact_decimal_arithmetic(self) -> None:
        self.assertEqual(simple_return(Decimal("102"), Decimal("100")), Decimal("0.02"))
        self.assertEqual(
            close_location(Decimal("102"), Decimal("100"), Decimal("104")),
            Decimal("0.5"),
        )
        self.assertEqual(
            wick_fractions(Decimal("101"), Decimal("104"), Decimal("100"), Decimal("102")),
            (Decimal("0.5"), Decimal("0.25")),
        )
        with af1_decimal_context():
            expected_path_efficiency = Decimal(1) / Decimal(3)
        self.assertEqual(
            path_efficiency((Decimal("100"), Decimal("102"), Decimal("101"))),
            expected_path_efficiency,
        )
        self.assertEqual(average_trade_notional(Decimal("1000"), 4), Decimal("250"))
        self.assertEqual(
            activity_concentration(
                Decimal("30"),
                (Decimal("10"), Decimal("20")),
                Decimal("2"),
                (Decimal("2"), Decimal("2")),
            ),
            Decimal("2"),
        )
        self.assertEqual(
            quote_volume_per_absolute_return_proxy(Decimal("2"), Decimal("0.01")),
            Decimal("2E+2"),
        )
        self.assertEqual(
            root_mean_square_return(
                (Decimal("100"), Decimal("110"), Decimal("121"))
            ),
            Decimal("0.1"),
        )

    def test_c3_tail_direction_is_correct_and_extreme_is_more_selective(self) -> None:
        c3 = next(
            item for item in catalog().entries if item.stable_id.startswith("af2a.c3.")
        )
        tails = {
            setting.label: Decimal(setting.values["close_tail"])
            for setting in c3.parameter_sets
        }
        self.assertEqual(tails, {"breakout": Decimal("0.80"), "extreme_breakout": Decimal("0.90")})
        up = Decimal("0.85")
        down = Decimal("0.15")
        positive = Decimal("0.01")
        negative = Decimal("-0.01")
        self.assertIs(
            c3_tail_direction(up, tails["breakout"], positive),
            ExpectedDirection.CONTINUATION_UP,
        )
        self.assertIsNone(c3_tail_direction(up, tails["extreme_breakout"], positive))
        self.assertIs(
            c3_tail_direction(Decimal("0.95"), tails["extreme_breakout"], positive),
            ExpectedDirection.CONTINUATION_UP,
        )
        self.assertIs(
            c3_tail_direction(down, tails["breakout"], negative),
            ExpectedDirection.CONTINUATION_DOWN,
        )
        self.assertIsNone(c3_tail_direction(down, tails["extreme_breakout"], negative))
        self.assertIs(
            c3_tail_direction(Decimal("0.05"), tails["extreme_breakout"], negative),
            ExpectedDirection.CONTINUATION_DOWN,
        )
        self.assertIn(
            "prior_rv_5=sqrt(mean(simple_return_j^2,j=t-5..t-1))",
            c3.causal_formula,
        )
        self.assertIn("prior_rv_30>0", c3.causal_formula)
        self.assertEqual(root_mean_square_return((Decimal("1"), Decimal("1"))), Decimal(0))

    def test_challenged_prior_kills_use_non_identical_claim_language(self) -> None:
        by_id = {item.stable_id: item for item in catalog().entries}
        challenged = ("af2a.a1.", "af2a.a2.", "af2a.b1.", "af2a.b2.", "af2a.b6.")
        for prefix in challenged:
            entry = next(item for sid, item in by_id.items() if sid.startswith(prefix))
            self.assertIs(entry.status, CatalogStatus.KILL_PRE_SCREEN)
            self.assertEqual(entry.research_viability.value, "LOW")
            self.assertTrue(
                "did not test this exact" in entry.reason
                or "did not test this efficiency-conditioned" in entry.reason
                or "neither directly tested" in entry.reason
            )
        self.assertFalse(
            any(item.research_viability.value == "STRONG" for item in catalog().entries)
        )

    def test_e2_and_e3_have_fixed_signed_reversal_mappings(self) -> None:
        for sid in (
            "af2a.e2.high-quote-volume-weak-return",
            "af2a.e3.high-trade-count-narrow-range",
        ):
            entry = next(item for item in catalog().entries if item.stable_id == sid)
            self.assertIn("prior_pressure_lookback", entry.causal_formula)
            self.assertIn("prior_pressure<=-pressure_min => reversal_up", entry.causal_formula)
            self.assertIn("prior_pressure>=pressure_min => reversal_down", entry.causal_formula)
            self.assertEqual(len(entry.parameter_sets), 2)
            for setting in entry.parameter_sets:
                self.assertEqual(setting.values["prior_pressure_lookback"], 5)
                self.assertIn("pressure_min", setting.values)
        self.assertIs(
            reversal_from_prior_pressure(Decimal("-0.004"), Decimal("0.004")),
            ExpectedDirection.REVERSAL_UP,
        )
        self.assertIs(
            reversal_from_prior_pressure(Decimal("0.004"), Decimal("0.004")),
            ExpectedDirection.REVERSAL_DOWN,
        )
        self.assertIsNone(
            reversal_from_prior_pressure(Decimal("0.0039"), Decimal("0.004"))
        )

    def test_duplicate_and_derived_concepts_do_not_enter_af3_or_fdr(self) -> None:
        actual = catalog()
        by_id = {item.stable_id: item for item in actual.entries}
        self.assertIs(
            by_id["af2a.b5.directional-move-high-participation-continuation"].status,
            CatalogStatus.KILL_PRE_SCREEN,
        )
        self.assertIs(
            by_id["af2a.e6.large-return-high-participation"].status,
            CatalogStatus.RETAIN_AF3,
        )
        f6 = by_id["af2a.f6.upside-downside-shock-asymmetry"]
        self.assertIs(f6.status, CatalogStatus.DEFER)
        self.assertIn("no AF1 definition and no FDR test", f6.reason)
        fdr = derive_fdr_plan(actual)
        fdr_ids = {
            item["concept_stable_id"] for item in fdr["planned_definitions"]
        }
        self.assertNotIn(f6.stable_id, fdr_ids)
        self.assertNotIn("af2a.b5.directional-move-high-participation-continuation", fdr_ids)

    def test_c4_c5_calibration_contract_is_complete_and_bound_to_identity(self) -> None:
        required = {
            "calibration_lookback_minutes", "calibration_mode", "calibration_scope",
            "calibration_update_minutes", "exclude_current_observation",
            "minimum_calibration_observations", "quantile_rule", "rv_estimator",
            "rv_lookback_returns", "rv_state_quantile", "shock",
            "zero_volatility_behavior",
        }
        for sid in (
            "af2a.c4.positive-shock-high-volatility-reversal-down",
            "af2a.c5.negative-shock-high-volatility-reversal-up",
        ):
            entry = next(item for item in catalog().entries if item.stable_id == sid)
            self.assertEqual(len(entry.parameter_sets), 1)
            self.assertEqual(set(entry.parameter_sets[0].values), required)
            self.assertEqual(entry.parameter_sets[0].values["calibration_scope"], "per_symbol")
            self.assertEqual(
                entry.parameter_sets[0].values["calibration_mode"], "rolling_prior_only"
            )
            self.assertTrue(entry.parameter_sets[0].values["exclude_current_observation"])
            self.assertEqual(entry.parameter_sets[0].values["quantile_rule"], "nearest_rank_ceil")
        values = tuple(Decimal(value) for value in ("1", "2", "3", "4", "5"))
        self.assertEqual(nearest_rank_quantile(values, Decimal("0.80"), 5), Decimal("4"))
        self.assertIsNone(nearest_rank_quantile(values, Decimal("0.80"), 6))

    def test_d5_d6_a6_and_e4_contracts_are_exact(self) -> None:
        by_id = {item.stable_id: item for item in catalog().entries}
        d5 = by_id["af2a.d5.volume-per-range-activity-concentration"]
        self.assertIn("normalized_volume/normalized_range", d5.causal_formula)
        self.assertIn("volume_normalization_window", d5.causal_formula)
        self.assertIn("range_normalization_window", d5.causal_formula)
        d6 = by_id["af2a.d6.quote-volume-per-absolute-return-proxy"]
        self.assertEqual(d6.causal_formula.count("quote_volume_per_absolute_return="), 1)
        self.assertIn("normalized_quote_volume/abs(r_1)", d6.causal_formula)
        self.assertNotIn("abs(r_1)/normalized_quote_volume", d6.causal_formula)
        a6 = by_id["af2a.a6.overextension-deteriorating-efficiency-reversal"]
        self.assertEqual(len(a6.parameter_sets), 1)
        self.assertEqual(a6.parameter_sets[0].values["extension_lookback_returns"], 15)
        self.assertEqual(a6.parameter_sets[0].values["short_efficiency_returns"], 5)
        self.assertEqual(a6.parameter_sets[0].values["prior_efficiency_returns"], 10)
        self.assertFalse(a6.parameter_sets[0].values["windows_overlap"])
        self.assertIn("shared_endpoint_disjoint_return_intervals", a6.causal_formula)
        e4 = by_id["af2a.e4.high-activity-declining-efficiency"]
        self.assertEqual(e4.causal_inputs, ("close", "quote_volume"))

    def test_exact_expectancy_and_cost_terminology(self) -> None:
        p_win = Decimal("0.6")
        average_win = Decimal("0.01")
        average_loss = Decimal("0.005")
        gross = gross_expectancy(p_win, average_win, average_loss)
        self.assertEqual(gross, Decimal("0.0040"))
        with af1_decimal_context():
            expected_net = (gross - ROUND_TRIP_COST_FLOOR) / (
                Decimal(1) + ROUND_TRIP_COST_FLOOR
            )
            expected_break_even = (average_loss + ROUND_TRIP_COST_FLOOR) / (
                average_win + average_loss
            )
        self.assertEqual(multiplicative_net_expectancy(gross), expected_net)
        self.assertEqual(
            break_even_win_rate(average_win, average_loss), expected_break_even
        )
        prescreen = (ARTIFACT_ROOT / "math_prescreen.md").read_text(encoding="utf-8")
        self.assertIn("mathematical gross break-even hurdle", prescreen)
        self.assertIn("deliberately conservative AF2A research margin", prescreen)
        self.assertIn("E_net = (E_gross - C) / (1 + C)", prescreen)

    def test_exact_closed_fdr_multiplicity_and_parameter_table(self) -> None:
        actual = catalog()
        fdr = derive_fdr_plan(actual)
        self.assertEqual(fdr["schema_version"], AF2A_FDR_PLAN_SCHEMA_VERSION)
        self.assertEqual(tuple(fdr["symbols"]), AF1_SYMBOLS)
        self.assertEqual(tuple(fdr["horizons_minutes"]), AF1_HORIZONS_MINUTES)
        self.assertEqual(fdr["retained_concept_count"], 15)
        self.assertEqual(fdr["planned_definition_count"], 44)
        self.assertEqual(fdr["planned_registered_test_count"], 616)
        self.assertEqual(len(fdr["planned_definitions"]), 44)
        self.assertEqual(len(fdr["planned_registered_tests"]), 616)
        self.assertEqual(
            fdr["fdr_plan_id"],
            "32a6a1e1e916a0b012bd5ac22d747b3b57afa53a99e5153d0fb1a0685511a6ae",
        )
        verified = load_and_verify_fdr_plan(
            (ARTIFACT_ROOT / "catalog.json").read_bytes(),
            (ARTIFACT_ROOT / "fdr_plan.json").read_bytes(),
        )
        self.assertEqual(dict(verified), fdr)
        self.assertEqual(
            {
                (
                    item["planned_definition_sha256"],
                    item["symbol"],
                    item["horizon_minutes"],
                )
                for item in fdr["planned_registered_tests"]
            },
            {
                (definition["planned_definition_sha256"], symbol, horizon)
                for definition in fdr["planned_definitions"]
                for symbol in AF1_SYMBOLS
                for horizon in AF1_HORIZONS_MINUTES
            },
        )

    def test_fdr_plan_rejects_every_self_resealed_semantic_drift(self) -> None:
        catalog_payload = (ARTIFACT_ROOT / "catalog.json").read_bytes()
        valid = json.loads((ARTIFACT_ROOT / "fdr_plan.json").read_bytes())

        def reseal_plan(document):
            identity = dict(document)
            identity.pop("fdr_plan_id", None)
            document["fdr_plan_id"] = sha256(
                canonical_json_bytes(identity)
            ).hexdigest()
            return canonical_json_bytes(document)

        def mutate_definition(document, predicate, mutate):
            row = next(item for item in document["planned_definitions"] if predicate(item))
            old_digest = row["planned_definition_sha256"]
            mutate(row)
            identity = dict(row)
            identity.pop("planned_definition_sha256")
            row["planned_definition_sha256"] = sha256(
                canonical_json_bytes(identity)
            ).hexdigest()
            for test in document["planned_registered_tests"]:
                if test["planned_definition_sha256"] == old_digest:
                    test["planned_definition_sha256"] = row[
                        "planned_definition_sha256"
                    ]

        attacks = {}
        direction = json.loads(json.dumps(valid))
        mutate_definition(
            direction,
            lambda row: row["concept_stable_id"].startswith("af2a.a3."),
            lambda row: row.__setitem__("direction", "reversal_down"),
        )
        attacks["A3 direction"] = direction

        threshold = json.loads(json.dumps(valid))
        mutate_definition(
            threshold,
            lambda row: row["concept_stable_id"].startswith("af2a.a3."),
            lambda row: row["parameters"].__setitem__("wick_min", "0.51"),
        )
        attacks["threshold"] = threshold

        label = json.loads(json.dumps(valid))
        mutate_definition(
            label,
            lambda row: row["concept_stable_id"].startswith("af2a.a3."),
            lambda row: row.__setitem__("parameter_set_label", "renamed"),
        )
        attacks["parameter-set name"] = label

        lookback = json.loads(json.dumps(valid))
        mutate_definition(
            lookback,
            lambda row: row["concept_stable_id"].startswith("af2a.a3."),
            lambda row: row.__setitem__("causal_lookback", 7),
        )
        attacks["causal lookback"] = lookback

        required_input = json.loads(json.dumps(valid))
        mutate_definition(
            required_input,
            lambda row: row["concept_stable_id"].startswith("af2a.a3."),
            lambda row: row["required_inputs"].append("quote_volume"),
        )
        attacks["required input"] = required_input

        removed = json.loads(json.dumps(valid))
        removed_row = removed["planned_definitions"].pop()
        removed["planned_registered_tests"] = [
            item for item in removed["planned_registered_tests"]
            if item["planned_definition_sha256"]
            != removed_row["planned_definition_sha256"]
        ]
        removed["planned_definition_count"] -= 1
        removed["planned_registered_test_count"] -= 14
        attacks["remove definition"] = removed

        added = json.loads(json.dumps(valid))
        extra = json.loads(json.dumps(added["planned_definitions"][0]))
        extra["parameter_set_label"] = "forged_extra"
        extra_identity = dict(extra)
        extra_identity.pop("planned_definition_sha256")
        extra["planned_definition_sha256"] = sha256(
            canonical_json_bytes(extra_identity)
        ).hexdigest()
        added["planned_definitions"].append(extra)
        added["planned_definition_count"] += 1
        for symbol in AF1_SYMBOLS:
            for horizon in AF1_HORIZONS_MINUTES:
                added["planned_registered_tests"].append({
                    "planned_definition_sha256": extra["planned_definition_sha256"],
                    "symbol": symbol,
                    "horizon_minutes": horizon,
                })
        added["planned_registered_test_count"] += 14
        attacks["add definition"] = added

        for name, forged_id in (
            ("killed B5", "af2a.b5.directional-move-high-participation-continuation"),
            ("deferred F6", "af2a.f6.upside-downside-shock-asymmetry"),
        ):
            forged = json.loads(json.dumps(valid))
            mutate_definition(
                forged,
                lambda row: row["concept_stable_id"].startswith("af2a.a3."),
                lambda row, value=forged_id: row.__setitem__(
                    "concept_stable_id", value
                ),
            )
            attacks[name] = forged

        calibration = json.loads(json.dumps(valid))
        mutate_definition(
            calibration,
            lambda row: row["concept_stable_id"].startswith("af2a.c4."),
            lambda row: row["parameters"].__setitem__(
                "calibration_lookback_minutes", 43199
            ),
        )
        attacks["C4 calibration window"] = calibration

        symbol = json.loads(json.dumps(valid))
        symbol["symbols"][0] = "BNBUSDT"
        for test in symbol["planned_registered_tests"]:
            if test["symbol"] == "BTCUSDT":
                test["symbol"] = "BNBUSDT"
        attacks["symbol"] = symbol

        horizon = json.loads(json.dumps(valid))
        horizon["horizons_minutes"][0] = 2
        for test in horizon["planned_registered_tests"]:
            if test["horizon_minutes"] == 1:
                test["horizon_minutes"] = 2
        attacks["horizon"] = horizon

        for name, forged in attacks.items():
            with self.subTest(attack=name):
                with self.assertRaisesRegex(
                    CatalogError, "differs from the canonical catalog"
                ):
                    load_and_verify_fdr_plan(
                        catalog_payload, reseal_plan(forged)
                    )

    def test_fdr_plan_mapping_order_is_semantically_invariant(self) -> None:
        catalog_payload = (ARTIFACT_ROOT / "catalog.json").read_bytes()
        valid = json.loads((ARTIFACT_ROOT / "fdr_plan.json").read_bytes())
        reordered_payload = (
            json.dumps(
                reverse_mappings(valid),
                sort_keys=False,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            + "\n"
        ).encode("utf-8")
        verified = load_and_verify_fdr_plan(catalog_payload, reordered_payload)
        self.assertEqual(dict(verified), derive_fdr_plan(catalog()))

    def test_semantic_parameter_change_changes_catalog_identity(self) -> None:
        document = json.loads((ARTIFACT_ROOT / "catalog.json").read_bytes())
        original_id = document["catalog_id"]
        target = next(
            item for item in document["hypotheses"]
            if item["stable_id"] == "af2a.a3.lower-wick-after-negative-move-reversal-up"
        )
        target["parameter_sets"][0]["values"]["wick_min"] = "0.51"
        identity = dict(document)
        identity.pop("catalog_id")
        document["catalog_id"] = sha256(canonical_json_bytes(identity)).hexdigest()
        changed = type(catalog()).from_document(document)
        self.assertNotEqual(changed.catalog_id, original_id)
        original_plan = derive_fdr_plan(catalog())
        changed_plan = derive_fdr_plan(changed)
        original_definition = next(
            item for item in original_plan["planned_definitions"]
            if item["concept_stable_id"].startswith("af2a.a3.")
            and item["parameter_set_label"] == "clear_rejection"
        )
        changed_definition = next(
            item for item in changed_plan["planned_definitions"]
            if item["concept_stable_id"].startswith("af2a.a3.")
            and item["parameter_set_label"] == "clear_rejection"
        )
        self.assertNotEqual(
            original_definition["planned_definition_sha256"],
            changed_definition["planned_definition_sha256"],
        )
        self.assertNotEqual(original_plan["fdr_plan_id"], changed_plan["fdr_plan_id"])
        with self.assertRaisesRegex(
            CatalogError, "differs from the canonical catalog"
        ):
            load_and_verify_fdr_plan(
                canonical_json_bytes(document),
                (ARTIFACT_ROOT / "fdr_plan.json").read_bytes(),
            )


if __name__ == "__main__":
    unittest.main()

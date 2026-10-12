"""AF3A executable hypothesis materialization and callback sentinel tests."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, localcontext
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from quantos.domain.evaluation.alpha_catalog import (
    AF1_HORIZONS_MINUTES,
    AF1_SYMBOLS,
    derive_fdr_plan,
    load_and_verify_fdr_plan,
    load_catalog_bytes,
    nearest_rank_quantile,
    root_mean_square_return,
    simple_return,
)
from quantos.domain.evaluation.alpha_funnel import af1_decimal_context, canonical_json_bytes
from quantos.domain.evaluation.alpha_hypotheses import (
    AF3A_MATERIALIZER_VERSION,
    EXPECTED_CONCEPT_IDS,
    FROZEN_CATALOG_ID,
    FROZEN_FDR_PLAN_ID,
    MaterializationError,
    _rms_from_squared_sum,
    _rolling_squared_sum_state,
    _rolling_squared_sums,
    derive_materialization_manifest,
    load_and_verify_materialization_manifest,
    materialize_hypotheses,
    verify_materialization,
)
from quantos.domain.market_data.contracts import Candle


ROOT = Path(__file__).resolve().parents[2]
AF2A_ROOT = ROOT / "research" / "alpha-funnel" / "af2a"
AF3A_ROOT = ROOT / "research" / "alpha-funnel" / "af3a"
START = datetime(2025, 1, 1, tzinfo=timezone.utc)


def candles(
    count: int,
    *,
    closes: list[Decimal] | None = None,
    opens: list[Decimal] | None = None,
    highs: list[Decimal] | None = None,
    lows: list[Decimal] | None = None,
    quote_volumes: list[Decimal] | None = None,
    trade_counts: list[int] | None = None,
) -> tuple[Candle, ...]:
    closes = closes or [Decimal("100")] * count
    opens = opens or list(closes)
    highs = highs or [max(open_price, close) + Decimal(1) for open_price, close in zip(opens, closes)]
    lows = lows or [max(Decimal(0), min(open_price, close) - Decimal(1)) for open_price, close in zip(opens, closes)]
    quote_volumes = quote_volumes or [Decimal("10")] * count
    trade_counts = trade_counts or [10] * count
    assert all(len(values) == count for values in (closes, opens, highs, lows, quote_volumes, trade_counts))
    return tuple(
        Candle(
            symbol="BTCUSDT",
            interval="1m",
            open_time=START + timedelta(minutes=index),
            close_time=START + timedelta(minutes=index, seconds=59),
            open=opens[index],
            high=highs[index],
            low=lows[index],
            close=closes[index],
            volume=Decimal("1"),
            quote_volume=quote_volumes[index],
            trade_count=trade_counts[index],
        )
        for index in range(count)
    )


def reseal(document: dict[str, object]) -> bytes:
    identity = dict(document)
    identity.pop("materialization_manifest_id", None)
    document["materialization_manifest_id"] = sha256(canonical_json_bytes(identity)).hexdigest()
    return canonical_json_bytes(document)


def reference_high_volatility(
    window: tuple[Candle, ...], parameters, direction: str,
) -> tuple[bool, Decimal | None]:
    """Deliberately slow frozen C4/C5 oracle used only by tests."""
    closes = tuple(candle.close for candle in window)
    prior = tuple(
        root_mean_square_return(closes[end - 20:end + 1])
        for end in range(20, len(closes) - 1)
    )
    current = root_mean_square_return(closes[-21:])
    if current is None or any(value is None for value in prior):
        return False, None
    threshold = nearest_rank_quantile(
        prior, Decimal(parameters["rv_state_quantile"]),
        parameters["minimum_calibration_observations"],
    )
    shock = simple_return(closes[-1], closes[-6])
    shock_min = Decimal(parameters["shock"])
    if threshold is None or threshold == 0 or shock is None:
        return False, threshold
    accepted_shock = (
        shock >= shock_min if direction == "DOWN" else shock <= -shock_min
    )
    return accepted_shock and current >= threshold, threshold


BEHAVIOR_STABLE_IDS = (
    "af3.a3.clear-rejection.up",
    "af3.a3.extreme-rejection.up",
    "af3.a4.clear-rejection.down",
    "af3.a4.extreme-rejection.down",
    "af3.a5.large.up",
    "af3.a5.large.down",
    "af3.a5.extreme.up",
    "af3.a5.extreme.down",
    "af3.a6.clear-decay.up",
    "af3.a6.clear-decay.down",
    "af3.b3.clear.up",
    "af3.b3.extreme.up",
    "af3.b4.clear.down",
    "af3.b4.extreme.down",
    "af3.c3.breakout.up",
    "af3.c3.breakout.down",
    "af3.c3.extreme-breakout.up",
    "af3.c3.extreme-breakout.down",
    "af3.c4.high-vol.down",
    "af3.c5.high-vol.up",
    "af3.e2.disagreement.up",
    "af3.e2.disagreement.down",
    "af3.e2.extreme.up",
    "af3.e2.extreme.down",
    "af3.e3.concentrated.up",
    "af3.e3.concentrated.down",
    "af3.e3.extreme.up",
    "af3.e3.extreme.down",
    "af3.e4.decay.up",
    "af3.e4.decay.down",
    "af3.e4.extreme-decay.up",
    "af3.e4.extreme-decay.down",
    "af3.e5.fragile.up",
    "af3.e5.fragile.down",
    "af3.e5.extreme-fragile.up",
    "af3.e5.extreme-fragile.down",
    "af3.e6.confirmed.up",
    "af3.e6.confirmed.down",
    "af3.e6.extreme-confirmed.up",
    "af3.e6.extreme-confirmed.down",
    "af3.f1.pullback.up",
    "af3.f1.pullback.down",
    "af3.f1.deep-pullback.up",
    "af3.f1.deep-pullback.down",
)


class AlphaHypothesisMaterializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog_payload = (AF2A_ROOT / "catalog.json").read_bytes()
        cls.plan_payload = (AF2A_ROOT / "fdr_plan.json").read_bytes()
        cls.catalog = load_catalog_bytes(cls.catalog_payload)
        cls.plan = load_and_verify_fdr_plan(cls.catalog_payload, cls.plan_payload)
        cls.hypotheses = materialize_hypotheses(cls.catalog, cls.plan)
        cls.by_stable_id = {item.metadata.stable_id: item for item in cls.hypotheses}
        cls.manifest = derive_materialization_manifest(cls.catalog, cls.plan, cls.hypotheses)

    def hypothesis(self, stable_id: str):
        return self.by_stable_id[stable_id]

    @staticmethod
    def _decimal_parameter(parameters, name: str) -> Decimal:
        return Decimal(parameters[name])

    def positive_behavior_window(self, stable_id: str) -> tuple[Candle, ...]:
        hypothesis = self.hypothesis(stable_id)
        parameters = hypothesis.metadata.parameters
        code = stable_id.split(".")[1]
        direction = hypothesis.metadata.direction.value
        sign = Decimal(1) if direction == "DOWN" else Decimal(-1)

        if code == "a3":
            movement = self._decimal_parameter(parameters, "return_max")
            close = Decimal("100") * (Decimal(1) + movement)
            width = Decimal("10")
            low = close - self._decimal_parameter(parameters, "close_min") * width
            high = low + width
            open_price = low + self._decimal_parameter(parameters, "wick_min") * width
            return candles(
                6,
                closes=[Decimal("100")] * 5 + [close],
                opens=[Decimal("100")] * 5 + [open_price],
                highs=[Decimal("101")] * 5 + [high],
                lows=[Decimal("99")] * 5 + [low],
            )

        if code == "a4":
            movement = self._decimal_parameter(parameters, "return_min")
            close = Decimal("100") * (Decimal(1) + movement)
            width = Decimal("10")
            low = close - self._decimal_parameter(parameters, "close_max") * width
            high = low + width
            open_price = high - self._decimal_parameter(parameters, "wick_min") * width
            return candles(
                6,
                closes=[Decimal("100")] * 5 + [close],
                opens=[Decimal("100")] * 5 + [open_price],
                highs=[Decimal("101")] * 5 + [high],
                lows=[Decimal("99")] * 5 + [low],
            )

        if code == "a5":
            width = Decimal("100") * self._decimal_parameter(parameters, "range_pct_min")
            tail = self._decimal_parameter(parameters, "failure_tail")
            low = Decimal("100")
            high = low + width
            if direction == "UP":
                close = low + tail * width
                open_price = high
            else:
                close = low + (Decimal(1) - tail) * width
                open_price = low
            return candles(
                2,
                closes=[Decimal("100"), close],
                opens=[Decimal("100"), open_price],
                highs=[Decimal("101"), high],
                lows=[Decimal("99"), low],
            )

        if code == "a6":
            extension = Decimal("100") * self._decimal_parameter(parameters, "extension_min")
            final = Decimal("100") + sign * extension
            boundary = Decimal("100") + sign * Decimal("0.50")
            prior = [
                Decimal("100") + (boundary - Decimal("100"))
                * Decimal(index) / Decimal(10)
                for index in range(11)
            ]
            recent = [
                boundary + sign * Decimal("0.10"),
                boundary + sign * Decimal("0.06875"),
                boundary + sign * Decimal("0.15"),
                boundary + sign * Decimal("0.20"),
                final,
            ]
            return candles(16, closes=prior + recent)

        if code in {"b3", "b4"}:
            width = self._decimal_parameter(parameters, "range_vs_20m_mean")
            low = Decimal("100")
            high = low + width
            if code == "b3":
                location = self._decimal_parameter(parameters, "close_min")
                open_price = low
            else:
                location = self._decimal_parameter(parameters, "close_max")
                open_price = high
            close = low + location * width
            return candles(
                21,
                closes=[Decimal("100")] * 20 + [close],
                opens=[Decimal("100")] * 20 + [open_price],
                highs=[Decimal("100.5")] * 20 + [high],
                lows=[Decimal("99.5")] * 20 + [low],
            )

        if code == "c3":
            compression = self._decimal_parameter(parameters, "compression_max")
            unit = Decimal("0.0001")
            if compression == Decimal("0.60"):
                early_returns = [
                    Decimal("4.8") * unit,
                    Decimal("2.2") * unit,
                    Decimal("0.4") * unit,
                    Decimal("0.4") * unit,
                ] + [Decimal(0)] * 21
            else:
                early_returns = [
                    Decimal("5.4") * unit,
                    Decimal("0.2") * unit,
                ] + [Decimal(0)] * 23
            prior = [Decimal("100")]
            with af1_decimal_context():
                for rate in early_returns + [compression * unit] * 5:
                    prior.append(prior[-1] * (Decimal(1) + rate))
            width = self._decimal_parameter(parameters, "range_expansion_min")
            tail = self._decimal_parameter(parameters, "close_tail")
            low = Decimal("99")
            high = low + width
            if direction == "UP":
                close = low + tail * width
                open_price = low
            else:
                close = low + (Decimal(1) - tail) * width
                open_price = high
            return candles(
                32,
                closes=prior + [close],
                opens=prior + [open_price],
                highs=[Decimal("101")] * len(prior) + [high],
                lows=[Decimal("100")] * len(prior) + [low],
            )

        if code in {"c4", "c5"}:
            values = [
                Decimal("100.01") if index % 2 else Decimal("100")
                for index in range(43221)
            ]
            values[-6:-1] = [Decimal("100")] * 5
            shock = self._decimal_parameter(parameters, "shock")
            values[-1] = Decimal("100") * (
                Decimal(1) + (shock if code == "c4" else -shock)
            )
            return candles(43221, closes=values)

        if code in {"e2", "e3"}:
            pressure = self._decimal_parameter(parameters, "pressure_min")
            pressure_end = Decimal("100") * (Decimal(1) + sign * pressure)
            closes = [Decimal("100")] * 21
            closes[15:20] = [pressure_end] * 5
            closes[20] = pressure_end
            if code == "e2":
                return_max = self._decimal_parameter(parameters, "return_max")
                closes[20] = pressure_end * (Decimal(1) + return_max)
                activity = self._decimal_parameter(parameters, "activity_min")
                return candles(
                    21,
                    closes=closes,
                    quote_volumes=[Decimal("10")] * 20 + [Decimal("10") * activity],
                )
            width = pressure_end * self._decimal_parameter(parameters, "range_max")
            count = int(self._decimal_parameter(parameters, "count_min") * Decimal(10))
            return candles(
                21,
                closes=closes,
                highs=[Decimal("101")] * 20 + [pressure_end + width / 2],
                lows=[Decimal("99")] * 20 + [pressure_end - width / 2],
                trade_counts=[10] * 20 + [count],
            )

        if code == "e4":
            movement = Decimal("100") * self._decimal_parameter(parameters, "move_min")
            drop = self._decimal_parameter(parameters, "efficiency_drop")
            final = Decimal("100") + sign * movement
            trend = [
                Decimal("100") + sign * movement * Decimal(index) / Decimal(5)
                for index in range(6)
            ]
            with af1_decimal_context():
                recent_path = movement * (Decimal(1) - drop) / drop
            recent = [
                final + sign * recent_path / 2,
                final,
                final,
                final,
                final,
            ]
            activity = self._decimal_parameter(parameters, "activity_min")
            return candles(
                21,
                closes=[Decimal("100")] * 10 + trend + recent,
                quote_volumes=[Decimal("10")] * 20 + [Decimal("10") * activity],
            )

        if code in {"e5", "e6"}:
            movement = self._decimal_parameter(parameters, "return_min")
            if code == "e5":
                movement_sign = Decimal(-1) if direction == "UP" else Decimal(1)
                quote_ratio = self._decimal_parameter(parameters, "quote_volume_ratio_max")
                count_ratio = self._decimal_parameter(parameters, "trade_count_ratio_max")
            else:
                movement_sign = Decimal(1) if direction == "UP" else Decimal(-1)
                quote_ratio = self._decimal_parameter(parameters, "quote_volume_ratio_min")
                count_ratio = self._decimal_parameter(parameters, "trade_count_ratio_min")
            closes = [Decimal("100")] * 20 + [
                Decimal("100") * (Decimal(1) + movement_sign * movement)
            ]
            return candles(
                21,
                closes=closes,
                quote_volumes=[Decimal("100")] * 20 + [Decimal("100") * quote_ratio],
                trade_counts=[100] * 20 + [int(Decimal("100") * count_ratio)],
            )

        if code == "f1":
            state = self._decimal_parameter(parameters, "state_min")
            pullback = self._decimal_parameter(parameters, "pullback_min")
            state_sign = Decimal(1) if direction == "UP" else Decimal(-1)
            state_factor = Decimal(1) + state_sign * state
            return_factor = Decimal(1) - state_sign * pullback
            state_numerator, state_denominator = state_factor.as_integer_ratio()
            return_numerator, return_denominator = return_factor.as_integer_ratio()
            final = Decimal(state_numerator * return_numerator)
            state_base = Decimal(state_denominator * return_numerator)
            previous = Decimal(return_denominator * state_numerator)
            closes = [state_base] * 21
            closes[-2] = previous
            closes[-1] = final
            return candles(21, closes=closes)

        raise AssertionError(f"missing positive behavior builder for {stable_id}")

    def behavior_cases(
        self,
    ) -> dict[str, tuple[tuple[Candle, ...], tuple[Candle, ...]]]:
        positive = {
            stable_id: self.positive_behavior_window(stable_id)
            for stable_id in BEHAVIOR_STABLE_IDS
        }
        paired = {"a5", "a6", "c3", "e2", "e3", "e4", "e5", "e6", "f1"}
        result = {}
        for stable_id, positive_window in positive.items():
            code = stable_id.split(".")[1]
            if code in paired:
                opposite_suffix = ".down" if stable_id.endswith(".up") else ".up"
                opposite_id = stable_id.rsplit(".", 1)[0] + opposite_suffix
                negative_window = positive[opposite_id]
            else:
                negative_window = candles(
                    self.hypothesis(stable_id).metadata.causal_lookback
                )
            result[stable_id] = positive_window, negative_window
        return result

    def test_exact_materialization_and_binding_contract(self) -> None:
        self.assertEqual(self.catalog.catalog_id, FROZEN_CATALOG_ID)
        self.assertEqual(self.plan["fdr_plan_id"], FROZEN_FDR_PLAN_ID)
        self.assertEqual(tuple(entry.stable_id for entry in self.catalog.af3_entries()), EXPECTED_CONCEPT_IDS)
        self.assertEqual(len(self.hypotheses), 44)
        self.assertEqual(len({item.metadata.stable_id for item in self.hypotheses}), 44)
        self.assertEqual(len({item.metadata.definition_sha256 for item in self.hypotheses}), 44)
        self.assertEqual(len(self.plan["planned_registered_tests"]), 44 * len(AF1_SYMBOLS) * len(AF1_HORIZONS_MINUTES))
        self.assertEqual(
            Counter(item.metadata.stable_id.split(".")[1] for item in self.hypotheses),
            {
                "a3": 2, "a4": 2, "a5": 4, "a6": 2, "b3": 2, "b4": 2,
                "c3": 4, "c4": 1, "c5": 1, "e2": 4, "e3": 4, "e4": 4,
                "e5": 4, "e6": 4, "f1": 4,
            },
        )
        for row, hypothesis in zip(self.plan["planned_definitions"], self.hypotheses, strict=True):
            parameters = hypothesis.metadata.parameters
            self.assertEqual(parameters["af2a_planned_definition_sha256"], row["planned_definition_sha256"])
            self.assertEqual(parameters["af2a_catalog_entry_sha256"], row["catalog_entry_sha256"])
            self.assertEqual(parameters["af2a_catalog_id"], FROZEN_CATALOG_ID)
            self.assertEqual(parameters["af2a_fdr_plan_id"], FROZEN_FDR_PLAN_ID)
            self.assertEqual(hypothesis.metadata.required_inputs, tuple(row["required_inputs"]))
            self.assertEqual(hypothesis.metadata.causal_lookback, row["causal_lookback"])
            self.assertNotEqual(hypothesis.metadata.definition_sha256, row["planned_definition_sha256"])
        verify_materialization(self.catalog, self.plan, self.hypotheses)

    def test_every_definition_executes_only_at_its_exact_lookback(self) -> None:
        for hypothesis in self.hypotheses:
            lookback = hypothesis.metadata.causal_lookback
            neutral = candles(lookback)
            with self.subTest(hypothesis=hypothesis.metadata.stable_id):
                self.assertIs(type(hypothesis.evaluator(neutral)), bool)
                self.assertFalse(hypothesis.evaluator(neutral[:-1]))

    def test_manifest_is_derived_canonical_and_verified(self) -> None:
        payload = (AF3A_ROOT / "materialization.json").read_bytes()
        self.assertEqual(payload, canonical_json_bytes(self.manifest))
        verified = load_and_verify_materialization_manifest(
            self.catalog_payload, self.plan_payload, payload
        )
        self.assertEqual(dict(verified), self.manifest)
        self.assertEqual(self.manifest["materializer_version"], AF3A_MATERIALIZER_VERSION)
        self.assertEqual(self.manifest["planned_definition_count"], 44)
        self.assertEqual(self.manifest["materialized_definition_count"], 44)
        self.assertEqual(self.manifest["planned_registered_test_count"], 616)

    def test_manifest_rejects_all_self_resealed_drift_attacks(self) -> None:
        valid = json.loads(canonical_json_bytes(self.manifest))
        attacks: list[dict[str, object]] = []

        def changed(mutate):
            document = json.loads(json.dumps(valid))
            mutate(document)
            attacks.append(document)

        changed(lambda d: d["definitions"][0].__setitem__("planned_definition_sha256", "0" * 64))
        changed(lambda d: d["definitions"][0].__setitem__("af1_stable_id", "af3.forged.id"))
        changed(lambda d: d["definitions"][0].__setitem__("af1_implementation_id", "forged-v1"))
        changed(lambda d: d["definitions"][0].__setitem__("af1_definition_sha256", "1" * 64))
        changed(lambda d: d["definitions"][0].__setitem__("af1_direction", "DOWN"))
        changed(lambda d: d["definitions"][0]["parameters"].__setitem__("wick_min", "0.51"))
        changed(lambda d: d["definitions"][0].__setitem__("causal_lookback", 7))
        changed(lambda d: d["definitions"][0]["required_inputs"].append("quote_volume"))
        changed(lambda d: d["definitions"][0].__setitem__("concept_stable_id", EXPECTED_CONCEPT_IDS[1]))
        changed(lambda d: d["definitions"][0].__setitem__("parameter_set_label", "forged"))
        changed(lambda d: d["definitions"].pop())
        changed(lambda d: d["definitions"].append(json.loads(json.dumps(d["definitions"][0]))))

        for index, attack in enumerate(attacks):
            with self.subTest(attack=index), self.assertRaisesRegex(
                MaterializationError, "differs from frozen AF3A semantics"
            ):
                load_and_verify_materialization_manifest(
                    self.catalog_payload, self.plan_payload, reseal(attack)
                )

    def test_manifest_mapping_key_order_is_semantically_invariant(self) -> None:
        def reverse(value):
            if isinstance(value, dict):
                return {key: reverse(value[key]) for key in reversed(tuple(value))}
            if isinstance(value, list):
                return [reverse(item) for item in value]
            return value

        payload = (json.dumps(reverse(self.manifest), separators=(",", ":")) + "\n").encode()
        verified = load_and_verify_materialization_manifest(
            self.catalog_payload, self.plan_payload, payload
        )
        self.assertEqual(dict(verified), self.manifest)

    def test_a3_a4_boundary_direction_and_zero_range(self) -> None:
        a3_closes = [Decimal("100")] * 5 + [Decimal("99.6")]
        a3 = candles(
            6, closes=a3_closes, opens=[Decimal("100")] * 6,
            highs=[Decimal("101")] * 5 + [Decimal("100")],
            lows=[Decimal("99")] * 6,
        )
        self.assertTrue(self.hypothesis("af3.a3.clear-rejection.up").evaluator(a3))
        self.assertFalse(self.hypothesis("af3.a3.extreme-rejection.up").evaluator(a3))

        a4 = candles(
            6, closes=[Decimal("100")] * 5 + [Decimal("100.4")],
            opens=[Decimal("100")] * 6,
            highs=[Decimal("101")] * 6,
            lows=[Decimal("99")] * 5 + [Decimal("100")],
        )
        self.assertTrue(self.hypothesis("af3.a4.clear-rejection.down").evaluator(a4))
        self.assertFalse(self.hypothesis("af3.a4.extreme-rejection.down").evaluator(a4))
        zero = candles(6, closes=[Decimal(0)] * 6, opens=[Decimal(0)] * 6, highs=[Decimal(0)] * 6, lows=[Decimal(0)] * 6)
        self.assertFalse(self.hypothesis("af3.a3.clear-rejection.up").evaluator(zero))
        self.assertFalse(self.hypothesis("af3.a4.clear-rejection.down").evaluator(zero))

    def test_a5_paired_sides_threshold_and_zero_denominator(self) -> None:
        down_event = candles(
            2, closes=[Decimal("100"), Decimal("100.06")],
            opens=[Decimal("100"), Decimal("99.8")],
            highs=[Decimal("101"), Decimal("100.3")],
            lows=[Decimal("99"), Decimal("99.7")],
        )
        self.assertTrue(self.hypothesis("af3.a5.large.down").evaluator(down_event))
        self.assertFalse(self.hypothesis("af3.a5.large.up").evaluator(down_event))
        zero = candles(2, closes=[Decimal(0)] * 2, opens=[Decimal(0)] * 2, highs=[Decimal(0)] * 2, lows=[Decimal(0)] * 2)
        self.assertFalse(self.hypothesis("af3.a5.large.down").evaluator(zero))

    def test_a6_disjoint_efficiency_windows_and_direction(self) -> None:
        closes = [Decimal("100") + Decimal(index) / Decimal(10) for index in range(11)]
        closes += [Decimal("100.8"), Decimal("101"), Decimal("100.8"), Decimal("101"), Decimal("100.75")]
        event = candles(16, closes=closes)
        self.assertTrue(self.hypothesis("af3.a6.clear-decay.down").evaluator(event))
        self.assertFalse(self.hypothesis("af3.a6.clear-decay.up").evaluator(event))
        self.assertFalse(self.hypothesis("af3.a6.clear-decay.down").evaluator(candles(16)))

    def test_b3_b4_prior_only_range_baselines_and_boundaries(self) -> None:
        common_closes = [Decimal("100")] * 20
        b3 = candles(
            21, closes=common_closes + [Decimal("101.4")],
            opens=[Decimal("100")] * 21,
            highs=[Decimal("100.5")] * 20 + [Decimal("101.75")],
            lows=[Decimal("99.5")] * 20 + [Decimal("100")],
        )
        self.assertTrue(self.hypothesis("af3.b3.clear.up").evaluator(b3))
        self.assertFalse(self.hypothesis("af3.b3.extreme.up").evaluator(b3))
        b4 = candles(
            21, closes=common_closes + [Decimal("100.35")],
            opens=[Decimal("100")] * 20 + [Decimal("101.75")],
            highs=[Decimal("100.5")] * 20 + [Decimal("101.75")],
            lows=[Decimal("99.5")] * 20 + [Decimal("100")],
        )
        self.assertTrue(self.hypothesis("af3.b4.clear.down").evaluator(b4))
        self.assertFalse(self.hypothesis("af3.b4.extreme.down").evaluator(b4))

    def test_c3_prior_only_compression_tail_and_paired_direction(self) -> None:
        prior = [Decimal("100") + (Decimal("0.1") if index % 2 else Decimal(0)) for index in range(26)]
        prior += [prior[-1]] * 5
        event = candles(
            32, closes=prior + [Decimal("100.6")],
            opens=prior + [Decimal("99.2")],
            highs=[value + Decimal("0.5") for value in prior] + [Decimal("101")],
            lows=[value - Decimal("0.5") for value in prior] + [Decimal("99")],
        )
        self.assertTrue(self.hypothesis("af3.c3.breakout.up").evaluator(event))
        self.assertFalse(self.hypothesis("af3.c3.breakout.down").evaluator(event))
        self.assertFalse(self.hypothesis("af3.c3.extreme-breakout.up").evaluator(event))

    @staticmethod
    def shock_closes(rate: Decimal) -> list[Decimal]:
        with af1_decimal_context():
            values = [Decimal("100")]
            for index in range(43220):
                step = rate if index >= 43215 else Decimal("0.0001")
                values.append(values[-1] * (Decimal(1) + step))
            return values

    @staticmethod
    def periodic_threshold_closes(shock: Decimal) -> list[Decimal]:
        with af1_decimal_context():
            values = [Decimal("100")]
            for index in range(43220):
                step = shock if (index + 1) % 20 == 0 else Decimal(0)
                values.append(values[-1] * (Decimal(1) + step))
            return values

    @staticmethod
    def low_volatility_shock_closes(shock: Decimal) -> list[Decimal]:
        values = [
            Decimal("102") if index % 2 else Decimal("100")
            for index in range(43221)
        ]
        values[-21:-1] = [Decimal("100")] * 20
        values[-1] = Decimal("100") * (Decimal(1) + shock)
        return values

    def test_c4_c5_exact_prior_calibration_and_current_exclusion(self) -> None:
        positive_closes = self.shock_closes(Decimal("0.0015"))
        prior_sums, current_sum = _rolling_squared_sums(tuple(positive_closes), 20)
        self.assertEqual(len(prior_sums), 43200)
        self.assertIsNotNone(current_sum)
        self.assertEqual(
            _rms_from_squared_sum(prior_sums[0], 20),
            root_mean_square_return(tuple(positive_closes[:21])),
        )
        self.assertEqual(
            _rms_from_squared_sum(prior_sums[-1], 20),
            root_mean_square_return(tuple(positive_closes[-22:-1])),
        )
        selected_sum = nearest_rank_quantile(prior_sums, Decimal("0.80"), 2000)
        self.assertIsNotNone(selected_sum)
        selected_rms = _rms_from_squared_sum(selected_sum, 20)
        reference_rv = tuple(
            root_mean_square_return(
                tuple(positive_closes[end - 20:end + 1])
            )
            for end in range(20, len(positive_closes) - 1)
        )
        self.assertEqual(
            selected_rms,
            nearest_rank_quantile(reference_rv, Decimal("0.80"), 2000),
        )

        changed = list(positive_closes)
        changed[-1] *= Decimal("1.1")
        changed_prior, changed_current = _rolling_squared_sums(tuple(changed), 20)
        self.assertEqual(changed_prior, prior_sums)
        self.assertNotEqual(changed_current, current_sum)
        self.assertTrue(
            self.hypothesis("af3.c4.high-vol.down").evaluator(
                candles(43221, closes=positive_closes)
            )
        )

        negative_closes = self.shock_closes(Decimal("-0.00151"))
        self.assertTrue(
            self.hypothesis("af3.c5.high-vol.up").evaluator(
                candles(43221, closes=negative_closes)
            )
        )
        self.assertFalse(
            self.hypothesis("af3.c4.high-vol.down").evaluator(candles(43221))
        )

    def test_c4_c5_fast_reject_never_builds_calibration(self) -> None:
        neutral = candles(43221)
        with patch(
            "quantos.domain.evaluation.alpha_hypotheses._rolling_squared_sum_state",
            side_effect=AssertionError("calibration must not run"),
        ) as calibration:
            self.assertFalse(
                self.hypothesis("af3.c4.high-vol.down").evaluator(neutral)
            )
            self.assertFalse(
                self.hypothesis("af3.c5.high-vol.up").evaluator(neutral)
            )
        calibration.assert_not_called()

    def test_r5_shock_enters_calibration_even_when_last_r1_is_small(self) -> None:
        closes = self.shock_closes(Decimal("0.0015"))
        self.assertLess(
            simple_return(closes[-1], closes[-2]), Decimal("0.0075")
        )
        self.assertGreaterEqual(
            simple_return(closes[-1], closes[-6]), Decimal("0.0075")
        )
        event = candles(43221, closes=closes)
        with patch(
            "quantos.domain.evaluation.alpha_hypotheses._rolling_squared_sum_state",
            wraps=_rolling_squared_sum_state,
        ) as calibration:
            self.assertTrue(
                self.hypothesis("af3.c4.high-vol.down").evaluator(event)
            )
        calibration.assert_called_once()

    def test_c4_c5_ordinary_slow_paths_use_exactly_two_square_roots(self) -> None:
        cases = (
            (
                "af3.c4.high-vol.down",
                candles(43221, closes=self.shock_closes(Decimal("0.0015"))),
            ),
            (
                "af3.c5.high-vol.up",
                candles(43221, closes=self.shock_closes(Decimal("-0.00151"))),
            ),
        )
        for stable_id, event in cases:
            with patch(
                "quantos.domain.evaluation.alpha_hypotheses._rms_from_squared_sum",
                wraps=_rms_from_squared_sum,
            ) as rms:
                self.assertTrue(self.hypothesis(stable_id).evaluator(event))
            with self.subTest(hypothesis=stable_id):
                self.assertEqual(rms.call_count, 2)

    def test_optimized_c4_c5_match_slow_reference_exactly(self) -> None:
        cases = [
            (
                "af3.c4.high-vol.down",
                self.positive_behavior_window("af3.c4.high-vol.down"),
            ),
            (
                "af3.c5.high-vol.up",
                self.positive_behavior_window("af3.c5.high-vol.up"),
            ),
            (
                "af3.c4.high-vol.down",
                candles(43221, closes=self.shock_closes(Decimal("0.0015"))),
            ),
            (
                "af3.c5.high-vol.up",
                candles(43221, closes=self.shock_closes(Decimal("-0.00151"))),
            ),
            (
                "af3.c4.high-vol.down",
                candles(
                    43221,
                    closes=self.periodic_threshold_closes(Decimal("0.0075")),
                ),
            ),
            (
                "af3.c5.high-vol.up",
                candles(
                    43221,
                    closes=self.periodic_threshold_closes(Decimal("-0.0075")),
                ),
            ),
            (
                "af3.c4.high-vol.down",
                candles(
                    43221,
                    closes=self.low_volatility_shock_closes(Decimal("0.0075")),
                ),
                False,
            ),
            (
                "af3.c5.high-vol.up",
                candles(
                    43221,
                    closes=self.low_volatility_shock_closes(Decimal("-0.0075")),
                ),
                False,
            ),
        ]
        for case in cases:
            stable_id, window = case[:2]
            compare_threshold = len(case) == 2 or case[2]
            hypothesis = self.hypothesis(stable_id)
            expected, reference_threshold = reference_high_volatility(
                window, hypothesis.metadata.parameters,
                hypothesis.metadata.direction.value,
            )
            prior_sums, _ = _rolling_squared_sums(
                tuple(candle.close for candle in window), 20
            )
            selected_sum = nearest_rank_quantile(
                prior_sums, Decimal("0.80"), 2000
            )
            self.assertIsNotNone(selected_sum)
            with self.subTest(hypothesis=stable_id):
                self.assertEqual(hypothesis.evaluator(window), expected)
                if compare_threshold:
                    self.assertEqual(
                        _rms_from_squared_sum(selected_sum, 20),
                        reference_threshold,
                    )

    def test_squared_sum_quantile_preserves_nearby_rounded_rms_ties(self) -> None:
        with af1_decimal_context():
            lower = Decimal(1)
            upper = lower + Decimal("1e-60")
        lower_rms = _rms_from_squared_sum(lower, 20)
        upper_rms = _rms_from_squared_sum(upper, 20)
        self.assertEqual(lower_rms, upper_rms)
        squared_values = (upper, lower, upper, lower, upper)
        rms_values = tuple(_rms_from_squared_sum(value, 20) for value in squared_values)
        selected_sum = nearest_rank_quantile(
            squared_values, Decimal("0.80"), len(squared_values)
        )
        selected_rms = nearest_rank_quantile(
            rms_values, Decimal("0.80"), len(rms_values)
        )
        self.assertEqual(_rms_from_squared_sum(selected_sum, 20), selected_rms)

    def test_all_44_definitions_have_direct_true_and_false_behavior(self) -> None:
        cases = self.behavior_cases()
        self.assertEqual(tuple(cases), BEHAVIOR_STABLE_IDS)
        self.assertEqual(set(cases), set(self.by_stable_id))
        covered_planned_ids = {
            self.hypothesis(stable_id).metadata.parameters[
                "af2a_planned_definition_sha256"
            ]
            for stable_id in cases
        }
        self.assertEqual(
            covered_planned_ids,
            {
                row["planned_definition_sha256"]
                for row in self.plan["planned_definitions"]
            },
        )
        for stable_id, (positive, negative) in cases.items():
            evaluator = self.hypothesis(stable_id).evaluator
            with self.subTest(hypothesis=stable_id, expected=True):
                self.assertTrue(evaluator(positive))
            with self.subTest(hypothesis=stable_id, expected=False):
                self.assertFalse(evaluator(negative))

    def test_all_44_positive_cases_ignore_hostile_decimal_context(self) -> None:
        for stable_id, (positive, _) in self.behavior_cases().items():
            evaluator = self.hypothesis(stable_id).evaluator
            expected = evaluator(positive)
            self.assertTrue(expected)
            with localcontext() as context:
                context.prec = 3
                context.rounding = ROUND_DOWN
                actual = evaluator(positive)
            with self.subTest(hypothesis=stable_id):
                self.assertEqual(actual, expected)


    def test_e2_e3_prior_pressure_activity_and_wrong_direction(self) -> None:
        closes = [Decimal("100")] * 21
        closes[19] = closes[20] = Decimal("99.6")
        quote = [Decimal("10")] * 20 + [Decimal("20")]
        e2 = candles(21, closes=closes, quote_volumes=quote)
        self.assertTrue(self.hypothesis("af3.e2.disagreement.up").evaluator(e2))
        self.assertFalse(self.hypothesis("af3.e2.disagreement.down").evaluator(e2))

        width = closes[19] * Decimal("0.0015")
        lows = [Decimal("99")] * 20 + [closes[20] - width / 2]
        highs = [Decimal("101")] * 20 + [closes[20] + width / 2]
        counts = [10] * 20 + [20]
        e3 = candles(21, closes=closes, highs=highs, lows=lows, trade_counts=counts)
        self.assertTrue(self.hypothesis("af3.e3.concentrated.up").evaluator(e3))
        self.assertFalse(self.hypothesis("af3.e3.concentrated.down").evaluator(e3))

    def test_e4_efficiency_decay_activity_and_wrong_direction(self) -> None:
        closes = [Decimal("100")] * 10
        closes += [Decimal("100"), Decimal("100.1"), Decimal("100.2"), Decimal("100.3"), Decimal("100.4"), Decimal("100.5"), Decimal("100.3"), Decimal("100.5"), Decimal("100.3"), Decimal("100.5"), Decimal("100.5")]
        quote = [Decimal("10")] * 20 + [Decimal("17.5")]
        event = candles(21, closes=closes, quote_volumes=quote)
        self.assertTrue(self.hypothesis("af3.e4.decay.down").evaluator(event))
        self.assertFalse(self.hypothesis("af3.e4.decay.up").evaluator(event))

    def test_e5_e6_participation_sign_and_boundaries(self) -> None:
        closes = [Decimal("100")] * 20 + [Decimal("100.75")]
        low = candles(
            21, closes=closes,
            quote_volumes=[Decimal("100")] * 20 + [Decimal("70")],
            trade_counts=[100] * 20 + [75],
        )
        self.assertTrue(self.hypothesis("af3.e5.fragile.down").evaluator(low))
        self.assertFalse(self.hypothesis("af3.e5.fragile.up").evaluator(low))
        high = candles(
            21, closes=closes,
            quote_volumes=[Decimal("100")] * 20 + [Decimal("175")],
            trade_counts=[100] * 20 + [150],
        )
        self.assertTrue(self.hypothesis("af3.e6.confirmed.up").evaluator(high))
        self.assertFalse(self.hypothesis("af3.e6.confirmed.down").evaluator(high))

    def test_f1_current_horizon_ownership_threshold_and_wrong_direction(self) -> None:
        closes = [Decimal("100")] * 21
        closes[20] = Decimal("100.5")
        with af1_decimal_context():
            closes[19] = closes[20] / (Decimal(1) - Decimal("0.0015"))
        event = candles(21, closes=closes)
        self.assertTrue(self.hypothesis("af3.f1.pullback.up").evaluator(event))
        self.assertFalse(self.hypothesis("af3.f1.pullback.down").evaluator(event))
        self.assertFalse(self.hypothesis("af3.f1.deep-pullback.up").evaluator(event))

    def test_callbacks_ignore_hostile_external_decimal_context(self) -> None:
        event = candles(
            6, closes=[Decimal("100")] * 5 + [Decimal("99.6")],
            opens=[Decimal("100")] * 6,
            highs=[Decimal("101")] * 5 + [Decimal("100")],
            lows=[Decimal("99")] * 6,
        )
        hypothesis = self.hypothesis("af3.a3.clear-rejection.up")
        baseline = hypothesis.evaluator(event)
        with localcontext() as context:
            context.prec = 3
            context.rounding = ROUND_DOWN
            hostile = hypothesis.evaluator(event)
        self.assertEqual(hostile, baseline)

        for hypothesis in self.hypotheses:
            neutral = candles(hypothesis.metadata.causal_lookback)
            expected = hypothesis.evaluator(neutral)
            with localcontext() as context:
                context.prec = 3
                context.rounding = ROUND_DOWN
                actual = hypothesis.evaluator(neutral)
            with self.subTest(hypothesis=hypothesis.metadata.stable_id):
                self.assertEqual(actual, expected)

    def test_zero_denominators_fail_every_concept_cleanly(self) -> None:
        representative = {}
        for hypothesis in self.hypotheses:
            representative.setdefault(
                hypothesis.metadata.stable_id.split(".")[1], hypothesis
            )
        self.assertEqual(len(representative), 15)
        for concept, hypothesis in representative.items():
            lookback = hypothesis.metadata.causal_lookback
            zero = candles(
                lookback,
                closes=[Decimal(0)] * lookback,
                opens=[Decimal(0)] * lookback,
                highs=[Decimal(0)] * lookback,
                lows=[Decimal(0)] * lookback,
                quote_volumes=[Decimal(0)] * lookback,
                trade_counts=[0] * lookback,
            )
            with self.subTest(concept=concept):
                self.assertFalse(hypothesis.evaluator(zero))

if __name__ == "__main__":
    unittest.main()

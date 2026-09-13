"""AF3A materialization of the frozen AF2A plan into executable AF1 hypotheses."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any

from quantos.domain.evaluation.alpha_catalog import (
    AF1_HORIZONS_MINUTES,
    AF1_SYMBOLS,
    AF2A_PLANNED_DEFINITION_VERSION,
    CANONICAL_CANDLE_INPUTS,
    EXPECTED_PLANNED_DEFINITION_COUNT,
    EXPECTED_RETAINED_CONCEPT_COUNT,
    CatalogEntry,
    ExpectedDirection,
    HypothesisCatalog,
    af1_decimal_context,
    close_location,
    derive_fdr_plan,
    load_and_verify_fdr_plan,
    load_catalog_bytes,
    nearest_rank_quantile,
    path_efficiency,
    relative_activity,
    root_mean_square_return,
    safe_ratio,
    simple_return,
    wick_fractions,
)
from quantos.domain.evaluation.alpha_funnel import (
    Direction,
    HypothesisMetadata,
    ResearchHypothesis,
    canonical_json_bytes,
)


AF3A_SCHEMA_VERSION = "alpha-hypothesis-materialization-af3a-v1"
AF3A_MATERIALIZER_VERSION = "af2a-explicit-candle-dispatch-v1"
FROZEN_CATALOG_ID = "060147848ab0d3aa3deb4a77fe0cf5af6bdd10a374587c24e5ff7a640a0a5d74"
FROZEN_FDR_PLAN_ID = "32a6a1e1e916a0b012bd5ac22d747b3b57afa53a99e5153d0fb1a0685511a6ae"
EXPECTED_REGISTERED_TEST_COUNT = 616

EXPECTED_CONCEPT_IDS = (
    "af2a.a3.lower-wick-after-negative-move-reversal-up",
    "af2a.a4.upper-wick-after-positive-move-reversal-down",
    "af2a.a5.large-range-weak-close-failed-continuation",
    "af2a.a6.overextension-deteriorating-efficiency-reversal",
    "af2a.b3.range-expansion-close-high-continuation-up",
    "af2a.b4.range-expansion-close-low-continuation-down",
    "af2a.c3.compression-directional-range-expansion",
    "af2a.c4.positive-shock-high-volatility-reversal-down",
    "af2a.c5.negative-shock-high-volatility-reversal-up",
    "af2a.e2.high-quote-volume-weak-return",
    "af2a.e3.high-trade-count-narrow-range",
    "af2a.e4.high-activity-declining-efficiency",
    "af2a.e5.large-return-low-participation",
    "af2a.e6.large-return-high-participation",
    "af2a.f1.one-minute-opposed-fifteen-minute-state",
)

_IMPLEMENTATION_IDS = MappingProxyType({
    concept_id: f"af3-{concept_id.split('.')[1]}-v1"
    for concept_id in EXPECTED_CONCEPT_IDS
})


class MaterializationError(ValueError):
    """The frozen AF2A plan cannot be materialized exactly into AF1."""


def _decimal(parameters: Mapping[str, object], name: str) -> Decimal:
    value = parameters.get(name)
    if type(value) is not str:
        raise MaterializationError(f"{name} must be a canonical Decimal string")
    try:
        with af1_decimal_context():
            parsed = Decimal(value)
    except DecimalException as error:
        raise MaterializationError(f"{name} is not a valid Decimal") from error
    if not parsed.is_finite():
        raise MaterializationError(f"{name} must be finite")
    return parsed


def _integer(parameters: Mapping[str, object], name: str) -> int:
    value = parameters.get(name)
    if type(value) is not int:
        raise MaterializationError(f"{name} must be an integer")
    return value


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values or any(type(value) is not Decimal or not value.is_finite() for value in values):
        return None
    with af1_decimal_context():
        return sum(values, Decimal(0)) / Decimal(len(values))


def _direction(expected: str) -> Direction:
    if expected in (
        ExpectedDirection.CONTINUATION_UP.value,
        ExpectedDirection.REVERSAL_UP.value,
    ):
        return Direction.UP
    if expected in (
        ExpectedDirection.CONTINUATION_DOWN.value,
        ExpectedDirection.REVERSAL_DOWN.value,
    ):
        return Direction.DOWN
    raise MaterializationError("planned definition has no AF1 direction")


def _signed_match(value: Decimal, direction: Direction, *, reverse: bool) -> bool:
    if value == 0:
        return False
    expected_up = value < 0 if reverse else value > 0
    return (direction is Direction.UP) is expected_up


def _a3(window: tuple[Any, ...], p: Mapping[str, object], _: Direction) -> bool:
    current = window[-1]
    movement = simple_return(current.close, window[-6].close)
    wicks = wick_fractions(current.open, current.high, current.low, current.close)
    location = close_location(current.close, current.low, current.high)
    return bool(
        movement is not None and wicks is not None and location is not None
        and movement <= _decimal(p, "return_max")
        and wicks[1] >= _decimal(p, "wick_min")
        and location >= _decimal(p, "close_min")
    )


def _a4(window: tuple[Any, ...], p: Mapping[str, object], _: Direction) -> bool:
    current = window[-1]
    movement = simple_return(current.close, window[-6].close)
    wicks = wick_fractions(current.open, current.high, current.low, current.close)
    location = close_location(current.close, current.low, current.high)
    return bool(
        movement is not None and wicks is not None and location is not None
        and movement >= _decimal(p, "return_min")
        and wicks[0] >= _decimal(p, "wick_min")
        and location <= _decimal(p, "close_max")
    )


def _a5(window: tuple[Any, ...], p: Mapping[str, object], direction: Direction) -> bool:
    current = window[-1]
    range_pct = safe_ratio(current.high - current.low, window[-2].close)
    location = close_location(current.close, current.low, current.high)
    if range_pct is None or location is None or range_pct < _decimal(p, "range_pct_min"):
        return False
    tail = _decimal(p, "failure_tail")
    if direction is Direction.UP:
        return current.close < current.open and location >= tail
    return current.close > current.open and location <= Decimal(1) - tail


def _a6(window: tuple[Any, ...], p: Mapping[str, object], direction: Direction) -> bool:
    extension_returns = _integer(p, "extension_lookback_returns")
    prior_returns = _integer(p, "prior_efficiency_returns")
    short_returns = _integer(p, "short_efficiency_returns")
    if (
        extension_returns != 15 or prior_returns != 10 or short_returns != 5
        or p.get("windows_overlap") is not False
        or p.get("boundary_rule") != "shared_endpoint_disjoint_return_intervals"
    ):
        raise MaterializationError("A6 frozen window contract changed")
    movement = simple_return(window[-1].close, window[-1 - extension_returns].close)
    prior = path_efficiency(tuple(candle.close for candle in window[: prior_returns + 1]))
    recent = path_efficiency(tuple(candle.close for candle in window[prior_returns:]))
    if movement is None or prior is None or recent is None:
        return False
    return bool(
        abs(movement) >= _decimal(p, "extension_min")
        and prior - recent >= _decimal(p, "efficiency_drop")
        and _signed_match(movement, direction, reverse=True)
    )


def _range_expansion(
    window: tuple[Any, ...], p: Mapping[str, object], direction: Direction,
) -> bool:
    current = window[-1]
    baseline = _mean(tuple(candle.high - candle.low for candle in window[:-1]))
    ratio = None if baseline is None else safe_ratio(current.high - current.low, baseline)
    location = close_location(current.close, current.low, current.high)
    if ratio is None or location is None or ratio < _decimal(p, "range_vs_20m_mean"):
        return False
    if direction is Direction.UP:
        return current.close > current.open and location >= _decimal(p, "close_min")
    return current.close < current.open and location <= _decimal(p, "close_max")


def _c3(window: tuple[Any, ...], p: Mapping[str, object], direction: Direction) -> bool:
    current = window[-1]
    prior_closes = tuple(candle.close for candle in window[:-1])
    rv30 = root_mean_square_return(prior_closes)
    rv5 = root_mean_square_return(prior_closes[-6:])
    compression = None if rv30 is None or rv5 is None else safe_ratio(rv5, rv30)
    baseline = _mean(tuple(candle.high - candle.low for candle in window[-21:-1]))
    expansion = None if baseline is None else safe_ratio(current.high - current.low, baseline)
    location = close_location(current.close, current.low, current.high)
    if (
        compression is None or expansion is None or location is None or rv30 == 0
        or compression > _decimal(p, "compression_max")
        or expansion < _decimal(p, "range_expansion_min")
    ):
        return False
    tail = _decimal(p, "close_tail")
    if direction is Direction.UP:
        return current.close > current.open and location >= tail
    return current.close < current.open and location <= Decimal(1) - tail


def _rolling_squared_sum_state(
    closes: tuple[Decimal, ...], returns_per_rv: int,
) -> tuple[
    tuple[Decimal, ...],
    Decimal | None,
    tuple[Decimal | None, ...],
]:
    """Build O(N) rolling sums and retain local squares for exact tie resolution."""
    if (
        type(closes) is not tuple
        or type(returns_per_rv) is not int
        or returns_per_rv < 1
        or len(closes) <= returns_per_rv
    ):
        return (), None, ()

    with af1_decimal_context():
        window: list[Decimal | None] = [None] * returns_per_rv
        squares: list[Decimal | None] = []
        invalid = 0
        total = Decimal(0)
        rolling: list[Decimal] = []
        position = 0

        for return_index, (previous, current) in enumerate(
            zip(closes, closes[1:])
        ):
            if (
                type(previous) is not Decimal
                or type(current) is not Decimal
                or not previous.is_finite()
                or not current.is_finite()
                or previous == 0
            ):
                square = None
            else:
                value = current / previous - Decimal(1)
                square = value * value if value.is_finite() else None
            squares.append(square)

            if return_index < returns_per_rv:
                window[position] = square
                position = (position + 1) % returns_per_rv
                if square is None:
                    invalid += 1
                else:
                    total += square
                if return_index == returns_per_rv - 1 and invalid == 0:
                    rolling.append(total)
                continue

            leaving = window[position]
            if leaving is None:
                invalid -= 1
            else:
                total -= leaving
            window[position] = square
            position = (position + 1) % returns_per_rv
            if square is None:
                invalid += 1
            else:
                total += square
            if invalid == 0:
                rolling.append(total)

        expected_window_count = len(closes) - returns_per_rv
        if len(rolling) != expected_window_count:
            return tuple(rolling), None, tuple(squares)
        current_values = squares[-returns_per_rv:]
        current = sum(
            (value for value in current_values if value is not None),
            Decimal(0),
        )
    return tuple(rolling[:-1]), current, tuple(squares)


def _rolling_squared_sums(
    closes: tuple[Decimal, ...], returns_per_rv: int,
) -> tuple[tuple[Decimal, ...], Decimal | None]:
    prior, current, _ = _rolling_squared_sum_state(closes, returns_per_rv)
    return prior, current


def _rms_from_squared_sum(squared_sum: Decimal, returns_per_rv: int) -> Decimal:
    with af1_decimal_context():
        return (squared_sum / Decimal(returns_per_rv)).sqrt()


def _reference_prior_squared_sums(
    squares: tuple[Decimal | None, ...], returns_per_rv: int,
) -> tuple[Decimal, ...]:
    """Resolve a rare rounding-boundary ambiguity without extra square roots."""
    if any(value is None for value in squares):
        return ()
    with af1_decimal_context():
        return tuple(
            sum(squares[start:start + returns_per_rv], Decimal(0))  # type: ignore[arg-type]
            for start in range(len(squares) - returns_per_rv)
        )


def _rolling_sum_uncertainty(values: Sequence[Decimal]) -> Decimal:
    """Conservative bound for fixed-context recurrence rounding."""
    maximum = max((abs(value) for value in values), default=Decimal(0))
    if maximum == 0:
        return Decimal(0)
    with af1_decimal_context() as context:
        ulp = Decimal(1).scaleb(maximum.adjusted() - context.prec + 1)
        return ulp * Decimal(4 * len(values) + 100)


def _high_volatility_shock(
    window: tuple[Any, ...], p: Mapping[str, object], direction: Direction,
) -> bool:
    if (
        _integer(p, "calibration_lookback_minutes") != 43200
        or _integer(p, "rv_lookback_returns") != 20
        or _integer(p, "calibration_update_minutes") != 1
        or p.get("calibration_scope") != "per_symbol"
        or p.get("calibration_mode") != "rolling_prior_only"
        or p.get("exclude_current_observation") is not True
        or p.get("quantile_rule") != "nearest_rank_ceil"
        or p.get("rv_estimator") != "rms_simple_returns"
        or p.get("zero_volatility_behavior")
        != "valid_zero_current_undefined_zero_baseline"
    ):
        raise MaterializationError("C4/C5 frozen calibration contract changed")

    # AF2A defines the directional shock as r_5. Rejecting that conjunct
    # before constructing the calibration is exact and keeps the common path O(1).
    shock = simple_return(window[-1].close, window[-6].close)
    shock_min = _decimal(p, "shock")
    if (
        shock is None
        or (direction is Direction.DOWN and shock < shock_min)
        or (direction is Direction.UP and shock > -shock_min)
    ):
        return False

    closes = tuple(candle.close for candle in window)
    prior_squared_sums, current_squared_sum, squares = (
        _rolling_squared_sum_state(closes, 20)
    )
    threshold_squared_sum = nearest_rank_quantile(
        prior_squared_sums,
        _decimal(p, "rv_state_quantile"),
        _integer(p, "minimum_calibration_observations"),
    )
    if current_squared_sum is None or threshold_squared_sum is None:
        return False

    uncertainty = _rolling_sum_uncertainty(prior_squared_sums)
    if abs(current_squared_sum - threshold_squared_sum) <= uncertainty:
        reference_prior = _reference_prior_squared_sums(squares, 20)
        threshold_squared_sum = nearest_rank_quantile(
            reference_prior,
            _decimal(p, "rv_state_quantile"),
            _integer(p, "minimum_calibration_observations"),
        )
        if threshold_squared_sum is None:
            return False

    threshold = _rms_from_squared_sum(threshold_squared_sum, 20)
    if threshold == 0:
        return False
    current_rv = _rms_from_squared_sum(current_squared_sum, 20)
    return current_rv >= threshold


def _e2(window: tuple[Any, ...], p: Mapping[str, object], direction: Direction) -> bool:
    previous = window[-2]
    pressure = simple_return(previous.close, window[-2 - _integer(p, "prior_pressure_lookback")].close)
    current_return = simple_return(window[-1].close, previous.close)
    activity = relative_activity(
        window[-1].quote_volume,
        tuple(candle.quote_volume for candle in window[-1 - _integer(p, "quote_volume_normalization_window"):-1]),
    )
    if pressure is None or current_return is None or activity is None:
        return False
    return bool(
        activity >= _decimal(p, "activity_min")
        and abs(current_return) <= _decimal(p, "return_max")
        and _signed_match(pressure, direction, reverse=True)
        and abs(pressure) >= _decimal(p, "pressure_min")
    )


def _e3(window: tuple[Any, ...], p: Mapping[str, object], direction: Direction) -> bool:
    previous = window[-2]
    pressure = simple_return(previous.close, window[-2 - _integer(p, "prior_pressure_lookback")].close)
    count_window = _integer(p, "trade_count_normalization_window")
    counts = tuple(Decimal(candle.trade_count) for candle in window[-1 - count_window:-1])
    activity = relative_activity(Decimal(window[-1].trade_count), counts)
    normalized_range = safe_ratio(window[-1].high - window[-1].low, previous.close)
    if pressure is None or activity is None or normalized_range is None:
        return False
    return bool(
        activity >= _decimal(p, "count_min")
        and normalized_range <= _decimal(p, "range_max")
        and _signed_match(pressure, direction, reverse=True)
        and abs(pressure) >= _decimal(p, "pressure_min")
    )


def _e4(window: tuple[Any, ...], p: Mapping[str, object], direction: Direction) -> bool:
    movement = simple_return(window[-1].close, window[-11].close)
    activity = relative_activity(
        window[-1].quote_volume, tuple(candle.quote_volume for candle in window[:-1])
    )
    efficiency10 = path_efficiency(tuple(candle.close for candle in window[-11:]))
    efficiency5 = path_efficiency(tuple(candle.close for candle in window[-6:]))
    if any(value is None for value in (movement, activity, efficiency10, efficiency5)):
        return False
    return bool(
        activity >= _decimal(p, "activity_min")  # type: ignore[operator]
        and efficiency5 <= efficiency10 - _decimal(p, "efficiency_drop")  # type: ignore[operator]
        and abs(movement) >= _decimal(p, "move_min")  # type: ignore[arg-type]
        and _signed_match(movement, direction, reverse=True)  # type: ignore[arg-type]
    )


def _participation(
    window: tuple[Any, ...], p: Mapping[str, object], direction: Direction, *, high: bool,
) -> bool:
    movement = simple_return(window[-1].close, window[-6].close)
    quote_ratio = relative_activity(
        window[-1].quote_volume, tuple(candle.quote_volume for candle in window[:-1])
    )
    count_ratio = relative_activity(
        Decimal(window[-1].trade_count), tuple(Decimal(candle.trade_count) for candle in window[:-1])
    )
    if movement is None or quote_ratio is None or count_ratio is None:
        return False
    if abs(movement) < _decimal(p, "return_min"):
        return False
    if high:
        accepted = (
            quote_ratio >= _decimal(p, "quote_volume_ratio_min")
            and count_ratio >= _decimal(p, "trade_count_ratio_min")
        )
    else:
        accepted = (
            quote_ratio <= _decimal(p, "quote_volume_ratio_max")
            and count_ratio <= _decimal(p, "trade_count_ratio_max")
        )
    return accepted and _signed_match(movement, direction, reverse=not high)


def _e5(window: tuple[Any, ...], p: Mapping[str, object], direction: Direction) -> bool:
    return _participation(window, p, direction, high=False)


def _e6(window: tuple[Any, ...], p: Mapping[str, object], direction: Direction) -> bool:
    return _participation(window, p, direction, high=True)


def _f1(window: tuple[Any, ...], p: Mapping[str, object], direction: Direction) -> bool:
    return1 = simple_return(window[-1].close, window[-2].close)
    return15 = simple_return(window[-1].close, window[-16].close)
    if return1 is None or return15 is None or return1 == 0 or return15 == 0:
        return False
    return bool(
        (return1 > 0) is not (return15 > 0)
        and abs(return15) >= _decimal(p, "state_min")
        and abs(return1) >= _decimal(p, "pullback_min")
        and _signed_match(return15, direction, reverse=False)
    )


_DISPATCH = MappingProxyType({
    EXPECTED_CONCEPT_IDS[0]: _a3,
    EXPECTED_CONCEPT_IDS[1]: _a4,
    EXPECTED_CONCEPT_IDS[2]: _a5,
    EXPECTED_CONCEPT_IDS[3]: _a6,
    EXPECTED_CONCEPT_IDS[4]: _range_expansion,
    EXPECTED_CONCEPT_IDS[5]: _range_expansion,
    EXPECTED_CONCEPT_IDS[6]: _c3,
    EXPECTED_CONCEPT_IDS[7]: _high_volatility_shock,
    EXPECTED_CONCEPT_IDS[8]: _high_volatility_shock,
    EXPECTED_CONCEPT_IDS[9]: _e2,
    EXPECTED_CONCEPT_IDS[10]: _e3,
    EXPECTED_CONCEPT_IDS[11]: _e4,
    EXPECTED_CONCEPT_IDS[12]: _e5,
    EXPECTED_CONCEPT_IDS[13]: _e6,
    EXPECTED_CONCEPT_IDS[14]: _f1,
})


@dataclass(frozen=True, slots=True)
class _ConceptEvaluator:
    concept_id: str
    parameters: tuple[tuple[str, str | int | bool | None], ...]
    direction: Direction
    causal_lookback: int

    def __call__(self, window: tuple[Any, ...]) -> bool:
        if type(window) is not tuple or len(window) != self.causal_lookback:
            return False
        callback = _DISPATCH.get(self.concept_id)
        if callback is None:
            raise MaterializationError("unknown AF3A concept")
        try:
            with af1_decimal_context():
                return bool(callback(window, MappingProxyType(dict(self.parameters)), self.direction))
        except MaterializationError:
            raise
        except (AttributeError, DecimalException, TypeError, ValueError):
            return False


def _stable_id(row: Mapping[str, object]) -> str:
    concept_code = str(row["concept_stable_id"]).split(".")[1]
    label = str(row["parameter_set_label"]).replace("_", "-")
    direction = "up" if str(row["direction"]).endswith("_up") else "down"
    return f"af3.{concept_code}.{label}.{direction}"


def _metadata(row: Mapping[str, object], entry: CatalogEntry, plan_id: str) -> HypothesisMetadata:
    concept_id = str(row["concept_stable_id"])
    implementation_id = _IMPLEMENTATION_IDS.get(concept_id)
    if implementation_id is None:
        raise MaterializationError("unknown retained AF2A concept")
    raw_parameters = row["parameters"]
    if type(raw_parameters) is not dict:
        raise MaterializationError("planned parameters must be a mapping")
    parameters = dict(raw_parameters)
    parameters.update({
        "af2a_catalog_entry_sha256": row["catalog_entry_sha256"],
        "af2a_catalog_id": row["catalog_id"],
        "af2a_concept_stable_id": concept_id,
        "af2a_fdr_plan_id": plan_id,
        "af2a_parameter_set_label": row["parameter_set_label"],
        "af2a_planned_definition_sha256": row["planned_definition_sha256"],
        "af2a_planning_identity_version": row["planning_identity_version"],
        "af3_semantic_version": implementation_id,
    })
    return HypothesisMetadata(
        stable_id=_stable_id(row),
        implementation_id=implementation_id,
        family=str(row["family"]),
        description=f"{entry.title}: {row['parameter_set_label']} {row['direction']}.",
        direction=_direction(str(row["direction"])),
        interpretation=entry.economic_rationale,
        required_inputs=tuple(row["required_inputs"]),  # type: ignore[arg-type]
        parameters=parameters,
        causal_lookback=int(row["causal_lookback"]),
        parameter_neighborhood={
            "frozen_before_af3": True,
            "outcome_optimized": False,
            "source": "af2a_predeclared_parameter_set",
        },
        human_explainability_score=entry.explainability_score,
    )


def _evaluator(row: Mapping[str, object]) -> _ConceptEvaluator:
    parameters = row["parameters"]
    if type(parameters) is not dict:
        raise MaterializationError("planned parameters must be a mapping")
    return _ConceptEvaluator(
        concept_id=str(row["concept_stable_id"]),
        parameters=tuple(sorted(parameters.items())),  # type: ignore[arg-type]
        direction=_direction(str(row["direction"])),
        causal_lookback=int(row["causal_lookback"]),
    )


def _validate_plan(catalog: HypothesisCatalog, plan: Mapping[str, object]) -> None:
    if type(catalog) is not HypothesisCatalog or not isinstance(plan, Mapping):
        raise MaterializationError("AF3A requires a verified catalog and FDR plan")
    if catalog.catalog_id != FROZEN_CATALOG_ID:
        raise MaterializationError("AF3A catalog ID is not the frozen repaired ID")
    if plan.get("fdr_plan_id") != FROZEN_FDR_PLAN_ID:
        raise MaterializationError("AF3A FDR-plan ID is not the frozen repaired ID")
    if dict(plan) != derive_fdr_plan(catalog):
        raise MaterializationError("AF3A FDR plan differs from the canonical catalog")
    retained = catalog.af3_entries()
    if tuple(entry.stable_id for entry in retained) != EXPECTED_CONCEPT_IDS:
        raise MaterializationError("AF3A retained concept universe changed")
    if len(retained) != EXPECTED_RETAINED_CONCEPT_COUNT:
        raise MaterializationError("AF3A retained concept count changed")
    if plan.get("planned_definition_count") != EXPECTED_PLANNED_DEFINITION_COUNT:
        raise MaterializationError("AF3A planned definition count changed")
    if plan.get("planned_registered_test_count") != EXPECTED_REGISTERED_TEST_COUNT:
        raise MaterializationError("AF3A planned registration count changed")
    if tuple(plan.get("symbols", ())) != AF1_SYMBOLS:
        raise MaterializationError("AF3A symbols differ from QuantOS V1")
    if tuple(plan.get("horizons_minutes", ())) != AF1_HORIZONS_MINUTES:
        raise MaterializationError("AF3A horizons differ from AF1")
    for row in plan["planned_definitions"]:  # type: ignore[index]
        if row["planning_identity_version"] != AF2A_PLANNED_DEFINITION_VERSION:
            raise MaterializationError("AF2A planning identity version changed")
        if not set(row["required_inputs"]).issubset(CANONICAL_CANDLE_INPUTS):
            raise MaterializationError("retained definition is not AF1 Candle-compatible")


def materialize_hypotheses(
    catalog: HypothesisCatalog, plan: Mapping[str, object],
) -> tuple[ResearchHypothesis, ...]:
    """Materialize exactly the 44 catalog-derived planned definitions."""
    _validate_plan(catalog, plan)
    by_id = {entry.stable_id: entry for entry in catalog.af3_entries()}
    hypotheses = tuple(
        ResearchHypothesis(
            metadata=_metadata(row, by_id[row["concept_stable_id"]], str(plan["fdr_plan_id"])),
            evaluator=_evaluator(row),
        )
        for row in plan["planned_definitions"]  # type: ignore[index]
    )
    verify_materialization(catalog, plan, hypotheses)
    return hypotheses


def materialize_from_bytes(
    catalog_payload: bytes, plan_payload: bytes,
) -> tuple[ResearchHypothesis, ...]:
    catalog = load_catalog_bytes(catalog_payload)
    plan = load_and_verify_fdr_plan(catalog_payload, plan_payload)
    return materialize_hypotheses(catalog, plan)


def verify_materialization(
    catalog: HypothesisCatalog,
    plan: Mapping[str, object],
    hypotheses: Sequence[ResearchHypothesis],
) -> None:
    """Verify one-to-one AF2A planning semantics and genuine AF1 definitions."""
    _validate_plan(catalog, plan)
    if type(hypotheses) not in (tuple, list) or len(hypotheses) != EXPECTED_PLANNED_DEFINITION_COUNT:
        raise MaterializationError("AF3A requires exactly 44 materialized hypotheses")
    by_id = {entry.stable_id: entry for entry in catalog.af3_entries()}
    stable_ids: list[str] = []
    definition_ids: list[str] = []
    for row, hypothesis in zip(plan["planned_definitions"], hypotheses, strict=True):  # type: ignore[index]
        if type(hypothesis) is not ResearchHypothesis:
            raise MaterializationError("materialization contains a non-AF1 hypothesis")
        expected_metadata = _metadata(
            row, by_id[row["concept_stable_id"]], str(plan["fdr_plan_id"])
        )
        if hypothesis.metadata.as_dict() != expected_metadata.as_dict():
            raise MaterializationError("AF1 metadata differs from its AF2A planned definition")
        expected_evaluator = _evaluator(row)
        if type(hypothesis.evaluator) is not _ConceptEvaluator or hypothesis.evaluator != expected_evaluator:
            raise MaterializationError("AF1 evaluator differs from the reviewed concept dispatch")
        stable_ids.append(hypothesis.metadata.stable_id)
        definition_ids.append(hypothesis.metadata.definition_sha256)
    if len(set(stable_ids)) != len(stable_ids):
        raise MaterializationError("AF3A stable IDs must be unique")
    if len(set(definition_ids)) != len(definition_ids):
        raise MaterializationError("AF1 definition SHA-256 values must be unique")


def derive_materialization_manifest(
    catalog: HypothesisCatalog,
    plan: Mapping[str, object],
    hypotheses: Sequence[ResearchHypothesis],
) -> dict[str, object]:
    verify_materialization(catalog, plan, hypotheses)
    rows = [
        {
            "planned_definition_sha256": planned["planned_definition_sha256"],
            "concept_stable_id": planned["concept_stable_id"],
            "parameter_set_label": planned["parameter_set_label"],
            "af2a_direction": planned["direction"],
            "af1_stable_id": hypothesis.metadata.stable_id,
            "af1_implementation_id": hypothesis.metadata.implementation_id,
            "af1_direction": hypothesis.metadata.direction.value,
            "af1_definition_sha256": hypothesis.metadata.definition_sha256,
            "causal_lookback": hypothesis.metadata.causal_lookback,
            "parameters": dict(planned["parameters"]),
            "required_inputs": list(hypothesis.metadata.required_inputs),
        }
        for planned, hypothesis in zip(plan["planned_definitions"], hypotheses, strict=True)  # type: ignore[index]
    ]
    identity = {
        "schema_version": AF3A_SCHEMA_VERSION,
        "catalog_id": catalog.catalog_id,
        "fdr_plan_id": plan["fdr_plan_id"],
        "materializer_version": AF3A_MATERIALIZER_VERSION,
        "planned_definition_count": EXPECTED_PLANNED_DEFINITION_COUNT,
        "materialized_definition_count": len(hypotheses),
        "planned_registered_test_count": plan["planned_registered_test_count"],
        "definitions": rows,
        "af3b_entry_gate": {
            "callback_sentinel_tests_required": True,
            "catalog_and_fdr_ids_remain_frozen": True,
            "exact_one_to_one_coverage": True,
            "extras_allowed": False,
            "full_repository_suite_required": True,
            "manifest_verification_required": True,
        },
    }
    return {
        **identity,
        "materialization_manifest_id": sha256(canonical_json_bytes(identity)).hexdigest(),
    }


def _parse_manifest(payload: bytes) -> dict[str, object]:
    if type(payload) is not bytes:
        raise MaterializationError("materialization manifest payload must be bytes")

    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise MaterializationError("materialization manifest contains duplicate keys")
            result[key] = value
        return result

    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MaterializationError("materialization manifest is not valid UTF-8 JSON") from error
    if type(document) is not dict:
        raise MaterializationError("materialization manifest must be a JSON object")
    return document


def load_and_verify_materialization_manifest(
    catalog_payload: bytes, plan_payload: bytes, manifest_payload: bytes,
) -> Mapping[str, object]:
    """Verify persisted AF3A semantics by deriving the official materialization again."""
    catalog = load_catalog_bytes(catalog_payload)
    plan = load_and_verify_fdr_plan(catalog_payload, plan_payload)
    hypotheses = materialize_hypotheses(catalog, plan)
    expected = derive_materialization_manifest(catalog, plan, hypotheses)
    persisted = _parse_manifest(manifest_payload)
    identity = dict(persisted)
    claimed = identity.pop("materialization_manifest_id", None)
    if claimed != sha256(canonical_json_bytes(identity)).hexdigest():
        raise MaterializationError("materialization manifest identity mismatch")
    if persisted != expected:
        raise MaterializationError("persisted materialization differs from frozen AF3A semantics")
    return MappingProxyType(expected)


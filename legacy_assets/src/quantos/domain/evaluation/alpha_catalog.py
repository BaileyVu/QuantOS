"""Human-directed AF2A hypothesis catalog contracts and deterministic math."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, DecimalException
from enum import Enum
from hashlib import sha256
import re
from types import MappingProxyType

from quantos.domain.common import V1_SYMBOLS
from quantos.domain.evaluation.alpha_funnel import (
    DEFAULT_HORIZONS_MINUTES,
    af1_decimal_context,
    canonical_json_bytes,
)


AF2A_SCHEMA_VERSION = "alpha-hypothesis-catalog-af2a-v2"
AF2A_LIST_SCHEMA_VERSION = "alpha-hypothesis-status-list-af2a-v1"
AF2A_FDR_PLAN_SCHEMA_VERSION = "alpha-fdr-plan-af2a-v2"
AF2A_PLANNED_DEFINITION_VERSION = "af2a-planned-definition-v1"
AF2A_FDR_PLAN_DERIVATION_VERSION = "catalog-derived-fdr-plan-v1"
CATALOG_SOURCE = "human_directed_fixed_catalog"
ONE_WAY_FEE_RATE = Decimal("0.001")
ONE_WAY_SLIPPAGE_RATE = Decimal("0.00025")
ROUND_TRIP_COST_FLOOR = Decimal("0.002503128284573645913980997751940")
CONSERVATIVE_RESEARCH_MARGIN_MULTIPLE = Decimal("2")
CONSERVATIVE_RESEARCH_MARGIN = Decimal("0.005006256569147291827961995503880")
AF1_SYMBOLS = tuple(sorted(V1_SYMBOLS))
AF1_HORIZONS_MINUTES = DEFAULT_HORIZONS_MINUTES
EXPECTED_RETAINED_CONCEPT_COUNT = 15
EXPECTED_PLANNED_DEFINITION_COUNT = 44
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
CANONICAL_CANDLE_INPUTS = frozenset({
    "open", "high", "low", "close", "volume", "quote_volume",
    "trade_count", "open_time", "close_time", "symbol",
})
APPROVED_FEATURE_INPUTS = frozenset({
    "feature:return_1m", "feature:return_15m",
    "feature:realized_volatility_20m", "feature:efficiency_ratio_20m",
    "feature:sma_spread_5_20", "feature:momentum_balance_14",
    "feature:volume_ratio_20", "feature:true_range_pct",
    "feature:range_position_20", "feature:completed_5m_return",
})
ALLOWED_INPUTS = CANONICAL_CANDLE_INPUTS | APPROVED_FEATURE_INPUTS


class CatalogError(ValueError):
    """AF2A catalog data is malformed or violates the fixed research contract."""


class HypothesisFamily(str, Enum):
    REVERSAL_EXHAUSTION = "reversal_exhaustion"
    MOMENTUM_PATH_PERSISTENCE = "momentum_path_persistence"
    VOLATILITY_TRANSITIONS = "volatility_transitions"
    ACTIVITY_LIQUIDITY_PROXIES = "activity_liquidity_proxies"
    PRICE_ACTIVITY_DISAGREEMENT = "price_activity_disagreement"
    REGIME_CROSS_HORIZON_ASYMMETRY = "regime_cross_horizon_asymmetry"


class ExpectedDirection(str, Enum):
    CONTINUATION_UP = "continuation_up"
    CONTINUATION_DOWN = "continuation_down"
    REVERSAL_UP = "reversal_up"
    REVERSAL_DOWN = "reversal_down"
    STATE_CONDITIONING = "direction_neutral_state_conditioning"


class CatalogStatus(str, Enum):
    KILL_PRE_SCREEN = "KILL_PRE_SCREEN"
    RETAIN_AF3 = "RETAIN_AF3"
    DEFER = "DEFER"


class ResearchViability(str, Enum):
    LOW = "LOW"
    BORDERLINE = "BORDERLINE"
    PLAUSIBLE = "PLAUSIBLE"
    STRONG = "STRONG"


class SampleFeasibility(str, Enum):
    INSUFFICIENT = "insufficient"
    WEAK = "weak"
    WORKABLE = "workable"
    STRONG = "strong"


class ComplexityPenalty(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Af1Feasibility(str, Enum):
    DIRECT = "direct_single_direction"
    PAIRED_DIRECTIONS = "requires_predeclared_paired_direction_definitions"
    CONDITION_ONLY = "condition_only_not_standalone_af1_hypothesis"


JsonScalar = str | int | bool | None


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip():
        raise CatalogError(f"{name} must be non-empty text")
    return value


def _identifier(value: object, name: str) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise CatalogError(f"{name} must be a stable lowercase identifier")
    return value


def _enum(enum_type, value: object, name: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as error:
        raise CatalogError(f"{name} is invalid") from error


def _decimal(value: object, name: str) -> Decimal:
    if type(value) is not str:
        raise CatalogError(f"{name} must be a canonical Decimal string")
    try:
        with af1_decimal_context():
            parsed = Decimal(value)
    except DecimalException as error:
        raise CatalogError(f"{name} is invalid") from error
    if not parsed.is_finite():
        raise CatalogError(f"{name} must be finite")
    return parsed


@dataclass(frozen=True, slots=True)
class ParameterSet:
    label: str
    values: Mapping[str, JsonScalar]
    rationale: str

    def __post_init__(self) -> None:
        _identifier(self.label, "parameter-set label")
        _text(self.rationale, "parameter-set rationale")
        if not isinstance(self.values, Mapping) or not self.values:
            raise CatalogError("parameter-set values must be a non-empty mapping")
        copied: dict[str, JsonScalar] = {}
        for key, value in self.values.items():
            _identifier(key, "parameter name")
            if type(value) not in (str, int, bool, type(None)):
                raise CatalogError("parameter values must be JSON scalar metadata")
            copied[key] = value
        object.__setattr__(self, "values", MappingProxyType(dict(sorted(copied.items()))))

    @classmethod
    def from_dict(cls, value: object) -> ParameterSet:
        if type(value) is not dict or set(value) != {"label", "values", "rationale"}:
            raise CatalogError("parameter set shape is invalid")
        return cls(value["label"], value["values"], value["rationale"])

    def as_dict(self) -> dict[str, object]:
        return {"label": self.label, "values": dict(self.values), "rationale": self.rationale}


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    stable_id: str
    family: HypothesisFamily
    title: str
    economic_rationale: str
    expected_direction: ExpectedDirection
    executable_directions: tuple[ExpectedDirection, ...]
    causal_inputs: tuple[str, ...]
    causal_formula: str
    lookback_minutes: int
    parameter_sets: tuple[ParameterSet, ...]
    expected_event_frequency: str
    minimum_cost_multiple: Decimal
    plausible_gross_effect_assessment: str
    sample_feasibility: SampleFeasibility
    primary_failure_mechanism: str
    overlap_redundancy_notes: str
    af1_feasibility: Af1Feasibility
    explainability_score: int
    complexity_penalty: ComplexityPenalty
    status: CatalogStatus
    reason: str
    research_viability: ResearchViability

    def __post_init__(self) -> None:
        _identifier(self.stable_id, "stable_id")
        for value, enum_type, name in (
            (self.family, HypothesisFamily, "family"),
            (self.expected_direction, ExpectedDirection, "expected_direction"),
            (self.sample_feasibility, SampleFeasibility, "sample_feasibility"),
            (self.af1_feasibility, Af1Feasibility, "af1_feasibility"),
            (self.complexity_penalty, ComplexityPenalty, "complexity_penalty"),
            (self.status, CatalogStatus, "status"),
            (self.research_viability, ResearchViability, "research_viability"),
        ):
            if type(value) is not enum_type:
                raise CatalogError(f"{name} must be {enum_type.__name__}")
        for value, name in (
            (self.title, "title"),
            (self.economic_rationale, "economic_rationale"),
            (self.causal_formula, "causal_formula"),
            (self.expected_event_frequency, "expected_event_frequency"),
            (self.plausible_gross_effect_assessment, "plausible_gross_effect_assessment"),
            (self.primary_failure_mechanism, "primary_failure_mechanism"),
            (self.overlap_redundancy_notes, "overlap_redundancy_notes"),
            (self.reason, "reason"),
        ):
            _text(value, name)
        if type(self.executable_directions) is not tuple or any(
            type(value) is not ExpectedDirection
            or value is ExpectedDirection.STATE_CONDITIONING
            for value in self.executable_directions
        ):
            raise CatalogError("executable_directions must contain directional values")
        if len(set(self.executable_directions)) != len(self.executable_directions):
            raise CatalogError("executable_directions must be unique")
        if type(self.causal_inputs) is not tuple or not self.causal_inputs:
            raise CatalogError("causal_inputs must be a non-empty tuple")
        if len(set(self.causal_inputs)) != len(self.causal_inputs):
            raise CatalogError("causal_inputs must be unique")
        if any(value not in ALLOWED_INPUTS for value in self.causal_inputs):
            raise CatalogError("causal_inputs contain unavailable Candle data or features")
        normalized_formula = self.causal_formula.replace(" ", "").lower()
        if "t+" in normalized_formula or "future" in normalized_formula:
            raise CatalogError("causal_formula must not reference future information")
        if type(self.lookback_minutes) is not int or self.lookback_minutes < 1:
            raise CatalogError("lookback_minutes must be positive")
        if type(self.parameter_sets) is not tuple or not 1 <= len(self.parameter_sets) <= 3:
            raise CatalogError("each entry requires one to three predeclared parameter sets")
        if any(type(item) is not ParameterSet for item in self.parameter_sets):
            raise CatalogError("parameter_sets must contain ParameterSet values")
        labels = tuple(item.label for item in self.parameter_sets)
        if len(set(labels)) != len(labels):
            raise CatalogError("parameter-set labels must be unique")
        parameter_names = {
            name for parameter_set in self.parameter_sets for name in parameter_set.values
        }
        formula_tokens = set(re.findall(r"[a-z][a-z0-9_]*", self.causal_formula.lower()))
        if not parameter_names.issubset(formula_tokens):
            raise CatalogError("causal_formula must reference every declared parameter")
        if type(self.minimum_cost_multiple) is not Decimal or (
            not self.minimum_cost_multiple.is_finite() or self.minimum_cost_multiple <= 1
        ):
            raise CatalogError("minimum_cost_multiple must be a finite Decimal above one")
        if type(self.explainability_score) is not int or not 0 <= self.explainability_score <= 4:
            raise CatalogError("explainability_score must be an integer from zero to four")
        if self.status is CatalogStatus.RETAIN_AF3:
            if any(value not in CANONICAL_CANDLE_INPUTS for value in self.causal_inputs):
                raise CatalogError(
                    "retained AF3 causal_inputs must be canonical Candle fields"
                )
            if self.research_viability is ResearchViability.LOW:
                raise CatalogError("LOW-viability hypotheses cannot enter AF3")
            if self.af1_feasibility is Af1Feasibility.CONDITION_ONLY:
                raise CatalogError("condition-only hypotheses cannot enter AF3")
            if self.af1_feasibility is Af1Feasibility.DIRECT and (
                self.executable_directions != (self.expected_direction,)
            ):
                raise CatalogError(
                    "direct retained hypotheses require their expected direction"
                )
            if self.af1_feasibility is Af1Feasibility.PAIRED_DIRECTIONS and (
                self.expected_direction is not ExpectedDirection.STATE_CONDITIONING
                or len(self.executable_directions) != 2
            ):
                raise CatalogError(
                    "paired retained hypotheses require two executable directions"
                )
        elif self.executable_directions:
            raise CatalogError("non-retained hypotheses cannot declare executable directions")

    @property
    def minimum_gross_effect(self) -> Decimal:
        with af1_decimal_context():
            return ROUND_TRIP_COST_FLOOR * self.minimum_cost_multiple

    @property
    def catalog_entry_sha256(self) -> str:
        """Digest the complete canonical catalog entry semantics."""
        return sha256(canonical_json_bytes(self.as_dict())).hexdigest()

    @classmethod
    def from_dict(cls, value: object) -> CatalogEntry:
        fields = {
            "stable_id", "family", "title", "economic_rationale",
            "expected_direction", "executable_directions",
            "causal_inputs", "causal_formula",
            "lookback_minutes", "parameter_sets", "expected_event_frequency",
            "minimum_cost_multiple", "minimum_gross_effect",
            "plausible_gross_effect_assessment", "sample_feasibility",
            "primary_failure_mechanism", "overlap_redundancy_notes",
            "af1_feasibility", "explainability_score", "complexity_penalty",
            "status", "reason", "research_viability",
        }
        if type(value) is not dict or set(value) != fields:
            raise CatalogError("hypothesis entry shape is invalid")
        inputs = value["causal_inputs"]
        parameters = value["parameter_sets"]
        directions = value["executable_directions"]
        if (
            type(inputs) is not list
            or type(parameters) is not list
            or type(directions) is not list
        ):
            raise CatalogError("hypothesis sequence fields are invalid")
        entry = cls(
            stable_id=value["stable_id"],
            family=_enum(HypothesisFamily, value["family"], "family"),
            title=value["title"],
            economic_rationale=value["economic_rationale"],
            expected_direction=_enum(
                ExpectedDirection, value["expected_direction"], "expected_direction"
            ),
            executable_directions=tuple(
                _enum(ExpectedDirection, item, "executable_direction")
                for item in directions
            ),
            causal_inputs=tuple(inputs),
            causal_formula=value["causal_formula"],
            lookback_minutes=value["lookback_minutes"],
            parameter_sets=tuple(ParameterSet.from_dict(item) for item in parameters),
            expected_event_frequency=value["expected_event_frequency"],
            minimum_cost_multiple=_decimal(
                value["minimum_cost_multiple"], "minimum_cost_multiple"
            ),
            plausible_gross_effect_assessment=value["plausible_gross_effect_assessment"],
            sample_feasibility=_enum(
                SampleFeasibility, value["sample_feasibility"], "sample_feasibility"
            ),
            primary_failure_mechanism=value["primary_failure_mechanism"],
            overlap_redundancy_notes=value["overlap_redundancy_notes"],
            af1_feasibility=_enum(
                Af1Feasibility, value["af1_feasibility"], "af1_feasibility"
            ),
            explainability_score=value["explainability_score"],
            complexity_penalty=_enum(
                ComplexityPenalty, value["complexity_penalty"], "complexity_penalty"
            ),
            status=_enum(CatalogStatus, value["status"], "status"),
            reason=value["reason"],
            research_viability=_enum(
                ResearchViability, value["research_viability"], "research_viability"
            ),
        )
        if value["minimum_gross_effect"] != str(entry.minimum_gross_effect):
            raise CatalogError("minimum_gross_effect does not match the cost multiple")
        return entry

    def as_dict(self) -> dict[str, object]:
        return {
            "stable_id": self.stable_id,
            "family": self.family.value,
            "title": self.title,
            "economic_rationale": self.economic_rationale,
            "expected_direction": self.expected_direction.value,
            "executable_directions": [
                direction.value for direction in self.executable_directions
            ],
            "causal_inputs": list(self.causal_inputs),
            "causal_formula": self.causal_formula,
            "lookback_minutes": self.lookback_minutes,
            "parameter_sets": [item.as_dict() for item in self.parameter_sets],
            "expected_event_frequency": self.expected_event_frequency,
            "minimum_cost_multiple": str(self.minimum_cost_multiple),
            "minimum_gross_effect": str(self.minimum_gross_effect),
            "plausible_gross_effect_assessment": self.plausible_gross_effect_assessment,
            "sample_feasibility": self.sample_feasibility.value,
            "primary_failure_mechanism": self.primary_failure_mechanism,
            "overlap_redundancy_notes": self.overlap_redundancy_notes,
            "af1_feasibility": self.af1_feasibility.value,
            "explainability_score": self.explainability_score,
            "complexity_penalty": self.complexity_penalty.value,
            "status": self.status.value,
            "reason": self.reason,
            "research_viability": self.research_viability.value,
        }


@dataclass(frozen=True, slots=True)
class HypothesisCatalog:
    entries: tuple[CatalogEntry, ...]
    catalog_id: str
    cost_assumption: Mapping[str, object]

    def __post_init__(self) -> None:
        if type(self.entries) is not tuple or len(self.entries) != 36:
            raise CatalogError("AF2A requires exactly 36 hypotheses")
        if any(type(item) is not CatalogEntry for item in self.entries):
            raise CatalogError("catalog entries must be CatalogEntry values")
        ids = tuple(item.stable_id for item in self.entries)
        if len(set(ids)) != len(ids) or ids != tuple(sorted(ids)):
            raise CatalogError("catalog stable IDs must be unique and canonically ordered")
        family_counts = {family: 0 for family in HypothesisFamily}
        for entry in self.entries:
            family_counts[entry.family] += 1
        if set(family_counts.values()) != {6}:
            raise CatalogError("AF2A requires exactly six entries in each family")
        if type(self.catalog_id) is not str or _SHA256.fullmatch(self.catalog_id) is None:
            raise CatalogError("catalog_id must be a canonical SHA-256")
        object.__setattr__(
            self, "cost_assumption", MappingProxyType(dict(self.cost_assumption))
        )

    @classmethod
    def from_document(cls, document: object) -> HypothesisCatalog:
        fields = {
            "schema_version", "catalog_source", "research_only",
            "af1_empirical_screen_executed", "cost_assumption",
            "hypotheses", "catalog_id",
        }
        if type(document) is not dict or set(document) != fields:
            raise CatalogError("catalog document shape is invalid")
        if document["schema_version"] != AF2A_SCHEMA_VERSION:
            raise CatalogError("catalog schema_version mismatch")
        if document["catalog_source"] != CATALOG_SOURCE:
            raise CatalogError("catalog must be the fixed human-directed source")
        if document["research_only"] is not True:
            raise CatalogError("catalog must remain research-only")
        if document["af1_empirical_screen_executed"] is not False:
            raise CatalogError("AF2A must not claim an AF1 empirical screen")
        cost = document["cost_assumption"]
        expected_cost = {
            "source": "phase-4c-r1-r6-experiment-specs",
            "one_way_fee_rate": str(ONE_WAY_FEE_RATE),
            "one_way_slippage_rate": str(ONE_WAY_SLIPPAGE_RATE),
            "round_trip_cost_floor": str(ROUND_TRIP_COST_FLOOR),
            "mathematical_gross_break_even_hurdle": str(ROUND_TRIP_COST_FLOOR),
            "conservative_research_margin_multiple": str(
                CONSERVATIVE_RESEARCH_MARGIN_MULTIPLE
            ),
            "conservative_research_margin": str(CONSERVATIVE_RESEARCH_MARGIN),
            "research_margin_is_mathematical_break_even": False,
            "unit": "simple_return_fraction_of_entry_notional",
        }
        if cost != expected_cost:
            raise CatalogError("catalog cost assumption is not the preserved Phase 4C model")
        hypotheses = document["hypotheses"]
        if type(hypotheses) is not list:
            raise CatalogError("catalog hypotheses must be a list")
        entries = tuple(CatalogEntry.from_dict(item) for item in hypotheses)
        identity = dict(document)
        claimed_id = identity.pop("catalog_id")
        expected_id = sha256(canonical_json_bytes(identity)).hexdigest()
        if claimed_id != expected_id:
            raise CatalogError("catalog identity mismatch")
        catalog = cls(entries, claimed_id, cost)
        if catalog.as_dict() != document:
            raise CatalogError("catalog document is not canonical")
        return catalog

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": AF2A_SCHEMA_VERSION,
            "catalog_source": CATALOG_SOURCE,
            "research_only": True,
            "af1_empirical_screen_executed": False,
            "cost_assumption": dict(self.cost_assumption),
            "hypotheses": [item.as_dict() for item in self.entries],
            "catalog_id": self.catalog_id,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def entries_with_status(self, status: CatalogStatus) -> tuple[CatalogEntry, ...]:
        if type(status) is not CatalogStatus:
            raise CatalogError("status must be CatalogStatus")
        return tuple(item for item in self.entries if item.status is status)

    def af3_entries(self) -> tuple[CatalogEntry, ...]:
        return self.entries_with_status(CatalogStatus.RETAIN_AF3)

    def status_document(self, status: CatalogStatus) -> dict[str, object]:
        candidates = [
            {
                "stable_id": item.stable_id,
                "title": item.title,
                "family": item.family.value,
                "research_viability": item.research_viability.value,
                "reason": item.reason,
            }
            for item in self.entries_with_status(status)
        ]
        identity = {
            "schema_version": AF2A_LIST_SCHEMA_VERSION,
            "catalog_id": self.catalog_id,
            "status": status.value,
            "candidates": candidates,
        }
        return {**identity, "list_id": sha256(canonical_json_bytes(identity)).hexdigest()}


def load_catalog_bytes(payload: bytes) -> HypothesisCatalog:
    """Parse and verify the fixed machine-readable AF2A catalog."""
    import json

    if type(payload) is not bytes:
        raise CatalogError("catalog payload must be bytes")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogError("catalog is not valid UTF-8 JSON") from error
    catalog = HypothesisCatalog.from_document(document)
    if catalog.canonical_bytes() != payload:
        raise CatalogError("catalog bytes are not canonical JSON")
    return catalog


def derive_fdr_plan(catalog: HypothesisCatalog) -> dict[str, object]:
    """Derive the complete AF2A planning universe from the canonical catalog."""
    if type(catalog) is not HypothesisCatalog:
        raise CatalogError("FDR-plan derivation requires HypothesisCatalog")
    catalog_identity = catalog.as_dict()
    claimed_catalog_id = catalog_identity.pop("catalog_id")
    if sha256(canonical_json_bytes(catalog_identity)).hexdigest() != claimed_catalog_id:
        raise CatalogError("cannot derive an FDR plan from an invalid catalog identity")

    retained = catalog.af3_entries()
    if len(retained) != EXPECTED_RETAINED_CONCEPT_COUNT:
        raise CatalogError("AF2A retained concept count changed unexpectedly")
    planned_definitions: list[dict[str, object]] = []
    for entry in retained:
        for parameter_set in entry.parameter_sets:
            for direction in entry.executable_directions:
                definition_identity = {
                    "planning_identity_version": AF2A_PLANNED_DEFINITION_VERSION,
                    "catalog_id": catalog.catalog_id,
                    "catalog_entry_sha256": entry.catalog_entry_sha256,
                    "concept_stable_id": entry.stable_id,
                    "family": entry.family.value,
                    "parameter_set_label": parameter_set.label,
                    "parameters": dict(parameter_set.values),
                    "direction": direction.value,
                    "causal_lookback": entry.lookback_minutes,
                    "required_inputs": list(entry.causal_inputs),
                    "causal_formula": entry.causal_formula,
                    "research_viability": entry.research_viability.value,
                }
                planned_definitions.append({
                    **definition_identity,
                    "planned_definition_sha256": sha256(
                        canonical_json_bytes(definition_identity)
                    ).hexdigest(),
                })
    if len(planned_definitions) != EXPECTED_PLANNED_DEFINITION_COUNT:
        raise CatalogError("AF2A planned definition count must remain exactly 44")
    definition_ids = tuple(
        item["planned_definition_sha256"] for item in planned_definitions
    )
    if len(set(definition_ids)) != len(definition_ids):
        raise CatalogError("AF2A planned definition identities must be unique")

    planned_tests = [
        {
            "planned_definition_sha256": definition["planned_definition_sha256"],
            "symbol": symbol,
            "horizon_minutes": horizon,
        }
        for definition in planned_definitions
        for symbol in AF1_SYMBOLS
        for horizon in AF1_HORIZONS_MINUTES
    ]
    expected_test_count = (
        EXPECTED_PLANNED_DEFINITION_COUNT
        * len(AF1_SYMBOLS)
        * len(AF1_HORIZONS_MINUTES)
    )
    if len(planned_tests) != expected_test_count:
        raise CatalogError("AF2A planned registered-test count is inconsistent")
    test_keys = {
        (
            item["planned_definition_sha256"],
            item["symbol"],
            item["horizon_minutes"],
        )
        for item in planned_tests
    }
    if len(test_keys) != len(planned_tests):
        raise CatalogError("AF2A planned registered-test keys must be unique")
    identity = {
        "schema_version": AF2A_FDR_PLAN_SCHEMA_VERSION,
        "derivation_version": AF2A_FDR_PLAN_DERIVATION_VERSION,
        "catalog_id": catalog.catalog_id,
        "retained_concept_count": len(retained),
        "planned_definition_count": len(planned_definitions),
        "symbols": list(AF1_SYMBOLS),
        "horizons_minutes": list(AF1_HORIZONS_MINUTES),
        "planned_registered_test_count": len(planned_tests),
        "planned_definitions": planned_definitions,
        "planned_registered_tests": planned_tests,
        "af3_materialization_gate": {
            "required_before_af1_screen": True,
            "one_to_one_planned_definition_coverage": True,
            "extra_executable_hypotheses_allowed": False,
            "genuine_af1_definition_sha256_required": True,
            "exact_registered_test_count_required": expected_test_count,
            "semantic_mismatch_behavior": "fail_closed",
            "identity_distinction": (
                "planned_definition_sha256_is_an_AF2A_planning_identity; "
                "AF3_must_create_and_verify_each_genuine_AF1_"
                "HypothesisMetadata.definition_sha256"
            ),
        },
    }
    return {
        **identity,
        "fdr_plan_id": sha256(canonical_json_bytes(identity)).hexdigest(),
    }


def _parse_fdr_plan_bytes(payload: bytes) -> dict[str, object]:
    import json

    if type(payload) is not bytes:
        raise CatalogError("FDR-plan payload must be bytes")

    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CatalogError("FDR-plan JSON contains duplicate keys")
            result[key] = value
        return result

    try:
        document = json.loads(
            payload.decode("utf-8"), object_pairs_hook=reject_duplicates
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogError("FDR plan is not valid UTF-8 JSON") from error
    if type(document) is not dict:
        raise CatalogError("FDR plan must be a JSON object")
    return document


def load_and_verify_fdr_plan(
    catalog_payload: bytes, plan_payload: bytes,
) -> Mapping[str, object]:
    """Verify a persisted plan by exact equality with catalog-derived semantics."""
    catalog = load_catalog_bytes(catalog_payload)
    persisted = _parse_fdr_plan_bytes(plan_payload)
    claimed_plan_id = persisted.get("fdr_plan_id")
    if type(claimed_plan_id) is not str or _SHA256.fullmatch(claimed_plan_id) is None:
        raise CatalogError("FDR-plan ID must be a canonical SHA-256")
    identity = dict(persisted)
    identity.pop("fdr_plan_id")
    if sha256(canonical_json_bytes(identity)).hexdigest() != claimed_plan_id:
        raise CatalogError("FDR-plan identity mismatch")
    expected = derive_fdr_plan(catalog)
    if persisted != expected:
        raise CatalogError("persisted FDR plan differs from the canonical catalog")
    return MappingProxyType(expected)


def safe_ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """Return an exact AF2A ratio, or None for zero/invalid denominators."""
    if (
        type(numerator) is not Decimal
        or type(denominator) is not Decimal
        or not numerator.is_finite()
        or not denominator.is_finite()
        or denominator == 0
    ):
        return None
    try:
        with af1_decimal_context():
            value = numerator / denominator
    except DecimalException:
        return None
    return value if value.is_finite() else None


def simple_return(current: Decimal, previous: Decimal) -> Decimal | None:
    ratio = safe_ratio(current, previous)
    if ratio is None:
        return None
    with af1_decimal_context():
        return ratio - Decimal(1)


def close_location(close: Decimal, low: Decimal, high: Decimal) -> Decimal | None:
    with af1_decimal_context():
        return safe_ratio(close - low, high - low)


def wick_fractions(
    open_price: Decimal, high: Decimal, low: Decimal, close: Decimal,
) -> tuple[Decimal, Decimal] | None:
    with af1_decimal_context():
        width = high - low
        upper = safe_ratio(high - max(open_price, close), width)
        lower = safe_ratio(min(open_price, close) - low, width)
    return None if upper is None or lower is None else (upper, lower)


def path_efficiency(closes: Sequence[Decimal]) -> Decimal | None:
    if type(closes) not in (tuple, list) or len(closes) < 2:
        return None
    if any(type(value) is not Decimal or not value.is_finite() for value in closes):
        return None
    with af1_decimal_context():
        path = sum(
            (abs(current - previous) for previous, current in zip(closes, closes[1:])),
            Decimal(0),
        )
        return safe_ratio(abs(closes[-1] - closes[0]), path)


def average_trade_notional(quote_volume: Decimal, trade_count: int) -> Decimal | None:
    if type(trade_count) is not int or trade_count <= 0:
        return None
    return safe_ratio(quote_volume, Decimal(trade_count))


def relative_activity(current: Decimal, history: Sequence[Decimal]) -> Decimal | None:
    if type(history) not in (tuple, list) or not history:
        return None
    if any(type(value) is not Decimal or not value.is_finite() for value in history):
        return None
    with af1_decimal_context():
        mean = sum(history, Decimal(0)) / Decimal(len(history))
        return safe_ratio(current, mean)


def root_mean_square_return(closes: Sequence[Decimal]) -> Decimal | None:
    """RMS of exact simple returns; zero is valid when every return is zero."""
    if type(closes) not in (tuple, list) or len(closes) < 2:
        return None
    returns = tuple(
        simple_return(current, previous)
        for previous, current in zip(closes, closes[1:])
    )
    if any(value is None for value in returns):
        return None
    try:
        with af1_decimal_context():
            return (
                sum((value * value for value in returns), Decimal(0))
                / Decimal(len(returns))
            ).sqrt()
    except DecimalException:
        return None


def c3_tail_direction(
    location: Decimal, close_tail: Decimal, candle_return: Decimal,
) -> ExpectedDirection | None:
    """Map an accepted directional candle tail to its continuation direction."""
    if any(
        type(value) is not Decimal or not value.is_finite()
        for value in (location, close_tail, candle_return)
    ) or not Decimal("0.5") < close_tail <= Decimal(1):
        return None
    if candle_return > 0 and location >= close_tail:
        return ExpectedDirection.CONTINUATION_UP
    with af1_decimal_context():
        lower_tail = Decimal(1) - close_tail
    if candle_return < 0 and location <= lower_tail:
        return ExpectedDirection.CONTINUATION_DOWN
    return None


def reversal_from_prior_pressure(
    prior_pressure: Decimal, pressure_min: Decimal,
) -> ExpectedDirection | None:
    """Map fixed signed prior pressure to a predeclared reversal direction."""
    if any(
        type(value) is not Decimal or not value.is_finite()
        for value in (prior_pressure, pressure_min)
    ) or pressure_min <= 0:
        return None
    if prior_pressure <= -pressure_min:
        return ExpectedDirection.REVERSAL_UP
    if prior_pressure >= pressure_min:
        return ExpectedDirection.REVERSAL_DOWN
    return None


def nearest_rank_quantile(
    prior_values: Sequence[Decimal], quantile: Decimal, minimum_observations: int,
) -> Decimal | None:
    """Deterministic ceil(N*q) nearest-rank quantile over prior observations."""
    if (
        type(prior_values) not in (tuple, list)
        or type(quantile) is not Decimal
        or not quantile.is_finite()
        or not Decimal(0) < quantile <= Decimal(1)
        or type(minimum_observations) is not int
        or minimum_observations < 1
        or len(prior_values) < minimum_observations
        or any(
            type(value) is not Decimal or not value.is_finite()
            for value in prior_values
        )
    ):
        return None
    with af1_decimal_context():
        rank = int(
            (Decimal(len(prior_values)) * quantile).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
    return sorted(prior_values)[rank - 1]


def activity_concentration(
    current_volume: Decimal,
    prior_volumes: Sequence[Decimal],
    current_normalized_range: Decimal,
    prior_normalized_ranges: Sequence[Decimal],
) -> Decimal | None:
    """Dimensionless normalized-volume / normalized-range concentration."""
    normalized_volume = relative_activity(current_volume, prior_volumes)
    normalized_range = relative_activity(
        current_normalized_range, prior_normalized_ranges
    )
    if normalized_volume is None or normalized_range is None:
        return None
    return safe_ratio(normalized_volume, normalized_range)


def quote_volume_per_absolute_return_proxy(
    normalized_quote_volume: Decimal, absolute_return: Decimal,
) -> Decimal | None:
    """Return normalized quote volume per absolute return, not order flow."""
    return safe_ratio(normalized_quote_volume, absolute_return)


def gross_expectancy(
    win_probability: Decimal, average_win: Decimal, average_loss: Decimal,
) -> Decimal | None:
    """Expected gross simple return for positive win/loss magnitudes."""
    if any(
        type(value) is not Decimal or not value.is_finite()
        for value in (win_probability, average_win, average_loss)
    ) or (
        not Decimal(0) <= win_probability <= Decimal(1)
        or average_win < 0
        or average_loss < 0
    ):
        return None
    with af1_decimal_context():
        return (
            win_probability * average_win
            - (Decimal(1) - win_probability) * average_loss
        )


def multiplicative_net_expectancy(
    gross: Decimal, cost: Decimal = ROUND_TRIP_COST_FLOOR,
) -> Decimal | None:
    """Apply the preserved multiplicative round-trip cost convention."""
    if any(
        type(value) is not Decimal or not value.is_finite()
        for value in (gross, cost)
    ):
        return None
    with af1_decimal_context():
        return safe_ratio(gross - cost, Decimal(1) + cost)


def break_even_win_rate(
    average_win: Decimal,
    average_loss: Decimal,
    cost: Decimal = ROUND_TRIP_COST_FLOOR,
) -> Decimal | None:
    """Win probability that makes gross expectancy equal the cost hurdle."""
    if any(
        type(value) is not Decimal or not value.is_finite()
        for value in (average_win, average_loss, cost)
    ) or average_win < 0 or average_loss < 0:
        return None
    with af1_decimal_context():
        return safe_ratio(average_loss + cost, average_win + average_loss)

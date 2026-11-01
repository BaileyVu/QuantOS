"""Deterministic, research-only Phase 4C alpha-discovery screening.

The funnel evaluates hypotheses supplied by a human researcher. A hypothesis
callback receives only a fixed lookback tuple ending at the completed decision
candle; forward candles are held by the evaluator and used only for outcomes.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
from enum import Enum
import hashlib
import hmac
import json
import math
import re
import secrets
from types import MappingProxyType
from typing import Any

from quantos.domain.market_data.contracts import DatasetValidationStatus
from quantos.domain.market_data.validation import ValidatedCandleSequence


DEFAULT_HORIZONS_MINUTES = (1, 3, 5, 10, 15, 30, 60)
AF1_SCHEMA_VERSION = "alpha-discovery-funnel-af1-v1"
AF1_NUMERICS_VERSION = "af1-decimal-50-half-even-tcdf-v1"
AF1_CANDLE_DIGEST_VERSION = "af1-canonical-candles-v1"
COST_RATE_UNIT = "simple_return_fraction_of_entry_notional"
UNDEFINED_P_VALUE_POLICY = "registered_undefined_p_values_count_as_one"
PROMOTE_MEANING = "eligible for human review for deeper research"
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_AF1_DECIMAL_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)


class AlphaFunnelError(ValueError):
    """AF1 input cannot be evaluated deterministically and safely."""


class DatasetRole(str, Enum):
    DEVELOPMENT = "development"
    SCREENING_VALIDATION = "screening_validation"
    SEALED_OOS = "sealed_oos"


class Direction(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


class CostKind(str, Enum):
    BASE = "base"
    STRESS = "stress"


class ResearchClassification(str, Enum):
    KILL = "KILL"
    WATCH = "WATCH"
    PROMOTE = "PROMOTE"


JsonScalar = str | int | bool | None
HypothesisEvaluator = Callable[[tuple[Any, ...]], bool]


def af1_decimal_context():
    """Return an isolated copy of AF1's deterministic arithmetic context."""
    return localcontext(_AF1_DECIMAL_CONTEXT)


def _require_identifier(value: str, name: str) -> None:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise AlphaFunnelError(f"{name} must match {_IDENTIFIER.pattern}")


def _require_text(value: str, name: str) -> None:
    if type(value) is not str or not value.strip():
        raise AlphaFunnelError(f"{name} must be non-empty text")


def _require_decimal(value: Decimal, name: str) -> None:
    if type(value) is not Decimal or not value.is_finite():
        raise AlphaFunnelError(f"{name} must be a finite built-in Decimal")


def _metadata(value: Mapping[str, JsonScalar], name: str) -> dict[str, JsonScalar]:
    if not isinstance(value, Mapping):
        raise AlphaFunnelError(f"{name} must be a mapping")
    result: dict[str, JsonScalar] = {}
    for key, item in value.items():
        _require_identifier(key, f"{name} key")
        if type(item) not in (str, int, bool, type(None)):
            raise AlphaFunnelError(f"{name} values must be strings, integers, booleans, or null")
        result[key] = item
    return MappingProxyType(dict(sorted(result.items())))


@dataclass(frozen=True, slots=True)
class HypothesisMetadata:
    stable_id: str
    implementation_id: str
    family: str
    description: str
    direction: Direction
    interpretation: str
    required_inputs: tuple[str, ...]
    parameters: Mapping[str, JsonScalar]
    causal_lookback: int
    parameter_neighborhood: Mapping[str, JsonScalar] = field(default_factory=dict)
    human_explainability_score: int | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.stable_id, "stable_id")
        _require_identifier(self.implementation_id, "implementation_id")
        _require_identifier(self.family, "family")
        _require_text(self.description, "description")
        if type(self.direction) is not Direction:
            raise AlphaFunnelError("direction must be a Direction")
        _require_text(self.interpretation, "interpretation")
        if type(self.required_inputs) is not tuple or not self.required_inputs:
            raise AlphaFunnelError("required_inputs must be a non-empty tuple")
        allowed = {
            "open", "high", "low", "close", "volume", "quote_volume", "trade_count",
            "open_time", "close_time", "symbol", "interval",
        }
        if any(type(item) is not str or item not in allowed for item in self.required_inputs):
            raise AlphaFunnelError("required_inputs contains a non-canonical candle field")
        if len(set(self.required_inputs)) != len(self.required_inputs):
            raise AlphaFunnelError("required_inputs must not contain duplicates")
        if type(self.causal_lookback) is not int or self.causal_lookback < 1:
            raise AlphaFunnelError("causal_lookback must be a positive integer")
        if self.human_explainability_score is not None and (
            type(self.human_explainability_score) is not int
            or not 0 <= self.human_explainability_score <= 4
        ):
            raise AlphaFunnelError("human_explainability_score must be null or an integer from 0 to 4")
        object.__setattr__(self, "parameters", _metadata(self.parameters, "parameters"))
        object.__setattr__(
            self,
            "parameter_neighborhood",
            _metadata(self.parameter_neighborhood, "parameter_neighborhood"),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "stable_id": self.stable_id,
            "implementation_id": self.implementation_id,
            "family": self.family,
            "description": self.description,
            "direction": self.direction.value,
            "interpretation": self.interpretation,
            "required_inputs": list(self.required_inputs),
            "parameters": dict(self.parameters),
            "causal_lookback": self.causal_lookback,
            "parameter_neighborhood": dict(self.parameter_neighborhood),
            "human_explainability_score": self.human_explainability_score,
        }

    @property
    def definition_sha256(self) -> str:
        """Digest the complete immutable hypothesis definition."""
        return hashlib.sha256(canonical_json_bytes(self.as_dict())).hexdigest()


@dataclass(frozen=True, slots=True)
class ResearchHypothesis:
    metadata: HypothesisMetadata
    evaluator: HypothesisEvaluator = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.metadata) is not HypothesisMetadata:
            raise AlphaFunnelError("metadata must be HypothesisMetadata")
        if not callable(self.evaluator):
            raise AlphaFunnelError("evaluator must be callable")


def canonical_candle_content_sha256(sequence: ValidatedCandleSequence) -> str:
    """Hash every canonical candle field used by AF1 in authoritative order."""
    if type(sequence) is not ValidatedCandleSequence:
        raise AlphaFunnelError("candle digest requires a ValidatedCandleSequence")
    document = {
        "digest_version": AF1_CANDLE_DIGEST_VERSION,
        "candles": [
            {
                "symbol": candle.symbol,
                "interval": candle.interval,
                "open_time": _timestamp(candle.open_time),
                "close_time": _timestamp(candle.close_time),
                "open": str(candle.open),
                "high": str(candle.high),
                "low": str(candle.low),
                "close": str(candle.close),
                "volume": str(candle.volume),
                "quote_volume": str(candle.quote_volume),
                "trade_count": candle.trade_count,
            }
            for candle in sequence.candles
        ],
    }
    return hashlib.sha256(canonical_json_bytes(document)).hexdigest()


@dataclass(frozen=True, slots=True)
class ResearchDataset:
    dataset_id: str
    content_sha256: str
    role: DatasetRole
    sequence: ValidatedCandleSequence = field(repr=False)

    def __post_init__(self) -> None:
        _require_identifier(self.dataset_id, "dataset_id")
        if type(self.content_sha256) is not str or _SHA256.fullmatch(self.content_sha256) is None:
            raise AlphaFunnelError("content_sha256 must be 64 lowercase hexadecimal characters")
        if type(self.role) is not DatasetRole:
            raise AlphaFunnelError("role must be a DatasetRole")
        if type(self.sequence) is not ValidatedCandleSequence:
            raise AlphaFunnelError("sequence must be a ValidatedCandleSequence")
        if self.sequence.identity.validation_status is not DatasetValidationStatus.VALIDATED:
            raise AlphaFunnelError("research datasets must be canonically validated")
        actual_digest = canonical_candle_content_sha256(self.sequence)
        if self.content_sha256 != actual_digest:
            raise AlphaFunnelError("content_sha256 does not match the canonical screened candle content")

    def identity_dict(self) -> dict[str, object]:
        identity = self.sequence.identity
        return {
            "dataset_id": self.dataset_id,
            "content_sha256": self.content_sha256,
            "role": self.role.value,
            "symbol": identity.symbol,
            "timeframe": identity.timeframe,
            "start_time": _timestamp(identity.start_time),
            "end_time": _timestamp(identity.end_time),
            "source": identity.source,
            "schema_version": identity.schema_version,
            "ingestion_version": identity.ingestion_version,
            "validation_status": identity.validation_status.value,
            "candle_count": len(self.sequence.candles),
        }


@dataclass(frozen=True, slots=True)
class FdrTestKey:
    """One predeclared statistical test and its authoritative data/role identity."""

    dataset_id: str
    dataset_content_sha256: str
    dataset_identity_sha256: str
    symbol: str
    dataset_role: DatasetRole
    hypothesis_id: str
    hypothesis_definition_sha256: str
    hypothesis_family: str
    evaluator_implementation_id: str
    direction: Direction
    horizon_minutes: int

    def __post_init__(self) -> None:
        for value, name in (
            (self.dataset_id, "FDR dataset_id"),
            (self.hypothesis_id, "FDR hypothesis_id"),
            (self.hypothesis_family, "FDR hypothesis_family"),
            (self.evaluator_implementation_id, "FDR evaluator_implementation_id"),
        ):
            _require_identifier(value, name)
        for value, name in (
            (self.dataset_content_sha256, "FDR dataset_content_sha256"),
            (self.dataset_identity_sha256, "FDR dataset_identity_sha256"),
            (
                self.hypothesis_definition_sha256,
                "FDR hypothesis_definition_sha256",
            ),
        ):
            if type(value) is not str or _SHA256.fullmatch(value) is None:
                raise AlphaFunnelError(f"{name} must be a canonical SHA-256")
        if type(self.symbol) is not str or not self.symbol:
            raise AlphaFunnelError("FDR symbol must be non-empty text")
        if type(self.dataset_role) is not DatasetRole:
            raise AlphaFunnelError("FDR dataset_role must be DatasetRole")
        if type(self.direction) is not Direction:
            raise AlphaFunnelError("FDR direction must be Direction")
        if self.horizon_minutes not in DEFAULT_HORIZONS_MINUTES:
            raise AlphaFunnelError("FDR horizon must be one of the fixed AF1 horizons")

    def sort_key(self) -> tuple[object, ...]:
        return (
            self.dataset_id,
            self.dataset_content_sha256,
            self.dataset_identity_sha256,
            self.symbol,
            self.dataset_role.value,
            self.hypothesis_id,
            self.hypothesis_definition_sha256,
            self.hypothesis_family,
            self.evaluator_implementation_id,
            self.direction.value,
            self.horizon_minutes,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_content_sha256": self.dataset_content_sha256,
            "dataset_identity_sha256": self.dataset_identity_sha256,
            "symbol": self.symbol,
            "dataset_role": self.dataset_role.value,
            "hypothesis_id": self.hypothesis_id,
            "hypothesis_definition_sha256": self.hypothesis_definition_sha256,
            "hypothesis_family": self.hypothesis_family,
            "evaluator_implementation_id": self.evaluator_implementation_id,
            "direction": self.direction.value,
            "horizon_minutes": self.horizon_minutes,
        }


@dataclass(frozen=True, slots=True)
class FdrUniverse:
    """Immutable closed family of tests from an authoritative research declaration."""

    authority_id: str
    tests: tuple[FdrTestKey, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.authority_id, "FDR authority_id")
        if type(self.tests) is not tuple or not self.tests:
            raise AlphaFunnelError("FDR universe must contain a non-empty tuple of tests")
        if any(type(item) is not FdrTestKey for item in self.tests):
            raise AlphaFunnelError("FDR universe must contain only FdrTestKey values")
        if len(set(self.tests)) != len(self.tests):
            raise AlphaFunnelError("FDR universe test keys must be unique")
        object.__setattr__(self, "tests", tuple(sorted(self.tests, key=FdrTestKey.sort_key)))

    def identity_dict(self) -> dict[str, object]:
        return {
            "schema_version": "af1-fdr-universe-v1",
            "authority_id": self.authority_id,
            "undefined_p_value_policy": UNDEFINED_P_VALUE_POLICY,
            "tests": [item.as_dict() for item in self.tests],
        }

    @property
    def universe_id(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self.identity_dict())).hexdigest()

    def as_dict(self) -> dict[str, object]:
        result = self.identity_dict()
        result["universe_id"] = self.universe_id
        return result


def _fdr_test_keys(
    datasets: Sequence[ResearchDataset], hypotheses: Sequence[ResearchHypothesis],
) -> tuple[FdrTestKey, ...]:
    keys: list[FdrTestKey] = []
    for dataset in datasets:
        identity_sha256 = hashlib.sha256(
            canonical_json_bytes(dataset.identity_dict())
        ).hexdigest()
        for hypothesis in hypotheses:
            for horizon in DEFAULT_HORIZONS_MINUTES:
                keys.append(FdrTestKey(
                    dataset_id=dataset.dataset_id,
                    dataset_content_sha256=dataset.content_sha256,
                    dataset_identity_sha256=identity_sha256,
                    symbol=dataset.sequence.identity.symbol,
                    dataset_role=dataset.role,
                    hypothesis_id=hypothesis.metadata.stable_id,
                    hypothesis_definition_sha256=(
                        hypothesis.metadata.definition_sha256
                    ),
                    hypothesis_family=hypothesis.metadata.family,
                    evaluator_implementation_id=hypothesis.metadata.implementation_id,
                    direction=hypothesis.metadata.direction,
                    horizon_minutes=horizon,
                ))
    return tuple(sorted(keys, key=FdrTestKey.sort_key))


def declare_fdr_universe(
    datasets: tuple[ResearchDataset, ...],
    hypotheses: tuple[ResearchHypothesis, ...],
    *,
    authority_id: str,
) -> FdrUniverse:
    """Predeclare the exact tests and split-role bindings for one AF1 batch."""
    if type(datasets) is not tuple or not datasets:
        raise AlphaFunnelError("FDR declaration requires a non-empty dataset tuple")
    if type(hypotheses) is not tuple or not hypotheses:
        raise AlphaFunnelError("FDR declaration requires a non-empty hypothesis tuple")
    if any(type(item) is not ResearchDataset for item in datasets):
        raise AlphaFunnelError("FDR declaration datasets must be ResearchDataset values")
    if any(type(item) is not ResearchHypothesis for item in hypotheses):
        raise AlphaFunnelError("FDR declaration hypotheses must be ResearchHypothesis values")
    return FdrUniverse(authority_id=authority_id, tests=_fdr_test_keys(datasets, hypotheses))


@dataclass(frozen=True, slots=True)
class CostScenario:
    name: str
    kind: CostKind
    round_trip_rate: Decimal

    def __post_init__(self) -> None:
        _require_identifier(self.name, "cost scenario name")
        if type(self.kind) is not CostKind:
            raise AlphaFunnelError("cost scenario kind must be CostKind")
        _require_decimal(self.round_trip_rate, "round_trip_rate")
        if not Decimal(0) <= self.round_trip_rate < Decimal(1):
            raise AlphaFunnelError(
                "round_trip_rate must be a simple-return fraction in [0, 1)"
            )

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "round_trip_rate": str(self.round_trip_rate),
            "round_trip_rate_unit": COST_RATE_UNIT,
        }


@dataclass(frozen=True, slots=True)
class ScoreBands:
    """Four ascending inclusive thresholds map one evidence input to 0..4."""

    thresholds: tuple[Decimal, Decimal, Decimal, Decimal]

    def __post_init__(self) -> None:
        if type(self.thresholds) is not tuple or len(self.thresholds) != 4:
            raise AlphaFunnelError("score thresholds must be a four-item tuple")
        for value in self.thresholds:
            _require_decimal(value, "score threshold")
        if tuple(sorted(self.thresholds)) != self.thresholds:
            raise AlphaFunnelError("score thresholds must be ascending")

    def score(self, value: Decimal | None) -> int:
        if value is None:
            return 0
        return sum(value >= threshold for threshold in self.thresholds)

    def as_list(self) -> list[str]:
        return [str(value) for value in self.thresholds]


@dataclass(frozen=True, slots=True)
class EvidenceScoringConfig:
    magnitude: ScoreBands
    statistical_strength: ScoreBands
    opportunity_frequency: ScoreBands
    robustness: ScoreBands

    def __post_init__(self) -> None:
        if any(type(value) is not ScoreBands for value in (
            self.magnitude,
            self.statistical_strength,
            self.opportunity_frequency,
            self.robustness,
        )):
            raise AlphaFunnelError("all deterministic evidence dimensions require ScoreBands")

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "M_base_cost_expectancy": self.magnitude.as_list(),
            "S_positive_t_statistic": self.statistical_strength.as_list(),
            "F_deoverlapped_events_per_day": self.opportunity_frequency.as_list(),
            "R_positive_temporal_block_ratio": self.robustness.as_list(),
        }


@dataclass(frozen=True, slots=True)
class ProvisionalClassificationConfig:
    promote_min_rvs: int
    promote_min_deoverlapped_events: int
    promote_min_base_cost_expectancy: Decimal
    kill_below_base_cost_expectancy: Decimal

    def __post_init__(self) -> None:
        if type(self.promote_min_rvs) is not int or not 0 <= self.promote_min_rvs <= 20:
            raise AlphaFunnelError("promote_min_rvs must be an integer from 0 to 20")
        if (
            type(self.promote_min_deoverlapped_events) is not int
            or self.promote_min_deoverlapped_events < 1
        ):
            raise AlphaFunnelError("promote_min_deoverlapped_events must be positive")
        _require_decimal(self.promote_min_base_cost_expectancy, "promote_min_base_cost_expectancy")
        _require_decimal(self.kill_below_base_cost_expectancy, "kill_below_base_cost_expectancy")
        if self.kill_below_base_cost_expectancy > self.promote_min_base_cost_expectancy:
            raise AlphaFunnelError("kill threshold must not exceed the promote expectancy threshold")

    def as_dict(self) -> dict[str, object]:
        return {
            "promote_min_rvs": self.promote_min_rvs,
            "promote_min_deoverlapped_events": self.promote_min_deoverlapped_events,
            "promote_min_base_cost_expectancy": str(self.promote_min_base_cost_expectancy),
            "kill_below_base_cost_expectancy": str(self.kill_below_base_cost_expectancy),
            "promote_meaning": PROMOTE_MEANING,
        }


@dataclass(frozen=True, slots=True)
class ScreeningConfig:
    code_version: str
    fdr_universe: FdrUniverse
    costs: tuple[CostScenario, ...]
    scoring: EvidenceScoringConfig
    classification: ProvisionalClassificationConfig
    random_seed: int
    bootstrap_samples: int = 2_000
    confidence_level: Decimal = Decimal("0.95")
    fdr_threshold: Decimal = Decimal("0.05")
    stability_blocks: int = 4
    minimum_stability_block_coverage: Decimal = Decimal("0.50")
    minimum_events_per_stability_block: int = 1
    minimum_positive_blocks_for_max_robustness: int = 2
    horizons_minutes: tuple[int, ...] = DEFAULT_HORIZONS_MINUTES
    allow_sealed_oos: bool = False

    def __post_init__(self) -> None:
        _require_text(self.code_version, "code_version")
        if type(self.fdr_universe) is not FdrUniverse:
            raise AlphaFunnelError("fdr_universe must be a predeclared FdrUniverse")
        if type(self.costs) is not tuple or len(self.costs) < 2:
            raise AlphaFunnelError("costs must include one base and at least one stress scenario")
        if any(type(item) is not CostScenario for item in self.costs):
            raise AlphaFunnelError("costs must contain CostScenario values")
        if len({item.name for item in self.costs}) != len(self.costs):
            raise AlphaFunnelError("cost scenario names must be unique")
        if sum(item.kind is CostKind.BASE for item in self.costs) != 1:
            raise AlphaFunnelError("costs must contain exactly one base scenario")
        if not any(item.kind is CostKind.STRESS for item in self.costs):
            raise AlphaFunnelError("costs must contain at least one stress scenario")
        base_rate = next(item.round_trip_rate for item in self.costs if item.kind is CostKind.BASE)
        if any(
            item.kind is CostKind.STRESS and item.round_trip_rate < base_rate
            for item in self.costs
        ):
            raise AlphaFunnelError("stress scenario rate must not be below the base rate")
        if type(self.scoring) is not EvidenceScoringConfig:
            raise AlphaFunnelError("scoring must be EvidenceScoringConfig")
        if type(self.classification) is not ProvisionalClassificationConfig:
            raise AlphaFunnelError("classification must be ProvisionalClassificationConfig")
        if type(self.random_seed) is not int or not 0 <= self.random_seed <= 2**63 - 1:
            raise AlphaFunnelError("random_seed must be a non-negative 63-bit integer")
        if type(self.bootstrap_samples) is not int or self.bootstrap_samples < 1:
            raise AlphaFunnelError("bootstrap_samples must be positive")
        _require_decimal(self.confidence_level, "confidence_level")
        if not Decimal(0) < self.confidence_level < Decimal(1):
            raise AlphaFunnelError("confidence_level must be in (0, 1)")
        _require_decimal(self.fdr_threshold, "fdr_threshold")
        if not Decimal(0) < self.fdr_threshold <= Decimal(1):
            raise AlphaFunnelError("fdr_threshold must be in (0, 1]")
        if type(self.stability_blocks) is not int or self.stability_blocks < 2:
            raise AlphaFunnelError("stability_blocks must be at least two")
        _require_decimal(
            self.minimum_stability_block_coverage,
            "minimum_stability_block_coverage",
        )
        if not Decimal(0) < self.minimum_stability_block_coverage <= Decimal(1):
            raise AlphaFunnelError("minimum_stability_block_coverage must be in (0, 1]")
        if (
            type(self.minimum_events_per_stability_block) is not int
            or self.minimum_events_per_stability_block < 1
        ):
            raise AlphaFunnelError("minimum_events_per_stability_block must be positive")
        if (
            type(self.minimum_positive_blocks_for_max_robustness) is not int
            or not 2
            <= self.minimum_positive_blocks_for_max_robustness
            <= self.stability_blocks
        ):
            raise AlphaFunnelError(
                "minimum_positive_blocks_for_max_robustness must be an integer "
                "from 2 through stability_blocks"
            )
        if self.horizons_minutes != DEFAULT_HORIZONS_MINUTES:
            raise AlphaFunnelError(f"AF1 horizons are fixed at {DEFAULT_HORIZONS_MINUTES}")
        if type(self.allow_sealed_oos) is not bool:
            raise AlphaFunnelError("allow_sealed_oos must be boolean")

    @property
    def batch_id(self) -> str:
        """The batch ID is the digest of the complete declared test universe."""
        return self.fdr_universe.universe_id

    @property
    def base_cost(self) -> CostScenario:
        return next(item for item in self.costs if item.kind is CostKind.BASE)

    def as_dict(self) -> dict[str, object]:
        return {
            "code_version": self.code_version,
            "batch_id": self.batch_id,
            "fdr_universe": self.fdr_universe.as_dict(),
            "costs": [item.as_dict() for item in self.costs],
            "scoring": self.scoring.as_dict(),
            "classification": self.classification.as_dict(),
            "random_seed": self.random_seed,
            "bootstrap_samples": self.bootstrap_samples,
            "confidence_level": str(self.confidence_level),
            "fdr_threshold": str(self.fdr_threshold),
            "stability_blocks": self.stability_blocks,
            "minimum_stability_block_coverage": str(
                self.minimum_stability_block_coverage
            ),
            "minimum_events_per_stability_block": self.minimum_events_per_stability_block,
            "minimum_positive_blocks_for_max_robustness": (
                self.minimum_positive_blocks_for_max_robustness
            ),
            "horizons_minutes": list(self.horizons_minutes),
            "allow_sealed_oos": self.allow_sealed_oos,
        }


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise AlphaFunnelError("canonical JSON mapping keys must be built-in strings")
            result[key] = _jsonable(item)
        return result
    if type(value) in (tuple, list):
        return [_jsonable(item) for item in value]
    if type(value) in (str, int, float, bool, type(None)):
        return value
    raise AlphaFunnelError(f"unsupported canonical JSON value: {type(value).__name__}")


def _deep_freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({
            key: _deep_freeze(item)
            for key, item in value.items()
        })
    if type(value) in (tuple, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def canonical_json_bytes(value: object) -> bytes:
    """Serialize AF1 artifacts with stable key order, separators, and newline."""
    try:
        return (
            json.dumps(
                _jsonable(value),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise AlphaFunnelError(f"value is not canonically JSON serializable: {error}") from error


def _parse_canonical_json_bytes(payload: bytes, name: str) -> object:
    if type(payload) is not bytes:
        raise AlphaFunnelError(f"{name} must be immutable bytes")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AlphaFunnelError(f"{name} is not valid UTF-8 canonical JSON") from error
    if canonical_json_bytes(value) != payload:
        raise AlphaFunnelError(f"{name} does not use canonical AF1 JSON bytes")
    return value


def _validate_screening_run_semantics(
    manifest: Mapping[str, object],
    results: Sequence[Mapping[str, object]],
) -> None:
    """Cross-check every published AF1 identity and evaluator relationship."""
    expected_manifest_fields = {
        "schema_version", "datasets", "hypotheses", "configuration",
        "decimal_policy", "causality_rule", "callback_trust_boundary",
        "timestamp_ordering_rule", "forward_outcome_rule",
        "event_deoverlap_rule", "common_support_rule", "event_frequency_rule",
        "bootstrap_generator", "research_only", "production_approved",
        "run_id", "results_sha256",
    }
    if set(manifest) != expected_manifest_fields:
        raise AlphaFunnelError("ScreeningRun manifest shape is invalid")
    if manifest.get("schema_version") != AF1_SCHEMA_VERSION:
        raise AlphaFunnelError("ScreeningRun manifest schema_version mismatch")
    if manifest.get("research_only") is not True or manifest.get("production_approved") is not False:
        raise AlphaFunnelError("ScreeningRun must be research-only and not production-approved")

    datasets = manifest.get("datasets")
    hypotheses = manifest.get("hypotheses")
    configuration = manifest.get("configuration")
    if type(datasets) is not list or not datasets:
        raise AlphaFunnelError("ScreeningRun manifest requires datasets")
    if type(hypotheses) is not list or not hypotheses:
        raise AlphaFunnelError("ScreeningRun manifest requires hypotheses")
    if type(configuration) is not dict:
        raise AlphaFunnelError("ScreeningRun manifest requires configuration")
    universe = configuration.get("fdr_universe")
    if type(universe) is not dict:
        raise AlphaFunnelError("ScreeningRun manifest requires an FDR universe")
    if set(universe) != {
        "schema_version",
        "authority_id",
        "undefined_p_value_policy",
        "tests",
        "universe_id",
    }:
        raise AlphaFunnelError("ScreeningRun FDR universe shape is invalid")
    if universe.get("schema_version") != "af1-fdr-universe-v1":
        raise AlphaFunnelError("ScreeningRun FDR universe schema mismatch")
    _require_identifier(universe.get("authority_id"), "FDR authority_id")
    if universe.get("undefined_p_value_policy") != UNDEFINED_P_VALUE_POLICY:
        raise AlphaFunnelError("ScreeningRun FDR undefined-p policy mismatch")
    tests = universe.get("tests")
    if type(tests) is not list or not tests:
        raise AlphaFunnelError("ScreeningRun FDR universe requires registered tests")

    universe_identity = dict(universe)
    universe_id = universe_identity.pop("universe_id", None)
    if type(universe_id) is not str or _SHA256.fullmatch(universe_id) is None:
        raise AlphaFunnelError("ScreeningRun FDR universe ID is invalid")
    expected_universe_id = hashlib.sha256(
        canonical_json_bytes(universe_identity)
    ).hexdigest()
    if universe_id != expected_universe_id:
        raise AlphaFunnelError("ScreeningRun FDR universe identity mismatch")
    if configuration.get("batch_id") != universe_id:
        raise AlphaFunnelError("ScreeningRun batch ID does not match its FDR universe")

    datasets_by_symbol: dict[str, dict[str, object]] = {}
    dataset_digests: dict[str, str] = {}
    dataset_fields = {
        "dataset_id", "content_sha256", "role", "symbol", "timeframe",
        "start_time", "end_time", "source", "schema_version",
        "ingestion_version", "validation_status", "candle_count",
    }
    for item in datasets:
        if type(item) is not dict or set(item) != dataset_fields:
            raise AlphaFunnelError("ScreeningRun dataset identity shape is invalid")
        _require_identifier(item.get("dataset_id"), "dataset_id")
        content_sha256 = item.get("content_sha256")
        if type(content_sha256) is not str or _SHA256.fullmatch(content_sha256) is None:
            raise AlphaFunnelError("ScreeningRun dataset content digest is invalid")
        if item.get("role") not in {role.value for role in DatasetRole}:
            raise AlphaFunnelError("ScreeningRun dataset role is invalid")
        if item.get("timeframe") != "1m":
            raise AlphaFunnelError("ScreeningRun dataset timeframe must be 1m")
        if item.get("validation_status") != DatasetValidationStatus.VALIDATED.value:
            raise AlphaFunnelError("ScreeningRun dataset must be validated")
        for field_name in (
            "symbol", "start_time", "end_time", "source",
            "schema_version", "ingestion_version",
        ):
            _require_text(item.get(field_name), f"dataset {field_name}")
        if type(item.get("candle_count")) is not int or item["candle_count"] < 1:
            raise AlphaFunnelError("ScreeningRun dataset candle_count is invalid")
        symbol = item["symbol"]
        if symbol in datasets_by_symbol:
            raise AlphaFunnelError("ScreeningRun dataset symbols must be unique strings")
        datasets_by_symbol[symbol] = item
        dataset_digests[symbol] = hashlib.sha256(
            canonical_json_bytes(item)
        ).hexdigest()
    if list(datasets_by_symbol) != sorted(datasets_by_symbol):
        raise AlphaFunnelError("ScreeningRun datasets are not canonically ordered")

    hypotheses_by_id: dict[str, dict[str, object]] = {}
    hypothesis_digests: dict[str, str] = {}
    hypothesis_fields = {
        "stable_id", "implementation_id", "family", "description", "direction",
        "interpretation", "required_inputs", "parameters", "causal_lookback",
        "parameter_neighborhood", "human_explainability_score",
    }
    for item in hypotheses:
        if type(item) is not dict or set(item) != hypothesis_fields:
            raise AlphaFunnelError("ScreeningRun hypothesis definition shape is invalid")
        if type(item.get("required_inputs")) is not list:
            raise AlphaFunnelError("ScreeningRun hypothesis required_inputs is invalid")
        try:
            metadata = HypothesisMetadata(
                stable_id=item["stable_id"],
                implementation_id=item["implementation_id"],
                family=item["family"],
                description=item["description"],
                direction=Direction(item["direction"]),
                interpretation=item["interpretation"],
                required_inputs=tuple(item["required_inputs"]),
                parameters=item["parameters"],
                causal_lookback=item["causal_lookback"],
                parameter_neighborhood=item["parameter_neighborhood"],
                human_explainability_score=item["human_explainability_score"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise AlphaFunnelError(
                "ScreeningRun hypothesis definition is invalid"
            ) from error
        if metadata.as_dict() != item:
            raise AlphaFunnelError("ScreeningRun hypothesis definition is not canonical")
        hypothesis_id = metadata.stable_id
        if hypothesis_id in hypotheses_by_id:
            raise AlphaFunnelError("ScreeningRun hypothesis IDs must be unique strings")
        hypotheses_by_id[hypothesis_id] = item
        hypothesis_digests[hypothesis_id] = metadata.definition_sha256
    if list(hypotheses_by_id) != sorted(hypotheses_by_id):
        raise AlphaFunnelError("ScreeningRun hypotheses are not canonically ordered")

    tests_by_result_key: dict[tuple[str, str, int], dict[str, object]] = {}
    actual_test_order: list[tuple[object, ...]] = []
    for item in tests:
        if type(item) is not dict:
            raise AlphaFunnelError("ScreeningRun FDR test key must be a mapping")
        symbol = item.get("symbol")
        hypothesis_id = item.get("hypothesis_id")
        horizon = item.get("horizon_minutes")
        if (
            type(symbol) is not str
            or type(hypothesis_id) is not str
            or type(horizon) is not int
            or horizon not in DEFAULT_HORIZONS_MINUTES
        ):
            raise AlphaFunnelError("ScreeningRun FDR test identity is malformed")
        dataset = datasets_by_symbol.get(symbol)
        hypothesis = hypotheses_by_id.get(hypothesis_id)
        if dataset is None or hypothesis is None:
            raise AlphaFunnelError("ScreeningRun FDR test references unknown identity")
        expected = {
            "dataset_id": dataset.get("dataset_id"),
            "dataset_content_sha256": dataset.get("content_sha256"),
            "dataset_identity_sha256": dataset_digests[symbol],
            "symbol": symbol,
            "dataset_role": dataset.get("role"),
            "hypothesis_id": hypothesis_id,
            "hypothesis_definition_sha256": hypothesis_digests[hypothesis_id],
            "hypothesis_family": hypothesis.get("family"),
            "evaluator_implementation_id": hypothesis.get("implementation_id"),
            "direction": hypothesis.get("direction"),
            "horizon_minutes": horizon,
        }
        if item != expected:
            raise AlphaFunnelError(
                "ScreeningRun FDR test does not match dataset and hypothesis provenance"
            )
        result_key = (hypothesis_id, symbol, horizon)
        if result_key in tests_by_result_key:
            raise AlphaFunnelError("ScreeningRun FDR test keys are not unique")
        tests_by_result_key[result_key] = item
        actual_test_order.append((
            item["dataset_id"],
            item["dataset_content_sha256"],
            item["dataset_identity_sha256"],
            item["symbol"],
            item["dataset_role"],
            item["hypothesis_id"],
            item["hypothesis_definition_sha256"],
            item["hypothesis_family"],
            item["evaluator_implementation_id"],
            item["direction"],
            item["horizon_minutes"],
        ))

    expected_registered_keys = {
        (hypothesis_id, symbol, horizon)
        for hypothesis_id in hypotheses_by_id
        for symbol in datasets_by_symbol
        for horizon in DEFAULT_HORIZONS_MINUTES
    }
    if set(tests_by_result_key) != expected_registered_keys:
        raise AlphaFunnelError(
            "ScreeningRun FDR universe is not the complete declared test product"
        )
    if actual_test_order != sorted(actual_test_order):
        raise AlphaFunnelError("ScreeningRun FDR tests are not canonically ordered")

    if len(results) != len(tests_by_result_key):
        raise AlphaFunnelError("ScreeningRun result count does not match its FDR universe")
    fdr_threshold_text = configuration.get("fdr_threshold")
    if type(fdr_threshold_text) is not str:
        raise AlphaFunnelError("ScreeningRun FDR threshold is invalid")
    try:
        with af1_decimal_context():
            fdr_threshold = Decimal(fdr_threshold_text)
    except DecimalException as error:
        raise AlphaFunnelError("ScreeningRun FDR threshold is invalid") from error
    if not fdr_threshold.is_finite() or not Decimal(0) < fdr_threshold <= Decimal(1):
        raise AlphaFunnelError("ScreeningRun FDR threshold is invalid")

    expected_result_order = sorted(tests_by_result_key)
    actual_result_order: list[tuple[str, str, int]] = []
    raw_p_values: list[Decimal | None] = []
    multiple_testing_records: list[dict[str, object]] = []
    for result in results:
        if type(result) is not dict:
            raise AlphaFunnelError("ScreeningRun result row must be a mapping")
        hypothesis_id = result.get("hypothesis_id")
        symbol = result.get("symbol")
        horizon = result.get("horizon_minutes")
        if type(hypothesis_id) is not str or type(symbol) is not str or type(horizon) is not int:
            raise AlphaFunnelError("ScreeningRun result identity is malformed")
        result_key = (hypothesis_id, symbol, horizon)
        test = tests_by_result_key.get(result_key)
        if test is None:
            raise AlphaFunnelError("ScreeningRun result is not registered in its FDR universe")
        for result_field, test_field in (
            ("hypothesis_definition_sha256", "hypothesis_definition_sha256"),
            ("hypothesis_family", "hypothesis_family"),
            ("evaluator_implementation_id", "evaluator_implementation_id"),
            ("direction", "direction"),
        ):
            if result.get(result_field) != test.get(test_field):
                raise AlphaFunnelError(
                    f"ScreeningRun {result_field} provenance mismatch"
                )
        multiple_testing = result.get("multiple_testing")
        if type(multiple_testing) is not dict:
            raise AlphaFunnelError("ScreeningRun result lacks multiple-testing provenance")
        if (
            multiple_testing.get("batch_id") != universe_id
            or multiple_testing.get("fdr_universe_id") != universe_id
            or multiple_testing.get("authority_id") != universe.get("authority_id")
            or multiple_testing.get("family") != result.get("hypothesis_family")
            or multiple_testing.get("registered_tests") != len(tests)
            or multiple_testing.get("undefined_p_value_policy")
            != UNDEFINED_P_VALUE_POLICY
            or multiple_testing.get("fdr_threshold") != fdr_threshold_text
            or multiple_testing.get("method") != "Benjamini-Hochberg"
        ):
            raise AlphaFunnelError(
                "ScreeningRun result multiple-testing provenance mismatch"
            )
        raw_p_value = result.get("raw_p_value")
        if raw_p_value is None:
            parsed_p_value = None
        elif type(raw_p_value) is str:
            try:
                with af1_decimal_context():
                    parsed_p_value = Decimal(raw_p_value)
            except DecimalException as error:
                raise AlphaFunnelError(
                    "ScreeningRun raw p-value is invalid"
                ) from error
            if (
                not parsed_p_value.is_finite()
                or not Decimal(0) <= parsed_p_value <= Decimal(1)
            ):
                raise AlphaFunnelError("ScreeningRun raw p-value is invalid")
        else:
            raise AlphaFunnelError("ScreeningRun raw p-value is invalid")
        raw_p_values.append(parsed_p_value)
        multiple_testing_records.append(multiple_testing)
        actual_result_order.append(result_key)
    if actual_result_order != expected_result_order:
        raise AlphaFunnelError("ScreeningRun results are not canonically ordered")

    expected_q_values = benjamini_hochberg(tuple(raw_p_values))
    calculated_tests = sum(value is not None for value in raw_p_values)
    for result, multiple_testing, q_value in zip(
        results, multiple_testing_records, expected_q_values, strict=True
    ):
        expected_q_value = None if q_value is None else str(q_value)
        passes_fdr = q_value is not None and q_value <= fdr_threshold
        if (
            multiple_testing.get("q_value") != expected_q_value
            or multiple_testing.get("passes_fdr_threshold") is not passes_fdr
            or multiple_testing.get("tests_with_calculated_p_values")
            != calculated_tests
        ):
            raise AlphaFunnelError(
                "ScreeningRun Benjamini-Hochberg result mismatch"
            )
        classification_inputs = result.get("classification_inputs")
        if (
            type(classification_inputs) is not dict
            or classification_inputs.get("q_value") != expected_q_value
            or classification_inputs.get("passes_fdr_threshold") is not passes_fdr
            or classification_inputs.get("fdr_threshold") != fdr_threshold_text
        ):
            raise AlphaFunnelError(
                "ScreeningRun classification FDR inputs mismatch"
            )


def _provenance_material(
    run_id: str, manifest_bytes: bytes, results_bytes: bytes,
) -> bytes:
    return b"AF1-screening-run-v1\0" + b"\0".join((
        run_id.encode("ascii"),
        hashlib.sha256(manifest_bytes).digest(),
        hashlib.sha256(results_bytes).digest(),
    ))


@dataclass(frozen=True, slots=True, init=False)
class ScreeningRun:
    """Deeply immutable, evaluator-proven, self-verifying AF1 result envelope."""

    run_id: str
    manifest: Mapping[str, object]
    results: tuple[Mapping[str, object], ...]
    _manifest_bytes: bytes = field(repr=False)
    _results_bytes: bytes = field(repr=False)
    _provenance_proof: bytes = field(repr=False, compare=False)

    def __init__(self) -> None:
        raise AlphaFunnelError(
            "ScreeningRun instances can only be created by screen_alpha_funnel"
        )

    def results_document(self) -> dict[str, object]:
        return {
            "schema_version": AF1_SCHEMA_VERSION,
            "run_id": self.run_id,
            "results": [_jsonable(item) for item in self.results],
        }

    def verify_integrity(self) -> None:
        if type(self.run_id) is not str or _SHA256.fullmatch(self.run_id) is None:
            raise AlphaFunnelError("ScreeningRun run_id is not a canonical SHA-256")
        manifest = _jsonable(self.manifest)
        if not isinstance(manifest, dict):
            raise AlphaFunnelError("ScreeningRun manifest must be a mapping")
        if manifest.get("run_id") != self.run_id:
            raise AlphaFunnelError("ScreeningRun manifest run_id mismatch")
        results_sha256 = manifest.get("results_sha256")
        if type(results_sha256) is not str or _SHA256.fullmatch(results_sha256) is None:
            raise AlphaFunnelError("ScreeningRun manifest results_sha256 is invalid")
        identity = dict(manifest)
        identity.pop("run_id")
        identity.pop("results_sha256")
        if hashlib.sha256(canonical_json_bytes(identity)).hexdigest() != self.run_id:
            raise AlphaFunnelError("ScreeningRun run identity mismatch")
        expected_results = canonical_json_bytes(self.results_document())
        if hashlib.sha256(expected_results).hexdigest() != results_sha256:
            raise AlphaFunnelError("ScreeningRun results SHA-256 mismatch")
        expected_manifest = canonical_json_bytes(manifest)
        if self._results_bytes != expected_results or self._manifest_bytes != expected_manifest:
            raise AlphaFunnelError("ScreeningRun canonical bytes mismatch")
        plain_results = [_jsonable(item) for item in self.results]
        if any(type(item) is not dict for item in plain_results):
            raise AlphaFunnelError("ScreeningRun results must be mappings")
        _validate_screening_run_semantics(manifest, plain_results)
        if not _verify_screening_run_provenance(self):
            raise AlphaFunnelError(
                "ScreeningRun lacks valid evaluator-issued provenance"
            )

    def manifest_bytes(self) -> bytes:
        self.verify_integrity()
        return self._manifest_bytes

    def results_bytes(self) -> bytes:
        self.verify_integrity()
        return self._results_bytes


@dataclass(frozen=True, slots=True, init=False)
class VerifiedScreeningArtifact:
    """Immutable AF1 output verified from durable canonical contents.

    Unlike ``ScreeningRun``, this value grants no evaluator publication
    authority and carries no process-private provenance proof.
    """

    run_id: str
    manifest: Mapping[str, object]
    results: tuple[Mapping[str, object], ...]
    results_sha256: str
    _manifest_bytes: bytes = field(repr=False)
    _results_bytes: bytes = field(repr=False)

    def __init__(self) -> None:
        raise AlphaFunnelError(
            "VerifiedScreeningArtifact instances can only be created by durable verification"
        )

    def manifest_bytes(self) -> bytes:
        return self._manifest_bytes

    def results_bytes(self) -> bytes:
        return self._results_bytes


def _verify_persisted_screening_artifact(
    run_id: str,
    manifest_bytes: bytes,
    results_bytes: bytes,
) -> VerifiedScreeningArtifact:
    """Verify persisted AF1 bytes without recreating evaluator provenance."""
    if type(run_id) is not str or _SHA256.fullmatch(run_id) is None:
        raise AlphaFunnelError("persisted AF1 run_id is not a canonical SHA-256")

    manifest = _parse_canonical_json_bytes(manifest_bytes, "AF1 manifest")
    results_document = _parse_canonical_json_bytes(results_bytes, "AF1 results")
    if type(manifest) is not dict:
        raise AlphaFunnelError("persisted AF1 manifest must be a mapping")
    if type(results_document) is not dict or set(results_document) != {
        "schema_version", "run_id", "results",
    }:
        raise AlphaFunnelError("persisted AF1 results document shape is invalid")
    if results_document.get("schema_version") != AF1_SCHEMA_VERSION:
        raise AlphaFunnelError("persisted AF1 results schema_version mismatch")
    if results_document.get("run_id") != run_id:
        raise AlphaFunnelError("persisted AF1 results run_id mismatch")
    results = results_document.get("results")
    if type(results) is not list or any(type(item) is not dict for item in results):
        raise AlphaFunnelError("persisted AF1 results must be a list of mappings")
    if manifest.get("run_id") != run_id:
        raise AlphaFunnelError("persisted AF1 manifest run_id mismatch")

    results_sha256 = manifest.get("results_sha256")
    if type(results_sha256) is not str or _SHA256.fullmatch(results_sha256) is None:
        raise AlphaFunnelError("persisted AF1 results_sha256 is invalid")
    if hashlib.sha256(results_bytes).hexdigest() != results_sha256:
        raise AlphaFunnelError("persisted AF1 results SHA-256 mismatch")

    identity = dict(manifest)
    identity.pop("run_id", None)
    identity.pop("results_sha256", None)
    if hashlib.sha256(canonical_json_bytes(identity)).hexdigest() != run_id:
        raise AlphaFunnelError("persisted AF1 run identity mismatch")

    _validate_screening_run_semantics(manifest, results)

    instance = object.__new__(VerifiedScreeningArtifact)
    object.__setattr__(instance, "run_id", run_id)
    object.__setattr__(instance, "manifest", _deep_freeze(manifest))
    object.__setattr__(
        instance,
        "results",
        tuple(_deep_freeze(item) for item in results),
    )
    object.__setattr__(instance, "results_sha256", results_sha256)
    object.__setattr__(instance, "_manifest_bytes", manifest_bytes)
    object.__setattr__(instance, "_results_bytes", results_bytes)
    return instance


def _assemble_screening_run(
    identity_payload: Mapping[str, object],
    results: Sequence[Mapping[str, object]],
    provenance_signer: Callable[[str, bytes, bytes], bytes],
) -> ScreeningRun:
    """Assemble a run; only the screening entrypoint possesses a valid signer."""
    identity = _jsonable(identity_payload)
    if type(identity) is not dict:
        raise AlphaFunnelError("ScreeningRun identity must be a mapping")
    run_id = hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    plain_results = [_jsonable(item) for item in results]
    if any(type(item) is not dict for item in plain_results):
        raise AlphaFunnelError("ScreeningRun results must be mappings")
    result_document = {
        "schema_version": AF1_SCHEMA_VERSION,
        "run_id": run_id,
        "results": plain_results,
    }
    results_bytes = canonical_json_bytes(result_document)
    manifest = dict(identity)
    manifest["run_id"] = run_id
    manifest["results_sha256"] = hashlib.sha256(results_bytes).hexdigest()
    manifest_bytes = canonical_json_bytes(manifest)
    _validate_screening_run_semantics(manifest, plain_results)
    proof = provenance_signer(run_id, manifest_bytes, results_bytes)
    if type(proof) is not bytes or len(proof) != hashlib.sha256().digest_size:
        raise AlphaFunnelError("AF1 provenance signer returned an invalid proof")

    instance = object.__new__(ScreeningRun)
    object.__setattr__(instance, "run_id", run_id)
    object.__setattr__(instance, "manifest", _deep_freeze(manifest))
    object.__setattr__(
        instance,
        "results",
        tuple(_deep_freeze(item) for item in plain_results),
    )
    object.__setattr__(instance, "_manifest_bytes", manifest_bytes)
    object.__setattr__(instance, "_results_bytes", results_bytes)
    object.__setattr__(instance, "_provenance_proof", proof)
    instance.verify_integrity()
    return instance

def _timestamp(value: Any) -> str:
    return value.isoformat(timespec="microseconds")


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    with af1_decimal_context():
        if not values:
            return None
        return sum(values, Decimal(0)) / Decimal(len(values))


def _median(values: Sequence[Decimal]) -> Decimal | None:
    with af1_decimal_context():
        if not values:
            return None
        ordered = sorted(values)
        midpoint = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[midpoint]
        return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


def _standard_error(values: Sequence[Decimal]) -> Decimal | None:
    with af1_decimal_context():
        if len(values) < 2:
            return None
        mean = _mean(values)
        assert mean is not None
        variance = (
            sum((value - mean) ** 2 for value in values)
            / Decimal(len(values) - 1)
        )
        return (variance / Decimal(len(values))).sqrt()


def _beta_fraction(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    tiny = 1e-300
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    result = d
    for iteration in range(1, 201):
        twice = 2 * iteration
        numerator = iteration * (b - iteration) * x / ((qam + twice) * (a + twice))
        d = 1.0 + numerator * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + numerator / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        result *= d * c
        numerator = -(a + iteration) * (qab + iteration) * x / ((a + twice) * (qap + twice))
        d = 1.0 + numerator * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + numerator / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        result *= delta
        if abs(delta - 1.0) <= 3e-14:
            return result
    raise AlphaFunnelError("Student t p-value calculation did not converge")


def _regularized_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _beta_fraction(a, b, x) / a
    return 1.0 - front * _beta_fraction(b, a, 1.0 - x) / b


def _t_statistic_and_p(
    values: Sequence[Decimal],
) -> tuple[Decimal | None, Decimal | None]:
    with af1_decimal_context():
        error = _standard_error(values)
        mean = _mean(values)
        if error is None or error == 0 or mean is None:
            return None, None
        statistic = mean / error
        degrees = len(values) - 1
        numeric = abs(float(statistic))
        x = degrees / (degrees + numeric * numeric)
        p_value = _regularized_beta(degrees / 2.0, 0.5, x)
        return statistic, Decimal(repr(min(1.0, max(0.0, p_value))))


class _SplitMix64:
    """Small fixed bootstrap generator; the algorithm is part of AF1 metadata."""

    def __init__(self, seed: int) -> None:
        self._state = seed & ((1 << 64) - 1)

    def next(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
        value = self._state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & ((1 << 64) - 1)
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & ((1 << 64) - 1)
        return value ^ (value >> 31)

    def index(self, length: int) -> int:
        return (self.next() * length) >> 64


def _quantile(values: Sequence[Decimal], probability: Decimal) -> Decimal:
    with af1_decimal_context():
        ordered = sorted(values)
        position = probability * Decimal(len(ordered) - 1)
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - Decimal(lower)
        return (
            ordered[lower] * (Decimal(1) - weight)
            + ordered[upper] * weight
        )


def _bootstrap_interval(
    values: Sequence[Decimal],
    *,
    samples: int,
    confidence: Decimal,
    seed_material: str,
) -> tuple[Decimal | None, Decimal | None]:
    with af1_decimal_context():
        if not values:
            return None, None
        seed = int.from_bytes(
            hashlib.sha256(seed_material.encode("utf-8")).digest()[:8],
            "big",
        )
        generator = _SplitMix64(seed)
        means: list[Decimal] = []
        for _ in range(samples):
            means.append(
                sum(
                    (values[generator.index(len(values))] for _ in values),
                    Decimal(0),
                )
                / Decimal(len(values))
            )
        tail = (Decimal(1) - confidence) / Decimal(2)
        return _quantile(means, tail), _quantile(
            means,
            Decimal(1) - tail,
        )


def benjamini_hochberg(p_values: Sequence[Decimal | None]) -> tuple[Decimal | None, ...]:
    """Adjust one closed universe; undefined registered tests conservatively count as p=1."""
    try:
        with af1_decimal_context():
            for value in p_values:
                if value is not None and (
                    type(value) is not Decimal or not Decimal(0) <= value <= Decimal(1)
                ):
                    raise AlphaFunnelError("p-values must be null or Decimals in [0, 1]")
            ranked = sorted(
                (
                    (value if value is not None else Decimal(1), index, value is not None)
                    for index, value in enumerate(p_values)
                ),
                key=lambda item: (item[0], item[1]),
            )
            adjusted: list[Decimal | None] = [None] * len(p_values)
            running = Decimal(1)
            total = len(ranked)
            for rank in range(total, 0, -1):
                p_value, original, is_defined = ranked[rank - 1]
                candidate = min(Decimal(1), p_value * Decimal(total) / Decimal(rank))
                running = min(running, candidate)
                if is_defined:
                    adjusted[original] = running
            return tuple(adjusted)
    except DecimalException as error:
        raise AlphaFunnelError(f"BH arithmetic failed deterministically: {error}") from error


def _deoverlapped_events(indices: Sequence[int], horizon: int) -> tuple[int, ...]:
    """Greedily retain earliest events with non-overlapping forward intervals."""
    selected: list[int] = []
    for index in indices:
        if not selected or index - selected[-1] >= horizon:
            selected.append(index)
    return tuple(selected)


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _temporal_stability(
    indexed_returns: Sequence[tuple[int, Decimal]],
    *,
    first_index: int,
    eligible_count: int,
    blocks: int,
    minimum_block_coverage: Decimal,
    minimum_events_per_block: int,
) -> tuple[dict[str, object], Decimal | None]:
    buckets: list[list[Decimal]] = [[] for _ in range(blocks)]
    for index, value in indexed_returns:
        block = min(blocks - 1, (index - first_index) * blocks // eligible_count)
        buckets[block].append(value)
    rows: list[dict[str, object]] = []
    qualified_means: list[Decimal] = []
    positive_qualified = 0
    for index, bucket in enumerate(buckets):
        mean = _mean(bucket)
        qualifies = len(bucket) >= minimum_events_per_block
        if qualifies:
            assert mean is not None
            qualified_means.append(mean)
            positive_qualified += int(mean > 0)
        rows.append({
            "block": index + 1,
            "event_count": len(bucket),
            "mean_gross_expectancy": _decimal(mean),
            "qualifies_for_robustness": qualifies,
        })
    qualified_count = len(qualified_means)
    coverage = Decimal(qualified_count) / Decimal(blocks)
    positive_qualified_ratio = (
        Decimal(positive_qualified) / Decimal(qualified_count)
        if qualified_count else None
    )
    positive_expected_ratio = Decimal(positive_qualified) / Decimal(blocks)
    robustness_evidence = (
        positive_expected_ratio if coverage >= minimum_block_coverage else None
    )
    dispersion = _standard_error(qualified_means)
    return {
        "method": "equal_contiguous_decision_index_blocks",
        "block_count": blocks,
        "minimum_events_per_qualified_block": minimum_events_per_block,
        "minimum_qualified_block_coverage": str(minimum_block_coverage),
        "qualified_block_count": qualified_count,
        "qualified_block_coverage": str(coverage),
        "positive_qualified_block_count": positive_qualified,
        "positive_qualified_block_ratio": _decimal(positive_qualified_ratio),
        "positive_expected_block_ratio": str(positive_expected_ratio),
        "robustness_evidence_ratio": _decimal(robustness_evidence),
        "minimum_qualified_block_mean": _decimal(
            min(qualified_means) if qualified_means else None
        ),
        "maximum_qualified_block_mean": _decimal(
            max(qualified_means) if qualified_means else None
        ),
        "standard_error_of_qualified_block_means": _decimal(dispersion),
        "blocks": rows,
    }, robustness_evidence


def _classify(
    *,
    deoverlapped_count: int,
    base_expectancy: Decimal | None,
    q_value: Decimal | None,
    rvs: int | None,
    classification: ProvisionalClassificationConfig,
    fdr_threshold: Decimal,
) -> ResearchClassification:
    if deoverlapped_count == 0 or base_expectancy is None:
        return ResearchClassification.KILL
    if base_expectancy < classification.kill_below_base_cost_expectancy:
        return ResearchClassification.KILL
    if (
        rvs is not None
        and rvs >= classification.promote_min_rvs
        and deoverlapped_count >= classification.promote_min_deoverlapped_events
        and q_value is not None
        and q_value <= fdr_threshold
        and base_expectancy >= classification.promote_min_base_cost_expectancy
    ):
        return ResearchClassification.PROMOTE
    return ResearchClassification.WATCH



def _validate_dataset_for_screen(dataset: ResearchDataset) -> None:
    """Revalidate immutable data, content identity, and executable candle chronology."""
    try:
        ValidatedCandleSequence(dataset.sequence.identity, dataset.sequence.candles)
    except (TypeError, ValueError) as error:
        raise AlphaFunnelError(f"dataset failed canonical revalidation: {error}") from error
    if canonical_candle_content_sha256(dataset.sequence) != dataset.content_sha256:
        raise AlphaFunnelError("dataset content changed after digest verification")
    for left, right in zip(
        dataset.sequence.candles[:-1],
        dataset.sequence.candles[1:],
        strict=True,
    ):
        if not left.close_time < right.open_time:
            raise AlphaFunnelError(
                "AF1 requires each completed decision candle close_time to be "
                "strictly before the next entry candle open_time"
            )


def _screen_one(
    dataset: ResearchDataset, hypothesis: ResearchHypothesis, horizon: int,
    raw_events: Sequence[int], config: ScreeningConfig, first_index: int, eligible_count: int,
) -> dict[str, object]:
    candles = dataset.sequence.candles
    deoverlapped = _deoverlapped_events(raw_events, horizon)
    raw_forward: list[Decimal] = []
    directional: list[Decimal] = []
    excursions: list[tuple[Decimal, Decimal]] = []
    indexed_directional: list[tuple[int, Decimal]] = []
    for index in deoverlapped:
        entry = candles[index + 1].open
        if entry == 0:
            raise AlphaFunnelError(f"zero next-candle entry open at {_timestamp(candles[index + 1].open_time)}")
        raw = candles[index + horizon].close / entry - Decimal(1)
        if hypothesis.metadata.direction is Direction.UP:
            value = raw
            favorable = max(candle.high / entry - Decimal(1) for candle in candles[index + 1:index + horizon + 1])
            adverse = min(candle.low / entry - Decimal(1) for candle in candles[index + 1:index + horizon + 1])
        else:
            value = -raw
            favorable = max(Decimal(1) - candle.low / entry for candle in candles[index + 1:index + horizon + 1])
            adverse = min(Decimal(1) - candle.high / entry for candle in candles[index + 1:index + horizon + 1])
        raw_forward.append(raw)
        directional.append(value)
        excursions.append((favorable, adverse))
        indexed_directional.append((index, value))
    mean_directional = _mean(directional)
    positive = [value for value in directional if value > 0]
    negative = [value for value in directional if value < 0]
    average_positive = _mean(positive)
    average_negative = _mean(negative)
    payoff = (
        average_positive / abs(average_negative)
        if average_positive is not None and average_negative is not None and average_negative != 0
        else None
    )
    t_statistic, p_value = _t_statistic_and_p(directional)
    seed_material = "|".join((
        str(config.random_seed),
        dataset.dataset_id,
        dataset.sequence.identity.symbol,
        dataset.content_sha256,
        hypothesis.metadata.stable_id,
        hypothesis.metadata.implementation_id,
        hypothesis.metadata.definition_sha256,
        str(horizon),
    ))
    ci_low, ci_high = _bootstrap_interval(
        directional, samples=config.bootstrap_samples, confidence=config.confidence_level,
        seed_material=seed_material,
    )
    screened_days = Decimal(eligible_count) / Decimal(1_440) if eligible_count else Decimal(1)
    temporal, robustness_evidence_ratio = _temporal_stability(
        indexed_directional,
        first_index=first_index,
        eligible_count=eligible_count,
        blocks=config.stability_blocks,
        minimum_block_coverage=config.minimum_stability_block_coverage,
        minimum_events_per_block=config.minimum_events_per_stability_block,
    )
    adjusted = {
        item.name: _decimal(mean_directional - item.round_trip_rate if mean_directional is not None else None)
        for item in config.costs
    }
    return {
        "hypothesis_id": hypothesis.metadata.stable_id,
        "hypothesis_definition_sha256": hypothesis.metadata.definition_sha256,
        "evaluator_implementation_id": hypothesis.metadata.implementation_id,
        "hypothesis_family": hypothesis.metadata.family,
        "description": hypothesis.metadata.description,
        "symbol": dataset.sequence.identity.symbol,
        "direction": hypothesis.metadata.direction.value,
        "interpretation": hypothesis.metadata.interpretation,
        "horizon_minutes": horizon,
        "raw_event_count": len(raw_events),
        "deoverlapped_event_count": len(deoverlapped),
        "eligible_decision_count": eligible_count,
        "event_frequency_denominator": "eligible_decision_minutes",
        "raw_event_frequency_per_day": str(Decimal(len(raw_events)) / screened_days),
        "deoverlapped_event_frequency_per_day": str(
            Decimal(len(deoverlapped)) / screened_days
        ),
        "mean_forward_return": _decimal(_mean(raw_forward)),
        "median_forward_return": _decimal(_median(raw_forward)),
        "mean_directional_return": _decimal(mean_directional),
        "median_directional_return": _decimal(_median(directional)),
        "win_rate": _decimal(
            Decimal(len(positive)) / Decimal(len(directional))
            if directional else None
        ),
        "zero_return_policy": {
            "win_rate": "zero_is_not_a_win",
            "positive_negative_means": "zero_is_excluded",
            "payoff_ratio": "requires_at_least_one_strict_win_and_one_strict_loss",
        },
        "average_positive_return": _decimal(average_positive),
        "average_negative_return": _decimal(average_negative),
        "payoff_ratio": _decimal(payoff),
        "standard_error": _decimal(_standard_error(directional)),
        "bootstrap_confidence_interval": {
            "method": "deoverlapped_event_resampling_with_replacement_splitmix64-v1",
            "samples": config.bootstrap_samples,
            "confidence_level": str(config.confidence_level),
            "lower": _decimal(ci_low),
            "upper": _decimal(ci_high),
        },
        "descriptive_t_statistic": _decimal(t_statistic),
        "raw_p_value": _decimal(p_value),
        "p_value_method": "two_sided_one_sample_student_t_against_zero",
        "gross_expectancy": _decimal(mean_directional),
        "cost_adjusted_expectancy": adjusted,
        "cost_adjusted_expectancy_unit": COST_RATE_UNIT,
        "average_maximum_favorable_excursion": _decimal(_mean([item[0] for item in excursions])),
        "average_maximum_adverse_excursion": _decimal(_mean([item[1] for item in excursions])),
        "temporal_stability": temporal,
        "parameter_neighborhood": dict(hypothesis.metadata.parameter_neighborhood),
        "_robustness_evidence_ratio": robustness_evidence_ratio,
        "_p_value": p_value,
        "_base_expectancy": (
            mean_directional - config.base_cost.round_trip_rate if mean_directional is not None else None
        ),
    }


def _evaluate_alpha_funnel(
    datasets: tuple[ResearchDataset, ...],
    hypotheses: tuple[ResearchHypothesis, ...],
    config: ScreeningConfig,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    if type(config) is not ScreeningConfig:
        raise AlphaFunnelError("config must be ScreeningConfig")
    if (
        type(datasets) is not tuple
        or not datasets
        or any(type(item) is not ResearchDataset for item in datasets)
    ):
        raise AlphaFunnelError("datasets must be a non-empty tuple of ResearchDataset values")
    if (
        type(hypotheses) is not tuple
        or not hypotheses
        or any(type(item) is not ResearchHypothesis for item in hypotheses)
    ):
        raise AlphaFunnelError("hypotheses must be a non-empty tuple of ResearchHypothesis values")
    if len({item.sequence.identity.symbol for item in datasets}) != len(datasets):
        raise AlphaFunnelError("datasets must contain at most one dataset per symbol")
    if len({item.metadata.stable_id for item in hypotheses}) != len(hypotheses):
        raise AlphaFunnelError("hypothesis stable IDs must be unique")

    ordered_datasets = tuple(
        sorted(datasets, key=lambda item: item.sequence.identity.symbol)
    )
    ordered_hypotheses = tuple(
        sorted(hypotheses, key=lambda item: item.metadata.stable_id)
    )
    for dataset in ordered_datasets:
        _validate_dataset_for_screen(dataset)

    actual_universe = _fdr_test_keys(ordered_datasets, ordered_hypotheses)
    if actual_universe != config.fdr_universe.tests:
        raise AlphaFunnelError(
            "screen inputs do not exactly match the predeclared FDR universe; "
            "subsets, additions, role changes, implementation changes, and "
            "parameterization changes require a new universe declaration"
        )
    if (
        any(item.role is DatasetRole.SEALED_OOS for item in ordered_datasets)
        and not config.allow_sealed_oos
    ):
        raise AlphaFunnelError(
            "sealed_oos requires allow_sealed_oos=True as an explicit screening action"
        )

    identity_payload = {
        "schema_version": AF1_SCHEMA_VERSION,
        "datasets": [item.identity_dict() for item in ordered_datasets],
        "hypotheses": [item.metadata.as_dict() for item in ordered_hypotheses],
        "configuration": config.as_dict(),
        "decimal_policy": {
            "version": AF1_NUMERICS_VERSION,
            "precision": _AF1_DECIMAL_CONTEXT.prec,
            "rounding": _AF1_DECIMAL_CONTEXT.rounding,
            "Emin": _AF1_DECIMAL_CONTEXT.Emin,
            "Emax": _AF1_DECIMAL_CONTEXT.Emax,
            "clamp": _AF1_DECIMAL_CONTEXT.clamp,
        },
        "causality_rule": (
            "hypothesis_receives_only_exact_lookback_ending_at_completed_decision_candle"
        ),
        "callback_trust_boundary": (
            "human_supplied_evaluator_is_trusted_reviewed_research_code; "
            "AF1_does_not_sandbox_closure_state"
        ),
        "timestamp_ordering_rule": (
            "decision_close_time_strictly_before_next_entry_open_time_and_"
            "forward_candles_strictly_chronological"
        ),
        "forward_outcome_rule": "next_candle_open_to_horizon_candle_close",
        "event_deoverlap_rule": (
            "for_each_horizon_greedily_keep_the_earliest_event_then_only_events_with_"
            "decision_index_spacing_at_least_the_horizon"
        ),
        "common_support_rule": (
            "all_horizons_use_decisions_with_a_complete_60m_forward_window"
        ),
        "event_frequency_rule": "event_count_per_1440_eligible_decision_minutes",
        "bootstrap_generator": "splitmix64-v1",
        "research_only": True,
        "production_approved": False,
    }
    partial: list[dict[str, object]] = []
    for dataset in ordered_datasets:
        candles = dataset.sequence.candles
        for hypothesis in ordered_hypotheses:
            first_index = hypothesis.metadata.causal_lookback - 1
            exclusive_end = len(candles) - max(config.horizons_minutes)
            eligible_count = max(0, exclusive_end - first_index)
            raw_events: list[int] = []
            for index in range(first_index, exclusive_end):
                window = candles[
                    index - hypothesis.metadata.causal_lookback + 1:index + 1
                ]
                try:
                    outcome = hypothesis.evaluator(window)
                except Exception as error:
                    raise AlphaFunnelError(
                        f"hypothesis {hypothesis.metadata.stable_id} failed at "
                        f"{_timestamp(candles[index].close_time)}: {error}"
                    ) from error
                if type(outcome) is not bool:
                    raise AlphaFunnelError(
                        "hypothesis evaluators must return a built-in bool"
                    )
                if outcome:
                    raw_events.append(index)
            for horizon in config.horizons_minutes:
                partial.append(_screen_one(
                    dataset,
                    hypothesis,
                    horizon,
                    raw_events,
                    config,
                    first_index,
                    eligible_count,
                ))

    if len(partial) != len(config.fdr_universe.tests):
        raise AlphaFunnelError("calculated tests do not match the declared FDR universe")
    q_values = benjamini_hochberg(tuple(item["_p_value"] for item in partial))
    calculated_tests = sum(value is not None for value in q_values)
    results: list[dict[str, object]] = []
    for item, q_value in zip(partial, q_values, strict=True):
        positive_t = (
            max(Decimal(0), Decimal(item["descriptive_t_statistic"]))
            if item["descriptive_t_statistic"] is not None
            else None
        )
        frequency = Decimal(item["deoverlapped_event_frequency_per_day"])
        m_score = config.scoring.magnitude.score(item["_base_expectancy"])
        s_score = config.scoring.statistical_strength.score(positive_t)
        f_score = config.scoring.opportunity_frequency.score(frequency)
        uncapped_r_score = config.scoring.robustness.score(
            item["_robustness_evidence_ratio"]
        )
        positive_block_count = int(
            item["temporal_stability"]["positive_qualified_block_count"]
        )
        maximum_robustness_prerequisite_met = (
            positive_block_count
            >= config.minimum_positive_blocks_for_max_robustness
        )
        r_score = (
            uncapped_r_score
            if uncapped_r_score < 4 or maximum_robustness_prerequisite_met
            else 3
        )
        x_score = next(
            hypothesis.metadata.human_explainability_score
            for hypothesis in ordered_hypotheses
            if hypothesis.metadata.stable_id == item["hypothesis_id"]
        )
        rvs = (
            m_score + s_score + f_score + r_score + x_score
            if x_score is not None
            else None
        )
        classification = _classify(
            deoverlapped_count=int(item["deoverlapped_event_count"]),
            base_expectancy=item["_base_expectancy"],
            q_value=q_value,
            rvs=rvs,
            classification=config.classification,
            fdr_threshold=config.fdr_threshold,
        )
        item.pop("_robustness_evidence_ratio")
        item.pop("_p_value")
        base_expectancy = item.pop("_base_expectancy")
        passes_fdr = q_value is not None and q_value <= config.fdr_threshold
        item["multiple_testing"] = {
            "method": "Benjamini-Hochberg",
            "batch_id": config.batch_id,
            "fdr_universe_id": config.fdr_universe.universe_id,
            "authority_id": config.fdr_universe.authority_id,
            "family": item["hypothesis_family"],
            "registered_tests": len(config.fdr_universe.tests),
            "tests_with_calculated_p_values": calculated_tests,
            "undefined_p_value_policy": UNDEFINED_P_VALUE_POLICY,
            "fdr_threshold": str(config.fdr_threshold),
            "q_value": _decimal(q_value),
            "passes_fdr_threshold": passes_fdr,
        }
        item["research_viability_score"] = {
            "M": m_score,
            "S": s_score,
            "F": f_score,
            "R": r_score,
            "R_uncapped": uncapped_r_score,
            "R_maximum_prerequisite_met": maximum_robustness_prerequisite_met,
            "R_minimum_positive_blocks_for_maximum": (
                config.minimum_positive_blocks_for_max_robustness
            ),
            "X": x_score,
            "RVS": rvs,
            "maximum": 20,
            "X_source": (
                "human_supplied_metadata" if x_score is not None else "not_supplied"
            ),
            "inputs": {
                "base_cost_expectancy": _decimal(base_expectancy),
                "positive_t_statistic": _decimal(positive_t),
                "deoverlapped_events_per_day": item[
                    "deoverlapped_event_frequency_per_day"
                ],
                "robustness_evidence_ratio": item["temporal_stability"][
                    "robustness_evidence_ratio"
                ],
            },
        }
        item["provisional_classification"] = classification.value
        item["classification_meaning"] = (
            PROMOTE_MEANING
            if classification is ResearchClassification.PROMOTE
            else "deterministic research triage; final decision remains human"
        )
        item["classification_inputs"] = {
            "deoverlapped_event_count": item["deoverlapped_event_count"],
            "base_cost_expectancy": _decimal(base_expectancy),
            "q_value": _decimal(q_value),
            "fdr_threshold": str(config.fdr_threshold),
            "passes_fdr_threshold": passes_fdr,
            "RVS": rvs,
            "configured_rules": config.classification.as_dict(),
        }
        item["event_deoverlap"] = {
            "method": "greedy_earliest_nonoverlapping_forward_intervals",
            "minimum_decision_spacing_minutes": item["horizon_minutes"],
            "statistics_use": "deoverlapped_events",
            "does_not_assert_iid": True,
            "raw_count_is_descriptive_only": True,
        }
        results.append(item)

    results.sort(
        key=lambda item: (
            item["hypothesis_id"],
            item["symbol"],
            item["horizon_minutes"],
        )
    )
    return identity_payload, results


def _make_screen_alpha_funnel_entrypoint():
    provenance_key = secrets.token_bytes(32)

    def sign(run_id: str, manifest_bytes: bytes, results_bytes: bytes) -> bytes:
        return hmac.digest(
            provenance_key,
            _provenance_material(run_id, manifest_bytes, results_bytes),
            "sha256",
        )

    def verify(run: ScreeningRun) -> bool:
        proof = getattr(run, "_provenance_proof", None)
        if type(proof) is not bytes:
            return False
        expected = sign(run.run_id, run._manifest_bytes, run._results_bytes)
        return hmac.compare_digest(proof, expected)

    def screen(
        datasets: tuple[ResearchDataset, ...],
        hypotheses: tuple[ResearchHypothesis, ...],
        config: ScreeningConfig,
    ) -> ScreeningRun:
        """Evaluate exactly one closed, causal, deterministic research test universe."""
        try:
            with af1_decimal_context():
                identity, results = _evaluate_alpha_funnel(
                    datasets, hypotheses, config
                )
                return _assemble_screening_run(identity, results, sign)
        except AlphaFunnelError:
            raise
        except DecimalException as error:
            raise AlphaFunnelError(
                f"AF1 decimal arithmetic failed deterministically: {error}"
            ) from error

    screen.__name__ = "screen_alpha_funnel"
    screen.__qualname__ = "screen_alpha_funnel"
    return screen, verify


screen_alpha_funnel, _verify_screening_run_provenance = (
    _make_screen_alpha_funnel_entrypoint()
)
del _make_screen_alpha_funnel_entrypoint

"""Causal, chronological supervised examples for one canonical source dataset."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import (
    Context, Decimal, DecimalException, DivisionByZero, FloatOperation,
    InvalidOperation, Overflow, ROUND_HALF_EVEN, Underflow, localcontext,
)
from types import MappingProxyType

from quantos.domain.common import V1_SYMBOLS
from quantos.domain.features import FEATURE_NAMES, FEATURE_VERSION, MIN_HISTORY, FeatureVector, compute_feature_vector
from quantos.domain.market_data import Candle, DatasetIdentity, DatasetValidationStatus, ValidatedCandleSequence

TARGET_VERSION = "gross-next-open-to-close-5m-v1"
TARGET_HORIZON_MINUTES = 5
_MINUTE = timedelta(minutes=1)


class TrainingDataError(ValueError):
    """Malformed training data, incompatible contracts, or invalid target mathematics."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TrainingDataError(message)


def _utc(value: datetime, name: str) -> None:
    _require(type(value) is datetime, f"{name} must be a built-in UTC datetime")
    _require(value.tzinfo is not None and value.utcoffset() == timedelta(0), f"{name} must be UTC")


def _target_metadata(version: str, horizon: int) -> None:
    _require(type(version) is str and version == TARGET_VERSION, "incompatible target_version")
    _require(type(horizon) is int and horizon == TARGET_HORIZON_MINUTES, "horizon_minutes must be 5")


def _identity(identity: DatasetIdentity) -> None:
    _require(type(identity) is DatasetIdentity, "source_identity must be a DatasetIdentity")
    _require(identity.validation_status is DatasetValidationStatus.VALIDATED, "source identity must be VALIDATED")
    try:
        DatasetIdentity.__post_init__(identity)
    except (ValueError, TypeError, AttributeError) as error:
        raise TrainingDataError(f"invalid source identity: {error}") from error
    _utc(identity.start_time, "source start_time")
    _utc(identity.end_time, "source end_time")
    _require((identity.end_time - identity.start_time) % _MINUTE == timedelta(0), "source bounds must span whole 1m intervals")


@dataclass(frozen=True, slots=True)
class TargetLabel:
    """Gross return from the next open to the fifth future candle's close."""

    decision_time: datetime
    symbol: str
    target_version: str
    horizon_minutes: int
    entry_reference_time: datetime
    exit_reference_time: datetime
    value: Decimal

    def __post_init__(self) -> None:
        for name in ("decision_time", "entry_reference_time", "exit_reference_time"):
            _utc(getattr(self, name), name)
        _require(type(self.symbol) is str and self.symbol in V1_SYMBOLS, "unsupported label symbol")
        _target_metadata(self.target_version, self.horizon_minutes)
        _require(type(self.value) is Decimal and self.value.is_finite(), "target value must be a finite built-in Decimal")
        _require(self.value >= Decimal(-1), "gross target return cannot be below -1")
        _require(self.decision_time < self.entry_reference_time < self.exit_reference_time, "target references must be strictly after decision_time and ordered")
        # The fifth future candle opens four minutes after the entry candle.
        # Preserve its actual close; do not assume any provider's close precision.
        _require(self.exit_reference_time - self.entry_reference_time > 4 * _MINUTE, "exit must be after the fifth future candle opens")


@dataclass(frozen=True, slots=True)
class TrainingExample:
    """One causal feature vector paired with separate future research truth."""

    feature: FeatureVector
    label: TargetLabel

    def __post_init__(self) -> None:
        _require(type(self.feature) is FeatureVector, "example requires a FeatureVector")
        _require(type(self.label) is TargetLabel, "example requires a TargetLabel")
        TargetLabel.__post_init__(self.label)
        _utc(self.feature.timestamp, "feature timestamp")
        _require(self.feature.feature_version == FEATURE_VERSION, "incompatible feature_version")
        _require(type(self.feature.values) is MappingProxyType, "feature values must be immutable")
        _require(tuple(self.feature.values) == FEATURE_NAMES, "feature schema must match the current ordered names")
        _require(all(type(value) is Decimal and value.is_finite() for value in self.feature.values.values()), "feature values must be finite built-in Decimals")
        _require(self.feature.symbol == self.label.symbol, "feature and label symbols must match")
        _require(self.feature.timestamp == self.label.decision_time, "feature and label decision timestamps must match")
        # Copy through the existing contract so even a forged mapping proxy
        # backed by caller-owned mutable data cannot mutate this example later.
        object.__setattr__(self, "feature", replace(self.feature))


@dataclass(frozen=True, slots=True)
class TrainingDataset:
    """One source identity, chronological rows, and complete candidate accounting."""

    source_identity: DatasetIdentity
    feature_version: str
    target_version: str
    horizon_minutes: int
    examples: tuple[TrainingExample, ...]
    unavailable_feature_timestamps: tuple[datetime, ...]

    @property
    def candidate_decision_count(self) -> int:
        source_count = (self.source_identity.end_time - self.source_identity.start_time) // _MINUTE + 1
        return max(0, source_count - (MIN_HISTORY - 1) - TARGET_HORIZON_MINUTES)

    def __post_init__(self) -> None:
        _identity(self.source_identity)
        _require(self.feature_version == FEATURE_VERSION, "incompatible dataset feature_version")
        _target_metadata(self.target_version, self.horizon_minutes)
        _require(type(self.examples) is tuple, "examples must be a built-in tuple")
        _require(type(self.unavailable_feature_timestamps) is tuple, "unavailable feature timestamps must be a built-in tuple")
        _require(len(self.examples) + len(self.unavailable_feature_timestamps) == self.candidate_decision_count, "candidate decision accounting mismatch")

        seen: set[int] = set()

        def record(timestamp: datetime) -> int:
            _utc(timestamp, "decision timestamp")
            offset = timestamp - self.source_identity.start_time
            index = offset // _MINUTE
            _require(offset % _MINUTE != timedelta(0), "decision must follow its candle open and precede the next open")
            _require(MIN_HISTORY - 1 <= index < MIN_HISTORY - 1 + self.candidate_decision_count, "decision outside eligible source range")
            _require(index not in seen, "duplicate candidate decision")
            seen.add(index)
            return index

        checked_examples: list[TrainingExample] = []
        previous: datetime | None = None
        for example in self.examples:
            _require(type(example) is TrainingExample, "examples must contain TrainingExample values")
            # Retain validated snapshots without mutating caller-owned examples.
            example = replace(example)
            checked_examples.append(example)
            timestamp = example.feature.timestamp
            _require(previous is None or previous < timestamp, "examples must be strictly chronological")
            index = record(timestamp)
            _require(example.feature.symbol == self.source_identity.symbol, "example symbol must match source identity")
            _require(example.label.entry_reference_time == self.source_identity.start_time + (index + 1) * _MINUTE, "entry reference must be the next source candle open")
            previous = timestamp
        previous = None
        for timestamp in self.unavailable_feature_timestamps:
            _utc(timestamp, "unavailable feature timestamp")
            _require(previous is None or previous < timestamp, "unavailable feature timestamps must be strictly chronological")
            record(timestamp)
            previous = timestamp
        object.__setattr__(self, "examples", tuple(checked_examples))


def _validated_source(sequence: ValidatedCandleSequence) -> tuple[Candle, ...]:
    _require(type(sequence) is ValidatedCandleSequence, "builder requires a ValidatedCandleSequence")
    _identity(sequence.identity)
    _require(type(sequence.candles) is tuple, "canonical candles must be a built-in tuple")
    for candle in sequence.candles:
        _require(type(candle) is Candle, "source must contain canonical Candle values")
        Candle.__post_init__(candle)
        _utc(candle.open_time, "candle open_time")
        _utc(candle.close_time, "candle close_time")
        for field in ("open", "high", "low", "close", "volume", "quote_volume"):
            _require(type(getattr(candle, field)) is Decimal, "candle market fields must be built-in Decimal values")
    return ValidatedCandleSequence(sequence.identity, sequence.candles).candles


def build_training_dataset(sequence: ValidatedCandleSequence) -> TrainingDataset:
    """Build one row per eligible minute; future data is used only by labels.

    Candidates are indices 20 through N-6, inclusive. Valid short sources
    return empty datasets. A zero entry open fails the entire construction,
    including when that candidate's features would be unavailable. Missing
    features otherwise record the decision timestamp and produce no example.
    """
    context = Context(
        prec=34, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
        capitals=1, clamp=0, flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow, Underflow, FloatOperation],
    )
    try:
        with localcontext(context):
            candles = _validated_source(sequence)
            examples: list[TrainingExample] = []
            unavailable: list[datetime] = []
            for index in range(MIN_HISTORY - 1, len(candles) - TARGET_HORIZON_MINUTES):
                decision = candles[index]
                entry = candles[index + 1]
                exit_candle = candles[index + TARGET_HORIZON_MINUTES]
                _require(entry.open != 0, f"zero target entry open at {entry.open_time.isoformat()}")
                label = TargetLabel(
                    decision_time=decision.close_time, symbol=decision.symbol,
                    target_version=TARGET_VERSION, horizon_minutes=TARGET_HORIZON_MINUTES,
                    entry_reference_time=entry.open_time, exit_reference_time=exit_candle.close_time,
                    value=exit_candle.close / entry.open - Decimal(1),
                )
                feature = compute_feature_vector(
                    candles[index - MIN_HISTORY + 1:index + 1], decision_time=decision.close_time,
                )
                if feature is None:
                    unavailable.append(decision.close_time)
                else:
                    examples.append(TrainingExample(feature, label))
            return TrainingDataset(
                source_identity=sequence.identity, feature_version=FEATURE_VERSION,
                target_version=TARGET_VERSION, horizon_minutes=TARGET_HORIZON_MINUTES,
                examples=tuple(examples), unavailable_feature_timestamps=tuple(unavailable),
            )
    except TrainingDataError:
        raise
    except (ValueError, TypeError, AttributeError, DecimalException, OverflowError) as error:
        raise TrainingDataError(f"cannot build training dataset: {error}") from error

"""Deterministic validation for canonical Market Data candle sequences."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from quantos.domain.market_data.contracts import (
    Candle,
    DatasetIdentity,
    DatasetValidationStatus,
)


class DatasetValidationError(ValueError):
    """Raised when canonical market data violates a required integrity rule."""


def _validated_sequence(
    identity: DatasetIdentity,
    candles: Iterable[Candle],
    *,
    expected_status: DatasetValidationStatus,
) -> tuple[Candle, ...]:
    """Apply the one authoritative set of canonical sequence invariants."""
    if not isinstance(identity, DatasetIdentity):
        raise DatasetValidationError("sequence validation requires a DatasetIdentity")
    if identity.validation_status is not expected_status:
        if expected_status is DatasetValidationStatus.UNVALIDATED:
            raise DatasetValidationError("dataset identity must be unvalidated before validation")
        raise DatasetValidationError("validated sequences require a validated dataset identity")
    try:
        sequence = tuple(candles)
    except TypeError as error:
        raise DatasetValidationError("candle sequence must be iterable") from error
    if not sequence:
        raise DatasetValidationError("candle sequence must not be empty")
    previous: Candle | None = None
    for candle in sequence:
        if not isinstance(candle, Candle):
            raise DatasetValidationError("candle sequences must contain only Candle values")
        if candle.symbol != identity.symbol:
            raise DatasetValidationError("candle symbol does not match dataset identity")
        if candle.interval != identity.timeframe:
            raise DatasetValidationError("candle interval does not match dataset identity")
        if previous is not None:
            if candle.open_time == previous.open_time:
                raise DatasetValidationError("duplicate candle timestamp")
            if candle.open_time < previous.open_time:
                raise DatasetValidationError("out-of-order candle timestamp")
            if candle.open_time - previous.open_time != timedelta(minutes=1):
                raise DatasetValidationError("missing 1m candle timestamp")
        previous = candle
    return sequence


@dataclass(frozen=True, slots=True)
class ValidatedCandleSequence:
    """The immutable outcome of successful canonical sequence validation."""

    identity: DatasetIdentity
    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        sequence = _validated_sequence(
            self.identity,
            self.candles,
            expected_status=DatasetValidationStatus.VALIDATED,
        )
        object.__setattr__(self, "candles", sequence)


def validate_candle_sequence(
    identity: DatasetIdentity, candles: Iterable[Candle]
) -> ValidatedCandleSequence:
    """Validate canonical candles and return their explicit validated outcome.

    Input order is authoritative. This function never sorts, deduplicates,
    repairs, or synthesizes candles.
    """
    sequence = _validated_sequence(
        identity,
        candles,
        expected_status=DatasetValidationStatus.UNVALIDATED,
    )
    return ValidatedCandleSequence(identity._validated_copy(), sequence)

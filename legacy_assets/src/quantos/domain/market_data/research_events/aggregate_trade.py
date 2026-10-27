"""Canonical research contract for Binance Spot aggregate trades.

This module defines event meaning and dataset identity only.  It deliberately
does not contain provider acquisition, file persistence, or trading behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import json
from typing import Final

from quantos.domain.common import require_v1_symbol

_UTC_EPOCH: Final = datetime(1970, 1, 1, tzinfo=timezone.utc)


class SourceTimestampUnit(str, Enum):
    """Explicit unit carried by the provider's integer event timestamp."""

    MILLISECOND = "millisecond"
    MICROSECOND = "microsecond"


class AggressorSide(str, Enum):
    """Side of the taker represented by an aggregate-trade event."""

    BUY = "buy"
    SELL = "sell"


class ResearchDatasetRole(str, Enum):
    """Research partition role bound into a dataset identity."""

    DEVELOPMENT = "development"
    SCREENING_VALIDATION = "screening_validation"
    SEALED_OOS = "sealed_oos"


class ResearchEventValidationStatus(str, Enum):
    """Lifecycle state for a research event dataset."""

    UNVALIDATED = "unvalidated"
    VALIDATED = "validated"


def _require_exact_int(value: object, *, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an int")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _require_exact_bool(value: object, *, name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{name} must be a bool")
    return value


def _require_optional_exact_bool(value: object, *, name: str) -> bool | None:
    if value is None:
        return None
    return _require_exact_bool(value, name=name)


def _require_positive_decimal(value: object, *, name: str) -> Decimal:
    if type(value) is not Decimal:
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _require_non_empty_string(value: object, *, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def _require_v1_symbol_exact(value: object) -> str:
    symbol = _require_non_empty_string(value, name="symbol")
    return require_v1_symbol(symbol)


def _require_utc_datetime(value: object, *, name: str) -> datetime:
    if type(value) is not datetime:
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be timezone-aware UTC")
    return value


def _require_timestamp_unit(value: object) -> SourceTimestampUnit:
    if type(value) is not SourceTimestampUnit:
        raise TypeError("source_timestamp_unit must be a SourceTimestampUnit")
    return value


def normalize_source_timestamp(
    source_timestamp: int,
    source_timestamp_unit: SourceTimestampUnit,
) -> datetime:
    """Convert an explicitly unit-tagged Unix timestamp without float math."""

    timestamp = _require_exact_int(source_timestamp, name="source_timestamp")
    unit = _require_timestamp_unit(source_timestamp_unit)
    try:
        if unit is SourceTimestampUnit.MILLISECOND:
            return _UTC_EPOCH + timedelta(milliseconds=timestamp)
        return _UTC_EPOCH + timedelta(microseconds=timestamp)
    except OverflowError as error:
        raise ValueError("source_timestamp is outside the datetime range") from error


@dataclass(frozen=True, slots=True)
class AggregateTrade:
    """One Binance Spot aggregate-trade event with explicit source semantics."""

    symbol: str
    aggregate_trade_id: int
    price: Decimal
    quantity: Decimal
    first_trade_id: int
    last_trade_id: int
    event_time: datetime
    source_timestamp: int
    source_timestamp_unit: SourceTimestampUnit
    buyer_is_maker: bool
    best_price_match: bool | None = None

    def __post_init__(self) -> None:
        _require_v1_symbol_exact(self.symbol)
        _require_exact_int(self.aggregate_trade_id, name="aggregate_trade_id")
        _require_positive_decimal(self.price, name="price")
        _require_positive_decimal(self.quantity, name="quantity")
        first_trade_id = _require_exact_int(
            self.first_trade_id, name="first_trade_id"
        )
        last_trade_id = _require_exact_int(self.last_trade_id, name="last_trade_id")
        if first_trade_id > last_trade_id:
            raise ValueError("first_trade_id must be less than or equal to last_trade_id")
        event_time = _require_utc_datetime(self.event_time, name="event_time")
        normalized_time = normalize_source_timestamp(
            self.source_timestamp, self.source_timestamp_unit
        )
        if event_time != normalized_time:
            raise ValueError(
                "event_time must exactly match source_timestamp and "
                "source_timestamp_unit"
            )
        _require_exact_bool(self.buyer_is_maker, name="buyer_is_maker")
        _require_optional_exact_bool(
            self.best_price_match, name="best_price_match"
        )

    @property
    def aggressor_side(self) -> AggressorSide:
        """Return the taker side implied by Binance's buyer-maker flag."""

        if self.buyer_is_maker:
            return AggressorSide.SELL
        return AggressorSide.BUY


@dataclass(frozen=True, slots=True)
class AggregateTradeDatasetIdentity:
    """Immutable identity for a requested aggregate-trade research dataset."""

    symbol: str
    requested_start_time: datetime
    requested_end_time_exclusive: datetime
    source_timestamp_unit: SourceTimestampUnit
    schema_version: str
    normalizer_version: str
    provenance: str
    research_role: ResearchDatasetRole
    validation_status: ResearchEventValidationStatus = field(
        default=ResearchEventValidationStatus.UNVALIDATED, init=False
    )
    provider: str = field(default="binance", init=False)
    market: str = field(default="spot", init=False)
    event_family: str = field(default="aggregate_trade", init=False)
    event_granularity: str = field(default="event", init=False)
    boundary_convention: str = field(
        default="[requested_start_time,requested_end_time_exclusive)", init=False
    )

    def __post_init__(self) -> None:
        fixed_fields = {
            "provider": (self.provider, "binance"),
            "market": (self.market, "spot"),
            "event_family": (self.event_family, "aggregate_trade"),
            "event_granularity": (self.event_granularity, "event"),
            "boundary_convention": (
                self.boundary_convention,
                "[requested_start_time,requested_end_time_exclusive)",
            ),
        }
        for name, (actual, expected) in fixed_fields.items():
            if actual != expected:
                raise ValueError(f"{name} must be {expected!r}")
        _require_v1_symbol_exact(self.symbol)
        start = _require_utc_datetime(
            self.requested_start_time, name="requested_start_time"
        )
        end = _require_utc_datetime(
            self.requested_end_time_exclusive,
            name="requested_end_time_exclusive",
        )
        if end <= start:
            raise ValueError(
                "requested_end_time_exclusive must be after requested_start_time"
            )
        _require_timestamp_unit(self.source_timestamp_unit)
        _require_non_empty_string(self.schema_version, name="schema_version")
        _require_non_empty_string(
            self.normalizer_version, name="normalizer_version"
        )
        _require_non_empty_string(self.provenance, name="provenance")
        if type(self.research_role) is not ResearchDatasetRole:
            raise TypeError("research_role must be a ResearchDatasetRole")
        if type(self.validation_status) is not ResearchEventValidationStatus:
            raise TypeError(
                "validation_status must be a ResearchEventValidationStatus"
            )

    def _validated_copy(self) -> AggregateTradeDatasetIdentity:
        """Return a new identity promoted to the validated lifecycle state."""

        validated_identity = AggregateTradeDatasetIdentity(
            symbol=self.symbol,
            requested_start_time=self.requested_start_time,
            requested_end_time_exclusive=self.requested_end_time_exclusive,
            source_timestamp_unit=self.source_timestamp_unit,
            schema_version=self.schema_version,
            normalizer_version=self.normalizer_version,
            provenance=self.provenance,
            research_role=self.research_role,
        )
        object.__setattr__(
            validated_identity,
            "validation_status",
            ResearchEventValidationStatus.VALIDATED,
        )
        return validated_identity

    def as_canonical_dict(self) -> dict[str, str]:
        """Return all identity-defining fields in serialization-safe form."""

        return {
            "boundary_convention": self.boundary_convention,
            "event_family": self.event_family,
            "event_granularity": self.event_granularity,
            "market": self.market,
            "normalizer_version": self.normalizer_version,
            "provenance": self.provenance,
            "provider": self.provider,
            "requested_end_time_exclusive": _canonical_utc(
                self.requested_end_time_exclusive
            ),
            "requested_start_time": _canonical_utc(self.requested_start_time),
            "research_role": self.research_role.value,
            "schema_version": self.schema_version,
            "source_timestamp_unit": self.source_timestamp_unit.value,
            "symbol": self.symbol,
            "validation_status": self.validation_status.value,
        }


def _canonical_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def aggregate_trade_dataset_identity_bytes(
    identity: AggregateTradeDatasetIdentity,
) -> bytes:
    """Serialize identity fields to deterministic canonical JSON bytes."""

    if type(identity) is not AggregateTradeDatasetIdentity:
        raise TypeError("identity must be an AggregateTradeDatasetIdentity")
    AggregateTradeDatasetIdentity.__post_init__(identity)
    return (
        json.dumps(
            identity.as_canonical_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def aggregate_trade_dataset_id(identity: AggregateTradeDatasetIdentity) -> str:
    """Return the SHA-256 identifier of canonical identity bytes."""

    return hashlib.sha256(aggregate_trade_dataset_identity_bytes(identity)).hexdigest()

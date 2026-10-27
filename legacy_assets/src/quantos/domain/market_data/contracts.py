"""Canonical, provider-independent market-data contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum

from quantos.domain.common import (
    V1_INTERVAL,
    require_decimal,
    require_non_empty,
    require_utc,
    require_v1_symbol,
)


class DatasetValidationStatus(str, Enum):
    """The recorded validation state of a canonical dataset."""

    UNVALIDATED = "unvalidated"
    VALIDATED = "validated"


@dataclass(frozen=True, slots=True)
class DatasetIdentity:
    """Immutable identity metadata for one canonical symbol-specific dataset."""

    symbol: str
    timeframe: str
    start_time: datetime
    end_time: datetime
    source: str
    schema_version: str
    ingestion_version: str
    validation_status: DatasetValidationStatus = field(
        default=DatasetValidationStatus.UNVALIDATED, init=False
    )

    def __post_init__(self) -> None:
        require_v1_symbol(self.symbol)
        if self.timeframe != V1_INTERVAL:
            raise ValueError(f"timeframe must be {V1_INTERVAL!r}")
        require_utc(self.start_time, "start_time")
        require_utc(self.end_time, "end_time")
        if self.end_time < self.start_time:
            raise ValueError("end_time must not be before start_time")
        require_non_empty(self.source, "source")
        require_non_empty(self.schema_version, "schema_version")
        require_non_empty(self.ingestion_version, "ingestion_version")

    def _validated_copy(self) -> DatasetIdentity:
        """Return a new identity marked by the canonical validation path."""
        validated_identity = DatasetIdentity(
            symbol=self.symbol,
            timeframe=self.timeframe,
            start_time=self.start_time,
            end_time=self.end_time,
            source=self.source,
            schema_version=self.schema_version,
            ingestion_version=self.ingestion_version,
        )
        object.__setattr__(
            validated_identity, "validation_status", DatasetValidationStatus.VALIDATED
        )
        return validated_identity


@dataclass(frozen=True, slots=True)
class Candle:
    """A canonical V1 one-minute OHLCV candle."""

    symbol: str
    interval: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trade_count: int

    def __post_init__(self) -> None:
        require_v1_symbol(self.symbol)
        if self.interval != V1_INTERVAL:
            raise ValueError(f"interval must be {V1_INTERVAL!r}")
        require_utc(self.open_time, "open_time")
        require_utc(self.close_time, "close_time")
        if self.close_time <= self.open_time:
            raise ValueError("close_time must be after open_time")
        for field_name in ("open", "high", "low", "close"):
            require_decimal(getattr(self, field_name), field_name, non_negative=True)
        require_decimal(self.volume, "volume", non_negative=True)
        require_decimal(self.quote_volume, "quote_volume", non_negative=True)
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must not be below open, close, or low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must not be above open, close, or high")
        if isinstance(self.trade_count, bool) or not isinstance(self.trade_count, int):
            raise ValueError("trade_count must be an integer")
        if self.trade_count < 0:
            raise ValueError("trade_count must not be negative")

    def is_complete_at(self, decision_time: datetime) -> bool:
        """Return whether this candle was complete at a UTC decision time."""
        require_utc(decision_time, "decision_time")
        return decision_time >= self.close_time


@dataclass(frozen=True, slots=True)
class MarketEvent:
    """A completed canonical candle made available to the application."""

    timestamp: datetime
    candle: Candle

    def __post_init__(self) -> None:
        require_utc(self.timestamp, "timestamp")
        if self.timestamp < self.candle.close_time:
            raise ValueError("a market event must not expose an incomplete candle")

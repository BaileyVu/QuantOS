"""Application orchestration for validated in-memory historical ingestion."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from quantos.domain.common import (
    V1_INTERVAL,
    require_non_empty,
    require_utc,
    require_v1_symbol,
)
from quantos.domain.market_data import (
    Candle,
    DatasetIdentity,
    DatasetValidationStatus,
    ValidatedCandleSequence,
    validate_candle_sequence,
)

_ONE_MINUTE = timedelta(minutes=1)


class HistoricalCandleRangeFetcher(Protocol):
    """Minimal provider-neutral boundary for normalized historical candles."""

    def fetch_open_time_range(
        self,
        *,
        symbol: str,
        interval: str,
        start_open_time: datetime,
        end_open_time_exclusive: datetime,
    ) -> tuple[Candle, ...]:
        """Fetch normalized candles for an explicit open-time range."""


class CanonicalCandleDatasetWriter(Protocol):
    """Minimal persistence boundary for one validated canonical dataset."""

    def write(self, sequence: ValidatedCandleSequence) -> Path:
        """Persist a validated sequence immutably and return its canonical path."""


class HistoricalIngestionError(ValueError):
    """Raised when a fetched range cannot represent the requested dataset."""


@dataclass(frozen=True, slots=True)
class PersistedHistoricalDataset:
    """Explicit immutable outcome of successful canonical persistence."""

    identity: DatasetIdentity
    path: Path

    def __post_init__(self) -> None:
        if not isinstance(self.identity, DatasetIdentity):
            raise HistoricalIngestionError("persisted result requires a DatasetIdentity")
        if self.identity.validation_status is not DatasetValidationStatus.VALIDATED:
            raise HistoricalIngestionError("persisted result requires a validated identity")
        if not isinstance(self.path, Path):
            raise HistoricalIngestionError("canonical dataset writer must return a Path")


def _require_minute_aligned(value: datetime, field_name: str) -> None:
    require_utc(value, field_name)
    if value.second != 0 or value.microsecond != 0:
        raise ValueError(f"{field_name} must be aligned to an exact UTC minute")


def ingest_historical_range(
    range_fetcher: HistoricalCandleRangeFetcher,
    *,
    symbol: str,
    interval: str,
    start_open_time: datetime,
    end_open_time_exclusive: datetime,
    source: str,
    schema_version: str,
    ingestion_version: str,
) -> ValidatedCandleSequence:
    """Fetch and canonically validate ``[start_open_time, end_open_time_exclusive)``."""
    require_v1_symbol(symbol)
    if interval != V1_INTERVAL:
        raise ValueError(f"interval must be {V1_INTERVAL!r}")
    _require_minute_aligned(start_open_time, "start_open_time")
    _require_minute_aligned(end_open_time_exclusive, "end_open_time_exclusive")
    if end_open_time_exclusive <= start_open_time:
        raise ValueError("end_open_time_exclusive must be after start_open_time")
    require_non_empty(source, "source")
    require_non_empty(schema_version, "schema_version")
    require_non_empty(ingestion_version, "ingestion_version")

    candidates = tuple(
        range_fetcher.fetch_open_time_range(
            symbol=symbol,
            interval=interval,
            start_open_time=start_open_time,
            end_open_time_exclusive=end_open_time_exclusive,
        )
    )
    if not candidates:
        raise HistoricalIngestionError("historical range returned no candles")
    if any(
        not start_open_time <= candle.open_time < end_open_time_exclusive
        for candle in candidates
    ):
        raise HistoricalIngestionError(
            "historical range returned a candle outside the requested open-time range"
        )

    identity = DatasetIdentity(
        symbol=symbol,
        timeframe=interval,
        start_time=candidates[0].open_time,
        end_time=candidates[-1].open_time,
        source=source,
        schema_version=schema_version,
        ingestion_version=ingestion_version,
    )
    validated = validate_candle_sequence(identity, candidates)

    expected_count = (end_open_time_exclusive - start_open_time) // _ONE_MINUTE
    expected_last_open_time = end_open_time_exclusive - _ONE_MINUTE
    if (
        validated.candles[0].open_time != start_open_time
        or validated.candles[-1].open_time != expected_last_open_time
        or len(validated.candles) != expected_count
    ):
        raise HistoricalIngestionError(
            "historical candle sequence is incomplete for the requested open-time range"
        )

    return validated


def _persist_historical_sequence(
    dataset_writer: CanonicalCandleDatasetWriter,
    sequence: ValidatedCandleSequence,
) -> PersistedHistoricalDataset:
    path = dataset_writer.write(sequence)
    return PersistedHistoricalDataset(identity=sequence.identity, path=path)


def ingest_and_persist_historical_range(
    range_fetcher: HistoricalCandleRangeFetcher,
    dataset_writer: CanonicalCandleDatasetWriter,
    *,
    symbol: str,
    interval: str,
    start_open_time: datetime,
    end_open_time_exclusive: datetime,
    source: str,
    schema_version: str,
    ingestion_version: str,
) -> PersistedHistoricalDataset:
    """Acquire through the canonical ingestion path and persist its result."""
    sequence = ingest_historical_range(
        range_fetcher,
        symbol=symbol,
        interval=interval,
        start_open_time=start_open_time,
        end_open_time_exclusive=end_open_time_exclusive,
        source=source,
        schema_version=schema_version,
        ingestion_version=ingestion_version,
    )
    return _persist_historical_sequence(dataset_writer, sequence)


def _revalidate_existing_sequence(
    existing: ValidatedCandleSequence,
) -> ValidatedCandleSequence:
    if not isinstance(existing, ValidatedCandleSequence):
        raise HistoricalIngestionError(
            "incremental extension requires a ValidatedCandleSequence"
        )
    if type(existing.candles) is not tuple:
        raise HistoricalIngestionError(
            "existing validated sequence must contain its canonical candle tuple"
        )

    # Frozen dataclasses can still be forged through low-level mutation. Re-run
    # both contract constructors and the shared sequence invariants before any
    # request is derived from the base dataset.
    replace(existing.identity)
    checked = ValidatedCandleSequence(existing.identity, existing.candles)
    candles = tuple(replace(candle) for candle in checked.candles)
    return ValidatedCandleSequence(checked.identity, candles)


def extend_and_persist_historical_range(
    existing: ValidatedCandleSequence,
    range_fetcher: HistoricalCandleRangeFetcher,
    dataset_writer: CanonicalCandleDatasetWriter,
    *,
    end_open_time_exclusive: datetime,
    ingestion_version: str,
) -> PersistedHistoricalDataset:
    """Publish a new immutable version by fetching only the missing suffix."""
    base = _revalidate_existing_sequence(existing)
    require_non_empty(ingestion_version, "ingestion_version")
    if ingestion_version == base.identity.ingestion_version:
        raise HistoricalIngestionError(
            "incremental ingestion_version must differ from the existing version"
        )

    extension_start = base.identity.end_time + _ONE_MINUTE
    _require_minute_aligned(extension_start, "extension_start_open_time")
    _require_minute_aligned(end_open_time_exclusive, "end_open_time_exclusive")
    if end_open_time_exclusive <= extension_start:
        raise HistoricalIngestionError(
            "end_open_time_exclusive must be after the extension start"
        )

    extension = ingest_historical_range(
        range_fetcher,
        symbol=base.identity.symbol,
        interval=base.identity.timeframe,
        start_open_time=extension_start,
        end_open_time_exclusive=end_open_time_exclusive,
        source=base.identity.source,
        schema_version=base.identity.schema_version,
        ingestion_version=ingestion_version,
    )
    combined_candles = base.candles + extension.candles
    combined_identity = DatasetIdentity(
        symbol=base.identity.symbol,
        timeframe=base.identity.timeframe,
        start_time=base.identity.start_time,
        end_time=extension.candles[-1].open_time,
        source=base.identity.source,
        schema_version=base.identity.schema_version,
        ingestion_version=ingestion_version,
    )
    combined = validate_candle_sequence(combined_identity, combined_candles)
    return _persist_historical_sequence(dataset_writer, combined)

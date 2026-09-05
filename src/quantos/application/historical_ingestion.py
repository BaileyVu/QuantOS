"""Application orchestration for validated in-memory historical ingestion."""

from __future__ import annotations

from datetime import datetime, timedelta
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


class HistoricalIngestionError(ValueError):
    """Raised when a fetched range cannot represent the requested dataset."""


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

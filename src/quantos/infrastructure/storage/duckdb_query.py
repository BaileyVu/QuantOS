"""Read-only typed DuckDB queries over one verified canonical Parquet dataset."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sys

import duckdb

from quantos.domain.common import require_utc
from quantos.domain.market_data import (
    Candle,
    DatasetIdentity,
    ValidatedCandleSequence,
    validate_candle_sequence,
)
from quantos.infrastructure.storage.parquet import ParquetCandleDatasetStore

_ONE_MINUTE = timedelta(minutes=1)
_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_DECIMAL_FIELDS = ("open", "high", "low", "close", "volume", "quote_volume")
_DUCKDB_GLOB_CHARACTERS = frozenset("*?[]")

_OPEN_TIME_RANGE_QUERY = """
SELECT
    symbol,
    interval,
    epoch_us(open_time) AS open_time_us,
    epoch_us(close_time) AS close_time_us,
    "open",
    high,
    low,
    "close",
    volume,
    quote_volume,
    trade_count
FROM read_parquet(?, hive_partitioning = false, union_by_name = false)
WHERE epoch_us(open_time) >= ?
  AND epoch_us(open_time) < ?
ORDER BY open_time
"""


class DuckDBQueryError(ValueError):
    """A typed analytical query cannot be completed without loss or ambiguity."""


def _minute_aligned_utc(value: datetime, field_name: str) -> datetime:
    # A datetime subclass can carry precision that Python/DuckDB conversion
    # silently discards. Query boundaries use the exact built-in representation.
    if type(value) is not datetime:
        raise DuckDBQueryError(f"{field_name} must be a built-in datetime")
    try:
        require_utc(value, field_name)
    except (TypeError, ValueError) as error:
        raise DuckDBQueryError(str(error)) from error
    if value.second != 0 or value.microsecond != 0:
        raise DuckDBQueryError(f"{field_name} must be aligned to an exact UTC minute")
    return value.astimezone(timezone.utc)


def _epoch_microseconds(value: datetime) -> int:
    delta = value - _UTC_EPOCH
    return ((delta.days * 86_400 + delta.seconds) * 1_000_000) + delta.microseconds


def _utc_datetime(value: object, field_name: str) -> datetime:
    if type(value) is not int:
        raise DuckDBQueryError(f"DuckDB {field_name} must be an integer microsecond value")
    try:
        return _UTC_EPOCH + timedelta(microseconds=value)
    except OverflowError as error:
        raise DuckDBQueryError(f"DuckDB {field_name} is outside the datetime range") from error


def _decimal(value: object, field_name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise DuckDBQueryError(f"DuckDB {field_name} must be an exact finite Decimal")
    return value


def _candle_from_row(row: object) -> Candle:
    if not isinstance(row, (tuple, list)) or len(row) != 11:
        raise DuckDBQueryError("DuckDB returned an unexpected candle row shape")
    symbol, interval = row[0], row[1]
    if type(symbol) is not str or type(interval) is not str:
        raise DuckDBQueryError("DuckDB symbol and interval must be built-in strings")
    if type(row[10]) is not int:
        raise DuckDBQueryError("DuckDB trade_count must be an integer")
    try:
        return Candle(
            symbol=symbol,
            interval=interval,
            open_time=_utc_datetime(row[2], "open_time"),
            close_time=_utc_datetime(row[3], "close_time"),
            open=_decimal(row[4], "open"),
            high=_decimal(row[5], "high"),
            low=_decimal(row[6], "low"),
            close=_decimal(row[7], "close"),
            volume=_decimal(row[8], "volume"),
            quote_volume=_decimal(row[9], "quote_volume"),
            trade_count=row[10],
        )
    except DuckDBQueryError:
        raise
    except (TypeError, ValueError, OverflowError) as error:
        raise DuckDBQueryError(f"DuckDB returned an invalid canonical candle: {error}") from error


def _exact_candle_values_match(
    actual: tuple[Candle, ...], expected: tuple[Candle, ...]
) -> bool:
    if actual != expected:
        return False
    return all(
        getattr(actual_candle, name).as_tuple()
        == getattr(expected_candle, name).as_tuple()
        for actual_candle, expected_candle in zip(actual, expected, strict=True)
        for name in _DECIMAL_FIELDS
    )


class DuckDBCandleDatasetQuery:
    """Run fixed analytical range queries against one canonical Parquet path."""

    def __init__(self, canonical_store: ParquetCandleDatasetStore) -> None:
        if not isinstance(canonical_store, ParquetCandleDatasetStore):
            raise TypeError("canonical_store must be a ParquetCandleDatasetStore")
        self._canonical_store = canonical_store

    def query_open_time_range(
        self,
        path: Path,
        *,
        start_open_time: datetime,
        end_open_time_exclusive: datetime,
    ) -> ValidatedCandleSequence:
        """Return the complete canonical ``[start, end)`` subset in open-time order."""
        start = _minute_aligned_utc(start_open_time, "start_open_time")
        end = _minute_aligned_utc(end_open_time_exclusive, "end_open_time_exclusive")
        if end <= start:
            raise DuckDBQueryError("end_open_time_exclusive must be after start_open_time")

        selected_path = Path(path)
        if any(character in str(selected_path) for character in _DUCKDB_GLOB_CHARACTERS):
            raise DuckDBQueryError("canonical Parquet paths must not contain glob characters")

        # Phase 2C1 remains authoritative. DuckDB is not opened until the full
        # selected file has passed checksum, schema, metadata, and Domain checks.
        canonical = self._canonical_store.read(selected_path)
        if selected_path != self._canonical_store.dataset_path(canonical.identity):
            raise DuckDBQueryError("selected Parquet file is not at its canonical dataset path")

        dataset_end_exclusive = canonical.identity.end_time + _ONE_MINUTE
        if start < canonical.identity.start_time or end > dataset_end_exclusive:
            raise DuckDBQueryError("requested range is not fully contained in the dataset")

        start_index = (start - canonical.identity.start_time) // _ONE_MINUTE
        end_index = (end - canonical.identity.start_time) // _ONE_MINUTE
        expected_count = (end - start) // _ONE_MINUTE
        expected_candles = canonical.candles[start_index:end_index]
        if len(expected_candles) != expected_count:
            raise DuckDBQueryError("canonical dataset cannot satisfy the requested range")

        connection: duckdb.DuckDBPyConnection | None = None
        try:
            connection = duckdb.connect(database=":memory:")
            rows = connection.execute(
                _OPEN_TIME_RANGE_QUERY,
                (str(selected_path), _epoch_microseconds(start), _epoch_microseconds(end)),
            ).fetchall()
        except duckdb.Error as error:
            raise DuckDBQueryError(f"DuckDB range query failed: {error}") from error
        finally:
            if connection is not None:
                active_error = sys.exception()
                try:
                    connection.close()
                except duckdb.Error as close_error:
                    if active_error is not None:
                        active_error.add_note(f"DuckDB connection cleanup also failed: {close_error}")
                    else:
                        raise DuckDBQueryError(
                            f"DuckDB connection cleanup failed: {close_error}"
                        ) from close_error

        if len(rows) != expected_count:
            raise DuckDBQueryError("DuckDB returned an unexpected candle count")
        candles = tuple(_candle_from_row(row) for row in rows)

        identity = DatasetIdentity(
            symbol=canonical.identity.symbol,
            timeframe=canonical.identity.timeframe,
            start_time=start,
            end_time=end - _ONE_MINUTE,
            source=canonical.identity.source,
            schema_version=canonical.identity.schema_version,
            ingestion_version=canonical.identity.ingestion_version,
        )
        try:
            result = validate_candle_sequence(identity, candles)
        except (TypeError, ValueError) as error:
            raise DuckDBQueryError(f"DuckDB result failed canonical validation: {error}") from error
        if not _exact_candle_values_match(result.candles, expected_candles):
            raise DuckDBQueryError("DuckDB result differs from the verified canonical dataset")

        # A direct path scan follows the initial canonical read. Re-read before
        # returning so a replacement/corruption during that interval fails closed.
        if self._canonical_store.read(selected_path) != canonical:
            raise DuckDBQueryError("canonical dataset changed during the DuckDB query")
        return result

"""Minimal verified DuckDB round-trip for one aggregate-trade Parquet archive."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys

import duckdb

from quantos.domain.market_data.research_events import (
    AggregateTrade,
    AggregateTradeDatasetIdentity,
    SourceTimestampUnit,
    ValidatedAggregateTradeArchive,
    validate_aggregate_trade_sequence,
)
from quantos.infrastructure.storage.aggregate_trade_parquet import (
    ParquetAggregateTradeArchiveStore,
)

_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_DUCKDB_GLOB_CHARACTERS = frozenset("*?[]")
_READ_ALL_QUERY = """
SELECT
    symbol,
    aggregate_trade_id,
    price,
    price_text,
    quantity,
    quantity_text,
    first_trade_id,
    last_trade_id,
    epoch_us(event_time) AS event_time_us,
    source_timestamp,
    source_timestamp_unit,
    buyer_is_maker,
    best_price_match
FROM read_parquet(?, hive_partitioning = false, union_by_name = false)
ORDER BY event_time, aggregate_trade_id
"""


class DuckDBAggregateTradeQueryError(ValueError):
    """A verified event archive cannot be reproduced exactly through DuckDB."""


def _event_from_row(row: object, row_index: int) -> AggregateTrade:
    if type(row) not in (tuple, list) or len(row) != 13:
        raise DuckDBAggregateTradeQueryError(
            f"DuckDB row {row_index} has an unexpected shape"
        )
    if (
        type(row[0]) is not str
        or type(row[3]) is not str
        or type(row[5]) is not str
        or type(row[10]) is not str
    ):
        raise DuckDBAggregateTradeQueryError(
            f"DuckDB row {row_index} contains invalid string fields"
        )
    try:
        price = Decimal(row[3])
        quantity = Decimal(row[5])
    except (InvalidOperation, TypeError, ValueError) as error:
        raise DuckDBAggregateTradeQueryError(
            f"DuckDB row {row_index} contains invalid Decimal text"
        ) from error
    if type(row[2]) is not Decimal or price != row[2]:
        raise DuckDBAggregateTradeQueryError(
            f"DuckDB row {row_index} price representations differ"
        )
    if type(row[4]) is not Decimal or quantity != row[4]:
        raise DuckDBAggregateTradeQueryError(
            f"DuckDB row {row_index} quantity representations differ"
        )
    if type(row[8]) is not int:
        raise DuckDBAggregateTradeQueryError(
            f"DuckDB row {row_index} event_time must be integer microseconds"
        )
    try:
        event_time = _UTC_EPOCH + timedelta(microseconds=row[8])
        return AggregateTrade(
            symbol=row[0],
            aggregate_trade_id=row[1],
            price=price,
            quantity=quantity,
            first_trade_id=row[6],
            last_trade_id=row[7],
            event_time=event_time,
            source_timestamp=row[9],
            source_timestamp_unit=SourceTimestampUnit(row[10]),
            buyer_is_maker=row[11],
            best_price_match=row[12],
        )
    except (OverflowError, TypeError, ValueError) as error:
        raise DuckDBAggregateTradeQueryError(
            f"DuckDB row {row_index} is not a canonical aggregate trade: {error}"
        ) from error


class DuckDBAggregateTradeArchiveQuery:
    """Verify a complete canonical archive through a fixed read-only query."""

    def __init__(self, canonical_store: ParquetAggregateTradeArchiveStore) -> None:
        if type(canonical_store) is not ParquetAggregateTradeArchiveStore:
            raise TypeError(
                "canonical_store must be a ParquetAggregateTradeArchiveStore"
            )
        self._canonical_store = canonical_store

    def read_all(self, path: Path) -> ValidatedAggregateTradeArchive:
        selected_path = Path(path)
        if any(character in str(selected_path) for character in _DUCKDB_GLOB_CHARACTERS):
            raise DuckDBAggregateTradeQueryError(
                "canonical Parquet paths must not contain glob characters"
            )
        canonical = self._canonical_store.read(selected_path)
        if selected_path != self._canonical_store.dataset_path(canonical.manifest):
            raise DuckDBAggregateTradeQueryError(
                "selected file is not at its canonical archive path"
            )
        connection: duckdb.DuckDBPyConnection | None = None
        try:
            connection = duckdb.connect(database=":memory:")
            rows = connection.execute(_READ_ALL_QUERY, (str(selected_path),)).fetchall()
        except duckdb.Error as error:
            raise DuckDBAggregateTradeQueryError(
                f"DuckDB aggregate-trade query failed: {error}"
            ) from error
        finally:
            if connection is not None:
                active_error = sys.exception()
                try:
                    connection.close()
                except duckdb.Error as close_error:
                    if active_error is not None:
                        active_error.add_note(
                            f"DuckDB cleanup also failed: {close_error}"
                        )
                    else:
                        raise DuckDBAggregateTradeQueryError(
                            f"DuckDB cleanup failed: {close_error}"
                        ) from close_error
        if len(rows) != len(canonical.sequence.events):
            raise DuckDBAggregateTradeQueryError(
                "DuckDB returned an unexpected aggregate-trade count"
            )
        events = tuple(_event_from_row(row, index) for index, row in enumerate(rows))
        identity = AggregateTradeDatasetIdentity(
            symbol=canonical.manifest.symbol,
            requested_start_time=canonical.manifest.requested_start_time,
            requested_end_time_exclusive=canonical.manifest.requested_end_time_exclusive,
            source_timestamp_unit=canonical.manifest.source_timestamp_unit,
            schema_version=canonical.manifest.schema_version,
            normalizer_version=canonical.manifest.normalizer_version,
            provenance=canonical.manifest.provenance,
            research_role=canonical.manifest.research_role,
        )
        try:
            result = ValidatedAggregateTradeArchive(
                validate_aggregate_trade_sequence(identity, events),
                canonical.manifest,
            )
        except (TypeError, ValueError, OverflowError) as error:
            raise DuckDBAggregateTradeQueryError(
                f"DuckDB result failed canonical validation: {error}"
            ) from error
        if result != canonical:
            raise DuckDBAggregateTradeQueryError(
                "DuckDB result differs from the verified canonical archive"
            )
        if self._canonical_store.read(selected_path) != canonical:
            raise DuckDBAggregateTradeQueryError(
                "canonical archive changed during the DuckDB query"
            )
        return result

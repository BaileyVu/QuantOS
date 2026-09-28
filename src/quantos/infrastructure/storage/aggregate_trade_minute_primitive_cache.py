"""Atomic, range-independent Parquet cache of verified daily minute primitives.

Derived and rebuildable: the application must establish current authoritative
source integrity before using these artifacts. A corrupt entry raises rather
than being silently replaced. No scientific dataset schema is changed.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

from quantos.application.aggregate_trade_minute_primitive_cache import (
    DailyAggregateTradeMinutePrimitives,
)
from quantos.domain.market_data.research_events import (
    AGGREGATE_TRADE_MINUTE_AGGREGATION_VERSION,
    AGGREGATE_TRADE_MINUTE_INTERVAL_SPECIFICATION,
    AGGREGATE_TRADE_MINUTE_STATE_SCHEMA_VERSION,
    AggregateTradeArchiveManifest,
    AggregateTradeMinutePrimitives,
    aggregate_trade_archive_manifest_id,
)

CACHE_SCHEMA_VERSION = "aggregate-trade-daily-minute-primitives-v1"
_METADATA_KEY = b"quantos.daily_minute_primitive_cache"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_DECIMALS = (
    "total_base_quantity", "total_quote_notional",
    "aggressive_buy_base_quantity", "aggressive_sell_base_quantity",
    "aggressive_buy_quote_notional", "aggressive_sell_quote_notional",
)
_TIMES = ("minute_start_time", "minute_end_time_exclusive", "first_event_time", "last_event_time")
_NULLABLE = ("first_event_time", "last_event_time", "first_aggregate_trade_id", "last_aggregate_trade_id")
PRIMITIVE_CACHE_SCHEMA = pa.schema([
    pa.field(
        field.name,
        pa.string() if field.name in ("symbol", *_DECIMALS)
        else pa.timestamp("us", tz="UTC") if field.name in _TIMES else pa.int64(),
        nullable=field.name in _NULLABLE,
    )
    for field in fields(AggregateTradeMinutePrimitives)
])


class AggregateTradePrimitiveCacheError(ValueError):
    """Derived cache integrity, lineage or publication could not be established."""


def _json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _lineage(source: AggregateTradeArchiveManifest) -> dict:
    if type(source) is not AggregateTradeArchiveManifest:
        raise AggregateTradePrimitiveCacheError("cache requires an authoritative source manifest")
    AggregateTradeArchiveManifest.__post_init__(source)
    return {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "symbol": source.symbol,
        "source_date": source.source_date.isoformat(),
        "source_manifest_id": aggregate_trade_archive_manifest_id(source),
        "source_revision_id": source.source_revision_id,
        "source_dataset_id": source.dataset_id,
        "source_timestamp_unit": source.source_timestamp_unit.value,
        "canonical_sequence_sha256": source.canonical_sequence_sha256,
        "aggregation_version": AGGREGATE_TRADE_MINUTE_AGGREGATION_VERSION,
        "state_schema_version": AGGREGATE_TRADE_MINUTE_STATE_SCHEMA_VERSION,
        "minute_interval_specification": AGGREGATE_TRADE_MINUTE_INTERVAL_SPECIFICATION,
    }


def _records(daily: DailyAggregateTradeMinutePrimitives) -> list[dict]:
    records = []
    for row in daily.rows:
        record = {}
        for name in PRIMITIVE_CACHE_SCHEMA.names:
            value = getattr(row, name)
            if name in _DECIMALS:
                # Decimal's exact string round trip retains coefficient/exponent,
                # including trailing zeros. No binary floats or context rounding.
                value = str(value)
            elif name in _TIMES and value is not None:
                delta = value - _EPOCH
                value = ((delta.days * 86400 + delta.seconds) * 1_000_000
                         + delta.microseconds)
            record[name] = value
        records.append(record)
    return records


def _metadata(lineage, records) -> dict:
    return {
        "lineage": lineage,
        "cache_id": sha256(_json(lineage)).hexdigest(),
        "row_count": 1440,
        "content_sha256": sha256(_json({"lineage": lineage, "rows": records})).hexdigest(),
    }


class ParquetAggregateTradeMinutePrimitiveCache:
    """One immutable cache artifact per source lineage and semantic version.

    The root is caller-owned. Paths are built only from validated symbols, dates
    and digests; links escaping the resolved root are rejected. Publication uses
    a fsynced, read-verified temporary file and an exclusive atomic hard link.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()

    def path_for(self, source: AggregateTradeArchiveManifest) -> Path:
        lineage = _lineage(source)
        cache_id = sha256(_json(lineage)).hexdigest()
        path = (self._root / "market_data" / "aggregate_trade_minute_primitives"
                / "cache" / CACHE_SCHEMA_VERSION / source.symbol
                / source.source_date.isoformat() / f"{cache_id}.parquet")
        if not path.resolve().is_relative_to(self._root):
            raise AggregateTradePrimitiveCacheError("cache path escapes its root")
        return path

    def _read(self, path, source) -> DailyAggregateTradeMinutePrimitives:
        try:
            with path.open("rb") as handle:
                with pq.ParquetFile(handle, page_checksum_verification=True) as parquet:
                    if not parquet.schema_arrow.equals(PRIMITIVE_CACHE_SCHEMA, check_metadata=False):
                        raise AggregateTradePrimitiveCacheError("cache Arrow schema differs")
                    if parquet.metadata.num_rows != 1440:
                        raise AggregateTradePrimitiveCacheError("cache must contain 1440 rows")
                    metadata = parquet.metadata.metadata or {}
                    payload = metadata[_METADATA_KEY]
                    parsed = json.loads(payload)
                    if type(parsed) is not dict or _json(parsed) != payload or parsed.get("lineage") != _lineage(source):
                        raise AggregateTradePrimitiveCacheError("cache metadata or source lineage differs")
                    table = parquet.read(use_threads=False, use_pandas_metadata=False)
            table.validate(full=True)
            values = {}
            for field, column in zip(PRIMITIVE_CACHE_SCHEMA, table.columns, strict=True):
                if not field.nullable and column.null_count:
                    raise AggregateTradePrimitiveCacheError("cache has a null required value")
                if field.name in _TIMES:
                    column = column.cast(pa.int64(), safe=True)
                values[field.name] = column.to_pylist()
            records = [dict(zip(values, row, strict=True)) for row in zip(*values.values(), strict=True)]
            if payload != _json(_metadata(_lineage(source), records)):
                raise AggregateTradePrimitiveCacheError("cache content hash or metadata differs")
            rows = []
            for record in records:
                restored = dict(record)
                for name in _DECIMALS:
                    restored[name] = Decimal(record[name])
                    if not restored[name].is_finite() or str(restored[name]) != record[name]:
                        raise AggregateTradePrimitiveCacheError("cache decimal is not canonically encoded")
                for name in _TIMES:
                    value = record[name]
                    restored[name] = None if value is None else _EPOCH + timedelta(microseconds=value)
                rows.append(AggregateTradeMinutePrimitives(**restored))
            return DailyAggregateTradeMinutePrimitives(source, tuple(rows))
        except AggregateTradePrimitiveCacheError:
            raise
        except (OSError, pa.ArrowException, TypeError, ValueError, KeyError, OverflowError, InvalidOperation) as error:
            raise AggregateTradePrimitiveCacheError(f"cannot verify primitive cache: {error}") from error

    def load(self, source: AggregateTradeArchiveManifest) -> DailyAggregateTradeMinutePrimitives | None:
        path = self.path_for(source)
        # Only absent artifacts are misses; corrupt/unreadable artifacts fail closed.
        try:
            path.stat()
        except FileNotFoundError:
            return None
        return self._read(path, source)

    def write(self, daily: DailyAggregateTradeMinutePrimitives) -> None:
        if type(daily) is not DailyAggregateTradeMinutePrimitives:
            raise AggregateTradePrimitiveCacheError("cache write requires daily primitives")
        DailyAggregateTradeMinutePrimitives.__post_init__(daily)
        for row in daily.rows:
            AggregateTradeMinutePrimitives.__post_init__(row)
        records = _records(daily)
        metadata = _metadata(_lineage(daily.source), records)
        destination = self.path_for(daily.source)

        def require_same(path):
            stored = self._read(path, daily.source)
            if _metadata(_lineage(stored.source), _records(stored)) != metadata:
                raise AggregateTradePrimitiveCacheError("immutable primitive cache collision")

        if destination.exists():
            require_same(destination)
            return
        temporary_path = None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.path_for(daily.source)  # Recheck resolved containment after mkdir.
            arrays = []
            for field in PRIMITIVE_CACHE_SCHEMA:
                values = [record[field.name] for record in records]
                if field.name in _TIMES:
                    array = pa.array(values, type=pa.int64()).cast(field.type, safe=True)
                else:
                    array = pa.array(values, type=field.type, safe=True)
                arrays.append(array)
            table = pa.Table.from_arrays(arrays, schema=PRIMITIVE_CACHE_SCHEMA.with_metadata(
                {_METADATA_KEY: _json(metadata)}
            ))
            with tempfile.NamedTemporaryFile(mode="w+b", dir=destination.parent,
                                             prefix=".primitive-", suffix=".tmp", delete=False) as temporary:
                temporary_path = Path(temporary.name)
                pq.write_table(table, temporary, version="2.6", compression="zstd",
                               use_dictionary=True, write_statistics=True,
                               write_page_checksum=True, store_schema=True,
                               use_deprecated_int96_timestamps=False,
                               allow_truncated_timestamps=False, data_page_version="2.0")
                temporary.flush()
                os.fsync(temporary.fileno())
            require_same(temporary_path)
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                require_same(destination)
        except AggregateTradePrimitiveCacheError:
            raise
        except (OSError, pa.ArrowException, TypeError, ValueError, OverflowError) as error:
            raise AggregateTradePrimitiveCacheError(f"cannot publish primitive cache: {error}") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

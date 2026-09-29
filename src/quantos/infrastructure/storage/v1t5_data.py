"""V1-T5 causal aggregation of one immutable canonical candle segment.

Array columns: open-time Unix seconds, open, high, low, close, base volume,
quote volume, trade count. Arrays are research numerical views, never canonical
publication or exchange-order prices. Exact Decimal domain validation occurs
before conversion. Callers must never concatenate disconnected segments.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from quantos.domain.market_data import Candle, DatasetIdentity, validate_candle_sequence
from quantos.infrastructure.storage.parquet import _parse_metadata, _require_schema

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_VALUES = ("open", "high", "low", "close", "volume", "quote_volume", "trade_count")


@dataclass(frozen=True)
class CanonicalSegment:
    dataset_id: str
    file_sha256: str
    identity: DatasetIdentity
    minutes: np.ndarray


@dataclass(frozen=True)
class AggregatedSegment:
    dataset_id: str
    minutes_per_bar: int
    bars: np.ndarray
    next_minutes: np.ndarray
    minute_rows: np.ndarray


def load_canonical_segment(path: Path) -> CanonicalSegment:
    """Validate exact canonical records in bounded batches, including file order.

    Reading 2026 here is integrity-only. Economic consumers must explicitly
    restrict the returned view to authorized periods before model evaluation.
    """
    digest = sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
        source.seek(0)
        with pq.ParquetFile(source, page_checksum_verification=True) as parquet:
            _require_schema(parquet)
            metadata = parquet.metadata.metadata or {}
            identity = _parse_metadata(metadata)
            if identity.symbol != "BTCUSDT":
                raise ValueError("V1-T5 requires BTCUSDT")
            result = np.empty((parquet.metadata.num_rows, 8), dtype=np.float64)
            offset = 0
            previous = None
            for batch in parquet.iter_batches(batch_size=32768, use_threads=False):
                batch.validate(full=True)
                if any(column.null_count for column in batch.columns):
                    raise ValueError("canonical candle columns contain nulls")
                values = {name: batch.column(name).to_pylist() for name in batch.schema.names
                          if name not in ("open_time", "close_time")}
                for name in ("open_time", "close_time"):
                    values[name] = [_EPOCH + timedelta(microseconds=t) for t in
                                    batch.column(name).cast(pa.int64()).to_pylist()]
                candles = tuple(Candle(**{name: values[name][i] for name in values})
                                for i in range(batch.num_rows))
                if previous is not None and candles[0].open_time != previous + timedelta(minutes=1):
                    raise ValueError("gap, duplicate or reordering across canonical batches")
                local = DatasetIdentity(identity.symbol, identity.timeframe,
                                        candles[0].open_time, candles[-1].open_time,
                                        identity.source, identity.schema_version,
                                        identity.ingestion_version)
                validate_candle_sequence(local, candles)
                for i, candle in enumerate(candles):
                    if candle.open_time.second or candle.open_time.microsecond:
                        raise ValueError("unaligned canonical minute")
                    if not (candle.open_time + timedelta(minutes=1, milliseconds=-1)
                            <= candle.close_time <= candle.open_time + timedelta(minutes=1)):
                        raise ValueError("unexpected canonical close-time convention")
                    result[offset + i] = [(candle.open_time - _EPOCH).total_seconds(),
                                          *(float(getattr(candle, name)) for name in _VALUES)]
                previous = candles[-1].open_time
                offset += len(candles)
            if offset == 0 or result[0, 0] != (identity.start_time - _EPOCH).total_seconds():
                raise ValueError("canonical start does not match identity")
            if previous != identity.end_time:
                raise ValueError("canonical end does not match identity")
            if not np.isfinite(result).all():
                raise ValueError("nonfinite research conversion")
            result.setflags(write=False)
            return CanonicalSegment(metadata[b"quantos.dataset_id"].decode(),
                                    digest.hexdigest(), identity, result)


def aggregate_segment(segment: CanonicalSegment, minutes: int,
                      *, end_exclusive: datetime) -> AggregatedSegment:
    """Aggregate completed UTC bins and retain an immediate next-minute fill row.

    Bins use [open, open + cadence). Only bins with every required minute and
    one following completed 1m candle strictly inside end_exclusive are returned.
    next_minutes[i] opens exactly at bars[i,0] + cadence*60. Its close becomes
    available one minute later. No economic calculation is performed here.
    """
    if minutes not in (30, 60):
        raise ValueError("V1-T5 supports only 30m and 1h aggregates")
    if end_exclusive.tzinfo is None or end_exclusive.utcoffset() != timedelta(0):
        raise ValueError("end_exclusive must be UTC")
    rows = segment.minutes
    if rows.ndim != 2 or rows.shape[1] != 8 or len(rows) == 0:
        raise ValueError("empty or malformed segment")
    if not np.all(np.diff(rows[:, 0]) == 60) or not np.all(rows[:, 0] % 60 == 0):
        raise ValueError("aggregation cannot cross a gap or reorder minutes")
    cutoff = (end_exclusive - _EPOCH).total_seconds()
    rows = rows[rows[:, 0] + 60 <= cutoff]
    width = minutes * 60
    start = next((i for i, row in enumerate(rows) if row[0] % width == 0), len(rows))
    count = max(0, (len(rows) - start - 1) // minutes)
    if not count:
        return AggregatedSegment(segment.dataset_id, minutes, np.empty((0, 8)),
                                 np.empty((0, 8)), rows)
    blocks = rows[start:start + count * minutes].reshape(count, minutes, 8)
    bars = np.column_stack((blocks[:, 0, 0], blocks[:, 0, 1],
                            blocks[:, :, 2].max(axis=1), blocks[:, :, 3].min(axis=1),
                            blocks[:, -1, 4], blocks[:, :, 5:].sum(axis=1)))
    next_minutes = rows[start + np.arange(1, count + 1) * minutes].copy()
    bars.setflags(write=False)
    next_minutes.setflags(write=False)
    return AggregatedSegment(segment.dataset_id, minutes, bars, next_minutes, rows)


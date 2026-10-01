"""Compressed immutable HFT event recording and research export."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


class HftEventRecorder:
    """Writes bounded immutable ZSTD Parquet batches, never raw databases."""

    def __init__(self, root: Path, session_id: str, batch_size: int = 1000) -> None:
        if not session_id or any(value in session_id for value in ("/", "\\", "..")):
            raise ValueError("invalid HFT session ID")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.directory = root / session_id
        self.batch_size = batch_size
        self._rows: list[dict[str, Any]] = []
        self._sequence = 0

    def append(
        self,
        event: object,
        processing_completed_at: datetime,
        processing_monotonic_ns: int | None = None,
    ) -> Path | None:
        if not is_dataclass(event):
            raise TypeError("recorded HFT event must be a dataclass")
        payload = _json_value(asdict(event))
        row = {
            "event_type": type(event).__name__,
            "symbol": payload.get("symbol"),
            "exchange_time": payload.get("exchange_time"),
            "received_at": payload.get("received_at"),
            "processing_completed_at": processing_completed_at.isoformat(),
            "processing_monotonic_ns": processing_monotonic_ns,
            "payload_json": json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ),
        }
        self._rows.append(row)
        if len(self._rows) >= self.batch_size:
            return self.flush()
        return None

    def checkpoint(
        self,
        symbol: str,
        update_id: int,
        bids: tuple[tuple[Decimal, Decimal], ...],
        asks: tuple[tuple[Decimal, Decimal], ...],
        timestamp: datetime,
    ) -> Path | None:
        payload = {
            "symbol": symbol,
            "update_id": update_id,
            "bids": _json_value(bids),
            "asks": _json_value(asks),
        }
        self._rows.append({
            "event_type": "BookCheckpoint",
            "symbol": symbol,
            "exchange_time": timestamp.isoformat(),
            "received_at": timestamp.isoformat(),
            "processing_completed_at": timestamp.isoformat(),
            "payload_json": json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ),
        })
        if len(self._rows) >= self.batch_size:
            return self.flush()
        return None

    def flush(self) -> Path | None:
        if not self._rows:
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"events-{self._sequence:08d}.parquet"
        if path.exists():
            raise FileExistsError(f"HFT event partition already exists: {path}")
        table = pa.Table.from_pylist(self._rows)
        pq.write_table(table, path, compression="zstd", use_dictionary=True)
        self._rows = []
        self._sequence += 1
        return path


def export_hftbacktest_research(
    partitions: tuple[Path, ...], output: Path
) -> Path:
    """Export normalized timing/event fields for hftbacktest conversion.

    This intentionally does not invent exchange order latency or queue fields.
    Downstream research supplies those explicit simulation inputs.
    """
    if not partitions:
        raise ValueError("at least one HFT event partition is required")
    if output.exists():
        raise FileExistsError(f"research export already exists: {output}")
    tables = [pq.read_table(path) for path in partitions]
    combined = pa.concat_tables(tables)
    rows = []
    for row in combined.to_pylist():
        payload = json.loads(row["payload_json"])
        rows.append({
            "event_type": row["event_type"],
            "symbol": row["symbol"],
            "exchange_timestamp": row["exchange_time"],
            "local_timestamp": row["received_at"],
            "processing_timestamp": row["processing_completed_at"],
            "processing_monotonic_ns": row.get("processing_monotonic_ns"),
            "local_receive_monotonic_ns": payload.get(
                "received_monotonic_ns"
            ),
            "exchange_transaction_timestamp": payload.get(
                "transaction_time"
            ),
            "first_update_id": payload.get("first_update_id"),
            "final_update_id": payload.get("final_update_id"),
            "previous_final_update_id": payload.get("previous_final_update_id"),
            "trade_id": payload.get("aggregate_trade_id"),
            "price": payload.get("price"),
            "quantity": payload.get("quantity"),
            "buyer_is_maker": payload.get("buyer_is_maker"),
            "bids_json": json.dumps(payload.get("bids"), separators=(",", ":")),
            "asks_json": json.dumps(payload.get("asks"), separators=(",", ":")),
        })
    output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pylist(rows), output, compression="zstd", use_dictionary=True
    )
    return output

"""Immutable Parquet publication for completed-minute aggregate-trade state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re
import sys
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

from quantos.domain.market_data.research_events import (
    AggregateTradeMinuteAvailabilityState,
    AggregateTradeMinuteCompletenessState,
    AggregateTradeMinuteDatasetManifest,
    AggregateTradeMinuteState,
    ResearchEventValidationStatus,
    SourceTimestampUnit,
    ValidatedAggregateTradeMinuteDataset,
    aggregate_trade_minute_dataset_manifest_bytes,
    aggregate_trade_minute_dataset_manifest_from_bytes,
    canonical_decimal_text,
)

AGGREGATE_TRADE_MINUTE_STORAGE_SCHEMA_VERSION = (
    "aggregate-trade-minute-parquet-v1"
)
_SAFE_PATH_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_DECIMAL_COLUMNS = (
    "total_base_quantity",
    "total_quote_notional",
    "aggressive_buy_base_quantity",
    "aggressive_sell_base_quantity",
    "aggressive_buy_quote_notional",
    "aggressive_sell_quote_notional",
)
_TIMESTAMP_COLUMNS = (
    "minute_start_time",
    "minute_end_time_exclusive",
    "first_event_time",
    "last_event_time",
)
_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
AGGREGATE_TRADE_MINUTE_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("minute_start_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field(
            "minute_end_time_exclusive",
            pa.timestamp("us", tz="UTC"),
            nullable=False,
        ),
        pa.field("source_range_id", pa.string(), nullable=False),
        pa.field("source_manifest_id", pa.string(), nullable=False),
        pa.field("source_revision_id", pa.string(), nullable=False),
        pa.field("source_dataset_id", pa.string(), nullable=False),
        pa.field("source_timestamp_unit", pa.string(), nullable=False),
        pa.field("event_count", pa.int64(), nullable=False),
        pa.field("aggressive_buy_event_count", pa.int64(), nullable=False),
        pa.field("aggressive_sell_event_count", pa.int64(), nullable=False),
        *(
            pa.field(name, pa.string(), nullable=False)
            for name in _DECIMAL_COLUMNS
        ),
        pa.field("first_aggregate_trade_id", pa.int64(), nullable=True),
        pa.field("last_aggregate_trade_id", pa.int64(), nullable=True),
        pa.field("first_event_time", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("last_event_time", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("completeness_state", pa.string(), nullable=False),
        pa.field("availability_state", pa.string(), nullable=False),
        pa.field("validation_status", pa.string(), nullable=False),
    ]
)


class AggregateTradeMinuteStorageError(ValueError):
    """Minute-state data cannot be represented, read, or published safely."""


class AggregateTradeMinuteDatasetCollisionError(AggregateTradeMinuteStorageError):
    """An immutable minute-state identity already contains different content."""


@dataclass(frozen=True, slots=True)
class AggregateTradeMinutePublication:
    canonical_parquet_path: Path
    dataset_id: str
    manifest: AggregateTradeMinuteDatasetManifest


def _safe_segment(value: str, name: str) -> str:
    if type(value) is not str or _SAFE_PATH_SEGMENT.fullmatch(value) is None:
        raise AggregateTradeMinuteStorageError(
            f"{name} is not a safe canonical path segment"
        )
    if value in (".", ".."):
        raise AggregateTradeMinuteStorageError(f"{name} is not a safe path segment")
    return value


def _metadata(
    manifest: AggregateTradeMinuteDatasetManifest,
) -> dict[bytes, bytes]:
    return {
        b"quantos.aggregate_trade_minute_storage_schema_version": (
            AGGREGATE_TRADE_MINUTE_STORAGE_SCHEMA_VERSION.encode("ascii")
        ),
        b"quantos.aggregate_trade_minute_manifest": (
            aggregate_trade_minute_dataset_manifest_bytes(manifest)
        ),
        b"quantos.aggregate_trade_minute_dataset_id": (
            manifest.dataset_id.encode("ascii")
        ),
    }


def _parse_manifest(
    metadata: dict[bytes, bytes] | None,
) -> AggregateTradeMinuteDatasetManifest:
    metadata = metadata or {}
    try:
        version = metadata[
            b"quantos.aggregate_trade_minute_storage_schema_version"
        ].decode("ascii")
        payload = metadata[b"quantos.aggregate_trade_minute_manifest"]
        recorded_id = metadata[
            b"quantos.aggregate_trade_minute_dataset_id"
        ].decode("ascii")
    except (KeyError, UnicodeError) as error:
        raise AggregateTradeMinuteStorageError(
            "missing or invalid aggregate-trade minute-state metadata"
        ) from error
    if version != AGGREGATE_TRADE_MINUTE_STORAGE_SCHEMA_VERSION:
        raise AggregateTradeMinuteStorageError(
            "unsupported aggregate-trade minute-state storage schema"
        )
    try:
        manifest = aggregate_trade_minute_dataset_manifest_from_bytes(payload)
    except (TypeError, ValueError) as error:
        raise AggregateTradeMinuteStorageError(
            f"invalid aggregate-trade minute-state manifest: {error}"
        ) from error
    if recorded_id != manifest.dataset_id:
        raise AggregateTradeMinuteStorageError(
            "recorded minute-state dataset ID differs from manifest"
        )
    return manifest


def _table(dataset: ValidatedAggregateTradeMinuteDataset) -> pa.Table:
    dataset = ValidatedAggregateTradeMinuteDataset(
        dataset.identity, dataset.states
    )
    values: dict[str, list[object]] = {
        name: [] for name in AGGREGATE_TRADE_MINUTE_SCHEMA.names
    }
    for state in dataset.states:
        values["symbol"].append(state.symbol)
        values["minute_start_time"].append(state.minute_start_time)
        values["minute_end_time_exclusive"].append(
            state.minute_end_time_exclusive
        )
        values["source_range_id"].append(state.source_range_id)
        values["source_manifest_id"].append(state.source_manifest_id)
        values["source_revision_id"].append(state.source_revision_id)
        values["source_dataset_id"].append(state.source_dataset_id)
        values["source_timestamp_unit"].append(
            state.source_timestamp_unit.value
        )
        values["event_count"].append(state.event_count)
        values["aggressive_buy_event_count"].append(
            state.aggressive_buy_event_count
        )
        values["aggressive_sell_event_count"].append(
            state.aggressive_sell_event_count
        )
        for name in _DECIMAL_COLUMNS:
            values[name].append(canonical_decimal_text(getattr(state, name)))
        values["first_aggregate_trade_id"].append(
            state.first_aggregate_trade_id
        )
        values["last_aggregate_trade_id"].append(state.last_aggregate_trade_id)
        values["first_event_time"].append(state.first_event_time)
        values["last_event_time"].append(state.last_event_time)
        values["completeness_state"].append(state.completeness_state.value)
        values["availability_state"].append(state.availability_state.value)
        values["validation_status"].append(state.validation_status.value)
    manifest = AggregateTradeMinuteDatasetManifest.from_dataset(dataset)
    try:
        arrays = [
            pa.array(
                values[field.name],
                type=field.type,
                from_pandas=False,
                safe=True,
            )
            for field in AGGREGATE_TRADE_MINUTE_SCHEMA
        ]
        table = pa.Table.from_arrays(
            arrays,
            schema=AGGREGATE_TRADE_MINUTE_SCHEMA.with_metadata(
                _metadata(manifest)
            ),
        )
        table.validate(full=True)
        return table
    except (pa.ArrowException, TypeError, ValueError, OverflowError) as error:
        raise AggregateTradeMinuteStorageError(
            f"minute-state dataset cannot be represented exactly: {error}"
        ) from error


def _decimal_from_text(value: object, name: str) -> Decimal:
    if type(value) is not str:
        raise AggregateTradeMinuteStorageError(f"stored {name} must be text")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise AggregateTradeMinuteStorageError(
            f"stored {name} is not an exact Decimal"
        ) from error
    if canonical_decimal_text(result) != value:
        raise AggregateTradeMinuteStorageError(
            f"stored {name} is not canonically encoded"
        )
    return result


def _utc_datetime(value: object, name: str) -> datetime | None:
    if value is None:
        return None
    if type(value) is not int:
        raise AggregateTradeMinuteStorageError(
            f"stored {name} must decode to integer microseconds"
        )
    try:
        return _UTC_EPOCH + timedelta(microseconds=value)
    except OverflowError as error:
        raise AggregateTradeMinuteStorageError(
            f"stored {name} is outside the datetime range"
        ) from error


def _dataset_from_table(
    table: pa.Table,
    manifest: AggregateTradeMinuteDatasetManifest,
) -> ValidatedAggregateTradeMinuteDataset:
    if not table.schema.equals(
        AGGREGATE_TRADE_MINUTE_SCHEMA, check_metadata=False
    ):
        raise AggregateTradeMinuteStorageError(
            "decoded minute-state table schema differs"
        )
    table.validate(full=True)
    for field, column in zip(
        AGGREGATE_TRADE_MINUTE_SCHEMA, table.columns, strict=True
    ):
        if not field.nullable and column.null_count:
            raise AggregateTradeMinuteStorageError(
                f"stored non-null minute-state column {field.name} contains nulls"
            )
    columns: dict[str, list[object]] = {}
    for name in AGGREGATE_TRADE_MINUTE_SCHEMA.names:
        if name in _TIMESTAMP_COLUMNS:
            columns[name] = [
                _utc_datetime(value, name)
                for value in table[name].cast(pa.int64(), safe=True).to_pylist()
            ]
        else:
            columns[name] = table[name].to_pylist()
    states: list[AggregateTradeMinuteState] = []
    for index in range(table.num_rows):
        try:
            state = AggregateTradeMinuteState(
                symbol=columns["symbol"][index],
                minute_start_time=columns["minute_start_time"][index],
                minute_end_time_exclusive=columns[
                    "minute_end_time_exclusive"
                ][index],
                source_range_id=columns["source_range_id"][index],
                source_manifest_id=columns["source_manifest_id"][index],
                source_revision_id=columns["source_revision_id"][index],
                source_dataset_id=columns["source_dataset_id"][index],
                source_timestamp_unit=SourceTimestampUnit(
                    columns["source_timestamp_unit"][index]
                ),
                event_count=columns["event_count"][index],
                aggressive_buy_event_count=columns[
                    "aggressive_buy_event_count"
                ][index],
                aggressive_sell_event_count=columns[
                    "aggressive_sell_event_count"
                ][index],
                total_base_quantity=_decimal_from_text(
                    columns["total_base_quantity"][index],
                    "total_base_quantity",
                ),
                total_quote_notional=_decimal_from_text(
                    columns["total_quote_notional"][index],
                    "total_quote_notional",
                ),
                aggressive_buy_base_quantity=_decimal_from_text(
                    columns["aggressive_buy_base_quantity"][index],
                    "aggressive_buy_base_quantity",
                ),
                aggressive_sell_base_quantity=_decimal_from_text(
                    columns["aggressive_sell_base_quantity"][index],
                    "aggressive_sell_base_quantity",
                ),
                aggressive_buy_quote_notional=_decimal_from_text(
                    columns["aggressive_buy_quote_notional"][index],
                    "aggressive_buy_quote_notional",
                ),
                aggressive_sell_quote_notional=_decimal_from_text(
                    columns["aggressive_sell_quote_notional"][index],
                    "aggressive_sell_quote_notional",
                ),
                first_aggregate_trade_id=columns[
                    "first_aggregate_trade_id"
                ][index],
                last_aggregate_trade_id=columns[
                    "last_aggregate_trade_id"
                ][index],
                first_event_time=columns["first_event_time"][index],
                last_event_time=columns["last_event_time"][index],
                completeness_state=AggregateTradeMinuteCompletenessState(
                    columns["completeness_state"][index]
                ),
                availability_state=AggregateTradeMinuteAvailabilityState(
                    columns["availability_state"][index]
                ),
                validation_status=ResearchEventValidationStatus(
                    columns["validation_status"][index]
                ),
            )
        except AggregateTradeMinuteStorageError:
            raise
        except (AttributeError, TypeError, ValueError, OverflowError) as error:
            raise AggregateTradeMinuteStorageError(
                f"stored minute-state row {index} is invalid: {error}"
            ) from error
        states.append(state)
    try:
        dataset = ValidatedAggregateTradeMinuteDataset(
            manifest.identity, tuple(states)
        )
    except (TypeError, ValueError) as error:
        raise AggregateTradeMinuteStorageError(
            f"stored minute-state sequence is invalid: {error}"
        ) from error
    if AggregateTradeMinuteDatasetManifest.from_dataset(dataset) != manifest:
        raise AggregateTradeMinuteStorageError(
            "stored minute-state content differs from manifest"
        )
    return dataset


class ParquetAggregateTradeMinuteStateStore:
    """Publish and verify immutable completed-minute research-state datasets."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def dataset_path(
        self, dataset: ValidatedAggregateTradeMinuteDataset
    ) -> Path:
        ValidatedAggregateTradeMinuteDataset.__post_init__(dataset)
        identity = dataset.identity
        interval = (
            f"{identity.requested_start_time.date().isoformat()}_"
            f"{identity.requested_end_time_exclusive.date().isoformat()}"
        )
        return (
            self._root
            / "market_data"
            / "aggregate_trade_minute_states"
            / "canonical"
            / AGGREGATE_TRADE_MINUTE_STORAGE_SCHEMA_VERSION
            / _safe_segment(identity.symbol, "symbol")
            / interval
            / f"{dataset.dataset_id}.parquet"
        )

    def read(self, path: Path) -> ValidatedAggregateTradeMinuteDataset:
        try:
            with Path(path).open("rb") as source:
                with pq.ParquetFile(
                    source, page_checksum_verification=True
                ) as parquet:
                    if not parquet.schema_arrow.equals(
                        AGGREGATE_TRADE_MINUTE_SCHEMA,
                        check_metadata=False,
                    ):
                        raise AggregateTradeMinuteStorageError(
                            "stored minute-state Arrow schema differs"
                        )
                    metadata = parquet.metadata.metadata or {}
                    manifest = _parse_manifest(metadata)
                    table = parquet.read(
                        use_threads=False, use_pandas_metadata=False
                    )
            dataset = _dataset_from_table(table, manifest)
            if any(
                metadata.get(key) != value
                for key, value in _metadata(manifest).items()
            ):
                raise AggregateTradeMinuteStorageError(
                    "validated minute-state metadata differs from storage"
                )
            return dataset
        except AggregateTradeMinuteStorageError:
            raise
        except (OSError, pa.ArrowException, TypeError, ValueError, OverflowError) as error:
            raise AggregateTradeMinuteStorageError(
                f"cannot read aggregate-trade minute-state dataset {path}: {error}"
            ) from error

    def write(
        self, dataset: ValidatedAggregateTradeMinuteDataset
    ) -> AggregateTradeMinutePublication:
        if type(dataset) is not ValidatedAggregateTradeMinuteDataset:
            raise AggregateTradeMinuteStorageError(
                "write requires a ValidatedAggregateTradeMinuteDataset"
            )
        dataset = ValidatedAggregateTradeMinuteDataset(
            dataset.identity, dataset.states
        )
        destination = self.dataset_path(dataset)
        manifest = AggregateTradeMinuteDatasetManifest.from_dataset(dataset)
        if destination.exists():
            if self.read(destination) != dataset:
                raise AggregateTradeMinuteDatasetCollisionError(
                    "minute-state identity already contains different content"
                )
            return AggregateTradeMinutePublication(
                destination, dataset.dataset_id, manifest
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            table = _table(dataset)
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix=f".{destination.stem}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                pq.write_table(
                    table,
                    temporary,
                    version="2.6",
                    compression="zstd",
                    use_dictionary=True,
                    write_statistics=True,
                    write_page_checksum=True,
                    store_schema=True,
                    use_deprecated_int96_timestamps=False,
                    coerce_timestamps=None,
                    allow_truncated_timestamps=False,
                    data_page_version="2.0",
                )
                temporary.flush()
                os.fsync(temporary.fileno())
            if self.read(temporary_path) != dataset:
                raise AggregateTradeMinuteStorageError(
                    "temporary minute-state dataset differs from input"
                )
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                if self.read(destination) != dataset:
                    raise AggregateTradeMinuteDatasetCollisionError(
                        "concurrent minute-state publication differs"
                    )
            return AggregateTradeMinutePublication(
                destination, dataset.dataset_id, manifest
            )
        except AggregateTradeMinuteStorageError:
            raise
        except (OSError, pa.ArrowException, TypeError, ValueError, OverflowError) as error:
            raise AggregateTradeMinuteStorageError(
                f"cannot publish aggregate-trade minute-state dataset: {error}"
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    active_error = sys.exception()
                    if active_error is not None:
                        active_error.add_note(
                            "minute-state temporary cleanup also failed: "
                            f"{cleanup_error}"
                        )
                    else:
                        raise AggregateTradeMinuteStorageError(
                            "cannot remove minute-state temporary file: "
                            f"{cleanup_error}"
                        ) from cleanup_error

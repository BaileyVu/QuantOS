"""Exact immutable Parquet publication for validated aggregate-trade archives."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

from quantos.domain.market_data.research_events import (
    AggregateTrade,
    AggregateTradeArchiveManifest,
    AggregateTradeDatasetIdentity,
    SourceTimestampUnit,
    ValidatedAggregateTradeArchive,
    aggregate_trade_archive_manifest_bytes,
    aggregate_trade_archive_manifest_from_bytes,
    aggregate_trade_archive_manifest_id,
    canonical_aggregate_trade_sequence_sha256,
    validate_aggregate_trade_sequence,
)

AGGREGATE_TRADE_STORAGE_SCHEMA_VERSION = "aggregate-trade-parquet-v1"
_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SAFE_PATH_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_DECIMAL_FIELDS = ("price", "quantity")
AGGREGATE_TRADE_SCHEMA = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("aggregate_trade_id", pa.int64(), nullable=False),
        pa.field("price", pa.decimal128(38, 18), nullable=False),
        pa.field("price_text", pa.string(), nullable=False),
        pa.field("quantity", pa.decimal128(38, 18), nullable=False),
        pa.field("quantity_text", pa.string(), nullable=False),
        pa.field("first_trade_id", pa.int64(), nullable=False),
        pa.field("last_trade_id", pa.int64(), nullable=False),
        pa.field("event_time", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("source_timestamp", pa.int64(), nullable=False),
        pa.field("source_timestamp_unit", pa.string(), nullable=False),
        pa.field("buyer_is_maker", pa.bool_(), nullable=False),
        pa.field("best_price_match", pa.bool_(), nullable=True),
    ]
)


class AggregateTradeParquetStorageError(ValueError):
    """An event archive cannot be safely represented, read, or published."""


class AggregateTradeDatasetCollisionError(AggregateTradeParquetStorageError):
    """An immutable archive identity already contains different content."""


@dataclass(frozen=True, slots=True)
class AggregateTradeArchivePublication:
    raw_archive_path: Path
    canonical_parquet_path: Path
    manifest_id: str


def _safe_segment(value: str, name: str) -> str:
    if type(value) is not str or _SAFE_PATH_SEGMENT.fullmatch(value) is None:
        raise AggregateTradeParquetStorageError(
            f"{name} is not a safe canonical path segment"
        )
    if value in (".", ".."):
        raise AggregateTradeParquetStorageError(f"{name} is not a safe path segment")
    return value


def _metadata(manifest: AggregateTradeArchiveManifest) -> dict[bytes, bytes]:
    manifest_bytes = aggregate_trade_archive_manifest_bytes(manifest)
    return {
        b"quantos.aggregate_trade_storage_schema_version": (
            AGGREGATE_TRADE_STORAGE_SCHEMA_VERSION.encode("ascii")
        ),
        b"quantos.aggregate_trade_archive_manifest": manifest_bytes,
        b"quantos.aggregate_trade_archive_manifest_id": (
            aggregate_trade_archive_manifest_id(manifest).encode("ascii")
        ),
    }


def _parse_manifest(metadata: dict[bytes, bytes] | None) -> AggregateTradeArchiveManifest:
    metadata = metadata or {}
    try:
        version = metadata[
            b"quantos.aggregate_trade_storage_schema_version"
        ].decode("ascii")
        manifest_bytes = metadata[b"quantos.aggregate_trade_archive_manifest"]
        recorded_id = metadata[
            b"quantos.aggregate_trade_archive_manifest_id"
        ].decode("ascii")
    except (KeyError, UnicodeError) as error:
        raise AggregateTradeParquetStorageError(
            "missing or invalid aggregate-trade metadata"
        ) from error
    if version != AGGREGATE_TRADE_STORAGE_SCHEMA_VERSION:
        raise AggregateTradeParquetStorageError(
            "unsupported aggregate-trade storage schema version"
        )
    try:
        manifest = aggregate_trade_archive_manifest_from_bytes(manifest_bytes)
    except (TypeError, ValueError) as error:
        raise AggregateTradeParquetStorageError(
            f"invalid aggregate-trade archive manifest: {error}"
        ) from error
    if recorded_id != aggregate_trade_archive_manifest_id(manifest):
        raise AggregateTradeParquetStorageError(
            "archive manifest ID does not match manifest bytes"
        )
    return manifest


def _require_schema(parquet: pq.ParquetFile) -> None:
    if not parquet.schema_arrow.equals(AGGREGATE_TRADE_SCHEMA, check_metadata=False):
        raise AggregateTradeParquetStorageError(
            "stored Arrow schema does not match aggregate-trade schema"
        )
    if len(parquet.schema) != len(AGGREGATE_TRADE_SCHEMA):
        raise AggregateTradeParquetStorageError("physical Parquet column count differs")
    string_fields = {"symbol", "price_text", "quantity_text", "source_timestamp_unit"}
    integer_fields = {
        "aggregate_trade_id",
        "first_trade_id",
        "last_trade_id",
        "source_timestamp",
    }
    for index, field in enumerate(AGGREGATE_TRADE_SCHEMA):
        column = parquet.schema.column(index)
        if (
            column.name != field.name
            or column.path != field.name
            or column.max_repetition_level != 0
            or column.max_definition_level != (1 if field.nullable else 0)
        ):
            raise AggregateTradeParquetStorageError(
                "physical Parquet columns must be flat, ordered, and correctly nullable"
            )
        logical = json.loads(column.logical_type.to_json())
        if field.name in string_fields:
            valid = column.physical_type == "BYTE_ARRAY" and logical.get("Type") == "String"
        elif field.name in integer_fields:
            valid = column.physical_type == "INT64" and logical.get("Type") == "None"
        elif field.name in _DECIMAL_FIELDS:
            valid = (
                column.physical_type == "FIXED_LEN_BYTE_ARRAY"
                and column.length == 16
                and logical.get("Type") == "Decimal"
                and column.precision == 38
                and column.scale == 18
            )
        elif field.name == "event_time":
            valid = (
                column.physical_type == "INT64"
                and logical.get("Type") == "Timestamp"
                and logical.get("timeUnit") == "microseconds"
                and logical.get("isAdjustedToUTC") is True
            )
        else:
            valid = column.physical_type == "BOOLEAN" and logical.get("Type") == "None"
        if not valid:
            raise AggregateTradeParquetStorageError(
                f"physical Parquet schema mismatch for {field.name}"
            )


def _table(archive: ValidatedAggregateTradeArchive) -> pa.Table:
    archive = ValidatedAggregateTradeArchive(archive.sequence, archive.manifest)
    values: dict[str, list[object]] = {name: [] for name in AGGREGATE_TRADE_SCHEMA.names}
    for event in archive.sequence.events:
        values["symbol"].append(event.symbol)
        values["aggregate_trade_id"].append(event.aggregate_trade_id)
        values["price"].append(event.price)
        values["price_text"].append(str(event.price))
        values["quantity"].append(event.quantity)
        values["quantity_text"].append(str(event.quantity))
        values["first_trade_id"].append(event.first_trade_id)
        values["last_trade_id"].append(event.last_trade_id)
        values["event_time"].append(event.event_time)
        values["source_timestamp"].append(event.source_timestamp)
        values["source_timestamp_unit"].append(event.source_timestamp_unit.value)
        values["buyer_is_maker"].append(event.buyer_is_maker)
        values["best_price_match"].append(event.best_price_match)
    try:
        arrays = [
            pa.array(values[field.name], type=field.type, from_pandas=False, safe=True)
            for field in AGGREGATE_TRADE_SCHEMA
        ]
        table = pa.Table.from_arrays(
            arrays,
            schema=AGGREGATE_TRADE_SCHEMA.with_metadata(_metadata(archive.manifest)),
        )
        table.validate(full=True)
        return table
    except (pa.ArrowException, ValueError, TypeError, OverflowError) as error:
        raise AggregateTradeParquetStorageError(
            f"aggregate trades cannot be represented exactly: {error}"
        ) from error


def _utc_datetime(value: object) -> datetime:
    if type(value) is not int:
        raise AggregateTradeParquetStorageError(
            "stored event_time must decode to integer microseconds"
        )
    try:
        return _UTC_EPOCH + timedelta(microseconds=value)
    except OverflowError as error:
        raise AggregateTradeParquetStorageError(
            "stored event_time is outside the datetime range"
        ) from error


def _decimal_from_columns(
    decimal_value: object, text_value: object, field_name: str
) -> Decimal:
    if type(decimal_value) is not Decimal or type(text_value) is not str:
        raise AggregateTradeParquetStorageError(
            f"stored {field_name} representation has an invalid type"
        )
    try:
        exact = Decimal(text_value)
    except (InvalidOperation, ValueError) as error:
        raise AggregateTradeParquetStorageError(
            f"stored {field_name} text is not an exact Decimal"
        ) from error
    if not exact.is_finite() or exact != decimal_value:
        raise AggregateTradeParquetStorageError(
            f"stored {field_name} Decimal and text representations differ"
        )
    return exact


def _archive_from_table(
    table: pa.Table, manifest: AggregateTradeArchiveManifest
) -> ValidatedAggregateTradeArchive:
    if not table.schema.equals(AGGREGATE_TRADE_SCHEMA, check_metadata=False):
        raise AggregateTradeParquetStorageError("decoded table schema does not match")
    table.validate(full=True)
    for field, column in zip(AGGREGATE_TRADE_SCHEMA, table.columns, strict=True):
        if not field.nullable and column.null_count:
            raise AggregateTradeParquetStorageError(
                f"canonical aggregate-trade column {field.name} contains nulls"
            )
    columns: dict[str, list[object]] = {
        field.name: (
            [
                _utc_datetime(value)
                for value in table[field.name].cast(pa.int64(), safe=True).to_pylist()
            ]
            if field.name == "event_time"
            else table[field.name].to_pylist()
        )
        for field in AGGREGATE_TRADE_SCHEMA
    }
    events: list[AggregateTrade] = []
    for index in range(table.num_rows):
        try:
            unit_value = columns["source_timestamp_unit"][index]
            if type(unit_value) is not str:
                raise TypeError("source timestamp unit must be a string")
            event = AggregateTrade(
                symbol=columns["symbol"][index],  # type: ignore[arg-type]
                aggregate_trade_id=columns["aggregate_trade_id"][index],  # type: ignore[arg-type]
                price=_decimal_from_columns(
                    columns["price"][index], columns["price_text"][index], "price"
                ),
                quantity=_decimal_from_columns(
                    columns["quantity"][index],
                    columns["quantity_text"][index],
                    "quantity",
                ),
                first_trade_id=columns["first_trade_id"][index],  # type: ignore[arg-type]
                last_trade_id=columns["last_trade_id"][index],  # type: ignore[arg-type]
                event_time=columns["event_time"][index],  # type: ignore[arg-type]
                source_timestamp=columns["source_timestamp"][index],  # type: ignore[arg-type]
                source_timestamp_unit=SourceTimestampUnit(unit_value),
                buyer_is_maker=columns["buyer_is_maker"][index],  # type: ignore[arg-type]
                best_price_match=columns["best_price_match"][index],  # type: ignore[arg-type]
            )
        except AggregateTradeParquetStorageError:
            raise
        except (TypeError, ValueError, OverflowError) as error:
            raise AggregateTradeParquetStorageError(
                f"stored aggregate-trade row {index} is invalid: {error}"
            ) from error
        events.append(event)
    identity = AggregateTradeDatasetIdentity(
        symbol=manifest.symbol,
        requested_start_time=manifest.requested_start_time,
        requested_end_time_exclusive=manifest.requested_end_time_exclusive,
        source_timestamp_unit=manifest.source_timestamp_unit,
        schema_version=manifest.schema_version,
        normalizer_version=manifest.normalizer_version,
        provenance=manifest.provenance,
        research_role=manifest.research_role,
    )
    try:
        sequence = validate_aggregate_trade_sequence(identity, events)
        archive = ValidatedAggregateTradeArchive(sequence, manifest)
    except (TypeError, ValueError, OverflowError) as error:
        raise AggregateTradeParquetStorageError(
            f"stored aggregate-trade sequence failed canonical validation: {error}"
        ) from error
    if manifest.canonical_sequence_sha256 != canonical_aggregate_trade_sequence_sha256(
        archive.sequence
    ):
        raise AggregateTradeParquetStorageError(
            "stored canonical sequence hash does not match decoded events"
        )
    return archive


def _events_from_record_batch(
    batch: pa.RecordBatch, *, row_offset: int
) -> tuple[AggregateTrade, ...]:
    if not batch.schema.equals(AGGREGATE_TRADE_SCHEMA, check_metadata=False):
        raise AggregateTradeParquetStorageError(
            "decoded record-batch schema does not match"
        )
    batch.validate(full=True)
    for field, column in zip(AGGREGATE_TRADE_SCHEMA, batch.columns, strict=True):
        if not field.nullable and column.null_count:
            raise AggregateTradeParquetStorageError(
                f"canonical aggregate-trade column {field.name} contains nulls"
            )
    columns: dict[str, list[object]] = {
        field.name: (
            [
                _utc_datetime(value)
                for value in batch.column(index).cast(
                    pa.int64(), safe=True
                ).to_pylist()
            ]
            if field.name == "event_time"
            else batch.column(index).to_pylist()
        )
        for index, field in enumerate(AGGREGATE_TRADE_SCHEMA)
    }
    events: list[AggregateTrade] = []
    for index in range(batch.num_rows):
        try:
            unit_value = columns["source_timestamp_unit"][index]
            if type(unit_value) is not str:
                raise TypeError("source timestamp unit must be a string")
            events.append(
                AggregateTrade(
                    symbol=columns["symbol"][index],  # type: ignore[arg-type]
                    aggregate_trade_id=columns["aggregate_trade_id"][index],  # type: ignore[arg-type]
                    price=_decimal_from_columns(
                        columns["price"][index],
                        columns["price_text"][index],
                        "price",
                    ),
                    quantity=_decimal_from_columns(
                        columns["quantity"][index],
                        columns["quantity_text"][index],
                        "quantity",
                    ),
                    first_trade_id=columns["first_trade_id"][index],  # type: ignore[arg-type]
                    last_trade_id=columns["last_trade_id"][index],  # type: ignore[arg-type]
                    event_time=columns["event_time"][index],  # type: ignore[arg-type]
                    source_timestamp=columns["source_timestamp"][index],  # type: ignore[arg-type]
                    source_timestamp_unit=SourceTimestampUnit(unit_value),
                    buyer_is_maker=columns["buyer_is_maker"][index],  # type: ignore[arg-type]
                    best_price_match=columns["best_price_match"][index],  # type: ignore[arg-type]
                )
            )
        except AggregateTradeParquetStorageError:
            raise
        except (TypeError, ValueError, OverflowError) as error:
            raise AggregateTradeParquetStorageError(
                f"stored aggregate-trade row {row_offset + index} is invalid: "
                f"{error}"
            ) from error
    return tuple(events)


class ParquetAggregateTradeArchiveStore:
    """Publish raw and canonical event archives without replacing existing files."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def raw_archive_path(self, manifest: AggregateTradeArchiveManifest) -> Path:
        aggregate_trade_archive_manifest_bytes(manifest)
        return (
            self._root
            / "market_data"
            / "aggregate_trades"
            / "raw"
            / _safe_segment(manifest.provider, "provider")
            / _safe_segment(manifest.market, "market")
            / _safe_segment(manifest.symbol, "symbol")
            / manifest.source_date.isoformat()
            / f"{manifest.source_revision_id}.zip"
        )

    def dataset_path(self, manifest: AggregateTradeArchiveManifest) -> Path:
        aggregate_trade_archive_manifest_bytes(manifest)
        return (
            self._root
            / "market_data"
            / "aggregate_trades"
            / "canonical"
            / AGGREGATE_TRADE_STORAGE_SCHEMA_VERSION
            / _safe_segment(manifest.symbol, "symbol")
            / manifest.source_date.isoformat()
            / f"{aggregate_trade_archive_manifest_id(manifest)}.parquet"
        )

    def read_raw(self, manifest: AggregateTradeArchiveManifest) -> bytes:
        path = self.raw_archive_path(manifest)
        try:
            content = path.read_bytes()
        except OSError as error:
            raise AggregateTradeParquetStorageError(
                f"cannot read immutable raw archive {path}: {error}"
            ) from error
        if sha256(content).hexdigest() != manifest.raw_zip_sha256:
            raise AggregateTradeParquetStorageError(
                "immutable raw archive SHA-256 does not match manifest"
            )
        return content

    def verify_raw(
        self,
        manifest: AggregateTradeArchiveManifest,
        *,
        chunk_size: int = 1_048_576,
    ) -> Path:
        """Verify immutable raw bytes without retaining the ZIP in memory."""

        if type(chunk_size) is not int or not 1 <= chunk_size <= 16_777_216:
            raise AggregateTradeParquetStorageError(
                "raw verification chunk_size must be between 1 and 16777216"
            )
        path = self.raw_archive_path(manifest)
        digest = sha256()
        try:
            with path.open("rb") as source:
                while True:
                    chunk = source.read(chunk_size)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError as error:
            raise AggregateTradeParquetStorageError(
                f"cannot read immutable raw archive {path}: {error}"
            ) from error
        if digest.hexdigest() != manifest.raw_zip_sha256:
            raise AggregateTradeParquetStorageError(
                "immutable raw archive SHA-256 does not match manifest"
            )
        return path

    def read_manifest(self, path: Path) -> AggregateTradeArchiveManifest:
        """Read and validate only trusted Parquet schema/footer metadata."""

        try:
            with Path(path).open("rb") as source:
                with pq.ParquetFile(
                    source, page_checksum_verification=True
                ) as parquet:
                    _require_schema(parquet)
                    manifest = _parse_manifest(
                        parquet.metadata.metadata or {}
                    )
                    if parquet.metadata.num_rows != manifest.accepted_row_count:
                        raise AggregateTradeParquetStorageError(
                            "Parquet row count differs from archive manifest"
                        )
                    return manifest
        except AggregateTradeParquetStorageError:
            raise
        except (OSError, pa.ArrowException, TypeError, ValueError) as error:
            raise AggregateTradeParquetStorageError(
                f"cannot read aggregate-trade Parquet manifest {path}: {error}"
            ) from error

    def iter_event_batches(
        self,
        path: Path,
        *,
        expected_manifest: AggregateTradeArchiveManifest,
        batch_size: int,
    ) -> Iterator[tuple[AggregateTrade, ...]]:
        """Decode one immutable partition in deterministic bounded batches."""

        if type(expected_manifest) is not AggregateTradeArchiveManifest:
            raise AggregateTradeParquetStorageError(
                "expected_manifest must be authoritative"
            )
        AggregateTradeArchiveManifest.__post_init__(expected_manifest)
        if type(batch_size) is not int or not 1 <= batch_size <= 1_000_000:
            raise AggregateTradeParquetStorageError(
                "batch_size must be between 1 and 1000000"
            )
        row_offset = 0
        try:
            with Path(path).open("rb") as source:
                with pq.ParquetFile(
                    source, page_checksum_verification=True
                ) as parquet:
                    _require_schema(parquet)
                    stored_metadata = parquet.metadata.metadata or {}
                    manifest = _parse_manifest(stored_metadata)
                    if manifest != expected_manifest:
                        raise AggregateTradeParquetStorageError(
                            "stored archive manifest differs from expected manifest"
                        )
                    if parquet.metadata.num_rows != manifest.accepted_row_count:
                        raise AggregateTradeParquetStorageError(
                            "Parquet row count differs from archive manifest"
                        )
                    if any(
                        stored_metadata.get(key) != value
                        for key, value in _metadata(manifest).items()
                    ):
                        raise AggregateTradeParquetStorageError(
                            "archive metadata differs from canonical manifest"
                        )
                    for batch in parquet.iter_batches(
                        batch_size=batch_size,
                        use_threads=False,
                        use_pandas_metadata=False,
                    ):
                        events = _events_from_record_batch(
                            batch, row_offset=row_offset
                        )
                        row_offset += len(events)
                        yield events
            if row_offset != expected_manifest.accepted_row_count:
                raise AggregateTradeParquetStorageError(
                    "decoded event count differs from archive manifest"
                )
        except AggregateTradeParquetStorageError:
            raise
        except (OSError, pa.ArrowException, TypeError, ValueError, OverflowError) as error:
            raise AggregateTradeParquetStorageError(
                f"cannot stream canonical aggregate-trade Parquet dataset "
                f"{path}: {error}"
            ) from error

    def read(self, path: Path) -> ValidatedAggregateTradeArchive:
        try:
            with Path(path).open("rb") as source:
                with pq.ParquetFile(source, page_checksum_verification=True) as parquet:
                    _require_schema(parquet)
                    stored_metadata = parquet.metadata.metadata or {}
                    manifest = _parse_manifest(stored_metadata)
                    table = parquet.read(use_threads=False, use_pandas_metadata=False)
            archive = _archive_from_table(table, manifest)
            expected_metadata = _metadata(archive.manifest)
            if any(
                stored_metadata.get(key) != value
                for key, value in expected_metadata.items()
            ):
                raise AggregateTradeParquetStorageError(
                    "validated archive metadata differs from stored metadata"
                )
            return archive
        except AggregateTradeParquetStorageError:
            raise
        except (OSError, pa.ArrowException, TypeError, ValueError, OverflowError) as error:
            raise AggregateTradeParquetStorageError(
                f"cannot read canonical aggregate-trade Parquet dataset {path}: {error}"
            ) from error

    def _existing_parquet(
        self, path: Path, archive: ValidatedAggregateTradeArchive
    ) -> Path:
        existing = self.read(path)
        if existing != archive:
            raise AggregateTradeDatasetCollisionError(
                f"archive manifest identity already contains different events: {path}"
            )
        return path

    def _publish_raw(
        self, destination: Path, content: bytes, expected_sha256: str
    ) -> Path:
        if sha256(content).hexdigest() != expected_sha256:
            raise AggregateTradeParquetStorageError(
                "raw ZIP bytes do not match the manifest SHA-256"
            )
        if destination.exists():
            existing = destination.read_bytes()
            if existing != content:
                raise AggregateTradeDatasetCollisionError(
                    f"raw source revision path contains different bytes: {destination}"
                )
            return destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix=f".{destination.stem}.",
                suffix=".tmp",
                dir=destination.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                existing = destination.read_bytes()
                if existing != content:
                    raise AggregateTradeDatasetCollisionError(
                        f"concurrent raw source revision differs: {destination}"
                    )
            return destination
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    active_error = sys.exception()
                    if active_error is not None:
                        active_error.add_note(
                            f"raw temporary cleanup also failed: {cleanup_error}"
                        )
                    else:
                        raise AggregateTradeParquetStorageError(
                            f"cannot remove raw temporary file: {cleanup_error}"
                        ) from cleanup_error

    def write(
        self,
        archive: ValidatedAggregateTradeArchive,
        *,
        raw_zip_bytes: bytes,
    ) -> AggregateTradeArchivePublication:
        if type(archive) is not ValidatedAggregateTradeArchive:
            raise AggregateTradeParquetStorageError(
                "write requires a ValidatedAggregateTradeArchive"
            )
        if type(raw_zip_bytes) is not bytes:
            raise AggregateTradeParquetStorageError("raw_zip_bytes must be exact bytes")
        archive = ValidatedAggregateTradeArchive(archive.sequence, archive.manifest)
        raw_destination = self.raw_archive_path(archive.manifest)
        parquet_destination = self.dataset_path(archive.manifest)
        raw_path = self._publish_raw(
            raw_destination, raw_zip_bytes, archive.manifest.raw_zip_sha256
        )
        table = _table(archive)
        if parquet_destination.exists():
            canonical_path = self._existing_parquet(parquet_destination, archive)
            return AggregateTradeArchivePublication(
                raw_path,
                canonical_path,
                aggregate_trade_archive_manifest_id(archive.manifest),
            )
        parquet_destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix=f".{parquet_destination.stem}.",
                suffix=".tmp",
                dir=parquet_destination.parent,
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
                    store_decimal_as_integer=False,
                )
                temporary.flush()
                os.fsync(temporary.fileno())
            if self.read(temporary_path) != archive:
                raise AggregateTradeParquetStorageError(
                    "temporary aggregate-trade dataset differs from input"
                )
            try:
                os.link(temporary_path, parquet_destination)
            except FileExistsError:
                self._existing_parquet(parquet_destination, archive)
            return AggregateTradeArchivePublication(
                raw_path,
                parquet_destination,
                aggregate_trade_archive_manifest_id(archive.manifest),
            )
        except AggregateTradeParquetStorageError:
            raise
        except (OSError, pa.ArrowException, TypeError, ValueError, OverflowError) as error:
            raise AggregateTradeParquetStorageError(
                f"cannot publish aggregate-trade archive: {error}"
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    active_error = sys.exception()
                    if active_error is not None:
                        active_error.add_note(
                            f"Parquet temporary cleanup also failed: {cleanup_error}"
                        )
                    else:
                        raise AggregateTradeParquetStorageError(
                            f"cannot remove Parquet temporary file: {cleanup_error}"
                        ) from cleanup_error

"""Exact, immutable Parquet persistence for validated canonical candle sequences."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

from quantos.domain.common import require_utc
from quantos.domain.market_data import (
    Candle,
    DatasetIdentity,
    DatasetValidationStatus,
    ValidatedCandleSequence,
    validate_candle_sequence,
)

STORAGE_SCHEMA_VERSION = "parquet-v1"
_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_IDENTITY_FIELDS = (
    "symbol", "timeframe", "start_time", "end_time", "source",
    "schema_version", "ingestion_version", "validation_status",
)
_DECIMAL_FIELDS = ("open", "high", "low", "close", "volume", "quote_volume")
CANDLE_SCHEMA = pa.schema([
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("interval", pa.string(), nullable=False),
    pa.field("open_time", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("close_time", pa.timestamp("us", tz="UTC"), nullable=False),
    *(pa.field(name, pa.decimal128(38, 18), nullable=False) for name in _DECIMAL_FIELDS),
    pa.field("trade_count", pa.int64(), nullable=False),
])


class ParquetStorageError(ValueError):
    """A dataset cannot be safely represented, read, or published."""


class DatasetCollisionError(ParquetStorageError):
    """An immutable identity path already contains different canonical data."""


def _utc_text(value: datetime) -> str:
    # Extended datetime types can carry sub-microsecond data that Arrow's
    # Python-datetime conversion would silently discard. Accept the exact
    # canonical Python representation rather than downcasting such values.
    if type(value) is not datetime:
        raise ParquetStorageError("storage timestamps must be built-in datetime values")
    require_utc(value, "storage timestamp")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _identity_fields(identity: DatasetIdentity) -> dict[str, str]:
    if not isinstance(identity, DatasetIdentity):
        raise ParquetStorageError("storage requires a DatasetIdentity")
    if not isinstance(identity.validation_status, DatasetValidationStatus):
        raise ParquetStorageError("invalid dataset validation status")
    try:
        # Recheck the contract instead of trusting a persisted validation flag.
        replace(identity)
        fields = {
            name: getattr(identity, name)
            for name in _IDENTITY_FIELDS
            if name not in ("start_time", "end_time", "validation_status")
        }
        if any(type(value) is not str for value in fields.values()):
            raise ParquetStorageError("identity metadata must contain built-in strings")
        fields["start_time"] = _utc_text(identity.start_time)
        fields["end_time"] = _utc_text(identity.end_time)
        fields["validation_status"] = identity.validation_status.value
        return fields
    except (TypeError, ValueError) as error:
        raise ParquetStorageError(f"invalid dataset identity: {error}") from error


def _digest(fields: dict[str, str]) -> str:
    # JSON escaping, fixed separators, and sorted keys avoid delimiter ambiguity
    # and locale/platform-dependent output. All eight identity fields participate.
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(canonical.encode("utf-8")).hexdigest()


def dataset_id(identity: DatasetIdentity) -> str:
    """SHA-256 of all eight identity fields in canonical UTF-8 JSON."""
    return _digest(_identity_fields(identity))


def _metadata(identity: DatasetIdentity) -> dict[bytes, bytes]:
    fields = _identity_fields(identity)
    result = {
        f"quantos.{name}".encode("ascii"): value.encode("utf-8")
        for name, value in fields.items()
    }
    result[b"quantos.storage_schema_version"] = STORAGE_SCHEMA_VERSION.encode("ascii")
    result[b"quantos.dataset_id"] = _digest(fields).encode("ascii")
    return result


def _parse_metadata(metadata: dict[bytes, bytes] | None) -> DatasetIdentity:
    metadata = metadata or {}
    try:
        version = metadata[b"quantos.storage_schema_version"].decode("utf-8")
        recorded_id = metadata[b"quantos.dataset_id"].decode("ascii")
        fields = {
            name: metadata[f"quantos.{name}".encode("ascii")].decode("utf-8")
            for name in _IDENTITY_FIELDS
        }
    except (KeyError, UnicodeError) as error:
        raise ParquetStorageError("missing or invalid required QuantOS metadata") from error
    if version != STORAGE_SCHEMA_VERSION:
        raise ParquetStorageError("unsupported storage schema version")
    if fields["validation_status"] != DatasetValidationStatus.VALIDATED.value:
        raise ParquetStorageError("persisted dataset identity must claim validated status")
    try:
        identity = DatasetIdentity(
            symbol=fields["symbol"],
            timeframe=fields["timeframe"],
            start_time=datetime.fromisoformat(fields["start_time"]),
            end_time=datetime.fromisoformat(fields["end_time"]),
            source=fields["source"],
            schema_version=fields["schema_version"],
            ingestion_version=fields["ingestion_version"],
        )
        # Strict canonical text comparison also rejects parsing that discarded
        # extra fractional timestamp digits or accepted alternate timezone text.
        canonical = _identity_fields(identity)
        canonical["validation_status"] = DatasetValidationStatus.VALIDATED.value
        if fields != canonical:
            raise ParquetStorageError("identity metadata is not canonically encoded")
        if recorded_id != _digest(canonical):
            raise ParquetStorageError("dataset id does not match identity metadata")
    except (ValueError, TypeError) as error:
        raise ParquetStorageError(f"invalid dataset metadata: {error}") from error
    return identity  # Still UNVALIDATED; only Domain validation can promote it.


def _require_schema(parquet: pq.ParquetFile) -> None:
    if not parquet.schema_arrow.equals(CANDLE_SCHEMA, check_metadata=False):
        raise ParquetStorageError("stored Arrow schema does not match the canonical schema")
    # Inspect actual Parquet leaf types too: an embedded ARROW:schema must
    # never hide a different on-disk timestamp unit, nullability, or encoding.
    if len(parquet.schema) != len(CANDLE_SCHEMA):
        raise ParquetStorageError("physical Parquet column count does not match")
    for index, field in enumerate(CANDLE_SCHEMA):
        column = parquet.schema.column(index)
        if (
            column.name != field.name or column.path != field.name
            or column.max_definition_level != 0 or column.max_repetition_level != 0
        ):
            raise ParquetStorageError("physical Parquet columns must be flat, ordered, and non-null")
        logical = json.loads(column.logical_type.to_json())
        if field.name in ("symbol", "interval"):
            valid = column.physical_type == "BYTE_ARRAY" and logical.get("Type") == "String"
        elif field.name in ("open_time", "close_time"):
            valid = (
                column.physical_type == "INT64" and logical.get("Type") == "Timestamp"
                and logical.get("timeUnit") == "microseconds"
                and logical.get("isAdjustedToUTC") is True
            )
        elif field.name in _DECIMAL_FIELDS:
            valid = (
                column.physical_type == "FIXED_LEN_BYTE_ARRAY" and column.length == 16
                and logical.get("Type") == "Decimal"
                and column.precision == 38 and column.scale == 18
            )
        else:
            valid = column.physical_type == "INT64" and logical.get("Type") == "None"
        if not valid:
            raise ParquetStorageError(f"physical Parquet schema mismatch for {field.name}")


def _table(sequence: ValidatedCandleSequence) -> pa.Table:
    for candle in sequence.candles:
        _utc_text(candle.open_time)
        _utc_text(candle.close_time)
    arrays = [
        pa.array(
            [getattr(candle, field.name) for candle in sequence.candles],
            type=field.type, from_pandas=False, safe=True,
        )
        for field in CANDLE_SCHEMA
    ]
    table = pa.Table.from_arrays(arrays, schema=CANDLE_SCHEMA.with_metadata(_metadata(sequence.identity)))
    table.validate(full=True)
    return table


class ParquetCandleDatasetStore:
    """Publish validated sequences under a caller-supplied local root Path.

    Canonical files are never overwritten. Publication requires atomic hard-link
    creation on the local filesystem; unsupported publication fails closed.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def dataset_path(self, identity: DatasetIdentity) -> Path:
        """Calculate a path without creating directories or files."""
        fields = _identity_fields(identity)
        if identity.validation_status is not DatasetValidationStatus.VALIDATED:
            raise ParquetStorageError("canonical paths require a validated identity")
        return (
            self._root / "market_data" / "candles" / STORAGE_SCHEMA_VERSION
            / fields["symbol"] / fields["timeframe"] / f"{_digest(fields)}.parquet"
        )

    def read(self, path: Path) -> ValidatedCandleSequence:
        """Verify one file's checksums, schema, metadata, and canonical sequence."""
        try:
            # A Python file handle keeps reads local, with no URI inference,
            # dataset discovery, schema coercion, or partition inference.
            with Path(path).open("rb") as source:
                with pq.ParquetFile(source, page_checksum_verification=True) as parquet:
                    _require_schema(parquet)
                    stored_metadata = parquet.metadata.metadata or {}
                    identity = _parse_metadata(stored_metadata)
                    table = parquet.read(use_threads=False, use_pandas_metadata=False)
            if not table.schema.equals(CANDLE_SCHEMA, check_metadata=False):
                raise ParquetStorageError("decoded table schema does not match")
            table.validate(full=True)
            if any(column.null_count for column in table.columns):
                raise ParquetStorageError("canonical candle columns must not contain nulls")
            # These columns have already passed the exact timestamp[us, UTC]
            # schema check. Integer decoding preserves every microsecond without
            # relying on an optional system timezone database (absent on Windows).
            columns = [
                [
                    _UTC_EPOCH + timedelta(microseconds=value)
                    for value in table[field.name].cast(pa.int64(), safe=True).to_pylist()
                ]
                if field.name in ("open_time", "close_time")
                else table[field.name].to_pylist()
                for field in CANDLE_SCHEMA
            ]
            candles = tuple(
                Candle(**dict(zip(CANDLE_SCHEMA.names, row, strict=True)))
                for row in zip(*columns, strict=True)
            )
            result = validate_candle_sequence(identity, candles)
            expected_metadata = _metadata(result.identity)
            if any(stored_metadata.get(key) != value for key, value in expected_metadata.items()):
                raise ParquetStorageError("validated identity does not match persisted identity")
            return result
        except ParquetStorageError:
            raise
        except (OSError, pa.ArrowException, ValueError, TypeError, OverflowError) as error:
            raise ParquetStorageError(f"cannot read canonical Parquet dataset {path}: {error}") from error

    def _existing(self, path: Path, sequence: ValidatedCandleSequence) -> Path:
        existing = self.read(path)
        if existing != sequence:
            raise DatasetCollisionError(f"dataset identity already contains different candles: {path}")
        return path

    def write(self, sequence: ValidatedCandleSequence) -> Path:
        """Write, read back, verify equality, then publish without replacing a file."""
        if not isinstance(sequence, ValidatedCandleSequence):
            raise ParquetStorageError("write requires a ValidatedCandleSequence")
        temporary_path: Path | None = None
        try:
            # Re-run the shared invariant checks, including identity boundaries.
            sequence = ValidatedCandleSequence(sequence.identity, sequence.candles)
            destination = self.dataset_path(sequence.identity)
            table = _table(sequence)
            if destination.exists():
                return self._existing(destination, sequence)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w+b", prefix=f".{destination.stem}.", suffix=".tmp",
                dir=destination.parent, delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                pq.write_table(
                    table, temporary,
                    version="2.6", compression="zstd", use_dictionary=True,
                    write_statistics=True, write_page_checksum=True, store_schema=True,
                    use_deprecated_int96_timestamps=False, coerce_timestamps=None,
                    allow_truncated_timestamps=False, data_page_version="2.0",
                    store_decimal_as_integer=False,
                )
                temporary.flush()
                os.fsync(temporary.fileno())
            if self.read(temporary_path) != sequence:
                raise ParquetStorageError("temporary dataset read-back differs from input")
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                # Another writer won publication. Its file must be fully checked
                # under exactly the same immutable/idempotent rules.
                return self._existing(destination, sequence)
            return destination
        except ParquetStorageError:
            raise
        except (OSError, pa.ArrowException, ValueError, TypeError, OverflowError) as error:
            raise ParquetStorageError(f"cannot publish canonical Parquet dataset: {error}") from error
        finally:
            if temporary_path is not None:
                active_error = sys.exception()
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    if active_error is not None:
                        active_error.add_note(f"temporary cleanup also failed: {cleanup_error}")
                    else:
                        raise ParquetStorageError(
                            f"cannot remove temporary dataset {temporary_path}: {cleanup_error}"
                        ) from cleanup_error

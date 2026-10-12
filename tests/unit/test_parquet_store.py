"""Offline canonical Parquet schema, identity, and read-integrity contracts."""

from __future__ import annotations

import base64
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from quantos.domain.market_data import (
    Candle,
    DatasetIdentity,
    DatasetValidationStatus,
    ValidatedCandleSequence,
    validate_candle_sequence,
)
from quantos.infrastructure.storage.parquet import (
    CANDLE_SCHEMA,
    STORAGE_SCHEMA_VERSION,
    ParquetCandleDatasetStore,
    ParquetStorageError,
    dataset_id,
)

UTC = timezone.utc
START = datetime(2024, 12, 31, 23, 59, tzinfo=UTC)
DECIMAL_FIELDS = ("open", "high", "low", "close", "volume", "quote_volume")
IDENTITY_FIELDS = (
    "symbol", "timeframe", "start_time", "end_time", "source",
    "schema_version", "ingestion_version", "validation_status",
)
EXPECTED_SCHEMA = pa.schema([
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("interval", pa.string(), nullable=False),
    pa.field("open_time", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("close_time", pa.timestamp("us", tz="UTC"), nullable=False),
    *(pa.field(name, pa.decimal128(38, 18), nullable=False) for name in DECIMAL_FIELDS),
    pa.field("trade_count", pa.int64(), nullable=False),
])


def sample_candle(open_time: datetime, *, symbol: str = "BTCUSDT") -> Candle:
    last_microsecond = 999_000 if open_time.year < 2025 else 999_999
    return Candle(
        symbol=symbol,
        interval="1m",
        open_time=open_time,
        close_time=open_time + timedelta(seconds=59, microseconds=last_microsecond),
        open=Decimal("100.123456789012345678"),
        high=Decimal("103.123456789012345678"),
        low=Decimal("99.123456789012345678"),
        close=Decimal("102.123456789012345678"),
        volume=Decimal("0.123456789012345678"),
        quote_volume=Decimal("12.123456789012345678"),
        trade_count=7,
    )


def sample_sequence(
    *, symbol: str = "BTCUSDT", count: int = 3, start: datetime = START,
    **identity_overrides: object,
) -> ValidatedCandleSequence:
    candles = tuple(
        sample_candle(start + timedelta(minutes=index), symbol=symbol)
        for index in range(count)
    )
    identity_values = {
        "symbol": symbol, "timeframe": "1m",
        "start_time": candles[0].open_time, "end_time": candles[-1].open_time,
        "source": "offline-fixture", "schema_version": "candle-v1",
        "ingestion_version": "fixture-2c1-v1",
    }
    identity_values.update(identity_overrides)
    return validate_candle_sequence(DatasetIdentity(**identity_values), candles)


def identity_strings(identity: DatasetIdentity) -> dict[str, str]:
    return {
        "symbol": identity.symbol,
        "timeframe": identity.timeframe,
        "start_time": identity.start_time.isoformat(timespec="microseconds"),
        "end_time": identity.end_time.isoformat(timespec="microseconds"),
        "source": identity.source,
        "schema_version": identity.schema_version,
        "ingestion_version": identity.ingestion_version,
        "validation_status": identity.validation_status.value,
    }


def expected_digest(identity: DatasetIdentity) -> str:
    payload = json.dumps(
        identity_strings(identity), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def metadata_for(identity: DatasetIdentity) -> dict[bytes, bytes]:
    metadata = {
        f"quantos.{key}".encode("ascii"): value.encode("utf-8")
        for key, value in identity_strings(identity).items()
    }
    metadata[b"quantos.storage_schema_version"] = b"parquet-v1"
    metadata[b"quantos.dataset_id"] = expected_digest(identity).encode("ascii")
    return metadata


class ParquetStoreContractTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = ParquetCandleDatasetStore(self.root)
        self.sequence = sample_sequence()

    def external_file(
        self, *, schema: pa.Schema = EXPECTED_SCHEMA,
        metadata: dict[bytes, bytes] | None = None,
        rows: list[dict[str, object]] | None = None,
        name: str = "external.parquet", store_schema: bool = True,
    ) -> Path:
        if rows is None:
            rows = [asdict(candle) for candle in self.sequence.candles]
        if metadata is None:
            metadata = metadata_for(self.sequence.identity)
        table = pa.Table.from_pylist(rows, schema=schema.with_metadata(metadata))
        path = self.root / name
        pq.write_table(
            table, path, version="2.6", compression="snappy",
            write_page_checksum=True, store_schema=store_schema,
        )
        return path

    def assert_write_rejected(self, candle: Candle) -> None:
        source = sample_sequence(count=1)
        sequence = ValidatedCandleSequence(source.identity, (candle,))
        with self.assertRaises(ParquetStorageError):
            self.store.write(sequence)
        self.assertEqual(list(self.root.rglob("*.parquet")), [])
        self.assertEqual([path for path in self.root.rglob("*") if path.is_file()], [])

    def test_public_schema_has_exact_columns_order_types_and_non_nullability(self) -> None:
        self.assertTrue(CANDLE_SCHEMA.equals(EXPECTED_SCHEMA, check_metadata=False))
        self.assertEqual(STORAGE_SCHEMA_VERSION, "parquet-v1")
        path = self.store.write(self.sequence)
        with pq.ParquetFile(path) as persisted:
            self.assertTrue(persisted.schema_arrow.equals(EXPECTED_SCHEMA, check_metadata=False))
            self.assertTrue(
                persisted.schema.to_arrow_schema().equals(EXPECTED_SCHEMA, check_metadata=False)
            )
            for index, field in enumerate(EXPECTED_SCHEMA):
                self.assertFalse(field.nullable)
                self.assertEqual(persisted.schema.column(index).max_definition_level, 0)
                self.assertFalse(pa.types.is_floating(field.type))

    def test_round_trip_single_candle_for_each_v1_symbol(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            with self.subTest(symbol=symbol):
                sequence = sample_sequence(symbol=symbol, count=1)
                result = self.store.read(self.store.write(sequence))
                self.assertEqual(result, sequence)
                self.assertIsInstance(result, ValidatedCandleSequence)
                self.assertEqual(result.identity.start_time, result.identity.end_time)
                self.assertIs(result.identity.validation_status, DatasetValidationStatus.VALIDATED)
                self.assertIsInstance(result.candles, tuple)

    def test_complete_multi_day_round_trip_preserves_both_timestamp_eras(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            with self.subTest(symbol=symbol):
                sequence = sample_sequence(
                    symbol=symbol, count=2880, start=datetime(2024, 12, 31, tzinfo=UTC)
                )
                result = self.store.read(self.store.write(sequence))
                self.assertEqual(result, sequence)
                self.assertEqual(len(result.candles), 2880)
                self.assertEqual(result.candles[1439].close_time.microsecond, 999_000)
                self.assertEqual(result.candles[1440].close_time.microsecond, 999_999)
                self.assertEqual(
                    result.candles[1440].open_time - result.candles[1439].open_time,
                    timedelta(minutes=1),
                )
                for candle in result.candles:
                    self.assertEqual(candle.open_time.utcoffset(), timedelta(0))
                    self.assertEqual(candle.close_time.utcoffset(), timedelta(0))
                    for name in DECIMAL_FIELDS:
                        self.assertIsInstance(getattr(candle, name), Decimal)

    def test_exact_decimal_values_survive_low_decimal_context_precision(self) -> None:
        with localcontext() as context:
            context.prec = 6
            result = self.store.read(self.store.write(self.sequence))
        self.assertEqual(result, self.sequence)
        for actual, expected in zip(result.candles, self.sequence.candles, strict=True):
            for name in DECIMAL_FIELDS:
                self.assertEqual(getattr(actual, name), getattr(expected, name))

    def test_maximum_representable_decimal_and_int64_are_exact(self) -> None:
        source = sample_sequence(count=1)
        maximum = Decimal("99999999999999999999.999999999999999999")
        candle = replace(source.candles[0], quote_volume=maximum, trade_count=2**63 - 1)
        sequence = ValidatedCandleSequence(source.identity, (candle,))
        self.assertEqual(self.store.read(self.store.write(sequence)), sequence)

    def test_redundant_fractional_zeros_are_not_loss_of_decimal_value(self) -> None:
        source = sample_sequence(count=1)
        candle = replace(source.candles[0], volume=Decimal("1.0000000000000000000"))
        sequence = ValidatedCandleSequence(source.identity, (candle,))
        self.assertEqual(self.store.read(self.store.write(sequence)), sequence)

    def test_nineteen_meaningful_fractional_digits_fail_without_publication(self) -> None:
        self.assert_write_rejected(
            replace(sample_candle(START), volume=Decimal("0.1234567890123456789"))
        )

    def test_decimal_overflow_fails_without_publication(self) -> None:
        self.assert_write_rejected(
            replace(sample_candle(START), quote_volume=Decimal("100000000000000000000"))
        )

    def test_int64_overflow_fails_without_publication(self) -> None:
        self.assert_write_rejected(replace(sample_candle(START), trade_count=2**63))

    def test_extended_timestamp_precision_fails_without_publication(self) -> None:
        class NanosecondDatetime(datetime):
            nanosecond = 1

        extended = NanosecondDatetime(2024, 12, 31, 23, 59, 59, 999_000, tzinfo=UTC)
        self.assert_write_rejected(replace(sample_candle(START), close_time=extended))

    def test_dataset_id_matches_independent_canonical_eight_dimension_digest(self) -> None:
        identity = self.sequence.identity
        self.assertEqual(set(identity_strings(identity)), set(IDENTITY_FIELDS))
        self.assertEqual(dataset_id(identity), expected_digest(identity))
        self.assertEqual(
            dataset_id(identity),
            "f62242a21ea5d8780aa42053102e0b967d0845247826a704feb3e5414f050d49",
        )
        self.assertRegex(dataset_id(identity), r"^[0-9a-f]{64}$")
        unvalidated = replace(identity)
        self.assertEqual(dataset_id(unvalidated), expected_digest(unvalidated))
        self.assertNotEqual(dataset_id(identity), dataset_id(unvalidated))
        for values in (
            {"symbol": "ETHUSDT"}, {"start_time": START - timedelta(minutes=1)},
            {"end_time": START + timedelta(minutes=3)}, {"source": "other"},
            {"schema_version": "candle-v2"}, {"ingestion_version": "other"},
        ):
            with self.subTest(values=values):
                changed = replace(identity, **values)._validated_copy()
                self.assertNotEqual(dataset_id(identity), dataset_id(changed))
                self.assertEqual(dataset_id(changed), expected_digest(changed))

    def test_path_uses_fixed_structure_and_full_digest_without_creating_files(self) -> None:
        identity = self.sequence.identity
        expected = (
            self.root / "market_data" / "candles" / "parquet-v1" /
            "BTCUSDT" / "1m" / f"{expected_digest(identity)}.parquet"
        )
        self.assertEqual(self.store.dataset_path(identity), expected)
        self.assertEqual(self.store.dataset_path(identity), self.store.dataset_path(identity))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_raw_metadata_cannot_traverse_or_change_the_path_structure(self) -> None:
        for field in ("source", "schema_version", "ingestion_version"):
            for value in ("../x", "a/b", "a\\b", "du lieu \u03bc \u6570\u636e"):
                with self.subTest(field=field, value=value):
                    sequence = sample_sequence(**{field: value})
                    path = self.store.dataset_path(sequence.identity)
                    self.assertEqual(
                        path.relative_to(self.root).parts,
                        ("market_data", "candles", "parquet-v1", "BTCUSDT", "1m",
                         f"{expected_digest(sequence.identity)}.parquet"),
                    )
                    self.assertEqual(self.store.read(self.store.write(sequence)), sequence)

    def test_unvalidated_identity_cannot_produce_a_canonical_path(self) -> None:
        with self.assertRaises(ParquetStorageError):
            self.store.dataset_path(replace(self.sequence.identity))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_metadata_preserves_all_identity_fields_and_versions(self) -> None:
        path = self.store.write(self.sequence)
        with pq.ParquetFile(path) as persisted:
            metadata = persisted.schema_arrow.metadata
            for key, value in metadata_for(self.sequence.identity).items():
                self.assertEqual(metadata[key], value)
            self.assertEqual(
                metadata[b"quantos.start_time"], b"2024-12-31T23:59:00.000000+00:00"
            )

    def test_missing_each_required_metadata_field_is_rejected(self) -> None:
        required = metadata_for(self.sequence.identity)
        for field in required:
            with self.subTest(field=field):
                metadata = {key: value for key, value in required.items() if key != field}
                with self.assertRaises(ParquetStorageError):
                    self.store.read(self.external_file(metadata=metadata))

    def test_missing_all_quantos_metadata_is_rejected(self) -> None:
        with self.assertRaises(ParquetStorageError):
            self.store.read(self.external_file(metadata={}))

    def test_invalid_utf8_metadata_is_rejected(self) -> None:
        for field in metadata_for(self.sequence.identity):
            with self.subTest(field=field):
                metadata = metadata_for(self.sequence.identity)
                metadata[field] = b"\xff\xfe"
                with self.assertRaises(ParquetStorageError):
                    self.store.read(self.external_file(metadata=metadata))

    def test_invalid_metadata_values_fail_closed(self) -> None:
        invalid = (
            (b"quantos.dataset_id", b"0" * 64),
            (b"quantos.storage_schema_version", b"parquet-v2"),
            (b"quantos.validation_status", b"unvalidated"),
            (b"quantos.validation_status", b"VALIDATED"),
            (b"quantos.symbol", b"SOLUSDT"),
            (b"quantos.timeframe", b"5m"),
            (b"quantos.source", b""),
            (b"quantos.schema_version", b" "),
            (b"quantos.ingestion_version", b""),
            (b"quantos.start_time", b"not-a-datetime"),
            (b"quantos.start_time", b"2024-12-31T23:59:00.000000"),
            (b"quantos.start_time", b"2024-12-31T23:59:00.0000001+00:00"),
            (b"quantos.start_time", b"2024-12-31T23:59:00Z"),
            (b"quantos.end_time", b"2025-01-01T00:01:00.000000+07:00"),
        )
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                metadata = metadata_for(self.sequence.identity)
                metadata[field] = value
                with self.assertRaises(ParquetStorageError):
                    self.store.read(self.external_file(metadata=metadata))

    def test_self_consistent_unvalidated_metadata_is_still_rejected(self) -> None:
        metadata = metadata_for(replace(self.sequence.identity))
        with self.assertRaises(ParquetStorageError):
            self.store.read(self.external_file(metadata=metadata))

    def test_wrong_column_order_missing_extra_and_nullable_columns_are_rejected(self) -> None:
        schemas = (
            pa.schema(list(EXPECTED_SCHEMA)[::-1]),
            EXPECTED_SCHEMA.remove(EXPECTED_SCHEMA.get_field_index("trade_count")),
            EXPECTED_SCHEMA.append(pa.field("unexpected", pa.string(), nullable=True)),
            EXPECTED_SCHEMA.set(0, pa.field("symbol", pa.string(), nullable=True)),
        )
        for schema in schemas:
            with self.subTest(schema=schema):
                with self.assertRaises(ParquetStorageError):
                    self.store.read(self.external_file(schema=schema))

    def test_wrong_physical_column_types_are_rejected(self) -> None:
        variants = (
            ("open_time", pa.timestamp("ms", tz="UTC")),
            ("open_time", pa.timestamp("ns", tz="UTC")),
            ("open_time", pa.timestamp("us")),
            ("open", pa.decimal128(38, 17)),
            ("open", pa.float64()),
            ("trade_count", pa.int32()),
        )
        for name, data_type in variants:
            with self.subTest(name=name, data_type=data_type):
                index = EXPECTED_SCHEMA.get_field_index(name)
                schema = EXPECTED_SCHEMA.set(index, pa.field(name, data_type, nullable=False))
                rows = [asdict(candle) for candle in self.sequence.candles]
                if name == "open":
                    for row in rows:
                        row["open"] = 100.0 if pa.types.is_floating(data_type) else Decimal("100")
                with self.assertRaises(ParquetStorageError):
                    self.store.read(self.external_file(schema=schema, rows=rows))

    def test_forged_arrow_schema_does_not_hide_nullable_physical_column(self) -> None:
        schema = EXPECTED_SCHEMA.set(0, pa.field("symbol", pa.string(), nullable=True))
        metadata = metadata_for(self.sequence.identity)
        metadata[b"ARROW:schema"] = base64.b64encode(EXPECTED_SCHEMA.serialize().to_pybytes())
        path = self.external_file(schema=schema, metadata=metadata, store_schema=False)
        with pq.ParquetFile(path) as persisted:
            self.assertEqual(persisted.schema.column(0).max_definition_level, 1)
        with self.assertRaises(ParquetStorageError):
            self.store.read(path)

    def test_read_revalidates_duplicate_gap_order_and_identity_boundaries(self) -> None:
        rows = [asdict(candle) for candle in self.sequence.candles]
        invalid_rows = (
            [rows[0], rows[1], rows[1]],
            [rows[0], rows[2]],
            [rows[1], rows[0], rows[2]],
            rows[1:],
            rows[:-1],
            [],
        )
        for candidates in invalid_rows:
            with self.subTest(candidates=candidates):
                with self.assertRaises(ParquetStorageError):
                    self.store.read(self.external_file(rows=candidates))

    def test_read_rejects_invalid_candle_contract_even_with_validated_metadata(self) -> None:
        for overrides in (
            {"symbol": "ETHUSDT"}, {"interval": "5m"},
            {"high": Decimal("1")}, {"volume": Decimal("-1")},
            {"trade_count": -1}, {"close_time": START},
        ):
            with self.subTest(overrides=overrides):
                rows = [asdict(candle) for candle in self.sequence.candles]
                rows[0].update(overrides)
                with self.assertRaises(ParquetStorageError):
                    self.store.read(self.external_file(rows=rows))

    def test_read_uses_unvalidated_identity_through_canonical_validation(self) -> None:
        path = self.external_file()
        with patch(
            "quantos.infrastructure.storage.parquet.validate_candle_sequence",
            wraps=validate_candle_sequence,
        ) as validation:
            result = self.store.read(path)
        validation.assert_called_once()
        initial_identity, candidates = validation.call_args.args
        self.assertIs(initial_identity.validation_status, DatasetValidationStatus.UNVALIDATED)
        self.assertEqual(tuple(candidates), self.sequence.candles)
        self.assertEqual(result.identity, self.sequence.identity)

    def test_malformed_corrupt_and_missing_files_fail_closed(self) -> None:
        path = self.root / "corrupt.parquet"
        for content in (b"", b"not parquet", b"PAR1broken footerPAR1"):
            with self.subTest(content=content):
                path.write_bytes(content)
                with self.assertRaises(ParquetStorageError):
                    self.store.read(path)
        with self.assertRaises(ParquetStorageError):
            self.store.read(self.root / "missing.parquet")

    def test_every_reader_opens_with_page_checksum_verification_enabled(self) -> None:
        path = self.external_file()
        with patch(
            "quantos.infrastructure.storage.parquet.pq.ParquetFile", wraps=pq.ParquetFile
        ) as reader:
            self.assertEqual(self.store.read(path), self.sequence)
        self.assertGreaterEqual(reader.call_count, 1)
        for invocation in reader.call_args_list:
            self.assertIs(invocation.kwargs.get("page_checksum_verification"), True)

    def test_corrupted_data_page_is_rejected_by_checksum_verification(self) -> None:
        table = pa.Table.from_pylist(
            [asdict(candle) for candle in self.sequence.candles],
            schema=EXPECTED_SCHEMA.with_metadata(metadata_for(self.sequence.identity)),
        )
        path = self.root / "damaged-page.parquet"
        pq.write_table(
            table, path, version="2.6", compression="NONE", use_dictionary=False,
            data_page_version="1.0", write_page_checksum=True, store_schema=True,
        )
        with pq.ParquetFile(path) as parquet:
            column = parquet.metadata.row_group(0).column(0)
            payload_end = column.data_page_offset + column.total_compressed_size - 1
        content = bytearray(path.read_bytes())
        content[payload_end] ^= 1
        path.write_bytes(content)
        # The file and page still decode; only their recorded checksum exposes
        # this payload mutation before any altered data can be trusted.
        with pq.ParquetFile(path, page_checksum_verification=False) as parquet:
            self.assertEqual(parquet.read().num_rows, len(self.sequence.candles))
        with self.assertRaisesRegex(ParquetStorageError, "CRC|checksum"):
            self.store.read(path)

    def test_writer_uses_explicit_integrity_settings(self) -> None:
        with patch(
            "quantos.infrastructure.storage.parquet.pq.write_table", wraps=pq.write_table
        ) as writer:
            self.store.write(self.sequence)
        writer.assert_called_once()
        options = writer.call_args.kwargs
        self.assertEqual(options["version"], "2.6")
        self.assertIs(options["write_page_checksum"], True)
        self.assertIs(options["store_schema"], True)
        self.assertIn("compression", options)
        self.assertIs(options["allow_truncated_timestamps"], False)

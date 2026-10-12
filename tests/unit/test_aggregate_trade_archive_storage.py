"""Offline exactness and immutability tests for aggregate-trade archives."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZIP_STORED, ZipFile

import pyarrow.parquet as pq

from quantos.domain.market_data.research_events import (
    AggregateTradeArchiveManifest,
    ValidatedAggregateTradeArchive,
    aggregate_trade_archive_manifest_bytes,
    aggregate_trade_archive_manifest_from_bytes,
    aggregate_trade_archive_manifest_id,
)
from quantos.infrastructure.binance import (
    aggregate_trade_archive_resource_urls,
)
from quantos.infrastructure.storage import (
    AGGREGATE_TRADE_SCHEMA,
    AGGREGATE_TRADE_STORAGE_SCHEMA_VERSION,
    AggregateTradeDatasetCollisionError,
    AggregateTradeParquetStorageError,
    DuckDBAggregateTradeArchiveQuery,
    DuckDBAggregateTradeQueryError,
    ParquetAggregateTradeArchiveStore,
)
from tests.unit.test_binance_aggregate_trade_archive import (
    MICROSECOND_DATE,
    MICROSECOND_TIMESTAMP,
    adapter_for_zip,
    csv_bytes,
    fetch,
    row,
    zip_bytes,
)

UTC = timezone.utc


def archive_rows() -> list[list[str]]:
    return [
        row(price="93452.1200", quantity="0.00170000"),
        row(
            aggregate_trade_id="701",
            price="93452.130000000000000001",
            quantity="1.2300",
            first_trade_id="903",
            last_trade_id="904",
            timestamp=str(MICROSECOND_TIMESTAMP + 1),
            buyer_is_maker="True",
            best_price_match="False",
        ),
    ]


def fetched_archive(*, raw_bytes: bytes | None = None):
    content = raw_bytes if raw_bytes is not None else zip_bytes(archive_rows())
    return fetch(adapter_for_zip(content)[0])


def byte_distinct_zip(rows: list[list[str]]) -> bytes:
    _, _, _, csv_filename = aggregate_trade_archive_resource_urls(
        symbol="BTCUSDT", archive_date=MICROSECOND_DATE
    )
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        archive.writestr(csv_filename, csv_bytes(rows))
    return output.getvalue()


class ArchiveManifestTests(unittest.TestCase):
    def test_manifest_bytes_and_id_are_canonical_and_reproducible(self) -> None:
        manifest = fetched_archive().archive.manifest

        first = aggregate_trade_archive_manifest_bytes(manifest)
        second = aggregate_trade_archive_manifest_bytes(manifest)

        self.assertEqual(first, second)
        self.assertEqual(
            aggregate_trade_archive_manifest_from_bytes(first), manifest
        )
        self.assertEqual(
            aggregate_trade_archive_manifest_id(manifest),
            aggregate_trade_archive_manifest_id(
                aggregate_trade_archive_manifest_from_bytes(first)
            ),
        )
        self.assertTrue(first.endswith(b"\n"))

    def test_manifest_parser_rejects_noncanonical_and_duplicate_json(self) -> None:
        manifest = fetched_archive().archive.manifest
        payload = aggregate_trade_archive_manifest_bytes(manifest)
        noncanonical = payload.replace(b'{"', b'{ "', 1)
        duplicate = payload.replace(
            b'"accepted_row_count":2',
            b'"accepted_row_count":2,"accepted_row_count":2',
            1,
        )
        for invalid in (noncanonical, duplicate, b"{}", b"\xff"):
            with self.subTest(invalid=invalid[:40]):
                with self.assertRaises(ValueError):
                    aggregate_trade_archive_manifest_from_bytes(invalid)

    def test_manifest_rejects_unverified_or_inconsistent_source_health(self) -> None:
        manifest = fetched_archive().archive.manifest
        invalid_changes = (
            {"parsed_row_count": 3},
            {"rejected_row_count": 1},
            {"duplicate_count": 1},
            {"conflicting_id_count": 1},
            {"observed_numerical_id_gap_count": 2},
            {"published_checksum": "0" * 64},
        )
        for changes in invalid_changes:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    replace(manifest, **changes)

    def test_archive_recomputes_recorded_numerical_gap_count(self) -> None:
        fetched = fetched_archive()
        forged_manifest = replace(
            fetched.archive.manifest,
            observed_numerical_id_gap_count=1,
        )

        with self.assertRaisesRegex(ValueError, "gap count"):
            ValidatedAggregateTradeArchive(
                fetched.archive.sequence, forged_manifest
            )


class ParquetAggregateTradeArchiveStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = ParquetAggregateTradeArchiveStore(self.root)
        self.fetched = fetched_archive()
        self.publication = self.store.write(
            self.fetched.archive,
            raw_zip_bytes=self.fetched.raw_zip_bytes,
        )

    def test_exact_raw_parquet_manifest_decimal_and_timestamp_round_trip(self) -> None:
        publication = self.publication
        manifest = self.fetched.archive.manifest

        self.assertEqual(
            publication.raw_archive_path,
            self.store.raw_archive_path(manifest),
        )
        self.assertEqual(
            publication.canonical_parquet_path,
            self.store.dataset_path(manifest),
        )
        self.assertEqual(
            publication.manifest_id,
            aggregate_trade_archive_manifest_id(manifest),
        )
        self.assertEqual(
            self.store.read_raw(manifest), self.fetched.raw_zip_bytes
        )
        restored = self.store.read(publication.canonical_parquet_path)
        self.assertEqual(restored, self.fetched.archive)
        self.assertEqual(
            restored.sequence.events[0].price.as_tuple(),
            Decimal("93452.1200").as_tuple(),
        )
        self.assertEqual(
            restored.sequence.events[0].quantity.as_tuple(),
            Decimal("0.00170000").as_tuple(),
        )
        self.assertEqual(
            restored.sequence.events[1].price.as_tuple(),
            Decimal("93452.130000000000000001").as_tuple(),
        )
        self.assertEqual(
            restored.sequence.events[1].event_time,
            datetime(2025, 1, 1, 0, 0, 0, 10_867, tzinfo=UTC),
        )
        with pq.ParquetFile(publication.canonical_parquet_path) as parquet:
            self.assertTrue(
                parquet.schema_arrow.equals(
                    AGGREGATE_TRADE_SCHEMA, check_metadata=False
                )
            )
            metadata = parquet.metadata.metadata or {}
        self.assertEqual(
            metadata[b"quantos.aggregate_trade_storage_schema_version"],
            AGGREGATE_TRADE_STORAGE_SCHEMA_VERSION.encode("ascii"),
        )
        self.assertEqual(
            metadata[b"quantos.aggregate_trade_archive_manifest"],
            aggregate_trade_archive_manifest_bytes(manifest),
        )

    def test_identical_republication_is_idempotent_and_byte_preserving(self) -> None:
        before_raw = self.publication.raw_archive_path.read_bytes()
        before_parquet = self.publication.canonical_parquet_path.read_bytes()

        repeated = self.store.write(
            self.fetched.archive,
            raw_zip_bytes=self.fetched.raw_zip_bytes,
        )

        self.assertEqual(repeated, self.publication)
        self.assertEqual(
            repeated.raw_archive_path.read_bytes(), before_raw
        )
        self.assertEqual(
            repeated.canonical_parquet_path.read_bytes(), before_parquet
        )

    def test_changed_valid_raw_revision_is_published_as_immutable_successor(self) -> None:
        revised_bytes = byte_distinct_zip(archive_rows())
        self.assertNotEqual(revised_bytes, self.fetched.raw_zip_bytes)
        revised = fetched_archive(raw_bytes=revised_bytes)

        revised_publication = self.store.write(
            revised.archive, raw_zip_bytes=revised.raw_zip_bytes
        )

        self.assertNotEqual(
            revised.archive.manifest.source_revision_id,
            self.fetched.archive.manifest.source_revision_id,
        )
        self.assertNotEqual(
            revised_publication.raw_archive_path,
            self.publication.raw_archive_path,
        )
        self.assertNotEqual(
            revised_publication.canonical_parquet_path,
            self.publication.canonical_parquet_path,
        )
        self.assertTrue(self.publication.raw_archive_path.exists())
        self.assertTrue(self.publication.canonical_parquet_path.exists())
        self.assertEqual(
            self.store.read(revised_publication.canonical_parquet_path),
            revised.archive,
        )

    def test_conflicting_raw_and_canonical_publications_fail_closed(self) -> None:
        self.publication.raw_archive_path.write_bytes(b"conflicting bytes")
        with self.assertRaises(AggregateTradeDatasetCollisionError):
            self.store.write(
                self.fetched.archive,
                raw_zip_bytes=self.fetched.raw_zip_bytes,
            )

        self.publication.raw_archive_path.write_bytes(
            self.fetched.raw_zip_bytes
        )
        revised = fetched_archive(raw_bytes=byte_distinct_zip(archive_rows()))
        revised_publication = self.store.write(
            revised.archive, raw_zip_bytes=revised.raw_zip_bytes
        )
        self.publication.canonical_parquet_path.write_bytes(
            revised_publication.canonical_parquet_path.read_bytes()
        )
        with self.assertRaises(AggregateTradeDatasetCollisionError):
            self.store.write(
                self.fetched.archive,
                raw_zip_bytes=self.fetched.raw_zip_bytes,
            )

    def test_corrupt_schema_metadata_and_raw_hash_are_rejected(self) -> None:
        corrupt = self.root / "corrupt.parquet"
        corrupt.write_bytes(b"PAR1 broken PAR1")
        with self.assertRaises(AggregateTradeParquetStorageError):
            self.store.read(corrupt)

        with pq.ParquetFile(self.publication.canonical_parquet_path) as parquet:
            table = parquet.read()
        metadata = dict(table.schema.metadata or {})
        metadata[
            b"quantos.aggregate_trade_storage_schema_version"
        ] = b"wrong-version"
        invalid_metadata = self.root / "invalid-metadata.parquet"
        pq.write_table(
            table.replace_schema_metadata(metadata),
            invalid_metadata,
            version="2.6",
            write_page_checksum=True,
            store_schema=True,
        )
        with self.assertRaises(AggregateTradeParquetStorageError):
            self.store.read(invalid_metadata)

        self.publication.raw_archive_path.write_bytes(b"corrupt")
        with self.assertRaises(AggregateTradeParquetStorageError):
            self.store.read_raw(self.fetched.archive.manifest)

    def test_decimal_overflow_never_publishes_canonical_parquet(self) -> None:
        content = zip_bytes(
            [
                row(
                    price="123456789012345678901234567890123456789",
                    quantity="1",
                )
            ]
        )
        fetched = fetch(adapter_for_zip(content)[0])
        expected_path = self.store.dataset_path(fetched.archive.manifest)

        with self.assertRaises(AggregateTradeParquetStorageError):
            self.store.write(
                fetched.archive, raw_zip_bytes=fetched.raw_zip_bytes
            )

        self.assertFalse(expected_path.exists())

    def test_unsafe_provenance_path_segments_are_rejected(self) -> None:
        manifest = replace(
            self.fetched.archive.manifest, provider="../outside"
        )
        with self.assertRaises(AggregateTradeParquetStorageError):
            self.store.raw_archive_path(manifest)


class DuckDBAggregateTradeArchiveQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = ParquetAggregateTradeArchiveStore(self.root)
        self.fetched = fetched_archive()
        self.publication = self.store.write(
            self.fetched.archive,
            raw_zip_bytes=self.fetched.raw_zip_bytes,
        )
        self.query = DuckDBAggregateTradeArchiveQuery(self.store)

    def test_duckdb_reproduces_exact_complete_archive_without_database(self) -> None:
        before = self.publication.canonical_parquet_path.read_bytes()

        result = self.query.read_all(
            self.publication.canonical_parquet_path
        )

        self.assertEqual(result, self.fetched.archive)
        self.assertEqual(
            result.sequence.events[0].price.as_tuple(),
            Decimal("93452.1200").as_tuple(),
        )
        self.assertEqual(
            result.sequence.events[1].event_time.microsecond, 10_867
        )
        self.assertEqual(
            self.publication.canonical_parquet_path.read_bytes(), before
        )
        self.assertEqual(list(self.root.rglob("*.duckdb*")), [])

    def test_noncanonical_and_glob_paths_fail_closed(self) -> None:
        copied = self.root / "copied.parquet"
        copied.write_bytes(
            self.publication.canonical_parquet_path.read_bytes()
        )
        with self.assertRaises(DuckDBAggregateTradeQueryError):
            self.query.read_all(copied)

        glob_root = self.root / "[ambiguous]"
        glob_store = ParquetAggregateTradeArchiveStore(glob_root)
        publication = glob_store.write(
            self.fetched.archive,
            raw_zip_bytes=self.fetched.raw_zip_bytes,
        )
        with self.assertRaisesRegex(
            DuckDBAggregateTradeQueryError, "glob"
        ):
            DuckDBAggregateTradeArchiveQuery(glob_store).read_all(
                publication.canonical_parquet_path
            )


if __name__ == "__main__":
    unittest.main()

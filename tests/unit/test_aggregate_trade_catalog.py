"""Adversarial tests for the rebuildable aggregate-trade catalog."""

from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quantos.infrastructure.storage import (
    AggregateTradeArchiveCatalogView,
    AggregateTradeCatalogError,
    CatalogedAggregateTradeRevision,
)
from tests.aggregate_trade_range_fixtures import local_catalog, publish_partition


class AggregateTradeCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_empty_and_single_revision_rebuild(self) -> None:
        catalog = local_catalog(self.root)
        self.assertEqual(catalog.rebuild().entries, ())
        fetched, publication = publish_partition(
            self.root,
            symbol="BTCUSDT",
            source_date=date(2024, 12, 31),
            first_id=10,
        )

        view = catalog.rebuild()

        self.assertEqual(len(view.entries), 1)
        self.assertEqual(view.entries[0].manifest, fetched.archive.manifest)
        self.assertEqual(view.entries[0].canonical_parquet_path, publication.canonical_parquet_path)
        self.assertEqual(len(view.catalog_id), 64)

    def test_multiple_revisions_are_preserved_and_rebuild_is_order_independent(self) -> None:
        first, first_publication = publish_partition(
            self.root,
            symbol="BTCUSDT",
            source_date=date(2025, 1, 1),
            first_id=10,
        )
        second, second_publication = publish_partition(
            self.root,
            symbol="BTCUSDT",
            source_date=date(2025, 1, 1),
            first_id=20,
            stored=True,
        )
        catalog = local_catalog(self.root)
        canonical_id = catalog.rebuild().catalog_id
        candidates = catalog._candidate_paths()
        with patch.object(catalog, "_candidate_paths", return_value=tuple(reversed(candidates))):
            reverse_id = catalog.rebuild().catalog_id
        os.utime(first_publication.canonical_parquet_path, (1, 1))
        os.utime(second_publication.canonical_parquet_path, (2_000_000_000, 2_000_000_000))
        mtime_id = catalog.rebuild().catalog_id

        revisions = catalog.revisions(symbol="BTCUSDT", source_date=date(2025, 1, 1))
        self.assertEqual(len(revisions), 2)
        self.assertEqual(canonical_id, reverse_id)
        self.assertEqual(canonical_id, mtime_id)
        self.assertEqual(
            {item.source_revision_id for item in revisions},
            {first.archive.manifest.source_revision_id, second.archive.manifest.source_revision_id},
        )

    def test_duplicate_catalog_manifest_is_rejected(self) -> None:
        fetched, publication = publish_partition(
            self.root,
            symbol="ETHUSDT",
            source_date=date(2025, 1, 1),
            first_id=10,
        )
        entry = CatalogedAggregateTradeRevision(
            fetched.archive.manifest,
            publication.canonical_parquet_path,
            publication.raw_archive_path,
        )
        with self.assertRaisesRegex(AggregateTradeCatalogError, "duplicate catalog manifest"):
            AggregateTradeArchiveCatalogView((entry, entry))

    def test_conflicting_catalog_scope_is_rejected(self) -> None:
        publish_partition(
            self.root,
            symbol="BTCUSDT",
            source_date=date(2025, 1, 1),
            first_id=10,
        )
        from quantos.infrastructure.storage import LocalAggregateTradeArchiveCatalog

        catalog = LocalAggregateTradeArchiveCatalog(
            self.root,
            provider="other",
            market="spot",
            event_family="aggregate_trade",
        )
        with self.assertRaisesRegex(AggregateTradeCatalogError, "conflicting logical scope"):
            catalog.rebuild()

    def test_corruption_missing_reference_and_hash_mismatch_fail_closed(self) -> None:
        fetched, publication = publish_partition(
            self.root,
            symbol="BTCUSDT",
            source_date=date(2025, 1, 1),
            first_id=10,
        )
        catalog = local_catalog(self.root)
        catalog.rebuild()

        publication.raw_archive_path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(AggregateTradeCatalogError, "failed verification"):
            catalog.load(fetched.archive.manifest)

        publication.raw_archive_path.unlink()
        with self.assertRaisesRegex(AggregateTradeCatalogError, "failed verification"):
            catalog.load(fetched.archive.manifest)

        publication.canonical_parquet_path.write_bytes(b"not parquet")
        with self.assertRaisesRegex(AggregateTradeCatalogError, "catalog rebuild failed"):
            catalog.rebuild()
        self.assertEqual(catalog.view.entries, ())

    def test_rejects_unsupported_symbol(self) -> None:
        catalog = local_catalog(self.root)
        catalog.rebuild()
        with self.assertRaises(ValueError):
            catalog.revisions(symbol="SOLUSDT", source_date=date(2025, 1, 1))


if __name__ == "__main__":
    unittest.main()

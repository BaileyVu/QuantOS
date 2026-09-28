"""A1-O1 exact verified-range proof, compatibility and corruption parity."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pyarrow.parquet as pq

from quantos.application import (
    AggregateTradeRangeCompositionError,
    compose_aggregate_trade_range,
)
from quantos.domain.market_data.research_events import (
    AggregateTradeRangeRequest,
    RevisionSelectionPolicy,
    aggregate_trade_range_manifest_bytes,
)
from quantos.infrastructure.storage import AggregateTradeCatalogError
from quantos.infrastructure.storage import aggregate_trade_catalog as catalog_module
from tests.aggregate_trade_range_fixtures import local_catalog, partition_rows, publish_rows
from tests.unit.test_aggregate_trade_range_composition import exact


START = date(2024, 12, 31)


class StreamOnlyCatalog:
    """Existing DE1H port, deliberately without the optional O1 capability."""

    def __init__(self, catalog):
        self.revisions = catalog.revisions
        self.load = catalog.load
        self.stream_range = catalog.stream_range


class LoadOnlyCatalog:
    """Pre-DE1H compatibility port, without either optional capability."""

    def __init__(self, catalog):
        self.revisions = catalog.revisions
        self.load = catalog.load


class VerifiedRangeTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="quantos-o1-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def publish(self, day_index, ids, *, price=None):
        source_date = START + timedelta(days=day_index)
        rows = partition_rows(source_date, first_id=1)
        # Existing fixtures cross the provider's millisecond/microsecond boundary.
        if day_index > 1:
            for row in rows:
                row[5] = str(int(row[5]) + (day_index - 1) * 86_400_000_000)
        for row, identifier in zip(rows, ids, strict=True):
            row[0] = str(identifier)
            if price is not None:
                row[1] = price
        return publish_rows(self.root, symbol="BTCUSDT", source_date=source_date, rows=rows)

    def setup_catalog(self, ids=((10, 100), (150, 200))):
        for day_index, values in enumerate(ids):
            self.publish(day_index, values)
        catalog = local_catalog(self.root)
        catalog.rebuild()
        request = AggregateTradeRangeRequest(
            symbol="BTCUSDT", start_date=START,
            end_date_exclusive=START + timedelta(days=len(ids)),
            selection_policy=RevisionSelectionPolicy.UNIQUE,
        )
        return catalog, request

    def test_fast_path_matches_full_manifest_bytes_every_field_and_batch_sizes(self):
        catalog, request = self.setup_catalog()
        baseline = compose_aggregate_trade_range(StreamOnlyCatalog(catalog), request)
        for size in (1, 2, 64, 4096, 16384, 65536, 262144):
            with self.subTest(size=size), patch.object(
                catalog, "stream_range", side_effect=AssertionError("unexpected replay")
            ), patch.object(
                catalog._store, "iter_event_batches",
                side_effect=AssertionError("unexpected event decoding"),
            ):
                actual = compose_aggregate_trade_range(catalog, request, batch_size=size)
            self.assertEqual(actual, baseline)  # Includes all partition and boundary fields.
            self.assertEqual(actual.range_id, baseline.range_id)
            self.assertEqual(aggregate_trade_range_manifest_bytes(actual),
                             aggregate_trade_range_manifest_bytes(baseline))
        self.assertEqual(compose_aggregate_trade_range(LoadOnlyCatalog(catalog), request), baseline)

    def test_disjoint_intervals_need_not_increase_in_time_or_be_contiguous(self):
        catalog, request = self.setup_catalog(((100, 90), (20, 10)))
        expected = compose_aggregate_trade_range(StreamOnlyCatalog(catalog), request)
        with patch.object(catalog, "stream_range", side_effect=AssertionError("replay")):
            actual = compose_aggregate_trade_range(catalog, request)
        self.assertEqual(actual, expected)
        self.assertEqual(actual.boundaries[0].observed_aggregate_id_delta, -70)
        self.assertFalse(actual.boundaries[0].numerical_continuity_asserted)

    def test_overlap_with_unique_ids_falls_back_and_accepts(self):
        catalog, request = self.setup_catalog(((10, 100), (50, 150)))
        expected = compose_aggregate_trade_range(StreamOnlyCatalog(catalog), request)
        with patch.object(catalog, "stream_range", wraps=catalog.stream_range) as stream:
            self.assertEqual(compose_aggregate_trade_range(catalog, request), expected)
        stream.assert_called_once()

    def test_nonadjacent_overlap_uses_exact_range_wide_fallback(self):
        catalog, request = self.setup_catalog(((10, 100), (200, 250), (50, 150)))
        expected = compose_aggregate_trade_range(StreamOnlyCatalog(catalog), request)
        with patch.object(catalog, "stream_range", wraps=catalog.stream_range) as stream:
            self.assertEqual(compose_aggregate_trade_range(catalog, request), expected)
        stream.assert_called_once()

    def test_repeated_cross_day_id_and_changed_price_preserve_conflict_errors(self):
        for different_price in (False, True):
            with self.subTest(different_price=different_price):
                # Both repeats are conflicts canonically: distinct valid UTC
                # source days necessarily have different event timestamps.
                with TemporaryDirectory() as directory:
                    self.root = Path(directory)
                    self.publish(0, (10, 100))
                    self.publish(1, (100, 150), price="123.00" if different_price else None)
                    catalog = local_catalog(self.root)
                    catalog.rebuild()
                    request = AggregateTradeRangeRequest(
                        symbol="BTCUSDT", start_date=START,
                        end_date_exclusive=START + timedelta(days=2),
                        selection_policy=RevisionSelectionPolicy.UNIQUE,
                    )
                    with self.assertRaises(AggregateTradeRangeCompositionError) as baseline:
                        compose_aggregate_trade_range(StreamOnlyCatalog(catalog), request)
                    with patch.object(catalog, "stream_range", wraps=catalog.stream_range) as stream:
                        with self.assertRaises(AggregateTradeRangeCompositionError) as actual:
                            compose_aggregate_trade_range(catalog, request)
                    stream.assert_called_once()
                    self.assertEqual(str(actual.exception), str(baseline.exception))
                    self.assertIn("conflicting", str(actual.exception))

    def test_identical_canonical_duplicate_cannot_receive_fast_report(self):
        catalog, _ = self.setup_catalog()
        manifest = catalog.view.entries[0].manifest
        selected = (manifest, manifest)
        self.assertIsNone(catalog.verified_range_report(selected))
        with self.assertRaisesRegex(AggregateTradeCatalogError, "duplicate"):
            list(catalog.stream_range(selected, batch_size=1))

    def test_same_size_and_mtime_corruption_does_not_use_cached_evidence(self):
        catalog, request = self.setup_catalog()
        path = catalog.view.entries[0].canonical_parquet_path
        original_stat = path.stat()
        content = bytearray(path.read_bytes())
        content[-1] ^= 1  # Invalid Parquet footer, same byte count.
        path.write_bytes(content)
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        self.assertEqual(path.stat().st_size, original_stat.st_size)
        self.assertEqual(path.stat().st_mtime_ns, original_stat.st_mtime_ns)
        with self.assertRaises(AggregateTradeRangeCompositionError) as baseline:
            compose_aggregate_trade_range(StreamOnlyCatalog(catalog), request)
        with patch.object(catalog, "stream_range", wraps=catalog.stream_range) as stream:
            with self.assertRaises(AggregateTradeRangeCompositionError) as actual:
                compose_aggregate_trade_range(catalog, request)
        stream.assert_called_once()
        self.assertEqual(str(actual.exception), str(baseline.exception))

    def test_valid_parquet_reencoding_falls_back_and_keeps_identity(self):
        catalog, request = self.setup_catalog()
        expected = compose_aggregate_trade_range(StreamOnlyCatalog(catalog), request)
        path = catalog.view.entries[0].canonical_parquet_path
        before = catalog_module._parquet_byte_sha256(path)
        with pq.ParquetFile(path) as parquet:
            table = parquet.read()
        pq.write_table(table, path, compression="NONE", row_group_size=1)
        self.assertNotEqual(catalog_module._parquet_byte_sha256(path), before)
        with patch.object(catalog, "stream_range", wraps=catalog.stream_range) as stream:
            self.assertEqual(compose_aggregate_trade_range(catalog, request), expected)
        stream.assert_called_once()
        self.assertEqual(compose_aggregate_trade_range(StreamOnlyCatalog(catalog), request), expected)

    def test_raw_corruption_retains_exact_error(self):
        catalog, request = self.setup_catalog()
        path = catalog.view.entries[0].raw_archive_path
        path.write_bytes(b"corrupt synthetic ZIP")
        with self.assertRaises(AggregateTradeRangeCompositionError) as baseline:
            compose_aggregate_trade_range(StreamOnlyCatalog(catalog), request)
        with patch.object(catalog, "stream_range", wraps=catalog.stream_range) as stream:
            with self.assertRaises(AggregateTradeRangeCompositionError) as actual:
                compose_aggregate_trade_range(catalog, request)
        stream.assert_called_once()
        self.assertEqual(str(actual.exception), str(baseline.exception))

    def test_missing_evidence_and_missing_file_fall_back(self):
        catalog, request = self.setup_catalog()
        expected = compose_aggregate_trade_range(StreamOnlyCatalog(catalog), request)
        catalog._verified_partitions.clear()
        with patch.object(catalog, "stream_range", wraps=catalog.stream_range) as stream:
            self.assertEqual(compose_aggregate_trade_range(catalog, request), expected)
        stream.assert_called_once()
        catalog.rebuild()
        catalog.view.entries[0].canonical_parquet_path.unlink()
        with patch.object(catalog, "stream_range", wraps=catalog.stream_range) as stream:
            with self.assertRaises(AggregateTradeRangeCompositionError):
                compose_aggregate_trade_range(catalog, request)
        stream.assert_called_once()

    def test_rebuild_rejects_byte_changes_during_verification_and_clears_evidence(self):
        catalog, _ = self.setup_catalog()
        # O3 may reuse persisted certificates; force the cold semantic path
        # so this still tests mutation across the complete verification stream.
        for entry in catalog.view.entries:
            catalog._certificates.path_for(entry.manifest).unlink()
        real_hash = catalog_module._parquet_byte_sha256
        calls = 0

        def changed_hash(path):
            nonlocal calls
            calls += 1
            return real_hash(path) if calls == 1 else "0" * 64

        with patch.object(catalog_module, "_parquet_byte_sha256", side_effect=changed_hash):
            with self.assertRaisesRegex(AggregateTradeCatalogError, "changed during"):
                catalog.rebuild()
        self.assertEqual(catalog.view.entries, ())
        self.assertEqual(catalog._verified_partitions, {})

    def test_unique_exact_and_invalid_batch_behavior_is_unchanged(self):
        catalog, request = self.setup_catalog()
        chosen = tuple(exact(entry.manifest) for entry in catalog.view.entries)
        self.publish(0, (20, 30))
        catalog.rebuild()
        for selected_catalog in (catalog, StreamOnlyCatalog(catalog)):
            with self.assertRaisesRegex(AggregateTradeRangeCompositionError, "ambiguous"):
                compose_aggregate_trade_range(selected_catalog, request)
        pinned = replace(request, selection_policy=RevisionSelectionPolicy.EXACT,
                         exact_revisions=chosen)
        self.assertEqual(compose_aggregate_trade_range(catalog, pinned),
                         compose_aggregate_trade_range(StreamOnlyCatalog(catalog), pinned))
        missing = replace(pinned, exact_revisions=(replace(chosen[0], manifest_id="0" * 64), chosen[1]))
        with self.assertRaisesRegex(AggregateTradeRangeCompositionError, "exact revision does not exist"):
            compose_aggregate_trade_range(catalog, missing)
        for size in (0, True, 1.5, 1_000_001):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "batch_size"):
                compose_aggregate_trade_range(catalog, pinned, batch_size=size)


if __name__ == "__main__":
    unittest.main()

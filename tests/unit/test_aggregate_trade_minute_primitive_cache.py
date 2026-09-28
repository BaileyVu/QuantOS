"""O2 exact parity, persisted reuse and adversarial derived-cache verification."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from quantos.application import (
    AggregateTradeMinuteAggregationError,
    AggregateTradeStreamingDiagnostics,
    aggregate_trade_minute_states,
    compose_aggregate_trade_range,
    replay_aggregate_trade_minute_states,
)
from quantos.application.aggregate_trade_minute_primitive_cache import DailyAggregateTradeMinutePrimitives
from quantos.domain.market_data.research_events import (
    AggregateTradeMinuteDatasetManifest,
    AggregateTradeRangeRequest,
    RevisionSelectionPolicy,
    SourceTimestampUnit,
    aggregate_trade_minute_dataset_manifest_bytes,
)
from quantos.infrastructure.storage.aggregate_trade_minute_primitive_cache import (
    AggregateTradePrimitiveCacheError,
    CACHE_SCHEMA_VERSION,
    ParquetAggregateTradeMinutePrimitiveCache,
    PRIMITIVE_CACHE_SCHEMA,
)
from tests.aggregate_trade_range_fixtures import (
    local_catalog, partition_rows, publish_partition, publish_rows,
)
from tests.unit.test_aggregate_trade_verified_range import StreamOnlyCatalog


DECEMBER = date(2024, 12, 31)
JANUARY = date(2025, 1, 1)
END = date(2025, 1, 2)
META_KEY = b"quantos.daily_minute_primitive_cache"


class MinutePrimitiveCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="quantos-o2-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sources = []
        for day, first_id in ((DECEMBER, 10), (JANUARY, 20)):
            fetched, _ = publish_partition(self.root, symbol="BTCUSDT", source_date=day, first_id=first_id)
            self.sources.append(fetched.archive.manifest)
        self.catalog = local_catalog(self.root)
        self.catalog.rebuild()
        self.range = self.compose(DECEMBER, END)
        self.cache = ParquetAggregateTradeMinutePrimitiveCache(self.root)

    def compose(self, start, end):
        return compose_aggregate_trade_range(self.catalog, AggregateTradeRangeRequest(
            symbol="BTCUSDT", start_date=start, end_date_exclusive=end,
            selection_policy=RevisionSelectionPolicy.UNIQUE,
        ))

    def run_cached(self, source_range=None, **kwargs):
        return aggregate_trade_minute_states(self.catalog, source_range or self.range,
                                            primitive_cache=self.cache, **kwargs)

    def assertParity(self, left, right):
        self.assertEqual(left, right)
        self.assertEqual(left.identity, right.identity)
        self.assertEqual(left.states, right.states)
        self.assertEqual(left.dataset_id, right.dataset_id)
        self.assertEqual(left.content_sha256, right.content_sha256)
        self.assertEqual(left.input_event_count, right.input_event_count)
        self.assertEqual(left.zero_event_minute_count, right.zero_event_minute_count)
        lm = AggregateTradeMinuteDatasetManifest.from_dataset(left)
        rm = AggregateTradeMinuteDatasetManifest.from_dataset(right)
        self.assertEqual(lm, rm)
        self.assertEqual(aggregate_trade_minute_dataset_manifest_bytes(lm),
                         aggregate_trade_minute_dataset_manifest_bytes(rm))

    def test_cold_warm_persisted_parity_and_zero_raw_consumption(self):
        baseline = aggregate_trade_minute_states(self.catalog, self.range)
        cold_diagnostics = AggregateTradeStreamingDiagnostics()
        cold = self.run_cached(diagnostics=cold_diagnostics, batch_size=1)
        self.assertParity(cold, baseline)
        self.assertEqual(cold_diagnostics.raw_events_consumed, 4)
        self.assertEqual(cold_diagnostics.cache_miss_partition_count, 2)
        self.assertEqual(cold_diagnostics.cache_built_partition_count, 2)
        # Fresh adapter and rebuilt catalog prove persistence, not an in-memory memo.
        self.cache = ParquetAggregateTradeMinutePrimitiveCache(self.root)
        self.catalog = local_catalog(self.root)
        self.catalog.rebuild()
        diagnostics = AggregateTradeStreamingDiagnostics()
        with patch.object(self.catalog, "stream_range", side_effect=AssertionError("raw replay")), \
             patch.object(self.catalog._store, "iter_event_batches", side_effect=AssertionError("decode")):
            warm = self.run_cached(diagnostics=diagnostics, batch_size=64)
        self.assertParity(warm, baseline)
        self.assertEqual(diagnostics.raw_events_consumed, 0)
        self.assertEqual(diagnostics.cache_hit_partition_count, 2)
        self.assertEqual(diagnostics.cache_miss_partition_count, 0)
        self.assertEqual(diagnostics.cache_built_partition_count, 0)
        self.assertEqual(diagnostics.cached_minute_rows_loaded, 2880)
        self.assertTrue(diagnostics.warm_cache_used)

    def test_cross_range_partial_reuse_keeps_artifact_and_injects_current_range(self):
        one_range = self.compose(DECEMBER, JANUARY)
        one = self.run_cached(one_range)
        self.assertParity(one, aggregate_trade_minute_states(self.catalog, one_range))
        path = self.cache.path_for(self.sources[0])
        before = path.read_bytes()
        diagnostics = AggregateTradeStreamingDiagnostics()
        with patch.object(self.catalog, "stream_range", wraps=self.catalog.stream_range) as stream:
            two = self.run_cached(diagnostics=diagnostics)
        self.assertEqual(stream.call_count, 1)
        self.assertEqual(stream.call_args.args[0], (self.sources[1],))
        self.assertEqual(diagnostics.raw_events_consumed, 2)
        self.assertEqual(diagnostics.cache_hit_partition_count, 1)
        self.assertEqual(diagnostics.cache_built_partition_count, 1)
        self.assertEqual(diagnostics.cached_minute_rows_loaded, 1440)
        self.assertFalse(diagnostics.warm_cache_used)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path, self.cache.path_for(self.sources[0]))
        self.assertNotEqual(one_range.range_id, self.range.range_id)
        self.assertTrue(all(row.source_range_id == one_range.range_id for row in one.states))
        self.assertTrue(all(row.source_range_id == self.range.range_id for row in two.states))
        self.assertParity(two, aggregate_trade_minute_states(self.catalog, self.range))

    def test_timestamp_units_zeros_and_exact_decimal_coefficients(self):
        with localcontext() as context:
            context.prec = 6
            baseline = aggregate_trade_minute_states(self.catalog, self.range)
            self.run_cached()
            warm = self.run_cached()
        self.assertParity(warm, baseline)
        self.assertIs(warm.states[0].source_timestamp_unit, SourceTimestampUnit.MILLISECOND)
        self.assertIs(warm.states[1440].source_timestamp_unit, SourceTimestampUnit.MICROSECOND)
        self.assertEqual(warm.zero_event_minute_count, 2878)
        self.assertIsNone(warm.states[1].first_aggregate_trade_id)
        self.assertIsNone(warm.states[1].last_event_time)
        self.assertEqual(warm.states[1].total_quote_notional, Decimal(0))
        for left, right in zip(warm.states, baseline.states, strict=True):
            for field in ("total_base_quantity", "total_quote_notional",
                          "aggressive_buy_base_quantity", "aggressive_sell_base_quantity",
                          "aggressive_buy_quote_notional", "aggressive_sell_quote_notional"):
                self.assertEqual(getattr(left, field).as_tuple(), getattr(right, field).as_tuple())

    def test_nonadjacent_misses_stream_contiguous_runs_without_replaying_middle_hit(self):
        rows = partition_rows(END, first_id=30)
        for row in rows:
            row[5] = str(int(row[5]) + 86_400_000_000)
        publish_rows(self.root, symbol="BTCUSDT", source_date=END, rows=rows)
        self.catalog.rebuild()
        self.run_cached(self.compose(JANUARY, END))
        source_range = self.compose(DECEMBER, date(2025, 1, 3))
        diagnostics = AggregateTradeStreamingDiagnostics()
        with patch.object(self.catalog, "stream_range", wraps=self.catalog.stream_range) as stream:
            actual = self.run_cached(source_range, diagnostics=diagnostics)
        self.assertEqual(stream.call_count, 2)
        self.assertEqual([call.args[0][0].source_date for call in stream.call_args_list], [DECEMBER, END])
        self.assertEqual(diagnostics.raw_events_consumed, 4)
        self.assertEqual(diagnostics.cache_hit_partition_count, 1)
        self.assertEqual(diagnostics.cache_built_partition_count, 2)
        self.assertParity(actual, aggregate_trade_minute_states(self.catalog, source_range))

    def test_no_or_invalid_cache_and_catalog_without_proof_preserve_replay(self):
        baseline = aggregate_trade_minute_states(self.catalog, self.range)
        for cache in (None, object(), type("Invalid", (), {"load": None, "write": None})()):
            diagnostics = AggregateTradeStreamingDiagnostics()
            actual = aggregate_trade_minute_states(self.catalog, self.range,
                                                  primitive_cache=cache, diagnostics=diagnostics)
            self.assertEqual(actual, baseline)
            self.assertEqual(diagnostics.raw_events_consumed, 4)
        self.run_cached()
        diagnostics = AggregateTradeStreamingDiagnostics()
        actual = aggregate_trade_minute_states(StreamOnlyCatalog(self.catalog), self.range,
                                              primitive_cache=self.cache, diagnostics=diagnostics)
        self.assertEqual(actual, baseline)
        self.assertEqual(diagnostics.raw_events_consumed, 4)
        self.assertEqual(diagnostics.cache_hit_partition_count, 0)

    def test_existing_replay_verifies_cached_final_dataset(self):
        expected = self.run_cached()
        self.assertEqual(replay_aggregate_trade_minute_states(self.catalog, self.range, expected), expected)

    def test_storage_layout_schema_no_range_id_idempotence_and_deterministic_bytes(self):
        self.run_cached()
        path = self.cache.path_for(self.sources[0])
        self.assertIn(CACHE_SCHEMA_VERSION, path.parts)
        self.assertIn("2024-12-31", path.parts)
        before = path.read_bytes()
        mtime = path.stat().st_mtime_ns
        daily = self.cache.load(self.sources[0])
        self.cache.write(daily)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, mtime)
        with TemporaryDirectory() as directory:
            other = ParquetAggregateTradeMinutePrimitiveCache(Path(directory))
            other.write(daily)
            self.assertEqual(other.path_for(self.sources[0]).read_bytes(), before)
        with pq.ParquetFile(path) as parquet:
            self.assertTrue(parquet.schema_arrow.equals(PRIMITIVE_CACHE_SCHEMA, check_metadata=False))
            self.assertEqual(parquet.metadata.num_rows, 1440)
            payload = parquet.metadata.metadata[META_KEY]
            self.assertNotIn(b"source_range_id", payload)
            self.assertNotIn("source_range_id", parquet.schema_arrow.names)

    def rewrite(self, path, transform):
        with pq.ParquetFile(path) as parquet:
            table = parquet.read()
        pq.write_table(transform(table), path, write_page_checksum=True)

    def test_content_mutation_is_not_accepted(self):
        self.run_cached()
        path = self.cache.path_for(self.sources[0])
        def mutate(table):
            name = "total_base_quantity"
            values = table[name].to_pylist()
            values[0] = "999"
            index = table.schema.get_field_index(name)
            return table.set_column(index, table.schema.field(index), pa.array(values, type=pa.string()))
        self.rewrite(path, mutate)
        with self.assertRaisesRegex(AggregateTradeMinuteAggregationError, "cache content"):
            self.run_cached()

    def test_manifest_and_every_lineage_field_mutation_fail_closed(self):
        self.run_cached()
        path = self.cache.path_for(self.sources[0])
        original = path.read_bytes()
        for field, value in (
            ("source_manifest_id", "0" * 64), ("source_revision_id", "0" * 64),
            ("source_dataset_id", "0" * 64), ("source_timestamp_unit", "microsecond"),
            ("aggregation_version", "other"), ("cache_schema_version", "other"),
            ("state_schema_version", "other"), ("canonical_sequence_sha256", "0" * 64),
            ("source_date", "2025-01-01"), ("symbol", "ETHUSDT"),
        ):
            with self.subTest(field=field):
                path.write_bytes(original)
                def mutate(table):
                    metadata = dict(table.schema.metadata)
                    parsed = json.loads(metadata[META_KEY])
                    parsed["lineage"][field] = value
                    metadata[META_KEY] = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode()
                    return table.replace_schema_metadata(metadata)
                self.rewrite(path, mutate)
                with self.assertRaises(AggregateTradeMinuteAggregationError):
                    self.run_cached()
        path.write_bytes(original)
        self.rewrite(path, lambda table: table.replace_schema_metadata({META_KEY: b"{}"}))
        with self.assertRaises(AggregateTradeMinuteAggregationError):
            self.run_cached()

    def test_wrong_source_artifact_cannot_be_reused(self):
        self.run_cached()
        self.cache.path_for(self.sources[1]).write_bytes(self.cache.path_for(self.sources[0]).read_bytes())
        with self.assertRaisesRegex(AggregateTradeMinuteAggregationError, "lineage"):
            self.run_cached()

    def test_canonical_mutation_cannot_be_hidden_by_warm_cache(self):
        self.run_cached()
        entry = self.catalog.view.entries[0]
        original = entry.canonical_parquet_path.read_bytes()
        original_stat = entry.canonical_parquet_path.stat()
        broken = bytearray(original)
        broken[-1] ^= 1
        entry.canonical_parquet_path.write_bytes(broken)
        import os
        os.utime(entry.canonical_parquet_path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        with self.assertRaises(AggregateTradeMinuteAggregationError):
            self.run_cached()
        entry.canonical_parquet_path.write_bytes(original)
        self.rewrite(entry.canonical_parquet_path, lambda table: table)
        diagnostics = AggregateTradeStreamingDiagnostics()
        actual = self.run_cached(diagnostics=diagnostics)
        self.assertEqual(diagnostics.raw_events_consumed, 4)
        self.assertEqual(actual, aggregate_trade_minute_states(self.catalog, self.range))

    def test_raw_zip_mutation_cannot_be_hidden_by_warm_cache(self):
        self.run_cached()
        self.catalog.view.entries[0].raw_archive_path.write_bytes(b"corrupt")
        with self.assertRaisesRegex(AggregateTradeMinuteAggregationError, "raw archive"):
            self.run_cached()

    def test_malformed_cache_results_rejected_and_invalid_batch_rejected(self):
        with patch.object(self.cache, "load", return_value=object()):
            with self.assertRaisesRegex(AggregateTradeMinuteAggregationError, "invalid source lineage"):
                self.run_cached()
        with self.assertRaises(ValueError):
            self.run_cached(batch_size=0)

    def test_failed_atomic_publication_leaves_no_cache_or_temporary(self):
        self.run_cached()
        daily = self.cache.load(self.sources[0])
        path = self.cache.path_for(self.sources[0])
        path.unlink()
        with patch("quantos.infrastructure.storage.aggregate_trade_minute_primitive_cache.os.link",
                   side_effect=OSError("injected publication failure")):
            with self.assertRaisesRegex(AggregateTradePrimitiveCacheError, "publication failure"):
                self.cache.write(daily)
        self.assertFalse(path.exists())
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_collision_does_not_overwrite_existing_cache(self):
        self.run_cached()
        daily = self.cache.load(self.sources[0])
        path = self.cache.path_for(self.sources[0])
        original = path.read_bytes()
        row = replace(daily.rows[0], total_base_quantity=Decimal(3),
                      aggressive_buy_base_quantity=Decimal(1), aggressive_sell_base_quantity=Decimal(2))
        changed = DailyAggregateTradeMinutePrimitives(daily.source, (row, *daily.rows[1:]))
        with self.assertRaisesRegex(AggregateTradePrimitiveCacheError, "collision"):
            self.cache.write(changed)
        self.assertEqual(path.read_bytes(), original)

    def test_missing_rows_wrong_schema_and_noncanonical_metadata_are_rejected(self):
        self.run_cached()
        path = self.cache.path_for(self.sources[0])
        original = path.read_bytes()
        def noncanonical_metadata(table):
            metadata = dict(table.schema.metadata)
            metadata[META_KEY] = json.dumps(json.loads(metadata[META_KEY]), indent=2).encode()
            return table.replace_schema_metadata(metadata)
        def noncanonical_integer(table):
            metadata = dict(table.schema.metadata)
            parsed = json.loads(metadata[META_KEY])
            parsed["row_count"] = 1440.0
            metadata[META_KEY] = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode()
            return table.replace_schema_metadata(metadata)
        for transform in (lambda table: table.slice(1),
                          lambda table: table.drop(["total_quote_notional"]),
                          noncanonical_metadata, noncanonical_integer):
            path.write_bytes(original)
            self.rewrite(path, transform)
            with self.assertRaises(AggregateTradePrimitiveCacheError):
                self.cache.load(self.sources[0])

    def test_path_escape_invalid_storage_input_and_empty_cache_are_rejected(self):
        self.assertIsNone(self.cache.load(self.sources[0]))
        with self.assertRaises(AggregateTradePrimitiveCacheError):
            self.cache.write(object())
        with self.assertRaises(AggregateTradePrimitiveCacheError):
            self.cache.path_for(object())
        with patch.object(Path, "resolve", return_value=self.root.parent):
            with self.assertRaisesRegex(AggregateTradePrimitiveCacheError, "escapes"):
                self.cache.path_for(self.sources[0])

    def test_late_source_failure_publishes_no_partial_cache(self):
        self.catalog.view.entries[1].canonical_parquet_path.write_bytes(b"corrupt parquet")
        with self.assertRaises(AggregateTradeMinuteAggregationError):
            self.run_cached()
        self.assertFalse(self.cache.path_for(self.sources[0]).exists())
        self.assertFalse(self.cache.path_for(self.sources[1]).exists())


if __name__ == "__main__":
    unittest.main()

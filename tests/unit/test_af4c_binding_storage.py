"""Temporary synthetic end-to-end binding, integrity and isolation tests."""

from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
import inspect
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import traceback
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from quantos.application import af4c_development_binding as contract_module
from quantos.application.af4c_development_binding import BindingError, SYMBOLS
from quantos.application.aggregate_trade_minute_states import AggregateTradeStreamingDiagnostics, aggregate_trade_minute_states
from quantos.application.aggregate_trade_ranges import compose_aggregate_trade_range
from quantos.domain.market_data.research_events import AggregateTradeRangeRequest, ExactAggregateTradeRevision, RevisionSelectionPolicy, aggregate_trade_archive_manifest_id
from quantos.infrastructure.storage import af4c_binding as storage_module
from quantos.infrastructure.storage.af4c_binding import DevelopmentBindingStore, _preflight, preflight_development
from quantos.infrastructure.storage.aggregate_trade_catalog import LocalAggregateTradeArchiveCatalog
from quantos.infrastructure.storage.aggregate_trade_minute_primitive_cache import ParquetAggregateTradeMinutePrimitiveCache
from quantos.infrastructure.storage.aggregate_trade_parquet import ParquetAggregateTradeArchiveStore
from quantos.infrastructure.storage.aggregate_trade_verification_certificate import AggregateTradeVerificationCertificateStore
from quantos.infrastructure.storage.parquet import ParquetCandleDatasetStore
from tests.aggregate_trade_range_fixtures import local_catalog, publish_partition
from tests.unit.test_af4c_development_binding import MINI, fixture, nested_keys, FORBIDDEN_KEYS


class BindingStorageTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory(prefix="af4c-binding-storage-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.paths, self.source, self.cache, self.sources = fixture(self.root)

    def run_binding(self, exact=None):
        return _preflight(self.paths, self.source, self.cache, exact, MINI)

    def test_cold_warm_invariance_and_no_evaluator_or_network(self):
        stdout, stderr = StringIO(), StringIO()
        with patch("quantos.domain.evaluation.af4c.evaluate_synthetic", side_effect=AssertionError("evaluator called")) as evaluator, \
             patch("socket.socket", side_effect=AssertionError("network called")), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            cold, cold_receipt = self.run_binding()
            warm, warm_receipt = self.run_binding()
        evaluator.assert_not_called()
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(cold.content, warm.content)
        self.assertEqual(cold.binding_id, warm.binding_id)
        self.assertNotEqual(cold_receipt, warm_receipt)
        first, second = json.loads(cold_receipt), json.loads(warm_receipt)
        self.assertEqual(first["catalog"]["certificate_built_partition_count"], 4)
        self.assertEqual(second["catalog"]["certificate_hit_partition_count"], 4)
        self.assertEqual(second["catalog"]["canonical_events_replayed"], 0)
        for item in second["symbols"]:
            self.assertTrue(item["warm_cache_used"])
            self.assertEqual(item["raw_events_consumed"], 0)
            self.assertEqual(item["cache_hit_partition_count"], 2)
        for payload in (cold.content, cold_receipt, warm_receipt):
            self.assertFalse(set(nested_keys(json.loads(payload))) & FORBIDDEN_KEYS)
        document = json.loads(cold.content)
        for item in document["de1_range_bindings"]:
            self.assertEqual([p["source_timestamp_unit"] for p in item["manifest"]["partitions"]],
                             ["millisecond", "microsecond"])
        for item in document["de1_minute_bindings"]:
            self.assertEqual([p["source_timestamp_unit"] for p in item["identity"]["source_partitions"]],
                             ["millisecond", "microsecond"])

    def test_o1_fallback_preserves_scientific_identity(self):
        accelerated, _ = self.run_binding()
        with patch.object(LocalAggregateTradeArchiveCatalog, "verified_range_report", return_value=None):
            replayed, receipt = self.run_binding()
        self.assertEqual(accelerated.content, replayed.content)
        self.assertTrue(all(not item["o1_verified_range_available"] for item in json.loads(receipt)["symbols"]))

    def test_missing_day_rejected(self):
        store = ParquetAggregateTradeArchiveStore(self.source)
        store.dataset_path(self.sources[0][1]).unlink()
        with self.assertRaises(BindingError):
            self.run_binding()

    def test_ambiguous_unique_and_predeclared_exact_selection(self):
        publish_partition(self.source, symbol="BTCUSDT", source_date=MINI.start.date(), first_id=10, stored=True)
        with self.assertRaises(BindingError):
            self.run_binding()
        exact = tuple(tuple(ExactAggregateTradeRevision(m.source_date, aggregate_trade_archive_manifest_id(m), m.source_revision_id)
                            for m in items) for items in self.sources)
        first, _ = self.run_binding(exact)
        second, _ = self.run_binding(exact)
        self.assertEqual(first.content, second.content)
        for item in json.loads(first.content)["de1_range_bindings"]:
            self.assertEqual(item["manifest"]["selection_policy"], "exact")

    def test_range_independent_primitive_current_range_identity(self):
        catalog = local_catalog(self.source)
        catalog.rebuild()
        def compose(end):
            return compose_aggregate_trade_range(catalog, AggregateTradeRangeRequest(
                "BTCUSDT", MINI.start.date(), end, RevisionSelectionPolicy.UNIQUE))
        small_range = compose(self.sources[0][1].source_date)
        small = aggregate_trade_minute_states(catalog, small_range, primitive_cache=self.cache)
        path = self.cache.path_for(self.sources[0][0])
        before = path.read_bytes()
        diagnostics = AggregateTradeStreamingDiagnostics()
        full_range = compose(MINI.end.date())
        full = aggregate_trade_minute_states(catalog, full_range, primitive_cache=self.cache, diagnostics=diagnostics)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(diagnostics.cache_hit_partition_count, 1)
        self.assertNotEqual(small.dataset_id, full.dataset_id)
        self.assertEqual(small.identity.source_range_id, small_range.range_id)
        self.assertEqual(full.identity.source_range_id, full_range.range_id)
        binding, _ = self.run_binding()
        self.assertEqual(json.loads(binding.content)["de1_minute_bindings"][0]["minute_state_dataset_id"], full.dataset_id)

    def test_cache_is_independent_of_source_root(self):
        self.run_binding()
        for items in self.sources:
            for source in items:
                self.assertTrue(self.cache.path_for(source).is_file())
                self.assertFalse(self.cache.path_for(source).is_relative_to(self.source))

    def test_corrupt_candle_parquet(self):
        self.paths[0].write_bytes(b"invalid parquet")
        with self.assertRaises(BindingError):
            self.run_binding()

    def test_candle_metadata_identity_mutation(self):
        table = pq.read_table(self.paths[0])
        metadata = dict(table.schema.metadata)
        metadata[b"quantos.dataset_id"] = b"0" * 64
        pq.write_table(table.replace_schema_metadata(metadata), self.paths[0])
        with self.assertRaises(BindingError):
            self.run_binding()

    def test_same_size_same_mtime_candle_mutation_is_not_trusted(self):
        first, _ = self.run_binding()
        path = self.paths[0]
        info = path.stat()
        content = bytearray(path.read_bytes())
        content[0] ^= 1
        path.write_bytes(content)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        mutated_info = path.stat()
        self.assertEqual(mutated_info.st_size, info.st_size)
        self.assertEqual(mutated_info.st_mtime_ns, info.st_mtime_ns)
        try:
            second, _ = self.run_binding()
        except BindingError:
            # Canonical rejection is valid, but leading-magic validation varies.
            return
        before = json.loads(first.content)["candle_bindings"][0]
        after = json.loads(second.content)["candle_bindings"][0]
        self.assertNotEqual(before["parquet_byte_sha256"], after["parquet_byte_sha256"])
        self.assertNotEqual(first.binding_id, second.binding_id)
        with self.assertRaises(BindingError):
            _preflight(self.paths, self.source, self.cache, None, MINI, expected_binding=first)

    def test_valid_alternate_candle_encoding_preserves_logical_identity(self):
        first, _ = self.run_binding()
        path = self.paths[0]
        # A different physical encoding remains valid under the canonical store.
        table = pq.read_table(path)
        pq.write_table(table, path, compression="NONE", use_dictionary=False,
                       write_page_checksum=True, row_group_size=97)
        second, _ = self.run_binding()
        before = json.loads(first.content)["candle_bindings"][0]
        after = json.loads(second.content)["candle_bindings"][0]
        for key in ("dataset_id", "content_sha256", "identity_sha256"):
            self.assertEqual(before[key], after[key])
        self.assertNotEqual(before["parquet_byte_sha256"], after["parquet_byte_sha256"])
        # The full evidence document includes physical SHA, so its ID changes.
        self.assertNotEqual(first.binding_id, second.binding_id)
        with self.assertRaises(BindingError):
            _preflight(self.paths, self.source, self.cache, None, MINI, first)

    def test_valid_logical_candle_mutation_cannot_replay_frozen_binding(self):
        binding, _ = self.run_binding()
        table = pq.read_table(self.paths[0])
        index = table.schema.get_field_index("close")
        field = table.schema.field(index)
        values = table.column(index).to_pylist()
        values[0] = Decimal("101")
        table = table.set_column(index, field, pa.array(values, type=field.type))
        pq.write_table(table, self.paths[0], write_page_checksum=True)
        with self.assertRaises(BindingError):
            _preflight(self.paths, self.source, self.cache, None, MINI, binding)

    def test_raw_zip_mutation(self):
        self.run_binding()
        path = ParquetAggregateTradeArchiveStore(self.source).raw_archive_path(self.sources[0][0])
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        with self.assertRaises(BindingError):
            self.run_binding()

    def test_de1_canonical_same_size_same_mtime_mutation(self):
        self.run_binding()
        path = ParquetAggregateTradeArchiveStore(self.source).dataset_path(self.sources[0][0])
        info = path.stat()
        data = bytearray(path.read_bytes())
        data[0] ^= 1
        path.write_bytes(data)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.assertRaises(BindingError):
            self.run_binding()

    def test_corrupt_certificate_rejected_without_automatic_repair(self):
        self.run_binding()
        path = AggregateTradeVerificationCertificateStore(self.source).path_for(self.sources[0][0])
        path.write_bytes(b"corrupt certificate")
        with self.assertRaises(BindingError):
            self.run_binding()
        self.assertEqual(path.read_bytes(), b"corrupt certificate")

    def test_corrupt_cache_rejected(self):
        self.run_binding()
        self.cache.path_for(self.sources[0][0]).write_bytes(b"corrupt primitive")
        with self.assertRaises(BindingError):
            self.run_binding()

    def test_exception_trace_does_not_expose_lower_level_values(self):
        with patch.object(ParquetCandleDatasetStore, "read", side_effect=ValueError("OBSERVATION_SECRET")):
            try:
                self.run_binding()
            except BindingError as error:
                self.assertNotIn("OBSERVATION_SECRET", "".join(traceback.format_exception(error)))
            else:
                self.fail("expected blinded rejection")

    def test_atomic_idempotent_publication_and_collision(self):
        binding, _ = self.run_binding()
        store = DevelopmentBindingStore(self.root / "artifacts")
        with patch.object(storage_module.os, "fsync", wraps=os.fsync) as sync:
            path = store._write(binding, MINI)
        self.assertGreaterEqual(sync.call_count, 1)
        self.assertEqual(path.name, f"{binding.binding_id}.json")
        self.assertEqual(path.read_bytes(), binding.content)
        self.assertEqual(store._write(binding, MINI), path)
        self.assertEqual(store._read(binding.binding_id, MINI), binding)
        path.write_bytes(b"collision")
        with self.assertRaises(BindingError):
            store._write(binding, MINI)
        self.assertEqual(path.read_bytes(), b"collision")
        self.assertEqual(list(path.parent.glob(".binding-*.tmp")), [])

    def test_atomic_link_failure_leaves_no_partial_manifest(self):
        binding, _ = self.run_binding()
        store = DevelopmentBindingStore(self.root / "artifacts")
        with patch.object(storage_module.os, "link", side_effect=OSError("unsupported")), self.assertRaises(BindingError):
            store._write(binding, MINI)
        self.assertFalse(store._path(binding.binding_id).exists())
        self.assertEqual(list((self.root / "artifacts").rglob("*.tmp")), [])

    def test_public_storage_and_preflight_cannot_accept_miniature(self):
        binding, _ = self.run_binding()
        store = DevelopmentBindingStore(self.root / "artifacts")
        with self.assertRaises(BindingError):
            store.write(binding)
        with self.assertRaises(BindingError):
            preflight_development(candle_paths=self.paths, source_root=self.source, primitive_cache=self.cache)

    def test_traversal_and_unc_rejected_before_io(self):
        store = DevelopmentBindingStore(self.root / "artifacts")
        for identity in ("../escape", "A" * 64, "0" * 63, "0" * 64 + "/child"):
            with self.assertRaises(BindingError):
                store.read(identity)
        with self.assertRaises(BindingError):
            DevelopmentBindingStore(self.root / ".." / "escape")
        with self.assertRaises(BindingError):
            DevelopmentBindingStore(Path("//invalid-server/share"))

    def test_symlink_escape_rejected(self):
        outside = self.root / "other-temporary-root"
        outside.mkdir()
        link = self.root / "redirect"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("creating symlinks requires unavailable OS privileges")
        with self.assertRaises(BindingError):
            DevelopmentBindingStore(link)

    def test_no_direct_network_evaluator_or_infrastructure_import_in_application(self):
        for module in (contract_module, storage_module):
            tree = ast.parse(inspect.getsource(module))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append(node.module or "")
            for name in imports:
                self.assertFalse(any(word in name for word in ("requests", "urllib", "websocket", ".binance", "evaluation.af4c")))
                if module is contract_module:
                    self.assertNotIn("infrastructure", name)
        self.assertNotIn("contract", inspect.signature(preflight_development).parameters)
        self.assertNotIn("test_mode", inspect.signature(preflight_development).parameters)

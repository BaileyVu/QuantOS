"""O3 persisted partition verification: exact parity and corruption fallback."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import localcontext
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from quantos.application import (
    AggregateTradeStreamingDiagnostics,
    aggregate_trade_minute_states,
    compose_aggregate_trade_range,
)
from quantos.domain.market_data.research_events import (
    AggregateTradeMinuteDatasetManifest,
    AggregateTradeRangeRequest,
    RevisionSelectionPolicy,
    aggregate_trade_minute_dataset_manifest_bytes,
    aggregate_trade_range_manifest_bytes,
    canonical_aggregate_trade_event_bytes,
)
from quantos.infrastructure.storage.aggregate_trade_catalog import (
    AggregateTradeCatalogError,
    AggregateTradeCatalogRebuildDiagnostics,
)
from quantos.infrastructure.storage.aggregate_trade_minute_primitive_cache import (
    ParquetAggregateTradeMinutePrimitiveCache,
)
from quantos.infrastructure.storage.aggregate_trade_verification_certificate import (
    AggregateTradeCertificateError,
    AggregateTradeVerificationCertificateStore,
    CERTIFICATE_SCHEMA_VERSION,
    certificate_bytes,
    certificate_from_bytes,
)
from tests.aggregate_trade_range_fixtures import local_catalog, publish_partition, publish_rows
from tests.unit.test_binance_aggregate_trade_archive import row


DECEMBER = date(2024, 12, 31)
JANUARY = date(2025, 1, 1)
END = date(2025, 1, 2)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def rehash(value):
    value["certificate_id"] = sha256(encoded(value["evidence"])).hexdigest()
    return encoded(value) + b"\n"


class VerificationCertificateTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="quantos-o3-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sources = []
        self.publications = []
        for day, identifier in ((DECEMBER, 10), (JANUARY, 20)):
            fetched, publication = publish_partition(
                self.root, symbol="BTCUSDT", source_date=day, first_id=identifier
            )
            self.sources.append(fetched.archive.manifest)
            self.publications.append(publication)
        self.certificates = AggregateTradeVerificationCertificateStore(self.root)
        self.request = AggregateTradeRangeRequest(
            symbol="BTCUSDT", start_date=DECEMBER, end_date_exclusive=END,
            selection_policy=RevisionSelectionPolicy.UNIQUE,
        )

    def cold(self):
        catalog = local_catalog(self.root)
        diagnostics = AggregateTradeCatalogRebuildDiagnostics()
        view = catalog.rebuild(diagnostics=diagnostics)
        self.assertEqual(diagnostics.canonical_events_replayed, 4)
        self.assertEqual(diagnostics.certificate_miss_partition_count, 2)
        self.assertEqual(diagnostics.certificate_built_partition_count, 2)
        self.assertEqual(diagnostics.certificate_hit_partition_count, 0)
        self.assertFalse(diagnostics.certificate_fast_path_used)
        return catalog, view

    def assert_one_reverified(self, expected_view):
        fresh = local_catalog(self.root)
        diagnostics = AggregateTradeCatalogRebuildDiagnostics()
        with patch.object(fresh._store, "iter_event_batches", wraps=fresh._store.iter_event_batches) as batches:
            self.assertEqual(fresh.rebuild(diagnostics=diagnostics), expected_view)
        batches.assert_called_once()
        self.assertEqual(diagnostics.certificate_hit_partition_count, 1)
        self.assertEqual(diagnostics.certificate_miss_partition_count, 1)
        self.assertEqual(diagnostics.certificate_built_partition_count, 1)
        self.assertEqual(diagnostics.canonical_events_replayed, 2)
        self.assertTrue(diagnostics.certificate_fast_path_used)
        return fresh

    def test_fresh_instance_restores_same_evidence_catalog_and_zero_replay(self):
        cold, view = self.cold()
        expected_evidence = dict(cold._verified_partitions)
        before = {source: self.certificates.path_for(source).read_bytes() for source in self.sources}
        del cold
        fresh = local_catalog(self.root)
        diagnostics = AggregateTradeCatalogRebuildDiagnostics()
        with patch.object(fresh._store, "iter_event_batches", side_effect=AssertionError("event replay")), \
             patch.object(fresh._store, "verify_raw", wraps=fresh._store.verify_raw) as raw:
            actual = fresh.rebuild(diagnostics=diagnostics)
        self.assertEqual(raw.call_count, 2)
        self.assertEqual(actual, view)
        self.assertEqual(actual.catalog_id, view.catalog_id)
        self.assertEqual(fresh._verified_partitions, expected_evidence)
        self.assertEqual(diagnostics.canonical_events_replayed, 0)
        self.assertEqual(diagnostics.certificate_hit_partition_count, 2)
        self.assertEqual(diagnostics.certificate_miss_partition_count, 0)
        self.assertEqual(diagnostics.certificate_built_partition_count, 0)
        self.assertTrue(diagnostics.certificate_fast_path_used)
        for source in self.sources:
            self.assertEqual(self.certificates.path_for(source).read_bytes(), before[source])

    def test_fully_warm_o1_and_o2_exact_scientific_parity(self):
        cold, _ = self.cold()
        source_range = compose_aggregate_trade_range(cold, self.request)
        cache = ParquetAggregateTradeMinutePrimitiveCache(self.root)
        baseline = aggregate_trade_minute_states(cold, source_range)
        self.assertEqual(aggregate_trade_minute_states(cold, source_range, primitive_cache=cache), baseline)
        del cold
        fresh = local_catalog(self.root)
        catalog_diagnostics = AggregateTradeCatalogRebuildDiagnostics()
        minute_diagnostics = AggregateTradeStreamingDiagnostics()
        with patch.object(fresh._store, "iter_event_batches", side_effect=AssertionError("source decode")), \
             patch.object(fresh, "stream_range", side_effect=AssertionError("raw stream")):
            fresh.rebuild(diagnostics=catalog_diagnostics)
            warm_range = compose_aggregate_trade_range(fresh, self.request)
            warm = aggregate_trade_minute_states(
                fresh, warm_range, primitive_cache=ParquetAggregateTradeMinutePrimitiveCache(self.root),
                diagnostics=minute_diagnostics,
            )
        self.assertEqual(warm_range, source_range)
        self.assertEqual(aggregate_trade_range_manifest_bytes(warm_range), aggregate_trade_range_manifest_bytes(source_range))
        self.assertEqual(warm, baseline)
        self.assertEqual(warm.dataset_id, baseline.dataset_id)
        self.assertEqual(warm.content_sha256, baseline.content_sha256)
        self.assertEqual(
            aggregate_trade_minute_dataset_manifest_bytes(AggregateTradeMinuteDatasetManifest.from_dataset(warm)),
            aggregate_trade_minute_dataset_manifest_bytes(AggregateTradeMinuteDatasetManifest.from_dataset(baseline)),
        )
        self.assertEqual(catalog_diagnostics.canonical_events_replayed, 0)
        self.assertEqual(minute_diagnostics.raw_events_consumed, 0)

    def test_deleted_certificate_replays_only_missing_partition(self):
        _, view = self.cold()
        first = self.certificates.path_for(self.sources[0])
        second = self.certificates.path_for(self.sources[1])
        expected = first.read_bytes()
        untouched = second.stat().st_mtime_ns
        first.unlink()
        self.assert_one_reverified(view)
        self.assertEqual(first.read_bytes(), expected)
        self.assertEqual(second.stat().st_mtime_ns, untouched)

    def test_malformed_noncanonical_duplicate_and_unknown_fields_reverified(self):
        _, view = self.cold()
        source = self.sources[0]
        path = self.certificates.path_for(source)
        original = path.read_bytes()
        unknown = json.loads(original)
        unknown["evidence"]["unknown_field"] = "not allowed"
        wrong_id = json.loads(original)
        wrong_id["certificate_id"] = "0" * 64
        mutations = (
            b"not JSON", b"[]", original.rstrip(),
            json.dumps(json.loads(original), indent=2).encode(),
            original.replace(b'"accepted_event_count":2', b'"accepted_event_count":2,"accepted_event_count":2'),
            original.replace(b'"accepted_event_count":2', b'"accepted_event_count":2.0'),
            original.replace(b'"accepted_event_count":2', b'"accepted_event_count":true'),
            rehash(unknown), encoded(wrong_id) + b"\n",
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(index=index):
                with self.assertRaises(AggregateTradeCertificateError):
                    certificate_from_bytes(mutation, source)
                path.write_bytes(mutation)
                self.assert_one_reverified(view)
                self.assertEqual(path.read_bytes(), original)

    def test_lineage_version_and_summary_mutations_rejected_even_with_new_id(self):
        _, view = self.cold()
        source = self.sources[0]
        path = self.certificates.path_for(source)
        original = path.read_bytes()
        changes = (
            ("manifest_id", "0" * 64), ("canonical_sequence_sha256", "0" * 64),
            ("certificate_schema_version", "other"), ("verification_version", "other"),
            ("storage_schema_version", "other"), ("accepted_event_count", 3),
            ("min_aggregate_trade_id", 11), ("max_aggregate_trade_id", 9),
        )
        nested = (
            ("source_revision_id", "0" * 64), ("dataset_id", "0" * 64),
            ("raw_zip_sha256", "0" * 64), ("canonical_sequence_sha256", "0" * 64),
            ("source_timestamp_unit", "unknown"), ("normalizer_version", "other"),
            ("schema_version", "other"), ("validation_status", "invalid"),
            ("provider", "other"), ("market", "other"), ("source_family", "other"),
            ("symbol", "ETHUSDT"), ("source_date", "2025-01-01"),
            ("observed_numerical_id_gap_count", 99),
            ("observed_first_event_time", "2025-01-01T00:00:00.000000Z"),
        )
        for nested_field, changeset in ((False, changes), (True, nested)):
            for key, value in changeset:
                with self.subTest(field=key, nested=nested_field):
                    parsed = json.loads(original)
                    destination = parsed["evidence"]["source_manifest"] if nested_field else parsed["evidence"]
                    destination[key] = value
                    content = rehash(parsed)
                    with self.assertRaises(AggregateTradeCertificateError):
                        certificate_from_bytes(content, source)
                    path.write_bytes(content)
                    self.assert_one_reverified(view)

    def test_invalid_event_representations_are_not_trusted(self):
        self.cold()
        source = self.sources[0]
        original = self.certificates.path_for(source).read_bytes()
        for field, value in (
            ("price", {"sign": 0, "digits": "001", "exponent": -2}),
            ("quantity", {"sign": 0, "digits": "1", "exponent": True}),
            ("price", {"sign": 0, "digits": "0", "exponent": 0}),
            ("buyer_is_maker", "False"), ("best_price_match", 1),
            ("source_timestamp_unit", "unknown"), ("source_timestamp", 0),
            ("event_time", "2024-12-31T00:00:00.123000+00:00"),
            ("unexpected", "field"),
        ):
            with self.subTest(field=field, value=value):
                parsed = json.loads(original)
                parsed["evidence"]["first_event"][field] = value
                with self.assertRaises(AggregateTradeCertificateError):
                    certificate_from_bytes(rehash(parsed), source)

    def test_event_round_trip_retains_exact_decimals_and_both_timestamp_policies(self):
        cold, _ = self.cold()
        with localcontext() as context:
            context.prec = 3
            for source in self.sources:
                proof = cold._verified_partitions[next(
                    entry.manifest_id for entry in cold.view.entries if entry.manifest == source
                )]
                payload = certificate_bytes(source, proof)
                restored = certificate_from_bytes(payload, source)
                self.assertEqual(restored, proof)
                for left, right in ((restored.first_event, proof.first_event),
                                    (restored.last_event, proof.last_event)):
                    self.assertEqual(canonical_aggregate_trade_event_bytes(left), canonical_aggregate_trade_event_bytes(right))
                    self.assertEqual(left.price.as_tuple(), right.price.as_tuple())
                    self.assertEqual(left.quantity.as_tuple(), right.quantity.as_tuple())
                parsed = json.loads(payload)
                self.assertEqual(parsed["certificate_id"], sha256(encoded(parsed["evidence"])).hexdigest())

    def test_single_event_partition_round_trip(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fetched, _ = publish_rows(root, symbol="BTCUSDT", source_date=JANUARY, rows=[row()])
            cold = local_catalog(root)
            cold.rebuild()
            store = AggregateTradeVerificationCertificateStore(root)
            proof = store.load(fetched.archive.manifest)
            self.assertEqual(proof.first_event, proof.last_event)
            self.assertEqual(proof.accepted_event_count, 1)
            with self.assertRaises(AggregateTradeCertificateError):
                certificate_bytes(fetched.archive.manifest, replace(proof, min_aggregate_trade_id=0))

    def test_same_size_same_mtime_single_byte_canonical_mutation_fails_closed(self):
        self.cold()
        source = self.sources[0]
        certificate_path = self.certificates.path_for(source)
        certificate = certificate_path.read_bytes()
        path = self.publications[0].canonical_parquet_path
        original_stat = path.stat()
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        self.assertEqual(path.stat().st_size, original_stat.st_size)
        self.assertEqual(path.stat().st_mtime_ns, original_stat.st_mtime_ns)
        fresh = local_catalog(self.root)
        with self.assertRaises(AggregateTradeCatalogError):
            fresh.rebuild()
        self.assertEqual(fresh.view.entries, ())
        self.assertEqual(fresh._verified_partitions, {})
        self.assertEqual(certificate_path.read_bytes(), certificate)

    def test_semantically_invalid_parquet_rewrite_gets_exact_verification_and_no_certificate(self):
        self.cold()
        path = self.publications[1].canonical_parquet_path
        with pq.ParquetFile(path) as parquet:
            table = parquet.read()
        index = table.schema.get_field_index("aggregate_trade_id")
        table = table.set_column(index, table.schema.field(index), pa.array([20, 20], type=pa.int64()))
        pq.write_table(table, path, write_page_checksum=True)
        self.certificates.path_for(self.sources[1]).unlink()
        fresh = local_catalog(self.root)
        with self.assertRaisesRegex(AggregateTradeCatalogError, "conflicting|duplicate"):
            fresh.rebuild()
        self.assertFalse(self.certificates.path_for(self.sources[1]).exists())
        self.assertEqual(fresh.view.entries, ())

    def test_valid_parquet_reencoding_recertifies_only_stale_partition(self):
        cold, view = self.cold()
        expected_range = compose_aggregate_trade_range(cold, self.request)
        cert_path = self.certificates.path_for(self.sources[0])
        before = cert_path.read_bytes()
        path = self.publications[0].canonical_parquet_path
        with pq.ParquetFile(path) as parquet:
            table = parquet.read()
        pq.write_table(table, path, compression="NONE", row_group_size=1, write_page_checksum=True)
        fresh = self.assert_one_reverified(view)
        self.assertNotEqual(cert_path.read_bytes(), before)
        self.assertEqual(compose_aggregate_trade_range(fresh, self.request), expected_range)
        next_instance = local_catalog(self.root)
        with patch.object(next_instance._store, "iter_event_batches", side_effect=AssertionError("replay")):
            self.assertEqual(next_instance.rebuild(), view)

    def test_raw_zip_mutation_retains_authoritative_failure(self):
        self.cold()
        path = self.publications[0].raw_archive_path
        path.write_bytes(b"invalid ZIP")
        fresh = local_catalog(self.root)
        with self.assertRaisesRegex(AggregateTradeCatalogError, "raw archive SHA-256"):
            fresh.rebuild()
        self.assertEqual(fresh.view.entries, ())

    def test_cold_mutation_during_verification_issues_no_certificate(self):
        catalog = local_catalog(self.root)
        real = catalog._store.iter_event_batches
        def mutate_after_stream(path, **kwargs):
            yield from real(path, **kwargs)
            with path.open("ab") as handle:
                handle.write(b"mutation after semantic replay")
        with patch.object(catalog._store, "iter_event_batches", side_effect=mutate_after_stream):
            with self.assertRaisesRegex(AggregateTradeCatalogError, "changed during"):
                catalog.rebuild()
        self.assertFalse(self.certificates.path_for(self.sources[0]).exists())
        self.assertEqual(catalog.view.entries, ())

    def test_atomic_replacement_failure_retains_previous_certificate_and_cleans_temp(self):
        self.cold()
        source = self.sources[0]
        path = self.certificates.path_for(source)
        before = path.read_bytes()
        proof = self.certificates.load(source)
        with patch("quantos.infrastructure.storage.aggregate_trade_verification_certificate.os.replace",
                   side_effect=OSError("injected replace failure")):
            with self.assertRaisesRegex(OSError, "replace failure"):
                self.certificates.write(source, proof)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_deterministic_root_independent_path_and_bytes_and_path_escape_rejection(self):
        self.cold()
        source = self.sources[0]
        proof = self.certificates.load(source)
        path = self.certificates.path_for(source)
        self.assertEqual(path.name, f"{proof.manifest_id}.json")
        self.assertIn(CERTIFICATE_SCHEMA_VERSION, path.parts)
        self.assertIn(source.source_date.isoformat(), path.parts)
        with TemporaryDirectory() as directory:
            other = AggregateTradeVerificationCertificateStore(Path(directory))
            other.write(source, proof)
            self.assertEqual(other.path_for(source).read_bytes(), path.read_bytes())
        with patch.object(Path, "resolve", return_value=self.root.parent):
            with self.assertRaisesRegex(AggregateTradeCertificateError, "escapes"):
                self.certificates.path_for(source)

    def test_empty_catalog_and_reused_diagnostics_reset(self):
        self.cold()
        diagnostics = AggregateTradeCatalogRebuildDiagnostics(7, 7, 7, 7, True)
        fresh = local_catalog(self.root)
        fresh.rebuild(diagnostics=diagnostics)
        self.assertEqual(diagnostics.canonical_events_replayed, 0)
        self.assertEqual(diagnostics.certificate_hit_partition_count, 2)
        self.assertEqual(diagnostics.certificate_miss_partition_count, 0)
        with TemporaryDirectory() as directory:
            self.assertEqual(local_catalog(Path(directory)).rebuild(diagnostics=diagnostics).entries, ())
        self.assertEqual(diagnostics, AggregateTradeCatalogRebuildDiagnostics())


if __name__ == "__main__":
    unittest.main()

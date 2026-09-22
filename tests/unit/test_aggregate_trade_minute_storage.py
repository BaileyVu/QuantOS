"""Immutable Parquet storage tests for completed-minute trade state."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from quantos.application import (
    aggregate_trade_minute_states,
    compose_aggregate_trade_range,
)
from quantos.domain.market_data.research_events import (
    AggregateTradeRangeRequest,
    RevisionSelectionPolicy,
)
from quantos.infrastructure.storage import (
    AGGREGATE_TRADE_MINUTE_SCHEMA,
    AggregateTradeMinuteStorageError,
    ParquetAggregateTradeMinuteStateStore,
)
from tests.aggregate_trade_range_fixtures import local_catalog, publish_partition


class AggregateTradeMinuteStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        publish_partition(
            self.root,
            symbol="BTCUSDT",
            source_date=date(2025, 1, 1),
            first_id=10,
        )
        catalog = local_catalog(self.root)
        catalog.rebuild()
        source_range = compose_aggregate_trade_range(
            catalog,
            AggregateTradeRangeRequest(
                symbol="BTCUSDT",
                start_date=date(2025, 1, 1),
                end_date_exclusive=date(2025, 1, 2),
                selection_policy=RevisionSelectionPolicy.UNIQUE,
            ),
        )
        self.dataset = aggregate_trade_minute_states(catalog, source_range)
        self.store = ParquetAggregateTradeMinuteStateStore(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_round_trip_schema_decimals_and_microseconds(self) -> None:
        publication = self.store.write(self.dataset)
        restored = self.store.read(publication.canonical_parquet_path)

        self.assertEqual(restored, self.dataset)
        self.assertEqual(restored.dataset_id, self.dataset.dataset_id)
        self.assertEqual(restored.content_sha256, self.dataset.content_sha256)
        self.assertIsInstance(restored.states[0].total_quote_notional, Decimal)
        self.assertEqual(
            restored.states[0].first_event_time,
            self.dataset.states[0].first_event_time,
        )
        with pq.ParquetFile(publication.canonical_parquet_path) as parquet:
            self.assertTrue(
                parquet.schema_arrow.equals(
                    AGGREGATE_TRADE_MINUTE_SCHEMA,
                    check_metadata=False,
                )
            )
            self.assertEqual(parquet.metadata.num_rows, 1440)

    def test_publication_is_idempotent_and_does_not_rewrite(self) -> None:
        first = self.store.write(self.dataset)
        original_bytes = first.canonical_parquet_path.read_bytes()
        original_mtime = first.canonical_parquet_path.stat().st_mtime_ns

        second = self.store.write(self.dataset)

        self.assertEqual(first, second)
        self.assertEqual(second.canonical_parquet_path.read_bytes(), original_bytes)
        self.assertEqual(
            second.canonical_parquet_path.stat().st_mtime_ns,
            original_mtime,
        )

    def test_path_binds_schema_symbol_interval_and_dataset_id(self) -> None:
        path = self.store.dataset_path(self.dataset)
        self.assertEqual(path.name, f"{self.dataset.dataset_id}.parquet")
        self.assertIn("aggregate-trade-minute-parquet-v1", path.parts)
        self.assertIn("BTCUSDT", path.parts)
        self.assertIn("2025-01-01_2025-01-02", path.parts)
        self.assertFalse(path.exists())

    def test_corrupt_file_schema_and_noncanonical_decimal_fail_closed(self) -> None:
        publication = self.store.write(self.dataset)
        path = publication.canonical_parquet_path
        original = path.read_bytes()
        path.write_bytes(b"not parquet")
        with self.assertRaises(AggregateTradeMinuteStorageError):
            self.store.read(path)

        path.write_bytes(original)
        table = pq.read_table(path)
        column_index = table.schema.get_field_index("total_base_quantity")
        values = table["total_base_quantity"].to_pylist()
        values[0] = "1.00"
        tampered = table.set_column(
            column_index,
            table.schema.field(column_index),
            pa.array(values, type=pa.string(), safe=True),
        )
        pq.write_table(tampered, path, write_page_checksum=True)
        with self.assertRaisesRegex(
            AggregateTradeMinuteStorageError, "not canonically encoded"
        ):
            self.store.read(path)

    def test_missing_file_and_wrong_write_type_fail_closed(self) -> None:
        with self.assertRaises(AggregateTradeMinuteStorageError):
            self.store.read(self.root / "missing.parquet")
        with self.assertRaises(AggregateTradeMinuteStorageError):
            self.store.write(object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()

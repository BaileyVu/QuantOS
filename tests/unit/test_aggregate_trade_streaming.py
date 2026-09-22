"""DE1H bounded-memory AggregateTrade replay and materialization tests."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from quantos.application import (
    AggregateTradeMinuteAggregationError,
    AggregateTradeStreamingDiagnostics,
    aggregate_trade_minute_states,
    compose_aggregate_trade_range,
    replay_aggregate_trade_minute_states,
)
from quantos.domain.market_data.research_events import (
    AggregateTradeRangeRequest,
    AggregateTradeValidationError,
    IncrementalAggregateTradeSequenceHasher,
    IncrementalAggregateTradeSequenceValidator,
    RevisionSelectionPolicy,
    canonical_aggregate_trade_event_bytes,
    canonical_aggregate_trade_sequence_sha256,
    validate_aggregate_trade_sequence,
)
from tests.aggregate_trade_range_fixtures import (
    local_catalog,
    publish_partition,
    publish_rows,
)
from tests.unit.test_aggregate_trade_research import (
    BASE_MS,
    aggregate_trade,
    dataset_identity,
)
from tests.unit.test_binance_aggregate_trade_archive import (
    MICROSECOND_DATE,
    MICROSECOND_TIMESTAMP,
    row,
)


DECEMBER = date(2024, 12, 31)
JANUARY = date(2025, 1, 1)


class DictExactIdRegistry:
    def __init__(self) -> None:
        self.values: dict[int, bytes] = {}

    def previous_or_add(
        self, aggregate_trade_id: int, canonical_event_bytes: bytes
    ) -> bytes | None:
        previous = self.values.get(aggregate_trade_id)
        if previous is None:
            self.values[aggregate_trade_id] = canonical_event_bytes
        return previous


def request(symbol: str = "BTCUSDT") -> AggregateTradeRangeRequest:
    return AggregateTradeRangeRequest(
        symbol=symbol,
        start_date=DECEMBER,
        end_date_exclusive=date(2025, 1, 2),
        selection_policy=RevisionSelectionPolicy.UNIQUE,
    )


def rewrite_table(path: Path, table: pa.Table) -> None:
    pq.write_table(
        table,
        path,
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
    )


class IncrementalCanonicalSequenceTests(unittest.TestCase):
    def test_streamed_digest_equals_materialized_for_adversarial_ids(self) -> None:
        events = (
            aggregate_trade(10_000, source_timestamp=BASE_MS),
            aggregate_trade(1, source_timestamp=BASE_MS + 1),
            aggregate_trade(500_000, source_timestamp=BASE_MS + 2),
        )
        materialized = validate_aggregate_trade_sequence(
            dataset_identity(), events
        )
        validator = IncrementalAggregateTradeSequenceValidator(
            dataset_identity(), DictExactIdRegistry()
        )
        hasher = IncrementalAggregateTradeSequenceHasher(
            materialized.identity
        )
        for event in events:
            encoded = canonical_aggregate_trade_event_bytes(event)
            validator.observe(event, encoded)
            hasher.update(event, canonical_event_bytes=encoded)

        result = validator.result()

        self.assertEqual(result.event_count, len(events))
        self.assertEqual(
            hasher.hexdigest(),
            canonical_aggregate_trade_sequence_sha256(materialized),
        )

    def test_cross_call_same_timestamp_gap_duplicate_conflict_and_reversal(self) -> None:
        first = aggregate_trade(100)
        same_time_gap = aggregate_trade(105)
        validator = IncrementalAggregateTradeSequenceValidator(
            dataset_identity(), DictExactIdRegistry()
        )
        for event in (first, same_time_gap):
            validator.observe(
                event, canonical_aggregate_trade_event_bytes(event)
            )
        self.assertEqual(
            validator.result().observed_numerical_id_gap_count, 1
        )

        cases = (
            (first, first, "duplicate"),
            (
                first,
                aggregate_trade(100, source_timestamp=BASE_MS + 1),
                "conflicting",
            ),
            (
                aggregate_trade(101, source_timestamp=BASE_MS + 1),
                aggregate_trade(100, source_timestamp=BASE_MS),
                "strictly ordered",
            ),
        )
        for left, right, message in cases:
            with self.subTest(message=message):
                validator = IncrementalAggregateTradeSequenceValidator(
                    dataset_identity(), DictExactIdRegistry()
                )
                validator.observe(
                    left, canonical_aggregate_trade_event_bytes(left)
                )
                with self.assertRaisesRegex(
                    AggregateTradeValidationError, message
                ):
                    validator.observe(
                        right, canonical_aggregate_trade_event_bytes(right)
                    )


class StreamingRangeAndMinuteStateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        publish_partition(
            self.root,
            symbol="BTCUSDT",
            source_date=DECEMBER,
            first_id=10,
        )
        publish_partition(
            self.root,
            symbol="BTCUSDT",
            source_date=JANUARY,
            first_id=20,
        )
        self.catalog = local_catalog(self.root)
        self.catalog.rebuild()

    def test_batch_sizes_preserve_range_state_decimal_zero_and_replay_identity(self) -> None:
        ranges = tuple(
            compose_aggregate_trade_range(
                self.catalog, request(), batch_size=batch_size
            )
            for batch_size in (1, 2, 64)
        )
        self.assertEqual(ranges[0], ranges[1])
        self.assertEqual(ranges[0], ranges[2])

        outputs = []
        diagnostics = []
        for batch_size in (1, 2, 64):
            observed = AggregateTradeStreamingDiagnostics()
            outputs.append(
                aggregate_trade_minute_states(
                    self.catalog,
                    ranges[0],
                    batch_size=batch_size,
                    diagnostics=observed,
                )
            )
            diagnostics.append(observed)

        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[0], outputs[2])
        self.assertEqual(outputs[0].input_event_count, 4)
        self.assertEqual(len(outputs[0].states), 2_880)
        self.assertEqual(outputs[0].zero_event_minute_count, 2_878)
        self.assertEqual(
            outputs[0].states[0].total_base_quantity.as_tuple(),
            outputs[1].states[0].total_base_quantity.as_tuple(),
        )
        self.assertEqual(
            replay_aggregate_trade_minute_states(
                self.catalog,
                ranges[0],
                outputs[0],
                batch_size=1,
            ),
            outputs[0],
        )
        for batch_size, observed in zip((1, 2, 64), diagnostics, strict=True):
            self.assertLessEqual(
                observed.max_batch_event_count, batch_size
            )
            self.assertLessEqual(
                observed.max_raw_events_buffered,
                batch_size + observed.max_minute_event_count,
            )

    def test_corruption_across_batch_boundaries_fails_closed(self) -> None:
        for mutation, message in (
            ("duplicate", "duplicate"),
            ("conflict", "conflicting"),
            ("reversal", "strictly ordered"),
        ):
            with self.subTest(mutation=mutation):
                with TemporaryDirectory() as directory:
                    root = Path(directory)
                    _, publication = publish_partition(
                        root,
                        symbol="BTCUSDT",
                        source_date=JANUARY,
                        first_id=700,
                    )
                    catalog = local_catalog(root)
                    catalog.rebuild()
                    with pq.ParquetFile(
                        publication.canonical_parquet_path
                    ) as parquet:
                        table = parquet.read()
                    if mutation == "duplicate":
                        table = table.take(pa.array([0, 0], type=pa.int64()))
                    elif mutation == "conflict":
                        column_index = table.schema.get_field_index(
                            "aggregate_trade_id"
                        )
                        table = table.set_column(
                            column_index,
                            table.schema.field(column_index),
                            pa.array([700, 700], type=pa.int64()),
                        )
                    else:
                        table = table.take(pa.array([1, 0], type=pa.int64()))
                    rewrite_table(publication.canonical_parquet_path, table)
                    one_day = AggregateTradeRangeRequest(
                        symbol="BTCUSDT",
                        start_date=JANUARY,
                        end_date_exclusive=date(2025, 1, 2),
                        selection_policy=RevisionSelectionPolicy.UNIQUE,
                    )
                    with self.assertRaisesRegex(ValueError, message):
                        compose_aggregate_trade_range(
                            catalog, one_day, batch_size=1
                        )

    def test_cross_partition_conflict_and_late_failure_publish_nothing(self) -> None:
        manifest = compose_aggregate_trade_range(
            self.catalog, request(), batch_size=1
        )
        january_entry = self.catalog.view.entry_for_manifest_id(
            manifest.partitions[1].manifest_id
        )
        with pq.ParquetFile(
            january_entry.canonical_parquet_path
        ) as parquet:
            table = parquet.read()
        column_index = table.schema.get_field_index("aggregate_trade_id")
        table = table.set_column(
            column_index,
            table.schema.field(column_index),
            pa.array([11, 21], type=pa.int64()),
        )
        rewrite_table(january_entry.canonical_parquet_path, table)
        output_root = (
            self.root / "market_data" / "aggregate_trade_minute_states"
        )

        with self.assertRaisesRegex(
            AggregateTradeMinuteAggregationError, "conflicting"
        ):
            aggregate_trade_minute_states(
                self.catalog, manifest, batch_size=1
            )

        self.assertFalse(output_root.exists())

    def test_buffer_bound_is_independent_of_total_event_count(self) -> None:
        def aggregate_count(root: Path, count: int):
            rows = [
                row(
                    aggregate_trade_id=str(10_000 + index),
                    first_trade_id=str(20_000 + index),
                    last_trade_id=str(20_000 + index),
                    timestamp=str(
                        MICROSECOND_TIMESTAMP + index * 60_000_000
                    ),
                )
                for index in range(count)
            ]
            publish_rows(
                root,
                symbol="BTCUSDT",
                source_date=MICROSECOND_DATE,
                rows=rows,
            )
            catalog = local_catalog(root)
            catalog.rebuild()
            one_day = AggregateTradeRangeRequest(
                symbol="BTCUSDT",
                start_date=MICROSECOND_DATE,
                end_date_exclusive=MICROSECOND_DATE + timedelta(days=1),
                selection_policy=RevisionSelectionPolicy.UNIQUE,
            )
            manifest = compose_aggregate_trade_range(
                catalog, one_day, batch_size=17
            )
            diagnostics = AggregateTradeStreamingDiagnostics()
            dataset = aggregate_trade_minute_states(
                catalog,
                manifest,
                batch_size=17,
                diagnostics=diagnostics,
            )
            return dataset, diagnostics

        with TemporaryDirectory() as small_directory, TemporaryDirectory() as large_directory:
            small, small_diagnostics = aggregate_count(
                Path(small_directory), 12
            )
            large, large_diagnostics = aggregate_count(
                Path(large_directory), 240
            )

        self.assertEqual(small.input_event_count, 12)
        self.assertEqual(large.input_event_count, 240)
        self.assertLessEqual(small_diagnostics.max_raw_events_buffered, 18)
        self.assertLessEqual(large_diagnostics.max_raw_events_buffered, 18)


if __name__ == "__main__":
    unittest.main()

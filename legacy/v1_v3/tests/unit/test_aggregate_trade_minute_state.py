"""Deterministic completed-minute aggregate-trade state tests."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, FloatOperation, getcontext
from pathlib import Path
import tempfile
import unittest

from quantos.application import (
    AggregateTradeMinuteAggregationError,
    aggregate_trade_minute_states,
    compose_aggregate_trade_range,
    replay_aggregate_trade_minute_states,
)
from quantos.domain.market_data.research_events import (
    AggregateTradeMinuteAvailabilityState,
    AggregateTradeMinuteDatasetManifest,
    AggregateTradeRangeRequest,
    ExactAggregateTradeRevision,
    RevisionSelectionPolicy,
    SourceTimestampUnit,
    aggregate_trade_archive_manifest_id,
    aggregate_trade_minute_dataset_id,
    aggregate_trade_minute_dataset_manifest_bytes,
    aggregate_trade_minute_dataset_manifest_from_bytes,
)
from tests.aggregate_trade_range_fixtures import (
    local_catalog,
    publish_partition,
    publish_rows,
)
from tests.unit.test_binance_aggregate_trade_archive import row


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
DAY = date(2025, 1, 1)


def timestamp(value: datetime) -> int:
    return (value - EPOCH) // timedelta(microseconds=1)


def exact(manifest) -> ExactAggregateTradeRevision:
    return ExactAggregateTradeRevision(
        source_date=manifest.source_date,
        manifest_id=aggregate_trade_archive_manifest_id(manifest),
        source_revision_id=manifest.source_revision_id,
    )


class AggregateTradeMinuteStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def custom_rows(self) -> list[list[str]]:
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        return [
            row(
                aggregate_trade_id="10",
                price="10.000000000000000000",
                quantity="0.100000000000000000",
                first_trade_id="100",
                last_trade_id="999",
                timestamp=str(timestamp(start)),
                buyer_is_maker="False",
            ),
            row(
                aggregate_trade_id="11",
                price="3.000000000000000000",
                quantity="0.200000000000000000",
                first_trade_id="1000",
                last_trade_id="1000",
                timestamp=str(timestamp(start + timedelta(seconds=59, microseconds=999999))),
                buyer_is_maker="True",
            ),
            row(
                aggregate_trade_id="12",
                price="1.234567890123456789",
                quantity="0.000000000000000001",
                first_trade_id="1001",
                last_trade_id="1001",
                timestamp=str(timestamp(start + timedelta(minutes=1))),
                buyer_is_maker="False",
            ),
            row(
                aggregate_trade_id="13",
                price="2.000000000000000000",
                quantity="3.000000000000000000",
                first_trade_id="1002",
                last_trade_id="1002",
                timestamp=str(timestamp(start + timedelta(minutes=1))),
                buyer_is_maker="True",
            ),
        ]

    def dataset_for_custom_day(self):
        fetched, publication = publish_rows(
            self.root,
            symbol="BTCUSDT",
            source_date=DAY,
            rows=self.custom_rows(),
        )
        catalog = local_catalog(self.root)
        catalog.rebuild()
        source_range = compose_aggregate_trade_range(
            catalog,
            AggregateTradeRangeRequest(
                symbol="BTCUSDT",
                start_date=DAY,
                end_date_exclusive=date(2025, 1, 2),
                selection_policy=RevisionSelectionPolicy.UNIQUE,
            ),
        )
        return (
            aggregate_trade_minute_states(catalog, source_range),
            catalog,
            source_range,
            fetched,
            publication,
        )

    def test_exact_half_open_buckets_aggressors_and_decimal_totals(self) -> None:
        original_context = getcontext().copy()
        getcontext().prec = 3
        getcontext().traps[FloatOperation] = True
        try:
            dataset, _, _, _, _ = self.dataset_for_custom_day()
        finally:
            getcontext().prec = original_context.prec
            getcontext().traps[FloatOperation] = original_context.traps[FloatOperation]

        first = dataset.states[0]
        second = dataset.states[1]
        self.assertEqual(len(dataset.states), 1440)
        self.assertEqual(first.event_count, 2)
        self.assertEqual(first.aggressive_buy_event_count, 1)
        self.assertEqual(first.aggressive_sell_event_count, 1)
        self.assertEqual(first.total_base_quantity, Decimal("0.3"))
        self.assertEqual(first.total_quote_notional, Decimal("1.6"))
        self.assertEqual(first.aggressive_buy_base_quantity, Decimal("0.1"))
        self.assertEqual(first.aggressive_sell_base_quantity, Decimal("0.2"))
        self.assertEqual(first.first_aggregate_trade_id, 10)
        self.assertEqual(first.last_aggregate_trade_id, 11)
        self.assertEqual(first.first_event_time, datetime(2025, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(
            first.last_event_time,
            datetime(2025, 1, 1, 0, 0, 59, 999999, tzinfo=timezone.utc),
        )
        self.assertEqual(second.event_count, 2)
        self.assertEqual(second.first_aggregate_trade_id, 12)
        self.assertEqual(second.last_aggregate_trade_id, 13)
        self.assertEqual(
            second.aggressive_buy_quote_notional,
            Decimal("0.000000000000000001234567890123456789"),
        )
        self.assertEqual(
            dataset.input_event_count,
            4,
            "aggregate-trade records, not underlying execution count",
        )

    def test_zero_event_minutes_require_complete_source_and_exact_zero_state(self) -> None:
        dataset, catalog, source_range, _, publication = self.dataset_for_custom_day()
        zero = dataset.states[2]
        self.assertEqual(zero.event_count, 0)
        self.assertEqual(zero.total_base_quantity, Decimal(0))
        self.assertEqual(zero.total_quote_notional, Decimal(0))
        self.assertIsNone(zero.first_aggregate_trade_id)
        self.assertIsNone(zero.last_event_time)
        self.assertEqual(dataset.zero_event_minute_count, 1438)

        publication.raw_archive_path.unlink()
        with self.assertRaisesRegex(
            AggregateTradeMinuteAggregationError, "failed exact replay"
        ):
            aggregate_trade_minute_states(catalog, source_range)

    def test_state_and_sequence_validation_reject_invalid_time_and_lineage(self) -> None:
        dataset, _, _, _, _ = self.dataset_for_custom_day()
        state = dataset.states[0]
        with self.assertRaises(ValueError):
            replace(state, minute_start_time=state.minute_start_time.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            replace(
                state,
                minute_start_time=state.minute_start_time.astimezone(
                    timezone(timedelta(hours=1))
                ),
            )
        wrong_lineage = replace(state, source_range_id="0" * 64)
        with self.assertRaises(ValueError):
            replace(dataset, states=(wrong_lineage,) + dataset.states[1:])
        with self.assertRaises(ValueError):
            replace(dataset, states=dataset.states[:-1])

    def test_manifest_content_identity_and_replay_are_deterministic(self) -> None:
        dataset, catalog, source_range, _, _ = self.dataset_for_custom_day()
        repeated = aggregate_trade_minute_states(catalog, source_range)
        replayed = replay_aggregate_trade_minute_states(
            catalog, source_range, dataset
        )
        manifest = AggregateTradeMinuteDatasetManifest.from_dataset(dataset)
        payload = aggregate_trade_minute_dataset_manifest_bytes(manifest)

        self.assertEqual(repeated, dataset)
        self.assertEqual(replayed.dataset_id, dataset.dataset_id)
        self.assertEqual(repeated.content_sha256, dataset.content_sha256)
        self.assertEqual(
            aggregate_trade_minute_dataset_manifest_from_bytes(payload),
            manifest,
        )
        self.assertEqual(
            aggregate_trade_minute_dataset_manifest_bytes(
                aggregate_trade_minute_dataset_manifest_from_bytes(payload)
            ),
            payload,
        )
        changed_version = replace(
            dataset.identity,
            aggregation_version="aggregate-trade-minute-aggregation-v2",
        )
        self.assertNotEqual(
            aggregate_trade_minute_dataset_id(
                changed_version, dataset.content_sha256
            ),
            dataset.dataset_id,
        )

    def test_two_day_grid_preserves_timestamp_unit_transition(self) -> None:
        publish_partition(
            self.root,
            symbol="ETHUSDT",
            source_date=date(2024, 12, 31),
            first_id=10,
        )
        publish_partition(
            self.root,
            symbol="ETHUSDT",
            source_date=DAY,
            first_id=20,
        )
        catalog = local_catalog(self.root)
        catalog.rebuild()
        source_range = compose_aggregate_trade_range(
            catalog,
            AggregateTradeRangeRequest(
                symbol="ETHUSDT",
                start_date=date(2024, 12, 31),
                end_date_exclusive=date(2025, 1, 2),
                selection_policy=RevisionSelectionPolicy.UNIQUE,
            ),
        )
        dataset = aggregate_trade_minute_states(catalog, source_range)

        self.assertEqual(len(dataset.states), 2880)
        self.assertEqual(dataset.states[1439].minute_end_time_exclusive, dataset.states[1440].minute_start_time)
        self.assertIs(dataset.states[1439].source_timestamp_unit, SourceTimestampUnit.MILLISECOND)
        self.assertIs(dataset.states[1440].source_timestamp_unit, SourceTimestampUnit.MICROSECOND)
        self.assertEqual(dataset.input_event_count, 4)

    def test_source_revision_and_wrong_range_identity_change_or_fail(self) -> None:
        rows = self.custom_rows()
        first, _ = publish_rows(
            self.root,
            symbol="BTCUSDT",
            source_date=DAY,
            rows=rows,
        )
        second, _ = publish_rows(
            self.root,
            symbol="BTCUSDT",
            source_date=DAY,
            rows=rows,
            stored=True,
        )
        catalog = local_catalog(self.root)
        catalog.rebuild()

        def compose(manifest):
            return compose_aggregate_trade_range(
                catalog,
                AggregateTradeRangeRequest(
                    symbol="BTCUSDT",
                    start_date=DAY,
                    end_date_exclusive=date(2025, 1, 2),
                    selection_policy=RevisionSelectionPolicy.EXACT,
                    exact_revisions=(exact(manifest),),
                ),
            )

        first_range = compose(first.archive.manifest)
        second_range = compose(second.archive.manifest)
        first_dataset = aggregate_trade_minute_states(catalog, first_range)
        second_dataset = aggregate_trade_minute_states(catalog, second_range)
        self.assertNotEqual(first_range.range_id, second_range.range_id)
        self.assertNotEqual(first_dataset.dataset_id, second_dataset.dataset_id)

        wrong_reference = replace(
            first_range.partitions[0], manifest_id="0" * 64
        )
        wrong_range = replace(first_range, partitions=(wrong_reference,))
        with self.assertRaisesRegex(
            AggregateTradeMinuteAggregationError, "failed exact replay"
        ):
            aggregate_trade_minute_states(catalog, wrong_range)

    def test_aggregation_implementation_contains_no_float_arithmetic(self) -> None:
        path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "quantos"
            / "application"
            / "aggregate_trade_minute_states.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant):
                self.assertNotIsInstance(node.value, float)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotEqual(node.func.id, "float")

    def test_historical_state_explicitly_does_not_claim_live_availability(self) -> None:
        dataset, _, _, _, _ = self.dataset_for_custom_day()
        self.assertIs(
            dataset.identity.availability_state,
            AggregateTradeMinuteAvailabilityState.HISTORICAL_ONLY_LIVE_UNPROVEN,
        )
        self.assertTrue(
            all(
                state.availability_state
                is AggregateTradeMinuteAvailabilityState.HISTORICAL_ONLY_LIVE_UNPROVEN
                for state in dataset.states
            )
        )


if __name__ == "__main__":
    unittest.main()

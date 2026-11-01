"""Adversarial tests for deterministic aggregate-trade range composition."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from quantos.application import (
    AggregateTradeRangeCompositionError,
    compose_aggregate_trade_range,
    iter_aggregate_trade_range,
    replay_aggregate_trade_range,
    validate_aggregate_trade_partition_boundary,
)
from quantos.domain.market_data.research_events import (
    AggregateTradeArchiveManifest,
    AggregateTradeRangeManifest,
    AggregateTradeRangeRequest,
    ExactAggregateTradeRevision,
    RevisionSelectionPolicy,
    SourceTimestampUnit,
    aggregate_trade_archive_manifest_id,
    aggregate_trade_range_manifest_bytes,
    aggregate_trade_range_manifest_from_bytes,
)
from tests.aggregate_trade_range_fixtures import local_catalog, publish_partition


DECEMBER = date(2024, 12, 31)
JANUARY = date(2025, 1, 1)


def exact(manifest: AggregateTradeArchiveManifest) -> ExactAggregateTradeRevision:
    return ExactAggregateTradeRevision(
        source_date=manifest.source_date,
        manifest_id=aggregate_trade_archive_manifest_id(manifest),
        source_revision_id=manifest.source_revision_id,
    )


class AggregateTradeRangeCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def publish_two_days(self):
        december, _ = publish_partition(
            self.root, symbol="BTCUSDT", source_date=DECEMBER, first_id=10
        )
        january, _ = publish_partition(
            self.root, symbol="BTCUSDT", source_date=JANUARY, first_id=20
        )
        catalog = local_catalog(self.root)
        catalog.rebuild()
        return catalog, december, january

    def test_one_day_and_two_day_ranges_are_complete_and_deterministic(self) -> None:
        catalog, december, january = self.publish_two_days()
        one = compose_aggregate_trade_range(
            catalog,
            AggregateTradeRangeRequest(
                symbol="BTCUSDT",
                start_date=DECEMBER,
                end_date_exclusive=JANUARY,
                selection_policy=RevisionSelectionPolicy.UNIQUE,
            ),
        )
        request = AggregateTradeRangeRequest(
            symbol="BTCUSDT",
            start_date=DECEMBER,
            end_date_exclusive=date(2025, 1, 2),
            selection_policy=RevisionSelectionPolicy.UNIQUE,
        )
        first = compose_aggregate_trade_range(catalog, request)
        second = compose_aggregate_trade_range(catalog, request)

        self.assertEqual(one.total_accepted_event_count, 2)
        self.assertEqual(first, second)
        self.assertEqual(first.range_id, second.range_id)
        self.assertEqual(first.total_accepted_event_count, 4)
        self.assertEqual(
            tuple(item.logical_partition.source_date for item in first.partitions),
            (DECEMBER, JANUARY),
        )
        self.assertIs(first.partitions[0].source_timestamp_unit, SourceTimestampUnit.MILLISECOND)
        self.assertIs(first.partitions[1].source_timestamp_unit, SourceTimestampUnit.MICROSECOND)
        self.assertEqual(first.boundaries[0].observed_aggregate_id_delta, 9)
        self.assertFalse(first.boundaries[0].numerical_continuity_asserted)
        self.assertEqual(first.partitions[0].manifest_id, aggregate_trade_archive_manifest_id(december.archive.manifest))
        self.assertEqual(first.partitions[1].manifest_id, aggregate_trade_archive_manifest_id(january.archive.manifest))

    def test_invalid_empty_reversed_and_missing_ranges_fail_closed(self) -> None:
        for start, end in ((JANUARY, JANUARY), (JANUARY, DECEMBER)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    AggregateTradeRangeRequest(
                        symbol="BTCUSDT",
                        start_date=start,
                        end_date_exclusive=end,
                        selection_policy=RevisionSelectionPolicy.UNIQUE,
                    )
        publish_partition(
            self.root, symbol="BTCUSDT", source_date=DECEMBER, first_id=10
        )
        catalog = local_catalog(self.root)
        catalog.rebuild()
        with self.assertRaisesRegex(AggregateTradeRangeCompositionError, "missing partition"):
            compose_aggregate_trade_range(
                catalog,
                AggregateTradeRangeRequest(
                    symbol="BTCUSDT",
                    start_date=DECEMBER,
                    end_date_exclusive=date(2025, 1, 2),
                    selection_policy=RevisionSelectionPolicy.UNIQUE,
                ),
            )

    def test_unique_rejects_ambiguity_and_exact_pins_each_revision(self) -> None:
        first, _ = publish_partition(
            self.root, symbol="BTCUSDT", source_date=JANUARY, first_id=10
        )
        second, _ = publish_partition(
            self.root,
            symbol="BTCUSDT",
            source_date=JANUARY,
            first_id=20,
            stored=True,
        )
        catalog = local_catalog(self.root)
        catalog.rebuild()
        with self.assertRaisesRegex(AggregateTradeRangeCompositionError, "ambiguous partition"):
            compose_aggregate_trade_range(
                catalog,
                AggregateTradeRangeRequest(
                    symbol="BTCUSDT",
                    start_date=JANUARY,
                    end_date_exclusive=date(2025, 1, 2),
                    selection_policy=RevisionSelectionPolicy.UNIQUE,
                ),
            )
        first_range = compose_aggregate_trade_range(
            catalog,
            AggregateTradeRangeRequest(
                symbol="BTCUSDT",
                start_date=JANUARY,
                end_date_exclusive=date(2025, 1, 2),
                selection_policy=RevisionSelectionPolicy.EXACT,
                exact_revisions=(exact(first.archive.manifest),),
            ),
        )
        second_range = compose_aggregate_trade_range(
            catalog,
            AggregateTradeRangeRequest(
                symbol="BTCUSDT",
                start_date=JANUARY,
                end_date_exclusive=date(2025, 1, 2),
                selection_policy=RevisionSelectionPolicy.EXACT,
                exact_revisions=(exact(second.archive.manifest),),
            ),
        )
        self.assertNotEqual(first_range.range_id, second_range.range_id)
        missing = replace(exact(first.archive.manifest), manifest_id="0" * 64)
        with self.assertRaisesRegex(AggregateTradeRangeCompositionError, "exact revision does not exist"):
            compose_aggregate_trade_range(
                catalog,
                AggregateTradeRangeRequest(
                    symbol="BTCUSDT",
                    start_date=JANUARY,
                    end_date_exclusive=date(2025, 1, 2),
                    selection_policy=RevisionSelectionPolicy.EXACT,
                    exact_revisions=(missing,),
                ),
            )

    def test_new_revision_never_replaces_pinned_range(self) -> None:
        first, _ = publish_partition(
            self.root, symbol="BTCUSDT", source_date=JANUARY, first_id=10
        )
        catalog = local_catalog(self.root)
        catalog.rebuild()
        pinned = compose_aggregate_trade_range(
            catalog,
            AggregateTradeRangeRequest(
                symbol="BTCUSDT",
                start_date=JANUARY,
                end_date_exclusive=date(2025, 1, 2),
                selection_policy=RevisionSelectionPolicy.UNIQUE,
            ),
        )
        publish_partition(
            self.root,
            symbol="BTCUSDT",
            source_date=JANUARY,
            first_id=20,
            stored=True,
        )
        catalog.rebuild()

        replayed = replay_aggregate_trade_range(catalog, pinned)

        self.assertEqual(replayed.range_id, pinned.range_id)
        self.assertEqual(replayed.partitions[0].manifest_id, aggregate_trade_archive_manifest_id(first.archive.manifest))

    def test_boundary_failures_and_id_gap_evidence(self) -> None:
        _, december, january = self.publish_two_days()
        left = december.archive.sequence.events
        right = january.archive.sequence.events
        gap = validate_aggregate_trade_partition_boundary(
            left,
            right,
            left_source_date=DECEMBER,
            right_source_date=JANUARY,
        )
        self.assertEqual(gap.observed_aggregate_id_delta, 9)
        with self.assertRaisesRegex(AggregateTradeRangeCompositionError, "chronological reversal"):
            validate_aggregate_trade_partition_boundary(
                right,
                left,
                left_source_date=DECEMBER,
                right_source_date=JANUARY,
            )
        with self.assertRaisesRegex(AggregateTradeRangeCompositionError, "duplicate aggregate trade IDs"):
            validate_aggregate_trade_partition_boundary(
                left,
                (left[-1],),
                left_source_date=DECEMBER,
                right_source_date=JANUARY,
            )
        conflicting = replace(right[0], aggregate_trade_id=left[-1].aggregate_trade_id)
        with self.assertRaisesRegex(AggregateTradeRangeCompositionError, "conflicting aggregate trade IDs"):
            validate_aggregate_trade_partition_boundary(
                left,
                (conflicting,),
                left_source_date=DECEMBER,
                right_source_date=JANUARY,
            )
        overlapping = replace(left[-1], price=Decimal("1"))
        with self.assertRaisesRegex(AggregateTradeRangeCompositionError, "overlapping canonical event identities"):
            validate_aggregate_trade_partition_boundary(
                left,
                (overlapping,),
                left_source_date=DECEMBER,
                right_source_date=JANUARY,
            )

    def test_manifest_round_trip_iteration_decimal_and_microseconds(self) -> None:
        catalog, _, january = self.publish_two_days()
        manifest = compose_aggregate_trade_range(
            catalog,
            AggregateTradeRangeRequest(
                symbol="BTCUSDT",
                start_date=DECEMBER,
                end_date_exclusive=date(2025, 1, 2),
                selection_policy=RevisionSelectionPolicy.UNIQUE,
            ),
        )
        payload = aggregate_trade_range_manifest_bytes(manifest)
        decoded = aggregate_trade_range_manifest_from_bytes(payload)
        events = tuple(iter_aggregate_trade_range(catalog, decoded))

        self.assertEqual(decoded, manifest)
        self.assertEqual(decoded.range_id, manifest.range_id)
        self.assertEqual(len(events), manifest.total_accepted_event_count)
        self.assertIsInstance(events[-1].price, Decimal)
        self.assertEqual(events[-1].price, Decimal("93452.130000000000000001"))
        self.assertEqual(events[-1].event_time.microsecond, january.archive.sequence.events[-1].event_time.microsecond)
        self.assertIs(events[-1].source_timestamp_unit, SourceTimestampUnit.MICROSECOND)
        self.assertIsInstance(decoded, AggregateTradeRangeManifest)

        with self.assertRaisesRegex(ValueError, "exact UTC date order"):
            replace(manifest, partitions=tuple(reversed(manifest.partitions)))

    def test_symbol_mismatch_is_rejected(self) -> None:
        eth, _ = publish_partition(
            self.root, symbol="ETHUSDT", source_date=JANUARY, first_id=10
        )

        class MismatchedCatalog:
            def revisions(self, *, symbol: str, source_date: date):
                return (eth.archive.manifest,)

            def load(self, manifest: AggregateTradeArchiveManifest):
                return eth.archive

        with self.assertRaisesRegex(AggregateTradeRangeCompositionError, "conflicting logical partition metadata"):
            compose_aggregate_trade_range(
                MismatchedCatalog(),
                AggregateTradeRangeRequest(
                    symbol="BTCUSDT",
                    start_date=JANUARY,
                    end_date_exclusive=date(2025, 1, 2),
                    selection_policy=RevisionSelectionPolicy.UNIQUE,
                ),
            )


if __name__ == "__main__":
    unittest.main()

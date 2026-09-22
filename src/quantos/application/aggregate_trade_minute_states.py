"""Research-only orchestration for completed-minute aggregate-trade state."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Iterator

from quantos.application.aggregate_trade_ranges import (
    AggregateTradePartitionCatalog,
    replay_aggregate_trade_range,
)
from quantos.domain.market_data.research_events import (
    AGGREGATE_TRADE_MINUTE_AGGREGATION_VERSION,
    AGGREGATE_TRADE_MINUTE_INTERVAL_SPECIFICATION,
    AGGREGATE_TRADE_MINUTE_STATE_FAMILY,
    AGGREGATE_TRADE_MINUTE_STATE_SCHEMA_VERSION,
    AggregateTrade,
    AggregateTradeMinuteAvailabilityState,
    AggregateTradeMinuteCompletenessState,
    AggregateTradeMinuteDatasetIdentity,
    AggregateTradeMinuteSourceReference,
    AggregateTradeMinuteState,
    AggregateTradeRangeManifest,
    ResearchEventValidationStatus,
    ValidatedAggregateTradeMinuteDataset,
    aggregate_trade_minute_primitives,
    aggregate_trade_archive_manifest_id,
)


class AggregateTradeMinuteAggregationError(ValueError):
    """A pinned source range cannot produce trustworthy minute state."""


def _iter_exact_range_events(
    catalog: AggregateTradePartitionCatalog,
    manifest: AggregateTradeRangeManifest,
) -> Iterator[AggregateTrade]:
    for reference in manifest.partitions:
        revisions = catalog.revisions(
            symbol=manifest.symbol,
            source_date=reference.logical_partition.source_date,
        )
        matches = tuple(
            item
            for item in revisions
            if aggregate_trade_archive_manifest_id(item) == reference.manifest_id
            and item.source_revision_id == reference.source_revision_id
        )
        if len(matches) != 1:
            raise AggregateTradeMinuteAggregationError(
                "pinned source revision became unavailable during aggregation"
            )
        archive = catalog.load(matches[0])
        if archive.manifest.dataset_id != reference.dataset_id:
            raise AggregateTradeMinuteAggregationError(
                "loaded source dataset differs from pinned range"
            )
        yield from archive.sequence.events


def aggregate_trade_minute_states(
    catalog: AggregateTradePartitionCatalog,
    source_range: AggregateTradeRangeManifest,
) -> ValidatedAggregateTradeMinuteDataset:
    """Aggregate one fully replayed exact daily range into a complete UTC grid."""

    if type(source_range) is not AggregateTradeRangeManifest:
        raise TypeError("source_range must be an AggregateTradeRangeManifest")
    try:
        replayed = replay_aggregate_trade_range(catalog, source_range)
    except (TypeError, ValueError) as error:
        raise AggregateTradeMinuteAggregationError(
            f"source range failed exact replay: {error}"
        ) from error
    first_partition = replayed.partitions[0].logical_partition
    source_references = tuple(
        AggregateTradeMinuteSourceReference.from_partition_reference(item)
        for item in replayed.partitions
    )
    start = datetime.combine(
        replayed.requested_start_date, time.min, tzinfo=timezone.utc
    )
    end = datetime.combine(
        replayed.requested_end_date_exclusive, time.min, tzinfo=timezone.utc
    )
    identity = AggregateTradeMinuteDatasetIdentity(
        provider=first_partition.provider,
        market=first_partition.market,
        source_event_family=first_partition.event_family,
        state_family=AGGREGATE_TRADE_MINUTE_STATE_FAMILY,
        symbol=replayed.symbol,
        requested_start_time=start,
        requested_end_time_exclusive=end,
        source_range_id=replayed.range_id,
        source_partitions=source_references,
        state_schema_version=AGGREGATE_TRADE_MINUTE_STATE_SCHEMA_VERSION,
        aggregation_version=AGGREGATE_TRADE_MINUTE_AGGREGATION_VERSION,
        minute_interval_specification=(
            AGGREGATE_TRADE_MINUTE_INTERVAL_SPECIFICATION
        ),
        availability_state=(
            AggregateTradeMinuteAvailabilityState.HISTORICAL_ONLY_LIVE_UNPROVEN
        ),
        validation_status=ResearchEventValidationStatus.VALIDATED,
    )
    references_by_date = {
        item.source_date: item for item in source_references
    }
    events = iter(_iter_exact_range_events(catalog, replayed))
    event = next(events, None)
    states: list[AggregateTradeMinuteState] = []
    observed_event_count = 0
    minute_start = start
    while minute_start < end:
        minute_end = minute_start + timedelta(minutes=1)
        reference = references_by_date[minute_start.date()]
        minute_events: list[AggregateTrade] = []
        while event is not None and event.event_time < minute_end:
            if event.event_time < minute_start:
                raise AggregateTradeMinuteAggregationError(
                    "source event falls before its deterministic minute bucket"
                )
            if (
                event.symbol != replayed.symbol
                or event.source_timestamp_unit is not reference.source_timestamp_unit
            ):
                raise AggregateTradeMinuteAggregationError(
                    "source event lineage differs from its daily partition"
                )
            minute_events.append(event)
            observed_event_count += 1
            event = next(events, None)
        primitives = aggregate_trade_minute_primitives(
            symbol=replayed.symbol,
            minute_start_time=minute_start,
            events=minute_events,
        )
        state = AggregateTradeMinuteState(
            symbol=replayed.symbol,
            minute_start_time=minute_start,
            minute_end_time_exclusive=minute_end,
            source_range_id=replayed.range_id,
            source_manifest_id=reference.manifest_id,
            source_revision_id=reference.source_revision_id,
            source_dataset_id=reference.dataset_id,
            source_timestamp_unit=reference.source_timestamp_unit,
            event_count=primitives.event_count,
            aggressive_buy_event_count=primitives.aggressive_buy_event_count,
            aggressive_sell_event_count=primitives.aggressive_sell_event_count,
            total_base_quantity=primitives.total_base_quantity,
            total_quote_notional=primitives.total_quote_notional,
            aggressive_buy_base_quantity=(
                primitives.aggressive_buy_base_quantity
            ),
            aggressive_sell_base_quantity=(
                primitives.aggressive_sell_base_quantity
            ),
            aggressive_buy_quote_notional=(
                primitives.aggressive_buy_quote_notional
            ),
            aggressive_sell_quote_notional=(
                primitives.aggressive_sell_quote_notional
            ),
            first_aggregate_trade_id=primitives.first_aggregate_trade_id,
            last_aggregate_trade_id=primitives.last_aggregate_trade_id,
            first_event_time=primitives.first_event_time,
            last_event_time=primitives.last_event_time,
            completeness_state=(
                AggregateTradeMinuteCompletenessState.VALIDATED_SOURCE_COMPLETE
            ),
            availability_state=(
                AggregateTradeMinuteAvailabilityState.HISTORICAL_ONLY_LIVE_UNPROVEN
            ),
            validation_status=ResearchEventValidationStatus.VALIDATED,
        )
        states.append(state)
        minute_start = minute_end
    if event is not None:
        raise AggregateTradeMinuteAggregationError(
            "source range contains an event outside requested coverage"
        )
    if observed_event_count != replayed.total_accepted_event_count:
        raise AggregateTradeMinuteAggregationError(
            "minute aggregation did not consume every pinned source event"
        )
    return ValidatedAggregateTradeMinuteDataset(identity, tuple(states))


def replay_aggregate_trade_minute_states(
    catalog: AggregateTradePartitionCatalog,
    source_range: AggregateTradeRangeManifest,
    expected: ValidatedAggregateTradeMinuteDataset,
) -> ValidatedAggregateTradeMinuteDataset:
    """Recompute and require exact state identity and canonical content."""

    if type(expected) is not ValidatedAggregateTradeMinuteDataset:
        raise TypeError("expected must be a ValidatedAggregateTradeMinuteDataset")
    ValidatedAggregateTradeMinuteDataset.__post_init__(expected)
    reproduced = aggregate_trade_minute_states(catalog, source_range)
    if (
        reproduced.identity != expected.identity
        or reproduced.content_sha256 != expected.content_sha256
        or reproduced.dataset_id != expected.dataset_id
        or reproduced.states != expected.states
    ):
        raise AggregateTradeMinuteAggregationError(
            "replayed minute-state dataset differs from expected dataset"
        )
    return reproduced

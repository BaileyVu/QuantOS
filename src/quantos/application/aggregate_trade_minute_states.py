"""Research-only orchestration for completed-minute aggregate-trade state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from quantos.application.aggregate_trade_ranges import (
    AggregateTradePartitionCatalog,
    DEFAULT_AGGREGATE_TRADE_BATCH_SIZE,
    _open_exact_range_batch_stream,
    _require_stream_matches_manifest,
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


@dataclass(slots=True)
class AggregateTradeStreamingDiagnostics:
    """Deterministic raw-event buffer bounds from one aggregation."""

    batch_size: int = 0
    max_batch_event_count: int = 0
    max_minute_event_count: int = 0
    max_raw_events_buffered: int = 0


def _exact_range_manifests(
    catalog: AggregateTradePartitionCatalog,
    manifest: AggregateTradeRangeManifest,
) -> tuple:
    selected = []
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
        selected_manifest = matches[0]
        if selected_manifest.dataset_id != reference.dataset_id:
            raise AggregateTradeMinuteAggregationError(
                "selected source dataset differs from pinned range"
            )
        selected.append(selected_manifest)
    return tuple(selected)


def aggregate_trade_minute_states(
    catalog: AggregateTradePartitionCatalog,
    source_range: AggregateTradeRangeManifest,
    *,
    batch_size: int = DEFAULT_AGGREGATE_TRADE_BATCH_SIZE,
    diagnostics: AggregateTradeStreamingDiagnostics | None = None,
) -> ValidatedAggregateTradeMinuteDataset:
    """Aggregate one fully replayed exact daily range into a complete UTC grid."""

    if type(source_range) is not AggregateTradeRangeManifest:
        raise TypeError("source_range must be an AggregateTradeRangeManifest")
    AggregateTradeRangeManifest.__post_init__(source_range)
    replayed = source_range
    try:
        manifests = _exact_range_manifests(catalog, replayed)
        stream = _open_exact_range_batch_stream(
            catalog, manifests, batch_size=batch_size
        )
    except (TypeError, ValueError) as error:
        raise AggregateTradeMinuteAggregationError(
            f"source range failed exact replay: {error}"
        ) from error
    if diagnostics is not None:
        if type(diagnostics) is not AggregateTradeStreamingDiagnostics:
            raise TypeError(
                "diagnostics must be AggregateTradeStreamingDiagnostics"
            )
        diagnostics.batch_size = batch_size
        diagnostics.max_batch_event_count = 0
        diagnostics.max_minute_event_count = 0
        diagnostics.max_raw_events_buffered = 0
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
    states: list[AggregateTradeMinuteState] = []
    observed_event_count = 0
    minute_events: list[AggregateTrade] = []

    def streamed_events():
        for batch in stream:
            if diagnostics is not None:
                diagnostics.max_batch_event_count = max(
                    diagnostics.max_batch_event_count, len(batch)
                )
                diagnostics.max_raw_events_buffered = max(
                    diagnostics.max_raw_events_buffered,
                    len(batch) + len(minute_events),
                )
            yield from batch

    events = iter(streamed_events())

    def next_event() -> AggregateTrade | None:
        try:
            return next(events, None)
        except (TypeError, ValueError) as error:
            raise AggregateTradeMinuteAggregationError(
                f"source range failed exact replay: {error}"
            ) from error

    event = next_event()
    minute_start = start
    while minute_start < end:
        minute_end = minute_start + timedelta(minutes=1)
        reference = references_by_date[minute_start.date()]
        minute_events.clear()
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
            if diagnostics is not None:
                diagnostics.max_minute_event_count = max(
                    diagnostics.max_minute_event_count,
                    len(minute_events),
                )
            observed_event_count += 1
            event = next_event()
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
    try:
        _require_stream_matches_manifest(stream.report, replayed)
    except (TypeError, ValueError) as error:
        raise AggregateTradeMinuteAggregationError(
            f"source range failed exact replay: {error}"
        ) from error
    return ValidatedAggregateTradeMinuteDataset(identity, tuple(states))


def replay_aggregate_trade_minute_states(
    catalog: AggregateTradePartitionCatalog,
    source_range: AggregateTradeRangeManifest,
    expected: ValidatedAggregateTradeMinuteDataset,
    *,
    batch_size: int = DEFAULT_AGGREGATE_TRADE_BATCH_SIZE,
) -> ValidatedAggregateTradeMinuteDataset:
    """Recompute and require exact state identity and canonical content."""

    if type(expected) is not ValidatedAggregateTradeMinuteDataset:
        raise TypeError("expected must be a ValidatedAggregateTradeMinuteDataset")
    ValidatedAggregateTradeMinuteDataset.__post_init__(expected)
    reproduced = aggregate_trade_minute_states(
        catalog, source_range, batch_size=batch_size
    )
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

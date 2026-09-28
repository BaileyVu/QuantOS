"""Research-only orchestration for completed-minute aggregate-trade state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from quantos.application.aggregate_trade_minute_primitive_cache import (
    AggregateTradeMinutePrimitiveCache,
    DailyAggregateTradeMinutePrimitives,
)
from quantos.application.aggregate_trade_ranges import (
    AggregateTradePartitionCatalog,
    DEFAULT_AGGREGATE_TRADE_BATCH_SIZE,
    _batch_size,
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
    cache_hit_partition_count: int = 0
    cache_miss_partition_count: int = 0
    cache_built_partition_count: int = 0
    cached_minute_rows_loaded: int = 0
    raw_events_consumed: int = 0
    warm_cache_used: bool = False


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


def _stream_daily_primitives(stream, manifests, diagnostics):
    """One exact stream of adjacent source days; one canonical bucketing rule."""
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
    event = next(events, None)
    days = []
    for manifest in manifests:
        rows = []
        minute_start = manifest.requested_start_time
        while minute_start < manifest.requested_end_time_exclusive:
            minute_end = minute_start + timedelta(minutes=1)
            minute_events.clear()
            while event is not None and event.event_time < minute_end:
                if event.event_time < minute_start:
                    raise AggregateTradeMinuteAggregationError(
                        "source event falls before its deterministic minute bucket"
                    )
                if (
                    event.symbol != manifest.symbol
                    or event.source_timestamp_unit is not manifest.source_timestamp_unit
                ):
                    raise AggregateTradeMinuteAggregationError(
                        "source event lineage differs from its daily partition"
                    )
                minute_events.append(event)
                if diagnostics is not None:
                    diagnostics.raw_events_consumed += 1
                    diagnostics.max_minute_event_count = max(
                        diagnostics.max_minute_event_count, len(minute_events)
                    )
                event = next(events, None)
            rows.append(aggregate_trade_minute_primitives(
                symbol=manifest.symbol,
                minute_start_time=minute_start,
                events=minute_events,
            ))
            minute_start = minute_end
        days.append(DailyAggregateTradeMinutePrimitives(manifest, tuple(rows)))
    if event is not None:
        raise AggregateTradeMinuteAggregationError(
            "source range contains an event outside requested coverage"
        )
    # Force complete stream verification before publishing any derived artifact.
    report = stream.report
    if (
        report.total_event_count != sum(item.accepted_row_count for item in manifests)
        or report.first_event_time != manifests[0].observed_first_event_time
        or report.last_event_time != manifests[-1].observed_last_event_time
    ):
        raise AggregateTradeMinuteAggregationError("source stream summary differs")
    return tuple(days)


def _daily_primitives(catalog, source_range, manifests, cache, batch_size, diagnostics):
    usable_cache = (
        callable(getattr(cache, "load", None))
        and callable(getattr(cache, "write", None))
    )
    verified_report = getattr(catalog, "verified_range_report", None)
    report = verified_report(manifests) if usable_cache and callable(verified_report) else None
    selected = {}
    if report is not None:
        # This is a whole-range proof, including exact cross-partition ID safety.
        # Verifying each cached day independently would not be sufficient.
        _require_stream_matches_manifest(report, source_range)
        for manifest in manifests:
            daily = cache.load(manifest)
            if daily is not None:
                if type(daily) is not DailyAggregateTradeMinutePrimitives or daily.source != manifest:
                    raise AggregateTradeMinuteAggregationError("cache returned invalid source lineage")
                DailyAggregateTradeMinutePrimitives.__post_init__(daily)
                selected[aggregate_trade_archive_manifest_id(manifest)] = daily
                if diagnostics is not None:
                    diagnostics.cache_hit_partition_count += 1
                    diagnostics.cached_minute_rows_loaded += len(daily.rows)
    missing = tuple(
        manifest for manifest in manifests
        if aggregate_trade_archive_manifest_id(manifest) not in selected
    )
    if usable_cache and diagnostics is not None:
        diagnostics.cache_miss_partition_count = len(missing)
        diagnostics.warm_cache_used = not missing
    if missing:
        # The existing boundary contract requires adjacent UTC dates. Stream
        # each contiguous missing run once, without replaying intervening hits.
        groups = []
        for manifest in missing:
            if not groups or manifest.source_date != groups[-1][-1].source_date + timedelta(days=1):
                groups.append([])
            groups[-1].append(manifest)
        built = []
        for group in groups:
            stream = _open_exact_range_batch_stream(catalog, tuple(group), batch_size=batch_size)
            built.extend(_stream_daily_primitives(stream, tuple(group), diagnostics))
            if report is None:
                # No O1 proof: all days replayed together, retaining exact fallback.
                _require_stream_matches_manifest(stream.report, source_range)
        for daily in built:
            if usable_cache:
                cache.write(daily)
                if diagnostics is not None:
                    diagnostics.cache_built_partition_count += 1
            selected[aggregate_trade_archive_manifest_id(daily.source)] = daily
    return tuple(selected[aggregate_trade_archive_manifest_id(item)] for item in manifests)


def aggregate_trade_minute_states(
    catalog: AggregateTradePartitionCatalog,
    source_range: AggregateTradeRangeManifest,
    *,
    batch_size: int = DEFAULT_AGGREGATE_TRADE_BATCH_SIZE,
    diagnostics: AggregateTradeStreamingDiagnostics | None = None,
    primitive_cache: AggregateTradeMinutePrimitiveCache | None = None,
) -> ValidatedAggregateTradeMinuteDataset:
    """Aggregate an exact UTC grid, optionally reusing verified daily primitives.

    No cache or an unsupported cache interface preserves full source replay.
    Corrupt entries fail closed; callers may explicitly remove/rebuild derived
    artifacts. Sources remain authoritative, including on fully warm reuse.
    """

    if type(source_range) is not AggregateTradeRangeManifest:
        raise TypeError("source_range must be an AggregateTradeRangeManifest")
    AggregateTradeRangeManifest.__post_init__(source_range)
    replayed = source_range
    try:
        manifests = _exact_range_manifests(catalog, replayed)
        _batch_size(batch_size)
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
        diagnostics.cache_hit_partition_count = 0
        diagnostics.cache_miss_partition_count = 0
        diagnostics.cache_built_partition_count = 0
        diagnostics.cached_minute_rows_loaded = 0
        diagnostics.raw_events_consumed = 0
        diagnostics.warm_cache_used = False
    try:
        days = _daily_primitives(
            catalog, replayed, manifests, primitive_cache, batch_size, diagnostics
        )
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
    states: list[AggregateTradeMinuteState] = []
    for primitives in (row for daily in days for row in daily.rows):
        minute_start = primitives.minute_start_time
        minute_end = primitives.minute_end_time_exclusive
        reference = references_by_date[minute_start.date()]
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
    if sum(state.event_count for state in states) != replayed.total_accepted_event_count:
        raise AggregateTradeMinuteAggregationError(
            "minute aggregation did not consume every pinned source event"
        )
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

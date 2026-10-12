"""Research-only orchestration for immutable aggregate-trade date ranges."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Protocol

from quantos.domain.market_data.research_events import (
    AGGREGATE_TRADE_RANGE_SCHEMA_VERSION,
    AGGREGATE_TRADE_REVISION_SELECTION_VERSION,
    AggregateTrade,
    AggregateTradeArchiveManifest,
    AggregateTradeBoundaryValidation,
    AggregateTradePartitionReference,
    AggregateTradeRangeManifest,
    AggregateTradeRangeRequest,
    ExactAggregateTradeRevision,
    RangeCompletenessState,
    ResearchEventValidationStatus,
    RevisionSelectionPolicy,
    ValidatedAggregateTradeArchive,
    aggregate_trade_archive_manifest_id,
    aggregate_trade_range_manifest_bytes,
    canonical_aggregate_trade_event_bytes,
)


DEFAULT_AGGREGATE_TRADE_BATCH_SIZE = 16_384


@dataclass(frozen=True, slots=True)
class AggregateTradeRangeStreamReport:
    """Compact evidence produced only after a complete range stream."""

    total_event_count: int
    first_event_time: datetime
    last_event_time: datetime
    boundaries: tuple[AggregateTradeBoundaryValidation, ...]
    max_batch_event_count: int


class AggregateTradeRangeBatchStream(Protocol):
    """Single-use exact range stream whose report appears after exhaustion."""

    def __iter__(self) -> Iterator[tuple[AggregateTrade, ...]]: ...

    @property
    def report(self) -> AggregateTradeRangeStreamReport: ...


class AggregateTradePartitionCatalog(Protocol):
    """Provider-neutral application port for verified immutable partitions."""

    def revisions(
        self, *, symbol: str, source_date: date
    ) -> tuple[AggregateTradeArchiveManifest, ...]:
        """Return every valid known revision for one logical partition."""

    def load(
        self, manifest: AggregateTradeArchiveManifest
    ) -> ValidatedAggregateTradeArchive:
        """Load and reverify one exact catalog manifest."""

    def stream_range(
        self,
        manifests: tuple[AggregateTradeArchiveManifest, ...],
        *,
        batch_size: int,
    ) -> AggregateTradeRangeBatchStream:
        """Open one verified ordered bounded-batch range stream."""


class AggregateTradeRangeCompositionError(ValueError):
    """A requested immutable event range cannot be composed safely."""


def _select_manifest(
    catalog: AggregateTradePartitionCatalog,
    request: AggregateTradeRangeRequest,
    *,
    source_date: date,
    exact: ExactAggregateTradeRevision | None,
) -> AggregateTradeArchiveManifest:
    revisions = tuple(
        catalog.revisions(symbol=request.symbol, source_date=source_date)
    )
    for manifest in revisions:
        if type(manifest) is not AggregateTradeArchiveManifest:
            raise AggregateTradeRangeCompositionError(
                "catalog returned a non-manifest revision"
            )
        AggregateTradeArchiveManifest.__post_init__(manifest)
        if (
            manifest.symbol != request.symbol
            or manifest.source_date != source_date
        ):
            raise AggregateTradeRangeCompositionError(
                "catalog returned conflicting logical partition metadata"
            )
    manifest_ids = tuple(
        aggregate_trade_archive_manifest_id(manifest)
        for manifest in revisions
    )
    if len(set(manifest_ids)) != len(manifest_ids):
        raise AggregateTradeRangeCompositionError(
            "catalog returned a duplicate manifest"
        )
    if request.selection_policy is RevisionSelectionPolicy.UNIQUE:
        if not revisions:
            raise AggregateTradeRangeCompositionError(
                f"missing partition {request.symbol} {source_date.isoformat()}"
            )
        if len(revisions) != 1:
            raise AggregateTradeRangeCompositionError(
                f"ambiguous partition {request.symbol} {source_date.isoformat()}"
            )
        return revisions[0]
    if exact is None or exact.source_date != source_date:
        raise AggregateTradeRangeCompositionError(
            f"missing exact revision selector for {source_date.isoformat()}"
        )
    matches = tuple(
        manifest
        for manifest in revisions
        if aggregate_trade_archive_manifest_id(manifest) == exact.manifest_id
        and manifest.source_revision_id == exact.source_revision_id
    )
    if len(matches) != 1:
        raise AggregateTradeRangeCompositionError(
            f"exact revision does not exist for {request.symbol} "
            f"{source_date.isoformat()}"
        )
    return matches[0]


def validate_aggregate_trade_partition_boundary(
    left_events: tuple[AggregateTrade, ...],
    right_events: tuple[AggregateTrade, ...],
    *,
    left_source_date: date,
    right_source_date: date,
) -> AggregateTradeBoundaryValidation:
    """Validate one adjacent boundary without sorting or continuity claims."""

    if (
        type(left_events) is not tuple
        or type(right_events) is not tuple
        or not left_events
        or not right_events
    ):
        raise AggregateTradeRangeCompositionError(
            "boundary validation requires two non-empty exact event tuples"
        )
    if any(type(event) is not AggregateTrade for event in left_events) or any(
        type(event) is not AggregateTrade for event in right_events
    ):
        raise AggregateTradeRangeCompositionError(
            "boundary validation requires canonical aggregate trades"
        )
    left_by_id = {event.aggregate_trade_id: event for event in left_events}
    duplicate_count = 0
    conflicting_count = 0
    overlap_count = 0
    for event in right_events:
        previous = left_by_id.get(event.aggregate_trade_id)
        if previous is not None:
            if previous == event:
                duplicate_count += 1
            else:
                conflicting_count += 1
            if previous.event_time == event.event_time:
                overlap_count += 1
    left_last = left_events[-1]
    right_first = right_events[0]
    ordered = (
        left_last.event_time,
        left_last.aggregate_trade_id,
    ) < (
        right_first.event_time,
        right_first.aggregate_trade_id,
    )
    if duplicate_count:
        raise AggregateTradeRangeCompositionError(
            f"duplicate aggregate trade IDs across partitions: {duplicate_count}"
        )
    if overlap_count:
        raise AggregateTradeRangeCompositionError(
            f"overlapping canonical event identities: {overlap_count}"
        )
    if conflicting_count:
        raise AggregateTradeRangeCompositionError(
            f"conflicting aggregate trade IDs across partitions: "
            f"{conflicting_count}"
        )
    if not ordered:
        raise AggregateTradeRangeCompositionError(
            "cross-partition chronological reversal"
        )
    return AggregateTradeBoundaryValidation(
        left_source_date=left_source_date,
        right_source_date=right_source_date,
        left_last_event_time=left_last.event_time,
        right_first_event_time=right_first.event_time,
        left_last_aggregate_trade_id=left_last.aggregate_trade_id,
        right_first_aggregate_trade_id=right_first.aggregate_trade_id,
        chronological_order_valid=True,
        duplicate_aggregate_id_count=0,
        conflicting_aggregate_id_count=0,
        overlapping_event_identity_count=0,
        observed_aggregate_id_delta=(
            right_first.aggregate_trade_id
            - left_last.aggregate_trade_id
        ),
        numerical_continuity_asserted=False,
    )


def _batch_size(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 1_000_000:
        raise AggregateTradeRangeCompositionError(
            "batch_size must be between 1 and 1000000"
        )
    return value


class _MaterializedCompatibilityRangeStream:
    """Compatibility fallback for catalogs without the DE1H streaming port."""

    def __init__(
        self,
        catalog: AggregateTradePartitionCatalog,
        manifests: tuple[AggregateTradeArchiveManifest, ...],
        *,
        batch_size: int,
    ) -> None:
        self._catalog = catalog
        self._manifests = manifests
        self._batch_size = _batch_size(batch_size)
        self._report: AggregateTradeRangeStreamReport | None = None
        self._started = False

    @property
    def report(self) -> AggregateTradeRangeStreamReport:
        if self._report is None:
            raise AggregateTradeRangeCompositionError(
                "range stream report is unavailable before complete exhaustion"
            )
        return self._report

    def __iter__(self) -> Iterator[tuple[AggregateTrade, ...]]:
        if self._started:
            raise AggregateTradeRangeCompositionError(
                "range stream is single-use"
            )
        self._started = True
        seen: dict[int, bytes] = {}
        boundaries: list[AggregateTradeBoundaryValidation] = []
        previous_events: tuple[AggregateTrade, ...] | None = None
        previous_source_date: date | None = None
        first_event_time: datetime | None = None
        last_event_time: datetime | None = None
        event_count = 0
        max_batch = 0
        for manifest in self._manifests:
            archive = self._catalog.load(manifest)
            if archive.manifest != manifest:
                raise AggregateTradeRangeCompositionError(
                    "loaded archive differs from selected catalog manifest"
                )
            events = archive.sequence.events
            if previous_events is not None and previous_source_date is not None:
                boundaries.append(
                    validate_aggregate_trade_partition_boundary(
                        previous_events,
                        events,
                        left_source_date=previous_source_date,
                        right_source_date=manifest.source_date,
                    )
                )
            for event in events:
                encoded = canonical_aggregate_trade_event_bytes(event)
                prior = seen.get(event.aggregate_trade_id)
                if prior is not None:
                    label = "duplicate" if prior == encoded else "conflicting"
                    raise AggregateTradeRangeCompositionError(
                        f"{label} aggregate trade ID across composed range "
                        f"{event.aggregate_trade_id}"
                    )
                seen[event.aggregate_trade_id] = encoded
            if first_event_time is None:
                first_event_time = events[0].event_time
            last_event_time = events[-1].event_time
            event_count += len(events)
            for offset in range(0, len(events), self._batch_size):
                batch = events[offset : offset + self._batch_size]
                max_batch = max(max_batch, len(batch))
                yield batch
            previous_events = events
            previous_source_date = manifest.source_date
        if first_event_time is None or last_event_time is None:
            raise AggregateTradeRangeCompositionError(
                "range stream contains no events"
            )
        self._report = AggregateTradeRangeStreamReport(
            total_event_count=event_count,
            first_event_time=first_event_time,
            last_event_time=last_event_time,
            boundaries=tuple(boundaries),
            max_batch_event_count=max_batch,
        )


def _open_exact_range_batch_stream(
    catalog: AggregateTradePartitionCatalog,
    manifests: tuple[AggregateTradeArchiveManifest, ...],
    *,
    batch_size: int,
) -> AggregateTradeRangeBatchStream:
    size = _batch_size(batch_size)
    factory = getattr(catalog, "stream_range", None)
    if callable(factory):
        return factory(manifests, batch_size=size)
    return _MaterializedCompatibilityRangeStream(
        catalog, manifests, batch_size=size
    )


def _require_stream_matches_manifest(
    report: AggregateTradeRangeStreamReport,
    manifest: AggregateTradeRangeManifest,
) -> None:
    if (
        report.total_event_count != manifest.total_accepted_event_count
        or report.first_event_time != manifest.observed_first_event_time
        or report.last_event_time != manifest.observed_last_event_time
        or report.boundaries != manifest.boundaries
    ):
        raise AggregateTradeRangeCompositionError(
            "streamed range evidence differs from pinned manifest"
        )


def compose_aggregate_trade_range(
    catalog: AggregateTradePartitionCatalog,
    request: AggregateTradeRangeRequest,
    *,
    batch_size: int = DEFAULT_AGGREGATE_TRADE_BATCH_SIZE,
) -> AggregateTradeRangeManifest:
    """Select, verify, and bind one complete UTC daily range."""

    if type(request) is not AggregateTradeRangeRequest:
        raise TypeError("request must be an AggregateTradeRangeRequest")
    AggregateTradeRangeRequest.__post_init__(request)
    exact_by_date = {
        item.source_date: item for item in request.exact_revisions
    }
    selected_manifests: list[AggregateTradeArchiveManifest] = []
    source_date = request.start_date
    while source_date < request.end_date_exclusive:
        manifest = _select_manifest(
            catalog,
            request,
            source_date=source_date,
            exact=exact_by_date.get(source_date),
        )
        if manifest.symbol != request.symbol or manifest.source_date != source_date:
            raise AggregateTradeRangeCompositionError(
                "selected manifest differs from requested logical partition"
            )
        selected_manifests.append(manifest)
        source_date += timedelta(days=1)

    stream = _open_exact_range_batch_stream(
        catalog,
        tuple(selected_manifests),
        batch_size=batch_size,
    )
    try:
        for _batch in stream:
            pass
        report = stream.report
    except (TypeError, ValueError) as error:
        raise AggregateTradeRangeCompositionError(
            f"selected range failed streamed content verification: {error}"
        ) from error

    references = tuple(
        AggregateTradePartitionReference.from_manifest(manifest)
        for manifest in selected_manifests
    )
    expected_event_count = sum(
        item.accepted_row_count for item in references
    )
    if (
        report.total_event_count != expected_event_count
        or report.first_event_time != references[0].observed_first_event_time
        or report.last_event_time != references[-1].observed_last_event_time
    ):
        raise AggregateTradeRangeCompositionError(
            "streamed range summary differs from selected manifests"
        )
    return AggregateTradeRangeManifest(
        symbol=request.symbol,
        requested_start_date=request.start_date,
        requested_end_date_exclusive=request.end_date_exclusive,
        selection_policy=request.selection_policy,
        selection_policy_version=(
            AGGREGATE_TRADE_REVISION_SELECTION_VERSION
        ),
        range_schema_version=AGGREGATE_TRADE_RANGE_SCHEMA_VERSION,
        expected_partition_count=(
            request.end_date_exclusive - request.start_date
        ).days,
        selected_partition_count=len(references),
        partitions=references,
        total_accepted_event_count=report.total_event_count,
        observed_first_event_time=report.first_event_time,
        observed_last_event_time=report.last_event_time,
        boundaries=report.boundaries,
        completeness_state=RangeCompletenessState.COMPLETE,
        validation_status=ResearchEventValidationStatus.VALIDATED,
    )


def replay_aggregate_trade_range(
    catalog: AggregateTradePartitionCatalog,
    manifest: AggregateTradeRangeManifest,
    *,
    batch_size: int = DEFAULT_AGGREGATE_TRADE_BATCH_SIZE,
) -> AggregateTradeRangeManifest:
    """Recompose an existing manifest using exact pinned revisions."""

    if type(manifest) is not AggregateTradeRangeManifest:
        raise TypeError("manifest must be an AggregateTradeRangeManifest")
    AggregateTradeRangeManifest.__post_init__(manifest)
    exact = tuple(
        ExactAggregateTradeRevision(
            source_date=item.logical_partition.source_date,
            manifest_id=item.manifest_id,
            source_revision_id=item.source_revision_id,
        )
        for item in manifest.partitions
    )
    reproduced = compose_aggregate_trade_range(
        catalog,
        AggregateTradeRangeRequest(
            symbol=manifest.symbol,
            start_date=manifest.requested_start_date,
            end_date_exclusive=manifest.requested_end_date_exclusive,
            selection_policy=RevisionSelectionPolicy.EXACT,
            exact_revisions=exact,
        ),
        batch_size=batch_size,
    )
    reproduced = replace(
        reproduced, selection_policy=manifest.selection_policy
    )
    if (
        reproduced.partitions != manifest.partitions
        or reproduced.boundaries != manifest.boundaries
        or reproduced.total_accepted_event_count
        != manifest.total_accepted_event_count
        or reproduced.observed_first_event_time
        != manifest.observed_first_event_time
        or reproduced.observed_last_event_time
        != manifest.observed_last_event_time
        or reproduced.range_id != manifest.range_id
    ):
        raise AggregateTradeRangeCompositionError(
            "replayed range differs from pinned manifest"
        )
    return reproduced


def iter_aggregate_trade_range(
    catalog: AggregateTradePartitionCatalog,
    manifest: AggregateTradeRangeManifest,
    *,
    batch_size: int = DEFAULT_AGGREGATE_TRADE_BATCH_SIZE,
) -> Iterator[AggregateTrade]:
    """Yield exact events by partition after complete pinned-range replay."""

    replayed = replay_aggregate_trade_range(
        catalog, manifest, batch_size=batch_size
    )
    manifests: list[AggregateTradeArchiveManifest] = []
    for reference in replayed.partitions:
        revisions = catalog.revisions(
            symbol=replayed.symbol,
            source_date=reference.logical_partition.source_date,
        )
        matches = tuple(
            item
            for item in revisions
            if aggregate_trade_archive_manifest_id(item)
            == reference.manifest_id
            and item.source_revision_id == reference.source_revision_id
        )
        if len(matches) != 1:
            raise AggregateTradeRangeCompositionError(
                "pinned partition became unavailable during range read"
            )
        manifests.append(matches[0])
    stream = _open_exact_range_batch_stream(
        catalog, tuple(manifests), batch_size=batch_size
    )
    for batch in stream:
        for event in batch:
            yield event
    _require_stream_matches_manifest(stream.report, replayed)


def canonical_range_manifest_bytes(
    manifest: AggregateTradeRangeManifest,
) -> bytes:
    """Expose canonical manifest bytes at the application boundary."""

    return aggregate_trade_range_manifest_bytes(manifest)

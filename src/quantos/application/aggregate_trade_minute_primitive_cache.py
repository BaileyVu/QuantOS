"""Optional source-bound daily acceleration artifacts, never source truth."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from quantos.domain.market_data.research_events import (
    AggregateTradeArchiveManifest,
    AggregateTradeMinutePrimitives,
)


@dataclass(frozen=True, slots=True)
class DailyAggregateTradeMinutePrimitives:
    """Exactly one verified source day; deliberately has no range identity."""

    source: AggregateTradeArchiveManifest
    rows: tuple[AggregateTradeMinutePrimitives, ...]

    def __post_init__(self) -> None:
        if type(self.source) is not AggregateTradeArchiveManifest:
            raise ValueError("daily primitives require an authoritative manifest")
        AggregateTradeArchiveManifest.__post_init__(self.source)
        if type(self.rows) is not tuple or len(self.rows) != 1440:
            raise ValueError("daily primitives require exactly 1440 minute rows")
        count = 0
        first = last = None
        for index, row in enumerate(self.rows):
            if type(row) is not AggregateTradeMinutePrimitives:
                raise ValueError("daily cache requires canonical minute primitives")
            # Immutable primitives validate their own scientific invariants at
            # construction; validate daily coverage and lineage here.
            if (
                row.symbol != self.source.symbol
                or row.minute_start_time
                != self.source.requested_start_time + timedelta(minutes=index)
            ):
                raise ValueError("daily primitive coverage or symbol differs from source")
            count += row.event_count
            if row.event_count:
                if first is None:
                    first = row.first_event_time
                last = row.last_event_time
        if (
            count != self.source.accepted_row_count
            or first != self.source.observed_first_event_time
            or last != self.source.observed_last_event_time
            or self.rows[-1].minute_end_time_exclusive
            != self.source.requested_end_time_exclusive
        ):
            raise ValueError("daily primitive summary differs from source manifest")


class AggregateTradeMinutePrimitiveCache(Protocol):
    """Derived cache port. Corruption raises; only an absent entry returns None.

    Implementations must verify exact lineage, semantic/storage versions,
    canonical metadata, row schema and cryptographic content identity on every
    read. Writes must be atomic and collision detecting. The application separately
    verifies authoritative source integrity before accepting cached rows.
    """

    def load(
        self, source: AggregateTradeArchiveManifest
    ) -> DailyAggregateTradeMinutePrimitives | None: ...

    def write(self, daily: DailyAggregateTradeMinutePrimitives) -> None: ...

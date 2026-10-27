"""Fail-closed validation for canonical aggregate-trade research sequences."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol

from quantos.domain.market_data.research_events.aggregate_trade import (
    AggregateTrade,
    AggregateTradeDatasetIdentity,
    ResearchEventValidationStatus,
)


class AggregateTradeValidationError(ValueError):
    """Raised when a research aggregate-trade sequence is not canonical."""


class ExactAggregateTradeIdRegistry(Protocol):
    """Exact duplicate registry whose storage policy is supplied externally."""

    def previous_or_add(
        self, aggregate_trade_id: int, canonical_event_bytes: bytes
    ) -> bytes | None:
        """Return prior exact bytes for an ID, or add the new identity."""


@dataclass(frozen=True, slots=True)
class IncrementalAggregateTradeValidationResult:
    """Compact validation evidence after one complete streamed partition."""

    identity: AggregateTradeDatasetIdentity
    event_count: int
    first_event: AggregateTrade
    last_event: AggregateTrade
    observed_numerical_id_gap_count: int


class IncrementalAggregateTradeSequenceValidator:
    """Preserve sequence invariants while retaining only adjacent event state."""

    __slots__ = (
        "_event_count",
        "_first_event",
        "_identity",
        "_last_event",
        "_observed_gap_count",
        "_registry",
    )

    def __init__(
        self,
        identity: AggregateTradeDatasetIdentity,
        registry: ExactAggregateTradeIdRegistry,
    ) -> None:
        if type(identity) is not AggregateTradeDatasetIdentity:
            raise TypeError("identity must be an AggregateTradeDatasetIdentity")
        AggregateTradeDatasetIdentity.__post_init__(identity)
        if identity.validation_status is not ResearchEventValidationStatus.UNVALIDATED:
            raise AggregateTradeValidationError("identity must be unvalidated")
        if not hasattr(registry, "previous_or_add"):
            raise TypeError("registry must provide exact duplicate detection")
        self._identity = identity
        self._registry = registry
        self._event_count = 0
        self._first_event: AggregateTrade | None = None
        self._last_event: AggregateTrade | None = None
        self._observed_gap_count = 0

    @property
    def event_count(self) -> int:
        return self._event_count

    def observe(
        self, event: AggregateTrade, canonical_event_bytes: bytes
    ) -> None:
        index = self._event_count
        if type(event) is not AggregateTrade:
            raise TypeError(f"events[{index}] must be an AggregateTrade")
        if type(canonical_event_bytes) is not bytes:
            raise TypeError("canonical_event_bytes must be exact bytes")
        try:
            AggregateTrade.__post_init__(event)
        except (AttributeError, OverflowError, TypeError, ValueError) as error:
            raise AggregateTradeValidationError(
                f"events[{index}] is invalid: {error}"
            ) from error
        identity = self._identity
        if event.symbol != identity.symbol:
            raise AggregateTradeValidationError(
                f"events[{index}] symbol does not match dataset identity"
            )
        if event.source_timestamp_unit is not identity.source_timestamp_unit:
            raise AggregateTradeValidationError(
                f"events[{index}] timestamp unit does not match dataset identity"
            )
        if not (
            identity.requested_start_time
            <= event.event_time
            < identity.requested_end_time_exclusive
        ):
            raise AggregateTradeValidationError(
                f"events[{index}] is outside requested half-open coverage"
            )
        prior = self._registry.previous_or_add(
            event.aggregate_trade_id, canonical_event_bytes
        )
        if prior is not None:
            if prior == canonical_event_bytes:
                raise AggregateTradeValidationError(
                    f"duplicate aggregate_trade_id {event.aggregate_trade_id}"
                )
            raise AggregateTradeValidationError(
                f"conflicting aggregate_trade_id {event.aggregate_trade_id}"
            )
        previous = self._last_event
        if previous is not None:
            if (
                event.event_time,
                event.aggregate_trade_id,
            ) <= (
                previous.event_time,
                previous.aggregate_trade_id,
            ):
                raise AggregateTradeValidationError(
                    "events must be strictly ordered by "
                    "(event_time, aggregate_trade_id); input is not sorted implicitly"
                )
            if event.aggregate_trade_id > previous.aggregate_trade_id + 1:
                self._observed_gap_count += 1
        if self._first_event is None:
            self._first_event = event
        self._last_event = event
        self._event_count += 1

    def result(self) -> IncrementalAggregateTradeValidationResult:
        if self._first_event is None or self._last_event is None:
            raise AggregateTradeValidationError(
                "aggregate-trade dataset must contain at least one event"
            )
        return IncrementalAggregateTradeValidationResult(
            identity=self._identity._validated_copy(),
            event_count=self._event_count,
            first_event=self._first_event,
            last_event=self._last_event,
            observed_numerical_id_gap_count=self._observed_gap_count,
        )


def _validate_sequence(
    identity: AggregateTradeDatasetIdentity,
    events: tuple[AggregateTrade, ...],
    *,
    required_status: ResearchEventValidationStatus,
) -> None:
    if type(identity) is not AggregateTradeDatasetIdentity:
        raise TypeError("identity must be an AggregateTradeDatasetIdentity")
    try:
        AggregateTradeDatasetIdentity.__post_init__(identity)
    except (AttributeError, OverflowError, TypeError, ValueError) as error:
        raise AggregateTradeValidationError(
            f"invalid aggregate-trade dataset identity: {error}"
        ) from error
    if identity.validation_status is not required_status:
        raise AggregateTradeValidationError(
            f"identity must be {required_status.value}"
        )
    if not events:
        raise AggregateTradeValidationError(
            "aggregate-trade dataset must contain at least one event"
        )

    previous_key: tuple[object, int] | None = None
    seen_by_id: dict[int, AggregateTrade] = {}
    for index, event in enumerate(events):
        if type(event) is not AggregateTrade:
            raise TypeError(f"events[{index}] must be an AggregateTrade")
        try:
            AggregateTrade.__post_init__(event)
        except (AttributeError, OverflowError, TypeError, ValueError) as error:
            raise AggregateTradeValidationError(
                f"events[{index}] is invalid: {error}"
            ) from error
        if event.symbol != identity.symbol:
            raise AggregateTradeValidationError(
                f"events[{index}] symbol does not match dataset identity"
            )
        if event.source_timestamp_unit is not identity.source_timestamp_unit:
            raise AggregateTradeValidationError(
                f"events[{index}] timestamp unit does not match dataset identity"
            )
        if not (
            identity.requested_start_time
            <= event.event_time
            < identity.requested_end_time_exclusive
        ):
            raise AggregateTradeValidationError(
                f"events[{index}] is outside requested half-open coverage"
            )

        prior_event = seen_by_id.get(event.aggregate_trade_id)
        if prior_event is not None:
            if prior_event == event:
                raise AggregateTradeValidationError(
                    f"duplicate aggregate_trade_id {event.aggregate_trade_id}"
                )
            raise AggregateTradeValidationError(
                f"conflicting aggregate_trade_id {event.aggregate_trade_id}"
            )
        seen_by_id[event.aggregate_trade_id] = event

        key = (event.event_time, event.aggregate_trade_id)
        if previous_key is not None and key <= previous_key:
            raise AggregateTradeValidationError(
                "events must be strictly ordered by "
                "(event_time, aggregate_trade_id); input is not sorted implicitly"
            )
        previous_key = key


@dataclass(frozen=True, slots=True)
class ValidatedAggregateTradeSequence:
    """Immutable non-empty aggregate-trade sequence with a validated identity."""

    identity: AggregateTradeDatasetIdentity
    events: tuple[AggregateTrade, ...]

    def __post_init__(self) -> None:
        try:
            sequence = tuple(self.events)
        except TypeError as error:
            raise TypeError("events must be an iterable of AggregateTrade values") from error
        _validate_sequence(
            self.identity,
            sequence,
            required_status=ResearchEventValidationStatus.VALIDATED,
        )
        object.__setattr__(self, "events", sequence)

    @property
    def observed_start_time(self) -> datetime:
        """Earliest event time; requested coverage remains in the identity."""

        return self.events[0].event_time

    @property
    def observed_end_time(self) -> datetime:
        """Latest event time; this is not an implied exclusive boundary."""

        return self.events[-1].event_time


def validate_aggregate_trade_sequence(
    identity: AggregateTradeDatasetIdentity,
    events: Iterable[AggregateTrade],
) -> ValidatedAggregateTradeSequence:
    """Validate authoritative input order and promote identity immutably.

    The function never sorts, deduplicates, or assumes aggregate-trade IDs are
    contiguous.  Acquisition-specific completeness belongs to a later phase.
    """

    if type(identity) is not AggregateTradeDatasetIdentity:
        raise TypeError("identity must be an AggregateTradeDatasetIdentity")
    if identity.validation_status is not ResearchEventValidationStatus.UNVALIDATED:
        raise AggregateTradeValidationError("identity must be unvalidated")
    try:
        materialized_events = tuple(events)
    except TypeError as error:
        raise TypeError("events must be an iterable of AggregateTrade values") from error
    _validate_sequence(
        identity,
        materialized_events,
        required_status=ResearchEventValidationStatus.UNVALIDATED,
    )
    return ValidatedAggregateTradeSequence(
        identity=identity._validated_copy(),
        events=materialized_events,
    )

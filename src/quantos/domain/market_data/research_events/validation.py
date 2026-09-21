"""Fail-closed validation for canonical aggregate-trade research sequences."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from quantos.domain.market_data.research_events.aggregate_trade import (
    AggregateTrade,
    AggregateTradeDatasetIdentity,
    ResearchEventValidationStatus,
)


class AggregateTradeValidationError(ValueError):
    """Raised when a research aggregate-trade sequence is not canonical."""


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

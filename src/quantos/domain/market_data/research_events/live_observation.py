"""Research-only live AggregateTrade availability and source-health contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from quantos.domain.market_data.research_events.aggregate_trade import (
    AggregateTrade,
    SourceTimestampUnit,
)
from quantos.domain.market_data.research_events.minute_primitives import (
    AggregateTradeMinutePrimitives,
)


_CANONICAL_V1_SYMBOL_SELECTIONS = (
    ("BTCUSDT",),
    ("ETHUSDT",),
    ("BTCUSDT", "ETHUSDT"),
)


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty canonical string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} must not contain control characters")
    return value


def _digest(value: object, name: str) -> str:
    result = _text(value, name)
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return result


def _utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise ValueError(f"{name} must be a built-in timezone-aware datetime")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be UTC")
    return value


def _count(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative exact integer")
    return value


def _delta_microseconds(later: datetime, earlier: datetime) -> int:
    delta = later - earlier
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


class LiveAggregateTradeConnectionKind(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    UNHEALTHY = "unhealthy"


class LiveAggregateTradeMinuteStatus(str, Enum):
    OPEN = "open"
    PROVISIONAL = "provisional"
    FINALIZED_UNDER_POLICY = "finalized_under_policy"
    INCOMPLETE_UNHEALTHY = "incomplete_unhealthy"


class LiveAggregateTradeAvailabilityState(str, Enum):
    LIVE_OBSERVED_POLICY_LIMITED = "live_observed_policy_limited"


class LiveAggregateTradeHealthStatus(str, Enum):
    ACTIVE = "active"
    HEALTHY_CAPTURE_COMPLETE = "healthy_capture_complete"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True, slots=True)
class ObservedAggregateTrade:
    """Canonical trade plus immutable provider and local availability evidence."""

    trade: AggregateTrade
    provider_event_timestamp: int
    provider_event_time: datetime
    provider_timestamp_unit: SourceTimestampUnit
    local_observation_time: datetime
    local_monotonic_ns: int
    source_identity: str
    stream_identity: str
    session_id: str
    receive_sequence: int
    normalization_version: str
    raw_payload_sha256: str

    def __post_init__(self) -> None:
        if type(self.trade) is not AggregateTrade:
            raise TypeError("trade must be an exact AggregateTrade")
        AggregateTrade.__post_init__(self.trade)
        if (
            type(self.provider_event_timestamp) is not int
            or self.provider_event_timestamp < 0
        ):
            raise ValueError("provider_event_timestamp must be non-negative")
        provider_time = _utc(self.provider_event_time, "provider_event_time")
        _utc(self.local_observation_time, "local_observation_time")
        if type(self.provider_timestamp_unit) is not SourceTimestampUnit:
            raise TypeError("provider_timestamp_unit must be explicit")
        if self.trade.source_timestamp_unit is not self.provider_timestamp_unit:
            raise ValueError("trade and provider timestamp units must match")
        if provider_time < self.trade.event_time:
            raise ValueError("provider event time cannot precede trade occurrence")
        if type(self.local_monotonic_ns) is not int or self.local_monotonic_ns < 0:
            raise ValueError("local_monotonic_ns must be non-negative")
        if type(self.receive_sequence) is not int or self.receive_sequence < 1:
            raise ValueError("receive_sequence must be a positive exact integer")
        for value, name in (
            (self.source_identity, "source_identity"),
            (self.stream_identity, "stream_identity"),
            (self.session_id, "session_id"),
            (self.normalization_version, "normalization_version"),
        ):
            _text(value, name)
        expected_stream = f"{self.trade.symbol.lower()}@aggTrade"
        if self.stream_identity != expected_stream:
            raise ValueError("stream identity differs from canonical trade symbol")
        _digest(self.raw_payload_sha256, "raw_payload_sha256")

    @property
    def provider_minus_trade_microseconds(self) -> int:
        return _delta_microseconds(
            self.provider_event_time, self.trade.event_time
        )

    @property
    def local_minus_provider_microseconds(self) -> int:
        return _delta_microseconds(
            self.local_observation_time, self.provider_event_time
        )

    @property
    def local_minus_trade_microseconds(self) -> int:
        return _delta_microseconds(
            self.local_observation_time, self.trade.event_time
        )


@dataclass(frozen=True, slots=True)
class LiveAggregateTradeConnectionEvent:
    """One connection lifecycle transition with causal observation time."""

    kind: LiveAggregateTradeConnectionKind
    session_id: str
    observed_at: datetime
    monotonic_ns: int
    endpoint: str
    symbols: tuple[str, ...]
    timestamp_unit: SourceTimestampUnit
    reconnect_count: int
    reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not LiveAggregateTradeConnectionKind:
            raise TypeError("connection kind must be explicit")
        _text(self.session_id, "session_id")
        _utc(self.observed_at, "observed_at")
        if type(self.monotonic_ns) is not int or self.monotonic_ns < 0:
            raise ValueError("monotonic_ns must be non-negative")
        _text(self.endpoint, "endpoint")
        if (
            type(self.symbols) is not tuple
            or not self.symbols
            or self.symbols not in _CANONICAL_V1_SYMBOL_SELECTIONS
        ):
            raise ValueError("symbols must be sorted unique V1 symbols")
        if type(self.timestamp_unit) is not SourceTimestampUnit:
            raise TypeError("timestamp_unit must be explicit")
        _count(self.reconnect_count, "reconnect_count")
        if self.kind is LiveAggregateTradeConnectionKind.CONNECTED:
            if self.reason is not None:
                raise ValueError("connected lifecycle events cannot have a reason")
        else:
            _text(self.reason, "reason")


@dataclass(frozen=True, slots=True)
class LiveAggregateTradeMinuteState:
    """Neutral minute totals plus policy-limited live availability evidence."""

    primitives: AggregateTradeMinutePrimitives
    status: LiveAggregateTradeMinuteStatus
    availability_state: LiveAggregateTradeAvailabilityState
    lateness_allowance_microseconds: int
    session_ids: tuple[str, ...]
    first_local_observation_time: datetime | None
    last_local_observation_time: datetime | None
    provider_event_watermark: datetime | None
    finalized_at: datetime | None
    invalid_reason: str | None

    def __post_init__(self) -> None:
        if type(self.primitives) is not AggregateTradeMinutePrimitives:
            raise TypeError("primitives must be exact neutral minute primitives")
        AggregateTradeMinutePrimitives.__post_init__(self.primitives)
        if type(self.status) is not LiveAggregateTradeMinuteStatus:
            raise TypeError("minute status must be explicit")
        if (
            self.availability_state
            is not LiveAggregateTradeAvailabilityState.LIVE_OBSERVED_POLICY_LIMITED
        ):
            raise ValueError("live minute state must remain policy-limited")
        _count(
            self.lateness_allowance_microseconds,
            "lateness_allowance_microseconds",
        )
        if type(self.session_ids) is not tuple or any(
            type(item) is not str or not item for item in self.session_ids
        ):
            raise ValueError("session_ids must be an exact tuple of identities")
        if len(set(self.session_ids)) != len(self.session_ids):
            raise ValueError("session_ids cannot contain duplicates")
        for value, name in (
            (self.first_local_observation_time, "first_local_observation_time"),
            (self.last_local_observation_time, "last_local_observation_time"),
            (self.provider_event_watermark, "provider_event_watermark"),
            (self.finalized_at, "finalized_at"),
        ):
            if value is not None:
                _utc(value, name)
        if (
            self.first_local_observation_time is None
        ) != (self.last_local_observation_time is None):
            raise ValueError("local observation boundaries must both be set or null")
        if (
            self.first_local_observation_time is not None
            and self.last_local_observation_time is not None
            and self.first_local_observation_time > self.last_local_observation_time
        ):
            raise ValueError("local observation boundaries are reversed")
        if self.status is LiveAggregateTradeMinuteStatus.FINALIZED_UNDER_POLICY:
            if self.finalized_at is None or self.provider_event_watermark is None:
                raise ValueError("finalized state requires policy evidence")
            if self.invalid_reason is not None:
                raise ValueError("finalized state cannot have an invalid reason")
        elif self.status is LiveAggregateTradeMinuteStatus.INCOMPLETE_UNHEALTHY:
            _text(self.invalid_reason, "invalid_reason")
        else:
            if self.finalized_at is not None or self.invalid_reason is not None:
                raise ValueError("open/provisional state cannot be final or invalid")


@dataclass(frozen=True, slots=True)
class LiveAggregateTradeSourceHealth:
    """Bounded aggregate source-health evidence for one capture."""

    endpoint: str
    symbols: tuple[str, ...]
    timestamp_unit: SourceTimestampUnit
    session_ids: tuple[str, ...]
    connected_at: datetime | None
    disconnected_at: datetime | None
    messages_received: int
    accepted_messages: int
    malformed_or_rejected_messages: int
    duplicate_messages: int
    conflicting_messages: int
    ordering_violations: int
    observed_id_gaps: int
    late_event_violations: int
    reconnect_count: int
    first_trade_occurrence_time: datetime | None
    last_trade_occurrence_time: datetime | None
    first_provider_event_time: datetime | None
    last_provider_event_time: datetime | None
    first_local_observation_time: datetime | None
    last_local_observation_time: datetime | None
    latency_observation_count: int
    provider_minus_trade_min_us: int | None
    provider_minus_trade_max_us: int | None
    provider_minus_trade_sum_us: int
    local_minus_provider_min_us: int | None
    local_minus_provider_max_us: int | None
    local_minus_provider_sum_us: int
    local_minus_trade_min_us: int | None
    local_minus_trade_max_us: int | None
    local_minus_trade_sum_us: int
    status: LiveAggregateTradeHealthStatus

    def __post_init__(self) -> None:
        _text(self.endpoint, "endpoint")
        if (
            type(self.symbols) is not tuple
            or not self.symbols
            or self.symbols not in _CANONICAL_V1_SYMBOL_SELECTIONS
        ):
            raise ValueError("symbols must be sorted V1 symbols")
        if type(self.timestamp_unit) is not SourceTimestampUnit:
            raise TypeError("timestamp_unit must be explicit")
        if type(self.session_ids) is not tuple or len(set(self.session_ids)) != len(
            self.session_ids
        ):
            raise ValueError("session_ids must be an exact unique tuple")
        for session_id in self.session_ids:
            _text(session_id, "session_id")
        for value, name in (
            (self.connected_at, "connected_at"),
            (self.disconnected_at, "disconnected_at"),
            (self.first_trade_occurrence_time, "first_trade_occurrence_time"),
            (self.last_trade_occurrence_time, "last_trade_occurrence_time"),
            (self.first_provider_event_time, "first_provider_event_time"),
            (self.last_provider_event_time, "last_provider_event_time"),
            (self.first_local_observation_time, "first_local_observation_time"),
            (self.last_local_observation_time, "last_local_observation_time"),
        ):
            if value is not None:
                _utc(value, name)
        count_names = (
            "messages_received",
            "accepted_messages",
            "malformed_or_rejected_messages",
            "duplicate_messages",
            "conflicting_messages",
            "ordering_violations",
            "observed_id_gaps",
            "late_event_violations",
            "reconnect_count",
            "latency_observation_count",
        )
        for name in count_names:
            _count(getattr(self, name), name)
        if self.messages_received != (
            self.accepted_messages
            + self.duplicate_messages
            + self.malformed_or_rejected_messages
        ):
            raise ValueError("message accounting is not exhaustive")
        if self.latency_observation_count != self.accepted_messages:
            raise ValueError("latency count must equal accepted messages")
        if type(self.status) is not LiveAggregateTradeHealthStatus:
            raise TypeError("source-health status must be explicit")
        latency_groups = (
            (
                self.provider_minus_trade_min_us,
                self.provider_minus_trade_max_us,
            ),
            (
                self.local_minus_provider_min_us,
                self.local_minus_provider_max_us,
            ),
            (
                self.local_minus_trade_min_us,
                self.local_minus_trade_max_us,
            ),
        )
        for minimum, maximum in latency_groups:
            if self.latency_observation_count == 0:
                if minimum is not None or maximum is not None:
                    raise ValueError("empty latency evidence requires null bounds")
            elif (
                type(minimum) is not int
                or type(maximum) is not int
                or minimum > maximum
            ):
                raise ValueError("latency bounds must be ordered exact integers")
        for value in (
            self.provider_minus_trade_sum_us,
            self.local_minus_provider_sum_us,
            self.local_minus_trade_sum_us,
        ):
            if type(value) is not int:
                raise ValueError("latency sums must be exact integers")

"""Causal research-only live AggregateTrade minute finalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from quantos.domain.common import V1_SYMBOLS
from quantos.domain.market_data.research_events import (
    AggregateTrade,
    AggregateTradeMinutePrimitives,
    LiveAggregateTradeAvailabilityState,
    LiveAggregateTradeConnectionEvent,
    LiveAggregateTradeConnectionKind,
    LiveAggregateTradeMinuteState,
    LiveAggregateTradeMinuteStatus,
    ObservedAggregateTrade,
    aggregate_trade_minute_primitives,
)


class LiveAggregateTradeMinuteEngineError(ValueError):
    """Live minute availability cannot be established safely."""


def _utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise LiveAggregateTradeMinuteEngineError(
            f"{name} must be a timezone-aware UTC datetime"
        )
    if value.utcoffset() != timedelta(0):
        raise LiveAggregateTradeMinuteEngineError(f"{name} must be UTC")
    return value


def _minute_start(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


@dataclass(slots=True)
class _MinuteRecord:
    symbol: str
    minute_start: datetime
    coverage_session_id: str | None
    events: list[AggregateTrade] = field(default_factory=list)
    session_ids: list[str] = field(default_factory=list)
    first_local_observation: datetime | None = None
    last_local_observation: datetime | None = None
    status: LiveAggregateTradeMinuteStatus = LiveAggregateTradeMinuteStatus.OPEN
    provider_watermark: datetime | None = None
    finalized_at: datetime | None = None
    invalid_reason: str | None = None
    final_primitives: AggregateTradeMinutePrimitives | None = None


class LiveAggregateTradeMinuteEngine:
    """Build policy-limited minute state from locally observed live messages.

    The per-symbol provider-event watermark must reach minute_end plus the
    configured research lateness allowance. Continuous connection coverage must
    start no later than the minute boundary and remain healthy through policy
    finalization. This policy is evidence, not an exchange completeness promise.
    """

    def __init__(
        self,
        *,
        symbols: Iterable[str],
        lateness_allowance: timedelta,
        max_retained_minutes: int = 1_440,
    ) -> None:
        if isinstance(symbols, (str, bytes)):
            raise ValueError("symbols must be a collection")
        try:
            selected = tuple(sorted(symbols))
        except TypeError as error:
            raise ValueError("symbols must be iterable") from error
        if (
            not selected
            or len(set(selected)) != len(selected)
            or any(type(symbol) is not str or symbol not in V1_SYMBOLS for symbol in selected)
        ):
            raise ValueError("symbols must be unique BTCUSDT/ETHUSDT values")
        if type(lateness_allowance) is not timedelta or lateness_allowance < timedelta(0):
            raise ValueError("lateness_allowance must be a non-negative timedelta")
        allowance_microseconds = (
            lateness_allowance.days * 86_400_000_000
            + lateness_allowance.seconds * 1_000_000
            + lateness_allowance.microseconds
        )
        if allowance_microseconds > 600_000_000:
            raise ValueError("lateness allowance must not exceed ten minutes")
        if type(max_retained_minutes) is not int or max_retained_minutes < 4:
            raise ValueError("max_retained_minutes must be an integer of at least four")
        self._symbols = selected
        self._allowance = lateness_allowance
        self._allowance_microseconds = allowance_microseconds
        self._max_retained = max_retained_minutes * len(selected)
        self._records: dict[tuple[str, datetime], _MinuteRecord] = {}
        self._capture_start: datetime | None = None
        self._materialized_through: datetime | None = None
        self._active_connection: LiveAggregateTradeConnectionEvent | None = None
        self._last_local_time: datetime | None = None
        self._last_monotonic_ns: int | None = None
        self._provider_watermarks: dict[str, datetime] = {}
        self._last_trades: dict[str, AggregateTrade] = {}
        self._late_event_violations = 0
        self._finalized_total = 0
        self._incomplete_total = 0
        self._evicted_finalized = 0
        self._evicted_incomplete = 0

    @property
    def lateness_allowance(self) -> timedelta:
        return self._allowance

    @property
    def late_event_violations(self) -> int:
        return self._late_event_violations

    @property
    def finalized_minute_count(self) -> int:
        return self._finalized_total

    @property
    def incomplete_minute_count(self) -> int:
        return self._incomplete_total

    def _check_clock(self, wall_time: datetime, monotonic_ns: int) -> None:
        wall = _utc(wall_time, "local observation time")
        if type(monotonic_ns) is not int or monotonic_ns < 0:
            raise LiveAggregateTradeMinuteEngineError(
                "monotonic clock value must be non-negative"
            )
        if (
            self._last_monotonic_ns is not None
            and monotonic_ns < self._last_monotonic_ns
        ):
            self._invalidate_open("local monotonic clock regressed")
            raise LiveAggregateTradeMinuteEngineError(
                "local monotonic clock regressed"
            )
        if self._last_local_time is not None and wall < self._last_local_time:
            self._invalidate_open("local UTC wall clock regressed")
            raise LiveAggregateTradeMinuteEngineError(
                "local UTC wall clock regressed"
            )
        self._last_local_time = wall
        self._last_monotonic_ns = monotonic_ns

    def _invalidate(self, record: _MinuteRecord, reason: str) -> None:
        if record.status is LiveAggregateTradeMinuteStatus.INCOMPLETE_UNHEALTHY:
            return
        if record.status is LiveAggregateTradeMinuteStatus.FINALIZED_UNDER_POLICY:
            self._finalized_total -= 1
        record.status = LiveAggregateTradeMinuteStatus.INCOMPLETE_UNHEALTHY
        record.invalid_reason = reason
        self._incomplete_total += 1

    def _invalidate_open(self, reason: str) -> None:
        for record in self._records.values():
            if record.status in (
                LiveAggregateTradeMinuteStatus.OPEN,
                LiveAggregateTradeMinuteStatus.PROVISIONAL,
            ):
                self._invalidate(record, reason)

    def _record_for(self, symbol: str, minute_start: datetime) -> _MinuteRecord:
        key = (symbol, minute_start)
        record = self._records.get(key)
        if record is not None:
            return record
        active = self._active_connection
        coverage_session = None
        if active is not None and active.observed_at <= minute_start:
            coverage_session = active.session_id
        record = _MinuteRecord(
            symbol=symbol,
            minute_start=minute_start,
            coverage_session_id=coverage_session,
        )
        if coverage_session is None:
            self._invalidate(record, "connection did not cover the minute start")
        else:
            record.session_ids.append(coverage_session)
        self._records[key] = record
        return record

    def _ensure_records(self, now: datetime) -> None:
        if self._capture_start is None:
            return
        target = _minute_start(now)
        current = self._materialized_through
        if current is None:
            current = _minute_start(self._capture_start)
        while current <= target:
            for symbol in self._symbols:
                self._record_for(symbol, current)
            current += timedelta(minutes=1)
        self._materialized_through = target + timedelta(minutes=1)
        self._prune()

    def _prune(self) -> None:
        if len(self._records) <= self._max_retained:
            return
        ordered = sorted(self._records)
        for key in ordered:
            if len(self._records) <= self._max_retained:
                break
            record = self._records[key]
            if record.status is LiveAggregateTradeMinuteStatus.FINALIZED_UNDER_POLICY:
                self._evicted_finalized += 1
            elif record.status is LiveAggregateTradeMinuteStatus.INCOMPLETE_UNHEALTHY:
                self._evicted_incomplete += 1
            else:
                continue
            del self._records[key]

    def process_connection(self, event: LiveAggregateTradeConnectionEvent) -> None:
        if type(event) is not LiveAggregateTradeConnectionEvent:
            raise TypeError("event must be a LiveAggregateTradeConnectionEvent")
        LiveAggregateTradeConnectionEvent.__post_init__(event)
        if event.symbols != self._symbols:
            raise LiveAggregateTradeMinuteEngineError(
                "connection symbols differ from engine scope"
            )
        self._check_clock(event.observed_at, event.monotonic_ns)
        if self._capture_start is None:
            self._capture_start = event.observed_at
        if event.kind is LiveAggregateTradeConnectionKind.CONNECTED:
            if self._active_connection is not None:
                self._invalidate_open("overlapping connection sessions")
                raise LiveAggregateTradeMinuteEngineError(
                    "a connection session is already active"
                )
            self._active_connection = event
            self._ensure_records(event.observed_at)
            self._advance_records(event.observed_at)
            return
        self._ensure_records(event.observed_at)
        self._advance_records(event.observed_at)
        if (
            self._active_connection is None
            or self._active_connection.session_id != event.session_id
        ):
            self._invalidate_open("unresolved connection lifecycle")
            raise LiveAggregateTradeMinuteEngineError(
                "disconnect does not match the active session"
            )
        reason = (
            "source-health failure: " + str(event.reason)
            if event.kind is LiveAggregateTradeConnectionKind.UNHEALTHY
            else "connection ended before policy finalization: " + str(event.reason)
        )
        for record in self._records.values():
            if (
                record.coverage_session_id == event.session_id
                and record.status
                in (
                    LiveAggregateTradeMinuteStatus.OPEN,
                    LiveAggregateTradeMinuteStatus.PROVISIONAL,
                )
            ):
                self._invalidate(record, reason)
        self._active_connection = None

    def process_observation(self, observation: ObservedAggregateTrade) -> None:
        if type(observation) is not ObservedAggregateTrade:
            raise TypeError("observation must be an ObservedAggregateTrade")
        ObservedAggregateTrade.__post_init__(observation)
        symbol = observation.trade.symbol
        if symbol not in self._symbols:
            raise LiveAggregateTradeMinuteEngineError("unexpected observation symbol")
        self._check_clock(
            observation.local_observation_time,
            observation.local_monotonic_ns,
        )
        self._ensure_records(observation.local_observation_time)
        self._advance_records(observation.local_observation_time)
        active = self._active_connection
        if active is None or active.session_id != observation.session_id:
            raise LiveAggregateTradeMinuteEngineError(
                "observation does not belong to the active connection"
            )
        trade = observation.trade
        minute_start = _minute_start(trade.event_time)
        record = self._record_for(symbol, minute_start)
        if record.status is LiveAggregateTradeMinuteStatus.FINALIZED_UNDER_POLICY:
            self._late_event_violations += 1
            self._invalidate(record, "event arrived after policy finalization")
            return
        if record.status is LiveAggregateTradeMinuteStatus.INCOMPLETE_UNHEALTHY:
            return
        previous = self._last_trades.get(symbol)
        if previous is not None:
            if trade.aggregate_trade_id == previous.aggregate_trade_id:
                if trade != previous:
                    self._invalidate(record, "conflicting repeated aggregate-trade ID")
                    raise LiveAggregateTradeMinuteEngineError(
                        "conflicting repeated aggregate-trade ID"
                    )
                return
            if (trade.event_time, trade.aggregate_trade_id) < (
                previous.event_time,
                previous.aggregate_trade_id,
            ):
                self._invalidate(record, "live aggregate-trade chronological reversal")
                raise LiveAggregateTradeMinuteEngineError(
                    "live aggregate-trade chronological reversal"
                )
        if record.coverage_session_id != observation.session_id:
            self._invalidate(record, "observation lacks continuous minute coverage")
            return
        record.events.append(trade)
        if observation.session_id not in record.session_ids:
            record.session_ids.append(observation.session_id)
        if record.first_local_observation is None:
            record.first_local_observation = observation.local_observation_time
        record.last_local_observation = observation.local_observation_time
        self._last_trades[symbol] = trade
        current_watermark = self._provider_watermarks.get(symbol)
        if current_watermark is None or observation.provider_event_time > current_watermark:
            self._provider_watermarks[symbol] = observation.provider_event_time
        self._advance_records(observation.local_observation_time)

    def _advance_records(self, now: datetime) -> None:
        for record in self._records.values():
            if record.status in (
                LiveAggregateTradeMinuteStatus.FINALIZED_UNDER_POLICY,
                LiveAggregateTradeMinuteStatus.INCOMPLETE_UNHEALTHY,
            ):
                continue
            minute_end = record.minute_start + timedelta(minutes=1)
            if now >= minute_end:
                record.status = LiveAggregateTradeMinuteStatus.PROVISIONAL
            threshold = minute_end + self._allowance
            watermark = self._provider_watermarks.get(record.symbol)
            active = self._active_connection
            if (
                now >= threshold
                and watermark is not None
                and watermark >= threshold
                and active is not None
                and record.coverage_session_id == active.session_id
                and active.observed_at <= record.minute_start
            ):
                record.final_primitives = aggregate_trade_minute_primitives(
                    symbol=record.symbol,
                    minute_start_time=record.minute_start,
                    events=record.events,
                )
                record.provider_watermark = watermark
                record.finalized_at = now
                record.status = (
                    LiveAggregateTradeMinuteStatus.FINALIZED_UNDER_POLICY
                )
                self._finalized_total += 1

    def advance_time(self, *, now: datetime, monotonic_ns: int) -> None:
        self._check_clock(now, monotonic_ns)
        self._ensure_records(now)
        self._advance_records(now)

    def close_capture(
        self,
        *,
        observed_at: datetime,
        monotonic_ns: int,
        reason: str = "bounded_capture_complete",
    ) -> None:
        active = self._active_connection
        if active is None:
            self.advance_time(now=observed_at, monotonic_ns=monotonic_ns)
            return
        self.process_connection(
            LiveAggregateTradeConnectionEvent(
                kind=LiveAggregateTradeConnectionKind.DISCONNECTED,
                session_id=active.session_id,
                observed_at=observed_at,
                monotonic_ns=monotonic_ns,
                endpoint=active.endpoint,
                symbols=active.symbols,
                timestamp_unit=active.timestamp_unit,
                reconnect_count=active.reconnect_count,
                reason=reason,
            )
        )

    def _state(self, record: _MinuteRecord) -> LiveAggregateTradeMinuteState:
        primitives = record.final_primitives
        if primitives is None:
            primitives = aggregate_trade_minute_primitives(
                symbol=record.symbol,
                minute_start_time=record.minute_start,
                events=record.events,
            )
        return LiveAggregateTradeMinuteState(
            primitives=primitives,
            status=record.status,
            availability_state=(
                LiveAggregateTradeAvailabilityState.LIVE_OBSERVED_POLICY_LIMITED
            ),
            lateness_allowance_microseconds=self._allowance_microseconds,
            session_ids=tuple(record.session_ids),
            first_local_observation_time=record.first_local_observation,
            last_local_observation_time=record.last_local_observation,
            provider_event_watermark=record.provider_watermark,
            finalized_at=record.finalized_at,
            invalid_reason=record.invalid_reason,
        )

    def states(self) -> tuple[LiveAggregateTradeMinuteState, ...]:
        return tuple(
            self._state(self._records[key]) for key in sorted(self._records)
        )

    def state(
        self, *, symbol: str, minute_start_time: datetime
    ) -> LiveAggregateTradeMinuteState:
        if symbol not in self._symbols:
            raise LiveAggregateTradeMinuteEngineError("symbol is outside engine scope")
        minute = _utc(minute_start_time, "minute_start_time")
        if minute.second or minute.microsecond:
            raise LiveAggregateTradeMinuteEngineError(
                "minute_start_time must be minute-aligned"
            )
        try:
            return self._state(self._records[(symbol, minute)])
        except KeyError as error:
            raise LiveAggregateTradeMinuteEngineError(
                "minute is not present in retained capture state"
            ) from error

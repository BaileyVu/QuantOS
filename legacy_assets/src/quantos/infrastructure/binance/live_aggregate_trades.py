"""Bounded research-only Binance Spot live AggregateTrade observation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import ssl
import time
from typing import Protocol
from uuid import uuid4

from websockets.asyncio.client import connect, process_exception
from websockets.exceptions import ConnectionClosed, WebSocketException

from quantos.domain.common import V1_SYMBOLS
from quantos.domain.market_data.research_events import (
    LiveAggregateTradeConnectionEvent,
    LiveAggregateTradeConnectionKind,
    LiveAggregateTradeHealthStatus,
    LiveAggregateTradeSourceHealth,
    ObservedAggregateTrade,
    SourceTimestampUnit,
    normalize_source_timestamp,
)
from quantos.infrastructure.binance.aggregate_trades import (
    AGGREGATE_TRADE_NORMALIZER_VERSION,
    BinanceAggregateTradeNormalizationError,
    normalize_websocket_aggregate_trade,
)

BINANCE_AGGREGATE_TRADE_LIVE_ENDPOINT = "wss://data-stream.binance.vision"
BINANCE_AGGREGATE_TRADE_LIVE_SOURCE_VERSION = (
    "binance-spot-live-aggregate-trade-v1"
)
_INITIAL_RECONNECT_DELAY = 1.0
_MAX_RECONNECT_DELAY = 30.0
_RECEIVE_WATCHDOG_SECONDS = 30.0


class BinanceLiveAggregateTradeError(ValueError):
    """Live aggregate-trade transport or integrity failed closed."""


class _ServerShutdown(Exception):
    pass


class _ReceiveWatchdogTimeout(Exception):
    pass


class _Connection(Protocol):
    async def recv(self) -> str | bytes: ...


_Connector = Callable[[str], AbstractAsyncContextManager[_Connection]]
_Sleep = Callable[[float], Awaitable[None]]
_WallClock = Callable[[], datetime]
_MonotonicClock = Callable[[], int]
_SessionFactory = Callable[[], str]


def _connect(url: str) -> AbstractAsyncContextManager[_Connection]:
    # Binance server Ping frames still receive automatic echoed Pong frames.
    return connect(
        url,
        ping_interval=None,
        proxy=None,
        open_timeout=10,
        close_timeout=10,
        max_size=16_384,
        max_queue=32,
    )


def _retryable_transport(error: Exception) -> bool:
    if isinstance(error, ssl.SSLError):
        return False
    if isinstance(error, ConnectionClosed):
        fatal_codes = {1002, 1003, 1007, 1008, 1009, 1010}
        return all(
            frame is None or frame.code not in fatal_codes
            for frame in (error.rcvd, error.sent)
        )
    return isinstance(error, EOFError) or process_exception(error) is None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BinanceLiveAggregateTradeError(
                "ambiguous duplicate JSON field"
            )
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise BinanceLiveAggregateTradeError(
        "non-standard JSON numeric constant"
    )


def _raw_bytes(message: str | bytes) -> bytes:
    if type(message) is bytes:
        try:
            message.decode("utf-8")
        except UnicodeDecodeError as error:
            raise BinanceLiveAggregateTradeError(
                "WebSocket payload must be UTF-8"
            ) from error
        return message
    if type(message) is str:
        return message.encode("utf-8")
    raise BinanceLiveAggregateTradeError(
        "WebSocket payload must be text or UTF-8 bytes"
    )


def normalize_live_aggregate_trade_message(
    message: str | bytes,
    *,
    expected_streams: frozenset[str],
    source_identity: str,
    session_id: str,
    receive_sequence: int,
    local_observation_time: datetime,
    local_monotonic_ns: int,
    source_timestamp_unit: SourceTimestampUnit,
) -> ObservedAggregateTrade:
    """Normalize one combined stream message after capture clocks are sampled."""

    raw = _raw_bytes(message)
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise BinanceLiveAggregateTradeError(
            "invalid or ambiguous Binance JSON message"
        ) from error
    if type(decoded) is not dict or set(decoded) != {"stream", "data"}:
        raise BinanceLiveAggregateTradeError(
            "combined stream wrapper must contain exactly stream and data"
        )
    stream = decoded["stream"]
    data = decoded["data"]
    if type(stream) is not str or type(data) is not dict:
        raise BinanceLiveAggregateTradeError(
            "combined stream identity/data types are invalid"
        )
    if stream == "!serverShutdown":
        if set(data) != {"e", "E"} or data.get("e") != "serverShutdown":
            raise BinanceLiveAggregateTradeError(
                "invalid server-shutdown control message"
            )
        raise _ServerShutdown
    if stream not in expected_streams:
        raise BinanceLiveAggregateTradeError(
            "unexpected or unsubscribed aggregate-trade stream"
        )
    try:
        trade = normalize_websocket_aggregate_trade(
            data,
            source_timestamp_unit=source_timestamp_unit,
        )
        provider_timestamp = data["E"]
        if type(provider_timestamp) is not int:
            raise BinanceLiveAggregateTradeError(
                "provider event timestamp must be an integer"
            )
        provider_time = normalize_source_timestamp(
            provider_timestamp, source_timestamp_unit
        )
        return ObservedAggregateTrade(
            trade=trade,
            provider_event_timestamp=provider_timestamp,
            provider_event_time=provider_time,
            provider_timestamp_unit=source_timestamp_unit,
            local_observation_time=local_observation_time,
            local_monotonic_ns=local_monotonic_ns,
            source_identity=source_identity,
            stream_identity=stream,
            session_id=session_id,
            receive_sequence=receive_sequence,
            normalization_version=AGGREGATE_TRADE_NORMALIZER_VERSION,
            raw_payload_sha256=sha256(raw).hexdigest(),
        )
    except BinanceLiveAggregateTradeError:
        raise
    except (BinanceAggregateTradeNormalizationError, TypeError, ValueError) as error:
        raise BinanceLiveAggregateTradeError(
            f"invalid aggregate-trade stream payload: {error}"
        ) from error


class _HealthAccumulator:
    def __init__(self, endpoint: str, symbols: tuple[str, ...]) -> None:
        self.endpoint = endpoint
        self.symbols = symbols
        self.sessions: list[str] = []
        self.connected_at: datetime | None = None
        self.disconnected_at: datetime | None = None
        self.messages = 0
        self.accepted = 0
        self.rejected = 0
        self.duplicates = 0
        self.conflicts = 0
        self.ordering = 0
        self.gaps = 0
        self.reconnects = 0
        self.first_trade: datetime | None = None
        self.last_trade: datetime | None = None
        self.first_provider: datetime | None = None
        self.last_provider: datetime | None = None
        self.first_local: datetime | None = None
        self.last_local: datetime | None = None
        self.latency_count = 0
        self.provider_trade_min: int | None = None
        self.provider_trade_max: int | None = None
        self.provider_trade_sum = 0
        self.local_provider_min: int | None = None
        self.local_provider_max: int | None = None
        self.local_provider_sum = 0
        self.local_trade_min: int | None = None
        self.local_trade_max: int | None = None
        self.local_trade_sum = 0
        self.unhealthy = False
        self.complete = False

    @staticmethod
    def _bounds(
        minimum: int | None, maximum: int | None, value: int
    ) -> tuple[int, int]:
        return (
            value if minimum is None else min(minimum, value),
            value if maximum is None else max(maximum, value),
        )

    def accept(self, observation: ObservedAggregateTrade) -> None:
        self.accepted += 1
        trade_time = observation.trade.event_time
        provider_time = observation.provider_event_time
        local_time = observation.local_observation_time
        if self.first_trade is None:
            self.first_trade = trade_time
            self.first_provider = provider_time
            self.first_local = local_time
        self.last_trade = trade_time
        self.last_provider = provider_time
        self.last_local = local_time
        values = (
            observation.provider_minus_trade_microseconds,
            observation.local_minus_provider_microseconds,
            observation.local_minus_trade_microseconds,
        )
        self.provider_trade_min, self.provider_trade_max = self._bounds(
            self.provider_trade_min, self.provider_trade_max, values[0]
        )
        self.local_provider_min, self.local_provider_max = self._bounds(
            self.local_provider_min, self.local_provider_max, values[1]
        )
        self.local_trade_min, self.local_trade_max = self._bounds(
            self.local_trade_min, self.local_trade_max, values[2]
        )
        self.provider_trade_sum += values[0]
        self.local_provider_sum += values[1]
        self.local_trade_sum += values[2]
        self.latency_count += 1

    def snapshot(self, *, late_events: int) -> LiveAggregateTradeSourceHealth:
        status = LiveAggregateTradeHealthStatus.ACTIVE
        if self.unhealthy:
            status = LiveAggregateTradeHealthStatus.UNHEALTHY
        elif self.complete:
            status = LiveAggregateTradeHealthStatus.HEALTHY_CAPTURE_COMPLETE
        return LiveAggregateTradeSourceHealth(
            endpoint=self.endpoint,
            symbols=self.symbols,
            timestamp_unit=SourceTimestampUnit.MICROSECOND,
            session_ids=tuple(self.sessions),
            connected_at=self.connected_at,
            disconnected_at=self.disconnected_at,
            messages_received=self.messages,
            accepted_messages=self.accepted,
            malformed_or_rejected_messages=self.rejected,
            duplicate_messages=self.duplicates,
            conflicting_messages=self.conflicts,
            ordering_violations=self.ordering,
            observed_id_gaps=self.gaps,
            late_event_violations=late_events,
            reconnect_count=self.reconnects,
            first_trade_occurrence_time=self.first_trade,
            last_trade_occurrence_time=self.last_trade,
            first_provider_event_time=self.first_provider,
            last_provider_event_time=self.last_provider,
            first_local_observation_time=self.first_local,
            last_local_observation_time=self.last_local,
            latency_observation_count=self.latency_count,
            provider_minus_trade_min_us=self.provider_trade_min,
            provider_minus_trade_max_us=self.provider_trade_max,
            provider_minus_trade_sum_us=self.provider_trade_sum,
            local_minus_provider_min_us=self.local_provider_min,
            local_minus_provider_max_us=self.local_provider_max,
            local_minus_provider_sum_us=self.local_provider_sum,
            local_minus_trade_min_us=self.local_trade_min,
            local_minus_trade_max_us=self.local_trade_max,
            local_minus_trade_sum_us=self.local_trade_sum,
            status=status,
        )


class BinanceSpotLiveAggregateTradeAdapter(
    AsyncIterator[ObservedAggregateTrade | LiveAggregateTradeConnectionEvent]
):
    """One bounded-reconnect combined public aggTrade research capture."""

    def __init__(
        self,
        *,
        symbols: Iterable[str],
        connector: _Connector | None = None,
        sleep: _Sleep | None = None,
        wall_clock: _WallClock | None = None,
        monotonic_clock: _MonotonicClock | None = None,
        session_factory: _SessionFactory | None = None,
        max_reconnect_attempts: int = 8,
        max_observed_ids: int = 1_000_000,
    ) -> None:
        if isinstance(symbols, (str, bytes)):
            raise ValueError("symbols must be a non-empty collection")
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
        if type(max_reconnect_attempts) is not int or not 0 <= max_reconnect_attempts <= 20:
            raise ValueError("max_reconnect_attempts must be between zero and twenty")
        if type(max_observed_ids) is not int or max_observed_ids < 1:
            raise ValueError("max_observed_ids must be positive")
        streams = tuple(f"{symbol.lower()}@aggTrade" for symbol in selected)
        self._symbols = selected
        self._streams = frozenset(streams)
        self._url = (
            f"{BINANCE_AGGREGATE_TRADE_LIVE_ENDPOINT}/stream?"
            f"streams={'/'.join(streams)}&timeUnit=MICROSECOND"
        )
        self._connector = _connect if connector is None else connector
        self._sleep = asyncio.sleep if sleep is None else sleep
        self._wall_clock = (
            (lambda: datetime.now(timezone.utc))
            if wall_clock is None
            else wall_clock
        )
        self._monotonic_clock = (
            time.monotonic_ns if monotonic_clock is None else monotonic_clock
        )
        self._session_factory = (
            (lambda: uuid4().hex)
            if session_factory is None
            else session_factory
        )
        self._max_reconnect_attempts = max_reconnect_attempts
        self._max_observed_ids = max_observed_ids
        self._seen: dict[str, dict[int, object]] = {
            symbol: {} for symbol in selected
        }
        self._last = {}
        self._health = _HealthAccumulator(self._url, selected)
        self._iterator = self._events()

    @property
    def url(self) -> str:
        return self._url

    def health_snapshot(
        self, *, late_event_violations: int = 0
    ) -> LiveAggregateTradeSourceHealth:
        return self._health.snapshot(late_events=late_event_violations)

    def mark_capture_complete(
        self, *, disconnected_at: datetime | None = None
    ) -> None:
        ended = self._wall_clock() if disconnected_at is None else disconnected_at
        if type(ended) is not datetime or ended.tzinfo is None:
            raise ValueError("disconnected_at must be timezone-aware UTC")
        if ended.utcoffset() != timedelta(0):
            raise ValueError("disconnected_at must be UTC")
        self._health.disconnected_at = ended
        if not self._health.unhealthy:
            self._health.complete = True

    def __aiter__(self) -> BinanceSpotLiveAggregateTradeAdapter:
        return self

    async def __anext__(
        self,
    ) -> ObservedAggregateTrade | LiveAggregateTradeConnectionEvent:
        return await anext(self._iterator)

    async def aclose(self) -> None:
        await self._iterator.aclose()

    def _lifecycle(
        self,
        *,
        kind: LiveAggregateTradeConnectionKind,
        session_id: str,
        reconnect_count: int,
        reason: str | None = None,
    ) -> LiveAggregateTradeConnectionEvent:
        observed_at = self._wall_clock()
        monotonic_ns = self._monotonic_clock()
        if kind is LiveAggregateTradeConnectionKind.CONNECTED:
            if self._health.connected_at is None:
                self._health.connected_at = observed_at
            self._health.sessions.append(session_id)
        else:
            self._health.disconnected_at = observed_at
        return LiveAggregateTradeConnectionEvent(
            kind=kind,
            session_id=session_id,
            observed_at=observed_at,
            monotonic_ns=monotonic_ns,
            endpoint=self._url,
            symbols=self._symbols,
            timestamp_unit=SourceTimestampUnit.MICROSECOND,
            reconnect_count=reconnect_count,
            reason=reason,
        )

    async def _events(
        self,
    ) -> AsyncIterator[ObservedAggregateTrade | LiveAggregateTradeConnectionEvent]:
        delay = _INITIAL_RECONNECT_DELAY
        failures = 0
        reconnect_count = 0
        while True:
            session_id = self._session_factory()
            receive_sequence = 0
            reason = "transport_closed"
            connected = False
            try:
                async with self._connector(self._url) as connection:
                    connected = True
                    yield self._lifecycle(
                        kind=LiveAggregateTradeConnectionKind.CONNECTED,
                        session_id=session_id,
                        reconnect_count=reconnect_count,
                    )
                    while True:
                        try:
                            async with asyncio.timeout(_RECEIVE_WATCHDOG_SECONDS):
                                message = await connection.recv()
                        except TimeoutError as error:
                            raise _ReceiveWatchdogTimeout from error
                        local_time = self._wall_clock()
                        monotonic_ns = self._monotonic_clock()
                        receive_sequence += 1
                        self._health.messages += 1
                        try:
                            observation = normalize_live_aggregate_trade_message(
                                message,
                                expected_streams=self._streams,
                                source_identity=self._url,
                                session_id=session_id,
                                receive_sequence=receive_sequence,
                                local_observation_time=local_time,
                                local_monotonic_ns=monotonic_ns,
                                source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
                            )
                        except _ServerShutdown:
                            self._health.rejected += 1
                            reason = "server_shutdown"
                            break
                        except BinanceLiveAggregateTradeError as error:
                            self._health.rejected += 1
                            self._health.unhealthy = True
                            yield self._lifecycle(
                                kind=LiveAggregateTradeConnectionKind.UNHEALTHY,
                                session_id=session_id,
                                reconnect_count=reconnect_count,
                                reason=str(error),
                            )
                            raise
                        seen = self._seen[observation.trade.symbol]
                        previous_for_id = seen.get(
                            observation.trade.aggregate_trade_id
                        )
                        if previous_for_id is not None:
                            if previous_for_id == observation.trade:
                                self._health.duplicates += 1
                                continue
                            self._health.rejected += 1
                            self._health.conflicts += 1
                            self._health.unhealthy = True
                            error = BinanceLiveAggregateTradeError(
                                "conflicting repeated aggregate-trade ID"
                            )
                            yield self._lifecycle(
                                kind=LiveAggregateTradeConnectionKind.UNHEALTHY,
                                session_id=session_id,
                                reconnect_count=reconnect_count,
                                reason=str(error),
                            )
                            raise error
                        previous = self._last.get(observation.trade.symbol)
                        if previous is not None and (
                            observation.trade.event_time,
                            observation.trade.aggregate_trade_id,
                        ) <= (
                            previous.event_time,
                            previous.aggregate_trade_id,
                        ):
                            self._health.rejected += 1
                            self._health.ordering += 1
                            self._health.unhealthy = True
                            error = BinanceLiveAggregateTradeError(
                                "live aggregate-trade chronological reversal"
                            )
                            yield self._lifecycle(
                                kind=LiveAggregateTradeConnectionKind.UNHEALTHY,
                                session_id=session_id,
                                reconnect_count=reconnect_count,
                                reason=str(error),
                            )
                            raise error
                        if len(seen) >= self._max_observed_ids:
                            self._health.rejected += 1
                            self._health.unhealthy = True
                            error = BinanceLiveAggregateTradeError(
                                "bounded duplicate-identity capacity exhausted"
                            )
                            yield self._lifecycle(
                                kind=LiveAggregateTradeConnectionKind.UNHEALTHY,
                                session_id=session_id,
                                reconnect_count=reconnect_count,
                                reason=str(error),
                            )
                            raise error
                        if (
                            previous is not None
                            and observation.trade.aggregate_trade_id
                            > previous.aggregate_trade_id + 1
                        ):
                            self._health.gaps += 1
                        seen[observation.trade.aggregate_trade_id] = observation.trade
                        self._last[observation.trade.symbol] = observation.trade
                        self._health.accept(observation)
                        failures = 0
                        delay = _INITIAL_RECONNECT_DELAY
                        yield observation
            except _ReceiveWatchdogTimeout:
                reason = "receive_watchdog_timeout"
            except (OSError, EOFError, WebSocketException) as error:
                if not _retryable_transport(error):
                    self._health.unhealthy = True
                    raise BinanceLiveAggregateTradeError(
                        "fatal WebSocket protocol/connection failure: "
                        f"{type(error).__name__}"
                    ) from error
                reason = type(error).__name__
            if connected:
                yield self._lifecycle(
                    kind=LiveAggregateTradeConnectionKind.DISCONNECTED,
                    session_id=session_id,
                    reconnect_count=reconnect_count,
                    reason=reason,
                )
            failures += 1
            if failures > self._max_reconnect_attempts:
                self._health.unhealthy = True
                raise BinanceLiveAggregateTradeError(
                    "bounded reconnect attempts exhausted"
                )
            await self._sleep(delay)
            delay = min(delay * 2, _MAX_RECONNECT_DELAY)
            reconnect_count += 1
            self._health.reconnects = reconnect_count

"""Offline Binance live AggregateTrade transport and envelope tests."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import json
import unittest

from websockets.exceptions import ConnectionClosed

from quantos.domain.market_data.research_events import (
    LiveAggregateTradeConnectionEvent,
    LiveAggregateTradeConnectionKind,
    LiveAggregateTradeHealthStatus,
    ObservedAggregateTrade,
    SourceTimestampUnit,
)
from quantos.infrastructure.binance import (
    AGGREGATE_TRADE_NORMALIZER_VERSION,
    BinanceLiveAggregateTradeError,
    BinanceSpotLiveAggregateTradeAdapter,
    normalize_live_aggregate_trade_message,
)


UTC = timezone.utc
BASE = datetime(2025, 1, 1, tzinfo=UTC)


def timestamp(value: datetime, unit: SourceTimestampUnit) -> int:
    microseconds = (value - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(
        microseconds=1
    )
    if unit is SourceTimestampUnit.MILLISECOND:
        return microseconds // 1_000
    return microseconds


def combined_message(
    *,
    symbol: str = "BTCUSDT",
    aggregate_trade_id: int = 100,
    occurrence: datetime = BASE,
    emission: datetime | None = None,
    unit: SourceTimestampUnit = SourceTimestampUnit.MICROSECOND,
    buyer_is_maker: bool = False,
    ignored: bool = True,
) -> str:
    emitted = occurrence + timedelta(microseconds=100) if emission is None else emission
    return json.dumps(
        {
            "stream": f"{symbol.lower()}@aggTrade",
            "data": {
                "e": "aggTrade",
                "E": timestamp(emitted, unit),
                "s": symbol,
                "a": aggregate_trade_id,
                "p": "93452.12000000",
                "q": "0.00170000",
                "f": aggregate_trade_id + 200,
                "l": aggregate_trade_id + 201,
                "T": timestamp(occurrence, unit),
                "m": buyer_is_maker,
                "M": ignored,
            },
        },
        separators=(",", ":"),
    )


def normalize(
    message: str,
    *,
    unit: SourceTimestampUnit = SourceTimestampUnit.MICROSECOND,
    local: datetime = BASE + timedelta(microseconds=200),
) -> ObservedAggregateTrade:
    return normalize_live_aggregate_trade_message(
        message,
        expected_streams=frozenset(
            {"btcusdt@aggTrade", "ethusdt@aggTrade"}
        ),
        source_identity="wss://data-stream.binance.vision/test",
        session_id="session-1",
        receive_sequence=1,
        local_observation_time=local,
        local_monotonic_ns=123,
        source_timestamp_unit=unit,
    )


class FakeConnection:
    def __init__(self, messages: list[object]) -> None:
        self.messages = deque(messages)
        self.closed = False

    async def recv(self) -> str | bytes:
        if not self.messages:
            raise AssertionError("fake messages exhausted")
        value = self.messages.popleft()
        if isinstance(value, BaseException):
            raise value
        return value  # type: ignore[return-value]


class FakeContext:
    def __init__(self, connection: FakeConnection | BaseException) -> None:
        self.connection = connection

    async def __aenter__(self) -> FakeConnection:
        if isinstance(self.connection, BaseException):
            raise self.connection
        return self.connection

    async def __aexit__(self, *args) -> None:
        if isinstance(self.connection, FakeConnection):
            self.connection.closed = True


class FakeConnector:
    def __init__(self, *sessions: list[object] | BaseException) -> None:
        self.values = deque(
            value if isinstance(value, BaseException) else FakeConnection(value)
            for value in sessions
        )
        self.urls: list[str] = []

    def __call__(self, url: str) -> FakeContext:
        self.urls.append(url)
        if not self.values:
            raise AssertionError("fake connector exhausted")
        return FakeContext(self.values.popleft())


class AdvancingClock:
    def __init__(self) -> None:
        self.wall = BASE + timedelta(seconds=10)
        self.monotonic = 1_000_000_000

    def now(self) -> datetime:
        result = self.wall
        self.wall += timedelta(seconds=1)
        return result

    def monotonic_ns(self) -> int:
        result = self.monotonic
        self.monotonic += 1_000_000_000
        return result


class FakeSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        await asyncio.sleep(0)


class LiveAggregateTradeNormalizerTests(unittest.TestCase):
    def test_valid_btc_eth_microsecond_timestamp_and_clock_semantics(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            occurrence = BASE + timedelta(seconds=1, microseconds=2)
            emission = occurrence + timedelta(microseconds=100)
            local = emission + timedelta(microseconds=200)
            observed = normalize(
                combined_message(
                    symbol=symbol,
                    occurrence=occurrence,
                    emission=emission,
                ),
                local=local,
            )
            self.assertEqual(observed.trade.symbol, symbol)
            self.assertEqual(observed.trade.event_time, occurrence)
            self.assertEqual(observed.provider_event_time, emission)
            self.assertEqual(observed.local_observation_time, local)
            self.assertIs(
                observed.provider_timestamp_unit,
                SourceTimestampUnit.MICROSECOND,
            )
            self.assertEqual(observed.provider_minus_trade_microseconds, 100)
            self.assertEqual(observed.local_minus_provider_microseconds, 200)
            self.assertEqual(observed.local_minus_trade_microseconds, 300)

    def test_explicit_millisecond_fixture_remains_supported_by_normalizer(self) -> None:
        occurrence = BASE + timedelta(milliseconds=1)
        emission = occurrence + timedelta(milliseconds=2)
        observed = normalize(
            combined_message(
                occurrence=occurrence,
                emission=emission,
                unit=SourceTimestampUnit.MILLISECOND,
            ),
            unit=SourceTimestampUnit.MILLISECOND,
            local=emission + timedelta(milliseconds=3),
        )
        self.assertEqual(observed.trade.event_time, occurrence)
        self.assertEqual(observed.provider_event_time, emission)
        self.assertIs(
            observed.trade.source_timestamp_unit,
            SourceTimestampUnit.MILLISECOND,
        )

    def test_buyer_maker_mapping_and_ignored_M_are_unchanged(self) -> None:
        sell = normalize(
            combined_message(buyer_is_maker=True, ignored=False)
        )
        buy = normalize(
            combined_message(buyer_is_maker=False, ignored=True)
        )
        self.assertTrue(sell.trade.buyer_is_maker)
        self.assertFalse(buy.trade.buyer_is_maker)
        self.assertIsNone(sell.trade.best_price_match)
        self.assertIsNone(buy.trade.best_price_match)
        self.assertEqual(
            sell.normalization_version,
            AGGREGATE_TRADE_NORMALIZER_VERSION,
        )

    def test_unexpected_symbol_event_type_and_malformed_payload_fail(self) -> None:
        unexpected = combined_message(symbol="ETHUSDT")
        wrong_type = json.loads(combined_message())
        wrong_type["data"]["e"] = "trade"
        cases = (
            (unexpected, frozenset({"btcusdt@aggTrade"})),
            (json.dumps(wrong_type), frozenset({"btcusdt@aggTrade"})),
            ("{malformed", frozenset({"btcusdt@aggTrade"})),
        )
        for payload, streams in cases:
            with self.subTest(payload=payload[:30]), self.assertRaises(
                BinanceLiveAggregateTradeError
            ):
                normalize_live_aggregate_trade_message(
                    payload,
                    expected_streams=streams,
                    source_identity="source",
                    session_id="session",
                    receive_sequence=1,
                    local_observation_time=BASE + timedelta(seconds=1),
                    local_monotonic_ns=1,
                    source_timestamp_unit=SourceTimestampUnit.MICROSECOND,
                )

    def test_observation_envelope_is_immutable(self) -> None:
        observed = normalize(combined_message())
        with self.assertRaises(FrozenInstanceError):
            observed.receive_sequence = 2  # type: ignore[misc]

    def test_local_observation_timestamp_must_be_timezone_aware_utc(self) -> None:
        for local in (
            BASE.replace(tzinfo=None),
            BASE.astimezone(timezone(timedelta(hours=1))),
        ):
            with self.subTest(local=local), self.assertRaises(ValueError):
                normalize(combined_message(), local=local)


class LiveAggregateTradeAdapterTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, connector: FakeConnector, *, max_reconnect_attempts: int = 2):
        clock = AdvancingClock()
        sleep = FakeSleep()
        sessions = iter(("session-1", "session-2", "session-3", "session-4"))
        adapter = BinanceSpotLiveAggregateTradeAdapter(
            symbols=("ETHUSDT", "BTCUSDT"),
            connector=connector,
            sleep=sleep,
            wall_clock=clock.now,
            monotonic_clock=clock.monotonic_ns,
            session_factory=lambda: next(sessions),
            max_reconnect_attempts=max_reconnect_attempts,
        )
        self.addAsyncCleanup(adapter.aclose)
        return adapter, sleep

    async def test_deterministic_market_data_only_combined_url_and_valid_events(self) -> None:
        connector = FakeConnector(
            [
                combined_message(),
                combined_message(
                    symbol="ETHUSDT", aggregate_trade_id=200
                ),
            ]
        )
        adapter, _ = self.adapter(connector)
        self.assertEqual(
            adapter.url,
            "wss://data-stream.binance.vision/stream?streams="
            "btcusdt@aggTrade/ethusdt@aggTrade&timeUnit=MICROSECOND",
        )
        connected = await anext(adapter)
        btc = await anext(adapter)
        eth = await anext(adapter)
        self.assertIsInstance(connected, LiveAggregateTradeConnectionEvent)
        self.assertIs(connected.kind, LiveAggregateTradeConnectionKind.CONNECTED)
        self.assertEqual([btc.trade.symbol, eth.trade.symbol], ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(connector.urls, [adapter.url])
        adapter.mark_capture_complete(disconnected_at=BASE + timedelta(minutes=1))
        health = adapter.health_snapshot()
        self.assertIs(
            health.status,
            LiveAggregateTradeHealthStatus.HEALTHY_CAPTURE_COMPLETE,
        )
        self.assertEqual(
            health.disconnected_at, BASE + timedelta(minutes=1)
        )

    async def test_duplicate_is_suppressed_conflict_is_fatal_and_accounted(self) -> None:
        conflict = json.loads(combined_message())
        conflict["data"]["p"] = "93453.12000000"
        connector = FakeConnector(
            [combined_message(), combined_message(), json.dumps(conflict)]
        )
        adapter, _ = self.adapter(connector)
        await anext(adapter)
        await anext(adapter)
        unhealthy = await anext(adapter)
        self.assertIs(unhealthy.kind, LiveAggregateTradeConnectionKind.UNHEALTHY)
        with self.assertRaises(BinanceLiveAggregateTradeError):
            await anext(adapter)
        health = adapter.health_snapshot()
        self.assertEqual(health.messages_received, 3)
        self.assertEqual(health.accepted_messages, 1)
        self.assertEqual(health.duplicate_messages, 1)
        self.assertEqual(health.conflicting_messages, 1)
        self.assertIs(health.status, LiveAggregateTradeHealthStatus.UNHEALTHY)

    async def test_reversal_is_fatal_and_numerical_gap_is_health_evidence_only(self) -> None:
        connector = FakeConnector(
            [
                combined_message(aggregate_trade_id=100),
                combined_message(
                    aggregate_trade_id=105,
                    occurrence=BASE + timedelta(microseconds=1),
                ),
                combined_message(
                    aggregate_trade_id=104,
                    occurrence=BASE,
                ),
            ]
        )
        adapter, _ = self.adapter(connector)
        await anext(adapter)
        await anext(adapter)
        await anext(adapter)
        unhealthy = await anext(adapter)
        self.assertIs(unhealthy.kind, LiveAggregateTradeConnectionKind.UNHEALTHY)
        health = adapter.health_snapshot()
        self.assertEqual(health.observed_id_gaps, 1)
        self.assertEqual(health.ordering_violations, 1)

    async def test_disconnect_reconnect_has_distinct_session_lineage_and_backoff(self) -> None:
        connector = FakeConnector(
            [combined_message(), ConnectionClosed(None, None)],
            [
                combined_message(
                    aggregate_trade_id=101,
                    occurrence=BASE + timedelta(microseconds=1),
                )
            ],
        )
        adapter, sleep = self.adapter(connector)
        first_connected = await anext(adapter)
        await anext(adapter)
        disconnected = await anext(adapter)
        second_connected = await anext(adapter)
        second = await anext(adapter)
        self.assertEqual(first_connected.session_id, "session-1")
        self.assertEqual(disconnected.session_id, "session-1")
        self.assertEqual(second_connected.session_id, "session-2")
        self.assertEqual(second.session_id, "session-2")
        self.assertEqual(sleep.delays, [1.0])
        self.assertEqual(adapter.health_snapshot().reconnect_count, 1)

    async def test_malformed_message_marks_session_unhealthy_without_reconnect(self) -> None:
        connector = FakeConnector(["{malformed"])
        adapter, sleep = self.adapter(connector)
        await anext(adapter)
        unhealthy = await anext(adapter)
        self.assertIs(unhealthy.kind, LiveAggregateTradeConnectionKind.UNHEALTHY)
        with self.assertRaises(BinanceLiveAggregateTradeError):
            await anext(adapter)
        self.assertEqual(sleep.delays, [])
        self.assertEqual(adapter.health_snapshot().malformed_or_rejected_messages, 1)

    async def test_reconnect_attempts_are_bounded(self) -> None:
        connector = FakeConnector(OSError("offline"), OSError("offline"))
        adapter, sleep = self.adapter(connector, max_reconnect_attempts=1)
        with self.assertRaisesRegex(
            BinanceLiveAggregateTradeError, "bounded reconnect"
        ):
            await anext(adapter)
        self.assertEqual(sleep.delays, [1.0])
        self.assertEqual(len(connector.urls), 2)


if __name__ == "__main__":
    unittest.main()

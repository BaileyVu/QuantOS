"""Deterministic asynchronous transport, continuity, and cancellation tests."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import aclosing
import ssl
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed, InvalidMessage, InvalidStatus
from websockets.frames import Close
from websockets.http11 import Response

from quantos.domain.market_data import MarketEvent
from quantos.infrastructure.binance import BinanceLiveMarketDataError, BinanceSpotLiveMarketDataAdapter
from tests.unit.test_binance_live_klines import OPEN_US, combined_kline, encode


def message(symbol: str = "BTCUSDT", minute: int = 0, *, closed: bool = True) -> str:
    return encode(combined_kline(symbol, minute, closed=closed))


class FakeConnection:
    def __init__(self, messages: list) -> None:
        self.messages = deque(messages)
        self.closed = False
        self.receiving = asyncio.Event()
        self.block = asyncio.Event()

    async def recv(self) -> str | bytes:
        if not self.messages:
            raise AssertionError("fake messages exhausted: unexpected receive")
        value = self.messages.popleft()
        if value is None:
            self.receiving.set()
            await self.block.wait()
            raise AssertionError("blocked fake receive unexpectedly resumed")
        if isinstance(value, BaseException):
            raise value
        return value


class FakeContext:
    def __init__(self, owner: FakeConnector, value: FakeConnection | BaseException) -> None:
        self.owner = owner
        self.value = value

    async def __aenter__(self) -> FakeConnection:
        if isinstance(self.value, BaseException):
            raise self.value
        self.owner.active += 1
        self.owner.peak_active = max(self.owner.peak_active, self.owner.active)
        return self.value

    async def __aexit__(self, *args) -> None:
        self.value.closed = True
        self.owner.active -= 1


class FakeConnector:
    def __init__(self, *connections: list | BaseException) -> None:
        self.values = deque(
            value if isinstance(value, BaseException) else FakeConnection(value)
            for value in connections
        )
        self.connections = [value for value in self.values if isinstance(value, FakeConnection)]
        self.urls: list[str] = []
        self.active = 0
        self.peak_active = 0

    def __call__(self, url: str) -> FakeContext:
        self.urls.append(url)
        if not self.values:
            raise AssertionError("fake connector exhausted: unexpected reconnect")
        return FakeContext(self, self.values.popleft())


class FakeSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        await asyncio.sleep(0)


class LiveStreamSubscriptionTests(unittest.TestCase):
    def test_exact_single_symbol_public_microsecond_urls(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            adapter = BinanceSpotLiveMarketDataAdapter(symbols=[symbol])
            self.assertEqual(adapter.url, f"wss://data-stream.binance.vision/stream?streams={symbol.lower()}@kline_1m&timeUnit=MICROSECOND")

    def test_two_symbols_use_one_deterministic_combined_url(self) -> None:
        first = BinanceSpotLiveMarketDataAdapter(symbols=["ETHUSDT", "BTCUSDT"])
        second = BinanceSpotLiveMarketDataAdapter(symbols=["BTCUSDT", "ETHUSDT"])
        self.assertEqual(first.url, second.url)
        self.assertEqual(first.url, "wss://data-stream.binance.vision/stream?streams=btcusdt@kline_1m/ethusdt@kline_1m&timeUnit=MICROSECOND")
        parsed = urlsplit(first.url)
        self.assertEqual((parsed.scheme, parsed.hostname, parsed.path),
                         ("wss", "data-stream.binance.vision", "/stream"))
        self.assertIsNone(parsed.username)
        self.assertIsNone(parsed.password)
        self.assertEqual(set(parse_qs(parsed.query)), {"streams", "timeUnit"})

    def test_invalid_empty_duplicate_or_unsupported_symbols_fail_before_connect(self) -> None:
        for symbols in ([], (), ["BTCUSDT", "BTCUSDT"], ["ETHUSDT", "ETHUSDT"],
                        ["BNBUSDT"], ["btcusdt"], ["BTCUSDT", "BNBUSDT"], "BTCUSDT",
                        [True], [None], [[]], None):
            with self.subTest(symbols=symbols), self.assertRaises(ValueError):
                BinanceSpotLiveMarketDataAdapter(symbols=symbols)

    def test_non_one_minute_interval_fails_before_connect(self) -> None:
        for interval in ("5m", "1M", "", None, 1, True):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                BinanceSpotLiveMarketDataAdapter(symbols=["BTCUSDT"], interval=interval)

    def test_construction_and_repeated_aiter_do_not_connect_or_create_sessions(self) -> None:
        connector = FakeConnector()
        adapter = BinanceSpotLiveMarketDataAdapter(symbols=["BTCUSDT"], connector=connector)
        self.assertIs(aiter(adapter), adapter)
        self.assertIs(aiter(adapter), adapter)
        self.assertEqual(connector.urls, [])


class LiveStreamTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, connector: FakeConnector, *, symbols=("BTCUSDT", "ETHUSDT")):
        sleep = FakeSleep()
        adapter = BinanceSpotLiveMarketDataAdapter(symbols=symbols, connector=connector, sleep=sleep)
        self.addAsyncCleanup(adapter.aclose)
        return adapter, sleep

    async def test_consecutive_btc_completed_events(self) -> None:
        connector = FakeConnector([message(minute=0), message(minute=1), message(minute=2)])
        adapter, sleep = self.adapter(connector, symbols=["BTCUSDT"])
        events = [await anext(adapter) for _ in range(3)]
        self.assertTrue(all(type(event) is MarketEvent for event in events))
        self.assertEqual([event.candle.open_time.minute for event in events], [0, 1, 2])
        self.assertEqual(sleep.delays, [])
        self.assertEqual(len(connector.urls), 1)

    async def test_consecutive_eth_completed_events(self) -> None:
        connector = FakeConnector([message("ETHUSDT", minute) for minute in range(3)])
        adapter, _ = self.adapter(connector, symbols=["ETHUSDT"])
        events = [await anext(adapter) for _ in range(3)]
        self.assertEqual([event.candle.symbol for event in events], ["ETHUSDT"] * 3)
        self.assertEqual([event.candle.open_time.minute for event in events], [0, 1, 2])

    async def test_interleaving_has_independent_per_symbol_continuity(self) -> None:
        sequence = [("ETHUSDT", 10), ("BTCUSDT", 0), ("BTCUSDT", 1), ("ETHUSDT", 11)]
        connector = FakeConnector([message(symbol, minute) for symbol, minute in sequence])
        adapter, _ = self.adapter(connector)
        events = [await anext(adapter) for _ in sequence]
        self.assertEqual([(event.candle.symbol, event.candle.open_time.minute) for event in events], sequence)
        self.assertEqual(connector.peak_active, 1)
        self.assertEqual(len(connector.urls), 1)

    async def test_exact_duplicate_candle_emits_once_even_with_later_event_time(self) -> None:
        duplicate = combined_kline()
        duplicate["data"]["E"] += 1000
        connector = FakeConnector([message(), encode(duplicate), message(minute=1)])
        adapter, _ = self.adapter(connector)
        events = [await anext(adapter), await anext(adapter)]
        self.assertEqual([event.candle.open_time.minute for event in events], [0, 1])

    async def test_conflicting_duplicate_fails_for_every_mutable_candle_value(self) -> None:
        for field, value in (("o", "101"), ("h", "106"), ("l", "98"), ("c", "103"),
                             ("v", "13"), ("q", "1235"), ("n", 18), ("T", OPEN_US + 59_999_998)):
            with self.subTest(field=field):
                duplicate = combined_kline()
                duplicate["data"]["k"][field] = value
                connector = FakeConnector([message(), encode(duplicate)])
                adapter, sleep = self.adapter(connector)
                await anext(adapter)
                with self.assertRaisesRegex(BinanceLiveMarketDataError, "conflicting"):
                    await anext(adapter)
                self.assertEqual(sleep.delays, [])
                self.assertTrue(connector.connections[0].closed)

    async def test_older_completed_candle_fails(self) -> None:
        connector = FakeConnector([message(minute=1), message(minute=0)])
        adapter, sleep = self.adapter(connector)
        await anext(adapter)
        with self.assertRaisesRegex(BinanceLiveMarketDataError, "out-of-order"):
            await anext(adapter)
        self.assertEqual(sleep.delays, [])

    async def test_missing_completed_minute_fails_without_repair(self) -> None:
        connector = FakeConnector([message(), message(minute=2)])
        adapter, sleep = self.adapter(connector)
        await anext(adapter)
        with self.assertRaisesRegex(BinanceLiveMarketDataError, "missing"):
            await anext(adapter)
        self.assertEqual(sleep.delays, [])
        self.assertEqual(len(connector.urls), 1)

    async def test_repeated_and_out_of_order_partials_do_not_change_completed_state(self) -> None:
        connector = FakeConnector([
            message(minute=0, closed=False), message(minute=0, closed=False), message(),
            message(minute=9, closed=False), message(minute=-1, closed=False),
            message(minute=1, closed=False), message(minute=1),
        ])
        adapter, _ = self.adapter(connector)
        events = [await anext(adapter), await anext(adapter)]
        self.assertEqual([event.candle.open_time.minute for event in events], [0, 1])

    async def test_partial_for_missing_minute_does_not_count_as_completion(self) -> None:
        adapter, _ = self.adapter(FakeConnector([message(), message(minute=1, closed=False), message(minute=2)]))
        await anext(adapter)
        with self.assertRaisesRegex(BinanceLiveMarketDataError, "missing"):
            await anext(adapter)

    async def test_transport_disconnect_preserves_state_and_accepts_next_minute(self) -> None:
        connector = FakeConnector([message(), ConnectionClosed(None, None)], [message(minute=1)])
        adapter, sleep = self.adapter(connector)
        events = [await anext(adapter), await anext(adapter)]
        self.assertEqual([event.candle.open_time.minute for event in events], [0, 1])
        self.assertEqual(sleep.delays, [1.0])
        self.assertTrue(connector.connections[0].closed)
        self.assertEqual(connector.peak_active, 1)
        self.assertEqual(connector.urls, [adapter.url, adapter.url])

    async def test_receive_watchdog_reconnects_without_emitting_an_event(self) -> None:
        connector = FakeConnector([None], [message()])
        adapter, sleep = self.adapter(connector)
        with self.assertLogs("quantos.binance.live", level="WARNING") as captured:
            with patch("quantos.infrastructure.binance.live_stream._RECEIVE_WATCHDOG_SECONDS", 0.001):
                event = await anext(adapter)
        self.assertEqual(event.candle.open_time.minute, 0)
        self.assertEqual(sleep.delays, [1.0])
        self.assertEqual(len(connector.urls), 2)
        self.assertTrue(connector.connections[0].closed)
        reconnect = next(
            record for record in captured.records
            if record.getMessage() == "live_market_data_reconnect"
        )
        self.assertEqual(reconnect.context["reason"], "receive_watchdog_timeout")

    async def test_completed_state_survives_watchdog_and_next_minute_succeeds(self) -> None:
        connector = FakeConnector([message(), None], [message(minute=1)])
        adapter, sleep = self.adapter(connector)
        first = await anext(adapter)
        with patch("quantos.infrastructure.binance.live_stream._RECEIVE_WATCHDOG_SECONDS", 0.001):
            second = await anext(adapter)
        self.assertEqual([first.candle.open_time.minute, second.candle.open_time.minute], [0, 1])
        self.assertEqual(sleep.delays, [1.0])

    async def test_duplicate_after_watchdog_is_suppressed(self) -> None:
        connector = FakeConnector([message(), None], [message(), message(minute=1)])
        adapter, sleep = self.adapter(connector)
        first = await anext(adapter)
        with patch("quantos.infrastructure.binance.live_stream._RECEIVE_WATCHDOG_SECONDS", 0.001):
            second = await anext(adapter)
        self.assertEqual([first.candle.open_time.minute, second.candle.open_time.minute], [0, 1])
        self.assertEqual(sleep.delays, [1.0])

    async def test_gap_after_watchdog_remains_fatal(self) -> None:
        connector = FakeConnector([message(), None], [message(minute=2)])
        adapter, sleep = self.adapter(connector)
        await anext(adapter)
        with patch("quantos.infrastructure.binance.live_stream._RECEIVE_WATCHDOG_SECONDS", 0.001):
            with self.assertRaisesRegex(BinanceLiveMarketDataError, "missing"):
                await anext(adapter)
        self.assertEqual(sleep.delays, [1.0])
        self.assertEqual(len(connector.urls), 2)

    async def test_conflicting_duplicate_after_watchdog_remains_fatal(self) -> None:
        conflicting = combined_kline()
        conflicting["data"]["k"]["c"] = "103"
        connector = FakeConnector([message(), None], [encode(conflicting)])
        adapter, sleep = self.adapter(connector)
        await anext(adapter)
        with patch("quantos.infrastructure.binance.live_stream._RECEIVE_WATCHDOG_SECONDS", 0.001):
            with self.assertRaisesRegex(BinanceLiveMarketDataError, "conflicting"):
                await anext(adapter)
        self.assertEqual(sleep.delays, [1.0])

    async def test_older_completion_after_watchdog_remains_fatal(self) -> None:
        connector = FakeConnector([message(minute=1), None], [message()])
        adapter, sleep = self.adapter(connector)
        await anext(adapter)
        with patch("quantos.infrastructure.binance.live_stream._RECEIVE_WATCHDOG_SECONDS", 0.001):
            with self.assertRaisesRegex(BinanceLiveMarketDataError, "out-of-order"):
                await anext(adapter)
        self.assertEqual(sleep.delays, [1.0])

    async def test_normal_payload_within_watchdog_does_not_reconnect(self) -> None:
        connector = FakeConnector([message()])
        adapter, sleep = self.adapter(connector)
        with patch("quantos.infrastructure.binance.live_stream._RECEIVE_WATCHDOG_SECONDS", 0.1):
            event = await anext(adapter)
        self.assertIs(type(event), MarketEvent)
        self.assertEqual(sleep.delays, [])
        self.assertEqual(len(connector.urls), 1)

    async def test_partial_payload_resets_watchdog_without_emission_or_state_change(self) -> None:
        connector = FakeConnector(
            [message(), message(minute=9, closed=False), None],
            [message(minute=1)],
        )
        adapter, sleep = self.adapter(connector)
        first = await anext(adapter)
        with patch("quantos.infrastructure.binance.live_stream._RECEIVE_WATCHDOG_SECONDS", 0.001):
            second = await anext(adapter)
        self.assertEqual([first.candle.open_time.minute, second.candle.open_time.minute], [0, 1])
        self.assertEqual(sleep.delays, [1.0])

    async def test_exact_duplicate_after_reconnect_emits_once(self) -> None:
        connector = FakeConnector([message(), ConnectionClosed(None, None)], [message(), message(minute=1)])
        adapter, sleep = self.adapter(connector)
        events = [await anext(adapter), await anext(adapter)]
        self.assertEqual([event.candle.open_time.minute for event in events], [0, 1])
        self.assertEqual(sleep.delays, [1.0])

    async def test_gap_conflict_and_older_completion_after_reconnect_are_fatal(self) -> None:
        conflict = combined_kline(minute=1)
        conflict["data"]["k"]["c"] = "103"
        for second, expected in ((message(minute=3), "missing"), (message(minute=0), "out-of-order"),
                                 (encode(conflict), "conflicting")):
            with self.subTest(expected=expected):
                connector = FakeConnector([message(minute=1), ConnectionClosed(None, None)], [second])
                adapter, sleep = self.adapter(connector)
                await anext(adapter)
                with self.assertRaisesRegex(BinanceLiveMarketDataError, expected):
                    await anext(adapter)
                self.assertEqual(sleep.delays, [1.0])
                self.assertEqual(len(connector.urls), 2)

    async def test_reconnect_retains_state_for_both_symbols(self) -> None:
        connector = FakeConnector(
            [message(), message("ETHUSDT", 10), OSError("offline")],
            [message("ETHUSDT", 10), message(minute=1), message("ETHUSDT", 11)],
        )
        adapter, _ = self.adapter(connector)
        events = [await anext(adapter) for _ in range(4)]
        self.assertEqual([(event.candle.symbol, event.candle.open_time.minute) for event in events],
                         [("BTCUSDT", 0), ("ETHUSDT", 10), ("BTCUSDT", 1), ("ETHUSDT", 11)])

    async def test_malformed_payload_terminates_session_without_reconnect(self) -> None:
        connector = FakeConnector(["{malformed"])
        adapter, sleep = self.adapter(connector)
        with self.assertRaises(BinanceLiveMarketDataError):
            await anext(adapter)
        with self.assertRaises(StopAsyncIteration):
            await anext(adapter)
        self.assertEqual(sleep.delays, [])
        self.assertEqual(len(connector.urls), 1)
        self.assertTrue(connector.connections[0].closed)

    async def test_unsubscribed_closed_and_partial_events_are_fatal(self) -> None:
        for closed in (True, False):
            connector = FakeConnector([message("ETHUSDT", closed=closed)])
            adapter, sleep = self.adapter(connector, symbols=["BTCUSDT"])
            with self.assertRaises(BinanceLiveMarketDataError):
                await anext(adapter)
            self.assertEqual(sleep.delays, [])

    async def test_bounded_exponential_backoff_on_repeated_connect_failures(self) -> None:
        connector = FakeConnector(*(OSError("offline") for _ in range(8)), [message()])
        adapter, sleep = self.adapter(connector)
        await anext(adapter)
        self.assertEqual(sleep.delays, [1, 2, 4, 8, 16, 30, 30, 30])
        self.assertEqual(len(connector.urls), 9)

    async def test_immediate_disconnects_and_partials_do_not_reset_backoff(self) -> None:
        connector = FakeConnector(
            [ConnectionClosed(None, None)],
            [message(closed=False), ConnectionClosed(None, None)],
            [ConnectionClosed(None, None)], [message()],
        )
        adapter, sleep = self.adapter(connector)
        await anext(adapter)
        self.assertEqual(sleep.delays, [1, 2, 4])

    async def test_duplicates_do_not_reset_backoff_but_new_completions_do(self) -> None:
        connector = FakeConnector(
            [message(), OSError("offline")],
            [message(), OSError("offline")],
            [message(minute=1), OSError("offline")], [message(minute=2)],
        )
        adapter, sleep = self.adapter(connector)
        for _ in range(3):
            await anext(adapter)
        self.assertEqual(sleep.delays, [1, 2, 1])

    async def test_connect_timeout_and_server_error_reconnect(self) -> None:
        failures = (TimeoutError(), InvalidStatus(Response(503, "Unavailable", Headers())))
        for failure in failures:
            connector = FakeConnector(failure, [message()])
            adapter, sleep = self.adapter(connector)
            self.assertIsInstance(await anext(adapter), MarketEvent)
            self.assertEqual(sleep.delays, [1])

    async def test_incomplete_handshake_from_eof_reconnects(self) -> None:
        failure = InvalidMessage("incomplete HTTP response")
        failure.__cause__ = EOFError()
        adapter, sleep = self.adapter(FakeConnector(failure, [message()]))
        await anext(adapter)
        self.assertEqual(sleep.delays, [1])

    async def test_fatal_handshake_or_certificate_error_does_not_reconnect(self) -> None:
        for failure in (InvalidStatus(Response(403, "Forbidden", Headers())),
                        InvalidMessage("invalid protocol"), ssl.SSLCertVerificationError("untrusted")):
            adapter, sleep = self.adapter(FakeConnector(failure))
            with self.assertRaises(BinanceLiveMarketDataError):
                await anext(adapter)
            self.assertEqual(sleep.delays, [])

    async def test_protocol_or_corrupt_frame_closure_is_fatal(self) -> None:
        for code in (1002, 1003, 1007, 1008, 1009, 1010):
            for received in (True, False):
                frame = Close(code, "invalid")
                failure = ConnectionClosed(frame if received else None, None if received else frame)
                adapter, sleep = self.adapter(FakeConnector([failure]))
                with self.subTest(code=code, received=received), self.assertRaises(BinanceLiveMarketDataError):
                    await anext(adapter)
                self.assertEqual(sleep.delays, [])

    async def test_normal_lifetime_close_reconnects(self) -> None:
        connector = FakeConnector([ConnectionClosed(Close(1000, "lifetime"), None)], [message()])
        adapter, sleep = self.adapter(connector)
        await anext(adapter)
        self.assertEqual(sleep.delays, [1])

    async def test_server_shutdown_reconnects_and_keeps_completed_state(self) -> None:
        shutdown = encode({"stream": "!serverShutdown", "data": {"e": "serverShutdown", "E": OPEN_US}})
        connector = FakeConnector([message(), shutdown], [message(), message(minute=1)])
        adapter, sleep = self.adapter(connector)
        events = [await anext(adapter), await anext(adapter)]
        self.assertEqual([event.candle.open_time.minute for event in events], [0, 1])
        self.assertEqual(sleep.delays, [1])
        self.assertEqual(connector.peak_active, 1)
        self.assertTrue(connector.connections[0].closed)

    async def test_cancellation_during_receive_closes_connection_without_retry(self) -> None:
        connector = FakeConnector([None])
        adapter, sleep = self.adapter(connector)
        with patch("quantos.infrastructure.binance.live_stream._RECEIVE_WATCHDOG_SECONDS", 1.0):
            pending = asyncio.create_task(anext(adapter))
            await asyncio.wait_for(connector.connections[0].receiving.wait(), timeout=0.1)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
        self.assertTrue(connector.connections[0].closed)
        self.assertEqual(sleep.delays, [])
        self.assertEqual(len(connector.urls), 1)

    async def test_cancellation_during_backoff_propagates(self) -> None:
        entered = asyncio.Event()

        async def sleep(delay):
            entered.set()
            await asyncio.Event().wait()

        connector = FakeConnector(OSError("offline"))
        adapter = BinanceSpotLiveMarketDataAdapter(symbols=["BTCUSDT"], connector=connector, sleep=sleep)
        self.addAsyncCleanup(adapter.aclose)
        pending = asyncio.create_task(anext(adapter))
        await asyncio.wait_for(entered.wait(), timeout=1)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(len(connector.urls), 1)

    async def test_cancellation_at_connect_propagates(self) -> None:
        adapter, sleep = self.adapter(FakeConnector(asyncio.CancelledError()))
        with self.assertRaises(asyncio.CancelledError):
            await anext(adapter)
        self.assertEqual(sleep.delays, [])

    async def test_system_exit_signals_are_not_wrapped_or_retried(self) -> None:
        for failure in (SystemExit(1), KeyboardInterrupt()):
            adapter, sleep = self.adapter(FakeConnector([failure]))
            with self.assertRaises(type(failure)):
                await anext(adapter)
            self.assertEqual(sleep.delays, [])

    async def test_explicit_close_releases_connection_and_ends_session(self) -> None:
        connector = FakeConnector([message()])
        adapter, sleep = self.adapter(connector)
        async with aclosing(adapter):
            async for event in adapter:
                self.assertIsInstance(event, MarketEvent)
                break
        self.assertTrue(connector.connections[0].closed)
        self.assertEqual(sleep.delays, [])
        with self.assertRaises(StopAsyncIteration):
            await anext(adapter)

    async def test_default_connector_disables_pings_proxy_and_bounds_resources(self) -> None:
        connector = FakeConnector([message()])
        adapter = BinanceSpotLiveMarketDataAdapter(symbols=["BTCUSDT"])
        self.addAsyncCleanup(adapter.aclose)
        with patch("quantos.infrastructure.binance.live_stream.connect",
                   side_effect=lambda url, **kwargs: connector(url)) as connect:
            async with aclosing(adapter):
                await anext(adapter)
        connect.assert_called_once_with(
            adapter.url, ping_interval=None, proxy=None, open_timeout=10, close_timeout=10,
            max_size=65_536, max_queue=16,
        )

"""One public Binance Spot connection exposing completed canonical events."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
import logging
import ssl
from typing import Protocol

from websockets.asyncio.client import connect, process_exception
from websockets.exceptions import ConnectionClosed, WebSocketException

from quantos.domain.common import V1_INTERVAL, V1_SYMBOLS
from quantos.domain.market_data import Candle, MarketEvent
from quantos.infrastructure.binance.live_klines import (
    BinanceLiveMarketDataError,
    _ServerShutdown,
    normalize_live_kline,
)

_ENDPOINT = "wss://data-stream.binance.vision"
_INITIAL_RECONNECT_DELAY = 1.0
_MAX_RECONNECT_DELAY = 30.0
_RECEIVE_WATCHDOG_SECONDS = 30.0
_LOGGER = logging.getLogger("quantos.binance.live")


class _Connection(Protocol):
    async def recv(self) -> str | bytes: ...


class _ReceiveWatchdogTimeout(Exception):
    """No market-data payload arrived within the bounded receive interval."""


_Connector = Callable[[str], AbstractAsyncContextManager[_Connection]]
_Sleep = Callable[[float], Awaitable[None]]


def _connect(url: str) -> AbstractAsyncContextManager[_Connection]:
    # Server Ping frames still receive automatic Pong responses. Disable
    # environment proxies and client keepalive; this is a public data endpoint.
    return connect(
        url, ping_interval=None, proxy=None, open_timeout=10, close_timeout=10,
        max_size=65_536, max_queue=16,
    )


def _retryable_transport(error: Exception) -> bool:
    if isinstance(error, ssl.SSLError):
        return False
    if isinstance(error, ConnectionClosed):
        # Protocol/payload/policy failures must not masquerade as outages.
        fatal_codes = {1002, 1003, 1007, 1008, 1009, 1010}
        return all(
            frame is None or frame.code not in fatal_codes
            for frame in (error.rcvd, error.sent)
        )
    return isinstance(error, EOFError) or process_exception(error) is None


class BinanceSpotLiveMarketDataAdapter(AsyncIterator[MarketEvent]):
    """A single-consumer live session; reuse the iterator across reconnects.

    Construction performs no I/O. Use async iteration and explicitly aclose()
    on early exit (for example with contextlib.aclosing). A fatal data error
    terminates this session. Starting a new session is an explicit caller act.
    Connector/sleep injection is confined to this Infrastructure boundary.
    """

    def __init__(
        self,
        *,
        symbols: Iterable[str],
        interval: str = V1_INTERVAL,
        connector: _Connector | None = None,
        sleep: _Sleep | None = None,
    ) -> None:
        if isinstance(symbols, (str, bytes)):
            raise ValueError("symbols must be a non-empty collection of V1 symbols")
        try:
            selected = tuple(symbols)
        except TypeError as error:
            raise ValueError("symbols must be iterable") from error
        if not selected or any(type(symbol) is not str or symbol not in V1_SYMBOLS for symbol in selected):
            raise ValueError("symbols must contain only BTCUSDT or ETHUSDT")
        if len(set(selected)) != len(selected):
            raise ValueError("duplicate subscription symbols are not allowed")
        if type(interval) is not str or interval != V1_INTERVAL:
            raise ValueError("interval must be 1m")
        streams = tuple(f"{symbol.lower()}@kline_{interval}" for symbol in sorted(selected))
        self._url = f"{_ENDPOINT}/stream?streams={'/'.join(streams)}&timeUnit=MICROSECOND"
        self._streams = frozenset(streams)
        self._connector = _connect if connector is None else connector
        self._sleep = asyncio.sleep if sleep is None else sleep
        self._iterator = self._events()

    @property
    def url(self) -> str:
        """The deterministic provider-owned combined subscription URL."""
        return self._url

    def __aiter__(self) -> BinanceSpotLiveMarketDataAdapter:
        return self

    async def __anext__(self) -> MarketEvent:
        return await anext(self._iterator)

    async def aclose(self) -> None:
        """Close the session and its connection without reconnecting."""
        await self._iterator.aclose()

    async def _events(self) -> AsyncIterator[MarketEvent]:
        last_completed: dict[str, Candle] = {}
        delay = _INITIAL_RECONNECT_DELAY
        _LOGGER.info("live_market_data_started", extra={"context": {"streams": sorted(self._streams)}})
        try:
            while True:
                reason = "transport_closed"
                try:
                    async with self._connector(self._url) as connection:
                        _LOGGER.info("live_market_data_connected")
                        while True:
                            try:
                                async with asyncio.timeout(_RECEIVE_WATCHDOG_SECONDS):
                                    message = await connection.recv()
                            except TimeoutError as error:
                                raise _ReceiveWatchdogTimeout from error
                            event = normalize_live_kline(message, expected_streams=self._streams)
                            if event is None:
                                continue
                            candle = event.candle
                            previous = last_completed.get(candle.symbol)
                            if previous is not None:
                                if candle.open_time == previous.open_time:
                                    if candle != previous:
                                        raise BinanceLiveMarketDataError(
                                            f"conflicting completed duplicate for {candle.symbol}"
                                        )
                                    _LOGGER.debug("live_market_data_duplicate", extra={"context": {
                                        "symbol": candle.symbol, "open_time": candle.open_time,
                                    }})
                                    continue
                                if candle.open_time < previous.open_time:
                                    raise BinanceLiveMarketDataError(
                                        f"out-of-order completed candle for {candle.symbol}"
                                    )
                                if candle.open_time - previous.open_time != timedelta(minutes=1):
                                    raise BinanceLiveMarketDataError(
                                        f"missing live 1m candle for {candle.symbol}"
                                    )
                            last_completed[candle.symbol] = candle
                            # A connection opening or a duplicate alone does not
                            # reset failure backoff; new completed data does.
                            delay = _INITIAL_RECONNECT_DELAY
                            _LOGGER.info("live_market_data_completed", extra={"context": {
                                "symbol": candle.symbol, "open_time": candle.open_time,
                                "event_time": event.timestamp,
                            }})
                            yield event
                except _ServerShutdown:
                    reason = "server_shutdown"
                except _ReceiveWatchdogTimeout:
                    reason = "receive_watchdog_timeout"
                except (OSError, EOFError, WebSocketException) as error:
                    if not _retryable_transport(error):
                        raise BinanceLiveMarketDataError(
                            f"fatal WebSocket protocol/connection failure: {type(error).__name__}"
                        ) from error
                    reason = type(error).__name__
                _LOGGER.warning("live_market_data_reconnect", extra={"context": {
                    "reason": reason, "delay_seconds": delay,
                }})
                await self._sleep(delay)
                delay = min(delay * 2, _MAX_RECONNECT_DELAY)
        except BinanceLiveMarketDataError as error:
            _LOGGER.error("live_market_data_integrity_failure", extra={"context": {"reason": str(error)}})
            raise
        finally:
            _LOGGER.info("live_market_data_stopped")

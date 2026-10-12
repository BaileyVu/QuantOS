"""Public Binance USD-M REST/WebSocket ingestion for HFT paper mode."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import json
import time
from typing import Any, AsyncIterator, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from websockets.asyncio.client import connect, process_exception
from websockets.exceptions import ConnectionClosed, WebSocketException

from quantos.domain.market_data.hft import (
    AggregateTrade,
    BookTicker,
    DepthDelta,
    DepthSnapshot,
    MarkPriceEvent,
)
from quantos.infrastructure.binance.futures import (
    BinanceFuturesError,
    BinanceUsdmPublicClient,
)


class BinanceHftStreamError(RuntimeError):
    pass


class BinanceHftBackpressureError(BinanceHftStreamError):
    pass


def _utc_milliseconds(value: Any, name: str) -> datetime:
    try:
        result = datetime.fromtimestamp(int(value) / 1000, timezone.utc)
    except (TypeError, ValueError, OSError) as error:
        raise BinanceHftStreamError(f"invalid {name}") from error
    return result


def _levels(value: Any, name: str) -> tuple[tuple[Decimal, Decimal], ...]:
    if not isinstance(value, list):
        raise BinanceHftStreamError(f"{name} must be an array")
    result = []
    for row in value:
        if not isinstance(row, list) or len(row) != 2:
            raise BinanceHftStreamError(f"malformed {name} level")
        try:
            result.append((Decimal(str(row[0])), Decimal(str(row[1]))))
        except Exception as error:
            raise BinanceHftStreamError(f"invalid {name} decimal") from error
    return tuple(result)


def _optional_utc_milliseconds(value: Any, name: str) -> datetime | None:
    return None if value is None else _utc_milliseconds(value, name)


class BinanceHftPublicClient(BinanceUsdmPublicClient):
    def __init__(
        self,
        base_url: str = "https://fapi.binance.com",
        *,
        request_timeout: float = 5,
        http_get: Callable[[str], bytes] | None = None,
    ) -> None:
        if request_timeout <= 0:
            raise ValueError("HFT REST timeout must be positive")

        def bounded_get(url: str) -> bytes:
            request = Request(url, headers={"User-Agent": "QuantOS/0.1"})
            try:
                with urlopen(request, timeout=request_timeout) as response:
                    return response.read()
            except HTTPError as error:
                raise BinanceFuturesError(
                    f"Binance Futures HTTP {error.code}",
                    retryable=(
                        error.code in {418, 429}
                        or 500 <= error.code < 600
                    ),
                    status_code=error.code,
                ) from error
            except (TimeoutError, URLError, OSError) as error:
                raise BinanceFuturesError(
                    f"Binance Futures request failed: {error}",
                    retryable=True,
                ) from error

        super().__init__(base_url, http_get or bounded_get)

    def depth_snapshot(
        self, symbol: str, limit: int = 1000,
        received_at: datetime | None = None,
    ) -> DepthSnapshot:
        if symbol not in {"BTCUSDC", "BTCUSDT"}:
            raise ValueError("unsupported HFT symbol")
        if limit not in {5, 10, 20, 50, 100, 500, 1000}:
            raise ValueError("unsupported Binance depth limit")
        payload = self._json("/fapi/v1/depth", {"symbol": symbol, "limit": limit})
        if not isinstance(payload, Mapping):
            raise BinanceFuturesError("depth snapshot must be an object")
        received = received_at or datetime.now(timezone.utc)
        received_monotonic_ns = time.monotonic_ns()
        event_time = _utc_milliseconds(
            payload.get("E", int(received.timestamp() * 1000)), "snapshot event time"
        )
        try:
            return DepthSnapshot(
                symbol=symbol,
                last_update_id=int(payload["lastUpdateId"]),
                bids=_levels(payload["bids"], "snapshot bids"),
                asks=_levels(payload["asks"], "snapshot asks"),
                exchange_time=event_time,
                received_at=received,
                received_monotonic_ns=received_monotonic_ns,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise BinanceFuturesError(f"invalid depth snapshot: {error}") from error


def normalize_hft_message(
    message: Mapping[str, Any],
    received_at: datetime,
    received_monotonic_ns: int | None = None,
) -> DepthDelta | BookTicker | AggregateTrade | MarkPriceEvent:
    if not isinstance(message, Mapping):
        raise BinanceHftStreamError("WebSocket message must be an object")
    payload = message.get("data", message)
    if not isinstance(payload, Mapping):
        raise BinanceHftStreamError("combined stream data must be an object")
    event_type = payload.get("e")
    try:
        symbol = str(payload["s"])
        event_time = _utc_milliseconds(payload["E"], "event time")
        if event_type == "depthUpdate":
            return DepthDelta(
                symbol=symbol,
                first_update_id=int(payload["U"]),
                final_update_id=int(payload["u"]),
                previous_final_update_id=int(payload["pu"]),
                bids=_levels(payload["b"], "depth bids"),
                asks=_levels(payload["a"], "depth asks"),
                exchange_time=event_time,
                received_at=received_at,
                transaction_time=_optional_utc_milliseconds(
                    payload.get("T"), "depth transaction time"
                ),
                received_monotonic_ns=received_monotonic_ns,
            )
        if event_type == "bookTicker":
            return BookTicker(
                symbol=symbol,
                update_id=int(payload["u"]),
                bid_price=Decimal(str(payload["b"])),
                bid_quantity=Decimal(str(payload["B"])),
                ask_price=Decimal(str(payload["a"])),
                ask_quantity=Decimal(str(payload["A"])),
                exchange_time=event_time,
                received_at=received_at,
                transaction_time=_optional_utc_milliseconds(
                    payload.get("T"), "bookTicker transaction time"
                ),
                received_monotonic_ns=received_monotonic_ns,
            )
        if event_type == "aggTrade":
            return AggregateTrade(
                symbol=symbol,
                aggregate_trade_id=int(payload["a"]),
                price=Decimal(str(payload["p"])),
                quantity=Decimal(str(payload["q"])),
                buyer_is_maker=payload["m"],
                exchange_time=event_time,
                received_at=received_at,
                transaction_time=_utc_milliseconds(
                    payload["T"], "trade transaction time"
                ),
                received_monotonic_ns=received_monotonic_ns,
            )
        if event_type == "markPriceUpdate":
            return MarkPriceEvent(
                symbol=symbol,
                mark_price=Decimal(str(payload["p"])),
                funding_rate=Decimal(str(payload["r"])),
                next_funding_time=_utc_milliseconds(
                    payload["T"], "next funding time"
                ),
                exchange_time=event_time,
                received_at=received_at,
                received_monotonic_ns=received_monotonic_ns,
            )
    except (KeyError, TypeError, ValueError, ArithmeticError) as error:
        raise BinanceHftStreamError(
            f"malformed {event_type or 'unknown'} event: {error}"
        ) from error
    raise BinanceHftStreamError(f"unsupported stream event: {event_type}")


class BinanceHftStream:
    def __init__(
        self,
        *,
        base_url: str = "wss://fstream.binance.com",
        open_timeout: float = 5,
        read_timeout: float = 1,
        shutdown_timeout: float = 3,
        queue_maxsize: int = 4096,
        lifecycle: Callable[[dict], None] = lambda value: None,
    ) -> None:
        if not base_url.startswith("wss://"):
            raise ValueError("HFT WebSocket URL must use wss")
        if min(open_timeout, read_timeout, shutdown_timeout) <= 0:
            raise ValueError("HFT WebSocket timeouts must be positive")
        if queue_maxsize < 1:
            raise ValueError("HFT stream queue size must be positive")
        self.base_url = base_url
        self.open_timeout = open_timeout
        self.read_timeout = read_timeout
        self.shutdown_timeout = shutdown_timeout
        self.queue_maxsize = queue_maxsize
        self.lifecycle = lifecycle
        self._public_ready = asyncio.Event()
        self._market_ready = asyncio.Event()
        self._last_socket_receive_ns = {"public": None, "market": None}
        self._last_depth_receive_ns: int | None = None
        self._stream_queue_current = 0
        self._stream_queue_high_water = 0
        self._backpressure_overflow_count = 0
        self._read_timeout_count = 0

    def telemetry(self, now_ns: int | None = None) -> dict[str, Any]:
        observed_now = time.monotonic_ns() if now_ns is None else now_ns

        def age(received_ns: int | None) -> Decimal | None:
            if received_ns is None:
                return None
            return Decimal(max(observed_now - received_ns, 0)) / Decimal("1000000")

        return {
            "socket_receive_age_by_route_ms": {
                route: age(received_ns)
                for route, received_ns in self._last_socket_receive_ns.items()
            },
            "depth_socket_receive_age_ms": age(self._last_depth_receive_ns),
            "stream_queue_current": self._stream_queue_current,
            "stream_queue_high_water": self._stream_queue_high_water,
            "backpressure_overflow_count": self._backpressure_overflow_count,
            "websocket_read_timeout_count": self._read_timeout_count,
        }

    async def wait_public_ready(self) -> None:
        await self._public_ready.wait()

    async def wait_market_ready(self) -> None:
        await self._market_ready.wait()

    def urls(self, symbol: str) -> tuple[str, str]:
        if symbol not in {"BTCUSDC", "BTCUSDT"}:
            raise ValueError("unsupported HFT symbol")
        name = symbol.lower()
        public_streams = (
            f"{name}@depth@100ms",
            f"{name}@bookTicker",
        )
        market_streams = (
            f"{name}@aggTrade",
            f"{name}@markPrice@1s",
        )
        return (
            self.base_url + "/public/stream?streams=" + "/".join(public_streams),
            self.base_url + "/market/stream?streams=" + "/".join(market_streams),
        )

    async def events(self, symbol: str) -> AsyncIterator[
        DepthDelta | BookTicker | AggregateTrade | MarkPriceEvent
    ]:
        queue: asyncio.Queue[
            DepthDelta | BookTicker | AggregateTrade | MarkPriceEvent | BaseException
        ] = asyncio.Queue(maxsize=self.queue_maxsize)
        terminal_errors: asyncio.Queue[BaseException] = asyncio.Queue(maxsize=2)

        def report_terminal(error: BaseException) -> None:
            try:
                terminal_errors.put_nowait(error)
            except asyncio.QueueFull:
                pass

        async def pump(route: str, url: str) -> None:
            self.lifecycle({
                "event": "websocket_connecting",
                "route": route,
            })
            try:
                async with connect(
                    url,
                    open_timeout=self.open_timeout,
                    ping_interval=20,
                    ping_timeout=20,
                    process_exception=process_exception,
                    max_queue=4096,
                ) as socket:
                    self.lifecycle({
                        "event": "websocket_connected",
                        "route": route,
                    })
                    self.lifecycle({
                        "event": "subscribing",
                        "route": route,
                    })
                    if route == "public":
                        self._public_ready.set()
                    else:
                        self._market_ready.set()
                    self.lifecycle({
                        "event": "subscription_confirmed",
                        "route": route,
                        "confirmation": "url_subscription_connection_established",
                    })
                    if route == "market":
                        self.lifecycle({
                            "event": "market_stream_ready",
                            "route": route,
                        })
                    timeout_reported = False
                    while True:
                        try:
                            raw = await asyncio.wait_for(
                                socket.recv(), timeout=self.read_timeout
                            )
                        except TimeoutError:
                            self._read_timeout_count += 1
                            if not timeout_reported:
                                self.lifecycle({
                                    "event": "websocket_timeout",
                                    "route": route,
                                    "timeout_seconds": self.read_timeout,
                                })
                                timeout_reported = True
                            continue
                        timeout_reported = False
                        received = datetime.now(timezone.utc)
                        received_monotonic_ns = time.monotonic_ns()
                        self._last_socket_receive_ns[route] = received_monotonic_ns
                        try:
                            payload = json.loads(raw)
                        except (TypeError, json.JSONDecodeError) as error:
                            raise BinanceHftStreamError(
                                "invalid WebSocket JSON"
                            ) from error
                        event = normalize_hft_message(
                            payload, received, received_monotonic_ns
                        )
                        if isinstance(event, DepthDelta):
                            self._last_depth_receive_ns = received_monotonic_ns
                        try:
                            queue.put_nowait(event)
                        except asyncio.QueueFull as error:
                            self._backpressure_overflow_count += 1
                            self.lifecycle({
                                "event": "websocket_backpressure",
                                "route": route,
                                "queue_size": queue.qsize(),
                                "queue_capacity": self.queue_maxsize,
                                "overflow_count": self._backpressure_overflow_count,
                            })
                            raise BinanceHftBackpressureError(
                                f"stream_queue_overflow:{route}"
                            ) from error
                        self._stream_queue_current = queue.qsize()
                        self._stream_queue_high_water = max(
                            self._stream_queue_high_water,
                            self._stream_queue_current,
                        )
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, WebSocketException, OSError) as error:
                report_terminal(BinanceHftStreamError(
                    f"Binance HFT stream disconnected ambiguously: {error}"
                ))
            except BaseException as error:
                report_terminal(error)
            finally:
                self.lifecycle({
                    "event": "websocket_disconnected",
                    "route": route,
                })

        public_url, market_url = self.urls(symbol)
        tasks = [
            asyncio.create_task(pump("public", public_url)),
            asyncio.create_task(pump("market", market_url)),
        ]
        item_task: asyncio.Task | None = None
        error_task: asyncio.Task | None = None

        try:
            while True:
                item_task = asyncio.create_task(queue.get())
                error_task = asyncio.create_task(terminal_errors.get())
                done, pending = await asyncio.wait(
                    {item_task, error_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if error_task in done:
                    item_task.cancel()
                    await asyncio.gather(item_task, return_exceptions=True)
                    raise error_task.result()
                error_task.cancel()
                await asyncio.gather(error_task, return_exceptions=True)
                item = item_task.result()
                self._stream_queue_current = queue.qsize()
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            waiter_tasks = [
                task
                for task in (item_task, error_task)
                if task is not None
            ]

            for task in waiter_tasks:
                if not task.done():
                    task.cancel()

            if waiter_tasks:
                await asyncio.gather(
                    *waiter_tasks,
                    return_exceptions=True,
                )

            for task in tasks:
                task.cancel()

            done, pending = await asyncio.wait(
                tasks,
                timeout=self.shutdown_timeout,
            )

            for task in pending:
                task.cancel()

            if pending:
                await asyncio.gather(
                    *pending,
                    return_exceptions=True,
                )

            for task in done:
                try:
                    task.result()
                except BaseException:
                    pass

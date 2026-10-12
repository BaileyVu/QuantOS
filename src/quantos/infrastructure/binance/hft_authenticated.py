"""Authenticated Binance USD-M transport for HFT Execution only."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import os
import time
from typing import Any, AsyncIterator, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from quantos.domain.execution.hft_authenticated import HftExecutionEnvironment


REST_BASE_URLS = {
    HftExecutionEnvironment.TESTNET: "https://testnet.binancefuture.com",
    HftExecutionEnvironment.MAINNET: "https://fapi.binance.com",
}
WS_BASE_URLS = {
    HftExecutionEnvironment.TESTNET: "wss://stream.binancefuture.com",
    HftExecutionEnvironment.MAINNET: "wss://fstream.binance.com",
}
WALLET_MAINNET_BASE_URL = "https://api.binance.com"


class BinanceAuthenticatedError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        status_code: int | None = None,
        retryable: bool = False,
        ambiguous: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class BinanceCredentials:
    api_key: str
    api_secret: str

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "BinanceCredentials":
        source = os.environ if environment is None else environment
        key = source.get("QUANTOS_BINANCE_API_KEY", "")
        secret = source.get("QUANTOS_BINANCE_API_SECRET", "")
        if not key or not secret:
            raise BinanceAuthenticatedError(
                "Binance credentials are missing from required environment variables"
            )
        return cls(key, secret)

    def health(self) -> dict[str, Any]:
        return {
            "api_key_present": bool(self.api_key),
            "api_secret_present": bool(self.api_secret),
            "api_key_fingerprint": hashlib.sha256(
                self.api_key.encode("utf-8")
            ).hexdigest()[:8],
            "credential_source": "environment",
            "credentials_redacted": True,
        }


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


HttpTransport = Callable[
    [str, str, Mapping[str, str], bytes | None, float], HttpResponse
]


def _default_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
) -> HttpResponse:
    request = Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urlopen(request, timeout=timeout) as response:
            return HttpResponse(
                int(response.status), dict(response.headers.items()), response.read()
            )
    except HTTPError as error:
        return HttpResponse(
            int(error.code), dict(error.headers.items()), error.read()
        )
    except (TimeoutError, URLError, OSError) as error:
        raise BinanceAuthenticatedError(
            "Binance authenticated request transport failed",
            retryable=True,
            ambiguous=method in {"POST", "PUT", "DELETE"},
        ) from error


@dataclass(slots=True)
class BinanceRateLimitState:
    used_weight_1m: int = 0
    order_count_10s: int = 0
    order_count_1m: int = 0
    retry_after_seconds: float | None = None
    banned: bool = False
    warning: bool = False

    def payload(self) -> dict[str, Any]:
        return {
            "used_weight_1m": self.used_weight_1m,
            "order_count_10s": self.order_count_10s,
            "order_count_1m": self.order_count_1m,
            "retry_after_seconds": self.retry_after_seconds,
            "banned": self.banned,
            "warning": self.warning,
        }


class BinanceUsdmAuthenticatedClient:
    def __init__(
        self,
        environment: HftExecutionEnvironment,
        credentials: BinanceCredentials,
        *,
        recv_window: int = 5000,
        request_timeout: float = 5,
        transport: HttpTransport = _default_transport,
        timestamp_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        sleep: Callable[[float], None] = time.sleep,
        weight_warning: int = 2000,
        order_warning_10s: int = 80,
    ) -> None:
        if environment is HftExecutionEnvironment.PAPER:
            raise ValueError("PAPER has no authenticated Binance client")
        if recv_window < 1 or recv_window > 60000:
            raise ValueError("recvWindow must be in 1..60000 milliseconds")
        if request_timeout <= 0:
            raise ValueError("authenticated request timeout must be positive")
        self.environment = environment
        self.credentials = credentials
        self.base_url = REST_BASE_URLS[environment]
        self.websocket_base_url = WS_BASE_URLS[environment]
        self.recv_window = recv_window
        self.request_timeout = request_timeout
        self.transport = transport
        self.timestamp_ms = timestamp_ms
        self.sleep = sleep
        self.clock_offset_ms = 0
        self.rate_limits = BinanceRateLimitState()
        self.weight_warning = weight_warning
        self.order_warning_10s = order_warning_10s

    @property
    def order_creation_suppressed(self) -> bool:
        return self.rate_limits.banned or self.rate_limits.warning

    def credential_health(self) -> dict[str, Any]:
        return {
            "environment": self.environment.value,
            **self.credentials.health(),
        }

    def calibrate_clock(self, samples: int = 3) -> dict[str, Any]:
        if samples < 1:
            raise ValueError("clock samples must be positive")
        observed = []
        for _ in range(samples):
            before = self.timestamp_ms()
            payload = self._request("GET", "/fapi/v1/time", signed=False)
            after = self.timestamp_ms()
            server = int(payload["serverTime"])
            midpoint = (before + after) // 2
            observed.append((after - before, server - midpoint))
        observed.sort(key=lambda item: item[0])
        selected = observed[: max(1, min(3, len(observed)))]
        offsets = sorted(item[1] for item in selected)
        self.clock_offset_ms = offsets[len(offsets) // 2]
        return {
            "clock_offset_ms": self.clock_offset_ms,
            "sample_count": samples,
            "best_rtt_ms": observed[0][0],
        }

    def exchange_info(self) -> dict[str, Any]:
        return self._object(self._request("GET", "/fapi/v1/exchangeInfo", signed=False))

    def book_ticker(self, symbol: str) -> dict[str, Any]:
        return self._object(self._request(
            "GET", "/fapi/v1/ticker/bookTicker", {"symbol": symbol}, signed=False
        ))

    def account(self) -> dict[str, Any]:
        return self._object(self._request("GET", "/fapi/v3/account"))

    def account_info(self) -> dict[str, Any]:
        return self._mainnet_wallet_object("/sapi/v1/account/info")

    def account_status(self) -> dict[str, Any]:
        return self._mainnet_wallet_object("/sapi/v1/account/status")

    def api_key_permissions(self) -> dict[str, Any]:
        return self._mainnet_wallet_object("/sapi/v1/account/apiRestrictions")

    def api_trading_status(self) -> dict[str, Any]:
        return self._mainnet_wallet_object("/sapi/v1/account/apiTradingStatus")

    def _mainnet_wallet_object(self, path: str) -> dict[str, Any]:
        if self.environment is not HftExecutionEnvironment.MAINNET:
            raise BinanceAuthenticatedError(
                "wallet permission diagnostics are MAINNET-only"
            )
        return self._object(self._request(
            "GET", path, base_url=WALLET_MAINNET_BASE_URL
        ))

    def account_config(self) -> dict[str, Any]:
        return self._object(self._request("GET", "/fapi/v1/accountConfig"))

    def symbol_config(self, symbol: str) -> list[dict[str, Any]]:
        return self._array(self._request(
            "GET", "/fapi/v1/symbolConfig", {"symbol": symbol}
        ))

    def order_rate_limits(self) -> list[dict[str, Any]]:
        return self._array(self._request("GET", "/fapi/v1/rateLimit/order"))

    def balances(self) -> list[dict[str, Any]]:
        return self._array(self._request("GET", "/fapi/v3/balance"))

    def position_risk(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        return self._array(self._request("GET", "/fapi/v3/positionRisk", params))

    def commission_rate(self, symbol: str) -> dict[str, Any]:
        return self._object(self._request(
            "GET", "/fapi/v1/commissionRate", {"symbol": symbol}
        ))

    def position_mode(self) -> dict[str, Any]:
        return self._object(self._request("GET", "/fapi/v1/positionSide/dual"))

    def set_one_way_mode(self) -> dict[str, Any]:
        return self._object(self._request(
            "POST", "/fapi/v1/positionSide/dual", {"dualSidePosition": "false"}
        ))

    def set_isolated_margin(self, symbol: str) -> dict[str, Any]:
        return self._object(self._request(
            "POST", "/fapi/v1/marginType",
            {"symbol": symbol, "marginType": "ISOLATED"},
        ))

    def set_leverage(self, symbol: str, leverage: int = 5) -> dict[str, Any]:
        if leverage != 5:
            raise ValueError("QuantOS HFT leverage must be exactly 5")
        return self._object(self._request(
            "POST", "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": leverage},
        ))

    def open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        return self._array(self._request(
            "GET", "/fapi/v1/openOrders", params
        ))

    def query_order(self, symbol: str, client_order_id: str) -> dict[str, Any] | None:
        try:
            return self._object(self._request(
                "GET", "/fapi/v1/order",
                {"symbol": symbol, "origClientOrderId": client_order_id},
            ))
        except BinanceAuthenticatedError as error:
            if error.code == -2013:
                return None
            raise

    def test_order(self, **parameters: Any) -> dict[str, Any]:
        values = self._order_parameters(parameters)
        values.update({"type": "LIMIT", "timeInForce": "GTX"})
        payload = self._request(
            "POST", "/fapi/v1/order/test", values
        )
        return self._object(payload)

    def new_limit_gtx(self, **parameters: Any) -> dict[str, Any]:
        values = self._order_parameters(parameters)
        values.update({"type": "LIMIT", "timeInForce": "GTX"})
        return self._object(self._request("POST", "/fapi/v1/order", values))

    def cancel_order(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        return self._object(self._request(
            "DELETE", "/fapi/v1/order",
            {"symbol": symbol, "origClientOrderId": client_order_id},
        ))

    def emergency_market_exit(self, **parameters: Any) -> dict[str, Any]:
        values = self._order_parameters(parameters)
        values.update({"type": "MARKET", "reduceOnly": "true"})
        values.pop("price", None)
        values.pop("timeInForce", None)
        return self._object(self._request("POST", "/fapi/v1/order", values))

    def start_listen_key(self) -> str:
        payload = self._object(self._request(
            "POST", "/fapi/v1/listenKey", signed=False, api_key=True
        ))
        key = payload.get("listenKey")
        if not isinstance(key, str) or not key:
            raise BinanceAuthenticatedError("listen key response is malformed")
        return key

    def keepalive_listen_key(self, listen_key: str) -> None:
        self._request(
            "PUT", "/fapi/v1/listenKey", {"listenKey": listen_key},
            signed=False, api_key=True,
        )

    def close_listen_key(self, listen_key: str) -> None:
        self._request(
            "DELETE", "/fapi/v1/listenKey", {"listenKey": listen_key},
            signed=False, api_key=True,
        )

    def _order_parameters(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        required = {"symbol", "side", "quantity", "client_order_id"}
        if not required.issubset(parameters):
            raise ValueError("order parameters are incomplete")
        result = {
            "symbol": str(parameters["symbol"]),
            "side": str(parameters["side"]),
            "quantity": str(parameters["quantity"]),
            "newClientOrderId": str(parameters["client_order_id"]),
            "newOrderRespType": "RESULT",
        }
        if "price" in parameters:
            result["price"] = str(parameters["price"])
        if parameters.get("reduce_only"):
            result["reduceOnly"] = "true"
        return result

    def _request(
        self,
        method: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        signed: bool = True,
        api_key: bool | None = None,
        base_url: str | None = None,
    ) -> Any:
        values = dict(params or {})
        if signed:
            values["recvWindow"] = self.recv_window
            values["timestamp"] = self.timestamp_ms() + self.clock_offset_ms
        encoded = urlencode(sorted(values.items()))
        if signed:
            signature = hmac.new(
                self.credentials.api_secret.encode("utf-8"),
                encoded.encode("utf-8"), hashlib.sha256,
            ).hexdigest()
            encoded = f"{encoded}&signature={signature}" if encoded else f"signature={signature}"
        headers = {"User-Agent": "QuantOS/0.1"}
        if signed or api_key:
            headers["X-MBX-APIKEY"] = self.credentials.api_key
        body = None
        url = (base_url or self.base_url) + path
        if method in {"POST", "PUT"}:
            body = encoded.encode("ascii")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif encoded:
            url += "?" + encoded
        attempts = 2 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = self.transport(
                    method, url, headers, body, self.request_timeout
                )
            except BinanceAuthenticatedError:
                if attempt + 1 == attempts:
                    raise
                self.sleep(0.25 * (2 ** attempt))
                continue
            self._observe_limits(response)
            if 200 <= response.status < 300:
                try:
                    return json.loads(response.body or b"{}")
                except (TypeError, json.JSONDecodeError) as error:
                    raise BinanceAuthenticatedError(
                        "Binance authenticated response is invalid JSON"
                    ) from error
            error = self._error(response, method)
            if error.retryable and method == "GET" and attempt + 1 < attempts:
                self.sleep(error.retry_after or 0.25 * (2 ** attempt))
                continue
            raise error
        raise AssertionError("unreachable authenticated request loop")

    def _observe_limits(self, response: HttpResponse) -> None:
        lowered = {key.lower(): value for key, value in response.headers.items()}
        def integer(name: str) -> int:
            try:
                return int(lowered.get(name, "0"))
            except ValueError:
                return 0
        self.rate_limits.used_weight_1m = integer("x-mbx-used-weight-1m")
        self.rate_limits.order_count_10s = integer("x-mbx-order-count-10s")
        self.rate_limits.order_count_1m = integer("x-mbx-order-count-1m")
        retry = lowered.get("retry-after")
        try:
            self.rate_limits.retry_after_seconds = float(retry) if retry else None
        except ValueError:
            self.rate_limits.retry_after_seconds = None
        if response.status == 418:
            self.rate_limits.banned = True
        self.rate_limits.warning = (
            response.status in {418, 429}
            or self.rate_limits.used_weight_1m >= self.weight_warning
            or self.rate_limits.order_count_10s >= self.order_warning_10s
        )

    def _error(self, response: HttpResponse, method: str) -> BinanceAuthenticatedError:
        code = None
        message = "Binance authenticated request failed"
        try:
            payload = json.loads(response.body or b"{}")
            if isinstance(payload, dict):
                code = int(payload["code"]) if "code" in payload else None
                message = str(payload.get("msg", message))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        retryable = response.status in {418, 429} or 500 <= response.status < 600
        return BinanceAuthenticatedError(
            f"Binance error {code}: {message}" if code is not None else message,
            code=code,
            status_code=response.status,
            retryable=retryable,
            ambiguous=method in {"POST", "PUT", "DELETE"} and retryable,
            retry_after=self.rate_limits.retry_after_seconds,
        )

    @staticmethod
    def _object(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise BinanceAuthenticatedError("Binance response must be an object")
        return value

    @staticmethod
    def _array(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise BinanceAuthenticatedError("Binance response must be an object array")
        return value


class BinanceHftUserDataStream:
    def __init__(
        self,
        client: BinanceUsdmAuthenticatedClient,
        *,
        read_timeout: float = 5,
        keepalive_seconds: float = 30 * 60,
        reconnect_delay: float = 1,
        lifecycle: Callable[[dict[str, Any]], None] = lambda payload: None,
        connect_factory: Callable[..., Any] = connect,
        async_sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        if min(read_timeout, keepalive_seconds, reconnect_delay) <= 0:
            raise ValueError("user-data stream timeouts must be positive")
        self.client = client
        self.read_timeout = read_timeout
        self.keepalive_seconds = keepalive_seconds
        self.reconnect_delay = reconnect_delay
        self.lifecycle = lifecycle
        self.connect_factory = connect_factory
        self.async_sleep = async_sleep
        self.last_order_update_monotonic: float | None = None
        self.last_account_update_monotonic: float | None = None
        self.health = "NOT_READY"

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            listen_key = await asyncio.to_thread(self.client.start_listen_key)
            url = f"{self.client.websocket_base_url}/private/ws/{listen_key}"
            self.lifecycle({"event": "user_data_connecting"})
            self.health = "CONNECTING"
            keepalive: asyncio.Task | None = None
            try:
                async with self.connect_factory(
                    url, open_timeout=self.client.request_timeout,
                    ping_interval=20, ping_timeout=20,
                ) as socket:
                    self.lifecycle({"event": "user_data_ready"})
                    self.health = "READY"
                    keepalive = asyncio.create_task(self._keepalive(listen_key))
                    while True:
                        try:
                            raw = await asyncio.wait_for(
                                socket.recv(), timeout=self.read_timeout
                            )
                        except TimeoutError:
                            self.lifecycle({"event": "user_data_timeout"})
                            continue
                        try:
                            payload = json.loads(raw)
                        except (TypeError, json.JSONDecodeError) as error:
                            raise BinanceAuthenticatedError(
                                "invalid user-data WebSocket JSON"
                            ) from error
                        if not isinstance(payload, dict):
                            raise BinanceAuthenticatedError(
                                "user-data event must be an object"
                            )
                        event_type = payload.get("e")
                        if event_type == "listenKeyExpired":
                            self.lifecycle({"event": "user_data_expired"})
                            self.health = "EXPIRED"
                            break
                        if event_type == "ORDER_TRADE_UPDATE":
                            self.last_order_update_monotonic = time.monotonic()
                        elif event_type == "ACCOUNT_UPDATE":
                            self.last_account_update_monotonic = time.monotonic()
                        elif event_type != "ACCOUNT_CONFIG_UPDATE":
                            continue
                        yield payload
            except asyncio.CancelledError:
                raise
            except (ConnectionClosed, WebSocketException, OSError, BinanceAuthenticatedError) as error:
                self.health = "DISCONNECTED"
                self.lifecycle({
                    "event": "user_data_disconnected",
                    "error": str(error),
                })
            finally:
                if keepalive is not None:
                    keepalive.cancel()
                    await asyncio.gather(keepalive, return_exceptions=True)
                try:
                    await asyncio.to_thread(self.client.close_listen_key, listen_key)
                except BaseException:
                    pass
            self.lifecycle({"event": "user_data_reconnecting"})
            self.health = "RECONNECTING"
            await self.async_sleep(self.reconnect_delay)

    async def _keepalive(self, listen_key: str) -> None:
        while True:
            await self.async_sleep(self.keepalive_seconds)
            await asyncio.to_thread(self.client.keepalive_listen_key, listen_key)

"""Public Binance USD-M Futures REST adapter."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from quantos.domain.market_data import Candle
from quantos.domain.market_data.futures import FuturesSymbolRules, parse_usdm_exchange_info


class BinanceFuturesError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False,
                 status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class FundingObservation:
    timestamp: datetime
    rate: Decimal
    mark_price: Decimal

    @property
    def funding_id(self) -> str:
        return f"BTCUSDT|{self.timestamp.isoformat()}"


HttpGet = Callable[[str], bytes]


def _default_get(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "QuantOS/0.1"})
    try:
        with urlopen(request, timeout=20) as response:
            return response.read()
    except HTTPError as error:
        raise BinanceFuturesError(
            f"Binance Futures HTTP {error.code}",
            retryable=error.code in {418, 429} or 500 <= error.code < 600,
            status_code=error.code,
        ) from error
    except (TimeoutError, URLError, OSError) as error:
        raise BinanceFuturesError(
            f"Binance Futures request failed: {error}", retryable=True
        ) from error


class BinanceUsdmPublicClient:
    def __init__(self, base_url: str = "https://fapi.binance.com",
                 http_get: HttpGet = _default_get) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("Binance Futures base URL must use HTTPS")
        self.base_url = base_url.rstrip("/")
        self.http_get = http_get

    def _json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = "" if not params else "?" + urlencode(params)
        try:
            return json.loads(self.http_get(self.base_url + path + query))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise BinanceFuturesError("invalid Binance Futures JSON") from error

    def exchange_info(self) -> dict[str, Any]:
        payload = self._json("/fapi/v1/exchangeInfo")
        if not isinstance(payload, dict):
            raise BinanceFuturesError("exchangeInfo response must be an object")
        return payload

    def symbol_rules(self, symbol: str = "BTCUSDT") -> FuturesSymbolRules:
        return parse_usdm_exchange_info(self.exchange_info(), symbol)

    def klines(self, symbol: str = "BTCUSDT", interval: str = "1m",
               start_time: datetime | None = None, end_time: datetime | None = None,
               limit: int = 1500) -> tuple[Candle, ...]:
        if symbol != "BTCUSDT" or interval != "1m":
            raise ValueError("V1 supports BTCUSDT 1m only")
        if limit < 1 or limit > 1500:
            raise ValueError("limit must be 1..1500")
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        for name, value in (("startTime", start_time), ("endTime", end_time)):
            if value is not None:
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError(f"{name} must be timezone-aware")
                params[name] = int(value.timestamp() * 1000)
        payload = self._json("/fapi/v1/klines", params)
        if not isinstance(payload, list):
            raise BinanceFuturesError("klines response must be an array")
        return tuple(self._candle(row, symbol) for row in payload)

    def historical_klines(self, start_time: datetime, end_time: datetime,
                          symbol: str = "BTCUSDT") -> tuple[Candle, ...]:
        if start_time >= end_time:
            raise ValueError("start_time must precede end_time")
        result: list[Candle] = []
        cursor = start_time
        while cursor < end_time:
            page = self.klines(symbol, "1m", cursor, end_time, 1500)
            if not page:
                break
            for candle in page:
                if candle.open_time >= end_time:
                    break
                if not result or candle.open_time > result[-1].open_time:
                    result.append(candle)
            next_cursor = page[-1].open_time
            if next_cursor < cursor:
                raise BinanceFuturesError("non-monotonic kline page")
            cursor = next_cursor.replace(second=0, microsecond=0)
            cursor = datetime.fromtimestamp(cursor.timestamp() + 60, timezone.utc)
            if len(page) < 1500:
                break
        return tuple(result)

    def mark_price(self, symbol: str = "BTCUSDT") -> Decimal:
        payload = self._json("/fapi/v1/premiumIndex", {"symbol": symbol})
        if not isinstance(payload, dict) or "markPrice" not in payload:
            raise BinanceFuturesError("mark price is missing")
        return Decimal(str(payload["markPrice"]))

    def funding_rate(self, symbol: str = "BTCUSDT") -> Decimal:
        payload = self._json("/fapi/v1/premiumIndex", {"symbol": symbol})
        if not isinstance(payload, dict) or "lastFundingRate" not in payload:
            raise BinanceFuturesError("funding rate is missing")
        return Decimal(str(payload["lastFundingRate"]))

    def server_time(self) -> datetime:
        payload = self._json("/fapi/v1/time")
        if not isinstance(payload, dict) or set(payload) != {"serverTime"}:
            raise BinanceFuturesError("server time response is malformed")
        try:
            return datetime.fromtimestamp(
                int(payload["serverTime"]) / 1000, timezone.utc
            )
        except (TypeError, ValueError, OSError) as error:
            raise BinanceFuturesError("server time is invalid") from error

    def funding_history(
        self, start_time: datetime, end_time: datetime,
        symbol: str = "BTCUSDT",
    ) -> tuple[FundingObservation, ...]:
        if symbol != "BTCUSDT":
            raise ValueError("V1 supports BTCUSDT only")
        if start_time.tzinfo is None or end_time.tzinfo is None:
            raise ValueError("funding range must be timezone-aware")
        if start_time >= end_time:
            return ()
        payload = self._json("/fapi/v1/fundingRate", {
            "symbol": symbol,
            "startTime": int(start_time.timestamp() * 1000),
            "endTime": int(end_time.timestamp() * 1000),
            "limit": 1000,
        })
        if not isinstance(payload, list):
            raise BinanceFuturesError("funding history response must be an array")
        result = []
        previous = None
        for row in payload:
            if not isinstance(row, dict) or not {
                "symbol", "fundingTime", "fundingRate", "markPrice"
            }.issubset(row):
                raise BinanceFuturesError("funding history row is malformed")
            try:
                if row["symbol"] != symbol:
                    raise ValueError("unexpected funding symbol")
                timestamp = datetime.fromtimestamp(
                    int(row["fundingTime"]) / 1000, timezone.utc
                )
                rate = Decimal(str(row["fundingRate"]))
                mark = Decimal(str(row["markPrice"]))
                if not rate.is_finite() or not mark.is_finite() or mark <= 0:
                    raise ValueError("invalid funding values")
            except (TypeError, ValueError, ArithmeticError, OSError) as error:
                raise BinanceFuturesError(
                    f"invalid funding history row: {error}"
                ) from error
            if previous is not None and timestamp <= previous:
                raise BinanceFuturesError("funding history is not chronological")
            previous = timestamp
            result.append(FundingObservation(timestamp, rate, mark))
        return tuple(result)

    @staticmethod
    def _candle(row: Any, symbol: str) -> Candle:
        if not isinstance(row, list) or len(row) < 9:
            raise BinanceFuturesError("malformed kline row")
        try:
            return Candle(
                symbol=symbol,
                interval="1m",
                open_time=datetime.fromtimestamp(int(row[0]) / 1000, timezone.utc),
                close_time=datetime.fromtimestamp(int(row[6]) / 1000, timezone.utc),
                open=Decimal(str(row[1])),
                high=Decimal(str(row[2])),
                low=Decimal(str(row[3])),
                close=Decimal(str(row[4])),
                volume=Decimal(str(row[5])),
                quote_volume=Decimal(str(row[7])),
                trade_count=int(row[8]),
            )
        except (TypeError, ValueError, ArithmeticError) as error:
            raise BinanceFuturesError(f"invalid kline row: {error}") from error


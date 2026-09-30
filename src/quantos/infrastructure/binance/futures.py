"""Public Binance USD-M Futures REST adapter."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from quantos.domain.market_data import Candle
from quantos.domain.market_data.futures import FuturesSymbolRules, parse_usdm_exchange_info


class BinanceFuturesError(RuntimeError):
    pass


HttpGet = Callable[[str], bytes]


def _default_get(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "QuantOS/0.1"})
    try:
        with urlopen(request, timeout=20) as response:
            return response.read()
    except Exception as error:
        raise BinanceFuturesError(f"Binance Futures request failed: {error}") from error


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


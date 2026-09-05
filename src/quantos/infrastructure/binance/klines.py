"""Single-page Binance Spot historical-kline normalization adapter."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from quantos.domain.common import V1_INTERVAL, require_utc, require_v1_symbol
from quantos.domain.market_data import Candle

BINANCE_PUBLIC_DATA_URL = "https://data-api.binance.vision"
KLINES_PATH = "/api/v3/klines"
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_HTTP_GET = Callable[[str, float], bytes]


class BinanceMarketDataError(ValueError):
    """Raised when Binance market-data transport or response data is invalid."""


def _default_http_get(url: str, timeout_seconds: float) -> bytes:
    """Fetch one response body with a finite timeout and no authentication."""
    with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310 -- fixed HTTPS endpoint
        return response.read()


def _epoch_milliseconds(value: datetime) -> int:
    """Convert a UTC datetime to Unix milliseconds without float arithmetic."""
    delta = value - _UNIX_EPOCH
    return (
        (delta.days * 86_400 + delta.seconds) * 1_000
        + delta.microseconds // 1_000
    )


def _utc_datetime_from_milliseconds(value: int, *, row_index: int, field_name: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BinanceMarketDataError(
            f"kline row {row_index} {field_name} must be an integer Unix-millisecond timestamp"
        )
    try:
        return _UNIX_EPOCH + timedelta(milliseconds=value)
    except OverflowError as error:
        raise BinanceMarketDataError(
            f"kline row {row_index} {field_name} is outside the supported datetime range"
        ) from error


def _decimal_from_binance(value: object, *, row_index: int, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise BinanceMarketDataError(
            f"kline row {row_index} {field_name} must be a Binance decimal string"
        )
    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise BinanceMarketDataError(
            f"kline row {row_index} {field_name} is not a valid decimal string"
        ) from error
    if not decimal_value.is_finite():
        raise BinanceMarketDataError(
            f"kline row {row_index} {field_name} must be a finite decimal string"
        )
    return decimal_value


class BinanceSpotHistoricalKlineAdapter:
    """Fetch and normalize at most one public Binance Spot kline response page."""

    def __init__(
        self,
        *,
        http_get: _HTTP_GET | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds must be a finite positive number")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a finite positive number")
        self._http_get = http_get or _default_http_get
        self._timeout_seconds = float(timeout_seconds)

    def fetch_klines(
        self,
        *,
        symbol: str,
        interval: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[Candle, ...]:
        """Fetch one page and normalize its provider rows without dataset validation."""
        url = self._build_klines_url(
            symbol=symbol,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
        try:
            payload = self._http_get(url, self._timeout_seconds)
        except HTTPError as error:
            raise BinanceMarketDataError(f"Binance kline HTTP request failed: {error.code}") from error
        except (socket.timeout, TimeoutError) as error:
            raise BinanceMarketDataError("Binance kline request timed out") from error
        except URLError as error:
            raise BinanceMarketDataError(f"Binance kline network request failed: {error.reason}") from error
        return self._decode_klines(payload, symbol=symbol, interval=interval)

    def _build_klines_url(
        self,
        *,
        symbol: str,
        interval: str,
        start_time: datetime | None,
        end_time: datetime | None,
        limit: int | None,
    ) -> str:
        require_v1_symbol(symbol)
        if interval != V1_INTERVAL:
            raise ValueError(f"interval must be {V1_INTERVAL!r}")
        if start_time is not None:
            require_utc(start_time, "start_time")
        if end_time is not None:
            require_utc(end_time, "end_time")
        if start_time is not None and end_time is not None and end_time < start_time:
            raise ValueError("end_time must not be before start_time")
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("limit must be an integer")
            if not 1 <= limit <= 1_000:
                raise ValueError("limit must be between 1 and 1000")

        parameters: dict[str, str | int] = {
            "symbol": symbol,
            "interval": interval,
            "timeZone": "0",
        }
        if start_time is not None:
            parameters["startTime"] = _epoch_milliseconds(start_time)
        if end_time is not None:
            parameters["endTime"] = _epoch_milliseconds(end_time)
        if limit is not None:
            parameters["limit"] = limit
        return f"{BINANCE_PUBLIC_DATA_URL}{KLINES_PATH}?{urlencode(parameters)}"

    @staticmethod
    def _decode_klines(payload: object, *, symbol: str, interval: str) -> tuple[Candle, ...]:
        if not isinstance(payload, bytes):
            raise BinanceMarketDataError("Binance kline response body must be bytes")
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BinanceMarketDataError("Binance kline response contained invalid JSON") from error
        if not isinstance(decoded, list):
            raise BinanceMarketDataError("Binance kline response must be a JSON array")

        candles: list[Candle] = []
        for row_index, row in enumerate(decoded):
            if not isinstance(row, list) or len(row) != 12:
                raise BinanceMarketDataError(
                    f"kline row {row_index} must be a 12-field JSON array"
                )
            candles.append(
                BinanceSpotHistoricalKlineAdapter._normalize_row(
                    row, row_index=row_index, symbol=symbol, interval=interval
                )
            )
        return tuple(candles)

    @staticmethod
    def _normalize_row(
        row: list[object], *, row_index: int, symbol: str, interval: str
    ) -> Candle:
        open_time = _utc_datetime_from_milliseconds(
            row[0], row_index=row_index, field_name="open time"
        )
        close_time = _utc_datetime_from_milliseconds(
            row[6], row_index=row_index, field_name="close time"
        )
        if isinstance(row[8], bool) or not isinstance(row[8], int):
            raise BinanceMarketDataError(f"kline row {row_index} trade count must be an integer")
        try:
            return Candle(
                symbol=symbol,
                interval=interval,
                open_time=open_time,
                close_time=close_time,
                open=_decimal_from_binance(row[1], row_index=row_index, field_name="open"),
                high=_decimal_from_binance(row[2], row_index=row_index, field_name="high"),
                low=_decimal_from_binance(row[3], row_index=row_index, field_name="low"),
                close=_decimal_from_binance(row[4], row_index=row_index, field_name="close"),
                volume=_decimal_from_binance(row[5], row_index=row_index, field_name="volume"),
                quote_volume=_decimal_from_binance(
                    row[7], row_index=row_index, field_name="quote volume"
                ),
                trade_count=row[8],
            )
        except ValueError as error:
            raise BinanceMarketDataError(
                f"kline row {row_index} violates canonical candle validation: {error}"
            ) from error

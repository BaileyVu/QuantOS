"""Strict normalization of Binance combined UTC kline stream messages."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import re

from quantos.domain.common import V1_INTERVAL, V1_SYMBOLS
from quantos.domain.market_data import Candle, MarketEvent

_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_DECIMAL_STRING = re.compile(r"-?[0-9]+(?:\.[0-9]+)?")
_KLINE_STREAMS = {f"{symbol.lower()}@kline_{V1_INTERVAL}": symbol for symbol in V1_SYMBOLS}


class BinanceLiveMarketDataError(ValueError):
    """A fatal Binance live protocol, normalization, or continuity failure."""


class _ServerShutdown(Exception):
    """The documented combined server-shutdown control event, never a candle."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BinanceLiveMarketDataError("ambiguous duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise BinanceLiveMarketDataError("non-standard JSON numeric constant")


def _object(value: object, field: str) -> dict[str, object]:
    if type(value) is not dict:
        raise BinanceLiveMarketDataError(f"{field} must be a JSON object")
    return value


def _string(value: object, field: str) -> str:
    if type(value) is not str:
        raise BinanceLiveMarketDataError(f"{field} must be a string")
    return value


def _integer(value: object, field: str) -> int:
    if type(value) is not int:
        raise BinanceLiveMarketDataError(f"{field} must be an integer, not bool")
    return value


def _timestamp(value: object, field: str) -> datetime:
    microseconds = _integer(value, field)
    try:
        return _UNIX_EPOCH + timedelta(microseconds=microseconds)
    except OverflowError as error:
        raise BinanceLiveMarketDataError(f"{field} timestamp is outside datetime range") from error


def _decimal(value: object, field: str) -> Decimal:
    text = _string(value, field)
    if _DECIMAL_STRING.fullmatch(text) is None:
        raise BinanceLiveMarketDataError(f"{field} must be a finite Binance decimal string")
    try:
        return Decimal(text)
    except InvalidOperation as error:
        raise BinanceLiveMarketDataError(f"{field} is not a valid decimal string") from error


def normalize_live_kline(
    message: str | bytes, *, expected_streams: frozenset[str]
) -> MarketEvent | None:
    """Normalize a closed subscribed kline, suppress partials, or signal shutdown.

    E/t/T are always Unix microseconds because the adapter requests that unit.
    JSON schema checks also apply to partial updates, but they never construct a
    Candle or MarketEvent. Unused provider fields are not copied into Domain.
    """
    if type(message) not in (str, bytes):
        raise BinanceLiveMarketDataError("WebSocket message must be JSON text or UTF-8 bytes")
    try:
        text = message.decode("utf-8") if isinstance(message, bytes) else message
        decoded = json.loads(
            text, object_pairs_hook=_unique_object, parse_constant=_reject_constant
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise BinanceLiveMarketDataError("invalid or ambiguous Binance JSON message") from error

    wrapper = _object(decoded, "wrapper")
    try:
        stream = _string(wrapper["stream"], "stream")
        data = _object(wrapper["data"], "data")
        event_type = _string(data["e"], "e")
        timestamp = _timestamp(data["E"], "E")
        if stream == "!serverShutdown":
            if event_type != "serverShutdown" or "k" in data or "s" in data:
                raise BinanceLiveMarketDataError("invalid server-shutdown envelope")
            raise _ServerShutdown
        if stream not in _KLINE_STREAMS or stream not in expected_streams:
            raise BinanceLiveMarketDataError("unexpected or unsubscribed kline stream")
        if event_type != "kline":
            raise BinanceLiveMarketDataError("event type must be kline")
        symbol = _string(data["s"], "s")
        kline = _object(data["k"], "k")
        inner_symbol = _string(kline["s"], "k.s")
        interval = _string(kline["i"], "k.i")
        if symbol != _KLINE_STREAMS[stream] or inner_symbol != symbol:
            raise BinanceLiveMarketDataError("stream/outer/inner symbol mismatch")
        if interval != V1_INTERVAL:
            raise BinanceLiveMarketDataError("kline interval must be 1m")
        closed = kline["x"]
        if type(closed) is not bool:
            raise BinanceLiveMarketDataError("k.x must be an exact bool")
        open_time = _timestamp(kline["t"], "k.t")
        close_time = _timestamp(kline["T"], "k.T")
        trade_count = _integer(kline["n"], "k.n")
        values = {field: _decimal(kline[field], f"k.{field}") for field in "ohlcvq"}
    except KeyError as error:
        raise BinanceLiveMarketDataError(f"missing required Binance field {error}") from error

    if not closed:
        return None
    if open_time.second != 0 or open_time.microsecond != 0:
        raise BinanceLiveMarketDataError("kline open_time must be an exact UTC minute")
    try:
        candle = Candle(
            symbol=inner_symbol,
            interval=interval,
            open_time=open_time,
            close_time=close_time,
            open=values["o"],
            high=values["h"],
            low=values["l"],
            close=values["c"],
            volume=values["v"],
            quote_volume=values["q"],
            trade_count=trade_count,
        )
        return MarketEvent(timestamp=timestamp, candle=candle)
    except ValueError as error:
        raise BinanceLiveMarketDataError(f"invalid completed canonical candle/event: {error}") from error

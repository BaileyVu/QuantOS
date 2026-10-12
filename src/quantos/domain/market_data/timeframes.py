"""Deterministic aggregation of completed one-minute candles."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantos.domain.market_data import Candle


SUPPORTED_SIGNAL_TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h")
_TIMEFRAME_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def timeframe_minutes(timeframe: str) -> int:
    try:
        return _TIMEFRAME_MINUTES[timeframe]
    except KeyError as error:
        raise ValueError(f"unsupported signal timeframe: {timeframe}") from error


@dataclass(frozen=True, slots=True)
class SignalCandle:
    symbol: str
    interval: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trade_count: int

    def __post_init__(self) -> None:
        timeframe_minutes(self.interval)
        if self.symbol != "BTCUSDT":
            raise ValueError("V1 supports BTCUSDT only")
        if self.open_time.tzinfo is None or self.open_time.utcoffset() != timedelta(0):
            raise ValueError("open_time must be UTC")
        if self.close_time.tzinfo is None or self.close_time.utcoffset() != timedelta(0):
            raise ValueError("close_time must be UTC")
        if self.close_time <= self.open_time:
            raise ValueError("close_time must follow open_time")
        if any(not isinstance(value, Decimal) or not value.is_finite()
               for value in (self.open, self.high, self.low, self.close,
                             self.volume, self.quote_volume)):
            raise ValueError("OHLCV values must be finite Decimals")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("invalid high")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("invalid low")
        if self.volume < 0 or self.quote_volume < 0 or self.trade_count < 0:
            raise ValueError("volume and trades must not be negative")


def _bucket_start(timestamp: datetime, minutes: int) -> datetime:
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError("candle time must be UTC")
    epoch_minute = int(timestamp.timestamp()) // 60
    aligned = epoch_minute - epoch_minute % minutes
    return datetime.fromtimestamp(aligned * 60, timezone.utc)


class CompletedTimeframeAggregator:
    """Incrementally emits one candle only after every required 1m part exists."""

    def __init__(self, timeframe: str) -> None:
        self.timeframe = timeframe
        self.minutes = timeframe_minutes(timeframe)
        self._start: datetime | None = None
        self._parts: list[Candle] = []
        self.incomplete_bucket_count = 0

    def update(self, candle: Candle) -> SignalCandle | None:
        if candle.interval != "1m" or candle.symbol != "BTCUSDT":
            raise ValueError("aggregator requires BTCUSDT 1m candles")
        if candle.open_time.second or candle.open_time.microsecond:
            raise ValueError("1m candle open_time must align to a UTC minute")
        if candle.close_time != candle.open_time + timedelta(minutes=1) - timedelta(milliseconds=1):
            raise ValueError("1m candle must be complete with the canonical close boundary")
        if self.minutes == 1:
            return SignalCandle(
                candle.symbol, "1m", candle.open_time, candle.close_time,
                candle.open, candle.high, candle.low, candle.close,
                candle.volume, candle.quote_volume, candle.trade_count,
            )

        start = _bucket_start(candle.open_time, self.minutes)
        if self._start != start:
            if self._parts:
                self.incomplete_bucket_count += 1
            self._start = start
            self._parts = []
        expected = start + timedelta(minutes=len(self._parts))
        if candle.open_time != expected:
            self._parts = []
            self._start = start
            expected = start
            if candle.open_time != expected:
                self.incomplete_bucket_count += 1
                return None
        self._parts.append(candle)
        if len(self._parts) < self.minutes:
            return None
        if len(self._parts) != self.minutes:
            raise ValueError("aggregation bucket overflow")
        parts = tuple(self._parts)
        self._parts = []
        self._start = None
        return SignalCandle(
            symbol=candle.symbol,
            interval=self.timeframe,
            open_time=start,
            close_time=parts[-1].close_time,
            open=parts[0].open,
            high=max(part.high for part in parts),
            low=min(part.low for part in parts),
            close=parts[-1].close,
            volume=sum((part.volume for part in parts), Decimal(0)),
            quote_volume=sum((part.quote_volume for part in parts), Decimal(0)),
            trade_count=sum(part.trade_count for part in parts),
        )


class MultiTimeframeAggregator:
    def __init__(self, timeframes=SUPPORTED_SIGNAL_TIMEFRAMES) -> None:
        values = tuple(timeframes)
        if not values or len(set(values)) != len(values):
            raise ValueError("enabled timeframes must be non-empty and unique")
        unsupported = set(values) - set(SUPPORTED_SIGNAL_TIMEFRAMES)
        if unsupported:
            raise ValueError(f"unsupported signal timeframes: {sorted(unsupported)}")
        self.timeframes = tuple(sorted(values, key=timeframe_minutes))
        self._aggregators = {
            timeframe: CompletedTimeframeAggregator(timeframe)
            for timeframe in self.timeframes
        }

    def update(self, candle: Candle) -> dict[str, SignalCandle]:
        completed = {}
        for timeframe, aggregator in self._aggregators.items():
            result = aggregator.update(candle)
            if result is not None:
                completed[timeframe] = result
        return completed

    @property
    def incomplete_bucket_count(self) -> int:
        return sum(item.incomplete_bucket_count for item in self._aggregators.values())


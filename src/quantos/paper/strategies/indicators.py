"""Closed-candle validation and deterministic indicator helpers for V4 rules."""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import hashlib

from ..types import Candle, Signal, Snapshot

D = Decimal

def validated_bars(snapshot: Snapshot, timeframe: str, period_ms: int, minimum: int) -> tuple[Candle, ...] | None:
    bars = getattr(snapshot, f"candles_{timeframe}")
    if len(bars) < minimum or snapshot.quote.symbol != snapshot.instrument.symbol or snapshot.quote.time_ms > snapshot.at_ms:
        return None
    previous = None
    for bar in bars:
        values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if any(not value.is_finite() for value in values) or min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0:
            return None
        if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close, bar.high):
            return None
        if bar.close_ms - bar.open_ms != period_ms - 1 or bar.close_ms > snapshot.at_ms:
            return None
        if previous is not None and bar.open_ms != previous.close_ms + 1:
            return None
        previous = bar
    expected_close = ((snapshot.at_ms + 1) // period_ms) * period_ms - 1
    return bars if bars[-1].close_ms == expected_close else None

def closed_bars(snapshot: Snapshot, timeframe: str, minimum: int) -> tuple[Candle, ...] | None:
    periods = {"1m": 60_000, "5m": 300_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
    period_ms = periods.get(timeframe)
    return None if period_ms is None else validated_bars(snapshot, timeframe, period_ms, minimum)

def ema(values: tuple[Decimal, ...], period: int) -> Decimal:
    alpha = D(2) / D(period + 1)
    result = sum(values[:period], D(0)) / D(period)
    for value in values[period:]:
        result = alpha * value + (D(1) - alpha) * result
    return result

def atr_wilder(bars: tuple[Candle, ...], period: int) -> Decimal:
    trs = []
    for i, bar in enumerate(bars):
        previous = bars[i - 1].close if i else bar.close
        trs.append(max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous)))
    if len(trs) < period:
        return D(0)
    result = sum(trs[:period], D(0)) / D(period)
    for value in trs[period:]:
        result = (result * D(period - 1) + value) / D(period)
    return result

wilder_atr = atr_wilder

def floor_tick(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick

def ceil_tick(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_CEILING) * tick

def make_signal(snapshot: Snapshot, strategy_id: str, version: str, identity: str, side: str,
                entry_type: str, entry: Decimal, stop: Decimal, target: Decimal | None,
                hold: int, expires: int, reason: str, features=()) -> Signal | None:
    ins = snapshot.instrument
    if not ins.tick.is_finite() or ins.tick <= 0 or any(not x.is_finite() for x in (entry, stop)):
        return None
    entry = ceil_tick(entry, ins.tick) if side == "LONG" else floor_tick(entry, ins.tick)
    stop = floor_tick(stop, ins.tick) if side == "LONG" else ceil_tick(stop, ins.tick)
    if target is not None:
        if not target.is_finite():
            return None
        target = ceil_tick(target, ins.tick) if side == "LONG" else floor_tick(target, ins.tick)
    prices = (entry, stop) if target is None else (entry, stop, target)
    if min(prices) < ins.min_price or max(prices) > ins.max_price:
        return None
    if (side == "LONG" and not stop < entry) or (side == "SHORT" and not stop > entry):
        return None
    if target is not None and ((side == "LONG" and not entry < target) or (side == "SHORT" and not target < entry)):
        return None
    signal_id = hashlib.sha256(f"{strategy_id}:{version}:{ins.symbol}:{identity}".encode()).hexdigest()
    return Signal(strategy_id, version, signal_id, ins.symbol, side, snapshot.at_ms, expires,
                  entry_type, entry, stop, target, hold, reason, tuple(features))

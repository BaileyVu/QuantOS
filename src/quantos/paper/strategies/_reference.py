"""Causal, deliberately simple reference strategies for V3 paper evaluation.

These rules are transparent examples, not evidence of an economic edge.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import hashlib
import json
from typing import Literal

from ..types import Candle, Signal, Snapshot, Strategy

D = Decimal
BAR_MS = 5 * 60_000
MIN_BARS = 60
HOLD_SECONDS = 3600
EXPIRY_MS = 90_000


def _floor_tick(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def _ceil_tick(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def _bars(snapshot: Snapshot) -> tuple[Candle, ...] | None:
    """Return only validated, completed contiguous 5m observations."""
    bars = snapshot.candles_5m
    if len(bars) < MIN_BARS:
        return None
    if snapshot.quote.symbol != snapshot.instrument.symbol or snapshot.quote.time_ms > snapshot.at_ms:
        return None
    previous = None
    for bar in bars:
        vals = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if any(not x.is_finite() for x in vals) or min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0:
            return None
        if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close, bar.high):
            return None
        if bar.close_ms > snapshot.at_ms or bar.open_ms >= bar.close_ms or bar.close_ms - bar.open_ms != BAR_MS - 1:
            return None
        if previous is not None and bar.open_ms != previous.close_ms + 1:
            return None
        previous = bar
    expected_close_ms = ((snapshot.at_ms + 1) // BAR_MS) * BAR_MS - 1
    if bars[-1].close_ms != expected_close_ms:
        return None
    return bars


def _ema(values: tuple[Decimal, ...], period: int) -> Decimal:
    alpha = D(2) / D(period + 1)
    result = sum(values[:period], D(0)) / D(period)
    for value in values[period:]:
        result = alpha * value + (D(1) - alpha) * result
    return result


def _atr(bars: tuple[Candle, ...], period: int = 14) -> Decimal:
    trs = []
    for i, bar in enumerate(bars):
        prev = bars[i - 1].close if i else bar.close
        trs.append(max(bar.high - bar.low, abs(bar.high - prev), abs(bar.low - prev)))
    return sum(trs[-period:], D(0)) / D(period)


def _id(strategy: str, version: str, params: object, symbol: str, close_ms: int) -> str:
    payload = json.dumps(asdict(params), sort_keys=True, separators=(",", ":"), default=str)
    ph = hashlib.sha256(payload.encode()).hexdigest()[:16]
    raw = f"{strategy}:{version}:{ph}:{symbol}:{close_ms}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _signal(snapshot: Snapshot, strategy: str, version: str, params: object,
            side: Literal["LONG", "SHORT"], entry_type: Literal["LIMIT", "STOP"],
            entry: Decimal, stop: Decimal, target: Decimal, reason: str,
            features: tuple[tuple[str, Decimal], ...]) -> Signal | None:
    ins = snapshot.instrument
    if not ins.tick.is_finite() or ins.tick <= 0:
        return None
    entry = _floor_tick(entry, ins.tick) if side == "LONG" and entry_type == "LIMIT" else _ceil_tick(entry, ins.tick) if entry_type == "LIMIT" else _ceil_tick(entry, ins.tick) if side == "LONG" else _floor_tick(entry, ins.tick)
    stop = _floor_tick(stop, ins.tick) if side == "LONG" else _ceil_tick(stop, ins.tick)
    target = _ceil_tick(target, ins.tick) if side == "LONG" else _floor_tick(target, ins.tick)
    if (min(entry, stop, target) < ins.min_price or max(entry, stop, target) > ins.max_price
            or (side == "LONG" and not stop < entry < target)
            or (side == "SHORT" and not target < entry < stop)):
        return None
    bars = snapshot.candles_5m
    return Signal(strategy, version, _id(strategy, version, params, ins.symbol, bars[-1].close_ms),
                  ins.symbol, side, snapshot.at_ms, snapshot.at_ms + EXPIRY_MS,
                  entry_type, entry, stop, target, HOLD_SECONDS, reason, features)

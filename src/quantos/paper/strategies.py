"""Causal, deliberately simple reference strategies for V3 paper evaluation.

These rules are transparent examples, not evidence of an economic edge.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import hashlib
import json
from typing import Literal

from .types import Candle, Signal, Snapshot, Strategy

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


@dataclass(frozen=True)
class EmaAtrParameters:
    fast_ema: int = 20
    slow_ema: int = 50
    atr_period: int = 14
    pullback_atr: Decimal = D("0.25")
    stop_atr: Decimal = D("1.5")
    target_atr: Decimal = D("2.0")


@dataclass(frozen=True)
class EmaAtrLimitStrategy:
    parameters: EmaAtrParameters = EmaAtrParameters()
    strategy_id: str = "ema_atr_limit_pullback"
    version: str = "1.0.0"

    def evaluate(self, snapshot: Snapshot) -> Signal | None:
        bars = _bars(snapshot)
        p = self.parameters
        if (bars is None or p.fast_ema <= 0 or p.slow_ema <= p.fast_ema or p.atr_period <= 0
                or len(bars) < max(p.slow_ema, p.atr_period)
                or any(not x.is_finite() or x <= 0 for x in (p.pullback_atr, p.stop_atr, p.target_atr))):
            return None
        closes = tuple(b.close for b in bars)
        fast, slow, atr, last = _ema(closes, p.fast_ema), _ema(closes, p.slow_ema), _atr(bars, p.atr_period), bars[-1]
        if atr <= 0 or fast == slow:
            return None
        side = "LONG" if fast > slow and last.close > fast else "SHORT" if fast < slow and last.close < fast else None
        if side is None:
            return None
        entry = fast - p.pullback_atr * atr if side == "LONG" else fast + p.pullback_atr * atr
        stop = entry - p.stop_atr * atr if side == "LONG" else entry + p.stop_atr * atr
        target = entry + p.target_atr * atr if side == "LONG" else entry - p.target_atr * atr
        return _signal(snapshot, self.strategy_id, self.version, p, side, "LIMIT", entry, stop, target,
                       "EMA20/EMA50 trend with ATR14 pullback", (("ema_fast", fast), ("ema_slow", slow), ("atr", atr)))


@dataclass(frozen=True)
class DonchianAtrParameters:
    channel_period: int = 20
    atr_period: int = 14
    stop_atr: Decimal = D("1.5")
    target_atr: Decimal = D("2.0")


@dataclass(frozen=True)
class DonchianAtrStopStrategy:
    parameters: DonchianAtrParameters = DonchianAtrParameters()
    strategy_id: str = "donchian_atr_stop_breakout"
    version: str = "1.0.0"

    def evaluate(self, snapshot: Snapshot) -> Signal | None:
        bars = _bars(snapshot)
        p = self.parameters
        if (bars is None or p.channel_period < 1 or p.atr_period < 1
                or len(bars) < max(p.channel_period + 1, p.atr_period)
                or any(not x.is_finite() or x <= 0 for x in (p.stop_atr, p.target_atr))):
            return None
        prior = bars[-p.channel_period-1:-1]
        last = bars[-1]
        upper, lower, atr = max(x.high for x in prior), min(x.low for x in prior), _atr(bars, p.atr_period)
        if atr <= 0:
            return None
        if last.close > upper:
            side, entry = "LONG", last.high + snapshot.instrument.tick
        elif last.close < lower:
            side, entry = "SHORT", last.low - snapshot.instrument.tick
        else:
            return None
        stop = entry - p.stop_atr * atr if side == "LONG" else entry + p.stop_atr * atr
        target = entry + p.target_atr * atr if side == "LONG" else entry - p.target_atr * atr
        return _signal(snapshot, self.strategy_id, self.version, p, side, "STOP", entry, stop, target,
                       "Donchian20 close breakout with ATR14 exits", (("donchian_high", upper), ("donchian_low", lower), ("atr", atr)))


@dataclass(frozen=True)
class StrategyMetadata:
    strategy_id: str
    version: str
    status: str
    eligible: bool
    rationale: str


def strategy_registry() -> tuple[StrategyMetadata, ...]:
    """Registry is descriptive; E17 cannot be evaluated as a trade strategy."""
    return (
        StrategyMetadata("ema_atr_limit_pullback", "1.0.0", "PAPER_REFERENCE", True, "Causal and synthetic lifecycle tests passed; no verified economic edge"),
        StrategyMetadata("donchian_atr_stop_breakout", "1.0.0", "PAPER_REFERENCE", True, "Causal and synthetic lifecycle tests passed; no verified economic edge"),
        StrategyMetadata("E17", "UNVERIFIED_SOURCE", "MONITOR_ONLY", False, "Source identity and original timing evidence are unverified"),
    )

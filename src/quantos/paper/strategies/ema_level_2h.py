"""EMA 12/26 crosses at causal 2h swing-pivot support/resistance zones."""
from __future__ import annotations

from decimal import Decimal

from ..types import Candle, Signal, Snapshot
from .candidate_features import HOUR_MS
from .indicators import make_signal

D = Decimal
PERIOD_MS = 2 * HOUR_MS
FAST_EMA = 12
SLOW_EMA = 26
ATR_PERIOD = 14
PIVOT_K = 5
LEVEL_LOOKBACK = 288
ZONE_TOL_ATR = D("0.25")
MIN_PRIOR_TOUCHES = 2
LEAVE_ATR = D(1)
RETEST_BARS = 12
NEAR_ATR = D(1)
STOP_BUFFER_ATR = D("0.25")
TARGET_R = D("1.5")
MAX_HOLD_SECONDS = 72 * 60 * 60
MAJORS = frozenset({
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT",
})


def _closed_bars(snapshot: Snapshot) -> tuple[Candle, ...] | None:
    """Filter after the as-of boundary, then require a contiguous UTC 2h series."""
    if snapshot.quote.symbol != snapshot.instrument.symbol or snapshot.quote.time_ms > snapshot.at_ms:
        return None
    bars = tuple(bar for bar in snapshot.candles_2h
                 if bar.close_ms + 1 <= snapshot.at_ms)
    if len(bars) < LEVEL_LOOKBACK:
        return None
    previous = None
    for bar in bars:
        if (bar.open_ms % PERIOD_MS != 0 or bar.close_ms != bar.open_ms + PERIOD_MS - 1
                or any(not value.is_finite() for value in
                       (bar.open, bar.high, bar.low, bar.close, bar.volume))
                or min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0
                or bar.high < max(bar.open, bar.close, bar.low)
                or bar.low > min(bar.open, bar.close, bar.high)):
            return None
        if previous is not None and bar.open_ms != previous + PERIOD_MS:
            return None
        previous = bar.open_ms
    expected_close = (snapshot.at_ms // PERIOD_MS) * PERIOD_MS - 1
    return bars if bars[-1].close_ms == expected_close else None


def _ema(values: tuple[Decimal, ...], period: int) -> tuple[Decimal, ...]:
    """Match the source EMA initialization: seed with the first observation."""
    alpha = D(2) / D(period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return tuple(result)


def _atr(bars: tuple[Candle, ...]) -> tuple[Decimal, ...]:
    """EMA(14) true range, matching the attached causal source implementation."""
    tr = []
    previous_close = bars[0].close
    for bar in bars:
        tr.append(max(bar.high - bar.low, abs(bar.high - previous_close),
                      abs(bar.low - previous_close)))
        previous_close = bar.close
    return _ema(tuple(tr), ATR_PERIOD)


def _pivot_indices(bars: tuple[Candle, ...], k: int, t: int,
                   start: int, kind: str) -> tuple[int, ...]:
    """Return pivots known by t; a pivot is emitted only after its right bars."""
    values = tuple(bar.high if kind == "high" else bar.low for bar in bars)
    out = []
    for i in range(max(k, start), min(t - k + 1, len(bars) - k)):
        around = values[i - k:i + k + 1]
        if kind == "high" and values[i] == max(around):
            out.append(i)
        elif kind == "low" and values[i] == min(around):
            out.append(i)
    return tuple(out)


def _clusters(indices: tuple[int, ...], prices: tuple[Decimal, ...],
              tolerance: Decimal) -> tuple[tuple[Decimal, Decimal, tuple[int, ...]], ...]:
    ordered = sorted(indices, key=lambda index: prices[index])
    groups: list[list[int]] = []
    current: list[int] = []
    for index in ordered:
        if not current or prices[index] - prices[current[0]] <= tolerance:
            current.append(index)
        else:
            groups.append(current)
            current = [index]
    if current:
        groups.append(current)
    return tuple((min(prices[i] for i in group), max(prices[i] for i in group),
                  tuple(sorted(group))) for group in groups)


def _find_level(bars: tuple[Candle, ...], atr: tuple[Decimal, ...],
                t: int, side: int) -> tuple[Decimal, Decimal, str] | None:
    """Source S/R and flip-zone logic with the preregistered 2h parameters."""
    a = atr[t]
    if a <= 0:
        return None
    start = max(0, t - LEVEL_LOOKBACK)
    touch_from = t - RETEST_BARS
    if touch_from <= start:
        return None
    tolerance = ZONE_TOL_ATR * a
    highs = tuple(bar.high for bar in bars)
    lows = tuple(bar.low for bar in bars)
    closes = tuple(bar.close for bar in bars)
    best = None
    sources = (
        ("sr", _pivot_indices(bars, PIVOT_K, t, start, "low" if side > 0 else "high"),
         lows if side > 0 else highs),
        ("flip", _pivot_indices(bars, PIVOT_K, t, start, "high" if side > 0 else "low"),
         highs if side > 0 else lows),
    )
    for kind, pivot_indices, level_prices in sources:
        touches = tuple(index for index in pivot_indices if index < touch_from)
        if len(touches) < MIN_PRIOR_TOUCHES:
            continue
        for lo, hi, clustered in _clusters(touches, level_prices, tolerance):
            if len(clustered) < MIN_PRIOR_TOUCHES:
                continue
            # Keep the source's minimum zone thickness for visually thin pivot clusters.
            if hi - lo < D("0.3") * a:
                mid = (hi + lo) / 2
                lo, hi = mid - D("0.15") * a, mid + D("0.15") * a
            distinct = True
            for first, second in zip(clustered[:-1], clustered[1:]):
                segment_high = max(highs[first:second + 1])
                segment_low = min(lows[first:second + 1])
                away = ((segment_high - hi) if (kind == "sr") == (side > 0)
                        else (lo - segment_low))
                if away < LEAVE_ATR * a:
                    distinct = False
                    break
            if not distinct:
                continue
            first, last = clustered[0], clustered[-1]
            if kind == "sr":
                held = (min(closes[first:t + 1]) >= lo if side > 0
                        else max(closes[first:t + 1]) <= hi)
                if not held:
                    continue
            else:
                after = closes[last + 1:touch_from]
                breaks = tuple(i for i, close in enumerate(after)
                               if (close > hi if side > 0 else close < lo))
                if not breaks:
                    continue
                broken_at = last + 1 + breaks[0]
                held = (min(closes[broken_at:t + 1]) >= lo if side > 0
                        else max(closes[broken_at:t + 1]) <= hi)
                if not held:
                    continue
            window_lows = min(lows[touch_from:t + 1])
            window_highs = max(highs[touch_from:t + 1])
            if (side > 0 and window_lows > hi) or (side < 0 and window_highs < lo):
                continue
            close = closes[t]
            distance = close - hi if side > 0 else lo - close
            if distance < 0 or distance > NEAR_ATR * a:
                continue
            candidate = (lo, hi, kind)
            if best is None or kind == "flip":
                best = candidate
    return best


class EmaLevel2hStrategy:
    strategy_id = "ema_level_2h"
    version = "research-1.0.0"

    def evaluate(self, snapshot: Snapshot) -> Signal | None:
        if (snapshot.instrument.status != "TRADING" or snapshot.instrument.margin_asset != "USDT"
                or snapshot.instrument.symbol not in MAJORS):
            return None
        bars = _closed_bars(snapshot)
        if bars is None:
            return None
        closes = tuple(bar.close for bar in bars)
        fast, slow = _ema(closes, FAST_EMA), _ema(closes, SLOW_EMA)
        if fast[-2] <= slow[-2] and fast[-1] > slow[-1]:
            side = 1
        elif fast[-2] >= slow[-2] and fast[-1] < slow[-1]:
            side = -1
        else:
            return None
        atr = _atr(bars)
        t = len(bars) - 1
        level = _find_level(bars, atr, t, side)
        if level is None:
            return None
        lo, hi, kind = level
        stop = lo - STOP_BUFFER_ATR * atr[t] if side > 0 else hi + STOP_BUFFER_ATR * atr[t]
        entry = closes[t]
        risk = (entry - stop) * side
        if risk <= 0:
            return None
        target = entry + D(side) * TARGET_R * risk
        candle = bars[t]
        direction = "LONG" if side > 0 else "SHORT"
        return make_signal(
            snapshot, self.strategy_id, self.version,
            f"{candle.close_ms}:{direction}:{kind}", direction, "MARKET", entry,
            stop, target, MAX_HOLD_SECONDS, snapshot.at_ms + 90_000,
            f"EMA12/26 cross at a confirmed 2h {kind} zone on its third distinct touch",
            (("ema12", fast[-1]), ("ema26", slow[-1]), ("atr14_ema", atr[t]),
             ("zone_low", lo), ("zone_high", hi), ("zone_kind", D(1) if kind == "sr" else D(2)),
             ("prior_distinct_touches", D(MIN_PRIOR_TOUCHES)),
             ("touch_tolerance_atr", ZONE_TOL_ATR),
             ("stop_buffer_atr", STOP_BUFFER_ATR), ("target_r", TARGET_R)),
        )

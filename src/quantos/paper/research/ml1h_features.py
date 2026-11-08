"""Pure causal extraction of the historical patml_v1 hourly candle features.

The source reference is kept under ``research/reference/patml_v1`` and must
never be imported or executed. This module is a standard-library-only port of
its feature definitions; it does not construct labels, train models, or access
market data.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import numbers
from typing import Sequence

SOURCE_RUN_SHA256 = "5a6470bfb28848ea31e21e7a821a7b69db410f63acf332262b2e7245253f6577"
WARMUP_BARS = 200
ATR_PERIOD = 14
ZSCORE_PERIOD = 48

FEATURE_NAMES = tuple(
    [f"{name}{lag}" for lag in range(3)
     for name in ("b", "uw", "lw", "rg", "cl")]
    + ["engulf", "c_vs_h1", "c_vs_l1", "ups5", "r3", "r12", "r48",
       "d_vwap", "d_dopen", "d_ema20", "d_ema50", "d_ema200", "vz", "rz",
       "hs", "hc", "dow"]
)


@dataclass(frozen=True)
class FeatureBatch:
    """Feature rows aligned one-to-one with input bars; undefined values are NaN."""

    names: tuple[str, ...]
    rows: tuple[tuple[float, ...], ...]
    atr: tuple[float, ...]
    warmup_bars: int = WARMUP_BARS
    source_sha256: str = SOURCE_RUN_SHA256


def _rolling_z(values: Sequence[float], end: int, period: int) -> float:
    if end + 1 < period:
        return math.nan
    window = values[end + 1 - period:end + 1]
    mean = sum(window) / period
    # pandas rolling.std() uses the sample standard deviation (ddof=1).
    variance = (sum(value * value for value in window) - period * mean * mean) / (period - 1)
    if variance <= 0:
        return math.nan
    return (values[end] - mean) / math.sqrt(variance)


def extract_features(timestamp_ms: Sequence[int], opens: Sequence[float],
                     highs: Sequence[float], lows: Sequence[float],
                     closes: Sequence[float], volumes: Sequence[float]) -> FeatureBatch:
    """Return the reference's 32 features using data through each bar only.

    The first 200 rows are deliberately all NaN, matching the reference's
    blanket warm-up. ATR is a 14-bar simple mean of high-low. The final bar is
    allowed even if its close is after a requested study cutoff; callers must
    select completed bars before calling this function.
    """
    columns = (timestamp_ms, opens, highs, lows, closes, volumes)
    n = len(timestamp_ms)
    if any(len(column) != n for column in columns):
        raise ValueError("hourly timestamp/OHLCV columns must have equal lengths")
    if any(isinstance(t, bool) or not isinstance(t, numbers.Integral) or t < 0 for t in timestamp_ms):
        raise ValueError("timestamps must be nonnegative integer milliseconds")
    if any(right <= left for left, right in zip(timestamp_ms, timestamp_ms[1:])):
        raise ValueError("timestamps must be strictly increasing")

    o, h, l, c, v = [], [], [], [], []
    for i in range(n):
        try:
            oi, hi, li, ci, vi = map(float, (opens[i], highs[i], lows[i], closes[i], volumes[i]))
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"invalid OHLCV value at row {i}") from None
        if (not all(math.isfinite(x) for x in (oi, hi, li, ci, vi)) or
                min(oi, hi, li, ci) <= 0 or vi < 0 or hi < max(oi, ci, li) or li > min(oi, ci, hi)):
            raise ValueError(f"invalid OHLCV value at row {i}")
        o.append(oi); h.append(hi); l.append(li); c.append(ci); v.append(vi)

    ranges = [high - low for high, low in zip(h, l)]
    atr: list[float] = []
    for i in range(n):
        atr.append(sum(ranges[max(0, i - ATR_PERIOD + 1):i + 1]) / ATR_PERIOD
                    if i >= ATR_PERIOD - 1 else math.nan)

    log_close = [math.log(value) for value in c]
    log_volume = [math.log(value + 1.0) for value in v]
    ema_values: dict[int, list[float]] = {}
    for period in (20, 50, 200):
        alpha = 2.0 / (period + 1.0)
        values = []
        for i, close in enumerate(c):
            values.append(close if i == 0 else alpha * close + (1.0 - alpha) * values[-1])
        ema_values[period] = values

    rows: list[tuple[float, ...]] = []
    daily_open: dict[int, float] = {}
    daily_volume: dict[int, float] = {}
    daily_pv: dict[int, float] = {}
    up_flags: deque[int] = deque(maxlen=5)
    for i, ts in enumerate(timestamp_ms):
        day = ts // 86_400_000
        daily_open.setdefault(day, o[i])
        typical = (h[i] + l[i] + c[i]) / 3.0
        daily_volume[day] = daily_volume.get(day, 0.0) + v[i]
        daily_pv[day] = daily_pv.get(day, 0.0) + typical * v[i]
        up_flags.append(int(c[i] > o[i]))

        features: list[float] = []
        a = max(atr[i], 1e-12) if math.isfinite(atr[i]) else math.nan
        for lag in range(3):
            j = i - lag
            if j < 0 or not math.isfinite(a):
                features.extend((math.nan,) * 5)
                continue
            bar_range = max(h[j] - l[j], 1e-12)
            features.extend(((c[j] - o[j]) / a,
                             (h[j] - max(o[j], c[j])) / a,
                             (min(o[j], c[j]) - l[j]) / a,
                             (h[j] - l[j]) / a,
                             (c[j] - l[j]) / bar_range))

        b0, b1 = features[0], features[5]
        sign0 = (b0 > 0) - (b0 < 0) if math.isfinite(b0) else 0
        sign1 = (b1 > 0) - (b1 < 0) if math.isfinite(b1) else 0
        engulf = float(sign0 if sign0 != sign1 and abs(b0) > abs(b1) else 0)
        features.extend((engulf,
                         (c[i] - h[i - 1]) / a if i and math.isfinite(a) else math.nan,
                         (c[i] - l[i - 1]) / a if i and math.isfinite(a) else math.nan,
                         float(sum(up_flags)) if len(up_flags) == 5 else math.nan))

        for width in (3, 12, 48):
            features.append(log_close[i] - log_close[i - width] if i >= width else math.nan)
        vwap = daily_pv[day] / max(daily_volume[day], 1e-12)
        features.extend(((c[i] - vwap) / a if math.isfinite(a) else math.nan,
                         (c[i] - daily_open[day]) / a if math.isfinite(a) else math.nan))
        for period in (20, 50, 200):
            features.append((c[i] - ema_values[period][i]) / a if math.isfinite(a) else math.nan)
        features.extend((_rolling_z(log_volume, i, ZSCORE_PERIOD),
                         _rolling_z(ranges, i, ZSCORE_PERIOD)))
        hour = (ts // 3_600_000) % 24
        angle = 2.0 * math.pi * hour / 24.0
        features.extend((math.sin(angle), math.cos(angle), float((day + 3) % 7)))

        if len(features) != len(FEATURE_NAMES):
            raise AssertionError("feature-name/value layout mismatch")
        if i < WARMUP_BARS:
            features = [math.nan] * len(FEATURE_NAMES)
        rows.append(tuple(features))

    return FeatureBatch(FEATURE_NAMES, tuple(rows), tuple(atr))

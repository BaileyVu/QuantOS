"""Causal, timestamp-aligned primitives for candidate strategy research.

These helpers intentionally do not define entry rules, stop placement, or zone
semantics. Inputs are immutable closed candles or timestamped observations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from ..types import Candle

D = Decimal
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
ALT_SOURCE_UNIVERSE = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "LTCUSDT", "BCHUSDT",
    "TRXUSDT", "ATOMUSDT", "NEARUSDT", "UNIUSDT", "FILUSDT", "ETCUSDT",
    "AAVEUSDT", "XLMUSDT", "ALGOUSDT", "SANDUSDT", "MANAUSDT", "AXSUSDT",
    "CRVUSDT", "RUNEUSDT", "THETAUSDT", "VETUSDT", "GALAUSDT",
)


@dataclass(frozen=True)
class TimedValue:
    time_ms: int
    value: Decimal


@dataclass(frozen=True)
class SwingPivot:
    """A pivot becomes available only at `confirmed_ms`, after right bars."""

    pivot_ms: int
    confirmed_ms: int
    kind: str  # "high" or "low"
    price: Decimal


@dataclass(frozen=True)
class QuarterlyReturnCutoff:
    """Threshold and explicit provenance for a frozen quarter calibration."""

    threshold: Decimal
    quantile: Decimal
    quarter_start_ms: int
    calibration_start_ms: int
    calibration_end_ms: int  # exclusive; last three days are embargoed
    symbols: tuple[str, ...]
    sample_count: int


def _valid_hourly(bars: tuple[Candle, ...]) -> bool:
    previous = None
    for bar in bars:
        if (bar.open_ms % HOUR_MS != 0 or bar.close_ms != bar.open_ms + HOUR_MS - 1
                or any(not v.is_finite() for v in (bar.open, bar.high, bar.low, bar.close, bar.volume))
                or min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0
                or bar.high < max(bar.open, bar.close, bar.low)
                or bar.low > min(bar.open, bar.close, bar.high)):
            return False
        if previous is not None and bar.open_ms != previous + HOUR_MS:
            return False
        previous = bar.open_ms
    return bool(bars)


def closed_hourly_bars(bars: tuple[Candle, ...], as_of_ms: int,
                       minimum: int) -> tuple[Candle, ...] | None:
    """Validate and return contiguous closed 1h bars available by an exclusive as-of."""
    if (isinstance(as_of_ms, bool) or not isinstance(as_of_ms, int)
            or minimum <= 0):
        return None
    eligible = tuple(bar for bar in bars if bar.close_ms + 1 <= as_of_ms)
    if len(eligible) < minimum or not _valid_hourly(eligible):
        return None
    expected_close = (as_of_ms // HOUR_MS) * HOUR_MS - 1
    return eligible if eligible[-1].close_ms == expected_close else None


def resample_hourly(bars: tuple[Candle, ...], hours: int, *,
                    as_of_ms: int) -> tuple[Candle, ...] | None:
    """Aggregate complete UTC epoch-aligned 2h or 4h bars from hourly bars.

    A partial first/last group is omitted. Any hourly gap, overlap, malformed
    candle, or unsupported target timeframe fails closed.
    """
    if isinstance(as_of_ms, bool) or not isinstance(as_of_ms, int) or hours not in (2, 4):
        return None
    # A Candle may be present in the input before its close has actually been
    # observed. Exclude it before continuity checks and grouping.
    bars = tuple(bar for bar in bars if bar.close_ms <= as_of_ms)
    if not _valid_hourly(bars):
        return None
    period = hours * HOUR_MS
    output: list[Candle] = []
    by_bucket: dict[int, list[Candle]] = {}
    for bar in bars:
        bucket = (bar.open_ms // period) * period
        by_bucket.setdefault(bucket, []).append(bar)
    for bucket in sorted(by_bucket):
        group = by_bucket[bucket]
        if (len(group) != hours or group[0].open_ms != bucket
                or group[-1].close_ms != bucket + period - 1):
            continue
        output.append(Candle(
            bucket, bucket + period - 1, group[0].open,
            max(c.high for c in group), min(c.low for c in group),
            group[-1].close, sum((c.volume for c in group), D(0)),
        ))
    return tuple(output) or None


def rolling_24h_return(bars: tuple[Candle, ...]) -> tuple[TimedValue, ...] | None:
    """Return fractional 24h close-to-close returns at close-exclusive times."""
    if not _valid_hourly(bars) or len(bars) <= 24:
        return None
    return tuple(TimedValue(bar.close_ms + 1, bar.close / bars[i - 24].close - 1)
                 for i, bar in enumerate(bars) if i >= 24)


def calibrate_quarterly_return_cutoff(
    returns_by_symbol: Mapping[str, tuple[TimedValue, ...]], quarter_start_ms: int,
    quantile: Decimal, symbols: tuple[str, ...] = ALT_SOURCE_UNIVERSE,
) -> QuarterlyReturnCutoff | None:
    """Pool prior closed hourly 24h returns over `[S-90d, S-3d)`.

    `TimedValue.time_ms` is the exclusive UTC boundary after a closed 1h bar.
    Every requested symbol must have all 2088 hourly values for the 87-day
    interval; missing data fails closed. Only the return series is accepted,
    so forward returns cannot enter this calibration path.
    """
    if (isinstance(quarter_start_ms, bool) or not isinstance(quarter_start_ms, int)
            or quarter_start_ms % DAY_MS != 0 or not quantile.is_finite()
            or quantile not in (D("0.005"), D("0.01")) or not symbols
            or len(set(symbols)) != len(symbols) or set(returns_by_symbol) != set(symbols)):
        return None
    quarter = datetime.fromtimestamp(quarter_start_ms / 1000, timezone.utc)
    if quarter.day != 1 or quarter.month not in (1, 4, 7, 10):
        return None
    start = quarter_start_ms - 90 * DAY_MS
    end = quarter_start_ms - 3 * DAY_MS
    expected_times = tuple(range(start, end, HOUR_MS))
    if len(expected_times) != 2088:
        return None
    pooled: list[Decimal] = []
    for symbol in symbols:
        rows = returns_by_symbol[symbol]
        previous = None
        for row in rows:
            if (row.time_ms % HOUR_MS != 0 or not row.value.is_finite()
                    or previous is not None and row.time_ms <= previous):
                return None
            previous = row.time_ms
        by_time = {row.time_ms: row.value for row in rows
                   if start <= row.time_ms < end}
        if tuple(sorted(by_time)) != expected_times:
            return None
        pooled.extend(by_time[t] for t in expected_times)
    ordered = sorted(pooled)
    rank = quantile * D(len(ordered) - 1)
    lower, upper = int(rank), min(int(rank) + 1, len(ordered) - 1)
    fraction = rank - D(lower)
    threshold = ordered[lower] * (1 - fraction) + ordered[upper] * fraction
    return QuarterlyReturnCutoff(threshold, quantile, quarter_start_ms, start, end,
                                 tuple(symbols), len(ordered))


def prior_rolling_quantile(values: tuple[Decimal, ...], window: int,
                           quantile: Decimal) -> tuple[Decimal | None, ...] | None:
    """Per-index quantile of the prior `window` values, excluding current.

    Uses linear interpolation between neighboring sorted observations. The
    first `window` results are None because there is no complete prior window.
    """
    if (window <= 0 or not quantile.is_finite() or quantile < 0 or quantile > 1
            or any(not v.is_finite() for v in values)):
        return None
    out: list[Decimal | None] = []
    for i in range(len(values)):
        if i < window:
            out.append(None)
            continue
        ordered = sorted(values[i - window:i])
        rank = quantile * D(window - 1)
        lower = int(rank)
        upper = min(lower + 1, window - 1)
        fraction = rank - D(lower)
        out.append(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)
    return tuple(out)


def oi_value_4h_change_zscore(observations: tuple[TimedValue, ...],
                              window_hours: int = 720) -> tuple[TimedValue, ...] | None:
    """Fractional 4h OI-value change and prior-only rolling z-score.

    Observations must be positive, UTC-hour-aligned, and exactly one hour
    apart. The z-score at time t uses the preceding `window_hours` hourly
    changes only, never the change at t. Results are timestamped at t.
    Population standard deviation is used; zero variance yields no score.
    """
    if window_hours <= 0 or len(observations) < 5:
        return None
    previous = None
    changes: list[TimedValue] = []
    for item in observations:
        if (item.time_ms % HOUR_MS != 0 or not item.value.is_finite() or item.value <= 0
                or (previous is not None and item.time_ms != previous + HOUR_MS)):
            return None
        previous = item.time_ms
    for i in range(4, len(observations)):
        now, prior = observations[i], observations[i - 4]
        changes.append(TimedValue(now.time_ms, now.value / prior.value - 1))
    out: list[TimedValue] = []
    for i, change in enumerate(changes):
        if i < window_hours:
            continue
        prior = [x.value for x in changes[i - window_hours:i]]
        mean = sum(prior, D(0)) / D(window_hours)
        variance = sum(((x - mean) ** 2 for x in prior), D(0)) / D(window_hours)
        if variance == 0:
            continue
        out.append(TimedValue(change.time_ms, (change.value - mean) / variance.sqrt()))
    return tuple(out) or None


def confirmed_swing_pivots(bars: tuple[Candle, ...], left: int = 2,
                           right: int = 2) -> tuple[SwingPivot, ...] | None:
    """Find strict local high/low pivots, emitted only after `right` bars close.

    Equal highs/lows do not create pivots. The record carries both pivot and
    confirmation times so consumers cannot mistake the pivot time for when it
    became knowable.
    """
    if left <= 0 or right <= 0 or not bars:
        return None
    previous = None
    for bar in bars:
        if (bar.close_ms < bar.open_ms or bar.high < max(bar.open, bar.close, bar.low)
                or bar.low > min(bar.open, bar.close, bar.high)
                or any(not v.is_finite() for v in (bar.open, bar.high, bar.low, bar.close))):
            return None
        if previous is not None and bar.open_ms != previous + 1:
            return None
        previous = bar.close_ms
    out = []
    for i in range(left, len(bars) - right):
        left_bars, right_bars = bars[i - left:i], bars[i + 1:i + right + 1]
        pivot = bars[i]
        if all(pivot.high > x.high for x in (*left_bars, *right_bars)):
            out.append(SwingPivot(pivot.close_ms, right_bars[-1].close_ms, "high", pivot.high))
        if all(pivot.low < x.low for x in (*left_bars, *right_bars)):
            out.append(SwingPivot(pivot.close_ms, right_bars[-1].close_ms, "low", pivot.low))
    return tuple(out)

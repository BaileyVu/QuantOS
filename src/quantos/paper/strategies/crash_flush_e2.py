"""Research-only crash-flush long candidate for nine USD-M majors.

The module expresses a causal entry rule only. Portfolio slot limits, one-entry
per-symbol cooldowns, time exits, and risk sizing remain engine responsibilities.
"""
from __future__ import annotations

from decimal import Decimal

from ..types import OpenInterestValue, Signal, Snapshot
from .candidate_features import (HOUR_MS, TimedValue, closed_hourly_bars, oi_value_4h_change_zscore,
                                 oi_value_4h_change_zscore_tolerant)
from .indicators import make_signal

D = Decimal
MAJORS = frozenset({
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT",
})
STOP_FRACTION = D("0.25")
SENSITIVITY_STOP_FRACTION = D("0.15")
OI_Z_THRESHOLD = D("-2")
PRICE_DRAWDOWN = D("0.08")


def crash_price_gate(bars):
    """Return the crash-price measurements, or None when price is ineligible.

    `bars` must already be the validated, causal 24-bar hourly window.
    Kept pure so price-only controls use the exact candidate predicate.
    """
    if len(bars) < 24:
        return None
    current = bars[-1]
    change = current.close / bars[-5].close - 1
    high = max(bar.high for bar in bars[-24:])
    if change >= 0 or current.close > high * (1 - PRICE_DRAWDOWN):
        return None
    return current, change, high


def _oi_observations(values: tuple[OpenInterestValue, ...], cutoff_ms: int
                     ) -> tuple[TimedValue, ...] | None:
    """Keep only measurements available at the decision time; validate ordering."""
    eligible = tuple(value for value in values
                     if value.time_ms <= cutoff_ms and value.available_ms <= cutoff_ms)
    if len(eligible) < 5:
        return None
    previous = None
    out = []
    for value in eligible:
        if (value.available_ms < value.time_ms or value.time_ms % HOUR_MS != 0
                or previous is not None and value.time_ms != previous + HOUR_MS):
            return None
        previous = value.time_ms
        out.append(TimedValue(value.time_ms, value.value))
    return tuple(out)


def _oi_tolerant(values: tuple[OpenInterestValue, ...], cutoff_ms: int) -> tuple[TimedValue, ...] | None:
    """Available, ordered observations; bad rows are kept and judged by the tolerant score."""
    eligible = sorted((v for v in values if v.time_ms <= cutoff_ms and v.available_ms <= cutoff_ms
                       and v.available_ms >= v.time_ms), key=lambda v: v.time_ms)
    out = tuple(TimedValue(v.time_ms, v.value) for v in eligible)
    if len(out) < 5 or len({v.time_ms for v in out}) != len(out):
        return None
    return out


class CrashFlushE2Strategy:
    strategy_id = "crash_flush_e2"
    version = "research-1.0.0"

    def __init__(self, holding_hours: int = 24,
                 stop_fraction: Decimal = STOP_FRACTION, oi_mode: str = "strict"):
        if holding_hours not in (24, 48, 72):
            raise ValueError("crash-flush hold must be 24, 48, or 72 hours")
        if stop_fraction not in (STOP_FRACTION, SENSITIVITY_STOP_FRACTION):
            raise ValueError("crash-flush stop must use the primary 25% or sensitivity 15% setting")
        if oi_mode not in ("strict", "tolerant"):
            raise ValueError("crash-flush OI mode must be strict or tolerant")
        self.holding_hours = holding_hours
        self.stop_fraction = stop_fraction
        self.oi_mode = oi_mode

    def evaluate(self, snapshot: Snapshot) -> Signal | None:
        if (snapshot.instrument.symbol not in MAJORS
                or snapshot.instrument.margin_asset != "USDT"
                or snapshot.instrument.status != "TRADING"
                or snapshot.quote.symbol != snapshot.instrument.symbol):
            return None
        bars = closed_hourly_bars(snapshot.candles_1h, snapshot.at_ms, 24)
        if bars is None:
            return None
        # Exactly four hourly close-to-close intervals and a trailing 24-bar high.
        price_gate = crash_price_gate(bars)
        if price_gate is None:
            return None
        current, price_change_4h, high_24h = price_gate

        # Metrics archive rows are conservatively available one hour after
        # their raw label. Use the latest exact hourly observation available at
        # this decision boundary; retain its original label and expose the lag.
        tolerant = self.oi_mode == "tolerant"
        oi = (_oi_tolerant if tolerant else _oi_observations)(snapshot.oi_values, snapshot.at_ms)
        current_oi_label = current.open_ms + HOUR_MS
        if (oi is None or oi[-1].time_ms not in
                (current_oi_label - HOUR_MS, current_oi_label)):
            return None
        if tolerant:
            latest = oi_value_4h_change_zscore_tolerant(oi, window_hours=720)
            scores = (latest,) if latest is not None else None
        else:
            scores = oi_value_4h_change_zscore(oi, window_hours=720)
        if not scores or scores[-1].time_ms != oi[-1].time_ms or scores[-1].value >= OI_Z_THRESHOLD:
            return None
        oi_lag_hours = D(current_oi_label - oi[-1].time_ms) / D(HOUR_MS)

        entry = current.close
        return make_signal(
            snapshot, self.strategy_id, self.version,
            f"{current.close_ms}:{oi[-1].time_ms}:h{self.holding_hours}:s{self.stop_fraction}",
            "LONG", "MARKET", entry,
            entry * (1 - self.stop_fraction), None, self.holding_hours * 3600,
            snapshot.at_ms + 90_000,
            "4h OI-value change z-score below -2 with a 4h price decline and 8% drawdown from the trailing 24h high",
            (("price_change_4h", price_change_4h),
             ("drawdown_from_24h_high", current.close / high_24h - 1),
             ("oi_value_change_4h_z", scores[-1].value),
             ("oi_lag_hours", oi_lag_hours),
             ("catastrophe_stop_fraction", self.stop_fraction),
             ("holding_hours", D(self.holding_hours))),
        )

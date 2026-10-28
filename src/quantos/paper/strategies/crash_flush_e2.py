"""Research-only crash-flush long candidate for nine USD-M majors.

The module expresses a causal entry rule only. Portfolio slot limits, one-entry
per-symbol cooldowns, time exits, and risk sizing remain engine responsibilities.
"""
from __future__ import annotations

from decimal import Decimal

from ..types import OpenInterestValue, Signal, Snapshot
from .candidate_features import HOUR_MS, TimedValue, oi_value_4h_change_zscore
from .indicators import make_signal, validated_bars

D = Decimal
MAJORS = frozenset({
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT",
})
HOLD_SECONDS = 24 * 60 * 60
STOP_FRACTION = D("0.25")
OI_Z_THRESHOLD = D("-2")
PRICE_DRAWDOWN = D("0.08")


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


class CrashFlushE2Strategy:
    strategy_id = "crash_flush_e2"
    version = "1.0.0"

    def evaluate(self, snapshot: Snapshot) -> Signal | None:
        if (snapshot.instrument.symbol not in MAJORS
                or snapshot.instrument.margin_asset != "USDT"
                or snapshot.instrument.status != "TRADING"
                or snapshot.quote.symbol != snapshot.instrument.symbol):
            return None
        bars = validated_bars(snapshot, "1h", HOUR_MS, 24)
        if bars is None:
            return None
        current = bars[-1]
        # Exactly four hourly close-to-close intervals and a trailing 24-bar high.
        price_change_4h = current.close / bars[-5].close - 1
        high_24h = max(bar.high for bar in bars[-24:])
        if price_change_4h >= 0 or current.close > high_24h * (1 - PRICE_DRAWDOWN):
            return None

        oi = _oi_observations(snapshot.oi_values, current.close_ms)
        if oi is None or oi[-1].time_ms != current.open_ms:
            return None
        scores = oi_value_4h_change_zscore(oi, window_hours=720)
        if not scores or scores[-1].time_ms != current.open_ms or scores[-1].value >= OI_Z_THRESHOLD:
            return None

        entry = current.close
        return make_signal(
            snapshot, self.strategy_id, self.version,
            f"{current.close_ms}:{oi[-1].time_ms}", "LONG", "MARKET", entry,
            entry * (1 - STOP_FRACTION), None, HOLD_SECONDS,
            current.close_ms + 90_000,
            "4h OI-value change z-score below -2 with a 4h price decline and 8% drawdown from the trailing 24h high",
            (("price_change_4h", price_change_4h),
             ("drawdown_from_24h_high", current.close / high_24h - 1),
             ("oi_value_change_4h_z", scores[-1].value),
             ("catastrophe_stop_fraction", STOP_FRACTION)),
        )

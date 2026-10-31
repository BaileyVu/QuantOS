"""Slow-moving inputs for the promoted event rules: USDT-twin OI and quarterly cutoffs.

Both are fetched from public endpoints, cached on disk, and attached to every
snapshot. Missing inputs leave the field empty, so the rule fails closed and
the self-check reports it loudly.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from .strategies.candidate_features import (
    ALT_SOURCE_UNIVERSE, DAY_MS, HOUR_MS, calibrate_quarterly_return_cutoff, rolling_24h_return)
from .types import D, OpenInterestValue

# Research semantics: an hourly OI label is treated as available one hour later.
OI_AVAILABILITY_MS = HOUR_MS
CALIBRATION_MIN_SYMBOLS = 25


def quarter_start_ms(now_ms: int) -> int:
    at = datetime.fromtimestamp(now_ms / 1000, timezone.utc)
    start = datetime(at.year, (at.month - 1) // 3 * 3 + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp() * 1000)


class OpenInterestCache:
    def __init__(self, market, symbols):
        self.market, self.symbols = market, tuple(symbols)  # USDT twins
        self.series: dict[str, dict[int, object]] = {}
        self.next_ms: dict[str, int] = {}

    def refresh(self, now_ms: int):
        for symbol in self.symbols:
            if now_ms < self.next_ms.get(symbol, 0):
                continue
            try:
                known = self.series.setdefault(symbol, {})
                rows = self.market.open_interest_value(symbol)
                if not known and rows:  # backfill the 30 days the API serves
                    rows = self.market.open_interest_value(symbol, end_ms=rows[0][0] - 1) + rows
                for label, value in rows:
                    known[label] = value
                cutoff = now_ms - 32 * DAY_MS
                for label in [k for k in known if k < cutoff]:
                    del known[label]
                self.next_ms[symbol] = now_ms // HOUR_MS * HOUR_MS + HOUR_MS + 300_000
            except Exception:
                self.next_ms[symbol] = now_ms + 300_000

    def values(self, twin: str) -> tuple[OpenInterestValue, ...]:
        rows = self.series.get(twin, {})
        return tuple(OpenInterestValue(t, t + OI_AVAILABILITY_MS, rows[t]) for t in sorted(rows))


def _hourly(market, symbol, start_ms, end_ms):
    bars, end = [], end_ms
    while end > start_ms:
        limit = min(1000, (end - start_ms) // HOUR_MS)
        chunk = market.candles(symbol, "1h", end, limit, historical=True)
        bars = list(chunk) + bars
        end = chunk[0].open_ms
    return tuple(bars)


def quarterly_cutoff(market, state: Path, now_ms: int, quantile=D("0.01")):
    """Frozen per-quarter cutoff over [S-90d, S-3d), cached; partial universe allowed (>=25 symbols)."""
    start = quarter_start_ms(now_ms)
    path = state / "calibration" / f"alt_dip_{start}_{quantile}.json"
    if path.exists():
        data = json.loads(path.read_text())
        return D(data["threshold"]), data["calibration_end_ms"], data
    returns, missing = {}, []
    for symbol in ALT_SOURCE_UNIVERSE:
        try:
            bars = _hourly(market, symbol, start - 90 * DAY_MS - 25 * HOUR_MS, start - 3 * DAY_MS)
            series = rolling_24h_return(bars)
            if series is None: raise ValueError("invalid bars")
            returns[symbol] = series
        except Exception:
            missing.append(symbol)
    if len(returns) < CALIBRATION_MIN_SYMBOLS:
        return None, None, {"missing": missing}
    result = calibrate_quarterly_return_cutoff(returns, start, quantile, tuple(sorted(returns)))
    if result is None:
        return None, None, {"missing": missing, "error": "calibration rejected"}
    data = {"threshold": str(result.threshold), "quantile": str(quantile), "quarter_start_ms": start,
            "calibration_end_ms": result.calibration_end_ms, "symbols": list(result.symbols),
            "missing": missing, "sample_count": result.sample_count}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True))
    return result.threshold, result.calibration_end_ms, data

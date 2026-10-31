"""Can every enabled strategy compute its inputs right now? Run at start and daily."""
from __future__ import annotations

from .strategies.candidate_features import TimedValue, closed_hourly_bars, oi_value_4h_change_zscore_tolerant
from .strategies.crash_flush_e2 import MAJORS
from .strategies.usdc_mapped import usdt_twin

# Minimum closed bars per frame that the trend rules need.
NEEDS = {"tsmom_4h": ("candles_4h", 185), "turtle_55": ("candles_4h", 60),
         "vol_squeeze_breakout": ("candles_1h", 60)}


def _oi_ok(snapshot) -> bool:
    obs = tuple(TimedValue(v.time_ms, v.value) for v in snapshot.oi_values if v.available_ms <= snapshot.at_ms)
    return bool(obs) and oi_value_4h_change_zscore_tolerant(obs) is not None


def self_check(strategies, snapshots) -> dict:
    """{strategy_id: {"ok": bool, "level": "ok"|"warn"|"critical", "detail": str}}."""
    out = {}
    for strategy in strategies:
        sid = strategy.strategy_id
        if sid == "crash_flush_e2":
            majors = [s for s in snapshots if usdt_twin(s.instrument.symbol) in MAJORS]
            bad = sorted(s.instrument.symbol for s in majors
                         if not _oi_ok(s) or closed_hourly_bars(s.candles_1h, s.at_ms, 24) is None)
            level = "critical" if not majors or len(bad) == len(majors) else "warn" if bad else "ok"
            detail = f"OI z / 1h bars unavailable on {len(bad)}/{len(majors)} majors" + (f": {','.join(bad)}" if bad else "")
        elif sid == "alt_dip_reversal":
            alts = [s for s in snapshots if s.quarterly_return_cutoff is not None]
            level = "ok" if alts else "critical"
            detail = f"quarterly cutoff attached to {len(alts)} symbols"
        elif sid in NEEDS:
            frame, need = NEEDS[sid]
            have = [len(getattr(s, frame)) for s in snapshots if s.instrument.symbol in ("BTCUSDC", "ETHUSDC")]
            level = "ok" if have and min(have) >= need else "critical"
            detail = f"{frame} bars {have} need {need}"
        else:
            level, detail = "ok", "no feature precheck"
        out[sid] = {"ok": level == "ok", "level": level, "detail": detail}
    return out

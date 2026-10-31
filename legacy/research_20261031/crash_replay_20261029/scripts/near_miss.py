"""Crash timeline and near-miss diagnostics using the strategies' own feature functions."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_crash as R  # noqa: E402  (loads data only; main() is not run)
from quantos.paper.strategies.candidate_features import TimedValue, oi_value_4h_change_zscore  # noqa: E402

D = R.D
f = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%m-%d %H:%M")  # noqa: E731
out = {}

# Hourly timeline: decisions at each hour boundary inside the window.
hours = list(range(R.T0, R.T1 + 1, R.HOUR_MS))
timeline = {}
for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT"):
    timeline[s] = [(f(b.open_ms), str(b.open), str(b.high), str(b.low), str(b.close))
                   for b in R.K[s, "1h"].bars if R.T0 <= b.open_ms < R.T1]
out["timeline"] = timeline

# Biggest window drawdowns from the pre-crash high, all fetched symbols.
dd = []
for s in sorted(set(R.USDC) | set(R.USDT)):
    bars = [b for b in R.K[s, "1m"].bars if R.T0 <= b.open_ms < R.T1]
    if not bars:
        continue
    pre = max(b.high for b in bars if b.open_ms < R.ms("2026-10-29 20:20"))
    low = min(bars, key=lambda b: b.low)
    dd.append((s, float(low.low / pre - 1), f(low.open_ms), str(pre), str(low.low)))
out["drawdowns"] = sorted(dd, key=lambda x: x[1])

# crash_flush near misses: at each window hour, the exact gate inputs.
crash = {}
for s in R.MAJORS:
    oi = R.OI[s]
    raw_ok = oi_value_4h_change_zscore(tuple(TimedValue(v.time_ms, v.value) for v in oi), 720) is not None
    oi = R.repaired_oi(s)
    series = oi_value_4h_change_zscore(tuple(TimedValue(v.time_ms, v.value) for v in oi), 720) or ()
    z = {v.time_ms: v.value for v in series}
    rows = []
    for at in hours[1:]:
        bars = R.K[s, "1h"].closed(at, 24)
        cur = bars[-1]
        high = max(b.high for b in bars)
        ch4 = cur.close / bars[-5].close - 1
        label = at - R.HOUR_MS        # latest OI label available at `at` (available = label + 1h)
        rows.append({"t": f(at), "close": str(cur.close), "dd24": float(cur.close / high - 1),
                     "ch4": float(ch4), "oi_z": float(z[label]) if label in z else None})
    labels = sorted(z)
    gaps = [f(a) for a, b in zip(labels, labels[1:]) if b - a != R.HOUR_MS]
    crash[s] = {"rows": rows, "min_dd24": min(r["dd24"] for r in rows),
                "min_oi_z": min(r["oi_z"] for r in rows if r["oi_z"] is not None),
                "oi_obs": len(oi), "raw_series_valid": raw_ok, "repaired_hours": R.REPAIRED.get(s, []), "oi_first": f(oi[0].time_ms), "z_gaps": gaps,
                "both_gates_hours": [r["t"] for r in rows if r["dd24"] <= -0.08 and r["ch4"] < 0
                                     and r["oi_z"] is not None and r["oi_z"] < -2]}
out["crash_flush"] = crash

# alt_dip near misses: min closed 24h return of each coin vs the frozen cutoff.
alt = []
for s in R.USDT:
    best = None
    for at in hours[1:]:
        bars = R.K[s, "1h"].closed(at, 25)
        r24 = bars[-1].close / bars[-25].close - 1
        if best is None or r24 < best[0]:
            best = (r24, f(at))
    alt.append((s, float(best[0]), best[1]))
out["alt_dip"] = {"cutoff": float(R.CAL.threshold), "calibration": [f(R.CAL.calibration_start_ms), f(R.CAL.calibration_end_ms)],
                  "samples": R.CAL.sample_count, "coins": sorted(alt, key=lambda x: x[1])}

# USDC universe size over time (>=20M rolling 24h quote volume).
out["usdc_universe"] = {f(at): sorted(s for s in R.USDC if R.eligible_usdc(s, at)) for at in hours[::4]}

# Mark vs last divergence on USDC baselines during the crash (feed-validity gate is 2%).
div = []
for s in R.USDC:
    for b in R.K[s, "1m"].bars:
        m = R.K[s, "mark"].by_open.get(b.open_ms)
        if m and R.T0 <= b.open_ms < R.T1:
            div.append((float(abs(m.low / b.low - 1)), float(abs(m.high / b.high - 1)), s, f(b.open_ms)))
out["max_mark_last_divergence"] = sorted(div, key=lambda x: -max(x[0], x[1]))[:10]
(R.ROOT / "out" / "near_miss.json").write_text(json.dumps(out, indent=1))
print(json.dumps({k: out[k] for k in ("drawdowns",)}, indent=0)[:2500])
for s, c in crash.items():
    print(s, "min_dd24 %.3f" % c["min_dd24"], "min_oi_z %.2f" % c["min_oi_z"], c["both_gates_hours"], c["oi_first"], c["z_gaps"][:3])
print("alt cutoff", out["alt_dip"]["cutoff"], out["alt_dip"]["coins"][:6])
print("divergence", out["max_mark_last_divergence"][:5])

"""Trade simulation of 4 rules + baselines on episodes. Writes trades.csv, results.json"""
import csv, json, datetime as dtm
from decimal import Decimal as Dc
from common import *
from quantos.paper.types import Candle
from quantos.paper.strategies.candidate_features import (ALT_SOURCE_UNIVERSE, calibrate_quarterly_return_cutoff,
    rolling_24h_return, oi_value_4h_change_zscore_tolerant, TimedValue)
from quantos.paper.strategies.crash_flush_e2 import MAJORS, crash_price_gate
FEE, SLIP, STOP, HOLD, WIN = 0.0005, 0.0015, 0.25, 24, 48
EP = list(csv.DictReader(open(HERE + "/episodes.csv")))
SI = {s: i for i, s in enumerate(SYMS)}
OI = {s: dict(v) for s, v in json.load(open(HERE + "/data/oi_hourly.json")).items()}

def trade(i, e, hold=HOLD):
    """Long sym i, entry at open of bar e, exit close of bar e+hold-1; returns (net, reason) or None."""
    if e + hold > N or np.isnan(O[i, e:e + hold]).any() or np.isnan(LO[i, e:e + hold]).any() or np.isnan(C[i, e + hold - 1]): return None
    ent = O[i, e]; cost = ent * (1 + SLIP) * (1 + FEE); stop = ent * (1 - STOP)
    for j in range(e, e + hold):
        if LO[i, j] <= stop:
            return (stop * (1 - SLIP) * (1 - FEE) / cost - 1, "stop")
    return (C[i, e + hold - 1] * (1 - SLIP) * (1 - FEE) / cost - 1, "time")

# ---- rule 1: crash_flush
_cf = {}
def cf_signal(sym, t):
    key = (sym, t)
    if key in _cf: return _cf[key]
    i = SI[sym]; res = False
    if t >= 24 and not np.isnan(C[i, t - 23:t + 1]).any() and not np.isnan(HI[i, t - 23:t + 1]).any():
        bars = tuple(Candle(T0 + b * H, T0 + b * H + H - 1, Dc(repr(float(O[i, b]))), Dc(repr(float(HI[i, b]))),
                            Dc(repr(float(LO[i, b]))), Dc(repr(float(C[i, b]))), Dc(1)) for b in range(t - 23, t + 1))
        if crash_price_gate(bars) is not None:
            cutoff = T0 + (t + 1) * H; obs = OI[sym]
            lab = cutoff - H   # latest label with availability (label+1h) <= cutoff
            if lab in obs:
                tv = tuple(TimedValue(l, Dc(repr(obs[l]))) for l in range(lab - 760 * H, lab + 1, H) if l in obs)
                z = oi_value_4h_change_zscore_tolerant(tv, window_hours=720)
                res = z is not None and z.value < Dc(-2)
    _cf[key] = res; return res

def rule_crash_flush(k):
    out = []
    for sym in sorted(MAJORS):
        for t in range(k, k + WIN + 1):
            if cf_signal(sym, t):
                tr = trade(SI[sym], t + 1)
                if tr: out.append((sym, t + 1, tr))
                break
    return out

# ---- rule 2: alt_dip
_cut = {}
def quarter_cutoff(k):
    d = dtm.datetime.fromtimestamp((T0 + k * H) / 1000, dtm.timezone.utc)
    qs = ms(d.year, ((d.month - 1) // 3) * 3 + 1, 1)
    if qs in _cut: return _cut[qs]
    ret = {}
    a, b = qs - 90 * 24 * H, qs - 3 * 24 * H
    for sym in ALT_SOURCE_UNIVERSE:
        i = SI[sym]; k0 = (a - T0) // H - 25; k1 = (b - T0) // H
        if k0 < 0 or np.isnan(C[i, k0:k1]).any(): continue
        bars = tuple(Candle(T0 + x * H, T0 + x * H + H - 1, Dc(repr(float(O[i, x]))), Dc(repr(float(HI[i, x]))),
                            Dc(repr(float(LO[i, x]))), Dc(repr(float(C[i, x]))), Dc(1)) for x in range(k0, k1))
        r = rolling_24h_return(bars)
        if r: ret[sym] = r
    c = None
    if ret:
        syms = tuple(s for s in ALT_SOURCE_UNIVERSE if s in ret)
        c = calibrate_quarterly_return_cutoff({s: ret[s] for s in syms}, qs, Dc("0.01"), syms)
    _cut[qs] = (float(c.threshold) if c else None, len(ret)); return _cut[qs]

def rule_alt_dip(k):
    thr, n = quarter_cutoff(k)
    if thr is None: return []
    out = []
    for i, sym in enumerate(SYMS):
        for t in range(k, k + WIN + 1):
            if t < 24 or np.isnan(C[i, t]) or np.isnan(C[i, t - 24]): continue
            if C[i, t] / C[i, t - 24] - 1 <= thr:
                tr = trade(i, t + 1)
                if tr: out.append((sym, t + 1, tr))
                break
    return out

# ---- rule 3: bounce_momentum
def rule_bounce(k):
    for t in range(k, k + WIN + 1):
        if t < 4 or t + 1 >= N: continue
        if LVL[t] / LVL[t - 1] - 1 > 0 and LVL[t] > LVL[t - 3:t].max():
            dd = np.full(NS, np.nan)
            for i in range(NS):
                w = HI[i, t - 167:t + 1]
                if t >= 167 and not np.isnan(w).any() and not np.isnan(C[i, t]) and not np.isnan(O[i, t + 1]): dd[i] = C[i, t] / w.max() - 1
            ok = np.where(~np.isnan(dd))[0]
            if len(ok) < 5: return []
            pick = ok[np.argsort(dd[ok])[:5]]
            return [(SYMS[i], t + 1, tr) for i in pick if (tr := trade(i, t + 1))]
    return []

# ---- baseline a
def base_basket(k):
    return [(SYMS[i], k + 1, tr) for i in range(NS) if (tr := trade(i, k + 1))]

RULES = {"crash_flush": rule_crash_flush, "alt_dip": rule_alt_dip, "bounce_momentum": rule_bounce, "baseline_basket_at_entry": base_basket}

def boot(vals, n=2000, seed=1):
    if len(vals) < 2: return None
    rng = np.random.default_rng(seed); v = np.array(vals); m = [v[rng.integers(0, len(v), len(v))].mean() for _ in range(n)]
    return [float(np.percentile(m, 5)), float(np.percentile(m, 95))]

def boot_trades(groups, n=2000, seed=1):
    """cluster bootstrap over episodes of pooled per-trade mean"""
    if len(groups) < 2: return None
    rng = np.random.default_rng(seed); g = [np.array(x) for x in groups]; m = []
    for _ in range(n):
        s = np.concatenate([g[j] for j in rng.integers(0, len(g), len(g))]); m.append(s.mean())
    return [float(np.percentile(m, 5)), float(np.percentile(m, 95))]

def summarize(per_ep, n_eps):
    """per_ep: {ep_id: [net returns]}"""
    trades = [x for v in per_ep.values() for x in v]
    if not trades: return dict(episodes_total=n_eps, episodes_with_trades=0, trades=0)
    epm = [float(np.mean(v)) for v in per_ep.values() if v]
    return dict(episodes_total=n_eps, episodes_with_trades=len(epm), trades=len(trades), mean_trade=float(np.mean(trades)),
                median_trade=float(np.median(trades)), win_rate=float(np.mean(np.array(trades) > 0)),
                mean_per_episode=float(np.mean(epm)), worst_trade=float(min(trades)),
                ci90_mean_per_episode=boot(epm), ci90_mean_trade_cluster=boot_trades([v for v in per_ep.values() if v]))

def run(rule, starts):
    """starts: list of (id, k) -> {id: [(sym,e,(net,reason))]}"""
    return {j: RULES[rule](k) for j, k in starts}

results = {}; trade_rows = []
starts = {s: [(int(r["id"]), int(r["start_k"])) for r in EP if r["split"] == s] for s in ("TRAIN", "HELD")}
# random non-episode hours (excluded: within [-1, +96]h of any real start), seeded, same count per split
def random_starts(split, seed, cnt):
    bad = np.zeros(N, bool)
    for r in EP: k = int(r["start_k"]); bad[max(0, k - 24):k + 97] = True
    avail = np.sum(~np.isnan(C), axis=0)
    lo, hi = (200, SPLIT_K) if split == "TRAIN" else (SPLIT_K, N - 100)
    cand = [k for k in range(lo, hi) if not bad[k] and avail[k] >= 5 and avail[k - 24] >= 5]
    rng = np.random.default_rng(seed); pick = sorted(rng.choice(cand, size=min(cnt, len(cand)), replace=False))
    return [(10000 + j, int(k)) for j, k in enumerate(pick)]

for split, st in starts.items():
    for rule in RULES:
        res = run(rule, st)
        per_ep = {j: [t[2][0] for t in v] for j, v in res.items()}
        results[f"{rule}|{split}|episodes"] = summarize(per_ep, len(st))
        for j, v in res.items():
            for sym, e, (net, why) in v:
                trade_rows.append(dict(rule=rule, set="episode", split=split, ep=j, symbol=sym, entry_utc=iso(e), net=round(net, 5), exit=why))
        if rule == "crash_flush":
            results[f"{rule}|{split}|episodes"]["episodes_with_OI_coverage"] = sum(
                1 for j, k in st if any((T0 + (k + 1) * H - H) in OI[s] for s in OI))
        # random baseline (b): seed 1 reported fully, plus mean of 30 draws
        rs = random_starts(split, 1, len(st)); res = run(rule, rs)
        per_ep = {j: [t[2][0] for t in v] for j, v in res.items()}
        results[f"{rule}|{split}|random_nonepisode"] = summarize(per_ep, len(rs))
        for j, v in res.items():
            for sym, e, (net, why) in v:
                trade_rows.append(dict(rule=rule, set="random", split=split, ep=j, symbol=sym, entry_utc=iso(e), net=round(net, 5), exit=why))
        means = []
        for sd in range(100, 130):
            rr = run(rule, random_starts(split, sd, len(st)))
            e = [np.mean([t[2][0] for t in v]) for v in rr.values() if v]
            if e: means.append(np.mean(e))
        results[f"{rule}|{split}|random_nonepisode"]["mean_per_episode_over_30_draws"] = float(np.mean(means)) if means else None
    print(split, "done", flush=True)
results["alt_dip_quarter_cutoffs"] = {dtm.datetime.fromtimestamp(q / 1000, dtm.timezone.utc).strftime("%Y-%m"): v for q, v in sorted(_cut.items())}
json.dump(results, open(HERE + "/results.json", "w"), indent=1)
with open(HERE + "/trades.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(trade_rows[0])); w.writeheader(); w.writerows(trade_rows)
for k, v in results.items(): print(k, json.dumps(v) if not k.startswith("alt_dip_q") else "")

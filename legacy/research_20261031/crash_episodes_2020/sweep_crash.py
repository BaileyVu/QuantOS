"""Pre-registered sweep (see PREREG_CRASH.txt). New file; does not modify the existing pipeline.
Usage: python sweep_crash.py sweep|select  (sweep caches BUILD metrics in sweep_cache.pkl; select does DSR/placebo/held/ledger)"""
import sys, json, csv, pickle, itertools, time, datetime as dtm
from statistics import NormalDist
from common import *
from quantos.paper.strategies.candidate_features import ALT_SOURCE_UNIVERSE
from quantos.paper.strategies.crash_flush_e2 import MAJORS
from numpy.lib.stride_tricks import sliding_window_view as swv

SI = {s: i for i, s in enumerate(SYMS)}
LEDGER = os.path.expanduser("~/Desktop/PythonProject/edge_vault/search_ledger.jsonl")
EP = list(csv.DictReader(open(HERE + "/episodes.csv")))
EPS = {sp: [(int(r["id"]), int(r["start_k"])) for r in EP if r["split"] == sp] for sp in ("TRAIN", "HELD")}
WIN = 48
HOLDS = [12, 24, 48, 72]; TRAILS = [None, (0.03, 0.02), (0.05, 0.03)]; CATS = [0.15, 0.25]
ENTRIES = ["market", "ladder"]; GATES = ["none", "btc24>-10%"]; MPS = [1, 3, 5]
ZS = [-1.5, -2.0, -2.5]; DDS = [0.06, 0.08, 0.10]; QS = [0.005, 0.01, 0.02]
EX = [(h, ti, c) for h in HOLDS for ti in range(3) for c in CATS]          # 24 exit configs
EXI = {e: n for n, e in enumerate(EX)}
FEES = {"base": (0.0, 0.0004), "stress": (0.0002, 0.0005)}                  # (maker, taker)
SLIP = np.array([0.0005 if s in ("BTCUSDT", "ETHUSDT") else 0.001 for s in SYMS])
RUNGS = (0.01, 0.02, 0.03)
BTC = SI["BTCUSDT"]
btc24 = np.full(N, np.nan); btc24[24:] = C[BTC, 24:] / C[BTC, :-24] - 1
GATE = {"none": np.ones(N, bool), "btc24>-10%": np.nan_to_num(btc24, nan=-1) > -0.10}

# ---------------- signals (vectorised) ----------------
import json as _j
OIraw = _j.load(open(HERE + "/data/oi_hourly.json"))
def oi_z(sym):
    v = np.full(N, np.nan)
    for lab, val in OIraw[sym]:
        if (lab - T0) % H == 0 and 0 <= (lab - T0) // H < N and val > 0 and np.isfinite(val): v[(lab - T0) // H] = val
    ch = np.full(N, np.nan); ch[4:] = v[4:] / v[:-4] - 1
    ok = ~np.isnan(ch); x = np.where(ok, ch, 0.0)
    c1 = np.concatenate([[0], np.cumsum(x)]); c2 = np.concatenate([[0], np.cumsum(x * x)]); cn = np.concatenate([[0], np.cumsum(ok)])
    z = np.full(N, np.nan); t = np.arange(721, N)
    # prior changes = at t-720..t-1 ; padded index p(j)=j+1 -> sum j in [a,b] = c[b+1]-c[a]
    n = cn[t] - cn[t - 720]; s1 = c1[t] - c1[t - 720]; s2 = c2[t] - c2[t - 720]
    with np.errstate(all="ignore"):
        m = s1 / n; var = s2 / n - m * m
        zz = (ch[t] - m) / np.sqrt(var)
    good = (n >= 0.9 * 720) & (var > 1e-18) & ok[t]
    z[t[good]] = zz[good]; return z
def roll_max(a, w=24):
    out = np.full(a.shape, np.nan); out[w - 1:] = swv(a, w).max(axis=1); return out
_cf_cache = {}
def cf_arrays():
    if _cf_cache: return _cf_cache
    for sym in sorted(MAJORS):
        i = SI[sym]; hi24 = roll_max(HI[i]); c24 = roll_max(C[i])      # nan if any nan in window
        chg4 = np.full(N, np.nan); chg4[4:] = C[i, 4:] / C[i, :-4] - 1
        _cf_cache[i] = dict(z=oi_z(sym), hi=hi24, ok=~np.isnan(c24) & ~np.isnan(hi24) & (chg4 < 0), dd=C[i] / hi24 - 1)
    return _cf_cache

def cf_sig(Z, DD):
    out = {}
    for i, a in cf_arrays().items():
        with np.errstate(invalid="ignore"):
            s = a["ok"] & (C[i] <= a["hi"] * (1 - DD)) & (a["z"] < Z)
        out[i] = (s, a["dd"])
    return out

_cut = {}
def cutoffs(Q):
    if Q in _cut: return _cut[Q]
    res = {}
    qs_all = sorted({dtm.datetime.fromtimestamp((T0 + k * H) / 1000, dtm.timezone.utc) for _, k in EPS["TRAIN"] + EPS["HELD"]}, key=lambda d: d)
    for d in {dtm.datetime(x.year, ((x.month - 1) // 3) * 3 + 1, 1, tzinfo=dtm.timezone.utc) for x in qs_all}:
        qs = int(d.timestamp() * 1000); A = (qs - 90 * 24 * H - T0) // H; Bn = (qs - 3 * 24 * H - T0) // H
        pool = []
        for sym in ALT_SOURCE_UNIVERSE:
            i = SI[sym]; k0 = A - 25
            if k0 < 0 or np.isnan(C[i, k0:Bn]).any(): continue
            x = np.arange(A - 1, A + 2087); pool.append(C[i, x] / C[i, x - 24] - 1)
        res[qs] = float(np.quantile(np.concatenate(pool), Q)) if pool else None
    _cut[Q] = res; return res
def quarter_start(k):
    d = dtm.datetime.fromtimestamp((T0 + k * H) / 1000, dtm.timezone.utc); return ms(d.year, ((d.month - 1) // 3) * 3 + 1, 1)
r24 = np.full((NS, N), np.nan); r24[:, 24:] = C[:, 24:] / C[:, :-24] - 1

def events(cfg, split):
    """{ep: [(t, i)]} first fire per symbol in [k,k+WIN], gate-checked, causal max-positions truncation."""
    out = {}
    gate = GATE[cfg["gate"]]
    if cfg["fam"] == "crash_flush":
        sig = cf_sig(cfg["z"], cfg["dd"]); uni = list(sig)
    else:
        cut = cutoffs(cfg["q"]); uni = [SI[s] for s in ALT_SOURCE_UNIVERSE if s in SI]
    for ep, k in EPS[split]:
        ev = []
        if cfg["fam"] == "alt_dip":
            thr = cut.get(quarter_start(k))
            if thr is None: out[ep] = []; continue
        for i in uni:
            if cfg["fam"] == "crash_flush": s, dd = sig[i]; seg = s[k:k + WIN + 1]
            else:
                with np.errstate(invalid="ignore"): seg = r24[i, k:k + WIN + 1] <= thr
            seg = seg & gate[k:k + WIN + 1]
            w = np.flatnonzero(seg)
            w = w[(k + w) >= 24]
            if len(w):
                t = k + int(w[0]); d = dd[t] if cfg["fam"] == "crash_flush" else r24[i, t]
                ev.append((t, d, i))
        ev.sort(); out[ep] = [(t, i) for t, _, i in ev[:cfg["mp"]]]
    return out

# ---------------- trade simulation ----------------
def sim_rung(i, f, ent, end, X, cat):
    """exit price for a long entered at bar f (fill price ent), bars f..end-1. Trail X=(arm,trail) or None; ladder fills pass first-bar flag via f<e handled by caller."""
    lo = LO[i, f:end]; op = O[i, f:end]; cl = C[i, end - 1]
    lvl = ent * (1 - cat); hc = np.flatnonzero(lo <= lvl); jc = hc[0] if len(hc) else 10 ** 9
    jt = 10 ** 9
    if X is not None:
        hi = HI[i, f:end].copy()
        if X[2]: hi[0] = 0.0                                   # ladder: trail high starts after fill bar
        M = np.maximum.accumulate(hi); lv = M * (1 - X[1])
        ht = np.flatnonzero((M >= ent * (1 + X[0])) & (lo <= lv)); jt = ht[0] if len(ht) else 10 ** 9
    if jc == 10 ** 9 and jt == 10 ** 9: return cl
    if jc <= jt: return min(op[jc], lvl)
    return min(op[jt], lv[jt])

def net(ent, ex, maker, slip, fees):
    fm, ft = fees
    cost = ent * (1 + (0 if maker else slip)) * (1 + (fm if maker else ft))
    return ex * (1 - slip) * (1 - ft) / cost - 1

def valid_len(i, e):
    """number of leading bars from e with all fields non-nan"""
    a = np.isnan(O[i, e:e + 72]) | np.isnan(LO[i, e:e + 72]) | np.isnan(HI[i, e:e + 72]) | np.isnan(C[i, e:e + 72])
    w = np.flatnonzero(a); return (int(w[0]) if len(w) else min(72, N - e)) if e < N else 0

def sim_one(i, t, entry, exi):
    """-> (net_base, net_stress) or None"""
    h, ti, cat = EX[exi]; e = t + 1
    if e + h > N or valid_len(i, e) < h: return None
    X = TRAILS[ti]; slip = SLIP[i]; end = e + h
    if entry == "market":
        Xm = None if X is None else (X[0], X[1], False)
        ent = O[i, e]; ex = sim_rung(i, e, ent, end, Xm, cat)
        return tuple(net(ent, ex, False, slip, FEES[k]) for k in ("base", "stress"))
    tot = np.zeros(2); sig_close = C[i, t]
    if np.isnan(sig_close): return None
    for p in RUNGS:
        L = sig_close * (1 - p); fl = np.flatnonzero(LO[i, e:e + 6] < L)
        if not len(fl): continue
        f = e + int(fl[0]); Xm = None if X is None else (X[0], X[1], True)
        ex = sim_rung(i, f, L, end, Xm, cat)
        tot += [net(L, ex, True, slip, FEES[k]) for k in ("base", "stress")]
    return tuple(tot / 3)

_sc = {}
def sim_cached(i, t, entry, exi):
    k = (i, t, entry, exi)
    if k not in _sc: _sc[k] = sim_one(i, t, entry, exi)
    return _sc[k]

# ---------------- evaluation ----------------
def boot_ci(v, n=2000, seed=1):
    v = np.asarray(v); rng = np.random.default_rng(seed)
    m = v[rng.integers(0, len(v), (n, len(v)))].mean(1); return float(np.percentile(m, 5)), float(np.percentile(m, 95))

def evaluate(cfg, split="TRAIN", ev=None):
    ev = ev if ev is not None else events(cfg, split)
    exi = EXI[(cfg["h"], cfg["trail"], cfg["cat"])]; epm = []; epm_s = []; nt = 0
    for ep, lst in ev.items():
        r = [x for x in (sim_cached(i, t, cfg["entry"], exi) for t, i in lst) if x is not None]
        if r: nt += len(r); a = np.mean(r, axis=0); epm.append(a[0]); epm_s.append(a[1])
    out = dict(trades=nt, episodes=len(epm))
    if len(epm) >= 2:
        v = np.array(epm); sd = v.std(ddof=1); lo, hi = boot_ci(v)
        out.update(mean=float(v.mean()), ci=(lo, hi), stress=float(np.mean(epm_s)), sr=float(v.mean() / sd) if sd > 0 else 0.0,
                   skew=float(((v - v.mean()) ** 3).mean() / v.std() ** 3) if v.std() > 0 else 0.0,
                   kurt=float(((v - v.mean()) ** 4).mean() / v.std() ** 4) if v.std() > 0 else 3.0, epm=v)
    else: out.update(mean=float("nan"), ci=(float("nan"),) * 2, stress=float("nan"), sr=0.0, skew=0.0, kurt=3.0, epm=np.array(epm))
    return out

def grid(fam):
    ex = [dict(h=h, trail=ti, cat=c) for h, ti, c in EX]
    base = list(itertools.product(range(4), range(3), range(2), range(2), range(2), range(3)))
    sub = list(itertools.product(range(3), range(3))) if fam == "crash_flush" else [(q,) for q in range(3)]
    for s in sub:
        for hi_, ti, ci, en, ga, mp in base:
            cfg = dict(fam=fam, h=HOLDS[hi_], trail=ti, cat=CATS[ci], entry=ENTRIES[en], gate=GATES[ga], mp=MPS[mp])
            if fam == "crash_flush": cfg.update(z=ZS[s[0]], dd=DDS[s[1]])
            else: cfg.update(q=QS[s[0]])
            yield (s + (hi_, ti, ci, en, ga, mp)), cfg

def params_of(cfg):
    p = {k: v for k, v in cfg.items() if k != "fam"}
    p["trail"] = [None, "arm3/trail2", "arm5/trail3"][cfg["trail"]]; return p
def vid(fam, key): return f"{fam}_sweep_" + "".join(str(x) for x in key)

def run_sweep():
    res = {}
    t0 = time.time()
    for fam in ("crash_flush", "alt_dip"):
        evc = {}
        for key, cfg in grid(fam):
            sk = (cfg.get("z"), cfg.get("dd"), cfg.get("q"), cfg["gate"], cfg["mp"])
            if sk not in evc: evc[sk] = events(cfg, "TRAIN")
            res[(fam, key)] = (cfg, evaluate(cfg, "TRAIN", evc[sk]))
        print(fam, "done", len(res), round(time.time() - t0), "s", len(_sc), "sims", flush=True)
    pickle.dump({k: (c, {kk: vv for kk, vv in r.items()}) for k, (c, r) in res.items()}, open(HERE + "/sweep_cache.pkl", "wb"))

# ---------------- selection ----------------
def nbrs(key, fam):
    for d in range(len(key)):
        top = (3, 3, 4, 3, 2, 2, 2, 3) if fam == "crash_flush" else (3, 4, 3, 2, 2, 2, 3)
        for s in (-1, 1):
            v = key[d] + s
            if 0 <= v < top[d]: yield key[:d] + (v,) + key[d + 1:]

def dsr(sr, T, skew, kurt, sr0):
    den = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    return (sr - sr0) * np.sqrt(max(T - 1, 1)) / np.sqrt(den) if den > 0 else float("nan")

def sr0_of(srs, Nn):
    g = 0.5772156649; nd = NormalDist()
    return float(np.std(srs, ddof=1) * ((1 - g) * nd.inv_cdf(1 - 1 / Nn) + g * nd.inv_cdf(1 - 1 / (Nn * np.e))))

def placebo(cfg, ev, ndraw=200, seed=7):
    """random-entry placebo: same #episodes & trades/episode, random non-episode start hours & symbols; returns array of draw means"""
    exi = EXI[(cfg["h"], cfg["trail"], cfg["cat"])]; bad = np.zeros(N, bool)
    for _, k in EPS["TRAIN"] + EPS["HELD"]: bad[max(0, k - 24):k + 97] = True
    lo = (ms(2021, 12, 1) - T0) // H if cfg["fam"] == "crash_flush" else (ms(2020, 7, 1) - T0) // H
    pool = np.array([k for k in range(lo, SPLIT_K - 100) if not bad[k]])
    uni = [SI[s] for s in sorted(MAJORS)] if cfg["fam"] == "crash_flush" else [SI[s] for s in ALT_SOURCE_UNIVERSE]
    cnts = [len(v) for v in ev.values() if v]; rng = np.random.default_rng(seed); means = []
    for _ in range(ndraw):
        epm = []
        for c in cnts:
            for _try in range(20):
                t = int(rng.choice(pool)); av = [i for i in uni if not np.isnan(C[i, t]) and not np.isnan(O[i, t + 1])]
                if len(av) < c: continue
                pick = rng.choice(av, size=c, replace=False)
                r = [x for x in (sim_cached(int(i), t, cfg["entry"], exi) for i in pick) if x is not None]
                if r: epm.append(np.mean(r, axis=0)[0]); break
        if epm: means.append(np.mean(epm))
    return np.array(means)

BASE = {"crash_flush": dict(fam="crash_flush", h=24, trail=0, cat=0.25, entry="market", gate="none", mp=99, z=-2.0, dd=0.08),
        "alt_dip": dict(fam="alt_dip", h=24, trail=0, cat=0.25, entry="market", gate="none", mp=99, q=0.01)}

def bp(x): return None if x is None or x != x else round(x * 1e4, 1)
def ledger_rows(rows):
    with open(LEDGER, "a") as f:
        f.write("".join(json.dumps(r) + "\n" for r in rows)); f.flush()
def nlines():
    try: return sum(1 for _ in open(LEDGER))
    except FileNotFoundError: return 0

def mkrow(fam, vid_, cfg, period, r, verdict, extra=None):
    d = dict(ts=dtm.datetime.now(dtm.timezone.utc).isoformat(timespec="seconds"), family=f"crash_episodes_2020/{fam}", variant_id=vid_,
             params=params_of(cfg), period=period, trades=r["trades"], episodes=r["episodes"], mean_net_bp=bp(r["mean"]),
             ci90=[bp(r["ci"][0]), bp(r["ci"][1])], verdict=verdict)
    if extra: d.update(extra)
    return d

def run_select():
    cache = pickle.load(open(HERE + "/sweep_cache.pkl", "rb"))
    Nn = nlines() + len(cache) + 81 + 8
    print("N =", Nn, flush=True)
    ledger, csvrows, finalists = [], [], {}
    for fam in ("crash_flush", "alt_dip"):
        keys = [k for (f, k) in cache if f == fam]
        srs = [cache[(fam, k)][1]["sr"] for k in keys if cache[(fam, k)][1]["episodes"] >= 5]
        sr0 = sr0_of(srs, Nn); print(fam, "SR0", round(sr0, 4), "sd(SR)", round(float(np.std(srs, ddof=1)), 4), flush=True)
        info = {}
        for k in keys:
            c, r = cache[(fam, k)]
            z = dsr(r["sr"], r["episodes"], r["skew"], r["kurt"], sr0) if r["episodes"] >= 5 else float("nan")
            info[k] = dict(dsr=z, prob=NormalDist().cdf(z) if z == z else float("nan"))
        def cheap(k):
            c, r = cache[(fam, k)]; why = []
            if not (info[k]["dsr"] > 0): why.append("dsr<=0")
            if not (r["ci"][0] > 0): why.append("ci_lo<=0")
            if not (r["stress"] >= 0): why.append("stress<0")
            if r["episodes"] < 30: why.append("eps<30")
            nb = [n for n in nbrs(k, fam)]
            if not all(cache[(fam, n)][1]["mean"] > 0 for n in nb): why.append("neighbour<=0")
            return why
        whys = {k: cheap(k) for k in keys}
        order = sorted(keys, key=lambda k: -(info[k]["dsr"] if info[k]["dsr"] == info[k]["dsr"] else -9))
        fin, pl = [], {}; checked = 0
        for k in order:
            if len(fin) >= 2 or checked >= 25: break
            if whys[k]: continue
            if fin and k in set(nbrs(fin[0], fam)): whys[k] = ["neighbour_of_finalist1"]; continue
            c, r = cache[(fam, k)]; checked += 1
            ev = events(c, "TRAIN"); pm = placebo(c, ev); p90 = float(np.percentile(pm, 90)); pl[k] = (p90, len(pm))
            if r["mean"] > p90: fin.append(k)
            else: whys[k] = ["placebo_p90"]
        for k in keys:
            c, r = cache[(fam, k)]
            v = "FINALIST" if k in fin else ("fail:" + ",".join(whys[k]) if whys[k] else "pass_filters;placebo_not_run(lower_rank_than_finalists)")
            ledger.append(mkrow(fam, vid(fam, k), c, "BUILD", r, v, dict(dsr_z=round(info[k]["dsr"], 3) if info[k]["dsr"] == info[k]["dsr"] else None)))
            csvrows.append(dict(family=fam, variant=vid(fam, k), period="BUILD", **params_of(c), trades=r["trades"], episodes=r["episodes"], mean_bp=bp(r["mean"]),
                                ci_lo_bp=bp(r["ci"][0]), ci_hi_bp=bp(r["ci"][1]), stress_bp=bp(r["stress"]), sharpe_ep=round(r["sr"], 3),
                                dsr_z=round(info[k]["dsr"], 3) if info[k]["dsr"] == info[k]["dsr"] else "", dsr_prob=round(info[k]["prob"], 3) if info[k]["prob"] == info[k]["prob"] else "", verdict=v))
        finalists[fam] = (fin, pl, order[:5], info, sr0, cache)
        print(fam, "finalists", fin, "placebo p90", {k: v for k, v in pl.items()}, flush=True)
        print(" top5 by DSR:", [(k, round(info[k]["dsr"], 2), bp(cache[(fam, k)][1]["mean"]), cache[(fam, k)][1]["episodes"], whys[k]) for k in order[:5]], flush=True)
    # held-out: baselines + finalists, once each
    held = []
    for fam in ("crash_flush", "alt_dip"):
        fin, pl, _, info, sr0, cache = finalists[fam]
        b = BASE[fam]
        for sp, per in (("TRAIN", "BUILD"), ("HELD", "HELDOUT")):
            r = evaluate(b, sp); held.append((fam, f"{fam}_current_rule", b, per, r, "baseline_current_rule", None))
        for k in fin:
            c = cache[(fam, k)][0]; r = evaluate(c, "HELD")
            held.append((fam, vid(fam, k), c, "HELDOUT", r, "HELD_OUT_ONCE", pl.get(k)))
    for fam, v_, c, per, r, verdict, plc in held:
        ledger.append(mkrow(fam, v_ + ("_heldout" if per == "HELDOUT" else "_build_new_costs"), c, per, r, verdict))
        csvrows.append(dict(family=fam, variant=v_, period=per, **params_of(c), trades=r["trades"], episodes=r["episodes"], mean_bp=bp(r["mean"]),
                            ci_lo_bp=bp(r["ci"][0]), ci_hi_bp=bp(r["ci"][1]), stress_bp=bp(r["stress"]), sharpe_ep=round(r["sr"], 3), verdict=verdict))
        print("HELD" if per == "HELDOUT" else "BUILD", v_, "trades", r["trades"], "eps", r["episodes"], "mean_bp", bp(r["mean"]), "ci", [bp(x) for x in r["ci"]], "stress", bp(r["stress"]), flush=True)
    keys_ = sorted({k for r in csvrows for k in r}, key=lambda s: s)
    allk = ["family", "variant", "period", "fam", "h", "trail", "cat", "entry", "gate", "mp", "z", "dd", "q", "trades", "episodes", "mean_bp", "ci_lo_bp", "ci_hi_bp", "stress_bp", "sharpe_ep", "dsr_z", "dsr_prob", "verdict"]
    with open(HERE + "/sweep_crash.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=allk, extrasaction="ignore"); w.writeheader(); w.writerows(csvrows)
    ledger_rows(ledger)
    print("ledger appended", len(ledger), "lines; total lines now", nlines(), "-> N_final =", nlines() + 81, flush=True)
    pickle.dump(dict(finalists={f: (v[0], v[1]) for f, v in finalists.items()}), open(HERE + "/select_out.pkl", "wb"))

if __name__ == "__main__":
    {"sweep": run_sweep, "select": run_select}[sys.argv[1]]()

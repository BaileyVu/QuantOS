"""trend_2020: tsmom_4h sweep (BUILD only unless --held). See PREREG.txt."""
import os, sys, json, itertools, time, datetime as dt
import numpy as np, pandas as pd
from scipy.stats import norm, skew, kurtosis
HERE = os.path.dirname(os.path.abspath(__file__)); H4 = 4 * 3600_000; WEEK = 7 * 24 * 3600_000
LEDGER = os.path.expanduser("~/Desktop/PythonProject/edge_vault/search_ledger.jsonl")
D = np.load(HERE + "/../crash_episodes_2020/data/prices.npz"); T0 = int(D["T0"]); SYMS = [str(s)[:-4] for s in D["symbols"]]; NS = len(SYMS)
def rs(x, f): b = x.reshape(NS, -1, 4); v = f(b); v[np.isnan(b).any(2)] = np.nan; return v
O4 = rs(D["O"], lambda b: b[:, :, 0]); H4a = rs(D["H"], lambda b: b.max(2)); L4 = rs(D["L"], lambda b: b.min(2)); C4 = rs(D["C"], lambda b: b[:, :, 3]); n4 = C4.shape[1]
SPLIT4 = (int(dt.datetime(2023, 7, 1, tzinfo=dt.timezone.utc).timestamp() * 1000) - T0) // H4; LO = 500
TK = 1e-5; SLIPS = {s: (0.0005 if s in ("BTC", "ETH") else 0.0010) for s in SYMS}; SC = {"base": (0.0, 0.0004), "stress": (0.0002, 0.0005)}
P = {s: (O4[i].tolist(), H4a[i].tolist(), L4[i].tolist(), C4[i].tolist()) for i, s in enumerate(SYMS)}
ATR = {}; SIG = {}
for i, s in enumerate(SYMS):
    h, l, c = pd.Series(H4a[i]), pd.Series(L4[i]), pd.Series(C4[i])
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1, skipna=False)
    ATR[s] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().values.tolist()
    for E in (50, 100):
        SIG[(s, "slope", E)] = (c.ewm(span=E, adjust=False, min_periods=E).mean().diff()).values
    for Lb in (90, 180, 360): SIG[(s, "ret", Lb)] = (c / c.shift(Lb) - 1).values
btc = pd.Series(C4[SYMS.index("BTC")]); BTCBELOW = (btc < btc.ewm(span=200, adjust=False, min_periods=200).mean()).values
CAND = {}
for Lb in (90, 180, 360):
    for E in (50, 100):
        lst = [[] for _ in range(n4)]
        for s in SYMS:
            r, sl, a = SIG[(s, "ret", Lb)], SIG[(s, "slope", E)], np.array(ATR[s])
            d = np.where((r > 0) & (sl > 0), 1, np.where((r < 0) & (sl < 0), -1, 0)); d[np.isnan(a) | np.isnan(r) | np.isnan(sl)] = 0
            for i in np.flatnonzero(d): lst[i].append((float(abs(r[i])), s, int(d[i])))
        for l in lst: l.sort(reverse=True)
        CAND[(Lb, E)] = lst

def run_trade(s, i, side, stopk, trail, entry, hi):
    O, H, L, C = P[s]; atr0 = ATR[s][i]; c0 = C[i]; stop = c0 - side * stopk * atr0
    if entry == "mkt":
        fb = i + 1
        if fb >= hi or O[fb] != O[fb]: return None
        fpx = O[fb]
    else:
        lim = c0 - side * 0.25 * atr0; fb = None
        for j in (i + 1, i + 2):
            if j >= hi: return None
            if (side == 1 and L[j] < lim * (1 - TK)) or (side == -1 and H[j] > lim * (1 + TK)): fb = j; break
        if fb is None: return ("miss", i + 2)
        fpx = lim
    R = abs(fpx - stop); best = fpx
    for b in range(fb, fb + 19):
        if b >= hi or C[b] != C[b]: return None
        if b - fb >= 18: return done(s, side, entry, fb, b, fpx, O[b], "time", atr0, c0)
        gap = not (b == fb and entry == "lmt")
        if side == 1 and L[b] <= stop: return done(s, side, entry, fb, b, fpx, min(O[b], stop) if gap else stop, "stop", atr0, c0)
        if side == -1 and H[b] >= stop: return done(s, side, entry, fb, b, fpx, max(O[b], stop) if gap else stop, "stop", atr0, c0)
        if trail and not (b == fb and entry == "lmt"):
            best = max(best, H[b]) if side == 1 else min(best, L[b])
            if (best - fpx) * side >= R: stop = max(stop, best - 2 * R) if side == 1 else min(stop, best + 2 * R)
    return None
def done(s, side, entry, fb, b, fpx, xpx, et, atr0, c0):
    g = side * (xpx / fpx - 1); out = dict(sym=s, side=side, fill=fb, exit=b, gross=g, etype=et, atrpct=atr0 / c0, w_vol=min(3.0, 0.01 / (atr0 / c0)))
    for sc, (mk, tk) in SC.items(): out["net_" + sc] = g - (tk + SLIPS[s] if entry == "mkt" else mk) - (tk + SLIPS[s])
    return out

def portfolio(cfg, lo, hi):
    Lb, E, stopk, trail, sm, vol, entry = cfg; trades = []; act = {}
    for i in range(lo, hi - 3):
        for s in [s for s, e in act.items() if e <= i]: del act[s]
        free = 5 - len(act)
        if free <= 0: continue
        for (_, s, d) in CAND[(Lb, E)][i]:
            if s in act: continue
            if sm == "long" and d != 1: continue
            if sm == "lf" and d == -1 and not BTCBELOW[i]: continue
            r = run_trade(s, i, d, stopk, trail, entry, hi)
            if r is None: continue
            if isinstance(r, tuple): act[s] = r[1]
            else: act[s] = r["exit"]; r["btcbelow"] = bool(BTCBELOW[i]); trades.append(r)
            free -= 1
            if free == 0: break
    return trades

def df_of(trades, vol):
    df = pd.DataFrame(trades); w = df.w_vol if vol else 1.0
    df["pnl_b"] = df.net_base * w; df["pnl_s"] = df.net_stress * w; df["week"] = (T0 + df.fill * H4) // WEEK; return df
def boot_ci(df, col, nb=2000, seed=1):
    g = df.groupby("week")[col].agg(["sum", "count"]); s, c = g["sum"].values, g["count"].values; k = len(g)
    ix = np.random.default_rng(seed).integers(0, k, (nb, k)); return tuple(np.percentile(s[ix].sum(1) / c[ix].sum(1), [5, 95]))
def stats(trades, vol):
    if len(trades) < 10: return dict(trades=len(trades), mean_bp=np.nan, ci_lo=np.nan, ci_hi=np.nan, stress_bp=np.nan, stop_share=np.nan, maxdd_usd=np.nan, sr=np.nan, win=np.nan, x=np.array([]))
    df = df_of(trades, vol); x = df.pnl_b.values; ci = boot_ci(df, "pnl_b"); pnl = df.sort_values("exit").pnl_b.values * 10; cum = np.cumsum(pnl)
    return dict(trades=len(df), win=float((x > 0).mean()), mean_bp=x.mean() * 1e4, median_bp=np.median(x) * 1e4, ci_lo=ci[0] * 1e4, ci_hi=ci[1] * 1e4, stress_bp=df.pnl_s.mean() * 1e4,
                stop_share=float((df.etype == "stop").mean()), maxdd_usd=float((cum - np.maximum.accumulate(np.concatenate([[0], cum]))[1:]).min()), sr=float(x.mean() / x.std(ddof=1)), x=x)

def pools(lo, hi, n=5000):
    rng = np.random.default_rng(9); out = {}
    for stopk, trail, entry in itertools.product((2, 3), (0, 1), ("mkt", "lmt")):
        rows = []
        for _ in range(n):
            s = SYMS[rng.integers(NS)]; i = int(rng.integers(lo, hi - 30)); side = int(rng.choice((-1, 1)))
            if ATR[s][i] != ATR[s][i] or P[s][3][i] != P[s][3][i]: continue
            r = run_trade(s, i, side, stopk, trail, entry, hi)
            if isinstance(r, dict): r["btcbelow"] = bool(BTCBELOW[i]); rows.append(r)
        out[(stopk, trail, entry)] = pd.DataFrame(rows)
    return out
def placebo_pct(pool, cfg, real_mean, ntr, seeds=200):
    sm, vol = cfg[4], cfg[5]; p = pool
    if sm == "long": p = p[p.side == 1]
    elif sm == "lf": p = p[(p.side == 1) | p.btcbelow]
    v = (p.net_base * (p.w_vol if vol else 1.0)).values; rng = np.random.default_rng(11)
    m = np.array([v[rng.integers(0, len(v), ntr)].mean() for _ in range(seeds)]); return float((m < real_mean).mean() * 100)
def dsr_prob(x, sr, sr0):
    z = (sr - sr0) * np.sqrt(len(x) - 1) / np.sqrt(1 - skew(x) * sr + (kurtosis(x, fisher=False) - 1) / 4 * sr ** 2); return float(norm.cdf(z))

GRID = list(itertools.product((90, 180, 360), (50, 100), (2, 3), (0, 1), ("long", "lf", "both"), (False, True), ("mkt", "lmt")))
def vid(c): return f"ts_L{c[0]}_E{c[1]}_st{c[2]}_tr{c[3]}_{c[4]}_vol{int(c[5])}_{c[6]}"
def axes(c): return (c[0], c[1], c[2], c[3], ["long", "lf", "both"].index(c[4]), int(c[5]), ["mkt", "lmt"].index(c[6]))
CUR = (180, 50, 2, 1, "both", False, "mkt")

def main():
    t0 = time.time(); rows = []
    for cfg in GRID:
        tr = portfolio(cfg, LO, SPLIT4); st = stats(tr, cfg[5]); rows.append(dict(cfg=cfg, variant_id=vid(cfg), axes=axes(cfg), current=(cfg == CUR), tr=tr, **st))
    print("sweep done", round(time.time() - t0)); pl = pools(LO, SPLIT4); print("pools", round(time.time() - t0))
    for r in rows:
        r["placebo_pct"] = placebo_pct(pl[(r["cfg"][2], r["cfg"][3], r["cfg"][6])], r["cfg"], r["mean_bp"] / 1e4, r["trades"]) if r["trades"] >= 10 else np.nan
    try: nlines = sum(1 for _ in open(LEDGER))
    except FileNotFoundError: nlines = 0
    Ntr = nlines + len(rows) + 81; srs = np.array([r["sr"] for r in rows if r["sr"] == r["sr"]]); sr_var = float(srs.var(ddof=1)); emc = 0.5772156649
    sr0 = float(np.sqrt(sr_var) * ((1 - emc) * norm.ppf(1 - 1 / Ntr) + emc * norm.ppf(1 - 1 / (Ntr * np.e))))
    byax = {r["axes"]: r for r in rows}; dom = [sorted({a[i] for a in byax}) for i in range(7)]
    for r in rows:
        x = r["x"]; ok = len(x) >= 10 and x.std() > 0
        r["dsr_prob"] = dsr_prob(x, r["sr"], sr0) if ok else np.nan; r["dsr_pos"] = bool(ok and r["sr"] > sr0)
        nb = []
        for k in range(7):
            j = dom[k].index(r["axes"][k])
            for jj in (j - 1, j + 1):
                if 0 <= jj < len(dom[k]): nb.append(byax[tuple(list(r["axes"][:k]) + [dom[k][jj]] + list(r["axes"][k + 1:]))])
        r["nbrs_pos"] = int(sum(1 for q in nb if q["mean_bp"] > 0)); r["n_nbrs"] = len(nb)
        r["finalist_rules"] = bool(ok and r["dsr_pos"] and r["ci_lo"] > 0 and r["stress_bp"] >= 0 and r["trades"] >= 200 and (r["placebo_pct"] > 90) and r["nbrs_pos"] == r["n_nbrs"])
    cols = ["variant_id", "current", "trades", "win", "mean_bp", "ci_lo", "ci_hi", "stress_bp", "stop_share", "maxdd_usd", "sr", "placebo_pct", "dsr_prob", "dsr_pos", "nbrs_pos", "n_nbrs", "finalist_rules"]
    df = pd.DataFrame(rows); df[cols].round(4).to_csv(HERE + "/sweep.csv", index=False)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with open(LEDGER, "a") as f:
        for r in rows:
            f.write(json.dumps(dict(ts=now, family="tsmom_4h", variant_id=r["variant_id"], params=dict(zip(["L", "ema", "stop_atr", "trail", "side", "vol_target", "entry"], r["cfg"])), period="DEVELOP",
                 trades=int(r["trades"]), mean_net_bp=None if r["mean_bp"] != r["mean_bp"] else round(float(r["mean_bp"]), 2), ci90=[None if c != c else round(float(c), 2) for c in (r["ci_lo"], r["ci_hi"])],
                 verdict="FINALIST(rules)" if r["finalist_rules"] else ("current-config reference" if r["current"] else "not eligible"))) + "\n")
    print("N", Ntr, "sr_var", round(sr_var, 5), "sr0", round(sr0, 4), "ledger_before", nlines)
    print("mean>0:", int((df.mean_bp > 0).sum()), "ci_lo>0:", int((df.ci_lo > 0).sum()), "dsr_pos:", int(df.dsr_pos.sum()), "finalists:", int(df.finalist_rules.sum()), "of", len(df))
    print(df[df.current][cols].round(3).to_string(index=False))
    print(df.sort_values("mean_bp", ascending=False).head(6)[["variant_id", "trades", "mean_bp", "ci_lo", "stress_bp", "placebo_pct", "dsr_prob", "nbrs_pos", "n_nbrs"]].round(3).to_string(index=False))
    print("median mean_bp", round(df.mean_bp.median(), 2))
    fin = df[df.finalist_rules].sort_values("dsr_prob", ascending=False).head(2)
    json.dump(dict(N=Ntr, sr0=sr0, sr_var=sr_var, finalists=fin.variant_id.tolist()), open(HERE + "/build_summary.json", "w"), indent=1)
if __name__ == "__main__": main()

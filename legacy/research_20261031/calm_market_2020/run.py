"""calm_market_2020 maker-entry study: range_mr_maker_v1 and grid_calm_maker_v1.  Pre-registration: PREREG.txt.
One command:  ~/Desktop/PythonProject/cci_study/.venv/bin/python run.py [--develop-only]
Reads only ../crash_episodes_2020/data/prices.npz (hourly OHLC, USDT-M).  No network.  FEES ARE PLACEHOLDERS (see PREREG)."""
import os, sys, json, datetime as dt
import numpy as np, pandas as pd
from scipy.stats import norm, skew, kurtosis
HERE = os.path.dirname(os.path.abspath(__file__)); H_MS = 3600_000; WEEK = 7 * 24 * H_MS
DEV_ONLY = "--develop-only" in sys.argv
D = np.load(HERE + "/../crash_episodes_2020/data/prices.npz")
O_, H_, L_, C_ = D["O"], D["H"], D["L"], D["C"]; T0 = int(D["T0"]); ALL = [str(s)[:-4] for s in D["symbols"]]; N = C_.shape[1]
SPLIT_K = (int(dt.datetime(2023, 7, 1, tzinfo=dt.timezone.utc).timestamp() * 1000) - T0) // H_MS
LAST_K = N if not DEV_ONLY else SPLIT_K          # develop-only mode never touches HELD bars
TK = 1e-5                                        # 1 tick = 1e-5 of price
SC = {"placeholder": dict(mk=0.0, tk=0.0004), "stress": dict(mk=0.0002, tk=0.0005)}
MR_SYMS = "BTC ETH SOL BNB XRP ADA DOGE LINK".split(); GRID_SYMS = ["BTC", "ETH"]
CAP = 50.0

def slip(sym): return 0.0005 if sym in ("BTC", "ETH") else 0.0010
def net(gross, sym, etype, sc):
    p = SC[sc]; ex = p["mk"] if etype == "target" else p["tk"] + slip(sym)
    return gross - p["mk"] - ex

def prep(sym):
    i = ALL.index(sym); o, h, l, c = [pd.Series(x[i, :LAST_K]) for x in (O_, H_, L_, C_)]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1, skipna=False)
    atr = tr.rolling(14).mean(); ap = atr / c; med = ap.rolling(720).median()
    sma100 = c.rolling(100).mean(); sma20 = c.rolling(20).mean(); sd = c.rolling(20).std(ddof=0)
    reg = ((ap < med) & ((c / sma100 - 1).abs() < 0.03)).fillna(False)
    d = c.diff(); up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    dn = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + up / dn)
    lo_b, up_b = sma20 - 2 * sd, sma20 + 2 * sd
    sl = (reg & (c < lo_b) & (rsi < 30)).fillna(False); ss = (reg & (c > up_b) & (rsi > 70)).fillna(False)
    L = lambda s: s.tolist()
    return dict(sym=sym, n=len(c), O=L(o), H=L(h), L=L(l), C=L(c), sma20=L(sma20), atr=L(atr), sma100=L(sma100),
                reg=reg.values.tolist(), sl=sl.values.tolist(), ss=ss.values.tolist())

def period(k): return "DEVELOP" if k < SPLIT_K else "HELD"

# ---------------------------------------------------------------- range_mr_maker_v1
def mr_trade(a, t, side):
    """Post-only limit at signal close, valid bars t+1..t+3. Returns ('miss', last_bar) | ('drop', bar) | ('ok', dict)."""
    n = a["n"]; O, H, L, C, S20 = a["O"], a["H"], a["L"], a["C"], a["sma20"]
    lim = C[t]; atr0 = a["atr"][t]; fb = None
    for j in range(t + 1, t + 4):
        if j >= n: return ("drop", n)
        if C[j] != C[j]: return ("drop", j)
        if (side == 1 and L[j] < lim * (1 - TK)) or (side == -1 and H[j] > lim * (1 + TK)): fb = j; break
    if fb is None: return ("miss", t + 3)
    entry = lim; stop = entry - side * 2 * atr0
    def done(b, px, et): return ("ok", dict(sym=a["sym"], side=side, t_sig=t, fill=fb, entry=entry, exit_bar=b, exit=px, etype=et,
                                          gross=side * (px / entry - 1)))
    for b in range(fb, fb + 24):
        if b >= n or C[b] != C[b]: return ("drop", b)
        if side == 1 and L[b] <= stop: return done(b, min(O[b], stop) if b > fb else stop, "stop")
        if side == -1 and H[b] >= stop: return done(b, max(O[b], stop) if b > fb else stop, "stop")
        if b > fb:
            tp = S20[b - 1]
            if tp == tp and ((side == 1 and H[b] > tp * (1 + TK)) or (side == -1 and L[b] < tp * (1 - TK))): return done(b, tp, "target")
    b = fb + 24
    if b >= n or O[b] != O[b]: return ("drop", b)
    return done(b, O[b], "time")

def mr_real(a):
    trades = []; placed = missed = dropped = 0; t = 0; n = a["n"]
    while t < n - 30:
        if a["sl"][t] or a["ss"][t]:
            side = 1 if a["sl"][t] else -1; placed += 1
            kind, r = mr_trade(a, t, side)
            if kind == "miss": missed += 1; t = r; continue
            if kind == "drop": dropped += 1; t += 1; continue
            trades.append(r); t = r["exit_bar"]; continue
        t += 1
    return trades, placed, missed, dropped

# ---------------------------------------------------------------- grid_calm_maker_v1
def grid_sim(a):
    n = a["n"]; O, H, L, C = a["O"], a["H"], a["L"], a["C"]; reg = a["reg"]; trades = []
    active = needoff = flat_next = False; pos = {}; armed = set(); center = step = 0.0; grids = stops = 0; fills = 0; gfill = 0; gid = 0; gflag = False; grid_hours = 0
    sym = a["sym"]
    def close(k, b, px, et):
        eb, ep = pos[k]; side = 1 if k < 0 else -1
        trades.append(dict(sym=sym, side=side, level=abs(k), grid=gid, fill=eb, entry=ep, exit_bar=b, exit=px, etype=et, gross=side * (px / ep - 1)))
    for b in range(n):
        if active:
            if flat_next:
                for k in list(pos): close(k, b, O[b], "regime")
                pos = {}; active = False; flat_next = False
            else:
                grid_hours += 1
                pr = lambda k: center + k * step
                for k in (-1, -2, -3, 1, 2, 3):      # post-only: a (re)placed order must sit on the passive side of the last close
                    if k not in pos and k not in armed and ((k < 0 and pr(k) < C[b - 1]) or (k > 0 and pr(k) > C[b - 1])): armed.add(k)
                dn = L[b] <= pr(-4); upx = H[b] >= pr(4); closed = set()
                if not (dn or upx):
                    for k in list(pos):
                        if pos[k][0] < b:
                            tg = pr(k + 1) if k < 0 else pr(k - 1)
                            if (k < 0 and H[b] > tg * (1 + TK)) or (k > 0 and L[b] < tg * (1 - TK)):
                                close(k, b, tg, "target"); del pos[k]; closed.add(k)
                for k in (-1, -2, -3, 1, 2, 3):
                    if k in pos or k in closed or k not in armed: continue
                    p = pr(k)
                    if (k < 0 and L[b] < p * (1 - TK)) or (k > 0 and H[b] > p * (1 + TK)):
                        pos[k] = (b, p); armed.discard(k); fills += 1
                        if not gflag: gflag = True; gfill += 1
                if dn or upx:
                    use_dn = dn and (not upx or O[b] <= center)
                    px = (pr(-4) if O[b] > pr(-4) else O[b]) if use_dn else (pr(4) if O[b] < pr(4) else O[b])
                    for k in list(pos): close(k, b, px, "stop")
                    pos = {}; active = False; needoff = True; stops += 1
        if active and not reg[b]: flat_next = True
        if not reg[b]: needoff = False
        if (not active) and reg[b] and (not needoff) and a["sma100"][b] == a["sma100"][b] and b < n - 2 and abs(C[b] - a["sma100"][b]) < 4 * 0.5 * a["atr"][b]:
            center = a["sma100"][b]; step = 0.5 * a["atr"][b]; active = True; pos = {}; grids += 1; gid += 1; gflag = False
            armed = {k for k in (-1, -2, -3, 1, 2, 3) if (k < 0 and center + k * step < C[b]) or (k > 0 and center + k * step > C[b])}
    for k in list(pos): close(k, n - 1, C[n - 1], "regime")
    return trades, dict(grids=grids, grids_with_fill=gfill, stopouts=stops, entry_fills=fills, grid_hours=grid_hours)

def gp_trade(a, t, side, k):
    """Placebo for grid: random regime hour t, limit k*step from close (valid 24 bars), target 1 step, stop at 4-k steps, regime-off flatten."""
    n = a["n"]; O, H, L, C, reg = a["O"], a["H"], a["L"], a["C"], a["reg"]; step = 0.5 * a["atr"][t]
    lim = C[t] - side * k * step; fb = None
    for j in range(t + 1, t + 25):
        if j >= n: return None
        if (side == 1 and L[j] < lim * (1 - TK)) or (side == -1 and H[j] > lim * (1 + TK)): fb = j; break
    if fb is None: return None
    tg = lim + side * step; st = lim - side * (4 - k) * step
    def done(b, px, et): return dict(sym=a["sym"], side=side, fill=fb, entry=lim, exit_bar=b, exit=px, etype=et, gross=side * (px / lim - 1))
    for b in range(fb, fb + 500):
        if b >= n: return None
        if b > fb and not reg[b - 1]: return done(b, O[b], "regime")
        if side == 1 and L[b] <= st: return done(b, min(O[b], st) if b > fb else st, "stop")
        if side == -1 and H[b] >= st: return done(b, max(O[b], st) if b > fb else st, "stop")
        if b > fb and ((side == 1 and H[b] > tg * (1 + TK)) or (side == -1 and L[b] < tg * (1 - TK))): return done(b, tg, "target")
    return done(fb + 500, O[min(fb + 500, n - 1)], "time")

# ---------------------------------------------------------------- stats
def addnet(df):
    for sc in SC: df["net_" + sc] = [net(g, s, e, sc) for g, s, e in zip(df.gross, df.sym, df.etype)]
    df["period"] = [period(k) for k in df.fill]; df["week"] = (T0 + df.fill * H_MS) // WEEK; return df

def boot_ci(df, col, nb=2000, seed=1):
    if len(df) < 5: return (np.nan, np.nan)
    g = df.groupby("week")[col].agg(["sum", "count"]); s, c = g["sum"].values, g["count"].values; k = len(g)
    ix = np.random.default_rng(seed).integers(0, k, (nb, k)); m = s[ix].sum(1) / c[ix].sum(1)
    return tuple(float(x) for x in np.percentile(m, [5, 95]))

def dsr(x, n_trials):
    x = np.asarray(x); n = len(x)
    if n < 10 or x.std() == 0: return dict(sr=np.nan, sr0=np.nan, prob=np.nan, pass_=False)
    sr = x.mean() / x.std(ddof=1); emc = 0.5772156649
    sr0 = np.sqrt(1 / n) * ((1 - emc) * norm.ppf(1 - 1 / n_trials) + emc * norm.ppf(1 - 1 / (n_trials * np.e)))
    z = (sr - sr0) * np.sqrt(n - 1) / np.sqrt(1 - skew(x) * sr + (kurtosis(x, fisher=False) - 1) / 4 * sr ** 2)
    return dict(sr=float(sr), sr0=float(sr0), prob=float(norm.cdf(z)), pass_=bool(sr > sr0))

def block_stats(df, notional, days, pool=None, seeds=200):
    r = {}; r["trades"] = int(len(df))
    if len(df) == 0: return r
    x = df.net_placeholder.values; r["win_rate"] = float((x > 0).mean())
    r["mean_net"] = float(x.mean()); r["median_net"] = float(np.median(x)); r["mean_net_stress"] = float(df.net_stress.mean())
    r["mean_gross"] = float(df.gross.mean()); r["ci90"] = boot_ci(df, "net_placeholder"); r["ci90_stress"] = boot_ci(df, "net_stress")
    pnl = df.sort_values("exit_t").net_placeholder.values * notional; cum = np.cumsum(pnl)
    r["total_usd"] = float(pnl.sum()); r["usd_per_day"] = float(pnl.sum() / days); r["notional_per_trade"] = notional
    r["max_dd_usd"] = float((cum - np.maximum.accumulate(np.concatenate([[0], cum]))[1:]).min()); r["max_dd_pct_of_50"] = r["max_dd_usd"] / CAP
    r["exit_mix"] = df.etype.value_counts(normalize=True).round(3).to_dict()
    r["dsr_2"] = dsr(x, 2); r["dsr_83"] = dsr(x, 83)
    if pool is not None and len(pool) > 0:
        rng = np.random.default_rng(11); m = np.array([pool[rng.integers(0, len(pool), len(df))].mean() for _ in range(seeds)])
        r["placebo_mean"] = float(m.mean()); r["placebo_p90"] = float(np.percentile(m, 90)); r["placebo_pct_of_real"] = float((m < x.mean()).mean() * 100)
        r["placebo_pool_n"] = int(len(pool))
    return r

def adverse(trs, arrs, per_filter):
    """Return after fill at 1/4/12 bars vs same-sym same-side random regime-on bars in the same period."""
    rng = np.random.default_rng(5); out = {}
    for h in (1, 4, 12):
        a_, b_, base = [], [], []
        for t in trs:
            if not per_filter(t["fill"]): continue
            a = arrs[t["sym"]]; f = t["fill"]
            if f + h >= a["n"] or a["C"][f + h] != a["C"][f + h]: continue
            a_.append(t["side"] * (a["C"][f + h] / t["entry"] - 1)); b_.append(t["side"] * (a["C"][f + h] / a["C"][f] - 1))
            cand = a["reg_idx"][(a["reg_idx"] >= (SPLIT_K if period(f) == "HELD" else 0)) & (a["reg_idx"] < (LAST_K if period(f) == "HELD" else SPLIT_K)) & (a["reg_idx"] + h < a["n"])]
            for r in rng.choice(cand, 5):
                if a["C"][r + h] == a["C"][r + h]: base.append(t["side"] * (a["C"][r + h] / a["C"][r] - 1))
        a_, b_, base = map(np.array, (a_, b_, base))
        if len(a_): out[f"h{h}"] = dict(n=int(len(a_)), real_from_fill_px=float(a_.mean()), real_from_fillbar_close=float(b_.mean()),
                                         baseline=float(base.mean()), diff_vs_baseline=float(b_.mean() - base.mean()), se_diff=float(b_.std(ddof=1) / np.sqrt(len(b_))))
    return out

def passrule(r, label):
    if r.get("trades", 0) == 0: return dict(verdict="FAIL (no trades)")
    c = dict(mean_pos_ci_excl0=bool(r["ci90"][0] > 0), stress_mean_ge0=bool(r["mean_net_stress"] >= 0), trades_ge_100=bool(r["trades"] >= 100),
             placebo_gt_p90=bool(r.get("placebo_pct_of_real", 0) > 90), dsr2_pos=bool(r["dsr_2"]["pass_"]))
    c["verdict"] = "PASS" if all(c.values()) else "FAIL"; c["dsr83_pos_(stricter,separate)"] = bool(r["dsr_83"]["pass_"]); return c

# ---------------------------------------------------------------- main
def main():
    arrs = {s: prep(s) for s in sorted(set(MR_SYMS + GRID_SYMS))}
    for a in arrs.values(): a["reg_idx"] = np.flatnonzero(np.array(a["reg"]))
    days = {"DEVELOP": (SPLIT_K) / 24.0, "HELD": (N - SPLIT_K) / 24.0}
    res = {"config": dict(split_k=int(SPLIT_K), fees=SC, tick=TK, develop_only=DEV_ONLY, universe_mr=MR_SYMS, universe_grid=GRID_SYMS)}
    # ---- range_mr
    mr, fr = [], {"DEVELOP": [0, 0, 0], "HELD": [0, 0, 0]}
    for s in MR_SYMS:
        tr, placed, missed, dropped = mr_real(arrs[s]); mr += tr
        a = arrs[s]; pl_t = np.flatnonzero((np.array(a["sl"]) | np.array(a["ss"])))
        # per-period placed/missed approximated by signal-time period
        pass
        res.setdefault("mr_per_sym_orders", {})[s] = dict(placed=placed, missed=missed, dropped=dropped, filled=len(tr))
    # recount fill rate per period exactly (second pass records order time)
    orders = {"DEVELOP": [0, 0], "HELD": [0, 0]}
    for s in MR_SYMS:
        a = arrs[s]; t = 0; n = a["n"]
        while t < n - 30:
            if a["sl"][t] or a["ss"][t]:
                side = 1 if a["sl"][t] else -1; kind, r = mr_trade(a, t, side); p = period(t)
                if kind == "miss": orders[p][0] += 1; t = r; continue
                if kind == "drop": t += 1; continue
                orders[p][0] += 1; orders[p][1] += 1; t = r["exit_bar"]; continue
            t += 1
    mrd = addnet(pd.DataFrame(mr)); mrd["exit_t"] = mrd.exit_bar; mrd["fill_time"] = [iso(k) for k in mrd.fill]
    # placebo pool
    rng = np.random.default_rng(3); pool = {"DEVELOP": [], "HELD": []}; pfill = {"DEVELOP": [0, 0], "HELD": [0, 0]}
    for s in MR_SYMS:
        a = arrs[s]
        for per, lo, hi in (("DEVELOP", 0, SPLIT_K), ("HELD", SPLIT_K, LAST_K)):
            idx = a["reg_idx"][(a["reg_idx"] >= lo) & (a["reg_idx"] < hi - 30) & np.array([a["atr"][i] == a["atr"][i] for i in a["reg_idx"]][: len(a["reg_idx"])])] if len(a["reg_idx"]) else []
            if len(idx) == 0: continue
            idx = rng.choice(idx, min(len(idx), 5000), replace=False)
            for t in idx:
                for side in (1, -1):
                    kind, r = mr_trade(a, int(t), side); pfill[per][0] += 1
                    if kind == "ok":
                        pfill[per][1] += 1; pool[per].append((s, r["gross"], r["etype"]))
    def pool_net(per): return np.array([net(g, s, e, "placeholder") for s, g, e in pool[per]])
    res["range_mr_maker_v1"] = {}
    for per in ("DEVELOP", "HELD"):
        if per == "HELD" and DEV_ONLY: continue
        sub = mrd[mrd.period == per]; r = block_stats(sub, CAP, days[per], pool_net(per))
        r["orders"] = orders[per][0]; r["filled"] = orders[per][1]; r["fill_rate"] = orders[per][1] / max(1, orders[per][0])
        r["placebo_fill_rate"] = pfill[per][1] / max(1, pfill[per][0]); r["pass_rule"] = passrule(r, per) if per == "HELD" else "n/a (develop)"
        by = sub.groupby("sym").net_placeholder.agg(["count", "mean"]).round(5); r["by_symbol"] = {k: dict(n=int(v["count"]), mean=float(v["mean"])) for k, v in by.iterrows()}
        r["by_side"] = {int(k): dict(n=int(v["count"]), mean=float(v["mean"])) for k, v in sub.groupby("side").net_placeholder.agg(["count", "mean"]).iterrows()}
        res["range_mr_maker_v1"][per] = r
        res["range_mr_maker_v1"][per]["adverse"] = adverse([t for t in mr], arrs, (lambda k, p=per: period(k) == p))
    mrd.drop(columns=["exit_t"]).to_csv(HERE + "/trades_range_mr.csv", index=False)
    # ---- grid
    gr = []; gi = {}
    for s in GRID_SYMS:
        tr, info = grid_sim(arrs[s]); gr += tr; gi[s] = info
    grd = addnet(pd.DataFrame(gr)); grd["exit_t"] = grd.exit_bar; grd["fill_time"] = [iso(k) for k in grd.fill]
    gpool = {"DEVELOP": [], "HELD": []}; gpf = {"DEVELOP": [0, 0], "HELD": [0, 0]}; rng = np.random.default_rng(4)
    for s in GRID_SYMS:
        a = arrs[s]
        for per, lo, hi in (("DEVELOP", 0, SPLIT_K), ("HELD", SPLIT_K, LAST_K)):
            if per == "HELD" and DEV_ONLY: continue
            idx = [i for i in a["reg_idx"] if lo <= i < hi - 30 and a["atr"][i] == a["atr"][i]]
            for t in rng.choice(idx, min(len(idx), 4000), replace=False):
                for side in (1, -1):
                    for k in (1, 2, 3):
                        r = gp_trade(a, int(t), side, k); gpf[per][0] += 1
                        if r: gpf[per][1] += 1; gpool[per].append(net(r["gross"], s, r["etype"], "placebo" if False else "placeholder"))
    res["grid_calm_maker_v1"] = {"grid_info": gi}
    for per in ("DEVELOP", "HELD"):
        if per == "HELD" and DEV_ONLY: continue
        sub = grd[grd.period == per]; r = block_stats(sub, CAP / 3, days[per], np.array(gpool[per]))
        r["placebo_fill_rate"] = gpf[per][1] / max(1, gpf[per][0]); r["pass_rule"] = passrule(r, per) if per == "HELD" else "n/a (develop)"
        r["by_symbol"] = {k: dict(n=int(v["count"]), mean=float(v["mean"])) for k, v in sub.groupby("sym").net_placeholder.agg(["count", "mean"]).iterrows()}
        r["by_level"] = {int(k): dict(n=int(v["count"]), mean=float(v["mean"])) for k, v in sub.groupby("level").net_placeholder.agg(["count", "mean"]).iterrows()}
        res["grid_calm_maker_v1"][per] = r
        res["grid_calm_maker_v1"][per]["adverse"] = adverse(gr, arrs, (lambda k, p=per: period(k) == p))
    grd.drop(columns=["exit_t"]).to_csv(HERE + "/trades_grid.csv", index=False)
    json.dump(res, open(HERE + ("/results_develop_only.json" if DEV_ONLY else "/results.json"), "w"), indent=1, default=float)
    for c in ("range_mr_maker_v1", "grid_calm_maker_v1"):
        for per in ("DEVELOP", "HELD"):
            if per in res[c]:
                r = res[c][per]; print(c, per, {k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items() if k in ("trades", "win_rate", "mean_net", "median_net", "mean_net_stress", "ci90", "fill_rate", "placebo_pct_of_real", "total_usd", "usd_per_day", "max_dd_pct_of_50", "exit_mix")})

def iso(k): return dt.datetime.fromtimestamp((T0 + k * H_MS) / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
if __name__ == "__main__": main()

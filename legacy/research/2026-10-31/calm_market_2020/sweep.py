"""Full-factorial DEVELOP-only sweep (PREREG.txt appendices 2-3). HELD-OUT bars never loaded."""
import sys; sys.argv.append("--develop-only")
import json, itertools, time, datetime as dt
import numpy as np, pandas as pd
from scipy.stats import norm, skew, kurtosis
import run as R
from run import *
LEDGER = os.path.expanduser("~/Desktop/PythonProject/edge_vault/search_ledger.jsonl")
t_start = time.time(); days = SPLIT_K / 24.0
arrs = {s: prep(s) for s in sorted(set(MR_SYMS + GRID_SYMS))}
for s, a in arrs.items():
    c = pd.Series(a["C"]); m = c.rolling(20).mean(); sd = c.rolling(20).std(ddof=0)
    a["bk"] = {k: ((m - k * sd).values, (m + k * sd).values) for k in (2.0, 2.5, 3.0)}
    a["bkl"] = {k: ((m - k * sd).tolist(), (m + k * sd).tolist()) for k in (2.0, 2.5, 3.0)}
    d = c.diff(); up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean(); dn = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    a["rsi"] = (100 - 100 / (1 + up / dn)).values; a["cv"] = c.values; a["atrv"] = np.array(a["atr"]); a["regv"] = np.array(a["reg"])

def mr_g(a, t, side, k, tgt, stopk, tmax):
    n = a["n"]; O, H, L, C, S20 = a["O"], a["H"], a["L"], a["C"], a["sma20"]; lim = C[t]; atr0 = a["atr"][t]; fb = None
    for j in range(t + 1, t + 4):
        if j >= n or C[j] != C[j]: return ("drop", j)
        if (side == 1 and L[j] < lim * (1 - TK)) or (side == -1 and H[j] > lim * (1 + TK)): fb = j; break
    if fb is None: return ("miss", t + 3)
    entry = lim; stop = None if stopk is None else entry - side * stopk * atr0
    def done(b, px, et): return ("ok", dict(sym=a["sym"], side=side, fill=fb, entry=entry, exit_bar=b, exit=px, etype=et, gross=side * (px / entry - 1)))
    lo_k, up_k = a["bkl"][k]
    for b in range(fb, fb + tmax):
        if b >= n or C[b] != C[b]: return ("drop", b)
        if stop is not None:
            if side == 1 and L[b] <= stop: return done(b, min(O[b], stop) if b > fb else stop, "stop")
            if side == -1 and H[b] >= stop: return done(b, max(O[b], stop) if b > fb else stop, "stop")
        if b > fb:
            tp = S20[b - 1] if tgt == "sma" else ((up_k[b - 1] if side == 1 else lo_k[b - 1]) if tgt == "band" else entry + side * 1.5 * atr0)
            if tp == tp and ((side == 1 and H[b] > tp * (1 + TK)) or (side == -1 and L[b] < tp * (1 - TK))): return done(b, tp, "target")
    b = fb + tmax
    if b >= n or O[b] != O[b]: return ("drop", b)
    return done(b, O[b], "time")

def mr_variant(k, rsi, tgt, stopk, regon):
    lowr, highr = (25, 75) if rsi == 25 else (30, 70); tmax = 24 if (tgt == "sma" and stopk is not None) else 48; out = []
    for s in MR_SYMS:
        a = arrs[s]; lo, up = a["bk"][k]; ok = ~np.isnan(a["atrv"]) & (a["regv"] if regon else True)
        sl = ok & (a["cv"] < lo) & (a["rsi"] < lowr); ss = ok & (a["cv"] > up) & (a["rsi"] > highr)
        idx = np.flatnonzero(sl | ss); busy = 0
        for t in idx:
            t = int(t)
            if t < busy or t >= SPLIT_K - 60: continue
            kind, r = mr_g(a, t, 1 if sl[t] else -1, k, tgt, stopk, tmax)
            if kind == "miss": busy = r; continue
            if kind == "drop": continue
            out.append(r); busy = r["exit_bar"]
    return out

def grid_g(a, sp, Lv, m, S):
    n = a["n"]; O, H, L, C = a["O"], a["H"], a["L"], a["C"]; reg = a["reg"]; trades = []; sym = a["sym"]
    ks = [k for j in range(1, Lv + 1) for k in (-j, j)]; Sx = S if S else Lv + 1
    active = needoff = flat_next = False; pos = {}; armed = set(); center = step = 0.0
    def close(k, b, px, et):
        eb, ep = pos[k]; side = 1 if k < 0 else -1
        trades.append(dict(sym=sym, side=side, fill=eb, entry=ep, exit_bar=b, exit=px, etype=et, gross=side * (px / ep - 1)))
    for b in range(n):
        if active:
            if flat_next:
                for k in list(pos): close(k, b, O[b], "regime")
                pos = {}; active = False; flat_next = False
            else:
                pr = lambda k: center + k * step
                for k in ks:
                    if k not in pos and k not in armed and ((k < 0 and pr(k) < C[b - 1]) or (k > 0 and pr(k) > C[b - 1])): armed.add(k)
                dn = bool(S) and L[b] <= pr(-S); upx = bool(S) and H[b] >= pr(S); closed = set()
                if not (dn or upx):
                    for k in list(pos):
                        if pos[k][0] < b:
                            tg = pr(k + m) if k < 0 else pr(k - m)
                            if (k < 0 and H[b] > tg * (1 + TK)) or (k > 0 and L[b] < tg * (1 - TK)): close(k, b, tg, "target"); del pos[k]; closed.add(k)
                for k in ks:
                    if k in pos or k in closed or k not in armed: continue
                    p = pr(k)
                    if (k < 0 and L[b] < p * (1 - TK)) or (k > 0 and H[b] > p * (1 + TK)): pos[k] = (b, p); armed.discard(k)
                if dn or upx:
                    use_dn = dn and (not upx or O[b] <= center)
                    px = (pr(-S) if O[b] > pr(-S) else O[b]) if use_dn else (pr(S) if O[b] < pr(S) else O[b])
                    for k in list(pos): close(k, b, px, "stop")
                    pos = {}; active = False; needoff = True
        if active and not reg[b]: flat_next = True
        if not reg[b]: needoff = False
        if (not active) and reg[b] and (not needoff) and a["sma100"][b] == a["sma100"][b] and b < n - 2 and abs(C[b] - a["sma100"][b]) < Sx * sp * a["atr"][b]:
            center = a["sma100"][b]; step = sp * a["atr"][b]; active = True; pos = {}
            armed = {k for k in ks if (k < 0 and center + k * step < C[b]) or (k > 0 and center + k * step > C[b])}
    for k in list(pos): close(k, n - 1, C[n - 1], "regime")
    return trades

def metrics(tr, notional):
    df = addnet(pd.DataFrame(tr)); df["exit_t"] = df.exit_bar; df = df[df.period == "DEVELOP"]
    x = df.net_placeholder.values; r = dict(trades=len(df))
    if len(df) < 10: return r | dict(mean_bp=np.nan, ci_lo=np.nan, ci_hi=np.nan, stress_bp=np.nan, stop_share=np.nan, maxdd_usd=np.nan, sr=np.nan, x=x)
    ci = boot_ci(df, "net_placeholder"); pnl = df.sort_values("exit_t").net_placeholder.values * notional; cum = np.cumsum(pnl)
    return dict(trades=len(df), win=float((x > 0).mean()), mean_bp=x.mean() * 1e4, median_bp=np.median(x) * 1e4, ci_lo=ci[0] * 1e4, ci_hi=ci[1] * 1e4,
                stress_bp=df.net_stress.mean() * 1e4, stop_share=float((df.etype == "stop").mean()), maxdd_usd=float((cum - np.maximum.accumulate(np.concatenate([[0], cum]))[1:]).min()),
                usd_per_day=float(pnl.sum() / days), sr=float(x.mean() / x.std(ddof=1)), x=x)

rows = []
MRG = list(itertools.product([2.0, 2.5, 3.0], [25, 30], ["sma", "band", "atr"], [2, 3, None], [True, False]))
for (k, rsi, tgt, st, rg) in MRG:
    m = metrics(mr_variant(k, rsi, tgt, st, rg), CAP)
    rows.append(dict(family="range_mr", variant_id=f"mr_k{k}_rsi{rsi}_{tgt}_stop{st or 'none'}_reg{'on' if rg else 'off'}", params=dict(k=k, rsi=rsi, target=tgt, stop_atr=st, regime=rg),
                     axes=(k, rsi, ["sma", "band", "atr"].index(tgt), [2, 3, None].index(st), int(rg)), **m))
print("mr done", round(time.time() - t_start))
GRD = list(itertools.product([0.5, 1.0], [3, 5], [1, 2, 3], [4, 6, None]))
for (sp, Lv, mm, S) in GRD:
    tr = []
    for s in GRID_SYMS: tr += grid_g(arrs[s], sp, Lv, mm, S)
    m = metrics(tr, CAP / 3)
    rows.append(dict(family="grid", variant_id=f"grid_sp{sp}_L{Lv}_m{mm}_stop{S or 'none'}", params=dict(spacing_atr=sp, levels=Lv, close_levels=mm, stop_levels=S),
                     axes=(sp, Lv, mm, [4, 6, None].index(S)), **m))
print("grid done", round(time.time() - t_start))

# ---- deflated Sharpe with ledger-based N
def ledger_lines():
    try: return sum(1 for _ in open(LEDGER))
    except FileNotFoundError: return 0
orig = json.load(open(HERE + "/results.json")); tpv = json.load(open(HERE + "/results_tp_variants_develop.json"))
n_new = 4 + len(tpv) + len(rows)
Ntr = ledger_lines() + n_new + 81
srs = np.array([r["sr"] for r in rows if r["sr"] == r["sr"]]); sr_var = float(srs.var(ddof=1)); emc = 0.5772156649
sr0 = np.sqrt(sr_var) * ((1 - emc) * norm.ppf(1 - 1 / Ntr) + emc * norm.ppf(1 - 1 / (Ntr * np.e)))
for r in rows:
    x = r["x"]
    if len(x) >= 10 and x.std() > 0:
        sr = r["sr"]; z = (sr - sr0) * np.sqrt(len(x) - 1) / np.sqrt(1 - skew(x) * sr + (kurtosis(x, fisher=False) - 1) / 4 * sr ** 2)
        r["dsr_prob"] = float(norm.cdf(z)); r["dsr_pos"] = bool(sr > sr0)
    else: r["dsr_prob"] = np.nan; r["dsr_pos"] = False
# ---- neighbours (one step along any axis, ordered axes; regime/target as stated)
byax = {(r["family"], r["axes"]): r for r in rows}
def nbrs(r):
    ax = list(r["axes"]); out = []
    domain = [sorted({a[i] for (f, a) in byax if f == r["family"]}) for i in range(len(ax))]
    for i in range(len(ax)):
        d = domain[i]; j = d.index(ax[i])
        for jj in (j - 1, j + 1):
            if 0 <= jj < len(d): out.append(byax[(r["family"], tuple(ax[:i] + [d[jj]] + ax[i + 1:]))])
    return out
for r in rows:
    nb = nbrs(r); r["n_nbrs"] = len(nb); r["nbrs_pos"] = int(sum(1 for q in nb if q["mean_bp"] > 0)); r["all_nbrs_pos"] = r["nbrs_pos"] == len(nb)
    r["candidate_rules"] = bool(r["dsr_pos"] and r["ci_lo"] > 0 and r["stress_bp"] >= 0 and r["trades"] >= 200 and r["all_nbrs_pos"]) if r["trades"] >= 10 else False
cols = ["family", "variant_id", "trades", "mean_bp", "ci_lo", "ci_hi", "stress_bp", "stop_share", "maxdd_usd", "sr", "dsr_prob", "dsr_pos", "nbrs_pos", "n_nbrs", "candidate_rules"]
pd.DataFrame(rows)[cols].round(4).to_csv(HERE + "/sweep.csv", index=False)
# ---- ledger (append-only)
now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"); L_ = []
def led(family, vid, params, period, trades, mean_bp, ci, verdict): L_.append(dict(ts=now, family=family, variant_id=vid, params=params, period=period, trades=int(trades),
        mean_net_bp=None if mean_bp != mean_bp else round(float(mean_bp), 2), ci90=[None if c != c else round(float(c), 2) for c in ci], verdict=verdict))
for fam, vid, p in (("range_mr", "range_mr_maker_v1", dict(k=2.0, rsi=30, target="sma20", stop_atr=2, regime=True)), ("grid", "grid_calm_maker_v1", dict(spacing_atr=0.5, levels=3, close_levels=1, stop_levels=4))):
    for per in ("DEVELOP", "HELD"):
        d = orig[{"range": "range_mr_maker_v1", "range_mr": "range_mr_maker_v1", "grid": "grid_calm_maker_v1"}[fam]][per]
        led(fam, vid, p, "DEVELOP" if per == "DEVELOP" else "HELD-OUT", d["trades"], d["mean_net"] * 1e4, [c * 1e4 for c in d["ci90"]], "FAIL(dead)" if per == "HELD" else "develop (fails)")
for v, d in tpv.items():
    led("range_mr" if v.startswith("range") else "grid", v, {"tp_variant": v}, "DEVELOP", d["trades"], d["mean_net"] * 1e4, [c * 1e4 for c in d["ci90"]], "not eligible")
for r in rows:
    led(r["family"], r["variant_id"], r["params"], "DEVELOP", r["trades"], r["mean_bp"], [r["ci_lo"], r["ci_hi"]], "CANDIDATE(rules)" if r["candidate_rules"] else "not eligible")
with open(LEDGER, "a") as f:
    for l in L_: f.write(json.dumps(l) + "\n")
print("ledger lines appended", len(L_), "N used", Ntr, "sr_var", round(sr_var, 5), "sr0", round(sr0, 4))
df = pd.DataFrame(rows)
print("positive mean:", int((df.mean_bp > 0).sum()), "of", len(df), "| CI lo>0:", int((df.ci_lo > 0).sum()), "| dsr_pos:", int(df.dsr_pos.sum()), "| candidates:", int(df.candidate_rules.sum()))
top = df.sort_values("dsr_prob", ascending=False).head(8)
print(top[["variant_id", "trades", "mean_bp", "ci_lo", "stress_bp", "stop_share", "dsr_prob", "nbrs_pos", "n_nbrs", "candidate_rules"]].round(3).to_string(index=False))
print("best mean_bp:", df.sort_values("mean_bp", ascending=False).head(5)[["variant_id", "trades", "mean_bp", "ci_lo", "dsr_prob"]].round(3).to_string(index=False))
print("elapsed", round(time.time() - t_start))

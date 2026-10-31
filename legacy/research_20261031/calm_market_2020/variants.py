"""TP variants, DEVELOP ONLY (see PREREG.txt appendix). HELD-OUT bars are never loaded (run.py develop-only mode)."""
import sys; sys.argv.append("--develop-only")
import json, numpy as np, pandas as pd
import run as R
from run import *

def bands(a):
    c = pd.Series(a["C"]); s = c.rolling(20).mean(); sd = c.rolling(20).std(ddof=0)
    a["up"] = (s + 2 * sd).tolist(); a["lo"] = (s - 2 * sd).tolist()

def mr2(a, t, side, mode):
    n = a["n"]; O, H, L, C = a["O"], a["H"], a["L"], a["C"]; lim = C[t]; atr0 = a["atr"][t]; fb = None
    for j in range(t + 1, t + 4):
        if j >= n or C[j] != C[j]: return ("drop", j)
        if (side == 1 and L[j] < lim * (1 - TK)) or (side == -1 and H[j] > lim * (1 + TK)): fb = j; break
    if fb is None: return ("miss", t + 3)
    entry = lim; stop = entry - side * 2 * atr0
    def done(b, px, et): return ("ok", dict(sym=a["sym"], side=side, t_sig=t, fill=fb, entry=entry, exit_bar=b, exit=px, etype=et, gross=side * (px / entry - 1)))
    for b in range(fb, fb + 48):
        if b >= n or C[b] != C[b]: return ("drop", b)
        if side == 1 and L[b] <= stop: return done(b, min(O[b], stop) if b > fb else stop, "stop")
        if side == -1 and H[b] >= stop: return done(b, max(O[b], stop) if b > fb else stop, "stop")
        if b > fb:
            tp = (a["up"][b - 1] if side == 1 else a["lo"][b - 1]) if mode == "band" else entry + side * 1.5 * atr0
            if tp == tp and ((side == 1 and H[b] > tp * (1 + TK)) or (side == -1 and L[b] < tp * (1 - TK))): return done(b, tp, "target")
    b = fb + 48
    if b >= n or O[b] != O[b]: return ("drop", b)
    return done(b, O[b], "time")

def mr_run(a, mode):
    out = []; t = 0; n = a["n"]
    while t < n - 60:
        if a["sl"][t] or a["ss"][t]:
            kind, r = mr2(a, t, 1 if a["sl"][t] else -1, mode)
            if kind == "miss": t = r; continue
            if kind == "drop": t += 1; continue
            out.append(r); t = r["exit_bar"]; continue
        t += 1
    return out

def grid2(a, m):
    n = a["n"]; O, H, L, C = a["O"], a["H"], a["L"], a["C"]; reg = a["reg"]; trades = []; sym = a["sym"]
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
                for k in (-1, -2, -3, 1, 2, 3):
                    if k not in pos and k not in armed and ((k < 0 and pr(k) < C[b - 1]) or (k > 0 and pr(k) > C[b - 1])): armed.add(k)
                dn = L[b] <= pr(-4); upx = H[b] >= pr(4); closed = set()
                if not (dn or upx):
                    for k in list(pos):
                        if pos[k][0] < b:
                            tg = pr(k + m) if k < 0 else pr(k - m)
                            if (k < 0 and H[b] > tg * (1 + TK)) or (k > 0 and L[b] < tg * (1 - TK)):
                                close(k, b, tg, "target"); del pos[k]; closed.add(k)
                for k in (-1, -2, -3, 1, 2, 3):
                    if k in pos or k in closed or k not in armed: continue
                    p = pr(k)
                    if (k < 0 and L[b] < p * (1 - TK)) or (k > 0 and H[b] > p * (1 + TK)): pos[k] = (b, p); armed.discard(k)
                if dn or upx:
                    use_dn = dn and (not upx or O[b] <= center)
                    px = (pr(-4) if O[b] > pr(-4) else O[b]) if use_dn else (pr(4) if O[b] < pr(4) else O[b])
                    for k in list(pos): close(k, b, px, "stop")
                    pos = {}; active = False; needoff = True
        if active and not reg[b]: flat_next = True
        if not reg[b]: needoff = False
        if (not active) and reg[b] and (not needoff) and a["sma100"][b] == a["sma100"][b] and b < n - 2 and abs(C[b] - a["sma100"][b]) < 2 * a["atr"][b]:
            center = a["sma100"][b]; step = 0.5 * a["atr"][b]; active = True; pos = {}
            armed = {k for k in (-1, -2, -3, 1, 2, 3) if (k < 0 and center + k * step < C[b]) or (k > 0 and center + k * step > C[b])}
    for k in list(pos): close(k, n - 1, C[n - 1], "regime")
    return trades

def gp2(a, t, side, k, m):
    n = a["n"]; O, H, L, C, reg = a["O"], a["H"], a["L"], a["C"], a["reg"]; step = 0.5 * a["atr"][t]
    lim = C[t] - side * k * step; fb = None
    for j in range(t + 1, t + 25):
        if j >= n: return None
        if (side == 1 and L[j] < lim * (1 - TK)) or (side == -1 and H[j] > lim * (1 + TK)): fb = j; break
    if fb is None: return None
    tg = lim + side * m * step; st = lim - side * (4 - k) * step
    def done(b, px, et): return dict(sym=a["sym"], fill=fb, exit_bar=b, etype=et, gross=side * (px / lim - 1))
    for b in range(fb, fb + 500):
        if b >= n: return None
        if b > fb and not reg[b - 1]: return done(b, O[b], "regime")
        if side == 1 and L[b] <= st: return done(b, min(O[b], st) if b > fb else st, "stop")
        if side == -1 and H[b] >= st: return done(b, max(O[b], st) if b > fb else st, "stop")
        if b > fb and ((side == 1 and H[b] > tg * (1 + TK)) or (side == -1 and L[b] < tg * (1 - TK))): return done(b, tg, "target")
    return None

arrs = {s: prep(s) for s in sorted(set(MR_SYMS + GRID_SYMS))}
for a in arrs.values(): a["reg_idx"] = np.flatnonzero(np.array(a["reg"])); bands(a)
days = SPLIT_K / 24.0; out = {}
def finish(name, tr, notional, poolv):
    df = addnet(pd.DataFrame(tr)); df["exit_t"] = df.exit_bar; df = df[df.period == "DEVELOP"]
    r = block_stats(df, notional, days, np.array(poolv))
    r["stopout_share"] = float((df.etype == "stop").mean())
    r["eligible"] = bool(r["mean_net"] > 0 and r["ci90"][0] > 0 and r["mean_net_stress"] >= 0)
    out[name] = {k: r[k] for k in ("trades", "win_rate", "mean_net", "median_net", "mean_net_stress", "ci90", "stopout_share", "placebo_pct_of_real", "placebo_mean", "total_usd", "usd_per_day", "eligible", "exit_mix")}
    print(name, {k: (round(v, 5) if isinstance(v, float) else v) for k, v in out[name].items()})
for name, mode in (("range_mr_tp_band", "band"), ("range_mr_tp_1p5atr", "atr")):
    tr = []; pool = []; rng = np.random.default_rng(3)
    for s in MR_SYMS:
        a = arrs[s]; tr += mr_run(a, mode)
        idx = [i for i in a["reg_idx"] if i < SPLIT_K - 60 and a["atr"][i] == a["atr"][i]]
        for t in rng.choice(idx, min(len(idx), 5000), replace=False):
            for side in (1, -1):
                kind, r = mr2(a, int(t), side, mode)
                if kind == "ok": pool.append(net(r["gross"], s, r["etype"], "placeholder"))
    finish(name, tr, CAP, pool)
for name, m in (("grid_tp2", 2), ("grid_tp3", 3)):
    tr = []; pool = []; rng = np.random.default_rng(4)
    for s in GRID_SYMS:
        a = arrs[s]; tr += grid2(a, m)
        idx = [i for i in a["reg_idx"] if i < SPLIT_K - 60 and a["atr"][i] == a["atr"][i]]
        for t in rng.choice(idx, min(len(idx), 4000), replace=False):
            for side in (1, -1):
                for k in (1, 2, 3):
                    r = gp2(a, int(t), side, k, m)
                    if r: pool.append(net(r["gross"], s, r["etype"], "placeholder"))
    finish(name, tr, CAP / 3, pool)
json.dump(out, open("results_tp_variants_develop.json", "w"), indent=1)

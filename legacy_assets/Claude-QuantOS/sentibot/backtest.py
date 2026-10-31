"""Hourly backtest of the technical part of the bot. Signal at bar t close, position held over bar t+1 (no look-ahead).

Costs: fee + slippage per side on traded notional. Portfolio = equal thirds of BTC, ETH, SOL, each run independently.
Usage: python3 backtest.py [--fee 0.001] [--slip 0.0005] [--no-fng]
"""
import argparse
import numpy as np
import pandas as pd
import data, signals, leverage as L
from strategy import composite, positions, DECIDE_EVERY

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
PERIODS = {"in-sample 2021-2023": ("2021-03-01", "2023-12-31 23:00"), "out-of-sample 2024-now": ("2024-01-01", None)}
VARIANTS = {
    "momentum only": {"momentum": 1},
    "pattern only": {"pattern": 1},
    "momentum+pattern": {"momentum": 0.35, "pattern": 0.25},
    "full technical (+fear&greed)": {"momentum": 0.35, "pattern": 0.25, "fear_greed": 0.10},
}


def sim(df, pos, cost):
    """Hourly strategy returns net of costs, given target weights decided at each bar's close."""
    ret = df["close"].pct_change().fillna(0)
    w = pos.shift(1).fillna(0)  # decided at t-1 close, earns bar t's return
    return w * ret - w.diff().abs().fillna(0) * cost, (w.diff().abs() > 0).sum()


LEV_VARIANTS = {  # all on the full technical composite; leverage rule in leverage.py
    "perp 3-20x long+short": {"shorts": True, "fixed": None},
    "perp 3-20x long only": {"shorts": False, "fixed": None},
    "perp fixed 3x long+short": {"shorts": True, "fixed": 3},
    "perp fixed 3x long only": {"shorts": False, "fixed": 3},
}


def sim_lev(df, comp, size, vol, every, shorts, fixed=None):
    """Bar-by-bar perp sim on one sleeve (equity starts at 1). Returns hourly returns and event counts."""
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    hours, comp, size, vol = df.index.hour, comp.values, size.values, vol.values
    eq, p, curve = 1.0, None, []
    ev = {"trades": 0, "stops": 0, "liquidated": 0, "lev_sum": 0}
    for i in range(len(c)):
        if p:  # position opened at an earlier close lives through bar i
            hit = L.check_bar(p, o[i], h[i], l[i])
            if hit:
                eq += hit[0]
                ev["stops" if hit[2] == "stop" else "liquidated"] += 1
                p = None
            elif hours[i] % 8 == 0:
                eq -= L.funding(p, o[i])
        if hours[i] % every == 0 and eq > 1e-6:
            side = p["side"] if p else 0
            new = L.want_side(comp[i], side, shorts)
            if p and new != side:
                eq += L.close_pos(p, c[i])[0]
                p, side = None, 0
                new = L.want_side(comp[i], 0, shorts)
            if not p and new and size[i] > 0:
                lev = fixed or L.choose_leverage(comp[i], vol[i])
                p, fee = L.open_pos(new, c[i], eq * size[i], lev)
                eq -= fee
                ev["trades"] += 1
                ev["lev_sum"] += lev
        curve.append(eq + (L.upnl(p, c[i]) if p else 0.0))
    curve = pd.Series(curve, index=df.index)
    return curve.pct_change().fillna(0), ev


def stats(r):
    eq = (1 + r).cumprod()
    yrs = len(r) / signals.HOURS_PER_YEAR
    total = eq.iloc[-1] - 1
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    sharpe = r.mean() / r.std() * np.sqrt(signals.HOURS_PER_YEAR) if r.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return total, cagr, sharpe, dd, 200 * cagr / 365


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slip", type=float, default=0.0005)
    ap.add_argument("--no-fng", action="store_true")
    ap.add_argument("--every", type=int, default=DECIDE_EVERY, help="hours between decisions")
    ap.add_argument("--resize", action="store_true", help="re-size the position while held (more trades)")
    a = ap.parse_args()
    cost = a.fee + a.slip
    fng = None if a.no_fng else data.fear_greed()
    print(f"Fear & Greed history: {'loaded, %d days' % len(fng) if fng is not None else 'unavailable (scored as 0)'}")
    frames = {s: data.klines(s) for s in SYMBOLS}
    rets = {name: [] for name in list(VARIANTS) + ["hold"]}
    trades = {name: 0 for name in rets}
    levents, lrets = {}, {}
    for s, df in frames.items():
        scores = {"momentum": signals.momentum(df), "pattern": signals.pattern(df),
                  "fear_greed": signals.fear_greed_signal(fng, df.index)}
        size = signals.vol_size(df)
        for name, w in VARIANTS.items():
            r, n = sim(df, positions(composite(scores, w), size, a.every, a.resize), cost)
            rets[name].append(r)
            trades[name] += n
        rets["hold"].append(df["close"].pct_change().fillna(0))
        full = composite(scores, VARIANTS["full technical (+fear&greed)"])
        vol = signals.realised_vol(df)
        for name, v in LEV_VARIANTS.items():  # path-dependent (stops, ruin): each period starts a fresh sleeve
            for pname, (start, end) in PERIODS.items():
                sl = slice(start, end)
                r, ev = sim_lev(df.loc[sl], full.loc[sl], size.loc[sl], vol.loc[sl], a.every, v["shorts"], v["fixed"])
                lrets.setdefault((pname, name), []).append(r)
                levents.setdefault(name, {}).setdefault(pname[:3], {})[s] = ev
    lines = [f"Costs {cost*100:.2f}% per side. Portfolio = 1/3 each of {', '.join(SYMBOLS)}. Perps: {(L.FEE+L.SLIP)*100:.2f}% per side + funding. $/day at $200 from CAGR.\n"]
    for pname, (start, end) in PERIODS.items():
        lines.append(f"\n## {pname}\n| Variant | Total | CAGR | Sharpe | MaxDD | $/day at $200 |\n|---|---|---|---|---|---|")
        for name, rs in rets.items():
            port = pd.concat(rs, axis=1).loc[start:end].mean(axis=1)
            t, c, sh, dd, pd_ = stats(port)
            lines.append(f"| {name} | {t:+.1%} | {c:+.1%} | {sh:.2f} | {dd:.1%} | ${pd_:.2f} |")
        for name in LEV_VARIANTS:
            port = pd.concat(lrets[(pname, name)], axis=1).mean(axis=1)
            t, c, sh, dd, pd_ = stats(port)
            lines.append(f"| {name} | {t:+.1%} | {c:+.1%} | {sh:.2f} | {dd:.1%} | ${pd_:.2f} |")
    lines.append("\nPerp variants (full technical composite): trades / stops / liquidations / avg leverage per coin")
    for name, per in levents.items():
        for pn, by in per.items():
            lines.append(f"- {name} [{pn}]: " + "; ".join(
                f"{s[:3]} {e['trades']}/{e['stops']}/{e['liquidated']}/{e['lev_sum']/max(1,e['trades']):.1f}x" for s, e in by.items()))
    lines.append(f"\nPosition changes over the whole history (all 3 coins): " + ", ".join(f"{k}: {v}" for k, v in trades.items() if k in VARIANTS))
    out = "\n".join(lines)
    print(out)
    return out


if __name__ == "__main__":
    main()

"""Walk-forward backtest with fees. In-sample 2020-2023, out-of-sample 2024-01-01 onward.

Signal on day t's close, trade at day t+1's open (no look-ahead). Cost per side = fee + slippage on traded notional.
Usage: python3 backtest.py [--fee 0.001] [--slip 0.0005] [--capital 200]
"""
import argparse, math
from datetime import date
from data import fetch_daily
from strategies import UNIVERSE, STRATEGIES, BENCHMARKS, WEEKLY

OOS_START = "2024-01-01"


def load():
    bars = {s: {r[0]: r for r in fetch_daily(s)} for s in UNIVERSE}
    dates = sorted(set().union(*[b.keys() for b in bars.values()]))
    dates = [d for d in dates if d >= "2019-06-01" and d in bars["BTCUSDT"]]
    return bars, dates


def run(fn, bars, dates, fee, slip, start, end, weekly=False, min_order=5.0, capital=200.0):
    """Holdings tracked in dollars. Returns equity curve (as multiple of start capital), turnover, trade count."""
    cost = fee + slip
    closes = {s: [] for s in UNIVERSE}
    hold, cash = {}, capital
    target, curve, turnover, trades = {}, [], 0.0, 0
    for i, d in enumerate(dates):
        active = start <= d <= end and i > 0
        if active:
            # yesterday close -> today open
            for s in list(hold):
                b = bars[s].get(d)
                if b and closes[s]:
                    hold[s] *= b[1] / closes[s][-1]
            eq = cash + sum(hold.values())
            # rebalance at the open to the target set at yesterday's close
            for s in set(hold) | set(target):
                if not bars[s].get(d):
                    continue
                want = target.get(s, 0.0) * eq
                dv = want - hold.get(s, 0.0)
                if abs(dv) < min_order and want > 0:
                    continue  # too small to trade on Binance at this capital
                if abs(dv) < 1e-9:
                    continue
                fee_paid = abs(dv) * cost
                hold[s] = hold.get(s, 0.0) + dv
                cash -= dv + fee_paid
                turnover += abs(dv) / eq
                trades += 1
                if hold[s] <= 1e-9:
                    del hold[s]
            # today open -> close
            for s in hold:
                b = bars[s].get(d)
                if b:
                    hold[s] *= b[4] / b[1]
            curve.append((d, (cash + sum(hold.values())) / capital))
        for s in UNIVERSE:
            b = bars[s].get(d)
            if b:
                closes[s].append(b[4])
        if not weekly or date.fromisoformat(d).weekday() == 6:  # Sunday close -> trade Monday open
            target = fn(closes)
    return curve, turnover, trades


def stats(curve, turnover, trades, capital):
    if len(curve) < 2:
        return {}
    rets = [curve[i][1] / curve[i - 1][1] - 1 for i in range(1, len(curve))]
    n = len(rets)
    mu = sum(rets) / n
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / n) or 1e-12
    peak, mdd = 0, 0
    for _, e in curve:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    years = n / 365
    total = curve[-1][1] / curve[0][1] - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else 0
    return {"days": n, "total": total, "cagr": cagr, "sharpe": mu / sd * math.sqrt(365), "maxdd": mdd,
            "turnover_per_yr": turnover / years, "trades": trades,
            "usd_per_day_at_capital": capital * ((1 + cagr) ** (1 / 365) - 1)}


def fmt(name, s):
    if not s:
        return f"{name:16s} (no data)"
    return (f"{name:16s} total {s['total']*100:7.1f}%  CAGR {s['cagr']*100:6.1f}%  Sharpe {s['sharpe']:5.2f}  "
            f"MaxDD {s['maxdd']*100:6.1f}%  turnover/yr {s['turnover_per_yr']:5.1f}x  trades {s['trades']:4d}  "
            f"~${s['usd_per_day_at_capital']:.2f}/day")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slip", type=float, default=0.0005)
    ap.add_argument("--capital", type=float, default=200)
    a = ap.parse_args()
    bars, dates = load()
    periods = [("IN-SAMPLE 2020-2023", "2020-01-01", "2023-12-31"), ("OUT-OF-SAMPLE 2024-now", OOS_START, "9999")]
    allfns = {**STRATEGIES, **BENCHMARKS}
    for label, st, en in periods:
        print(f"\n== {label}  (fee {a.fee*100:.2f}% + slip {a.slip*100:.2f}% per side, ${a.capital:.0f}) ==")
        for name, fn in allfns.items():
            c, t, n = run(fn, bars, dates, a.fee, a.slip, st, en, weekly=name in WEEKLY, capital=a.capital)
            print(fmt(name, stats(c, t, n, a.capital)))
    print("\nSensitivity (OOS, not used to pick params): SMA length for A and B")
    for n in (50, 100, 150, 200):
        for name, fn in (("A", STRATEGIES["A_btc_trend"]), ("B", STRATEGIES["B_basket_trend"])):
            c, t, k = run(lambda cl, f=fn: f(cl, n), bars, dates, a.fee, a.slip, OOS_START, "9999", capital=a.capital)
            print(fmt(f"{name} SMA{n}", stats(c, t, k, a.capital)))


if __name__ == "__main__":
    main()

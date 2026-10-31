"""Account-path statistics; no return threshold is used to select trades."""
from __future__ import annotations

import math
import random

DAY_MS = 86_400_000
YEAR_MS = 365.25 * DAY_MS


def max_drawdown(equities):
    peak = 0.0
    worst = 0.0
    for value in equities:
        if not math.isfinite(value): raise ValueError("non-finite equity")
        peak = max(peak, value)
        if peak > 0: worst = max(worst, 1 - value / peak)
    return worst


def summarize(points: list[tuple[int, float]], trades: list[dict]) -> dict:
    """Points include initial/final equity and every UTC daily boundary.

    Intraday observations enter drawdown; fixed-calendar rolling returns use
    daily boundary values. Trade net_pnl must include both fees and funding.
    """
    if len(points) < 2 or points[0][1] <= 0:
        raise ValueError("missing initial/final account path")
    if any(a[0] >= b[0] for a, b in zip(points, points[1:])):
        raise ValueError("equity timestamps must strictly increase")
    if any(not math.isfinite(v) for _, v in points): raise ValueError("invalid equity")
    start, initial = points[0]
    end, final = points[-1]
    duration = end - start
    if duration <= 0: raise ValueError("invalid duration")
    by_time = dict(points)
    boundaries = list(range(((start + DAY_MS - 1) // DAY_MS) * DAY_MS,
                            end + 1, DAY_MS))
    if any(t not in by_time for t in boundaries):
        raise ValueError("missing daily equity boundary; do not interpolate gaps")
    daily = [by_time[t] for t in boundaries]
    windows = {n: [daily[i] / daily[i-n] - 1 for i in range(n, len(daily))
                   if daily[i-n] > 0] for n in (3, 30)}
    pnls = [float(t["net_pnl"]) for t in trades]
    if any(not math.isfinite(x) for x in pnls): raise ValueError("invalid trade PnL")
    gross_gain = sum(max(p, 0) for p in pnls)
    gross_loss = -sum(min(p, 0) for p in pnls)
    # Daily account PnL includes MTM and funding rather than attributing an
    # entire multiday trade to its exit day.
    daily_pnl = [b-a for a, b in zip(daily, daily[1:])]
    net = final - initial
    top5 = sum(sorted(daily_pnl, reverse=True)[:5])
    stops = [t for t in trades if t.get("exit_reason") == "CATASTROPHE_STOP"]
    return {
        "initial_equity": initial, "final_equity": final,
        "net_return": final / initial - 1,
        "cagr": ((final / initial) ** (YEAR_MS / duration) - 1 if final > 0 else -1.0),
        "max_drawdown": max_drawdown(v for _, v in points),
        "worst_3day_return": min(windows[3], default=None),
        "worst_30day_return": min(windows[30], default=None),
        "positive_3day_window_fraction": (sum(v > 0 for v in windows[3]) / len(windows[3])
                                           if windows[3] else None),
        "closed_trades": len(trades), "trades_per_week": len(trades) * 7 * DAY_MS / duration,
        "profit_factor": gross_gain / gross_loss if gross_loss else None,
        "profit_factor_status": "FINITE" if gross_loss else "NO_LOSS_SAMPLE",
        "top5_days_pnl": top5, "top5_days_share_of_net_pnl": top5 / net if net > 0 else None,
        "top5_share_status": "DEFINED" if net > 0 else "NONPOSITIVE_NET_PNL",
        "catastrophe_stop_count": len(stops),
        "catastrophe_stop_frequency": len(stops) / len(trades) if trades else None,
        "catastrophe_stop_net_pnl": sum(float(t["net_pnl"]) for t in stops),
        "catastrophe_stop_fees": sum(float(t.get("fees", 0)) for t in stops),
    }


def kelly_fraction(net_r: list[float]) -> dict:
    """Training-only empirical log-growth optimum in units of initial stop risk.

    It ignores simultaneous positions and parameter uncertainty; callers must
    still use portfolio caps and at most half the estimate, never leverage it
    into a guarantee of positive future growth.
    """
    if not net_r or any(not math.isfinite(r) for r in net_r):
        return {"fraction": None, "half_fraction": None, "status": "NO_VALID_SAMPLE"}
    if sum(net_r) <= 0:
        return {"fraction": 0.0, "half_fraction": 0.0, "status": "NONPOSITIVE_MEAN"}
    negative = [r for r in net_r if r < 0]
    if not negative:
        return {"fraction": None, "half_fraction": None, "status": "NO_LOSS_SAMPLE"}
    lo, hi = 0.0, min(1.0, .999999 / -min(negative))
    def derivative(f): return sum(r / (1 + f*r) for r in net_r)
    if derivative(hi) >= 0:
        best = hi
    else:
        for _ in range(70):
            mid = (lo + hi) / 2
            if derivative(mid) > 0: lo = mid
            else: hi = mid
        best = (lo + hi) / 2
    return {"fraction": best, "half_fraction": best / 2,
            "status": "EMPIRICAL_TRAINING_ONLY"}


def drawdown_monte_carlo(trade_returns: list[float], daily_returns: list[float], *,
                        draws: int = 1000, block_days: int = 7, seed: int = 28102026) -> dict:
    """Conditional path stress, not an unconditional probability of future ruin.

    Trade reshuffling retains each net trade return; daily circular block
    bootstrap retains within-block daily dependence and cross-asset netting.
    Intraday gaps/liquidation are not invented from the resampled paths.
    """
    if draws < 1 or block_days < 1: raise ValueError("invalid Monte Carlo size")
    if any(not math.isfinite(r) for r in trade_returns + daily_returns):
        raise ValueError("invalid returns")
    rng = random.Random(seed)
    def hit(values):
        equity = peak = 1.0
        for r in values:
            equity *= max(0.0, 1+r)
            peak = max(peak, equity)
            if equity <= .5 * peak: return True
        return False
    def probability(kind):
        values = trade_returns if kind == "trade" else daily_returns
        if not values: return None
        hits = 0
        for _ in range(draws):
            if kind == "trade":
                sample = values.copy(); rng.shuffle(sample)
            else:
                sample = []
                while len(sample) < len(values):
                    j = rng.randrange(len(values))
                    sample.extend(values[(j+k) % len(values)] for k in range(block_days))
                sample = sample[:len(values)]
            hits += hit(sample)
        return hits / draws
    return {"draws": draws, "seed": seed, "block_days": block_days,
            "trade_reshuffle_p_dd50": probability("trade"),
            "daily_block_bootstrap_p_dd50": probability("day"),
            "interpretation": "conditional historical resampling; not future ruin probability"}

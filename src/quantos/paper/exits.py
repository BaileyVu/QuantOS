"""Offline TP/SL study on journaled post-entry paths. Never changes a running strategy by itself.

1R is each trade's original entry-to-stop distance. A policy rescales the stop (sl_mult), sets the target
in R, can move the stop to breakeven and trail it. choose_exit() only recommends a switch when the new
policy beats the current one on the newer, unseen half of the trades (paired t > 2, >= 60 trades).
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ExitPolicy:
    sl_mult: float = 1.0          # stop distance as a multiple of the strategy's own stop
    tp_r: float = 2.0             # target in R (inf = none)
    be_at_r: float = math.inf     # move stop to entry after +be_at_r
    trail_r: float = math.inf     # after +1R trail the stop trail_r behind the best price
    max_minutes: int = 240


def exit_r(path, entry: float, stop: float, side: int, p: ExitPolicy, cost_r: float = 0.06) -> float:
    """Result in R of one trade path [[minute, high, low, close], ...]; stop wins a same-bar tie."""
    risk = abs(entry - stop) * p.sl_mult
    if risk <= 0 or not path:
        return 0.0
    level, best = entry - side * risk, entry
    for _, h, l, c in path[:p.max_minutes]:
        h, l = float(h), float(l)
        if ((l if side > 0 else h) - level) * side <= 0:
            return (level - entry) * side / risk - cost_r
        if ((h if side > 0 else l) - entry) * side >= p.tp_r * risk:
            return p.tp_r - cost_r
        best = max(best, h) if side > 0 else min(best, l)
        gain = (best - entry) * side / risk
        if gain >= p.be_at_r and (entry - level) * side > 0:
            level = entry
        if gain >= 1.0 and p.trail_r < math.inf:
            trail = best - side * p.trail_r * risk
            level = max(level, trail) if side > 0 else min(level, trail)
    last = float(path[min(p.max_minutes, len(path)) - 1][3])
    return (last - entry) * side / risk - cost_r


def grid():
    out = []
    for sl in (0.75, 1.0, 1.5):
        for tp in (1.0, 1.5, 2.0, 3.0, math.inf):
            for be in (math.inf, 1.0):
                for tr in ((math.inf, 1.0) if tp == math.inf else (math.inf,)):
                    out.append(ExitPolicy(sl, tp, be, tr))
    return out


def trades_from_orders(orders):
    """(path, entry, stop, side) for closed orders that recorded a path, oldest first."""
    rows = sorted((o for o in orders if o.get("state") == "CLOSED" and o.get("path")), key=lambda o: o["closed_ms"])
    return [(o["path"], float(o["average"]) if float(o.get("average", 0)) else float(o["signal"]["entry_price"]),
             float(o["signal"]["stop_loss"]), 1 if o["signal"]["side"] == "LONG" else -1) for o in rows]


def choose_exit(trades, current: ExitPolicy = ExitPolicy(), min_trades=60, margin_r=0.05, min_t=2.0):
    if len(trades) < min_trades:
        return current, {"reason": "NOT_ENOUGH_TRADES", "n": len(trades)}
    half = len(trades) // 2
    score = lambda p, ts: [exit_r(*t, p) for t in ts]
    best = max(grid(), key=lambda p: sum(score(p, trades[:half])))
    cur, new = score(current, trades[half:]), score(best, trades[half:])
    diff = [b - a for a, b in zip(cur, new)]
    n, m = len(diff), sum(diff) / len(diff)
    sd = math.sqrt(sum((d - m) ** 2 for d in diff) / (n - 1)) if n > 1 else 0.0
    t = m / (sd / math.sqrt(n)) if sd > 0 else 0.0
    rep = {"n": len(trades), "current_oos_R": sum(cur) / n, "best_oos_R": sum(new) / n, "t": t, "best": best}
    if best != current and m > margin_r and t > min_t:
        return best, {"reason": "SWITCH", **rep}
    return current, {"reason": "KEEP", **rep}

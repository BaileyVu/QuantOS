"""Owner-authorized fixed-cost ml1h scenario, never full depth-aware L2.

Reproduces the reference's maker trade-through/48h/stop-first scenario with
the owner's uniform 4bp taker + 10bp adverse slippage, scaled together.
Maker entry/target fees are zero in that original experiment. No funding,
queue or USDC liquidity claim is made. Production costs are not changed.
"""
from __future__ import annotations

import numpy as np

HOUR_MS = 3_600_000
HOLD = 48
TAKER = .0004
SLIPPAGE = .001


def labels(times, high, low, close, atr, side, *, delay=0):
    """Return causal-origin labels and gross returns/cost indicators.

    Every label needs all 48 future bars inside BUILD. TP cannot fill in
    the entry bar; stop wins an ambiguous bar. Unfilled orders stay NaN.
    A fixed 48-hour exclusion is used even for an earlier exit, matching
    the historical test's conservative per-symbol non-overlap rule.
    """
    if side not in (-1, 1):
        raise ValueError("side must be -1 or +1")
    if delay not in (0, 1):
        raise ValueError("delay must be zero or one bar")
    t = np.asarray(times, dtype=np.int64)
    h, l, c, a = (np.asarray(x, dtype=float) for x in (high, low, close, atr))
    n = len(t)
    if any(len(x) != n or x.ndim != 1 for x in (h, l, c, a)) or t.ndim != 1:
        raise ValueError("aligned one-dimensional series required")
    if n > 1 and not np.all(np.diff(t) == HOUR_MS):
        raise ValueError("hourly coverage must be exact")
    if not all(np.isfinite(x).all() for x in (h, l, c)) or np.any(l <= 0) or np.any(h < l) or np.any(c < l) or np.any(c > h):
        raise ValueError("invalid bars")
    gross = np.full(n, np.nan)
    taker_exit = np.zeros(n, dtype=bool)
    y = np.zeros(n, dtype=np.int8)
    label_end = np.full(n, np.iinfo(np.int64).max, dtype=np.int64)
    i = np.arange(max(0, n-HOLD-delay))
    if not len(i):
        return y, gross, taker_exit, label_end
    valid = np.isfinite(a[i]) & (a[i] > 0) & (1.5*a[i] < c[i])
    valid &= l[i+1+delay] < c[i] if side == 1 else h[i+1+delay] > c[i]
    target, stop = c[i]+side*1.5*a[i], c[i]-side*1.5*a[i]
    tp, sl = np.full(len(i), HOLD+1), np.full(len(i), HOLD+1)
    for k in range(1, HOLD+1):
        hit_sl = l[i+k+delay] <= stop if side == 1 else h[i+k+delay] >= stop
        hit_tp = h[i+k+delay] > target if side == 1 else l[i+k+delay] < target
        sl = np.where((sl > HOLD) & hit_sl, k, sl)
        if k >= 2:
            tp = np.where((tp > HOLD) & hit_tp, k, tp)
    win = (tp < sl) & (tp <= HOLD)
    loss = (sl <= tp) & (sl <= HOLD)
    r = np.where(win, 1.5*a[i]/c[i], np.where(loss, -1.5*a[i]/c[i], side*(c[i+HOLD+delay]/c[i]-1)))
    selected = i[valid]
    gross[selected] = r[valid]
    taker_exit[selected] = ~win[valid]
    y[selected] = win[valid]
    label_end[selected] = t[selected+HOLD+delay]
    return y, gross, taker_exit, label_end


def net_returns(gross, taker_exit, cost_multiple):
    if cost_multiple not in (1, 2, 3):
        raise ValueError("only preregistered 1x/2x/3x costs")
    return np.asarray(gross) - np.asarray(taker_exit)*(TAKER+SLIPPAGE)*cost_multiple


def select_positions(times, symbols, sides, scores, thresholds, *, filled=None):
    """One combined long/short policy, top 2% calibrated scores, 48h lock.

    Cross-side ties prefer the larger score excess above its calibrated
    threshold, then LONG. No outcome/label is used for selection.
    """
    t, s, side, score, threshold = (np.asarray(x) for x in (times, symbols, sides, scores, thresholds))
    if not all(len(x) == len(t) for x in (s, side, score, threshold)):
        raise ValueError("misaligned selections")
    filled = np.ones(len(t), dtype=bool) if filled is None else np.asarray(filled, dtype=bool)
    if len(filled) != len(t):
        raise ValueError("misaligned fill observations")
    candidates = np.flatnonzero(np.isfinite(score) & np.isfinite(threshold) & (score >= threshold))
    order = sorted(candidates, key=lambda i: (int(t[i]), str(s[i]), -float(score[i]-threshold[i]), -int(side[i])))
    selected, busy, decided = [], {}, set()
    for i in order:
        key = (int(t[i]), str(s[i]))
        if key in decided:
            continue
        decided.add(key)  # Side choice precedes observing next-bar execution.
        if t[i] < busy.get(str(s[i]), -1):
            continue
        if not filled[i]:
            continue
        selected.append(int(i))
        busy[str(s[i])] = int(t[i])+HOLD*HOUR_MS
    return np.asarray(selected, dtype=int)

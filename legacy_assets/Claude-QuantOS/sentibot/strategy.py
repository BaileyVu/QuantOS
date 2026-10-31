"""Combine component scores into a target position. Long or cash only (spot, no leverage, no shorting)."""
import pandas as pd

# Weights fixed before testing. Live-only components (reddit, news) are 0 in backtests because no free history exists.
WEIGHTS = {"momentum": 0.35, "pattern": 0.25, "fear_greed": 0.10, "reddit": 0.15, "news": 0.15}
ENTER, EXIT = 0.25, 0.0            # hysteresis on the composite score
MIN_REBALANCE = 0.10               # only trade when target changes by >= 10% of equity


def composite(scores, weights=WEIGHTS):
    """scores: dict name -> Series (or float). Weighted average over the components present."""
    used = {k: w for k, w in weights.items() if k in scores and w > 0}
    tot = sum(used.values())
    return sum(scores[k] * w for k, w in used.items()) / tot


DECIDE_EVERY = 4                   # hours between decisions (cuts turnover; 1 = every bar)
RESIZE = False                     # False: size is fixed at entry, only entries and exits trade


def positions(comp, size, every=DECIDE_EVERY, resize=RESIZE):
    """Turn a composite score Series and a size Series into target weights with hysteresis and a rebalance band."""
    out, held, cur = [], False, 0.0
    hours = comp.index.hour if hasattr(comp.index, "hour") else range(len(comp))
    for h, sc, sz in zip(hours, comp.values, size.values):
        if h % every:
            out.append(cur)
            continue
        if held and cur > 0 and not resize:
            sz = cur
        if not held and sc > ENTER:
            held = True
        elif held and sc < EXIT:
            held = False
        want = sz if held else 0.0
        if (want == 0) != (cur == 0) or abs(want - cur) >= MIN_REBALANCE:
            cur = want
        out.append(cur)
    return pd.Series(out, index=comp.index)


def decide(score, size, held, cur, resize=RESIZE):
    """Single-step version of positions() for the live paper loop (call it every DECIDE_EVERY hours). Returns (held, target weight)."""
    if held and cur > 0 and not resize:
        size = cur
    if not held and score > ENTER:
        held = True
    elif held and score < EXIT:
        held = False
    want = size if held else 0.0
    if (want == 0) != (cur == 0) or abs(want - cur) >= MIN_REBALANCE:
        cur = want
    return held, cur

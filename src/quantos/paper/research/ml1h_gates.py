"""Preregistered fixed-cost L2 statistics; not portfolio/depth-aware returns."""
from __future__ import annotations

import numpy as np
from datetime import datetime, timezone

from .ml1h_fixed_cost import net_returns, select_positions, HOUR_MS

DAY = 86_400_000


def block_means(times, values, *, start_ms, end_ms, draws=2000, seed=17):
    """Seven-calendar-day paired bootstrap, retaining cross-asset dependence.

    Shared seed and calendar produce paired draws for both arms even when
    their trade dates/counts differ. Non-trading days remain on the calendar.
    """
    t, v = np.asarray(times), np.asarray(values, dtype=float)
    if len(t) != len(v) or not np.isfinite(v).all():
        raise ValueError("finite aligned trade returns required")
    days = np.arange(start_ms//DAY, (end_ms-1)//DAY+1)
    if len(days) < 14 or np.any(t < start_ms) or np.any(t >= end_ms):
        raise ValueError("invalid fixed evaluation calendar")
    ix = t//DAY-days[0]
    sums = np.bincount(ix.astype(int), weights=v, minlength=len(days))
    counts = np.bincount(ix.astype(int), minlength=len(days))
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, len(days), size=(draws, (len(days)+6)//7))
    sample = ((starts[:, :, None]+np.arange(7)) % len(days)).reshape(draws, -1)[:, :len(days)]
    numerator, denominator = sums[sample].sum(1), counts[sample].sum(1)
    return np.divide(numerator, denominator, out=np.full(draws, np.nan), where=denominator > 0)


def summarize(times, returns, quarters, *, placebo_means, start_ms, end_ms):
    r = np.asarray(returns, dtype=float)
    q = np.asarray(quarters)
    if len(r) != len(q) or not np.isfinite(r).all():
        raise ValueError("invalid trade observations")
    bootstrap = block_means(times, r, start_ms=start_ms, end_ms=end_ms)
    valid = bootstrap[np.isfinite(bootstrap)]
    ci = np.quantile(valid, [.05, .95]).tolist() if len(valid) == len(bootstrap) else [None, None]
    placebo = np.asarray(placebo_means, dtype=float)
    if len(placebo) != 200 or not np.isfinite(placebo).all():
        raise ValueError("all 200 preregistered placebo draws required")
    mean = float(r.mean()) if len(r) else None
    quarter_means = {x: (float(r[q == x].mean()) if np.any(q == x) else None)
                     for x in ("2022Q4", "2023Q1", "2023Q2")}
    p90 = float(np.quantile(placebo, .90))
    checks = {"n_at_least_150": len(r) >= 150,
              "positive_mean": mean is not None and mean > 0,
              "positive_ci90_low": ci[0] is not None and ci[0] > 0,
              "beats_placebo_p90": mean is not None and mean > p90,
              "half_quarters_positive": sum(x is not None and x > 0 for x in quarter_means.values()) >= 2}
    return {"n": len(r), "mean": mean, "ci90": ci, "placebo_p90": p90,
            "quarter_means": quarter_means, "gates": checks, "pass": all(checks.values())}, bootstrap


def decide(baseline, augmented, paired_delta_ci90, *, delay_mean_1x, delay_mean_2x):
    """Require all absolute gates and a paired improvement at both 1x and 2x.

    3x is reported stress, not an additional pass threshold. Missing metrics
    cannot pass. Result is only L3_SHADOW_ELIGIBLE, never promotion.
    """
    checks = {}
    for cost, delay in ((1, delay_mean_1x), (2, delay_mean_2x)):
        base, aug, ci = baseline[str(cost)], augmented[str(cost)], paired_delta_ci90[str(cost)]
        checks[f"{cost}x_absolute_gates"] = aug["pass"]
        checks[f"{cost}x_beats_baseline"] = (aug["mean"] is not None and base["mean"] is not None
                                            and aug["mean"] > base["mean"] and ci[0] is not None and ci[0] > 0)
        checks[f"{cost}x_delay_nonnegative"] = delay is not None and np.isfinite(delay) and delay >= 0
    return {"gates": checks, "decision": "L3_SHADOW_ELIGIBLE" if all(checks.values()) else "REJECTED",
            "scope": "fixed-cost L2; no depth, funding or historical USDC execution validation"}


def evaluate_records(records, *, start_ms=1664582400000, end_ms=1688169600000):
    """Evaluate frozen fold predictions without any refitting or selection search.

    Rows: arm, time_ms, symbol, side (+/-1), score, threshold, gross,
    taker_exit, delayed_gross, delayed_taker_exit. Missing gross is None.
    A/B must contain the exact same origin/side keys, including unfilled orders.
    """
    identities, summaries, bootstraps, delayed, counts = {}, {}, {}, {}, {}
    for arm in ("baseline", "augmented"):
        rows = [r for r in records if r["arm"] == arm]
        keys = [(int(r["time_ms"]), r["symbol"], int(r["side"])) for r in rows]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate prediction origin/side")
        identities[arm] = set(keys)
        if not rows:
            raise ValueError("both arms need complete predictions")
        times = np.array([r["time_ms"] for r in rows], dtype=np.int64)
        symbols = np.array([r["symbol"] for r in rows])
        sides = np.array([r["side"] for r in rows])
        if np.any(~np.isin(sides, [-1, 1])):
            raise ValueError("invalid prediction side")
        score = np.array([r["score"] for r in rows], dtype=float)
        threshold = np.array([r["threshold"] for r in rows], dtype=float)
        if not np.isfinite(score).all() or not np.isfinite(threshold).all():
            raise ValueError("nonfinite prediction/calibration score")
        gross = np.array([np.nan if r["gross"] is None else r["gross"] for r in rows], dtype=float)
        delayed_gross = np.array([np.nan if r["delayed_gross"] is None else r["delayed_gross"] for r in rows], dtype=float)
        taker = np.array([r["taker_exit"] for r in rows], dtype=bool)
        delayed_taker = np.array([r["delayed_taker_exit"] for r in rows], dtype=bool)
        quarters = np.array([f"{(d := datetime.fromtimestamp(int(t)/1000, timezone.utc)).year}Q{(d.month-1)//3+1}" for t in times])
        selected = select_positions(times, symbols, sides, score, threshold, filled=np.isfinite(gross))
        lagged = select_positions(times+HOUR_MS, symbols, sides, score, threshold, filled=np.isfinite(delayed_gross))
        strata = [(str(symbols[i]), int(sides[i]), str(quarters[i])) for i in range(len(rows))]
        matched = {}
        for i in selected:
            key = strata[i]
            matched[key] = matched.get(key, 0)+1
        pools = {key: np.array([i for i, k in enumerate(strata) if k == key and np.isfinite(gross[i])], dtype=int)
                 for key in matched}
        rng = np.random.default_rng(17)
        random_draws = [np.concatenate([rng.choice(pools[key], matched[key], replace=True)
                                        for key in sorted(matched)]) if matched else np.array([], dtype=int)
                        for _ in range(200)]
        summaries[arm], bootstraps[arm], delayed[arm] = {}, {}, {}
        counts[arm] = {"selected": len(selected), "delayed_selected": len(lagged), "predictions": len(rows)}
        for multiplier in (1, 2, 3):
            returns = net_returns(gross, taker, multiplier)
            lag_returns = net_returns(delayed_gross, delayed_taker, multiplier)
            # No-trade arm has no empirical placebo or mean; it fails n without
            # fabricating a return. Zero placeholders below are never reported.
            placebo = [float(returns[ix].mean()) if len(ix) else 0. for ix in random_draws]
            result, bs = summarize(times[selected], returns[selected], quarters[selected],
                                   placebo_means=placebo, start_ms=start_ms, end_ms=end_ms)
            if not len(selected): result["placebo_p90"] = None
            summaries[arm][str(multiplier)] = result
            bootstraps[arm][str(multiplier)] = bs
            delayed[arm][str(multiplier)] = float(lag_returns[lagged].mean()) if len(lagged) else None
    if identities["baseline"] != identities["augmented"]:
        raise ValueError("A/B prediction universes differ")
    delta = {}
    for multiplier in (1, 2, 3):
        differences = bootstraps["augmented"][str(multiplier)]-bootstraps["baseline"][str(multiplier)]
        delta[str(multiplier)] = np.quantile(differences, [.05, .95]).tolist() if np.isfinite(differences).all() else [None, None]
    decision = decide(summaries["baseline"], summaries["augmented"], delta,
                      delay_mean_1x=delayed["augmented"]["1"], delay_mean_2x=delayed["augmented"]["2"])
    return {**decision, "arms": summaries, "paired_improvement_ci90": delta,
            "delayed_mean": delayed, "counts": counts,
            "dsr": {"status": "UNAVAILABLE_HISTORICAL_MOMENTS", "historical_family_variants": 30,
                    "new_arms": 2, "global_status": "CANONICAL_REGISTRY_NOT_LOCATED"},
            "placebo": "200 symbol/side/quarter count-matched draws with replacement; overlap unconstrained"}

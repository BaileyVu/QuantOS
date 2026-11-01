"""Causal calibration estimates with explicit synthetic provenance."""
from .common import D, ZERO, ONE, utc_ms
from collections import defaultdict
from random import Random


def wilson(successes, count):
    if not count:
        return ZERO, ONE
    n, z = D(count), D('2.576')  # two-sided 99% marginal interval
    p = D(successes) / n
    denom = ONE + z*z/n
    center = (p + z*z/(2*n)) / denom
    radius = z * (p*(ONE-p)/n + z*z/(4*n*n)).sqrt() / denom
    return max(ZERO, center-radius), min(ONE, center+radius)


class Features:
    def __init__(self, calibration=None):
        self.calibration = calibration
        self.bounds = None

    def block_bounds(self, rows):
        if self.bounds is not None:
            return self.bounds
        groups = defaultdict(lambda: [0, 0, 0])
        for r in rows:
            g = groups[r['day']]
            g[0] += r['result'] == 'both_full'
            g[1] += r['result'] == 'one'
            g[2] += 1
        if len(groups) < 10:
            return None
        rng, groups = Random(199), list(groups.values())
        both, one = [], []
        for _ in range(1000):
            sample = [rng.choice(groups) for _ in groups]
            n = sum(g[2] for g in sample)
            both.append(D(sum(g[0] for g in sample))/n)
            one.append(D(sum(g[1] for g in sample))/n)
        both.sort(); one.sort()
        # Whole-day bootstrap preserves within-day dependence. Wilson bounds add
        # a finite-sample penalty even when a resample sees no one-leg failures.
        both_lo, _ = wilson(sum(r['result'] == 'both_full' for r in rows), len(rows))
        _, one_hi = wilson(sum(r['result'] == 'one' for r in rows), len(rows))
        self.bounds = min(both_lo, both[5]), max(one_hi, one[994]), len(groups)
        return self.bounds

    def estimate(self, now, prices, queues, size, shadow=False):
        c = self.calibration
        if not c or (shadow and c.get('synthetic', True)):
            return None
        if utc_ms(c['trained_through']) >= now or c['execution_model'] != 'pessimistic':
            return None
        if size > D(c['max_size']) or max(queues) > D(c['max_queue']):
            return None
        rows = c['observations']
        if any(utc_ms(r['observed_at']) > utc_ms(c['trained_through']) for r in rows):
            raise ValueError('calibration future leakage')
        if any(r['result'] not in ('both_full', 'one', 'neither') for r in rows):
            raise ValueError('invalid calibration result')
        bounds = self.block_bounds(rows)
        if bounds is None:
            return None
        both_lo, one_hi, blocks = bounds
        # Any unmatched quantity may lose its full cost. Incentives are exactly zero.
        matched = size * (ONE - sum(prices, ZERO))
        unmatched = -size * max(prices)
        costs = size * D(c['slippage_and_ops_per_share'])
        net = both_lo*matched + one_hi*unmatched - costs
        return dict(p_both_lower=both_lo, p_one_upper=one_hi, matched=matched,
                    unmatched=unmatched, costs=costs, expected_net_lower=net,
                    expected_rebates=ZERO, expected_rewards=ZERO,
                    source=c['source'], synthetic=c['synthetic'], day_blocks=blocks,
                    estimator='day-block-bootstrap plus finite-sample penalty; not a validated economic guarantee')

"""Persistent carry research core. No exchange effects or future-data selection."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from research.v1f1.factor import rank01, HOUR


@dataclass(frozen=True)
class Signal:
    score: float
    funding_8h: float


def observations(s, t):
    if t < 720:
        return None
    p = s.perp[t-720:t]
    index, mark = s.index[t-168:t], s.mark[t-168:t]
    if not all(np.isfinite(a).all() for a in (p, index, mark)):
        return None
    if np.any(p[:, :4] <= 0) or np.any(index[:, :4] <= 0):
        return None
    now = s.start + t*HOUR
    end = s.funding_before(now-HOUR)+1
    begin = np.searchsorted(s.funding[:, 0], now-168*HOUR)
    f = s.funding[begin:end]
    if len(f) < 15 or now-f[-1, 0] > (f[-1, 1]+1)*HOUR:
        return None
    # Reject a materially absent settlement, including interval changes.
    if np.any(np.diff(f[:, 0]) > (np.maximum(f[:-1, 1], f[1:, 1])+1)*HOUR):
        return None
    rates = f[:, 2]*8/f[:, 1]
    basis = (index[:, 3]-p[-168:, 3])/p[-168:, 3]
    values = [-rates[-1], -rates.mean(), -np.sign(rates).mean(),
              basis[-1], np.sign(basis).mean()]
    return np.asarray(values), float(rates.mean())


def signals(panel, candidates, t, variant):
    if variant not in ('A', 'B', 'C'):
        raise ValueError('unregistered variant')
    hedges = () if variant == 'A' else (('BTCUSDT',) if variant == 'B' else ('BTCUSDT', 'ETHUSDT'))
    values = []
    for symbol in candidates:
        if symbol in hedges:
            continue
        value = observations(panel[symbol], t)
        if value is not None:
            values.append((symbol, value))
        if len(values) == 25:
            break
    if len(values) < 15 or any(observations(panel[s], t) is None for s in hedges):
        return {}, {}
    matrix = np.asarray([x[1][0] for x in values])
    score = np.column_stack([rank01(matrix[:, j]) for j in range(5)]) @ np.array([.2, .35, .15, .2, .1])
    scored = {symbol: Signal(float(score[i]), value[1]) for i, (symbol, value) in enumerate(values)}
    betas = {}
    if hedges:
        benchmark = np.mean([np.diff(np.log(panel[s].perp[t-720:t, 3])) for s in hedges], axis=0)
        variance = np.var(benchmark, ddof=1)
        if variance <= 0:
            return {}, {}
        for symbol in scored:
            returns = np.diff(np.log(panel[symbol].perp[t-720:t, 3]))
            beta = np.cov(returns, benchmark, ddof=1)[0, 1]/variance
            if 0 < beta <= 3:
                betas[symbol] = float(beta)
        scored = {s: value for s, value in scored.items() if s in betas}
        if len(scored) < 15:
            return {}, {}
    return scored, betas


def choose_side(scored, held, side):
    """Two slots; replacement must clear twice full stress switching cost."""
    ordered = sorted(scored, key=lambda s: (-side*scored[s].score, s))
    n = len(ordered)
    enter = ordered[:max(2, int(np.ceil(.2*n)))]
    keep_band = set(ordered[:int(np.ceil(.4*n))])
    selected = list(held)
    for old in list(held):
        if old in keep_band:
            continue
        replacement = next((s for s in enter if s not in selected), None)
        if replacement is None:
            continue
        # Seven-day expected funding only; no assumed price/basis convergence.
        advantage = side*(scored[old].funding_8h-scored[replacement].funding_8h)*21
        if advantage > 2*(2*(5+10)/10000):
            selected[selected.index(old)] = replacement
    for symbol in enter:
        if len(selected) == 2:
            break
        if symbol not in selected:
            selected.append(symbol)
    return selected


def plan(scored, betas, longs, shorts, variant):
    if len(scored) < 15 or any(s not in scored for s in longs+shorts):
        return [], [], {}, 'eligibility_exit'
    if not longs and max(x.score for x in scored.values()) == min(x.score for x in scored.values()):
        return [], [], {}, 'flat_cross_section'
    new_long = choose_side(scored, longs, 1)
    new_short = choose_side(scored, shorts, -1) if variant == 'A' else []
    if len(new_long) != 2 or (variant == 'A' and (len(new_short) != 2 or set(new_long)&set(new_short))):
        return [], [], {}, 'uncoordinated_exit'
    if variant == 'A':
        weights = {**{s: .25 for s in new_long}, **{s: -.25 for s in new_short}}
    else:
        beta = np.mean([betas[s] for s in new_long])
        long_gross = 1/(1+beta)
        weights = {s: long_gross/2 for s in new_long}
        hedges = ['BTCUSDT'] if variant == 'B' else ['BTCUSDT', 'ETHUSDT']
        weights.update({s: -(1-long_gross)/len(hedges) for s in hedges})
    reason = 'membership' if set(new_long)!=set(longs) or set(new_short)!=set(shorts) else 'retain'
    return new_long, new_short, weights, reason


def fill_cost(delta, price, fee, slip):
    if not all(np.isfinite(x) for x in (delta, price, fee, slip)) or price <= 0 or min(fee, slip) < 0:
        raise ValueError('invalid fill')
    notional = abs(delta)*price
    return notional*(1+np.sign(delta)*slip/10000)*fee/10000, notional*slip/10000


def valid_open(s, h):
    now = s.start+h*HOUR
    j = s.funding_before(now)
    return (np.isfinite(s.perp[h, 0]) and s.perp[h, 0] > 0 and
            np.isfinite(s.mark[h, 0]) and s.mark[h, 0] > 0 and j >= 0 and
            now-s.funding[j, 0] <= (s.funding[j, 1]+1)*HOUR)


def replacement_economic(panel, scored, h, quantities, weights, equity):
    """Portfolio-level hurdle includes changed hedge and retained-leg notionals."""
    old_carry = new_carry = cost = 0.
    symbols = set(quantities)|set(weights)
    for symbol in symbols:
        s = panel[symbol]
        # Completed reference prices and one-hour-lagged funding forecast only.
        price = s.perp[h-1, 3]
        value = observations(s, h) if symbol not in scored else None
        if symbol not in scored and value is None:
            return False
        rate = scored[symbol].funding_8h if symbol in scored else value[1]
        old = quantities.get(symbol, 0.)
        new = weights.get(symbol, 0.)*max(0., equity)/price
        old_carry -= old*price*rate*21
        new_carry -= new*price*rate*21
        fees, slip = fill_cost(new-old, price, 5, 10)
        cost += fees+slip
    return new_carry-old_carry > 2*cost


def simulate(panel, signal_cache, variant, first, last, fee, slip):
    """Continuous quantities, hourly valuation, actual funding, delta-only fills."""
    if first < 720 or last <= first or last > min(len(s.perp) for s in panel.values()):
        raise ValueError('invalid research bounds')
    equity = np.ones(last-first+1)
    cash = 1.; q = {}; last_price = {}; opened = {}; longs = []; shorts = []
    totals = dict(price=0., funding=0., fees=0., slippage=0., long=0., short=0., turnover=0.)
    fills = []; durations = []; pending = None; loss_exits = 0; missing_entries = 0
    active_hours = 0; max_gross = 0.; max_net = 0.; changes = 0

    def book(key, amount, quantity):
        totals[key] += amount
        totals['long' if quantity > 0 else 'short'] += amount if key in ('price', 'funding') else -amount

    def trade(target, prices, h, reason, exit_slip=None):
        nonlocal cash, q, changes
        changed = False
        capital = cash
        for symbol in sorted(set(q)|set(target)):
            old, new = q.get(symbol, 0.), target.get(symbol, 0.)
            delta = new-old
            if abs(delta) <= 1e-12*max(1., abs(old), abs(new)):
                continue
            changed = True
            price = prices[symbol]
            f, s = fill_cost(delta, price, fee, slip if exit_slip is None else exit_slip)
            # Split a crossing fill into its old-side close and new-side opening.
            close = min(abs(old), abs(delta)) if old*delta < 0 else 0.
            fraction = close/abs(delta)
            if fraction:
                book('fees', f*fraction, old); book('slippage', s*fraction, old)
            if fraction < 1:
                book('fees', f*(1-fraction), new); book('slippage', s*(1-fraction), new)
            cash -= f+s
            totals['turnover'] += abs(delta)*price/max(capital, 1e-12)
            fills.append(dict(hour=h, symbol=symbol, delta=float(delta), notional=float(abs(delta)*price),
                              fees=float(f), slippage=float(s), reason=reason))
            if old and (not new or old*new < 0):
                durations.append(h-opened.pop(symbol))
            if new and (not old or old*new < 0):
                opened[symbol] = h
            if new:
                last_price[symbol] = price
            else:
                last_price.pop(symbol, None)
        q = {s: v for s, v in target.items() if v}
        changes += int(changed)

    for h in range(first, last):
        invalid = False
        for symbol in q:
            s = panel[symbol]
            if not valid_open(s, h):
                invalid = True; break
        if invalid:
            prices = {s: panel[s].perp[h-1, 3] for s in q}
            if not all(np.isfinite(p) for p in prices.values()):
                raise ValueError('last trustworthy exit unavailable')
            for s, quantity in q.items():
                amount = quantity*(prices[s]-last_price[s]); cash += amount; book('price', amount, quantity)
                series = panel[s]
                lo = np.searchsorted(series.funding[:, 0], series.start+(h-1)*HOUR, side='right')
                hi = np.searchsorted(series.funding[:, 0], series.start+h*HOUR, side='left')
                for stamp, interval, rate in series.funding[lo:hi]:
                    mark = series.mark[int((stamp-series.start)//HOUR), 0]
                    if not np.isfinite(mark):
                        raise ValueError('missing funding before forced exit')
                    amount = -quantity*mark*rate; cash += amount; book('funding', amount, quantity)
            trade({}, prices, h, 'data_loss', max(10, slip))
            longs, shorts, pending = [], [], None
            loss_exits += 1
        else:
            for symbol, quantity in q.items():
                s = panel[symbol]; price = s.perp[h, 0]
                amount = quantity*(price-last_price[symbol]); cash += amount; book('price', amount, quantity)
                last_price[symbol] = price
                lo = np.searchsorted(s.funding[:, 0], s.start+(h-1)*HOUR, side='right')
                hi = np.searchsorted(s.funding[:, 0], s.start+h*HOUR, side='right')
                for stamp, interval, rate in s.funding[lo:hi]:
                    mark = s.mark[int((stamp-s.start)//HOUR), 0]
                    if not np.isfinite(mark):
                        raise ValueError('missing actual funding valuation')
                    amount = -quantity*mark*rate; cash += amount; book('funding', amount, quantity)
        if pending is not None:
            new_long, new_short, weights, reason = pending; pending = None
            symbols = set(q)|set(weights)
            if any(not valid_open(panel[s], h) for s in symbols):
                # Whole basket cancels before any fill; old valid holdings remain.
                missing_entries += 1
            else:
                prices = {s: panel[s].perp[h, 0] for s in symbols}
                marked = cash+sum(v*(panel[s].mark[h, 0]-prices[s]) for s, v in q.items())
                gross = sum(abs(v)*prices[s] for s, v in q.items())
                friction = ((1+slip/10000)*fee+slip)/10000
                if reason == 'retain':
                    scale = min(1., max(0., marked-friction*gross)/(gross*(1-friction))) if gross else 1.
                    target = {s: v*scale for s, v in q.items()}
                    reason = 'gross_cap' if scale < 1 else 'retain'
                else:
                    budget = max(0., marked-friction*gross)/(1+friction)
                    target = {s: w*budget/prices[s] for s, w in weights.items()}
                trade(target, prices, h, reason)
                longs, shorts = new_long, new_short
        if h == last-1 or cash <= 0:
            trade({}, {s: panel[s].perp[h, 0] for s in q}, h, 'fold_close' if cash > 0 else 'insolvency')
            longs, shorts = [], []
        marked = cash+sum(v*(panel[s].mark[h, 0]-panel[s].perp[h, 0]) for s, v in q.items())
        equity[h-first] = marked
        if q:
            active_hours += 1
            notionals = [v*panel[s].mark[h, 0] for s, v in q.items()]
            max_gross = max(max_gross, sum(abs(v) for v in notionals)/max(marked, 1e-12))
            max_net = max(max_net, abs(sum(notionals))/max(marked, 1e-12))
        if cash <= 0 or marked <= 0:
            equity[h-first:] = min(cash, marked)
            break
        if h in signal_cache and h < last-1:
            scored, betas = signal_cache[h]
            pending = plan(scored, betas, longs, shorts, variant)
            if q and pending[3] == 'membership' and not replacement_economic(panel, scored, h, q, pending[2], marked):
                pending = (longs, shorts, pending[2], 'retain')
    else:
        equity[-1] = cash
    daily = equity[24::24]/equity[:-24:24]-1
    return dict(equity=equity, daily=daily, totals=totals, fills=fills,
                holding_hours=durations, economic_changes=changes, data_loss_exits=loss_exits,
                failed_baskets=missing_entries, active_hours=active_hours,
                max_gross=max_gross, max_abs_net=max_net)

"""Final authority: conservative scenario reservation, no netting across markets."""
from datetime import datetime, timezone
from .common import D, ZERO


class Risk:
    def __init__(self, capital):
        self.peak = self.day_start = self.week_start = capital
        self.last_equity = capital
        self.day = self.week = None
        self.halted = ''

    def observe(self, now, equity):
        dt = datetime.fromtimestamp(now / 1000, timezone.utc)
        day, week = dt.date().isoformat(), dt.isocalendar()[:2]
        if day != self.day:
            self.day, self.day_start = day, self.last_equity
        if week != self.week:
            self.week, self.week_start = week, self.last_equity
        self.peak = max(self.peak, equity)
        if equity <= self.day_start*D('.97'):
            self.halted = 'daily_loss_halt'
        if equity <= self.week_start*D('.94'):
            self.halted = 'weekly_loss_halt'
        if equity <= self.peak*D('.90'):
            self.halted = 'peak_drawdown_halt'
        self.last_equity = equity
        return self.halted

    def approve(self, intents, execution, md, equity):
        if self.halted or execution.uncertain:
            return self.halted or 'cancellation_or_fill_uncertainty'
        if not intents:
            return 'empty_intent'
        if any(execution.positions.get(i.market) and execution.positions[i.market].total_cost > 0 for i in intents):
            return 'no_averaging_or_inventory_increase'
        reserves = [o for o in execution.orders.values() if o.pending]
        projected = {}
        for mid in md.markets:
            pos = execution.positions.get(mid)
            q = {s: pos.qty[s] if pos else ZERO for s in ('UP', 'DOWN')}
            cost = {s: pos.cost[s] if pos else ZERO for s in q}
            pendq = dict.fromkeys(q, ZERO)
            pendc = dict.fromkeys(q, ZERO)
            for o in reserves:
                if o.market == mid:
                    pendq[o.outcome] += o.remaining
                    pendc[o.outcome] += o.remaining*o.price
            for i in intents:
                if i.market == mid:
                    m = md.markets[mid]
                    if i.size < m.minimum or not ZERO < i.price < 1 or i.price % m.tick[i.outcome] or i.size <= 0:
                        return 'tick_or_minimum'
                    pendq[i.outcome] += i.size
                    pendc[i.outcome] += i.size*i.price
            # Upper bound: cost of all shares on the dominant side. This deliberately
            # over-reserves some matched inventory; never understates a one-leg loss.
            unmatched = max((cost[s]+pendc[s] if q[s]+pendq[s] > q[t] else ZERO)
                            for s, t in [('UP', 'DOWN'), ('DOWN', 'UP')])
            matched = min(q['UP']+pendq['UP'], q['DOWN']+pendq['DOWN'])  # $1 collateral cap
            projected[mid] = unmatched, matched
            if unmatched > equity*D('.02'):
                return 'market_unmatched_limit'
            if matched > equity*D('.10'):
                return 'market_matched_limit'
        if sum((x[0] for x in projected.values()), ZERO) > equity*D('.03'):
            return 'global_unmatched_limit'
        if sum((x[1] for x in projected.values()), ZERO) > equity*D('.20'):
            return 'global_matched_limit'
        cash_needed = sum((o.remaining*o.price for o in reserves), ZERO) + sum((i.size*i.price for i in intents), ZERO)
        if cash_needed > execution.cash:
            return 'insufficient_unreserved_collateral'
        return ''

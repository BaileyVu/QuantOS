from collections import Counter
from random import Random
from .common import D, ZERO, ONE, iso, utc_ms


def block_bootstrap(blocks, iterations=1000):
    """Resample whole days/markets, never individual five-minute fills."""
    values = list(blocks.values())
    if len(values) < 2:
        return {'status': 'insufficient_independent_blocks', 'blocks': len(values)}
    rng = Random(199)
    samples = sorted(sum((rng.choice(values) for _ in values), ZERO)/D(len(values)) for _ in range(iterations))
    return dict(status='descriptive_only', blocks=len(values), iterations=iterations,
                lower_95=samples[int(iterations*.025)], upper_95=samples[int(iterations*.975)])


class Evaluation:
    def __init__(self, capital):
        self.start, self.peak, self.last_equity = capital, capital, capital
        self.drawdown = self.max_unmatched = self.max_utilization = ZERO
        self.unmatched_ms = self.last_time = 0
        self.last_unmatched = ZERO
        self.daily = {}
        self.skips = Counter()
        self.decisions = self.accepted = 0
        self.stops = []
        self.latencies = []
        self.max_book_age = 0

    def observe(self, now, execution, md, event):
        eq = execution.equity(md, now)
        self.peak = max(self.peak, eq)
        self.drawdown = max(self.drawdown, (self.peak-eq)/self.peak)
        unmatched = sum((p.unmatched_cost for p in execution.positions.values()), ZERO)
        if self.last_time and self.last_unmatched:
            self.unmatched_ms += now-self.last_time
        self.max_unmatched = max(self.max_unmatched, unmatched)
        self.last_time, self.last_unmatched = now, unmatched
        locked = execution.reserved + sum((p.total_cost for p in execution.positions.values()), ZERO)
        self.max_utilization = max(self.max_utilization, locked/eq if eq > 0 else D('1'))
        day = iso(now)[:10]
        self.daily[day] = self.daily.get(day, ZERO)+eq-self.last_equity
        self.last_equity = eq
        self.latencies.append(now-utc_ms(event['event_time']))
        self.max_book_age = max(self.max_book_age, max((max(0, now-b.event_ms) for (mid, _), b in md.books.items() if b.event_ms >= 0 and md.markets[mid].active), default=0))

    def report(self, execution, md, model, mode):
        eq = self.last_equity
        market_pnl = {}
        for r in execution.records:
            if 'pnl' in r:
                market_pnl[r['market']] = market_pnl.get(r['market'], ZERO)+r['pnl']
        return dict(schema='quantos-pm-report-v1', virtual_only=True, mode=mode, model=model,
                    starting_equity=self.start, ending_equity=eq, realized_pnl=execution.realized,
                    unrealized_pnl=eq-self.start-execution.realized, pnl_excluding_incentives=eq-self.start,
                    rebates=ZERO, rewards=ZERO, fees=execution.fees, slippage=ZERO,
                    slippage_note='passive fills at limit; adverse selection reflected by inventory marks/settlement',
                    turnover=execution.turnover, candidate_markets=len(md.markets),
                    quoted_markets=self.accepted,
                    skipped_market_fraction=D(len(md.markets)-self.accepted)/len(md.markets) if md.markets else ONE,
                    orders=execution.counts, complete_pairs=execution.pairs,
                    matched_set_pnl=execution.matched_pnl, directional_pnl=execution.directional_pnl,
                    average_locked_spread=execution.matched_pnl/execution.pairs if execution.pairs else ZERO,
                    unmatched_exposure=execution.positions and sum((p.unmatched_cost for p in execution.positions.values()), ZERO) or ZERO,
                    max_unmatched_exposure=self.max_unmatched, unmatched_duration_seconds=D(self.unmatched_ms)/1000,
                    reserved_collateral=execution.reserved, cash=execution.cash,
                    capital_utilization_peak=self.max_utilization, max_drawdown=self.drawdown,
                    daily_pnl=self.daily, stop_events=self.stops, skip_reasons=dict(self.skips),
                    decisions=self.decisions, accepted_opportunities=self.accepted,
                    decision_definition='each snapshot/delta/timer evaluation of an active market; includes already-used markets',
                    skipped_fraction=D(self.decisions-self.accepted)/self.decisions if self.decisions else ONE,
                    mean_receipt_latency_ms=D(sum(self.latencies))/len(self.latencies) if self.latencies else ZERO,
                    max_receipt_latency_ms=max(self.latencies, default=0), max_book_age_ms=self.max_book_age,
                    day_block_bootstrap=block_bootstrap(self.daily), market_block_bootstrap=block_bootstrap(market_pnl),
                    promotion_eligible=False, edge_demonstrated=False,
                    pending_orders=sum(o.pending for o in execution.orders.values()),
                    execution_uncertain=execution.uncertain)


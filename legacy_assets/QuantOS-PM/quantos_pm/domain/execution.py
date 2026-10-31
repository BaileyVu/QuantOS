"""Virtual order lifecycle and collateral ledger. No network imports."""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP
from .common import D, ZERO, ONE, utc_ms


def fee(shares, price, rate, *, maker=False):
    shares, price, rate = D(shares), D(price), D(rate)
    if shares < 0 or not ZERO <= price <= ONE or rate < 0:
        raise ValueError('invalid fee input')
    return ZERO if maker else (shares*rate*price*(ONE-price)).quantize(D('.00001'), rounding=ROUND_HALF_UP)


@dataclass
class Position:
    qty: dict = field(default_factory=lambda: dict.fromkeys(('UP', 'DOWN'), ZERO))
    cost: dict = field(default_factory=lambda: dict.fromkeys(('UP', 'DOWN'), ZERO))

    @property
    def matched(self):
        return min(self.qty.values())

    @property
    def total_cost(self):
        return sum(self.cost.values(), ZERO)

    @property
    def unmatched_cost(self):
        return sum((self.cost[o]*(self.qty[o]-self.matched)/self.qty[o]
                    for o in self.qty if self.qty[o]), ZERO)

    def buy(self, outcome, size, price):
        if outcome not in self.qty or size <= 0 or not ZERO < price < ONE:
            raise ValueError('invalid virtual fill')
        self.qty[outcome] += size
        self.cost[outcome] += size*price

    def remove(self, outcome, size):
        if size < 0 or size > self.qty[outcome]:
            raise ValueError('invalid position removal')
        cost = self.cost[outcome]*size/self.qty[outcome] if size else ZERO
        self.qty[outcome] -= size
        self.cost[outcome] -= cost
        return cost


@dataclass
class Order:
    id: str
    market: str
    outcome: str
    price: object
    size: object
    remaining: object
    submitted: int
    activation: int
    status: str = 'SUBMITTING'
    queue: object = ZERO
    cancel_at: int | None = None
    cancel_failed: bool = False

    @property
    def pending(self):
        return self.status in ('SUBMITTING', 'OPEN', 'CANCEL_PENDING', 'UNCERTAIN')


class Execution:
    def __init__(self, config):
        self.config, self.cash = config, config.capital
        self.positions, self.orders, self.records = {}, {}, []
        self.realized = self.turnover = self.fees = ZERO
        self.matched_pnl = self.directional_pnl = self.pairs = ZERO
        self.uncertain = False
        self.counts = dict(quoted=0, canceled=0, rejected=0, fills=0, partial_fills=0,
                           missed_fills=0, unobservable_fills=0)

    def record(self, kind, now, **fields):
        self.records.append(dict(kind=kind, time=now, **fields))

    @property
    def reserved(self):
        return sum((o.price*o.remaining for o in self.orders.values() if o.pending), ZERO)

    def submit(self, intents, now):
        multiplier = {'optimistic': 1, 'baseline': 2, 'pessimistic': 3}[self.config.model]
        for i in intents:
            oid = f'virtual-{len(self.orders)+1:08d}'
            self.orders[oid] = Order(oid, i.market, i.outcome, i.price, i.size, i.size,
                                     now, now+self.config.submit_ms*multiplier)
            self.counts['quoted'] += 1
            self.record('virtual_submit', now, order=oid, market=i.market, outcome=i.outcome,
                        price=i.price, size=i.size)

    def cancel(self, now, reason, mid=None):
        for o in self.orders.values():
            if o.pending and o.cancel_at is None and (mid is None or o.market == mid):
                lag = self.config.cancel_ms * (3 if self.config.model == 'pessimistic' else 1)
                o.cancel_at = now+lag
                if o.status != 'SUBMITTING':
                    o.status = 'CANCEL_PENDING'
                self.record('cancel_requested', now, order=o.id, reason=reason, due=o.cancel_at)

    def advance(self, now, md):
        for o in self.orders.values():
            if not o.pending:
                continue
            m = md.markets[o.market]
            if now >= m.expiry or m.resolved:
                o.status = 'EXPIRED'
                self.record('expiry_reconciled', now, order=o.id)
                continue
            if o.cancel_at is not None and now >= o.cancel_at:
                if o.cancel_failed or self.uncertain:
                    o.status = 'UNCERTAIN'
                else:
                    o.status = 'CANCELED'
                    self.counts['canceled'] += 1
                    self.record('cancel_ack_reconciled', now, order=o.id)
                continue
            if o.status == 'SUBMITTING' and now >= o.activation:
                b = md.books[o.market, o.outcome]
                if (not b.fresh(now, self.config.stale_ms) or not m.active
                        or o.price % m.tick[o.outcome] or o.size < m.minimum
                        or o.price >= min(b.asks)):
                    o.status = 'REJECTED'
                    self.counts['rejected'] += 1
                    self.record('post_only_or_metadata_reject', now, order=o.id)
                    continue
                o.status = 'CANCEL_PENDING' if o.cancel_at else 'OPEN'
                factor = {'optimistic': D('1'), 'baseline': D('1.5'), 'pessimistic': D('2')}[self.config.model]
                o.queue = b.queue(o.price)*factor
                self.record('virtual_open', now, order=o.id, queue_ahead=o.queue)
            if now-o.submitted >= self.config.quote_ttl_ms and o.cancel_at is None:
                self.cancel(now, 'quote_ttl', o.market)

    def depth_change(self, event, previous_bids, md):
        """Depth removal is ambiguous; additions at better prices jump our queue.

        Pessimistic mode also reserves same-price additions ahead, since public L2
        cannot distinguish replenishment from new orders. No touch-based fills.
        """
        mid, outcome = event['market'], event['outcome']
        current = md.books[mid, outcome].bids
        for o in self.orders.values():
            if o.market != mid or o.outcome != outcome or o.status not in ('OPEN', 'CANCEL_PENDING'):
                continue
            added = sum((max(ZERO, q-previous_bids.get(p, ZERO)) for p, q in current.items()
                         if p > o.price or (p == o.price and self.config.model == 'pessimistic')), ZERO)
            o.queue += added*(2 if self.config.model == 'pessimistic' else 1)

    def trade(self, e, now, md):
        size, price, ts = D(e['size']), D(e['price']), utc_ms(e['event_time'])
        if size <= 0 or not ZERO < price < ONE:
            raise ValueError('invalid trade')
        for o in self.orders.values():
            if (o.status == 'CANCELED' and o.market == e['market'] and o.outcome == e['outcome']
                    and o.price == price and o.cancel_at is not None
                    and o.activation <= ts <= o.cancel_at < now):
                self.uncertain = True
                self.record('late_trade_after_cancel_requires_reconciliation', now, order=o.id)
        candidates = [o for o in self.orders.values() if o.market == e['market'] and o.outcome == e['outcome']
                      and o.pending and o.status != 'SUBMITTING']
        if not candidates:
            return
        if e.get('aggressor') != 'SELL' or e.get('aggressor_verified') is not True or self.uncertain:
            self.counts['unobservable_fills'] += 1
            return
        available = size * (D('.5') if self.config.model == 'pessimistic' else ONE)
        for o in sorted(candidates, key=lambda x: (-x.price, x.submitted, x.id)):
            if price != o.price or ts < o.activation or now-ts > self.config.stale_ms:
                self.counts['unobservable_fills'] += 1
                continue
            used = min(o.queue, available)
            o.queue -= used
            available -= used
            q = min(o.remaining, available)
            if q <= 0:
                self.counts['missed_fills'] += 1
                continue
            self.cash -= q*o.price
            self.turnover += q*o.price
            p = self.positions.setdefault(o.market, Position())
            p.buy(o.outcome, q, o.price)
            o.remaining -= q
            available -= q
            self.counts['fills'] += 1
            if o.remaining:
                self.counts['partial_fills'] += 1
            else:
                o.status = 'FILLED'
            self.record('virtual_fill', now, order=o.id, market=o.market, outcome=o.outcome,
                        price=o.price, size=q, fee=fee(q, o.price, md.markets[o.market].fee_rate, maker=True),
                        matched=p.matched, unmatched_cost=p.unmatched_cost)
            if self.cash < 0 or self.reserved > self.cash:
                raise ValueError('collateral invariant breached')

    def merge(self, mid, now):
        p = self.positions.get(mid)
        if p is None or p.matched == 0:
            return
        q = p.matched
        cost = sum((p.remove(o, q) for o in ('UP', 'DOWN')), ZERO)
        self.cash += q
        self.realized += q-cost
        self.matched_pnl += q-cost
        self.pairs += q
        self.record('virtual_merge', now, market=mid, pairs=q, cost=cost, payout=q, pnl=q-cost)

    def settle(self, mid, winner, now):
        self.merge(mid, now)
        p = self.positions.get(mid)
        if p is None:
            return
        payout, cost = p.qty[winner], p.total_cost
        self.cash += payout
        self.realized += payout-cost
        self.directional_pnl += payout-cost
        for o in ('UP', 'DOWN'):
            p.remove(o, p.qty[o])
        self.record('virtual_redemption', now, market=mid, winner=winner, payout=payout, cost=cost, pnl=payout-cost)

    def equity(self, md, now):
        value = self.cash
        for mid, p in self.positions.items():
            value += p.matched
            for o in p.qty:
                b = md.books[mid, o]
                mark = max(b.bids) if b.fresh(now, self.config.stale_ms) else ZERO
                value += (p.qty[o]-p.matched)*mark
        return value

    def reconcile(self, e, now):
        pending = {o.id for o in self.orders.values() if o.pending}
        if e.get('authoritative_virtual_state') is not True or set(e.get('canceled_orders', [])) != pending:
            raise ValueError('incomplete virtual reconciliation')
        for oid in pending:
            self.orders[oid].status = 'CANCELED'
            self.counts['canceled'] += 1
            self.record('explicit_virtual_reconciliation', now, order=oid)
        self.uncertain = False

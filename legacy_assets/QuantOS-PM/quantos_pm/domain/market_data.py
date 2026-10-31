"""Validated canonical books. Transport arrival order is not exchange sequencing."""
from dataclasses import dataclass, field
import re
from .common import D, ZERO, ONE, utc_ms


@dataclass
class Market:
    id: str
    tokens: dict
    start: int
    expiry: int
    tick: dict
    minimum: object
    fee_rate: object
    rules: str
    rules_verified: bool
    fee_verified: bool
    active: bool = True
    resolved: bool = False

    @classmethod
    def parse(cls, e):
        if not re.fullmatch(r'0x[0-9a-fA-F]{64}', e['market']):
            raise ValueError('invalid condition id')
        tokens = e['tokens']
        if set(tokens) != {'UP', 'DOWN'} or len(set(tokens.values())) != 2 or not all(str(x).isdigit() for x in tokens.values()):
            raise ValueError('invalid outcome token mapping')
        start, end = utc_ms(e['start']), utc_ms(e['expiry'])
        if e['symbol'] != 'BTC' or end - start != 300000:
            raise ValueError('only BTC five-minute binary markets')
        tick, minimum, fee = D(e['tick']), D(e['minimum']), D(e['fee_rate'])
        if not ZERO < tick < ONE or minimum <= 0 or not ZERO <= fee <= ONE:
            raise ValueError('invalid market constraints')
        if not e['rules']:
            raise ValueError('missing settlement rules')
        return cls(e['market'], tokens, start, end, {x: tick for x in tokens}, minimum,
                   fee, e['rules'], e['rules_verified'] is True, e['fee_verified'] is True,
                   e['active'] is True)


@dataclass
class Book:
    bids: dict = field(default_factory=dict)
    asks: dict = field(default_factory=dict)
    event_ms: int = -1
    receipt_ms: int = -1
    seq: int | None = None
    valid: bool = False
    sequenced: bool = False

    def update(self, e, now):
        ts = utc_ms(e['event_time'])
        if ts < self.event_ms:
            self.valid = False
            raise ValueError('out-of-sequence book timestamp')
        seq = e.get('seq')
        if e.get('sequence_verified') is True and (type(seq) is not int or seq < 0):
            self.valid = False
            raise ValueError('verified sequence requires an exchange sequence number')
        if e['type'] == 'snapshot' and seq is not None and self.seq is not None and seq <= self.seq:
            self.valid = False
            raise ValueError('non-increasing snapshot sequence')
        if e['type'] == 'delta' and (not self.valid or (self.seq is not None and seq != self.seq + 1)):
            self.valid = False
            raise ValueError('book sequence gap or missing snapshot')
        if e['type'] == 'snapshot':
            bids, asks = {}, {}
            for key, target in [('bids', bids), ('asks', asks)]:
                for p, q in e[key]:
                    p, q = D(p), D(q)
                    if p in target or not ZERO < p < ONE or q <= ZERO:
                        raise ValueError('invalid snapshot level')
                    target[p] = q
            self.bids, self.asks = bids, asks
        else:
            for side, p, q in e['changes']:
                if side not in ('BUY', 'SELL'):
                    raise ValueError('invalid book side')
                p, q = D(p), D(q)
                if not ZERO < p < ONE or q < ZERO:
                    raise ValueError('invalid delta level')
                target = self.bids if side == 'BUY' else self.asks
                if q == ZERO:
                    target.pop(p, None)
                else:
                    target[p] = q
        self.event_ms, self.receipt_ms, self.seq = ts, now, seq
        self.sequenced = e.get('sequence_verified', False) is True
        self.valid = bool(self.bids and self.asks and max(self.bids) < min(self.asks))
        if not self.valid:
            raise ValueError('empty or crossed book')

    def fresh(self, now, stale):
        return self.valid and 0 <= now - self.event_ms <= stale and now - self.receipt_ms <= stale

    def queue(self, price):
        return sum((q for p, q in self.bids.items() if p >= price), ZERO)


class MarketData:
    def __init__(self):
        self.markets = {}
        self.books = {}
        self.healthy = True
        self.reason = ''
        self.last_receipt = -1

    def apply(self, e):
        now, ts = utc_ms(e['receipt_time']), utc_ms(e['event_time'])
        if now < self.last_receipt or ts > now + 1000:
            raise ValueError('receipt ordering or clock synchronization failure')
        self.last_receipt = now
        kind = e['type']
        if kind == 'market':
            m = Market.parse(e)
            if m.id in self.markets:
                raise ValueError('duplicate market metadata')
            self.markets[m.id] = m
            for outcome in m.tokens:
                self.books[m.id, outcome] = Book()
        elif kind in ('snapshot', 'delta'):
            self.books[e['market'], e['outcome']].update(e, now)
        elif kind == 'tick':
            tick = D(e['tick'])
            if not ZERO < tick < ONE:
                raise ValueError('invalid tick')
            self.markets[e['market']].tick[e['outcome']] = tick
        elif kind == 'status':
            self.markets[e['market']].active = e['active'] is True
        elif kind == 'resolution':
            if e['winner'] not in ('UP', 'DOWN') or now < self.markets[e['market']].expiry:
                raise ValueError('invalid resolution')
            self.markets[e['market']].active = False
            self.markets[e['market']].resolved = True
        elif kind == 'disconnect':
            self.healthy, self.reason = False, 'disconnect: execution state uncertain'
            for b in self.books.values():
                b.valid = False
        elif kind not in ('trade', 'timer', 'cancel_failure', 'reconcile'):
            raise ValueError('unknown event type')
        return now

    def eligible(self, mid, now, stale, *, shadow=False):
        m = self.markets[mid]
        if not self.healthy:
            return 'network_or_execution_uncertain'
        if not m.active or m.resolved or not m.start <= now < m.expiry - 60000:
            return 'inactive_or_expiring'
        if not m.rules_verified or not m.fee_verified:
            return 'unverified_rules_or_fees'
        for o in m.tokens:
            b = self.books[mid, o]
            if not b.fresh(now, stale):
                return 'stale_missing_or_invalid_book'
            if shadow and not b.sequenced:
                return 'public_feed_sequence_unverifiable'
            if any(p % m.tick[o] for p in (*b.bids, *b.asks)):
                return 'off_tick_book'
        return ''

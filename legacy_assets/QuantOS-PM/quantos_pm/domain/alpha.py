from dataclasses import dataclass
from .common import D, ZERO


@dataclass(frozen=True)
class Intent:
    market: str
    outcome: str
    price: object
    size: object


class HedgedMaker:
    def propose(self, mid, md, features, config, now, equity):
        m = md.markets[mid]
        prices = [max(md.books[mid, o].bids) for o in ('UP', 'DOWN')]
        if sum(prices, ZERO) > D('.99'):
            return (), 'combined_price_filter', None
        # Size down; never round up to the venue minimum.
        size = min(config.quote_size, equity*D('.02')/max(prices)).quantize(D('.000001'), rounding='ROUND_DOWN')
        if size < m.minimum:
            return (), 'minimum_exceeds_risk_budget', None
        queues = [md.books[mid, o].queue(p) for o, p in zip(('UP', 'DOWN'), prices)]
        estimate = features.estimate(now, prices, queues, size, shadow=config.mode == 'shadow')
        if estimate is None or estimate['expected_net_lower'] <= 0:
            return (), 'no_positive_calibrated_lower_bound', estimate
        return tuple(Intent(mid, o, p, size) for o, p in zip(('UP', 'DOWN'), prices)), '', estimate

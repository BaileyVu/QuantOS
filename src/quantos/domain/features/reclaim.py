"""Bounded causal price range; no future-confirmed extrema or candle synthesis."""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ReclaimRange:
    low: Decimal
    high: Decimal
    first_ns: int
    last_ns: int
    first_book_id: int
    last_book_id: int


class ReclaimFeatures:
    def __init__(self):
        self.points = []
        self.last_ns = None
        self.reason = "range_warmup_300s"
        self.last_range_bps = None

    def observe(self, features, now_ns):
        if self.last_ns is not None and now_ns <= self.last_ns:
            raise ValueError("ambiguous reclaim clock")
        gap = self.last_ns is not None and now_ns - self.last_ns > 1_500_000_000
        if gap:
            self.points.clear()
        self.last_ns = now_ns
        cutoff = now_ns - 300_000_000_000
        while len(self.points) > 1 and self.points[1][0] <= cutoff:
            self.points.pop(0)
        result = None
        self.last_range_bps = None
        self.reason = "range_data_gap" if gap else "range_warmup_300s"
        if self.points and cutoff - 1_500_000_000 <= self.points[0][0] <= cutoff:
            prices = [point[1] for point in self.points]
            result = ReclaimRange(min(prices), max(prices), self.points[0][0],
                                  self.points[-1][0], self.points[0][2], self.points[-1][2])
            self.reason = "valid"
            self.last_range_bps = (result.high / result.low - 1) * Decimal(10000)
        point = (now_ns, features.mid_price, features.book_update_id)
        if self.points and self.points[-1][0] // 1_000_000_000 == now_ns // 1_000_000_000:
            self.points[-1] = point
        else:
            self.points.append(point)
        return result

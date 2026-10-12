"""Information-only observations; never an exchange order authority."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from quantos.domain.common import require_utc


@dataclass(frozen=True, slots=True)
class IntelligenceEvent:
    provider: str
    instrument: str
    market: str
    kind: str
    source_time: datetime
    received_at: datetime
    received_ns: int
    available_ns: int
    sequence: int
    provenance: str
    integrity: str
    valid: bool
    price: Decimal = Decimal("0")
    bid: Decimal = Decimal("0")
    ask: Decimal = Decimal("0")
    quantity: Decimal = Decimal("0")
    direction: int = 0

    def __post_init__(self):
        require_utc(self.source_time, "source_time")
        require_utc(self.received_at, "received_at")
        if not all((self.provider, self.instrument, self.market, self.provenance)):
            raise ValueError("missing observation identity")
        if self.kind not in {"leader_quote", "liquidation", "unavailable"}:
            raise ValueError("unsupported intelligence kind")
        if self.received_ns < 0 or self.available_ns < self.received_ns or self.sequence < 0:
            raise ValueError("ambiguous observation clock or sequence")
        if any(not value.is_finite() for value in (self.price, self.bid, self.ask, self.quantity)):
            raise ValueError("nonfinite observation")
        if self.valid and self.kind == "leader_quote" and not 0 < self.bid < self.ask:
            raise ValueError("crossed external quote")
        if self.valid and self.kind == "liquidation" and not (
            self.price > 0 and self.quantity > 0 and self.direction in {-1, 1}
        ):
            raise ValueError("invalid forced-flow observation")

    def freshness_reason(self, now_ns: int, maximum_age_ns: int = 2_000_000_000) -> str:
        if not self.valid or self.kind == "unavailable":
            return "provider_unavailable_or_invalid"
        if self.integrity not in {"monotone_snapshot", "sampled_not_complete"}:
            return "unverified_integrity"
        if now_ns < self.available_ns:
            return "not_yet_available"
        delay_us = (self.received_at - self.source_time) // timedelta(microseconds=1)
        if delay_us < 0:
            return "ambiguous_source_clock"
        if delay_us * 1000 > maximum_age_ns or now_ns - self.received_ns > maximum_age_ns:
            return "stale_observation"
        return "valid"

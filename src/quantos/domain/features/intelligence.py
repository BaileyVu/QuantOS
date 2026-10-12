"""Bounded causal information cache, independent of transport and provider."""
from dataclasses import dataclass
from decimal import Decimal

from quantos.domain.market_data.intelligence import IntelligenceEvent


@dataclass(frozen=True, slots=True)
class IntelligenceFeatures:
    leader_return_bps: Decimal | None
    leader_reason: str
    liquidation: IntelligenceEvent | None
    liquidation_reason: str
    leader_available_ns: int | None


class IntelligenceFeatureEngine:
    def __init__(self):
        self.observations: dict[str, list[IntelligenceEvent]] = {}
        self.last_sequence: dict[tuple[str, str, str], int] = {}
        self.last_available_ns = -1
        self.reasons: dict[str, int] = {}

    def ingest(self, event: IntelligenceEvent) -> str:
        key = (event.provider, event.instrument, event.kind)
        reason = event.freshness_reason(event.available_ns)
        if event.available_ns < self.last_available_ns:
            reason = "out_of_order_availability"
        previous = self.last_sequence.get(key)
        if previous is not None and event.sequence <= previous:
            reason = "duplicate" if event.sequence == previous else "out_of_order_sequence"
        self.reasons[reason] = self.reasons.get(reason, 0) + 1
        if reason != "valid":
            # Invalid evidence cannot leave a usable cached value behind.
            if reason != "duplicate":
                self.observations.pop(event.kind, None)
            return reason
        self.last_available_ns = event.available_ns
        self.last_sequence[key] = event.sequence
        rows = self.observations.setdefault(event.kind, [])
        rows.append(event)
        cutoff = event.available_ns - 12_000_000_000
        while len(rows) > 1 and (rows[1].available_ns < cutoff or len(rows) > 4096):
            rows.pop(0)
        return reason

    def invalidate(self):
        self.observations.clear()

    def compute(self, now_ns: int) -> IntelligenceFeatures:
        rows = self.observations.get("leader_quote", [])
        reason = "missing_leader"
        movement = None
        available = None
        if rows:
            latest = rows[-1]
            reason = latest.freshness_reason(now_ns)
            available = latest.available_ns
            cutoff = now_ns - 5_000_000_000
            bases = [row for row in rows if cutoff - 2_000_000_000 <= row.available_ns <= cutoff]
            if reason == "valid" and not bases:
                reason = "leader_warmup_5s"
            if reason == "valid":
                base = bases[-1]
                movement = ((latest.bid + latest.ask) / (base.bid + base.ask) - 1) * 10000
        forced = self.observations.get("liquidation", [])
        liquidation = forced[-1] if forced else None
        forced_reason = liquidation.freshness_reason(now_ns) if liquidation else "missing_liquidation"
        if forced_reason != "valid":
            liquidation = None
        return IntelligenceFeatures(movement, reason, liquidation, forced_reason, available)

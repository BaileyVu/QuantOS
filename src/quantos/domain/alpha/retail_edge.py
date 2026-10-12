"""Two fixed experimental mechanisms. Observed moves are not calibrated forecasts."""
from dataclasses import dataclass
from decimal import Decimal

from quantos.domain.alpha.hft import HftSide
from quantos.domain.alpha.retail import RetailSignal
from quantos.domain.features.hft import HftFeatures
from quantos.domain.features.intelligence import IntelligenceFeatures

D = Decimal


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    strategy: str
    reason: str
    raw_candidate: bool = False
    signal: RetailSignal | None = None


class RetailEdgeAlpha:
    strategies = ("Q04-LL-v1", "Q04-LD-v1")

    def __init__(self):
        self.mids: list[tuple[int, Decimal]] = []

    def observe(self, features: HftFeatures, external: IntelligenceFeatures,
                now_ns: int) -> tuple[CandidateDecision, ...]:
        if self.mids and now_ns <= self.mids[-1][0]:
            raise ValueError("ambiguous Alpha availability clock")
        self.mids.append((now_ns, features.mid_price))
        cutoff = now_ns - 5_000_000_000
        while len(self.mids) > 1 and self.mids[1][0] <= cutoff:
            self.mids.pop(0)
        if not cutoff - 2_000_000_000 <= self.mids[0][0] <= cutoff:
            return tuple(CandidateDecision(s, "local_warmup_5s") for s in self.strategies)
        response = (features.mid_price / self.mids[0][1] - 1) * 10000
        if features.realized_volatility_bps > D("20") or features.spread_bps > D("10"):
            return tuple(CandidateDecision(s, "dislocated_or_wide_spread_regime") for s in self.strategies)
        return (self._lead_lag(features, external, response, now_ns),
                self._liquidation(features, external, response, now_ns))

    def _lead_lag(self, f, e, response, ns):
        strategy = self.strategies[0]
        if e.leader_return_bps is None or e.leader_reason != "valid":
            return CandidateDecision(strategy, e.leader_reason)
        if e.leader_available_ns is None or e.leader_available_ns >= ns:
            return CandidateDecision(strategy, "leader_not_available_before_target")
        leader = e.leader_return_bps
        gap = leader - response
        if abs(leader) < D("2") or gap * leader <= 0 or abs(gap) < D("2"):
            return CandidateDecision(strategy, "no_leading_displacement")
        # Relative returns avoid treating a USD/stablecoin level basis as alpha.
        expected = min(abs(leader), abs(gap))
        return self._confirm(strategy, f, 1 if gap > 0 else -1, expected, response, ns)

    def _liquidation(self, f, e, response, ns):
        strategy = self.strategies[1]
        event = e.liquidation
        if event is None:
            return CandidateDecision(strategy, e.liquidation_reason)
        if event.available_ns >= ns:
            return CandidateDecision(strategy, "forced_flow_not_available_before_target")
        visible = f.best_ask_quantity if event.direction > 0 else f.best_bid_quantity
        if event.quantity < visible * 2:
            return CandidateDecision(strategy, "forced_flow_below_visible_liquidity")
        if response * event.direction < D("2"):
            return CandidateDecision(strategy, "no_forced_price_response")
        return self._confirm(strategy, f, event.direction, abs(response), response, ns)

    def _confirm(self, strategy, f, direction, expected, response, ns):
        if f.static_obi * direction < D("0.15") or f.signed_trade_flow * direction <= 0:
            return CandidateDecision(strategy, "flow_or_imbalance_not_confirmed", True)
        side = HftSide.BUY if direction > 0 else HftSide.SELL
        signal = RetailSignal(
            f"{strategy}:{f.timestamp.isoformat()}:{f.book_update_id}:{side.value}",
            side, expected, response, f.book_update_id, ns, ns + 2_000_000_000,
            strategy_id=strategy, uncertainty_bps=max(D("2"), expected / 2),
            horizon_seconds=30,
        )
        return CandidateDecision(strategy, "confirmed", True, signal)

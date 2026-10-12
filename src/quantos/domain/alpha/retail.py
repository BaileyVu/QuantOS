"""Experimental causal directional signal for the separate retail paper path."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantos.domain.alpha.hft import HftSide
from quantos.domain.features.hft import HftFeatures


@dataclass(frozen=True, slots=True)
class RetailSignal:
    signal_id: str
    side: HftSide
    expected_move_bps: Decimal
    response_bps: Decimal
    book_update_id: int
    observed_ns: int
    expires_ns: int
    strategy_id: str = "Q03-directional-v1"
    uncertainty_bps: Decimal = Decimal("0")
    horizon_seconds: int = 60


class RetailDirectionalAlpha:
    """30-second observed response confirmed by 5-second flow and current OBI."""

    def __init__(self) -> None:
        self._mids: list[tuple[int, Decimal]] = []
        self.last_reason = "warmup_30s"
        self.raw_candidate = False

    def observe(self, features: HftFeatures, observed_ns: int) -> RetailSignal | None:
        self.raw_candidate = False
        self.last_reason = "warmup_30s"
        if observed_ns < 0 or (self._mids and observed_ns <= self._mids[-1][0]):
            raise ValueError("retail signal clock is ambiguous")
        self._mids.append((observed_ns, features.mid_price))
        cutoff = observed_ns - 30_000_000_000
        while len(self._mids) > 1 and self._mids[1][0] <= cutoff:
            self._mids.pop(0)
        baseline_ns, baseline = self._mids[0]
        if baseline_ns > cutoff or baseline_ns < cutoff - 2_000_000_000:
            return None
        response = (features.mid_price - baseline) / baseline * Decimal("10000")
        side = (
            HftSide.BUY if response >= Decimal("2")
            else HftSide.SELL if response <= Decimal("-2")
            else None
        )
        if side is None:
            self.last_reason = "response_below_2bps"
            return None
        self.raw_candidate = True
        self.last_reason = "flow_or_imbalance_not_confirmed"
        if side is HftSide.BUY and not (
            features.static_obi >= Decimal("0.15")
            and features.signed_trade_flow > 0
        ):
            return None
        if side is HftSide.SELL and not (
            features.static_obi <= Decimal("-0.15")
            and features.signed_trade_flow < 0
        ):
            return None
        self.last_reason = "confirmed"
        identity = f"{features.timestamp.isoformat()}:{features.book_update_id}:{side.value}"
        return RetailSignal(
            f"qos03-{identity}", side, abs(response), response,
            features.book_update_id, observed_ns, observed_ns + 2_000_000_000,
        )


@dataclass(frozen=True, slots=True)
class ProtectedRetailSignal(RetailSignal):
    """Future Alpha boundary: explicit absolute exits; None means NO_TRADE."""
    protective_stop_price: Decimal = Decimal("0")
    take_profit_price: Decimal = Decimal("0")

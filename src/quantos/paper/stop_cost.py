"""Stressed stop pricing adapted from HyperGrok scripts/stop_cost.py::price_stop.

See licenses/HyperGrok-MIT.txt for the upstream MIT notice and provenance.
Upstream commit: 01c2a46c8a36656a2f958d9063201a77556cae5d (Galleon Labs).
Rates are fractions, slippage is a price amount, and quantities use Binance's
combined LOT_SIZE/MARKET_LOT_SIZE step rather than decimal-place rounding.
"""
from dataclasses import dataclass
from decimal import Decimal

from .types import D, floor_step


@dataclass(frozen=True)
class StopCost:
    stop_fill: Decimal
    fees_per_unit: Decimal
    loss_per_unit: Decimal

    def quantity(self, risk: Decimal, step: Decimal) -> Decimal:
        if not risk.is_finite() or risk < 0 or not step.is_finite() or step <= 0:
            raise ValueError("invalid risk budget or quantity step")
        return floor_step(risk / self.loss_per_unit, step)


def price_stop(entry: Decimal, stop: Decimal, side: str, slippage: Decimal,
               entry_fee: Decimal, exit_fee: Decimal) -> StopCost:
    """Price both fee legs at their respective fills; never assume a stop fills at trigger."""
    if side not in {"LONG", "SHORT"}:
        raise ValueError("invalid side")
    if any(not x.is_finite() or x <= 0 for x in (entry, stop)):
        raise ValueError("invalid price")
    if any(not x.is_finite() or x < 0 for x in (slippage, entry_fee, exit_fee)):
        raise ValueError("invalid execution costs")
    direction = D(1) if side == "LONG" else D(-1)
    stop_fill = stop - direction * slippage
    if stop_fill <= 0:
        raise ValueError("unpriceable stop")
    fees = entry * entry_fee + stop_fill * exit_fee
    # Tightened profitable stops carry fees but do not turn locked profit into risk.
    loss = max(D(0), (entry - stop_fill) * direction) + fees
    if loss <= 0:
        raise ValueError("nonpositive stressed loss")
    return StopCost(stop_fill, fees, loss)

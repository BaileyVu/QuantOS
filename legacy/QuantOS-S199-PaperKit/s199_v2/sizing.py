from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Iterable

from .fees import FeeMetadata, taker_fee


@dataclass(frozen=True)
class AskLevel:
    price: Decimal
    shares: Decimal


@dataclass(frozen=True)
class FillPlan:
    requested_shares: Decimal
    executable_shares: Decimal
    quote_cost: Decimal
    fee: Decimal
    cash_debit: Decimal
    average_price: Decimal | None
    complete: bool
    reason: str | None


def _floor(value: Decimal, increment: Decimal) -> Decimal:
    if increment <= 0:
        raise ValueError("quantity_increment must be positive")
    return (value / increment).to_integral_value(rounding=ROUND_DOWN) * increment


def plan_market_buy(
    *,
    target_shares: Decimal | str,
    asks: Iterable[AskLevel],
    available_cash: Decimal | str,
    minimum_shares: Decimal | str,
    quantity_increment: Decimal | str,
    fee_metadata: FeeMetadata,
    allow_partial: bool = True,
) -> FillPlan:
    """Size a base-denominated IOC buy without inventing liquidity."""
    target = _floor(Decimal(str(target_shares)), Decimal(str(quantity_increment)))
    cash = Decimal(str(available_cash))
    minimum = Decimal(str(minimum_shares))
    if target <= 0 or cash < 0:
        raise ValueError("target_shares must be positive and cash non-negative")
    levels = sorted(
        (AskLevel(Decimal(str(x.price)), Decimal(str(x.shares))) for x in asks),
        key=lambda x: x.price,
    )
    if any(x.price <= 0 or x.price > 1 or x.shares <= 0 for x in levels):
        raise ValueError("invalid ask level")

    fills: list[tuple[Decimal, Decimal]] = []
    remaining = target
    for level in levels:
        take = _floor(min(remaining, level.shares), Decimal(str(quantity_increment)))
        if take <= 0:
            continue
        # Reduce the level until cost plus the price-dependent fee is affordable.
        while take > 0:
            trial = fills + [(level.price, take)]
            shares = sum(q for _, q in trial)
            quote = sum(p * q for p, q in trial)
            fee = sum(taker_fee(shares=q, price=p, metadata=fee_metadata) for p, q in trial)
            if quote + fee <= cash:
                break
            take = _floor(take - Decimal(str(quantity_increment)), Decimal(str(quantity_increment)))
        if take <= 0:
            break
        fills.append((level.price, take))
        remaining -= take
        if remaining <= 0:
            break

    executed = sum(q for _, q in fills)
    quote_cost = sum(p * q for p, q in fills)
    fee = sum(taker_fee(shares=q, price=p, metadata=fee_metadata) for p, q in fills)
    if executed < minimum:
        return FillPlan(target, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), None, False, "venue_minimum")
    if executed < target and not allow_partial:
        return FillPlan(target, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), None, False, "insufficient_liquidity_or_cash")
    average = quote_cost / executed if executed else None
    return FillPlan(
        target, executed, quote_cost, fee, quote_cost + fee, average,
        executed == target,
        None if executed == target else "partial_liquidity_or_cash",
    )

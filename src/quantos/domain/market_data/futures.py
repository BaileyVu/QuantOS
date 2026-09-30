"""Provider-independent USD-M Futures symbol rules."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from typing import Any, Mapping


class FuturesRuleError(ValueError):
    """An exchange rule is missing, invalid, or violated."""


def _decimal(value: Any, name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as error:
        raise FuturesRuleError(f"invalid {name}") from error
    if not result.is_finite():
        raise FuturesRuleError(f"invalid {name}")
    return result


@dataclass(frozen=True, slots=True)
class FuturesSymbolRules:
    symbol: str
    status: str
    quantity_step: Decimal
    minimum_quantity: Decimal
    maximum_quantity: Decimal
    price_tick: Decimal
    minimum_price: Decimal
    maximum_price: Decimal
    minimum_notional: Decimal

    def __post_init__(self) -> None:
        if self.symbol != "BTCUSDT":
            raise FuturesRuleError("V1 supports BTCUSDT only")
        for name in ("quantity_step", "minimum_quantity", "maximum_quantity",
                     "price_tick", "minimum_price", "maximum_price", "minimum_notional"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise FuturesRuleError(f"{name} must be a positive Decimal")
        if self.minimum_quantity > self.maximum_quantity:
            raise FuturesRuleError("invalid quantity range")
        if self.minimum_price > self.maximum_price:
            raise FuturesRuleError("invalid price range")

    def round_quantity(self, quantity: Decimal) -> Decimal:
        if not isinstance(quantity, Decimal) or not quantity.is_finite() or quantity <= 0:
            raise FuturesRuleError("quantity must be a positive Decimal")
        units = (quantity / self.quantity_step).to_integral_value(rounding=ROUND_DOWN)
        return units * self.quantity_step

    def round_quantity_up(self, quantity: Decimal) -> Decimal:
        if not isinstance(quantity, Decimal) or not quantity.is_finite() or quantity <= 0:
            raise FuturesRuleError("quantity must be a positive Decimal")
        units = (quantity / self.quantity_step).to_integral_value(
            rounding=ROUND_CEILING
        )
        return units * self.quantity_step

    def round_price(self, price: Decimal) -> Decimal:
        if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
            raise FuturesRuleError("price must be a positive Decimal")
        units = (price / self.price_tick).to_integral_value(rounding=ROUND_DOWN)
        return units * self.price_tick

    def validate_market_order(self, quantity: Decimal, reference_price: Decimal) -> None:
        if self.status != "TRADING":
            raise FuturesRuleError(f"{self.symbol} is not TRADING")
        if self.round_quantity(quantity) != quantity:
            raise FuturesRuleError("quantity is not representable by LOT_SIZE")
        if quantity < self.minimum_quantity or quantity > self.maximum_quantity:
            raise FuturesRuleError("quantity violates LOT_SIZE")
        if reference_price < self.minimum_price or reference_price > self.maximum_price:
            raise FuturesRuleError("reference price violates PRICE_FILTER")
        if quantity * reference_price < self.minimum_notional:
            raise FuturesRuleError("order violates minimum notional")


def parse_usdm_exchange_info(payload: Mapping[str, Any], symbol: str = "BTCUSDT") -> FuturesSymbolRules:
    if not isinstance(payload, Mapping):
        raise FuturesRuleError("exchangeInfo must be an object")
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        raise FuturesRuleError("exchangeInfo symbols are missing")
    item = next((row for row in symbols if isinstance(row, Mapping) and row.get("symbol") == symbol), None)
    if item is None:
        raise FuturesRuleError(f"{symbol} is absent from exchangeInfo")
    if item.get("contractType") not in (None, "PERPETUAL"):
        raise FuturesRuleError(f"{symbol} is not a perpetual contract")
    raw_filters = item.get("filters")
    if not isinstance(raw_filters, list):
        raise FuturesRuleError("symbol filters are missing")
    filters = {row.get("filterType"): row for row in raw_filters if isinstance(row, Mapping)}
    try:
        lot = filters["LOT_SIZE"]
        price = filters["PRICE_FILTER"]
        notional = filters["MIN_NOTIONAL"]
    except KeyError as error:
        raise FuturesRuleError(f"required filter missing: {error.args[0]}") from error
    return FuturesSymbolRules(
        symbol=symbol,
        status=str(item.get("status", "")),
        quantity_step=_decimal(lot.get("stepSize"), "stepSize"),
        minimum_quantity=_decimal(lot.get("minQty"), "minQty"),
        maximum_quantity=_decimal(lot.get("maxQty"), "maxQty"),
        price_tick=_decimal(price.get("tickSize"), "tickSize"),
        minimum_price=_decimal(price.get("minPrice"), "minPrice"),
        maximum_price=_decimal(price.get("maxPrice"), "maxPrice"),
        minimum_notional=_decimal(notional.get("notional"), "notional"),
    )


"""Futures-specific risk sizing and leverage approval."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from hashlib import sha256

from quantos.domain.alpha.futures import Direction, TradeCandidate
from quantos.domain.market_data.futures import FuturesRuleError, FuturesSymbolRules


@dataclass(frozen=True, slots=True)
class FuturesRiskPolicy:
    risk_fraction: Decimal = Decimal(".01")
    maximum_risk_fraction: Decimal = Decimal(".02")
    daily_loss_fraction: Decimal = Decimal(".03")
    consecutive_loss_limit: int = 3
    margin_fraction: Decimal = Decimal(".8")
    leverage_ceiling: int = 5
    emergency_leverage_ceiling: int = 10
    liquidation_buffer_fraction: Decimal = Decimal(".01")
    maintenance_margin_fraction: Decimal = Decimal(".005")
    maximum_slippage_rate: Decimal = Decimal(".0002")
    maximum_fee_rate: Decimal = Decimal(".0005")

    def __post_init__(self) -> None:
        decimals = ("risk_fraction", "maximum_risk_fraction", "daily_loss_fraction",
                    "margin_fraction", "liquidation_buffer_fraction",
                    "maintenance_margin_fraction", "maximum_slippage_rate",
                    "maximum_fee_rate")
        if any(not isinstance(getattr(self, n), Decimal) for n in decimals):
            raise ValueError("risk policy values must use Decimal")
        if not (Decimal(0) < self.risk_fraction <= self.maximum_risk_fraction < Decimal(1)):
            raise ValueError("invalid risk fractions")
        if not (Decimal(0) < self.margin_fraction <= Decimal(1)):
            raise ValueError("invalid margin fraction")
        if self.leverage_ceiling < 1 or self.leverage_ceiling > self.emergency_leverage_ceiling:
            raise ValueError("invalid leverage ceilings")
        if self.consecutive_loss_limit < 1:
            raise ValueError("invalid consecutive loss limit")
        if any(getattr(self, name) < 0 or getattr(self, name) >= 1
               for name in ("maximum_slippage_rate", "maximum_fee_rate")):
            raise ValueError("invalid execution cost bounds")


@dataclass(frozen=True, slots=True)
class FuturesAccountState:
    equity: Decimal
    day_start_equity: Decimal
    consecutive_losses: int = 0
    has_position: bool = False
    reconciled: bool = True


@dataclass(frozen=True, slots=True)
class FuturesRiskDecision:
    approved: bool
    reason: str
    quantity: Decimal | None = None
    leverage: int | None = None
    notional: Decimal | None = None
    allocated_margin: Decimal | None = None
    risk_amount: Decimal | None = None
    liquidation_price: Decimal | None = None
    client_order_id: str | None = None


def _reject(reason: str) -> FuturesRiskDecision:
    return FuturesRiskDecision(False, reason)


def evaluate_futures_risk(candidate: TradeCandidate | None, account: FuturesAccountState,
                          rules: FuturesSymbolRules,
                          policy: FuturesRiskPolicy = FuturesRiskPolicy()) -> FuturesRiskDecision:
    if candidate is None:
        return _reject("no trade candidate")
    if not account.reconciled:
        return _reject("account state is not reconciled")
    if account.equity <= 0 or account.day_start_equity <= 0:
        return _reject("account equity is invalid")
    if account.has_position:
        return _reject("one-position rule")
    if account.consecutive_losses >= policy.consecutive_loss_limit:
        return _reject("consecutive-loss circuit breaker")
    loss = max(Decimal(0), account.day_start_equity - account.equity)
    if loss >= account.day_start_equity * policy.daily_loss_fraction:
        return _reject("daily-loss circuit breaker")

    signal = candidate.signal
    if signal.direction is Direction.LONG and signal.stop >= signal.entry:
        return _reject("LONG stop must be below entry")
    if signal.direction is Direction.SHORT and signal.stop <= signal.entry:
        return _reject("SHORT stop must be above entry")
    if signal.direction is Direction.HOLD:
        return _reject("HOLD is not executable")
    if signal.direction is Direction.LONG:
        risk_entry = signal.entry * (Decimal(1) + policy.maximum_slippage_rate)
        risk_exit = signal.stop * (Decimal(1) - policy.maximum_slippage_rate)
        price_loss = risk_entry - risk_exit
    else:
        risk_entry = signal.entry * (Decimal(1) - policy.maximum_slippage_rate)
        risk_exit = signal.stop * (Decimal(1) + policy.maximum_slippage_rate)
        price_loss = risk_exit - risk_entry
    loss_per_unit = price_loss + (
        risk_entry + risk_exit
    ) * policy.maximum_fee_rate
    if loss_per_unit <= 0:
        return _reject("stop distance must be positive")

    risk_budget = account.equity * min(policy.risk_fraction, policy.maximum_risk_fraction)
    try:
        quantity = rules.round_quantity(risk_budget / loss_per_unit)
    except FuturesRuleError as error:
        return _reject(str(error))
    if quantity <= 0:
        return _reject("risk-sized quantity rounds to zero")
    notional = quantity * risk_entry
    try:
        rules.validate_market_order(quantity, risk_entry)
    except FuturesRuleError as error:
        return _reject(str(error))

    available_margin = account.equity * policy.margin_fraction
    required = (notional / available_margin).to_integral_value(rounding=ROUND_CEILING)
    leverage = max(1, int(required))
    if leverage > policy.emergency_leverage_ceiling:
        return _reject("required leverage exceeds emergency ceiling")
    if leverage > policy.leverage_ceiling:
        return _reject("required leverage exceeds configured ceiling")
    allocated_margin = notional / Decimal(leverage)

    if signal.direction is Direction.LONG:
        liquidation = risk_entry * (Decimal(1) - Decimal(1) / Decimal(leverage)
                                      + policy.maintenance_margin_fraction)
        safe = liquidation <= signal.stop * (Decimal(1) - policy.liquidation_buffer_fraction)
    else:
        liquidation = risk_entry * (Decimal(1) + Decimal(1) / Decimal(leverage)
                                      - policy.maintenance_margin_fraction)
        safe = liquidation >= signal.stop * (Decimal(1) + policy.liquidation_buffer_fraction)
    if not safe:
        return _reject("liquidation buffer is insufficient")

    identity = "|".join((signal.symbol, str(signal.timestamp), signal.strategy_id,
                         signal.direction.value))
    client_id = "qv1-" + sha256(identity.encode("utf-8")).hexdigest()[:24]
    return FuturesRiskDecision(True, "approved", quantity, leverage, notional,
                               allocated_margin, quantity * loss_per_unit,
                               liquidation, client_id)


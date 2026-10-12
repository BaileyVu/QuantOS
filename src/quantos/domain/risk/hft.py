"""Fee-aware bounded inventory admission for HFT paper execution."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantos.domain.alpha.hft import HftIntentAction, HftQuoteIntent
from quantos.domain.features.hft import HftFeatures
from quantos.domain.market_data.futures import FuturesRuleError, FuturesSymbolRules


BPS = Decimal("10000")


@dataclass(frozen=True, slots=True)
class HftFeeSchedule:
    maker_rate: Decimal
    taker_rate: Decimal

    def __post_init__(self) -> None:
        for name in ("maker_rate", "taker_rate"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{name} must be a finite Decimal")
            if value < Decimal("-0.001") or value > Decimal("0.01"):
                raise ValueError(f"{name} is outside conservative bounds")


@dataclass(frozen=True, slots=True)
class HftRiskPolicy:
    maximum_leverage: Decimal = Decimal("5")
    maximum_inventory_notional: Decimal = Decimal("100")
    maximum_position_quantity: Decimal = Decimal("1")
    maximum_account_loss_fraction: Decimal = Decimal("0.01")
    daily_loss_fraction: Decimal = Decimal("0.03")
    adverse_fill_limit: int = 5
    adverse_selection_bps: Decimal = Decimal("0.5")
    safety_buffer_bps: Decimal = Decimal("0.5")
    emergency_move_bps: Decimal = Decimal("20")
    funding_allowance_bps: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.maximum_leverage <= 0 or self.maximum_leverage > 5:
            raise ValueError("maximum_leverage must be in (0, 5]")
        if not Decimal("0") < self.maximum_account_loss_fraction <= Decimal("0.01"):
            raise ValueError("normal HFT account risk may not exceed 1%")
        if self.adverse_fill_limit < 1:
            raise ValueError("adverse_fill_limit must be positive")


@dataclass(frozen=True, slots=True)
class HftCostHurdle:
    maker_entry_bps: Decimal
    maker_exit_bps: Decimal
    normal_fee_bps: Decimal
    emergency_taker_bps: Decimal
    spread_bps: Decimal
    adverse_selection_bps: Decimal
    funding_bps: Decimal
    safety_buffer_bps: Decimal
    normal_hurdle_bps: Decimal
    emergency_loss_hurdle_bps: Decimal

    @property
    def expected_round_trip_bps(self) -> Decimal:
        """Backward-compatible name for the normal maker lifecycle hurdle."""
        return self.normal_hurdle_bps

    @property
    def emergency_round_trip_bps(self) -> Decimal:
        """Backward-compatible name for the emergency loss hurdle."""
        return self.emergency_loss_hurdle_bps

    @property
    def admission_hurdle_bps(self) -> Decimal:
        """Only normal maker economics govern passive quote admission."""
        return self.normal_hurdle_bps


@dataclass(frozen=True, slots=True)
class HftRiskState:
    starting_equity: Decimal
    realized_pnl: Decimal = Decimal("0")
    mark_to_market_loss: Decimal = Decimal("0")
    consecutive_adverse_fills: int = 0
    kill_switch: bool = False


@dataclass(frozen=True, slots=True)
class HftRiskDecision:
    approved: bool
    reason: str
    quantity: Decimal
    notional: Decimal
    leverage: Decimal
    hurdle: HftCostHurdle


def cost_hurdle(
    fees: HftFeeSchedule,
    features: HftFeatures,
    policy: HftRiskPolicy,
) -> HftCostHurdle:
    maker = fees.maker_rate * BPS
    taker = fees.taker_rate * BPS
    normal_fees = maker + maker
    normal = (
        normal_fees + policy.adverse_selection_bps
        + policy.funding_allowance_bps + policy.safety_buffer_bps
    )
    emergency_loss = maker + taker + policy.emergency_move_bps
    return HftCostHurdle(
        maker_entry_bps=maker,
        maker_exit_bps=maker,
        normal_fee_bps=normal_fees,
        emergency_taker_bps=taker,
        spread_bps=features.spread_bps,
        adverse_selection_bps=policy.adverse_selection_bps,
        funding_bps=policy.funding_allowance_bps,
        safety_buffer_bps=policy.safety_buffer_bps,
        normal_hurdle_bps=normal,
        emergency_loss_hurdle_bps=emergency_loss,
    )


class HftRiskEngine:
    def __init__(
        self,
        policy: HftRiskPolicy,
        fees: HftFeeSchedule,
        rules: FuturesSymbolRules,
    ) -> None:
        self.policy = policy
        self.fees = fees
        self.rules = rules

    def evaluate(
        self,
        intent: HftQuoteIntent,
        features: HftFeatures,
        state: HftRiskState,
        equity: Decimal,
        inventory_quantity: Decimal = Decimal("0"),
    ) -> HftRiskDecision:
        hurdle = cost_hurdle(self.fees, features, self.policy)
        rejected = lambda reason: HftRiskDecision(
            False, reason, Decimal("0"), Decimal("0"), Decimal("0"), hurdle
        )
        if intent.action is not HftIntentAction.QUOTE:
            return rejected("not_new_exposure")
        if self.rules.status != "TRADING":
            return rejected("symbol_not_trading")
        if state.kill_switch:
            return rejected("kill_switch")
        if state.realized_pnl <= -(state.starting_equity * self.policy.daily_loss_fraction):
            return rejected("daily_loss_breaker")
        if state.consecutive_adverse_fills >= self.policy.adverse_fill_limit:
            return rejected("adverse_fill_breaker")
        if state.mark_to_market_loss >= equity * self.policy.maximum_account_loss_fraction:
            return rejected("account_loss_breaker")
        if inventory_quantity != 0:
            return rejected("inventory_already_open")
        if intent.price is None or intent.expected_alpha_bps <= hurdle.normal_hurdle_bps:
            return rejected("economic_hurdle")
        raw = max(
            self.rules.minimum_quantity,
            self.rules.minimum_notional / intent.price,
        )
        quantity = self.rules.round_quantity_up(raw)
        quantity = min(quantity, self.policy.maximum_position_quantity)
        notional = quantity * intent.price
        try:
            self.rules.validate_market_order(quantity, intent.price)
        except FuturesRuleError:
            return rejected("exchange_filter")
        if notional > self.policy.maximum_inventory_notional:
            return rejected("inventory_notional_ceiling")
        leverage = notional / equity if equity > 0 else Decimal("Infinity")
        if leverage > self.policy.maximum_leverage:
            return rejected("leverage_ceiling")
        worst_loss = notional * (hurdle.emergency_loss_hurdle_bps / BPS)
        if worst_loss > equity * self.policy.maximum_account_loss_fraction:
            return rejected("one_percent_account_risk")
        return HftRiskDecision(True, "approved", quantity, notional, leverage, hurdle)

"""Futures-specific risk sizing and leverage approval."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
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
    consecutive_loss_cooldown_minutes: int = 240
    consecutive_loss_reset_on_utc_day: bool = True
    margin_fraction: Decimal = Decimal(".8")
    leverage_ceiling: int = 5
    emergency_leverage_ceiling: int = 10
    liquidation_buffer_fraction: Decimal = Decimal(".01")
    maintenance_margin_fraction: Decimal = Decimal(".005")
    maximum_slippage_rate: Decimal = Decimal(".0002")
    maximum_fee_rate: Decimal = Decimal(".0005")
    minimum_net_reward_risk: Decimal = Decimal("1.5")
    minimum_reward_to_cost_multiple: Decimal = Decimal("3")
    minimum_expected_movement_to_cost_multiple: Decimal = Decimal("3")

    def __post_init__(self) -> None:
        decimals = ("risk_fraction", "maximum_risk_fraction", "daily_loss_fraction",
                    "margin_fraction", "liquidation_buffer_fraction",
                    "maintenance_margin_fraction", "maximum_slippage_rate",
                    "maximum_fee_rate", "minimum_net_reward_risk",
                    "minimum_reward_to_cost_multiple",
                    "minimum_expected_movement_to_cost_multiple")
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
        if self.consecutive_loss_cooldown_minutes < 1:
            raise ValueError("consecutive-loss cooldown must be positive")
        if not isinstance(self.consecutive_loss_reset_on_utc_day, bool):
            raise ValueError("UTC-day breaker reset flag must be boolean")
        if any(getattr(self, name) < 0 or getattr(self, name) >= 1
               for name in ("maximum_slippage_rate", "maximum_fee_rate")):
            raise ValueError("invalid execution cost bounds")
        if self.minimum_net_reward_risk <= 0:
            raise ValueError("minimum net reward:risk must be positive")
        if self.minimum_reward_to_cost_multiple <= 0:
            raise ValueError("minimum reward-to-cost multiple must be positive")
        if self.minimum_expected_movement_to_cost_multiple <= 0:
            raise ValueError("minimum expected-movement-to-cost multiple must be positive")


@dataclass(frozen=True, slots=True)
class FuturesAccountState:
    equity: Decimal
    day_start_equity: Decimal
    consecutive_losses: int = 0
    consecutive_loss_breaker_active: bool = False
    has_position: bool = False
    reconciled: bool = True


@dataclass(frozen=True, slots=True)
class CandidateEconomics:
    approved: bool
    reason: str
    rejection_category: str | None
    minimum_executable_quantity: Decimal
    approved_quantity: Decimal
    estimated_entry_price: Decimal
    estimated_stop_exit_price: Decimal
    estimated_target_exit_price: Decimal
    notional: Decimal
    margin_required: Decimal
    leverage_required: int
    raw_stop_loss: Decimal
    estimated_entry_cost: Decimal
    estimated_exit_cost: Decimal
    estimated_slippage: Decimal
    total_execution_costs: Decimal
    cost_adjusted_stop_loss: Decimal
    expected_gross_reward: Decimal
    expected_gross_loss: Decimal
    expected_net_reward: Decimal
    gross_reward_risk: Decimal
    net_reward_risk: Decimal
    reward_to_execution_cost_multiple: Decimal
    risk_budget: Decimal
    expected_movement: Decimal = Decimal(0)
    expected_movement_to_execution_cost_multiple: Decimal = Decimal(0)
    expected_movement_rate: Decimal = Decimal(0)
    estimated_round_trip_cost_rate: Decimal = Decimal(0)
    passes_exchange_filter: bool = False
    passes_cost_filter: bool = False
    passes_expected_movement_filter: bool = False
    passes_net_reward_risk_filter: bool = False
    passes_minimum_executable_risk_filter: bool = False
    passes_leverage_filter: bool = False


def _empty_economics(reason: str, category: str) -> CandidateEconomics:
    zero = Decimal(0)
    return CandidateEconomics(
        approved=False,
        reason=reason,
        rejection_category=category,
        minimum_executable_quantity=zero,
        approved_quantity=zero,
        estimated_entry_price=zero,
        estimated_stop_exit_price=zero,
        estimated_target_exit_price=zero,
        notional=zero,
        margin_required=zero,
        leverage_required=0,
        raw_stop_loss=zero,
        estimated_entry_cost=zero,
        estimated_exit_cost=zero,
        estimated_slippage=zero,
        total_execution_costs=zero,
        cost_adjusted_stop_loss=zero,
        expected_gross_reward=zero,
        expected_gross_loss=zero,
        expected_net_reward=zero,
        gross_reward_risk=zero,
        net_reward_risk=zero,
        reward_to_execution_cost_multiple=zero,
        risk_budget=zero,
    )


def assess_candidate_economics(
    candidate: TradeCandidate,
    account: FuturesAccountState,
    rules: FuturesSymbolRules,
    policy: FuturesRiskPolicy,
) -> CandidateEconomics:
    signal = candidate.signal
    if account.equity <= 0:
        return _empty_economics("account equity is invalid", "minimum_executable_risk")
    if signal.direction is Direction.LONG:
        if not (signal.stop < signal.entry < signal.target):
            return _empty_economics("LONG stop/target geometry is invalid", "cost")
        entry_fill = signal.entry * (Decimal(1) + policy.maximum_slippage_rate)
        stop_fill = signal.stop * (Decimal(1) - policy.maximum_slippage_rate)
        target_fill = signal.target * (Decimal(1) - policy.maximum_slippage_rate)
        raw_loss_per_unit = signal.entry - signal.stop
        raw_reward_per_unit = signal.target - signal.entry
    elif signal.direction is Direction.SHORT:
        if not (signal.target < signal.entry < signal.stop):
            return _empty_economics("SHORT stop/target geometry is invalid", "cost")
        entry_fill = signal.entry * (Decimal(1) - policy.maximum_slippage_rate)
        stop_fill = signal.stop * (Decimal(1) + policy.maximum_slippage_rate)
        target_fill = signal.target * (Decimal(1) + policy.maximum_slippage_rate)
        raw_loss_per_unit = signal.stop - signal.entry
        raw_reward_per_unit = signal.entry - signal.target
    else:
        return _empty_economics("HOLD is not executable", "cost")

    entry_slippage_per_unit = abs(entry_fill - signal.entry)
    stop_slippage_per_unit = abs(stop_fill - signal.stop)
    target_slippage_per_unit = abs(target_fill - signal.target)
    entry_fee_per_unit = entry_fill * policy.maximum_fee_rate
    stop_fee_per_unit = stop_fill * policy.maximum_fee_rate
    target_fee_per_unit = target_fill * policy.maximum_fee_rate
    loss_per_unit = (
        raw_loss_per_unit + entry_slippage_per_unit + stop_slippage_per_unit
        + entry_fee_per_unit + stop_fee_per_unit
    )
    if loss_per_unit <= 0 or raw_reward_per_unit <= 0:
        return _empty_economics("candidate has non-positive reward or loss", "cost")

    try:
        minimum_quantity = rules.round_quantity_up(max(
            rules.minimum_quantity,
            rules.minimum_notional / entry_fill,
        ))
        rules.validate_market_order(minimum_quantity, entry_fill)
    except FuturesRuleError as error:
        return _empty_economics(str(error), "exchange_filters")

    risk_budget = account.equity * min(
        policy.risk_fraction, policy.maximum_risk_fraction
    )
    available_margin = account.equity * policy.margin_fraction

    def economics_for(
        quantity: Decimal,
        *,
        approved: bool,
        reason: str,
        category: str | None,
    ) -> CandidateEconomics:
        notional = quantity * entry_fill
        leverage = max(
            1,
            int((notional / available_margin).to_integral_value(
                rounding=ROUND_CEILING
            )),
        )
        margin_required = notional / Decimal(leverage)
        raw_loss = quantity * raw_loss_per_unit
        entry_cost = quantity * (
            entry_slippage_per_unit + entry_fee_per_unit
        )
        target_exit_cost = quantity * (
            target_slippage_per_unit + target_fee_per_unit
        )
        slippage = quantity * (
            entry_slippage_per_unit + target_slippage_per_unit
        )
        total_costs = entry_cost + target_exit_cost
        cost_adjusted_loss = quantity * loss_per_unit
        gross_reward = quantity * raw_reward_per_unit
        net_reward = gross_reward - total_costs
        gross_rr = gross_reward / raw_loss
        net_rr = net_reward / cost_adjusted_loss
        reward_to_cost = (
            gross_reward / total_costs
            if total_costs > 0 else Decimal("Infinity")
        )
        expected_movement = quantity * raw_reward_per_unit
        expected_movement_to_cost = (
            expected_movement / total_costs
            if total_costs > 0 else Decimal("Infinity")
        )
        expected_movement_rate = raw_reward_per_unit / signal.entry
        estimated_round_trip_cost_rate = (
            total_costs / quantity / signal.entry
        )
        return CandidateEconomics(
            approved, reason, category, minimum_quantity, quantity,
            entry_fill, stop_fill, target_fill, notional, margin_required,
            leverage, raw_loss, entry_cost, target_exit_cost, slippage,
            total_costs, cost_adjusted_loss, gross_reward, raw_loss, net_reward,
            gross_rr, net_rr, reward_to_cost, risk_budget,
            expected_movement,
            expected_movement_to_cost,
            expected_movement_rate,
            estimated_round_trip_cost_rate,
            True,
            net_reward > 0
            and reward_to_cost >= policy.minimum_reward_to_cost_multiple,
            expected_movement_to_cost
            >= policy.minimum_expected_movement_to_cost_multiple,
            net_rr >= policy.minimum_net_reward_risk,
            risk_quantity >= minimum_quantity,
            margin_quantity >= minimum_quantity,
        )

    risk_quantity = rules.round_quantity(risk_budget / loss_per_unit)
    margin_quantity = rules.round_quantity(
        available_margin * Decimal(policy.leverage_ceiling) / entry_fill
    )
    approved_quantity = min(
        risk_quantity, margin_quantity, rules.maximum_quantity
    )
    if risk_quantity < minimum_quantity:
        return economics_for(
            minimum_quantity,
            approved=False,
            reason=(
                "minimum executable quantity required by minimum notional "
                "exceeds account loss budget"
            ),
            category="minimum_executable_risk",
        )
    if margin_quantity < minimum_quantity:
        return economics_for(
            minimum_quantity,
            approved=False,
            reason="minimum executable quantity exceeds leverage ceiling",
            category="leverage",
        )
    if approved_quantity < minimum_quantity:
        return economics_for(
            minimum_quantity,
            approved=False,
            reason="minimum executable quantity violates exchange maximum",
            category="exchange_filters",
        )
    result = economics_for(
        approved_quantity,
        approved=True,
        reason="economically executable",
        category=None,
    )
    if (
        result.expected_net_reward <= 0
        or result.reward_to_execution_cost_multiple
        < policy.minimum_reward_to_cost_multiple
    ):
        return replace(
            result,
            approved=False,
            reason="candidate reward is dominated by execution costs",
            rejection_category="cost",
        )
    if result.net_reward_risk < policy.minimum_net_reward_risk:
        return replace(
            result,
            approved=False,
            reason="candidate net reward:risk is below threshold",
            rejection_category="net_reward_risk",
        )
    if (
        result.expected_movement_to_execution_cost_multiple
        < policy.minimum_expected_movement_to_cost_multiple
    ):
        return replace(
            result,
            approved=False,
            reason="candidate expected movement is too small relative to costs",
            rejection_category="expected_movement_cost",
        )
    if result.leverage_required > policy.leverage_ceiling:
        return replace(
            result,
            approved=False,
            reason="candidate requires leverage above configured ceiling",
            rejection_category="leverage",
        )
    return result


@dataclass(frozen=True, slots=True)
class ConsecutiveLossBreakerState:
    """Serializable breaker lifecycle state.

    A profitable close resets the streak. A breakeven close leaves the streak
    unchanged. Only cooldown or an enabled UTC-day boundary resets a tripped
    breaker.
    """

    consecutive_losses: int = 0
    tripped_at: datetime | None = None
    cooldown_until: datetime | None = None
    trigger_count: int = 0
    reset_count: int = 0
    blocked_candidates: int = 0
    disabled_duration_seconds: int = 0

    @property
    def active(self) -> bool:
        return self.tripped_at is not None


def _require_utc(timestamp: datetime) -> None:
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError("breaker timestamp must be UTC")


def advance_consecutive_loss_breaker(
    state: ConsecutiveLossBreakerState,
    timestamp: datetime,
    policy: FuturesRiskPolicy,
) -> ConsecutiveLossBreakerState:
    _require_utc(timestamp)
    if not state.active:
        return state
    assert state.tripped_at is not None and state.cooldown_until is not None
    reset_for_cooldown = timestamp >= state.cooldown_until
    reset_for_day = (
        policy.consecutive_loss_reset_on_utc_day
        and timestamp.date() > state.tripped_at.date()
    )
    if not (reset_for_cooldown or reset_for_day):
        return state
    elapsed = max(0, int((timestamp - state.tripped_at).total_seconds()))
    return replace(
        state,
        consecutive_losses=0,
        tripped_at=None,
        cooldown_until=None,
        reset_count=state.reset_count + 1,
        disabled_duration_seconds=state.disabled_duration_seconds + elapsed,
    )


def record_closed_trade(
    state: ConsecutiveLossBreakerState,
    pnl: Decimal,
    timestamp: datetime,
    policy: FuturesRiskPolicy,
) -> ConsecutiveLossBreakerState:
    _require_utc(timestamp)
    if not isinstance(pnl, Decimal) or not pnl.is_finite():
        raise ValueError("closed-trade PnL must be a finite Decimal")
    state = advance_consecutive_loss_breaker(state, timestamp, policy)
    if pnl > 0:
        return replace(state, consecutive_losses=0)
    if pnl == 0:
        return state
    losses = state.consecutive_losses + 1
    if losses < policy.consecutive_loss_limit:
        return replace(state, consecutive_losses=losses)
    if state.active:
        return replace(state, consecutive_losses=losses)
    return replace(
        state,
        consecutive_losses=losses,
        tripped_at=timestamp,
        cooldown_until=timestamp + timedelta(
            minutes=policy.consecutive_loss_cooldown_minutes
        ),
        trigger_count=state.trigger_count + 1,
    )


def record_breaker_blocked_candidate(
    state: ConsecutiveLossBreakerState,
) -> ConsecutiveLossBreakerState:
    if not state.active:
        raise ValueError("cannot record a breaker block while breaker is inactive")
    return replace(state, blocked_candidates=state.blocked_candidates + 1)


def breaker_disabled_duration(
    state: ConsecutiveLossBreakerState,
    timestamp: datetime | None,
) -> int:
    if not state.active or timestamp is None:
        return state.disabled_duration_seconds
    _require_utc(timestamp)
    assert state.tripped_at is not None
    return state.disabled_duration_seconds + max(
        0, int((timestamp - state.tripped_at).total_seconds())
    )


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
    economics: CandidateEconomics | None = None


def _reject(
    reason: str,
    economics: CandidateEconomics | None = None,
) -> FuturesRiskDecision:
    return FuturesRiskDecision(False, reason, economics=economics)


def evaluate_futures_risk(candidate: TradeCandidate | None, account: FuturesAccountState,
                          rules: FuturesSymbolRules,
                          policy: FuturesRiskPolicy = FuturesRiskPolicy()) -> FuturesRiskDecision:
    if candidate is None:
        return _reject("no trade candidate")
    if not account.reconciled:
        return _reject("account state is not reconciled")
    if account.equity <= 0 or account.day_start_equity <= 0:
        return _reject("account equity is invalid")
    loss = max(Decimal(0), account.day_start_equity - account.equity)
    if loss >= account.day_start_equity * policy.daily_loss_fraction:
        return _reject("daily-loss circuit breaker")
    if account.consecutive_loss_breaker_active:
        return _reject("consecutive-loss circuit breaker")
    if account.has_position:
        return _reject("one-position rule")

    signal = candidate.signal
    economics = assess_candidate_economics(candidate, account, rules, policy)
    if not economics.approved:
        return _reject(economics.reason, economics)
    quantity = economics.approved_quantity
    risk_entry = economics.estimated_entry_price
    notional = economics.notional
    leverage = economics.leverage_required
    allocated_margin = economics.margin_required

    if signal.direction is Direction.LONG:
        liquidation = risk_entry * (Decimal(1) - Decimal(1) / Decimal(leverage)
                                      + policy.maintenance_margin_fraction)
        safe = liquidation <= signal.stop * (Decimal(1) - policy.liquidation_buffer_fraction)
    else:
        liquidation = risk_entry * (Decimal(1) + Decimal(1) / Decimal(leverage)
                                      - policy.maintenance_margin_fraction)
        safe = liquidation >= signal.stop * (Decimal(1) + policy.liquidation_buffer_fraction)
    if not safe:
        return _reject("liquidation buffer is insufficient", economics)

    identity = "|".join((signal.symbol, signal.timeframe, str(signal.timestamp),
                         signal.strategy_id, signal.direction.value, signal.signal_id))
    client_id = "qv1-" + sha256(identity.encode("utf-8")).hexdigest()[:24]
    return FuturesRiskDecision(True, "approved", quantity, leverage, notional,
                               allocated_margin, economics.cost_adjusted_stop_loss,
                               liquidation, client_id, economics)


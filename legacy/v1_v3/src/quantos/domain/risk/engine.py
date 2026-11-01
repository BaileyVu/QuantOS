"""Stateless, fail-closed V1 Risk Engine over explicit immutable inputs."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException, ROUND_DOWN, localcontext

from quantos.domain.alpha import AlphaAction, AlphaDecision
from quantos.domain.common import require_decimal, require_utc
from quantos.domain.runtime_contracts import (
    AccountSnapshot, TransactionCosts, arithmetic, identity, validate_account,
)
from quantos.domain.market_data import Candle
from quantos.domain.risk.contracts import RiskDecision


@dataclass(frozen=True, slots=True)
class MarketState:
    candle: Candle
    valid: bool


@dataclass(frozen=True, slots=True)
class RiskContext:
    timestamp: datetime
    markets: tuple[MarketState, ...]
    account: AccountSnapshot
    gross_edge_rate: Decimal
    day_start_timestamp: datetime
    day_start_equity: Decimal
    peak_equity: Decimal
    volatility: Decimal | None = None

    def __post_init__(self):
        object.__setattr__(self, 'markets', tuple(self.markets))


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    order_notional: Decimal
    quantity_step: Decimal
    sell_fraction: Decimal
    max_position_quantity: Decimal
    max_position_notional: Decimal
    max_exposure_fraction: Decimal
    daily_loss_fraction: Decimal
    drawdown_fraction: Decimal
    stale_seconds: int
    safety_margin_rate: Decimal
    costs: TransactionCosts
    volatility_target: Decimal | None = None

    def __post_init__(self):
        for name in ('order_notional', 'quantity_step', 'sell_fraction', 'max_position_quantity',
                     'max_position_notional', 'max_exposure_fraction', 'daily_loss_fraction',
                     'drawdown_fraction'):
            value = require_decimal(getattr(self, name), name)
            if value <= 0:
                raise ValueError(f'{name} must be positive')
        for name in ('sell_fraction', 'max_exposure_fraction', 'daily_loss_fraction', 'drawdown_fraction'):
            if getattr(self, name) > 1:
                raise ValueError(f'{name} must be at most one')
        require_decimal(self.safety_margin_rate, 'safety_margin_rate', non_negative=True)
        if type(self.stale_seconds) is not int or self.stale_seconds <= 0:
            raise ValueError('stale_seconds must be a positive integer')
        if type(self.costs) is not TransactionCosts:
            raise ValueError('explicit execution costs required')
        self.costs.__post_init__()
        if self.volatility_target is not None:
            require_decimal(self.volatility_target, 'volatility_target')
            if self.volatility_target <= 0:
                raise ValueError('volatility_target must be positive when enabled')


def decision_identity(decision: RiskDecision) -> str:
    return identity(replace(decision, decision_id=None))


class RiskEngine:
    def __init__(self, policy: RiskPolicy):
        policy.__post_init__()
        self.policy = policy

    def evaluate(self, alpha: AlphaDecision, context: RiskContext) -> RiskDecision:
        AlphaDecision.__post_init__(alpha)
        try:
            with localcontext(arithmetic()):
                quantity = self._quantity(alpha, context)
                net_edge = (context.gross_edge_rate - self.policy.costs.conservative_cost_rate
                            - self.policy.safety_margin_rate)
            result = RiskDecision(context.timestamp, alpha.symbol, True, quantity,
                                  alpha_id=identity(alpha), context_id=identity((context, self.policy)),
                                  net_edge_rate=net_edge)
            return replace(result, decision_id=decision_identity(result))
        except (ValueError, DecimalException, TypeError, AttributeError) as error:
            # Even an unusable context timestamp must yield a valid rejection.
            return RiskDecision(alpha.timestamp, alpha.symbol, False, rejection_reason=str(error),
                                alpha_id=identity(alpha))

    def _quantity(self, alpha: AlphaDecision, context: RiskContext) -> Decimal:
        p = self.policy
        if alpha.action is AlphaAction.HOLD:
            raise ValueError('HOLD produces no order')
        require_utc(context.timestamp, 'timestamp')
        if alpha.timestamp != context.timestamp:
            raise ValueError('inconsistent alpha timestamp')
        validate_account(context.account)
        if context.account.timestamp > context.timestamp:
            raise ValueError('future account state')
        marks = {}
        for market in context.markets:
            if type(market) is not MarketState or market.valid is not True:
                raise ValueError('invalid market state')
            candle = market.candle
            Candle.__post_init__(candle)
            if candle.symbol in marks:
                raise ValueError('duplicate market state')
            if candle.open_time.second or candle.open_time.microsecond:
                raise ValueError('invalid candle boundary')
            end = candle.open_time + timedelta(minutes=1)
            if not end-timedelta(milliseconds=1) <= candle.close_time <= end:
                raise ValueError('invalid one-minute candle boundary')
            age = context.timestamp - candle.close_time
            if age < timedelta(0) or age > timedelta(seconds=p.stale_seconds):
                raise ValueError('stale or incomplete market state')
            if candle.close <= 0:
                raise ValueError('invalid market price')
            marks[candle.symbol] = candle.close
        if alpha.symbol not in marks or any(pos.symbol not in marks for pos in context.account.positions):
            raise ValueError('missing required market state')
        require_utc(context.day_start_timestamp, 'day_start_timestamp')
        if context.day_start_timestamp != context.timestamp.replace(hour=0, minute=0, second=0, microsecond=0):
            raise ValueError('invalid day-start reference')
        for name in ('gross_edge_rate', 'day_start_equity', 'peak_equity'):
            require_decimal(getattr(context, name), name)
        if context.day_start_equity <= 0 or context.peak_equity <= 0:
            raise ValueError('invalid equity reference')
        exposure = sum((pos.quantity * marks[pos.symbol] for pos in context.account.positions), Decimal('0'))
        equity = context.account.balances['USDT'] + exposure
        if equity <= 0 or context.peak_equity < max(equity, context.day_start_equity):
            raise ValueError('inconsistent peak/current equity')
        if context.volatility is not None:
            require_decimal(context.volatility, 'volatility', non_negative=True)
        if alpha.action is AlphaAction.BUY:
            if context.day_start_equity-equity >= context.day_start_equity*p.daily_loss_fraction:
                raise ValueError('daily loss stop')
            if context.peak_equity-equity >= context.peak_equity*p.drawdown_fraction:
                raise ValueError('drawdown stop')
            cost = p.costs.conservative_cost_rate
            if context.gross_edge_rate <= cost + p.safety_margin_rate:
                raise ValueError('non-positive edge after costs and safety margin')
        price = marks[alpha.symbol]
        owned = context.account.balances.get(alpha.symbol[:-4], Decimal('0'))
        if alpha.action is AlphaAction.SELL and owned <= 0:
            raise ValueError('insufficient owned position')
        with localcontext(arithmetic()) as sizing_context:
            sizing_context.rounding = ROUND_DOWN
            desired = p.order_notional/price if alpha.action is AlphaAction.BUY else owned*p.sell_fraction
            if alpha.action is AlphaAction.BUY and p.volatility_target is not None:
                if context.volatility is None:
                    raise ValueError('missing required volatility')
                # A single configured cap: higher volatility may only reduce sizing.
                if context.volatility > p.volatility_target:
                    desired *= p.volatility_target/context.volatility
            quantity = (desired/p.quantity_step).to_integral_value(rounding=ROUND_DOWN)*p.quantity_step
        if quantity <= 0:
            raise ValueError('non-positive sized quantity')
        if quantity > desired:
            raise ValueError('rounded quantity exceeds sizing limit')
        if alpha.action is AlphaAction.SELL:
            if quantity > owned:
                raise ValueError('insufficient owned position')
            return quantity
        fill_price = price*(1+p.costs.slippage_rate)
        debit = quantity*fill_price*(1+p.costs.fee_rate)
        if debit > context.account.balances['USDT']:
            raise ValueError('insufficient BUY cash including costs')
        if owned+quantity > p.max_position_quantity or (owned+quantity)*fill_price > p.max_position_notional:
            raise ValueError('maximum position size/notional exceeded')
        projected_exposure = exposure + quantity*fill_price
        projected_equity = equity - debit + quantity*fill_price
        if projected_exposure > projected_equity*p.max_exposure_fraction:
            raise ValueError('maximum exposure exceeded')
        return quantity

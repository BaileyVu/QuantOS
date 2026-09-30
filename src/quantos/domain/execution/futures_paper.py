"""Deterministic isolated-margin Futures paper execution and exit management."""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from enum import Enum

from quantos.domain.alpha.futures import Direction, MarketRegime, TradeCandidate
from quantos.domain.market_data import Candle
from quantos.domain.risk.futures import FuturesRiskDecision


class PaperExecutionError(RuntimeError):
    pass


class PositionManagementState(str, Enum):
    INITIAL_RISK = "INITIAL_RISK"
    PROFIT_DEVELOPING = "PROFIT_DEVELOPING"
    BREAKEVEN_PROTECTED = "BREAKEVEN_PROTECTED"
    PROFIT_LOCKED = "PROFIT_LOCKED"
    TREND_RUNNER = "TREND_RUNNER"
    EXIT = "EXIT"


class ExitReason(str, Enum):
    INITIAL_STOP = "INITIAL_STOP"
    BREAKEVEN_STOP = "BREAKEVEN_STOP"
    TRAILING_STOP = "TRAILING_STOP"
    FIXED_TARGET = "FIXED_TARGET"
    STRUCTURE_TARGET = "STRUCTURE_TARGET"
    TIME_STOP = "TIME_STOP"
    REGIME_INVALIDATION = "REGIME_INVALIDATION"
    MANUAL = "MANUAL"
    PAPER_END = "PAPER_END"
    LIQUIDATION_APPROXIMATION = "LIQUIDATION_APPROXIMATION"


@dataclass(frozen=True, slots=True)
class PositionManagementPolicy:
    breakeven_activation_r: Decimal = Decimal(".85")
    breakeven_safety_buffer_r: Decimal = Decimal(".05")
    profit_lock_activation_r: Decimal = Decimal("1.5")
    profit_lock_floor_r: Decimal = Decimal(".5")
    runner_activation_r: Decimal = Decimal("2")
    minimum_development_r: Decimal = Decimal(".35")
    atr_multiplier_tight: Decimal = Decimal("1.25")
    atr_multiplier_medium: Decimal = Decimal("1.75")
    atr_multiplier_wide: Decimal = Decimal("2.25")
    structure_lookback: int = 5
    minimum_stop_adjustment_ticks: int = 2
    time_stop_bars_1m: int = 12
    time_stop_bars_3m: int = 10
    time_stop_bars_5m: int = 8
    time_stop_bars_15m: int = 6
    time_stop_bars_30m: int = 5
    time_stop_bars_1h: int = 4

    def __post_init__(self) -> None:
        decimal_names = (
            "breakeven_activation_r", "breakeven_safety_buffer_r",
            "profit_lock_activation_r", "profit_lock_floor_r",
            "runner_activation_r", "minimum_development_r",
            "atr_multiplier_tight", "atr_multiplier_medium", "atr_multiplier_wide",
        )
        if any(not isinstance(getattr(self, name), Decimal)
               or getattr(self, name) < 0 for name in decimal_names):
            raise ValueError("position-management values must be non-negative Decimals")
        if not (self.breakeven_activation_r <= self.profit_lock_activation_r
                <= self.runner_activation_r):
            raise ValueError("position-management R thresholds must be ordered")
        if self.structure_lookback < 2 or self.minimum_stop_adjustment_ticks < 1:
            raise ValueError("invalid trailing-stop configuration")
        if any(self.time_stop_bars(value) < 1
               for value in ("1m", "3m", "5m", "15m", "30m", "1h")):
            raise ValueError("time-stop bars must be positive")

    def time_stop_bars(self, timeframe: str) -> int:
        return {
            "1m": self.time_stop_bars_1m, "3m": self.time_stop_bars_3m,
            "5m": self.time_stop_bars_5m, "15m": self.time_stop_bars_15m,
            "30m": self.time_stop_bars_30m, "1h": self.time_stop_bars_1h,
        }[timeframe]

    def atr_multiplier(self, timeframe: str) -> Decimal:
        if timeframe in ("1m", "3m"):
            return self.atr_multiplier_tight
        if timeframe in ("5m", "15m"):
            return self.atr_multiplier_medium
        return self.atr_multiplier_wide


@dataclass(frozen=True, slots=True)
class FuturesPaperPolicy:
    taker_fee_rate: Decimal = Decimal(".0005")
    slippage_rate: Decimal = Decimal(".0002")
    management: PositionManagementPolicy = PositionManagementPolicy()

    def __post_init__(self) -> None:
        if any(not isinstance(value, Decimal) or value < 0 or value >= 1
               for value in (self.taker_fee_rate, self.slippage_rate)):
            raise ValueError("invalid paper execution costs")


@dataclass(frozen=True, slots=True)
class PaperPosition:
    direction: Direction
    quantity: Decimal
    entry_price: Decimal
    reference_entry_price: Decimal
    initial_stop: Decimal
    stop: Decimal
    target: Decimal
    leverage: int
    margin: Decimal
    entry_fee: Decimal
    strategy_id: str
    timeframe: str
    regime: MarketRegime
    opened_at: object
    client_order_id: str
    state: PositionManagementState
    initial_risk_per_unit: Decimal
    initial_risk_amount: Decimal
    highest_price: Decimal
    lowest_price: Decimal
    mfe_price: Decimal = Decimal(0)
    mae_price: Decimal = Decimal(0)
    maximum_unrealized_profit: Decimal = Decimal(0)
    maximum_unrealized_loss: Decimal = Decimal(0)
    bars_held: int = 0
    stop_adjustments: int = 0
    aligned_higher_timeframes: int = 0


@dataclass(frozen=True, slots=True)
class PaperTrade:
    direction: Direction
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    pnl: Decimal
    gross_pnl: Decimal
    fees: Decimal
    slippage_cost: Decimal
    total_execution_costs: Decimal
    initial_risk_amount: Decimal
    realized_r: Decimal
    mfe_price: Decimal
    mfe_r: Decimal
    mae_price: Decimal
    mae_r: Decimal
    maximum_unrealized_profit: Decimal
    maximum_unrealized_loss: Decimal
    strategy_id: str
    timeframe: str
    regime: MarketRegime
    opened_at: object
    closed_at: object
    exit_reason: ExitReason
    management_state_at_exit: PositionManagementState
    stop_adjustments: int
    initial_stop: Decimal
    final_stop: Decimal
    notional: Decimal
    leverage: int


class FuturesPaperExecution:
    """Shared replay/live-paper execution engine; no authenticated order path."""

    def __init__(self, starting_equity: Decimal,
                 policy: FuturesPaperPolicy = FuturesPaperPolicy(),
                 price_tick: Decimal = Decimal(".01")) -> None:
        if not isinstance(starting_equity, Decimal) or starting_equity <= 0:
            raise ValueError("starting equity must be a positive Decimal")
        if not isinstance(price_tick, Decimal) or price_tick <= 0:
            raise ValueError("price tick must be a positive Decimal")
        self.starting_equity = starting_equity
        self.balance = starting_equity
        self.policy = policy
        self.price_tick = price_tick
        self.position: PaperPosition | None = None
        self.trades: list[PaperTrade] = []
        self.audit_events: list[dict] = []
        self.total_fees = Decimal(0)
        self.total_slippage_cost = Decimal(0)
        self._order_ids: set[str] = set()

    def _round_up(self, value: Decimal) -> Decimal:
        return ((value / self.price_tick).to_integral_value(
            rounding=ROUND_CEILING) * self.price_tick)

    def _round_down(self, value: Decimal) -> Decimal:
        return ((value / self.price_tick).to_integral_value(
            rounding=ROUND_FLOOR) * self.price_tick)

    @staticmethod
    def _aligned_context(candidate: TradeCandidate) -> int:
        signal = candidate.signal
        aligned = "TREND_UP" if signal.direction is Direction.LONG else "TREND_DOWN"
        return sum(label.endswith(aligned)
                   for label in signal.higher_timeframe_context)

    def open(self, candidate: TradeCandidate,
             approval: FuturesRiskDecision) -> PaperPosition:
        if not approval.approved or approval.quantity is None or approval.leverage is None:
            raise PaperExecutionError("Risk approval is required")
        if self.position is not None:
            raise PaperExecutionError("one-position rule")
        if not approval.client_order_id or approval.client_order_id in self._order_ids:
            raise PaperExecutionError("duplicate or missing client order id")
        signal = candidate.signal
        adverse = (Decimal(1) + self.policy.slippage_rate
                   if signal.direction is Direction.LONG
                   else Decimal(1) - self.policy.slippage_rate)
        entry = signal.entry * adverse
        fee = approval.quantity * entry * self.policy.taker_fee_rate
        if fee >= self.balance:
            raise PaperExecutionError("insufficient equity for entry fee")
        raw_risk_per_unit = abs(entry - signal.stop)
        risk_amount = (
            approval.risk_amount
            if approval.risk_amount is not None
            else raw_risk_per_unit * approval.quantity
        )
        risk_per_unit = risk_amount / approval.quantity
        if risk_per_unit <= 0:
            raise PaperExecutionError("initial risk must be positive")
        self.balance -= fee
        self.total_fees += fee
        entry_slippage = abs(entry - signal.entry) * approval.quantity
        self.total_slippage_cost += entry_slippage
        self._order_ids.add(approval.client_order_id)
        self.position = PaperPosition(
            signal.direction, approval.quantity, entry, signal.entry,
            signal.stop, signal.stop, signal.target, approval.leverage,
            approval.allocated_margin or Decimal(0), fee, signal.strategy_id,
            signal.timeframe, signal.regime, signal.timestamp,
            approval.client_order_id, PositionManagementState.INITIAL_RISK,
            risk_per_unit, risk_amount, entry, entry,
            aligned_higher_timeframes=self._aligned_context(candidate),
        )
        self.audit_events.append({
            "timestamp": signal.timestamp, "event": "POSITION_OPENED",
            "state": PositionManagementState.INITIAL_RISK,
            "stop": signal.stop, "signal_id": signal.signal_id,
        })
        return self.position

    def _update_excursions(self, candle: Candle) -> None:
        position = self.position
        if position is None:
            return
        highest = max(position.highest_price, candle.high)
        lowest = min(position.lowest_price, candle.low)
        if position.direction is Direction.LONG:
            mfe = max(Decimal(0), highest - position.entry_price)
            mae = max(Decimal(0), position.entry_price - lowest)
        else:
            mfe = max(Decimal(0), position.entry_price - lowest)
            mae = max(Decimal(0), highest - position.entry_price)
        self.position = replace(
            position, highest_price=highest, lowest_price=lowest,
            mfe_price=max(position.mfe_price, mfe),
            mae_price=max(position.mae_price, mae),
            maximum_unrealized_profit=max(
                position.maximum_unrealized_profit, mfe * position.quantity),
            maximum_unrealized_loss=max(
                position.maximum_unrealized_loss, mae * position.quantity),
        )

    @staticmethod
    def _stop_reason(position: PaperPosition) -> ExitReason:
        if position.state in (PositionManagementState.PROFIT_LOCKED,
                              PositionManagementState.TREND_RUNNER):
            return ExitReason.TRAILING_STOP
        if position.state is PositionManagementState.BREAKEVEN_PROTECTED:
            return ExitReason.BREAKEVEN_STOP
        return ExitReason.INITIAL_STOP

    def _close(self, reference: Decimal, timestamp: object,
               reason: ExitReason) -> PaperTrade:
        position = self.position
        if position is None:
            raise PaperExecutionError("no position")
        adverse = (Decimal(1) - self.policy.slippage_rate
                   if position.direction is Direction.LONG
                   else Decimal(1) + self.policy.slippage_rate)
        exit_price = reference * adverse
        sign = Decimal(1) if position.direction is Direction.LONG else Decimal(-1)
        gross = ((reference - position.reference_entry_price)
                 * position.quantity * sign)
        exit_fee = exit_price * position.quantity * self.policy.taker_fee_rate
        exit_slippage = abs(exit_price - reference) * position.quantity
        entry_slippage = (abs(position.entry_price - position.reference_entry_price)
                          * position.quantity)
        fees = position.entry_fee + exit_fee
        slippage = entry_slippage + exit_slippage
        net = gross - fees - slippage
        self.balance += ((exit_price - position.entry_price)
                         * position.quantity * sign - exit_fee)
        self.total_fees += exit_fee
        self.total_slippage_cost += exit_slippage
        realized_r = (net / position.initial_risk_amount
                      if position.initial_risk_amount > 0 else Decimal(0))
        trade = PaperTrade(
            position.direction, position.quantity, position.entry_price, exit_price,
            net, gross, fees, slippage, fees + slippage,
            position.initial_risk_amount, realized_r,
            position.mfe_price, position.mfe_price / position.initial_risk_per_unit,
            position.mae_price, position.mae_price / position.initial_risk_per_unit,
            position.maximum_unrealized_profit, position.maximum_unrealized_loss,
            position.strategy_id, position.timeframe, position.regime,
            position.opened_at, timestamp, reason, position.state,
            position.stop_adjustments, position.initial_stop, position.stop,
            position.reference_entry_price * position.quantity, position.leverage,
        )
        self.trades.append(trade)
        self.audit_events.append({
            "timestamp": timestamp, "event": "POSITION_EXITED",
            "from_state": position.state, "state": PositionManagementState.EXIT,
            "reason": reason, "final_stop": position.stop,
        })
        self.position = None
        return trade

    def process_candle(self, candle: Candle) -> PaperTrade | None:
        position = self.position
        if position is None:
            return None
        prior_mfe = position.mfe_price
        prior_maximum_profit = position.maximum_unrealized_profit
        if position.direction is Direction.LONG:
            if candle.low <= position.stop:
                adverse = max(Decimal(0), position.entry_price - position.stop)
                self.position = replace(
                    position,
                    lowest_price=min(position.lowest_price, position.stop),
                    mae_price=max(position.mae_price, adverse),
                    maximum_unrealized_loss=max(
                        position.maximum_unrealized_loss,
                        adverse * position.quantity,
                    ),
                )
                return self._close(position.stop, candle.close_time,
                                   self._stop_reason(position))
        else:
            if candle.high >= position.stop:
                adverse = max(Decimal(0), position.stop - position.entry_price)
                self.position = replace(
                    position,
                    highest_price=max(position.highest_price, position.stop),
                    mae_price=max(position.mae_price, adverse),
                    maximum_unrealized_loss=max(
                        position.maximum_unrealized_loss,
                        adverse * position.quantity,
                    ),
                )
                return self._close(position.stop, candle.close_time,
                                   self._stop_reason(position))
        self._update_excursions(candle)
        position = self.position
        assert position is not None
        if position.direction is Direction.LONG:
            target_hit = candle.high >= position.target
        else:
            target_hit = candle.low <= position.target
        if not target_hit:
            return None
        if position.strategy_id == "range_mean_reversion":
            favorable = abs(position.target - position.entry_price)
            self.position = replace(
                position,
                mfe_price=max(prior_mfe, favorable),
                maximum_unrealized_profit=max(
                    prior_maximum_profit,
                    favorable * position.quantity,
                ),
            )
            return self._close(position.target, candle.close_time,
                               ExitReason.STRUCTURE_TARGET)
        favorable_r = position.mfe_price / position.initial_risk_per_unit
        if (position.state is PositionManagementState.TREND_RUNNER
                or position.aligned_higher_timeframes >= 2
                and favorable_r >= self.policy.management.runner_activation_r):
            self._transition(PositionManagementState.TREND_RUNNER,
                             candle.close_time)
            return None
        favorable = abs(position.target - position.entry_price)
        self.position = replace(
            position,
            mfe_price=max(prior_mfe, favorable),
            maximum_unrealized_profit=max(
                prior_maximum_profit,
                favorable * position.quantity,
            ),
        )
        return self._close(position.target, candle.close_time,
                           ExitReason.FIXED_TARGET)

    def _transition(self, state: PositionManagementState,
                    timestamp: object) -> None:
        position = self.position
        if position is None or position.state is state:
            return
        self.position = replace(position, state=state)
        self.audit_events.append({
            "timestamp": timestamp, "event": "STATE_TRANSITION",
            "from_state": position.state, "state": state,
        })

    def _cost_adjusted_breakeven(self, position: PaperPosition) -> Decimal:
        entry_fee_per_unit = position.entry_fee / position.quantity
        safety = (position.initial_risk_per_unit
                  * self.policy.management.breakeven_safety_buffer_r)
        if position.direction is Direction.LONG:
            reference = (position.entry_price + entry_fee_per_unit + safety) / (
                (Decimal(1) - self.policy.slippage_rate)
                * (Decimal(1) - self.policy.taker_fee_rate))
            return self._round_up(reference)
        reference = (position.entry_price - entry_fee_per_unit - safety) / (
            (Decimal(1) + self.policy.slippage_rate)
            * (Decimal(1) + self.policy.taker_fee_rate))
        return self._round_down(reference)

    def _tighten_stop(self, candidate: Decimal, timestamp: object,
                      source: str, market_price: Decimal | None = None) -> bool:
        position = self.position
        if position is None:
            return False
        adjusted = (self._round_up(candidate)
                    if position.direction is Direction.LONG
                    else self._round_down(candidate))
        if market_price is not None and (
            (position.direction is Direction.LONG and adjusted >= market_price)
            or (position.direction is Direction.SHORT and adjusted <= market_price)
        ):
            return False
        improvement = (adjusted - position.stop
                       if position.direction is Direction.LONG
                       else position.stop - adjusted)
        if improvement < (self.price_tick
                          * self.policy.management.minimum_stop_adjustment_ticks):
            return False
        old = position.stop
        self.position = replace(
            position, stop=adjusted,
            stop_adjustments=position.stop_adjustments + 1)
        self.audit_events.append({
            "timestamp": timestamp, "event": "STOP_ADJUSTED",
            "state": position.state, "old_stop": old,
            "new_stop": adjusted, "source": source,
        })
        return True

    @staticmethod
    def _invalidated(position: PaperPosition, regime: MarketRegime) -> bool:
        return ((position.direction is Direction.LONG
                 and regime is MarketRegime.TREND_DOWN)
                or (position.direction is Direction.SHORT
                    and regime is MarketRegime.TREND_UP))

    def manage_completed_candle(
        self, candle: Candle, atr: Decimal, regime: MarketRegime,
        higher_context: tuple[tuple[str, MarketRegime], ...] = (),
        recent_candles: tuple[Candle, ...] = (),
    ) -> PaperTrade | None:
        """Manage once per completed entry-timeframe candle using confirmed data."""
        position = self.position
        if position is None or candle.interval != position.timeframe:
            return None
        position = replace(position, bars_held=position.bars_held + 1)
        self.position = position
        if self._invalidated(position, regime):
            return self._close(candle.close, candle.close_time,
                               ExitReason.REGIME_INVALIDATION)
        favorable_r = position.mfe_price / position.initial_risk_per_unit
        if (position.bars_held >=
                self.policy.management.time_stop_bars(position.timeframe)
                and favorable_r < self.policy.management.minimum_development_r):
            return self._close(candle.close, candle.close_time,
                               ExitReason.TIME_STOP)
        if favorable_r > 0 and position.state is PositionManagementState.INITIAL_RISK:
            self._transition(PositionManagementState.PROFIT_DEVELOPING,
                             candle.close_time)
        if favorable_r >= self.policy.management.breakeven_activation_r:
            if self.position and self.position.state in (
                    PositionManagementState.INITIAL_RISK,
                    PositionManagementState.PROFIT_DEVELOPING):
                self._transition(PositionManagementState.BREAKEVEN_PROTECTED,
                                 candle.close_time)
            assert self.position is not None
            self._tighten_stop(self._cost_adjusted_breakeven(self.position),
                               candle.close_time, "COST_ADJUSTED_BREAKEVEN",
                               candle.close)
        if favorable_r >= self.policy.management.profit_lock_activation_r:
            if (self.position and
                    self.position.state is not PositionManagementState.TREND_RUNNER):
                self._transition(PositionManagementState.PROFIT_LOCKED,
                                 candle.close_time)
            assert self.position is not None
            distance = (self.position.initial_risk_per_unit
                        * self.policy.management.profit_lock_floor_r)
            lock = (self.position.entry_price + distance
                    if self.position.direction is Direction.LONG
                    else self.position.entry_price - distance)
            self._tighten_stop(lock, candle.close_time, "PROFIT_LOCK",
                               candle.close)
        aligned = sum(
            (position.direction is Direction.LONG
             and value is MarketRegime.TREND_UP)
            or (position.direction is Direction.SHORT
                and value is MarketRegime.TREND_DOWN)
            for _, value in higher_context)
        if (favorable_r >= self.policy.management.runner_activation_r
                and position.strategy_id in {
                    "breakout_expansion", "trend_continuation", "trend_pullback"}
                and aligned >= 2):
            self._transition(PositionManagementState.TREND_RUNNER,
                             candle.close_time)
        current = self.position
        if (current is None
                or current.state not in (
                    PositionManagementState.PROFIT_LOCKED,
                    PositionManagementState.TREND_RUNNER)
                or atr <= 0 or current.strategy_id == "range_mean_reversion"):
            return None
        multiplier = self.policy.management.atr_multiplier(current.timeframe)
        confirmed = recent_candles[
            -(self.policy.management.structure_lookback + 1):-1]
        if current.direction is Direction.LONG:
            candidates = [current.highest_price - atr * multiplier]
            if confirmed:
                structure = min(item.low for item in confirmed)
                if structure < candle.close:
                    candidates.append(structure)
            trail = max(candidates)
        else:
            candidates = [current.lowest_price + atr * multiplier]
            if confirmed:
                structure = max(item.high for item in confirmed)
                if structure > candle.close:
                    candidates.append(structure)
            trail = min(candidates)
        self._tighten_stop(trail, candle.close_time, "HYBRID_ATR_STRUCTURE",
                           candle.close)
        return None

    def close_at(self, price: Decimal, timestamp: object,
                 reason: ExitReason | str = ExitReason.PAPER_END) -> PaperTrade | None:
        if self.position is None:
            return None
        if not isinstance(reason, ExitReason):
            raw_reason = reason
            aliases = {"end_of_data": ExitReason.PAPER_END,
                       "manual": ExitReason.MANUAL}
            reason = aliases.get(raw_reason)
            if reason is None:
                reason = ExitReason(raw_reason)
        return self._close(price, timestamp, reason)

    def marked_equity(self, price: Decimal) -> Decimal:
        if self.position is None:
            return self.balance
        sign = Decimal(1) if self.position.direction is Direction.LONG else Decimal(-1)
        return self.balance + ((price - self.position.entry_price)
                               * self.position.quantity * sign)

"""Deterministic isolated-margin Futures paper execution."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantos.domain.alpha.futures import Direction, MarketRegime, TradeCandidate
from quantos.domain.market_data import Candle
from quantos.domain.risk.futures import FuturesRiskDecision


class PaperExecutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FuturesPaperPolicy:
    taker_fee_rate: Decimal = Decimal(".0005")
    slippage_rate: Decimal = Decimal(".0002")

    def __post_init__(self) -> None:
        if any(not isinstance(v, Decimal) or v < 0 or v >= 1
               for v in (self.taker_fee_rate, self.slippage_rate)):
            raise ValueError("invalid paper execution costs")


@dataclass(frozen=True, slots=True)
class PaperPosition:
    direction: Direction
    quantity: Decimal
    entry_price: Decimal
    stop: Decimal
    target: Decimal
    leverage: int
    margin: Decimal
    entry_fee: Decimal
    strategy_id: str
    regime: MarketRegime
    opened_at: object
    client_order_id: str


@dataclass(frozen=True, slots=True)
class PaperTrade:
    direction: Direction
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    pnl: Decimal
    fees: Decimal
    strategy_id: str
    regime: MarketRegime
    opened_at: object
    closed_at: object
    exit_reason: str


class FuturesPaperExecution:
    def __init__(self, starting_equity: Decimal,
                 policy: FuturesPaperPolicy = FuturesPaperPolicy()) -> None:
        if not isinstance(starting_equity, Decimal) or starting_equity <= 0:
            raise ValueError("starting equity must be a positive Decimal")
        self.starting_equity = starting_equity
        self.balance = starting_equity
        self.policy = policy
        self.position: PaperPosition | None = None
        self.trades: list[PaperTrade] = []
        self.total_fees = Decimal(0)
        self._order_ids: set[str] = set()

    def open(self, candidate: TradeCandidate, approval: FuturesRiskDecision) -> PaperPosition:
        if not approval.approved or approval.quantity is None or approval.leverage is None:
            raise PaperExecutionError("Risk approval is required")
        if self.position is not None:
            raise PaperExecutionError("one-position rule")
        if not approval.client_order_id or approval.client_order_id in self._order_ids:
            raise PaperExecutionError("duplicate or missing client order id")
        signal = candidate.signal
        adverse = Decimal(1) + self.policy.slippage_rate if signal.direction is Direction.LONG else Decimal(1) - self.policy.slippage_rate
        entry = signal.entry * adverse
        fee = approval.quantity * entry * self.policy.taker_fee_rate
        if fee >= self.balance:
            raise PaperExecutionError("insufficient equity for entry fee")
        self.balance -= fee
        self.total_fees += fee
        self._order_ids.add(approval.client_order_id)
        self.position = PaperPosition(signal.direction, approval.quantity, entry,
            signal.stop, signal.target, approval.leverage,
            approval.allocated_margin or Decimal(0), fee, signal.strategy_id,
            signal.regime, signal.timestamp, approval.client_order_id)
        return self.position

    def _close(self, reference: Decimal, timestamp: object, reason: str) -> PaperTrade:
        position = self.position
        if position is None:
            raise PaperExecutionError("no position")
        adverse = Decimal(1) - self.policy.slippage_rate if position.direction is Direction.LONG else Decimal(1) + self.policy.slippage_rate
        exit_price = reference * adverse
        sign = Decimal(1) if position.direction is Direction.LONG else Decimal(-1)
        gross = (exit_price - position.entry_price) * position.quantity * sign
        exit_fee = exit_price * position.quantity * self.policy.taker_fee_rate
        self.balance += gross - exit_fee
        self.total_fees += exit_fee
        total_trade_fees = position.entry_fee + exit_fee
        trade = PaperTrade(position.direction, position.quantity, position.entry_price,
            exit_price, gross - total_trade_fees, total_trade_fees, position.strategy_id,
            position.regime, position.opened_at, timestamp, reason)
        self.trades.append(trade)
        self.position = None
        return trade

    def process_candle(self, candle: Candle) -> PaperTrade | None:
        position = self.position
        if position is None:
            return None
        if position.direction is Direction.LONG:
            if candle.low <= position.stop:
                return self._close(position.stop, candle.close_time, "stop")
            if candle.high >= position.target:
                return self._close(position.target, candle.close_time, "target")
        else:
            if candle.high >= position.stop:
                return self._close(position.stop, candle.close_time, "stop")
            if candle.low <= position.target:
                return self._close(position.target, candle.close_time, "target")
        return None

    def close_at(self, price: Decimal, timestamp: object,
                 reason: str = "end_of_data") -> PaperTrade | None:
        return None if self.position is None else self._close(price, timestamp, reason)

    def marked_equity(self, price: Decimal) -> Decimal:
        if self.position is None:
            return self.balance
        sign = Decimal(1) if self.position.direction is Direction.LONG else Decimal(-1)
        return self.balance + (price - self.position.entry_price) * self.position.quantity * sign


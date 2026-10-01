"""Conservative queue-aware paper execution for directional maker HFT."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from quantos.domain.alpha.hft import HftSide
from quantos.domain.common import require_utc
from quantos.domain.market_data.hft import AggregateTrade, L2OrderBook
from quantos.domain.risk.hft import HftFeeSchedule, HftRiskDecision


class HftExecutionError(RuntimeError):
    pass


@dataclass(slots=True)
class HftWorkingOrder:
    order_id: str
    side: HftSide
    price: Decimal
    quantity: Decimal
    remaining_quantity: Decimal
    queue_ahead: Decimal
    initial_queue_ahead: Decimal
    placed_at: datetime
    acknowledged_at: datetime
    book_update_id: int
    role: str
    submitted_monotonic: Decimal | None = None
    acknowledged_monotonic: Decimal | None = None


@dataclass(frozen=True, slots=True)
class HftFill:
    fill_id: str
    order_id: str
    timestamp: datetime
    side: HftSide
    price: Decimal
    quantity: Decimal
    maker: bool
    role: str
    fee: Decimal
    realized_pnl: Decimal
    queue_wait_ms: Decimal
    queue_ahead_at_placement: Decimal
    monotonic_timestamp: Decimal | None = None


@dataclass(slots=True)
class HftInventory:
    quantity: Decimal
    entry_price: Decimal
    opened_at: datetime
    opened_monotonic: Decimal | None = None


class HftPaperAccount:
    """One working quote and one directional inventory; no real-order adapter."""

    def __init__(self, starting_equity: Decimal, fees: HftFeeSchedule) -> None:
        if starting_equity <= 0:
            raise HftExecutionError("starting equity must be positive")
        self.starting_equity = starting_equity
        self.balance = starting_equity
        self.fees = fees
        self.working_order: HftWorkingOrder | None = None
        self.inventory: HftInventory | None = None
        self.fills: list[HftFill] = []
        self.maker_fees = Decimal("0")
        self.taker_fees = Decimal("0")
        self.realized_pnl = Decimal("0")
        self.funding = Decimal("0")
        self.quotes_submitted = 0
        self.quotes_cancelled = 0
        self.cancelled_before_fill = 0
        self.taker_exits = 0
        self._order_sequence = 0
        self._fill_sequence = 0

    @property
    def inventory_quantity(self) -> Decimal:
        return self.inventory.quantity if self.inventory is not None else Decimal("0")

    def marked_equity(self, mark_price: Decimal) -> Decimal:
        unrealized = Decimal("0")
        if self.inventory is not None:
            unrealized = (
                mark_price - self.inventory.entry_price
            ) * self.inventory.quantity
        return self.balance + unrealized

    def place_entry(
        self,
        decision: HftRiskDecision,
        side: HftSide,
        price: Decimal,
        book: L2OrderBook,
        submitted_at: datetime,
        acknowledged_at: datetime,
        submitted_monotonic: Decimal | None = None,
        acknowledged_monotonic: Decimal | None = None,
    ) -> HftWorkingOrder:
        if not decision.approved:
            raise HftExecutionError("Risk approval required")
        if self.working_order is not None or self.inventory is not None:
            raise HftExecutionError("entry requires no order or inventory")
        return self._place(
            side, price, decision.quantity, book, submitted_at, acknowledged_at,
            "ENTRY", submitted_monotonic, acknowledged_monotonic,
        )

    def place_exit(
        self,
        side: HftSide,
        price: Decimal,
        book: L2OrderBook,
        submitted_at: datetime,
        acknowledged_at: datetime,
        submitted_monotonic: Decimal | None = None,
        acknowledged_monotonic: Decimal | None = None,
    ) -> HftWorkingOrder:
        if self.inventory is None or self.working_order is not None:
            raise HftExecutionError("exit requires inventory and no working order")
        expected = HftSide.SELL if self.inventory.quantity > 0 else HftSide.BUY
        if side is not expected:
            raise HftExecutionError("exit side would increase inventory")
        return self._place(
            side, price, abs(self.inventory.quantity), book,
            submitted_at, acknowledged_at, "EXIT",
            submitted_monotonic, acknowledged_monotonic,
        )

    def _place(
        self,
        side: HftSide,
        price: Decimal,
        quantity: Decimal,
        book: L2OrderBook,
        submitted_at: datetime,
        acknowledged_at: datetime,
        role: str,
        submitted_monotonic: Decimal | None,
        acknowledged_monotonic: Decimal | None,
    ) -> HftWorkingOrder:
        require_utc(submitted_at, "submitted_at")
        require_utc(acknowledged_at, "acknowledged_at")
        if not book.valid or book.last_update_id is None:
            raise HftExecutionError("valid book required")
        if acknowledged_at < submitted_at:
            raise HftExecutionError("negative acknowledgement latency")
        if (
            submitted_monotonic is not None
            and acknowledged_monotonic is not None
            and acknowledged_monotonic < submitted_monotonic
        ):
            raise HftExecutionError("negative monotonic acknowledgement latency")
        if side is HftSide.BUY and price >= book.best_ask:
            raise HftExecutionError("post-only BUY would cross")
        if side is HftSide.SELL and price <= book.best_bid:
            raise HftExecutionError("post-only SELL would cross")
        visible = book.bids.get(price, Decimal("0")) if side is HftSide.BUY else book.asks.get(price, Decimal("0"))
        self._order_sequence += 1
        order = HftWorkingOrder(
            order_id=f"hft-paper-{self._order_sequence}",
            side=side,
            price=price,
            quantity=quantity,
            remaining_quantity=quantity,
            queue_ahead=visible,
            initial_queue_ahead=visible,
            placed_at=submitted_at,
            acknowledged_at=acknowledged_at,
            book_update_id=book.last_update_id,
            role=role,
            submitted_monotonic=submitted_monotonic,
            acknowledged_monotonic=acknowledged_monotonic,
        )
        self.working_order = order
        self.quotes_submitted += 1
        return order

    def cancel(self, reason: str) -> HftWorkingOrder | None:
        del reason
        order = self.working_order
        if order is not None:
            if order.remaining_quantity == order.quantity:
                self.cancelled_before_fill += 1
            self.quotes_cancelled += 1
            self.working_order = None
        return order

    def on_trade(self, trade: AggregateTrade) -> HftFill | None:
        order = self.working_order
        if order is None or trade.price != order.price:
            return None
        trade_monotonic = (
            Decimal(trade.received_monotonic_ns) / Decimal("1000000000")
            if trade.received_monotonic_ns is not None else None
        )
        if (
            trade_monotonic is not None
            and order.submitted_monotonic is not None
            and trade_monotonic < order.submitted_monotonic
        ):
            return None
        if (
            trade_monotonic is None
            and trade.received_at < order.placed_at
        ):
            return None
        hits_order_side = (
            (order.side is HftSide.BUY and trade.buyer_is_maker)
            or (order.side is HftSide.SELL and not trade.buyer_is_maker)
        )
        if not hits_order_side:
            return None
        available = trade.quantity
        if order.queue_ahead > 0:
            consumed = min(order.queue_ahead, available)
            order.queue_ahead -= consumed
            available -= consumed
        if available <= 0:
            return None
        quantity = min(order.remaining_quantity, available)
        fill = self._apply_fill(
            order, quantity, trade.price, trade.received_at,
            monotonic_timestamp=trade_monotonic, maker=True,
        )
        order.remaining_quantity -= quantity
        if order.remaining_quantity == 0:
            self.working_order = None
        return fill

    def emergency_exit(
        self,
        price: Decimal,
        timestamp: datetime,
        reason: str,
        monotonic_timestamp: Decimal | None = None,
    ) -> HftFill:
        del reason
        if self.inventory is None:
            raise HftExecutionError("no inventory to exit")
        self.cancel("emergency_exit")
        side = HftSide.SELL if self.inventory.quantity > 0 else HftSide.BUY
        self._order_sequence += 1
        synthetic = HftWorkingOrder(
            order_id=f"hft-paper-{self._order_sequence}",
            side=side,
            price=price,
            quantity=abs(self.inventory.quantity),
            remaining_quantity=abs(self.inventory.quantity),
            queue_ahead=Decimal("0"),
            initial_queue_ahead=Decimal("0"),
            placed_at=timestamp,
            acknowledged_at=timestamp,
            book_update_id=-1,
            role="SAFETY_EXIT",
            submitted_monotonic=monotonic_timestamp,
            acknowledged_monotonic=monotonic_timestamp,
        )
        self.taker_exits += 1
        return self._apply_fill(
            synthetic, synthetic.quantity, price, timestamp,
            monotonic_timestamp=monotonic_timestamp, maker=False,
        )

    def _apply_fill(
        self,
        order: HftWorkingOrder,
        quantity: Decimal,
        price: Decimal,
        timestamp: datetime,
        *,
        monotonic_timestamp: Decimal | None,
        maker: bool,
    ) -> HftFill:
        fee_rate = self.fees.maker_rate if maker else self.fees.taker_rate
        fee = quantity * price * fee_rate
        realized = Decimal("0")
        signed = quantity if order.side is HftSide.BUY else -quantity
        if order.role == "ENTRY":
            if self.inventory is None:
                self.inventory = HftInventory(
                    signed, price, timestamp, monotonic_timestamp
                )
            else:
                if (
                    self.inventory.quantity * signed <= 0
                    or self.inventory.entry_price != price
                    or self.working_order is not order
                ):
                    raise HftExecutionError("entry fill would pyramid")
                self.inventory.quantity += signed
        else:
            if self.inventory is None or self.inventory.quantity * signed >= 0:
                raise HftExecutionError("exit fill does not reduce inventory")
            closed = min(abs(self.inventory.quantity), quantity)
            direction = Decimal("1") if self.inventory.quantity > 0 else Decimal("-1")
            realized = (price - self.inventory.entry_price) * closed * direction
            remaining = abs(self.inventory.quantity) - closed
            if remaining == 0:
                self.inventory = None
            else:
                self.inventory.quantity = direction * remaining
        self.balance += realized - fee
        self.realized_pnl += realized
        if maker:
            self.maker_fees += fee
        else:
            self.taker_fees += fee
        self._fill_sequence += 1
        if (
            monotonic_timestamp is not None
            and order.submitted_monotonic is not None
        ):
            wait_ms = (
                monotonic_timestamp - order.submitted_monotonic
            ) * Decimal("1000")
        else:
            wait_ms = Decimal(str(
                (timestamp - order.placed_at).total_seconds() * 1000
            ))
        if wait_ms < 0:
            raise HftExecutionError("negative monotonic queue wait")
        fill = HftFill(
            fill_id=f"hft-fill-{self._fill_sequence}",
            order_id=order.order_id,
            timestamp=timestamp,
            side=order.side,
            price=price,
            quantity=quantity,
            maker=maker,
            role=order.role,
            fee=fee,
            realized_pnl=realized,
            queue_wait_ms=wait_ms,
            queue_ahead_at_placement=order.initial_queue_ahead,
            monotonic_timestamp=monotonic_timestamp,
        )
        self.fills.append(fill)
        return fill

    def apply_funding(self, amount: Decimal) -> None:
        self.balance += amount
        self.funding += amount

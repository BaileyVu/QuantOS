"""Liquidity-limited paper execution; no authenticated or real-order adapter."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from quantos.domain.alpha.hft import HftSide
from quantos.domain.alpha.retail import RetailSignal
from quantos.domain.alpha.reclaim import ReclaimSignal
from quantos.domain.market_data.hft import L2OrderBook
from quantos.domain.risk.hft import HftFeeSchedule
from quantos.domain.risk.retail import RetailRiskDecision, RetailRiskPolicy, FullEquityRiskPolicy, ProtectedRiskDecision


@dataclass(slots=True)
class RetailOrder:
    order_id: str
    signal_id: str
    kind: str
    side: HftSide
    quantity: Decimal
    remaining: Decimal
    submitted_at: datetime
    submitted_ns: int
    acknowledged_ns: int
    expires_ns: int
    book_update_id: int
    last_fill_update_id: int
    reason: str
    reference_price: Decimal
    last_observed_price: Decimal | None = None
    last_observed_visible: Decimal = Decimal("0")
    consumed_at_price: Decimal = Decimal("0")
    strategy_id: str = "Q03-directional-v1"
    horizon_seconds: int = 60
    consumed_by_price: dict[str, Decimal] = field(default_factory=dict)


@dataclass(slots=True)
class RetailPosition:
    signal_id: str
    entry_order_id: str
    side: HftSide
    quantity: Decimal
    entry_price: Decimal
    entry_fee_remaining: Decimal
    opened_ns: int
    strategy_id: str = "Q03-directional-v1"
    horizon_seconds: int = 60
    funding_remaining: Decimal = Decimal("0")


@dataclass(slots=True)
class ProtectedOrder(RetailOrder):
    stop_price: Decimal = Decimal("0")
    target_price: Decimal = Decimal("0")


@dataclass(slots=True)
class ProtectedPosition(RetailPosition):
    stop_price: Decimal = Decimal("0")
    target_price: Decimal = Decimal("0")
    exit_reason: str = ""
    exit_consumed_by_price: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetailFill:
    fill_id: str
    order_id: str
    signal_id: str
    kind: str
    side: HftSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    timestamp: datetime
    received_ns: int
    book_update_id: int
    gross_pnl: Decimal
    net_pnl: Decimal
    reason: str
    strategy_id: str = "Q03-directional-v1"


class RetailPaperAccount:
    def __init__(self, fees: HftFeeSchedule, policy: RetailRiskPolicy) -> None:
        self.starting_equity = Decimal("200")
        self.balance = self.starting_equity
        self.peak_equity = self.starting_equity
        self.fees, self.policy = fees, policy
        self.position: RetailPosition | None = None
        self.pending: RetailOrder | None = None
        self.fills: list[RetailFill] = []
        self.lifecycle: list[dict] = []
        self.orders_submitted = 0
        self.orders_cancelled = 0
        self.realized_gross_pnl = Decimal("0")
        self.realized_net_pnl = Decimal("0")
        self.total_fees = Decimal("0")
        self._order_sequence = 0
        self._fill_sequence = 0
        self.total_funding = Decimal("0")
        self.funding_ledger: list[dict] = []
        self.funding_due = None
        self.last_funding_time = None
        self.funding_rate = Decimal("0")
        self.funding_mark = Decimal("0")
        self.risk_off = False
        self.day = None
        self.daily_start_equity = self.starting_equity

    def marked_equity(self, bid: Decimal, ask: Decimal) -> Decimal:
        position = self.position
        if position is None:
            return self.balance
        mark = bid if position.side is HftSide.BUY else ask
        direction = Decimal("1") if position.side is HftSide.BUY else Decimal("-1")
        unrealized = (mark - position.entry_price) * position.quantity * direction
        estimated_exit_fee = mark * position.quantity * self.fees.taker_rate
        return self.balance + unrealized - estimated_exit_fee

    def submit_entry(
        self, decision: RetailRiskDecision, signal: RetailSignal,
        timestamp: datetime, now_ns: int, reference_price: Decimal,
    ) -> RetailOrder:
        if not decision.approved or decision.quantity <= 0:
            raise ValueError("retail Risk approval required")
        if self.position is not None or self.pending is not None:
            raise ValueError("retail entry requires flat account")
        if now_ns >= signal.expires_ns or now_ns < signal.observed_ns:
            raise ValueError("retail signal expired or clock ambiguous")
        if reference_price <= 0:
            raise ValueError("retail reference price must be positive")
        if isinstance(self.policy, FullEquityRiskPolicy):
            if not isinstance(decision, ProtectedRiskDecision) or not decision.stop_price.is_finite() or decision.stop_price <= 0:
                raise ValueError("explicit protective Risk approval required")
        order = self._submit(
            signal.signal_id, "ENTRY", signal.side, decision.quantity,
            timestamp, now_ns, signal.expires_ns, signal.book_update_id,
            "directional_signal", reference_price,
        )
        if isinstance(self.policy, FullEquityRiskPolicy):
            from dataclasses import asdict
            order = ProtectedOrder(**asdict(order), stop_price=decision.stop_price, target_price=decision.target_price)
            self.pending = order
        order.strategy_id, order.horizon_seconds = signal.strategy_id, signal.horizon_seconds
        if isinstance(signal, ReclaimSignal):
            self.lifecycle.append({"event": "alpha_context", "signal_id": signal.signal_id,
                "order_id": order.order_id, "target_price": signal.target_price,
                "invalidation_price": signal.invalidation_price, "source_window": signal.source_window,
                "observed_ns": signal.observed_ns, "expires_ns": signal.expires_ns})
        return order

    def submit_exit(
        self, reason: str, timestamp: datetime, now_ns: int,
        book_update_id: int,
    ) -> RetailOrder:
        if self.position is None or self.pending is not None:
            raise ValueError("retail exit requires position and no order")
        side = HftSide.SELL if self.position.side is HftSide.BUY else HftSide.BUY
        order = self._submit(
            self.position.signal_id, "EXIT", side, self.position.quantity,
            timestamp, now_ns, now_ns + 2_000_000_000, book_update_id,
            reason, Decimal("0"),
        )
        order.strategy_id = self.position.strategy_id
        if isinstance(self.position, ProtectedPosition):
            order.consumed_by_price = self.position.exit_consumed_by_price
        return order

    def _submit(
        self, signal_id: str, kind: str, side: HftSide,
        quantity: Decimal, timestamp: datetime, now_ns: int,
        expires_ns: int, book_update_id: int, reason: str,
        reference_price: Decimal,
    ) -> RetailOrder:
        self._order_sequence += 1
        order = RetailOrder(
            f"retail-paper-{self._order_sequence}", signal_id, kind, side,
            quantity, quantity, timestamp, now_ns,
            now_ns + self.policy.acknowledgement_ms * 1_000_000,
            expires_ns, book_update_id, book_update_id, reason,
            reference_price,
        )
        self.pending = order
        self.orders_submitted += 1
        self.lifecycle.append({
            "event": "submitted", "order_id": order.order_id,
            "signal_id": signal_id, "kind": kind, "side": side.value,
            "quantity": quantity, "submitted_at": timestamp,
            "submitted_ns": now_ns, "acknowledged_ns": order.acknowledged_ns,
            "reason": reason,
        })
        return order

    def cancel_pending(self, reason: str, timestamp: datetime) -> None:
        order = self.pending
        if order is None:
            return
        self.lifecycle.append({
            "event": "cancelled", "order_id": order.order_id,
            "signal_id": order.signal_id, "remaining": order.remaining,
            "timestamp": timestamp, "reason": reason,
        })
        self.orders_cancelled += 1
        self.pending = None

    def on_depth(
        self, book: L2OrderBook, timestamp: datetime, received_ns: int,
    ) -> RetailFill | None:
        order = self.pending
        if order is None:
            return None
        if not book.valid or book.last_received_monotonic_ns != received_ns:
            self.cancel_pending("invalid_or_ambiguous_depth", timestamp)
            return None
        if received_ns < order.submitted_ns:
            self.cancel_pending("clock_regression", timestamp)
            return None
        if received_ns >= order.expires_ns:
            self.cancel_pending("order_expired", timestamp)
            return None
        if received_ns < order.acknowledged_ns:
            return None
        if book.last_update_id <= order.last_fill_update_id:
            return None
        if order.side is HftSide.BUY:
            price = book.best_ask
            visible = book.asks[price]
            simulated = price * (
                1 + self.policy.slippage_bps_per_leg / Decimal("10000")
            )
        else:
            price = book.best_bid
            visible = book.bids[price]
            simulated = price * (
                1 - self.policy.slippage_bps_per_leg / Decimal("10000")
            )
        if isinstance(order, ProtectedOrder):
            direction = 1 if order.side is HftSide.BUY else -1
            if (simulated - order.stop_price) * direction <= 0 or (order.target_price - simulated) * direction <= 0:
                self.cancel_pending("protective_exit_invalid_at_fill", timestamp)
                return None
        if order.kind == "ENTRY":
            adverse = (
                (price - order.reference_price)
                if order.side is HftSide.BUY
                else (order.reference_price - price)
            ) / order.reference_price * Decimal("10000")
            if adverse > Decimal("5"):
                self.cancel_pending("adverse_price_before_fill", timestamp)
                return None
        if order.last_observed_price != price:
            order.last_observed_price = price
            order.consumed_at_price = order.consumed_by_price.get(str(price), Decimal("0"))
        order.last_observed_visible = visible
        quantity = min(order.remaining, max(visible - order.consumed_at_price, Decimal("0")))
        if quantity <= 0:
            return None
        fee = quantity * simulated * self.fees.taker_rate
        gross = net = Decimal("0")
        if order.kind == "ENTRY":
            self.balance -= fee
            if self.position is None:
                self.position = RetailPosition(
                    order.signal_id, order.order_id, order.side, quantity,
                    simulated, fee, received_ns, order.strategy_id, order.horizon_seconds,
                )
                if isinstance(order, ProtectedOrder):
                    from dataclasses import asdict
                    self.position = ProtectedPosition(**asdict(self.position), stop_price=order.stop_price, target_price=order.target_price)
            else:
                position = self.position
                if position.entry_order_id != order.order_id:
                    raise ValueError("retail entry would average into another order")
                position.entry_price = (
                    position.entry_price * position.quantity + simulated * quantity
                ) / (position.quantity + quantity)
                position.quantity += quantity
                position.entry_fee_remaining += fee
        else:
            position = self.position
            if position is None or position.quantity < quantity:
                raise ValueError("retail exit exceeds inventory")
            direction = Decimal("1") if position.side is HftSide.BUY else Decimal("-1")
            gross = (simulated - position.entry_price) * quantity * direction
            entry_fee = position.entry_fee_remaining * quantity / position.quantity
            funding = position.funding_remaining * quantity / position.quantity
            net = gross - entry_fee - fee - funding
            position.funding_remaining -= funding
            self.balance += gross - fee
            self.realized_gross_pnl += gross
            self.realized_net_pnl += net
            position.quantity -= quantity
            position.entry_fee_remaining -= entry_fee
            if position.quantity == 0:
                self.position = None
                self.peak_equity = max(self.peak_equity, self.balance)
        order.remaining -= quantity
        order.consumed_at_price += quantity
        order.consumed_by_price[str(price)] = order.consumed_at_price
        order.last_fill_update_id = book.last_update_id
        self.total_fees += fee
        self._fill_sequence += 1
        fill = RetailFill(
            f"retail-fill-{self._fill_sequence}", order.order_id,
            order.signal_id, order.kind, order.side, quantity, simulated, fee,
            timestamp, received_ns, book.last_update_id, gross, net,
            order.reason, order.strategy_id,
        )
        self.fills.append(fill)
        self.lifecycle.append({
            "event": "filled" if order.remaining == 0 else "partial_fill",
            "order_id": order.order_id, "signal_id": order.signal_id,
            "fill_id": fill.fill_id, "quantity": quantity,
            "remaining": order.remaining, "price": simulated,
            "fee": fee, "net_pnl": net, "timestamp": timestamp,
            "book_update_id": book.last_update_id,
        })
        if order.strategy_id == "Q05-LR-v1":
            self.lifecycle[-1].update({"observed_price": price,
                "slippage_cost": abs(simulated - price) * quantity,
                "received_ns": received_ns, "acknowledged_ns": order.acknowledged_ns})
        if order.remaining == 0:
            self.pending = None
        return fill

    def observe_protective_price(self, price, timestamp):
        position = self.position
        if not isinstance(position, ProtectedPosition) or not price.is_finite() or price <= 0:
            return
        direction = 1 if position.side is HftSide.BUY else -1
        if (price - position.stop_price) * direction <= 0:
            position.exit_reason = "protective_stop"
        elif not position.exit_reason and (price - position.target_price) * direction >= 0:
            position.exit_reason = "take_profit"
        if position.exit_reason and self.pending is not None and self.pending.kind == "ENTRY":
            self.cancel_pending("protective_trigger_cancels_entry", timestamp)

    def manage_protection(self, book, timestamp, received_ns, submitted_ns, *, force=False):
        """Only validated fresh depth can trigger an executable protective intent."""
        position = self.position
        if not isinstance(position, ProtectedPosition):
            raise ValueError("protective state missing or ambiguous")
        if not book.valid or book.last_received_monotonic_ns != received_ns or submitted_ns < received_ns:
            self.cancel_pending("protective_execution_uncertain", timestamp)
            return
        direction = 1 if position.side is HftSide.BUY else -1
        mark = book.best_bid if direction > 0 else book.best_ask
        equity = self.marked_equity(book.best_bid, book.best_ask)
        if equity <= max(self.peak_equity * (1 - self.policy.maximum_drawdown_fraction),
                         self.daily_start_equity * (1 - self.policy.maximum_daily_loss_fraction)):
            self.risk_off = True
        reason = position.exit_reason or None
        if force or self.risk_off:
            reason = "risk_or_recovery_exit"
        elif (mark - position.stop_price) * direction <= 0:
            reason = "protective_stop"
        elif (mark - position.target_price) * direction >= 0:
            reason = "take_profit"
        elif received_ns - position.opened_ns >= position.horizon_seconds * 1_000_000_000:
            reason = "horizon_expired"
        if reason:
            position.exit_reason = reason
            if self.pending is not None and self.pending.kind == "ENTRY":
                self.cancel_pending("protective_exit_cancels_partial_entry", timestamp)
            if self.pending is not None and received_ns >= self.pending.expires_ns:
                self.cancel_pending("protective_exit_retry_after_expiry", timestamp)
            if self.pending is None:
                self.submit_exit(reason, timestamp, submitted_ns, book.last_update_id)

    def observe_day(self, timestamp: datetime) -> None:
        day = timestamp.date().isoformat()
        if self.day != day:
            self.day, self.daily_start_equity = day, self.balance

    def observe_funding(self, event) -> None:
        # No favorable funding credits from uncertain public settlement data.
        self.settle_funding(event.exchange_time)
        if (self.last_funding_time is None or event.next_funding_time > self.last_funding_time) and (self.funding_due is None or event.next_funding_time > self.funding_due):
            self.funding_due = event.next_funding_time
        self.funding_rate, self.funding_mark = event.funding_rate, event.mark_price

    def settle_funding(self, timestamp: datetime) -> None:
        if self.funding_due is None or timestamp < self.funding_due:
            return
        if self.position is not None:
            cost = self.position.quantity * self.funding_mark * abs(self.funding_rate)
            self.balance -= cost
            self.total_funding += cost
            self.position.funding_remaining += cost
            self.funding_ledger.append({"timestamp": self.funding_due,
                "signal_id": self.position.signal_id, "strategy_id": self.position.strategy_id,
                "cost": cost, "basis": "conservative_public_rate_debit"})
        self.last_funding_time = self.funding_due
        self.funding_due = None

"""Provider-independent sequenced USD-M Futures L2 market state."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable

from quantos.domain.common import require_decimal, require_utc


HFT_SYMBOLS = frozenset({"BTCUSDC", "BTCUSDT"})
BookLevel = tuple[Decimal, Decimal]


class HftBookError(RuntimeError):
    """A book event or state is invalid and cannot authorize quoting."""


def _symbol(value: str) -> str:
    if value not in HFT_SYMBOLS:
        raise HftBookError("HFT symbol must be BTCUSDC or BTCUSDT")
    return value


def _levels(values: tuple[BookLevel, ...], name: str) -> tuple[BookLevel, ...]:
    seen: set[Decimal] = set()
    for price, quantity in values:
        require_decimal(price, f"{name} price")
        require_decimal(quantity, f"{name} quantity", non_negative=True)
        if price <= 0 or price in seen:
            raise HftBookError(f"invalid or duplicate {name} price")
        seen.add(price)
    return values


def _monotonic_ns(value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise HftBookError("received_monotonic_ns must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class DepthSnapshot:
    symbol: str
    last_update_id: int
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    exchange_time: datetime
    received_at: datetime
    received_monotonic_ns: int | None = None

    def __post_init__(self) -> None:
        _symbol(self.symbol)
        if type(self.last_update_id) is not int or self.last_update_id < 0:
            raise HftBookError("invalid snapshot update ID")
        _levels(self.bids, "bid")
        _levels(self.asks, "ask")
        require_utc(self.exchange_time, "exchange_time")
        require_utc(self.received_at, "received_at")
        _monotonic_ns(self.received_monotonic_ns)


@dataclass(frozen=True, slots=True)
class DepthDelta:
    symbol: str
    first_update_id: int
    final_update_id: int
    previous_final_update_id: int
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    exchange_time: datetime
    received_at: datetime
    transaction_time: datetime | None = None
    received_monotonic_ns: int | None = None

    def __post_init__(self) -> None:
        _symbol(self.symbol)
        for name in (
            "first_update_id", "final_update_id", "previous_final_update_id"
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise HftBookError(f"invalid {name}")
        if self.first_update_id > self.final_update_id:
            raise HftBookError("depth update ID range is reversed")
        _levels(self.bids, "bid")
        _levels(self.asks, "ask")
        require_utc(self.exchange_time, "exchange_time")
        require_utc(self.received_at, "received_at")
        if self.transaction_time is not None:
            require_utc(self.transaction_time, "transaction_time")
        _monotonic_ns(self.received_monotonic_ns)


@dataclass(frozen=True, slots=True)
class BookTicker:
    symbol: str
    update_id: int
    bid_price: Decimal
    bid_quantity: Decimal
    ask_price: Decimal
    ask_quantity: Decimal
    exchange_time: datetime
    received_at: datetime
    transaction_time: datetime | None = None
    received_monotonic_ns: int | None = None

    def __post_init__(self) -> None:
        _symbol(self.symbol)
        if type(self.update_id) is not int or self.update_id < 0:
            raise HftBookError("invalid bookTicker update ID")
        for name in ("bid_price", "bid_quantity", "ask_price", "ask_quantity"):
            value = getattr(self, name)
            require_decimal(value, name, non_negative="quantity" in name)
            if value <= 0:
                raise HftBookError(f"{name} must be positive")
        if self.bid_price >= self.ask_price:
            raise HftBookError("bookTicker is crossed")
        require_utc(self.exchange_time, "exchange_time")
        require_utc(self.received_at, "received_at")
        if self.transaction_time is not None:
            require_utc(self.transaction_time, "transaction_time")
        _monotonic_ns(self.received_monotonic_ns)


@dataclass(frozen=True, slots=True)
class AggregateTrade:
    symbol: str
    aggregate_trade_id: int
    price: Decimal
    quantity: Decimal
    buyer_is_maker: bool
    exchange_time: datetime
    received_at: datetime
    transaction_time: datetime | None = None
    received_monotonic_ns: int | None = None

    def __post_init__(self) -> None:
        _symbol(self.symbol)
        if type(self.aggregate_trade_id) is not int or self.aggregate_trade_id < 0:
            raise HftBookError("invalid aggregate trade ID")
        if type(self.buyer_is_maker) is not bool:
            raise HftBookError("buyer_is_maker must be boolean")
        for name in ("price", "quantity"):
            value = getattr(self, name)
            require_decimal(value, name)
            if value <= 0:
                raise HftBookError(f"{name} must be positive")
        require_utc(self.exchange_time, "exchange_time")
        require_utc(self.received_at, "received_at")
        if self.transaction_time is not None:
            require_utc(self.transaction_time, "transaction_time")
        _monotonic_ns(self.received_monotonic_ns)


@dataclass(frozen=True, slots=True)
class MarkPriceEvent:
    symbol: str
    mark_price: Decimal
    funding_rate: Decimal
    next_funding_time: datetime
    exchange_time: datetime
    received_at: datetime
    received_monotonic_ns: int | None = None

    def __post_init__(self) -> None:
        _symbol(self.symbol)
        require_decimal(self.mark_price, "mark_price")
        require_decimal(self.funding_rate, "funding_rate")
        if self.mark_price <= 0:
            raise HftBookError("mark_price must be positive")
        require_utc(self.next_funding_time, "next_funding_time")
        require_utc(self.exchange_time, "exchange_time")
        require_utc(self.received_at, "received_at")
        _monotonic_ns(self.received_monotonic_ns)


class L2OrderBook:
    """Mutable local book with explicit validity and provider update sequencing."""

    def __init__(self, symbol: str) -> None:
        self.symbol = _symbol(symbol)
        self.bids: dict[Decimal, Decimal] = {}
        self.asks: dict[Decimal, Decimal] = {}
        self.last_update_id: int | None = None
        self.last_exchange_time: datetime | None = None
        self.last_received_at: datetime | None = None
        self.last_received_monotonic_ns: int | None = None
        self.valid = False
        self.invalid_reason = "not_bootstrapped"

    def invalidate(self, reason: str) -> None:
        self.valid = False
        self.invalid_reason = reason

    def bootstrap(
        self, snapshot: DepthSnapshot, buffered: Iterable[DepthDelta]
    ) -> int:
        if snapshot.symbol != self.symbol:
            raise HftBookError("snapshot symbol differs from book")
        self.bids = {price: quantity for price, quantity in snapshot.bids if quantity}
        self.asks = {price: quantity for price, quantity in snapshot.asks if quantity}
        self.last_update_id = snapshot.last_update_id
        self.last_exchange_time = snapshot.exchange_time
        self.last_received_at = snapshot.received_at
        self.last_received_monotonic_ns = snapshot.received_monotonic_ns
        self.valid = False
        self.invalid_reason = "awaiting_snapshot_bridge"
        self._validate_shape()

        retained = tuple(
            item for item in buffered
            if item.symbol == self.symbol
            and item.final_update_id >= snapshot.last_update_id
        )
        bridge_index = next(
            (
                index for index, item in enumerate(retained)
                if item.first_update_id <= snapshot.last_update_id
                <= item.final_update_id
            ),
            None,
        )
        if bridge_index is None:
            raise HftBookError("buffered deltas do not bridge snapshot update ID")

        first = retained[bridge_index]
        self._apply_levels(first)
        self.last_update_id = first.final_update_id
        self.last_exchange_time = first.exchange_time
        self.last_received_at = first.received_at
        self.last_received_monotonic_ns = first.received_monotonic_ns
        self.valid = True
        self.invalid_reason = ""
        self._validate_shape()
        applied = 1
        for item in retained[bridge_index + 1:]:
            if self.apply(item):
                applied += 1
        return applied

    def apply(self, delta: DepthDelta) -> bool:
        if delta.symbol != self.symbol:
            self.invalidate("symbol_mismatch")
            raise HftBookError("delta symbol differs from book")
        if not self.valid or self.last_update_id is None:
            raise HftBookError("book must be freshly bootstrapped")
        if delta.final_update_id <= self.last_update_id:
            return False
        if delta.previous_final_update_id != self.last_update_id:
            self.invalidate("sequence_gap")
            raise HftBookError("depth sequence gap")
        self._apply_levels(delta)
        self.last_update_id = delta.final_update_id
        self.last_exchange_time = delta.exchange_time
        self.last_received_at = delta.received_at
        self.last_received_monotonic_ns = delta.received_monotonic_ns
        try:
            self._validate_shape()
        except HftBookError:
            self.invalidate("crossed_or_empty_book")
            raise
        return True

    def _apply_levels(self, delta: DepthDelta) -> None:
        for levels, updates in ((self.bids, delta.bids), (self.asks, delta.asks)):
            for price, quantity in updates:
                if quantity == 0:
                    levels.pop(price, None)
                else:
                    levels[price] = quantity

    def _validate_shape(self) -> None:
        if not self.bids or not self.asks:
            raise HftBookError("book side is empty")
        if self.best_bid >= self.best_ask:
            raise HftBookError("book is crossed")

    @property
    def best_bid(self) -> Decimal:
        if not self.bids:
            raise HftBookError("bid book is empty")
        return max(self.bids)

    @property
    def best_ask(self) -> Decimal:
        if not self.asks:
            raise HftBookError("ask book is empty")
        return min(self.asks)

    def bid_levels(self, count: int) -> tuple[BookLevel, ...]:
        return tuple(
            (price, self.bids[price])
            for price in sorted(self.bids, reverse=True)[:count]
        )

    def ask_levels(self, count: int) -> tuple[BookLevel, ...]:
        return tuple(
            (price, self.asks[price])
            for price in sorted(self.asks)[:count]
        )

    def event_age(self, now: datetime) -> timedelta:
        require_utc(now, "now")
        if self.last_received_at is None:
            raise HftBookError("book has no receive timestamp")
        return now - self.last_received_at

    def require_fresh(self, now: datetime, maximum_age: timedelta) -> None:
        if not self.valid:
            raise HftBookError(f"book is invalid: {self.invalid_reason}")
        age = self.event_age(now)
        if age < timedelta(0) or age > maximum_age:
            self.invalidate("stale_stream")
            raise HftBookError("book is stale")

    def require_fresh_monotonic(
        self, now_ns: int, maximum_age: timedelta
    ) -> None:
        if not self.valid:
            raise HftBookError(f"book is invalid: {self.invalid_reason}")
        if self.last_received_monotonic_ns is None:
            raise HftBookError("book has no monotonic receive timestamp")
        age_ns = now_ns - self.last_received_monotonic_ns
        maximum_ns = int(maximum_age.total_seconds() * 1_000_000_000)
        if age_ns < 0 or age_ns > maximum_ns:
            self.invalidate("stale_stream")
            raise HftBookError("book is stale")

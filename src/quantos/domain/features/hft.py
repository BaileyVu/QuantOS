"""Causal event-driven microstructure features for HFT V1."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from quantos.domain.common import require_decimal, require_utc
from quantos.domain.market_data.hft import AggregateTrade, L2OrderBook


class HftFeatureError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HftFeaturePolicy:
    depth_levels: int = 5
    obi_window: int = 100
    trade_flow_seconds: Decimal = Decimal("5")
    volatility_seconds: Decimal = Decimal("10")

    def __post_init__(self) -> None:
        if self.depth_levels < 1 or self.obi_window < 2:
            raise HftFeatureError("invalid HFT feature lookback")
        if self.trade_flow_seconds <= 0 or self.volatility_seconds <= 0:
            raise HftFeatureError("feature windows must be positive")


@dataclass(frozen=True, slots=True)
class HftFeatures:
    timestamp: datetime
    book_update_id: int
    best_bid: Decimal
    best_ask: Decimal
    best_bid_quantity: Decimal
    best_ask_quantity: Decimal
    mid_price: Decimal
    spread_bps: Decimal
    static_obi: Decimal
    standardized_obi: Decimal
    vamp_price: Decimal
    microprice: Decimal
    alpha_bps: Decimal
    signed_trade_flow: Decimal
    realized_volatility_bps: Decimal
    inventory_quantity: Decimal
    feed_latency_ms: Decimal
    event_age_ms: Decimal


class HftFeatureEngine:
    def __init__(self, policy: HftFeaturePolicy = HftFeaturePolicy()) -> None:
        self.policy = policy
        self._obi: list[Decimal] = []
        self._trades: list[AggregateTrade] = []
        self._mids: list[tuple[datetime, Decimal]] = []

    def on_trade(self, trade: AggregateTrade) -> None:
        self._trades.append(trade)
        self._trim_trades(trade.received_at)

    def compute(
        self,
        book: L2OrderBook,
        now: datetime,
        inventory_quantity: Decimal = Decimal("0"),
    ) -> HftFeatures:
        require_utc(now, "now")
        require_decimal(inventory_quantity, "inventory_quantity")
        if not book.valid or book.last_update_id is None:
            raise HftFeatureError("valid book required")
        bids = book.bid_levels(self.policy.depth_levels)
        asks = book.ask_levels(self.policy.depth_levels)
        if len(bids) < self.policy.depth_levels or len(asks) < self.policy.depth_levels:
            raise HftFeatureError("insufficient L2 depth")
        bid, bid_quantity = bids[0]
        ask, ask_quantity = asks[0]
        mid = (bid + ask) / Decimal("2")
        spread_bps = (ask - bid) / mid * Decimal("10000")
        best_total = bid_quantity + ask_quantity
        if best_total <= 0:
            raise HftFeatureError("invalid best-level quantity")
        static_obi = (bid_quantity - ask_quantity) / best_total
        self._obi.append(static_obi)
        if len(self._obi) > self.policy.obi_window:
            self._obi.pop(0)
        standardized = self._zscore(static_obi)
        vamp = self._vamp(bids, asks)
        microprice = (
            bid * ask_quantity + ask * bid_quantity
        ) / best_total
        alpha_bps = (vamp - mid) / mid * Decimal("10000")

        self._trim_trades(now)
        flow = sum(
            (
                trade.quantity if not trade.buyer_is_maker else -trade.quantity
                for trade in self._trades
            ),
            Decimal("0"),
        )
        self._mids.append((now, mid))
        cutoff = now - timedelta(
            microseconds=int(self.policy.volatility_seconds * Decimal("1000000"))
        )
        while self._mids and self._mids[0][0] < cutoff:
            self._mids.pop(0)
        volatility = self._volatility()
        received = book.last_received_at
        exchange = book.last_exchange_time
        if received is None or exchange is None:
            raise HftFeatureError("book timestamps are missing")
        feed_ms = Decimal(str((received - exchange).total_seconds() * 1000))
        age_ms = Decimal(str((now - received).total_seconds() * 1000))
        if feed_ms < 0 or age_ms < 0:
            raise HftFeatureError("future market timestamp")
        return HftFeatures(
            timestamp=now,
            book_update_id=book.last_update_id,
            best_bid=bid,
            best_ask=ask,
            best_bid_quantity=bid_quantity,
            best_ask_quantity=ask_quantity,
            mid_price=mid,
            spread_bps=spread_bps,
            static_obi=static_obi,
            standardized_obi=standardized,
            vamp_price=vamp,
            microprice=microprice,
            alpha_bps=alpha_bps,
            signed_trade_flow=flow,
            realized_volatility_bps=volatility,
            inventory_quantity=inventory_quantity,
            feed_latency_ms=feed_ms,
            event_age_ms=age_ms,
        )

    def _trim_trades(self, now: datetime) -> None:
        cutoff = now - timedelta(
            microseconds=int(self.policy.trade_flow_seconds * Decimal("1000000"))
        )
        while self._trades and self._trades[0].received_at < cutoff:
            self._trades.pop(0)

    def _zscore(self, value: Decimal) -> Decimal:
        if len(self._obi) < 2:
            return Decimal("0")
        mean = sum(self._obi, Decimal("0")) / Decimal(len(self._obi))
        variance = sum(
            ((item - mean) ** 2 for item in self._obi), Decimal("0")
        ) / Decimal(len(self._obi))
        if variance == 0:
            return Decimal("0")
        return (value - mean) / variance.sqrt()

    @staticmethod
    def _vamp(
        bids: tuple[tuple[Decimal, Decimal], ...],
        asks: tuple[tuple[Decimal, Decimal], ...],
    ) -> Decimal:
        numerator = Decimal("0")
        denominator = Decimal("0")
        for (bid_price, bid_quantity), (ask_price, ask_quantity) in zip(bids, asks):
            numerator += bid_price * ask_quantity + ask_price * bid_quantity
            denominator += bid_quantity + ask_quantity
        if denominator <= 0:
            raise HftFeatureError("VAMP denominator is zero")
        return numerator / denominator

    def _volatility(self) -> Decimal:
        if len(self._mids) < 2:
            return Decimal("0")
        values = tuple(value for _, value in self._mids)
        returns = tuple(
            (current - previous) / previous
            for previous, current in zip(values, values[1:])
        )
        variance = sum((item * item for item in returns), Decimal("0"))
        variance /= Decimal(len(returns))
        return variance.sqrt() * Decimal("10000")

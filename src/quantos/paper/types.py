"""Small immutable contracts; monetary values are finite Decimal values."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_FLOOR
import json
from typing import Literal, Protocol

D = Decimal


def floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def dumps(value: object) -> str:
    return json.dumps(value, default=lambda x: str(x) if isinstance(x, Decimal)
                      else asdict(x), sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class Instrument:
    symbol: str
    margin_asset: str
    status: str
    tick: Decimal
    step: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal
    min_price: Decimal = D("0.00000001")
    max_price: Decimal = D("10000000")
    market_step: Decimal = D("0.00000001")
    market_min_qty: Decimal = D("0")
    market_max_qty: Decimal = D("100000000")
    multiplier_up: Decimal = D("1.05")
    multiplier_down: Decimal = D("0.95")


@dataclass(frozen=True)
class Candle:
    open_ms: int
    close_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class Quote:
    symbol: str
    time_ms: int
    received_ms: int
    bid: Decimal
    ask: Decimal
    bid_qty: Decimal
    ask_qty: Decimal
    mark: Decimal
    mark_time_ms: int | None = None


@dataclass(frozen=True)
class Trade:
    symbol: str
    trade_id: int
    time_ms: int
    price: Decimal
    quantity: Decimal
    buyer_maker: bool


@dataclass(frozen=True)
class Funding:
    symbol: str
    time_ms: int
    rate: Decimal
    mark: Decimal


@dataclass(frozen=True)
class Snapshot:
    at_ms: int
    instrument: Instrument
    quote: Quote
    candles_1m: tuple[Candle, ...]
    candles_5m: tuple[Candle, ...]


@dataclass(frozen=True)
class Signal:
    strategy_id: str
    strategy_version: str
    signal_id: str
    symbol: str
    side: Literal["LONG", "SHORT"]
    observed_ms: int
    expires_ms: int
    entry_type: Literal["LIMIT", "MARKET", "STOP"]
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    maximum_hold_seconds: int
    reason: str
    features: tuple[tuple[str, Decimal], ...] = ()


class Strategy(Protocol):
    strategy_id: str
    version: str

    def evaluate(self, snapshot: Snapshot) -> Signal | None: ...

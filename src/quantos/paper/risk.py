"""Independent, conservative sizing. A p5 target is a ceiling, never a promise."""
from dataclasses import dataclass
from decimal import Decimal
from math import ceil, floor, lcm

from .types import D, Instrument, Quote, Signal, floor_step


@dataclass(frozen=True)
class Policy:
    risk_fraction: Decimal = D("0.01")
    daily_loss: Decimal = D("0.10")
    drawdown: Decimal = D("0.25")
    gross_fraction: Decimal = D("3")     # max notional as a multiple of equity
    leverage: Decimal = D("3")           # max exchange leverage; the per-trade value is chosen in evaluate()
    margin_fraction: Decimal = D("0.5")  # one trade may lock at most this share of equity as margin
    maker_fee: Decimal = D("0.0002")
    taker_fee: Decimal = D("0.0006")
    slippage: Decimal = D("0.0015")
    min_net_rr: Decimal = D("1.2")
    maintenance_buffer: Decimal = D("0.02")
    max_spread: Decimal = D("0.002")

    def __post_init__(self):
        for name, value in vars(self).items():
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"invalid policy {name}")
        for name, ceiling in (("risk_fraction", ".05"), ("daily_loss", ".10"),
                              ("drawdown", ".25"), ("gross_fraction", "3"),
                              ("leverage", "3"), ("margin_fraction", "1")):
            if not 0 < getattr(self, name) <= D(ceiling):
                raise ValueError(f"hard ceiling: {name}")
        if self.leverage < 1 or self.slippage < D(".0015"):
            raise ValueError("invalid leverage or execution cost allowance")


@dataclass(frozen=True)
class Account:
    equity: Decimal
    day_start_equity: Decimal
    high_water: Decimal
    active_orders: int = 0
    reconciled: bool = True
    paused: bool = False


@dataclass(frozen=True)
class Decision:
    approved: bool
    reason: str
    quantity: Decimal = D(0)
    requested_risk: Decimal = D(0)
    achievable_risk: Decimal = D(0)
    notional: Decimal = D(0)
    margin: Decimal = D(0)
    leverage: Decimal = D(0)
    effective_leverage: Decimal = D(0)
    binding_constraint: str = ""


def quantity_step(instrument: Instrument) -> Decimal:
    """Quantity must satisfy LOT_SIZE and MARKET_LOT_SIZE for protective exits."""
    scale = 10 ** max(-instrument.step.as_tuple().exponent,
                      -instrument.market_step.as_tuple().exponent, 0)
    return D(lcm(int(instrument.step * scale), int(instrument.market_step * scale))) / scale


def valid_quote(q: Quote, now_ms: int) -> bool:
    values = (q.bid, q.ask, q.bid_qty, q.ask_qty, q.mark)
    return (all(x.is_finite() and x > 0 for x in values)
            and q.bid <= q.ask and 0 <= now_ms - q.time_ms <= 5000
            and 0 <= now_ms - q.received_ms <= 5000
            and 0 <= now_ms - (q.mark_time_ms or q.time_ms) <= 5000
            and abs(q.mark / ((q.bid + q.ask) / 2) - 1) <= D(".02"))


def evaluate(signal: Signal, instrument: Instrument, quote: Quote,
             account: Account, policy: Policy, now_ms: int) -> Decision:
    def reject(reason):
        return Decision(False, reason)
    if account.paused or not account.reconciled:
        return reject("UNRECONCILED_OR_PAUSED")
    if account.active_orders:
        return reject("EXISTING_EXPOSURE")
    if not all(x.is_finite() and x > 0 for x in
               (account.equity, account.day_start_equity, account.high_water)):
        return reject("INVALID_EQUITY")
    if account.equity <= account.day_start_equity * (1 - policy.daily_loss):
        return reject("DAILY_LOSS_BREAKER")
    if account.equity <= account.high_water * (1 - policy.drawdown):
        return reject("DRAWDOWN_BREAKER")
    if instrument.margin_asset != "USDC":
        return reject("INCOMPATIBLE_MARGIN_ASSET")
    if instrument.status != "TRADING" or signal.symbol != instrument.symbol or quote.symbol != signal.symbol:
        return reject("INVALID_INSTRUMENT")
    numeric = (instrument.tick, instrument.step, instrument.market_step,
               instrument.min_qty, instrument.max_qty, instrument.min_notional,
               instrument.min_price, instrument.max_price, instrument.market_max_qty,
               instrument.multiplier_down, instrument.multiplier_up)
    if not all(x.is_finite() and x > 0 for x in numeric):
        return reject("INVALID_FILTERS")
    if (not instrument.market_min_qty.is_finite() or instrument.market_min_qty < 0
        or instrument.min_qty > instrument.max_qty
        or instrument.market_min_qty > instrument.market_max_qty
        or instrument.multiplier_down > instrument.multiplier_up):
        return reject("INVALID_FILTERS")
    if not valid_quote(quote, now_ms):
        return reject("INVALID_DATA")
    if (quote.ask - quote.bid) / quote.bid > policy.max_spread:
        return reject("INSUFFICIENT_LIQUIDITY")
    if not signal.observed_ms <= now_ms < signal.expires_ms or now_ms - signal.observed_ms > 120000:
        return reject("EXPIRED_SIGNAL")
    if not 1 <= signal.maximum_hold_seconds <= 14400:
        return reject("INVALID_HOLD")
    if signal.side not in {"LONG", "SHORT"} or signal.entry_type not in {"LIMIT", "MARKET", "STOP"}:
        return reject("INVALID_SIGNAL")
    entry, stop, target = signal.entry_price, signal.stop_loss, signal.take_profit
    if not all(x.is_finite() and instrument.min_price <= x <= instrument.max_price
               and x % instrument.tick == 0 for x in (entry, stop, target)):
        return reject("PRICE_FILTER")
    if not (stop < entry < target if signal.side == "LONG" else target < entry < stop):
        return reject("INVALID_BRACKET")
    if not quote.mark * instrument.multiplier_down <= entry <= quote.mark * instrument.multiplier_up:
        return reject("PERCENT_PRICE_FILTER")
    if signal.entry_type == "LIMIT" and (entry > quote.bid if signal.side == "LONG" else entry < quote.ask):
        return reject("POST_ONLY_WOULD_CROSS")
    if signal.entry_type == "STOP" and (entry <= quote.ask if signal.side == "LONG" else entry >= quote.bid):
        return reject("STOP_ALREADY_TRIGGERED")
    distance = abs(entry - stop)
    # Leverage decision, step 1: the highest leverage whose isolated liquidation price stays at least twice
    # as far as the stop (+slippage). Conservative approximation; no claim about live brackets.
    safe = min(policy.leverage, D(floor(1 / (2 * (distance / entry + policy.slippage) + policy.maintenance_buffer))))
    if safe < 1:
        return reject("LIQUIDATION_BUFFER")
    entry_fee = policy.maker_fee if signal.entry_type == "LIMIT" else policy.taker_fee
    costs = entry * (entry_fee + policy.taker_fee + 2 * policy.slippage)
    unit_loss = distance + costs
    if (abs(target - entry) - costs) / unit_loss < policy.min_net_rr:
        return reject("NET_REWARD_RISK")
    budget = account.equity * policy.risk_fraction
    # Reserve both adverse price movement and entry costs inside the gross cap.
    max_entry = entry * (1 + policy.slippage)
    bounds = {"risk": budget / unit_loss,
              "gross_notional": account.equity * policy.gross_fraction / (max_entry * (1 + entry_fee)),
              "margin": account.equity * policy.margin_fraction / (max_entry / safe + costs),
              "exchange_max": min(instrument.max_qty, instrument.market_max_qty)}
    binding = min(bounds, key=bounds.get)
    quantity = floor_step(bounds[binding], quantity_step(instrument))
    if (quantity <= 0 or quantity < max(instrument.min_qty, instrument.market_min_qty)
            or quantity * entry < instrument.min_notional):
        return reject("BELOW_EXCHANGE_MINIMUM")
    notional = quantity * entry
    # Leverage decision, step 2: the lowest leverage that fits the margin budget, so unused leverage
    # never shortens the distance to liquidation and free margin stays available.
    leverage = min(safe, max(D(1), D(ceil(notional / (account.equity * policy.margin_fraction)))))
    return Decision(True, "APPROVED", quantity, budget, quantity * unit_loss, notional,
                    notional / leverage, leverage,
                    notional / account.equity, binding)

"""Independent, conservative sizing. A p5 target is a ceiling, never a promise."""
from dataclasses import dataclass
from decimal import Decimal
from math import ceil, floor, lcm

from .types import D, Instrument, Quote, Signal, floor_step
from .strategies.manifest import manifest_for


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
        for name, ceiling in (("risk_fraction", self.risk_ceiling), ("daily_loss", ".10"),
                              ("drawdown", self.drawdown_ceiling), ("gross_fraction", "3"),
                              ("leverage", "3"), ("margin_fraction", "1")):
            if not 0 < getattr(self, name) <= D(ceiling):
                raise ValueError(f"hard ceiling: {name}")
        if self.leverage < 1 or self.slippage < D(".0015"):
            raise ValueError("invalid leverage or execution cost allowance")

    risk_ceiling = ".05"
    drawdown_ceiling = ".25"
    max_positions = 1
    margin_asset = "USDC"
    candidates_enabled = False


@dataclass(frozen=True)
class ResearchPolicy(Policy):
    """Explicit candidate profile; existing p1/p5/pA defaults stay frozen.

    50% is a breaker threshold, not a promise that gaps cannot exceed it.
    Leverage remains subject to the existing twice-stop liquidation buffer.
    """
    drawdown: Decimal = D(".50")
    taker_fee: Decimal = D(".0005")
    maker_fee: Decimal = D(".0005")  # conservative taker charge on all research fills

    # Zero retains the original homogeneous-risk caps. Mixed research cells
    # freeze this to their largest configured per-strategy risk before the run.
    portfolio_risk_fraction: Decimal = D(0)

    def __post_init__(self):
        super().__post_init__()
        if self.portfolio_risk_fraction and not self.risk_fraction <= self.portfolio_risk_fraction <= D(".08"):
            raise ValueError("portfolio risk cap must cover configured risk and stay <=8%")

    risk_ceiling = ".08"
    drawdown_ceiling = ".50"
    max_positions = 3
    margin_asset = "USDT"
    candidates_enabled = True


def portfolio_risk_caps(policy: Policy) -> tuple[Decimal, Decimal]:
    """Frozen same-direction and total fractions; independent of signal order."""
    base = getattr(policy, "portfolio_risk_fraction", D(0)) or policy.risk_fraction
    return min(2 * base, D(".16")), min(3 * base, D(".24"))


@dataclass(frozen=True)
class Account:
    equity: Decimal
    day_start_equity: Decimal
    high_water: Decimal
    active_orders: int = 0
    reconciled: bool = True
    paused: bool = False
    gross_notional: Decimal = D(0)
    margin_used: Decimal = D(0)
    long_risk: Decimal = D(0)
    short_risk: Decimal = D(0)
    open_symbols: tuple[str, ...] = ()


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
    if account.active_orders >= policy.max_positions:
        return reject("EXISTING_EXPOSURE")
    if account.active_orders < 0:
        return reject("INVALID_EXPOSURE")
    exposure = (account.gross_notional, account.margin_used, account.long_risk, account.short_risk)
    if (any(not value.is_finite() or value < 0 for value in exposure)
            or (account.active_orders and (account.gross_notional <= 0
                or account.margin_used <= 0 or len(account.open_symbols) != account.active_orders))):
        return reject("INVALID_EXPOSURE")
    if signal.symbol in account.open_symbols:
        return reject("EXISTING_SYMBOL_EXPOSURE")
    if not all(x.is_finite() and x > 0 for x in
               (account.equity, account.day_start_equity, account.high_water)):
        return reject("INVALID_EQUITY")
    if account.equity <= account.day_start_equity * (1 - policy.daily_loss):
        return reject("DAILY_LOSS_BREAKER")
    if account.equity <= account.high_water * (1 - policy.drawdown):
        return reject("DRAWDOWN_BREAKER")
    if instrument.margin_asset != policy.margin_asset:
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
    manifest = manifest_for(signal.strategy_id, signal.strategy_version)
    if manifest and manifest.status == "candidate" and not policy.candidates_enabled:
        return reject("CANDIDATE_RESEARCH_ONLY")
    hold_ceiling = 259200 if manifest and manifest.timeframe == "4h" else 14400
    if manifest and manifest.status == "candidate" and policy.candidates_enabled:
        hold_ceiling = min(259200, manifest.maximum_hold_seconds)
    no_target = bool(manifest and manifest.exit_model in {"trailing", "channel", "time"})
    if not 1 <= signal.maximum_hold_seconds <= hold_ceiling:
        return reject("INVALID_HOLD")
    if signal.side not in {"LONG", "SHORT"} or signal.entry_type not in {"LIMIT", "MARKET", "STOP"}:
        return reject("INVALID_SIGNAL")
    entry, stop, target = signal.entry_price, signal.stop_loss, signal.take_profit
    if (target is None) != no_target:
        return reject("INVALID_BRACKET")
    if manifest and signal.entry_type != manifest.entry_type:
        return reject("INVALID_SIGNAL")
    prices = (entry, stop) if no_target else (entry, stop, target)
    if not all(x.is_finite() and instrument.min_price <= x <= instrument.max_price
               and x % instrument.tick == 0 for x in prices):
        return reject("PRICE_FILTER")
    if not (stop < entry if signal.side == "LONG" else stop > entry):
        return reject("INVALID_BRACKET")
    if not no_target and not (entry < target if signal.side == "LONG" else target < entry):
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
    if no_target and costs > D(".25") * distance:
        return reject("COSTS_TOO_HIGH_FOR_STOP")
    if not no_target and (abs(target - entry) - costs) / unit_loss < policy.min_net_rr:
        return reject("NET_REWARD_RISK")
    budget = account.equity * policy.risk_fraction
    if policy.candidates_enabled:
        # All same-direction positions share a risk bucket, even if a noisy
        # historical correlation estimate would call them independent.
        used = account.long_risk if signal.side == "LONG" else account.short_risk
        direction_cap, total_cap = portfolio_risk_caps(policy)
        direction_budget = account.equity * direction_cap - used
        total_budget = account.equity * total_cap - account.long_risk - account.short_risk
        budget = min(budget, direction_budget, total_budget)
        if budget <= 0:
            return reject("PORTFOLIO_RISK_CAP")
    # Reserve both adverse price movement and entry costs inside the gross cap.
    max_entry = entry * (1 + policy.slippage)
    margin_capacity = max(D(0), min(account.equity * policy.margin_fraction,
                                   account.equity - account.margin_used))
    bounds = {"risk": budget / unit_loss,
              "gross_notional": max(D(0), account.equity * policy.gross_fraction - account.gross_notional) / (max_entry * (1 + entry_fee)),
              "margin": margin_capacity / (max_entry / safe + costs),
              "exchange_max": min(instrument.max_qty, instrument.market_max_qty)}
    binding = min(bounds, key=bounds.get)
    quantity = floor_step(bounds[binding], quantity_step(instrument))
    if (quantity <= 0 or quantity < max(instrument.min_qty, instrument.market_min_qty)
            or quantity * entry < instrument.min_notional):
        return reject("BELOW_EXCHANGE_MINIMUM")
    notional = quantity * entry
    # Leverage decision, step 2: the lowest leverage that fits the margin budget, so unused leverage
    # never shortens the distance to liquidation and free margin stays available.
    leverage_capacity = (margin_capacity - quantity * costs if policy.candidates_enabled
                         else account.equity * policy.margin_fraction)
    if leverage_capacity <= 0:
        return reject("INSUFFICIENT_MARGIN")
    leverage = min(safe, max(D(1), D(ceil(notional / leverage_capacity))))
    return Decision(True, "NET_RR_NOT_APPLICABLE" if no_target else "APPROVED",
                    quantity, budget, quantity * unit_loss, notional,
                    notional / leverage, leverage,
                    notional / account.equity, binding)

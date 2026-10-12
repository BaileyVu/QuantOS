"""Full-cost, equity-based admission for experimental retail paper trades."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal

from quantos.domain.alpha.hft import HftSide
from quantos.domain.alpha.retail import RetailSignal
from quantos.domain.features.hft import HftFeatures
from quantos.domain.market_data.futures import FuturesRuleError, FuturesSymbolRules
from quantos.domain.risk.hft import HftFeeSchedule


@dataclass(frozen=True, slots=True)
class RetailRiskPolicy:
    maximum_risk_fraction: Decimal = Decimal("0.01")
    maximum_exposure_fraction: Decimal = Decimal("1")
    maximum_drawdown_fraction: Decimal = Decimal("0.03")
    stop_loss_bps: Decimal = Decimal("20")
    slippage_bps_per_leg: Decimal = Decimal("1")
    latency_allowance_bps: Decimal = Decimal("1")
    safety_buffer_bps: Decimal = Decimal("2")
    maximum_event_age_ms: int = 1500
    acknowledgement_ms: int = 100
    maximum_daily_loss_fraction: Decimal = Decimal("0.03")
    stop_uncertainty_bps: Decimal = Decimal("2")

    def __post_init__(self) -> None:
        if not 0 < self.maximum_daily_loss_fraction <= Decimal("0.03") or self.stop_uncertainty_bps < 2:
            raise ValueError("retail daily loss or stop uncertainty guard invalid")
        if not 0 < self.maximum_risk_fraction <= Decimal("0.01"):
            raise ValueError("retail risk fraction exceeds 1%")
        if not 0 < self.maximum_exposure_fraction <= Decimal("1"):
            raise ValueError("retail exposure exceeds 1x equity")
        if not 0 < self.maximum_drawdown_fraction <= Decimal("0.03"):
            raise ValueError("retail drawdown limit exceeds 3%")
        if min(
            self.stop_loss_bps, self.slippage_bps_per_leg,
            self.latency_allowance_bps, self.safety_buffer_bps,
        ) <= 0 or self.maximum_event_age_ms <= 0 or self.acknowledgement_ms < 0:
            raise ValueError("retail cost, stop, or timing guard is invalid")


@dataclass(frozen=True, slots=True)
class FullEquityRiskPolicy(RetailRiskPolicy):
    maximum_risk_fraction: Decimal | None = None
    profile: str = "retail-full-equity-v1"
    adverse_gap_bps: Decimal = Decimal("20")

    def __post_init__(self):
        inherited = asdict(self)
        for key in ("profile", "adverse_gap_bps", "maximum_risk_fraction"):
            inherited.pop(key)
        RetailRiskPolicy(**inherited)
        if self.maximum_risk_fraction is not None:
            raise ValueError("full-equity profile has no standalone per-trade fraction")
        if self.profile != "retail-full-equity-v1" or not self.adverse_gap_bps.is_finite() or self.adverse_gap_bps < 20:
            raise ValueError("invalid full-equity profile or gap reserve")


def policy_from_dict(payload):
    return (FullEquityRiskPolicy if "profile" in payload else RetailRiskPolicy)(**payload)


def protective_prices(signal, reference, policy):
    from quantos.domain.alpha.retail import ProtectedRetailSignal
    from quantos.domain.alpha.reclaim import ReclaimSignal
    direction = Decimal(1) if signal.side is HftSide.BUY else Decimal(-1)
    if isinstance(signal, ProtectedRetailSignal):
        return signal.protective_stop_price, signal.take_profit_price
    if isinstance(signal, ReclaimSignal):
        safety = reference * (1 - direction * policy.stop_loss_bps / 10000)
        stop = max(safety, signal.invalidation_price) if direction > 0 else min(safety, signal.invalidation_price)
        return stop, signal.target_price
    return Decimal(0), Decimal(0)


@dataclass(frozen=True, slots=True)
class RetailRiskDecision:
    approved: bool
    reason: str
    quantity: Decimal
    hurdle_bps: Decimal
    risk_per_unit: Decimal
    credible_net_bps: Decimal = Decimal("0")
    conservative_collateral_buffer_bps: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class ProtectedRiskDecision(RetailRiskDecision):
    stop_price: Decimal = Decimal("0")
    target_price: Decimal = Decimal("0")


class RetailRiskEngine:
    def __init__(
        self, policy: RetailRiskPolicy, fees: HftFeeSchedule,
        rules: FuturesSymbolRules,
    ) -> None:
        minimum_taker = (
            Decimal("0.0004") if rules.symbol == "BTCUSDC"
            else Decimal("0.0005")
        )
        if fees.taker_rate < minimum_taker:
            raise ValueError("retail paper taker fee below approved cost floor")
        self.policy, self.fees, self.rules = policy, fees, rules

    def evaluate(
        self, signal: RetailSignal | None, features: HftFeatures,
        equity: Decimal, peak_equity: Decimal, has_exposure: bool,
        *, funding_bps: Decimal = Decimal("0"),
        daily_start_equity: Decimal | None = None, emergency_risk_off: bool = False,
    ) -> RetailRiskDecision:
        if signal is not None and not isinstance(signal, RetailSignal):
            return RetailRiskDecision(False, "invalid_alpha_contract", Decimal(0), Decimal(0), Decimal(0))
        numeric = (equity, peak_equity, funding_bps, features.feed_latency_ms,
                   features.event_age_ms, features.mid_price, features.best_bid,
                   features.best_ask, features.spread_bps, features.best_bid_quantity,
                   features.best_ask_quantity)
        if signal is not None:
            numeric += (signal.expected_move_bps, signal.uncertainty_bps)
        if any(not x.is_finite() for x in numeric):
            return RetailRiskDecision(False, "nonfinite_risk_input", Decimal("0"), Decimal("0"), Decimal("0"))
        latency_cost = max(
            self.policy.latency_allowance_bps,
            (features.feed_latency_ms + self.policy.acknowledgement_ms)
            / Decimal("100"),
        )
        cost = (
            self.fees.taker_rate * Decimal("20000")
            + features.spread_bps
            + self.policy.slippage_bps_per_leg * 2
            + latency_cost
            + self.policy.safety_buffer_bps + abs(funding_bps)
        )
        per_unit = features.mid_price * (
            self.policy.stop_loss_bps + self.policy.stop_uncertainty_bps + cost
        ) / Decimal("10000")

        def reject(reason: str) -> RetailRiskDecision:
            return RetailRiskDecision(False, reason, Decimal("0"), cost, per_unit)

        if emergency_risk_off:
            return reject("emergency_risk_off")
        if daily_start_equity is not None and (not daily_start_equity.is_finite() or daily_start_equity <= 0 or equity <= daily_start_equity * (1 - self.policy.maximum_daily_loss_fraction)):
            return reject("daily_loss_stop")
        if signal is None:
            return reject("no_confirmed_signal")
        if not 0 < features.best_bid < features.best_ask or features.mid_price <= 0 or min(features.best_bid_quantity, features.best_ask_quantity) <= 0:
            return reject("invalid_book_features")
        if self.rules.status != "TRADING":
            return reject("symbol_not_trading")
        if has_exposure:
            return reject("one_position_limit")
        if equity <= 0 or peak_equity <= 0:
            return reject("invalid_equity")
        if equity <= peak_equity * (1 - self.policy.maximum_drawdown_fraction):
            return reject("drawdown_stop")
        if features.event_age_ms < 0 or features.event_age_ms > self.policy.maximum_event_age_ms:
            return reject("stale_market_data")
        if features.feed_latency_ms < 0:
            return reject("ambiguous_feed_latency")
        if features.feed_latency_ms > self.policy.maximum_event_age_ms:
            return reject("stale_feed_latency")
        if features.spread_bps <= 0:
            return reject("invalid_spread")
        if signal.book_update_id != features.book_update_id:
            return reject("signal_book_mismatch")
        if signal.uncertainty_bps < 0:
            return reject("invalid_uncertainty")
        if signal.expected_move_bps - signal.uncertainty_bps <= cost:
            return reject("non_economic_after_full_cost")
        if per_unit <= 0:
            return reject("invalid_risk_estimate")
        reference_price = (
            features.best_ask if signal.side is HftSide.BUY
            else features.best_bid
        )
        full = isinstance(self.policy, FullEquityRiskPolicy)
        budget = Decimal(0) if full else equity * self.policy.maximum_risk_fraction
        reserve = self.policy.slippage_bps_per_leg + Decimal("5") + self.fees.taker_rate * 10000
        if full:
            if not isinstance(signal.side, HftSide) or not signal.signal_id or not signal.strategy_id or not 0 < signal.horizon_seconds <= 43200 or signal.expires_ns <= signal.observed_ns:
                return reject("invalid_alpha_contract")
            stop, target = protective_prices(signal, reference_price, self.policy)
            direction = 1 if signal.side is HftSide.BUY else -1
            if not stop.is_finite() or not target.is_finite() or stop <= 0 or target <= 0 or (reference_price - stop) * direction <= 0 or (target - reference_price) * direction <= 0:
                return reject("invalid_protective_exit")
            per_unit = abs(reference_price - stop) + reference_price * (cost + self.policy.adverse_gap_bps + self.policy.stop_uncertainty_bps + 5) / 10000
            floor = max(peak_equity * (1 - self.policy.maximum_drawdown_fraction),
                        (daily_start_equity or equity) * (1 - self.policy.maximum_daily_loss_fraction))
            budget = max(Decimal(0), equity - floor)
            reserve = cost + self.policy.adverse_gap_bps + self.policy.stop_uncertainty_bps + 5
        quantity = self.rules.round_quantity(min(
            equity * self.policy.maximum_exposure_fraction / (reference_price * (1 + reserve / 10000)),
            budget / per_unit,
        ))
        visible = features.best_ask_quantity if signal.side is HftSide.BUY else features.best_bid_quantity
        quantity = self.rules.round_quantity(min(quantity, visible, self.rules.maximum_quantity) if full else min(quantity, visible))
        if quantity <= 0:
            return reject("quantity_below_exchange_minimum")
        try:
            self.rules.validate_market_order(quantity, reference_price)
        except FuturesRuleError:
            return reject("quantity_below_exchange_minimum")
        # Paper-only collateral stress proxy, not an exchange liquidation quote.
        collateral_buffer = equity / (quantity * reference_price) * 10000 - Decimal("100")
        if self.policy.stop_loss_bps + self.policy.stop_uncertainty_bps + cost >= collateral_buffer:
            return reject("insufficient_collateral_distance")
        if full:
            return ProtectedRiskDecision(True, "approved", quantity, cost, per_unit,
                signal.expected_move_bps - signal.uncertainty_bps - cost, collateral_buffer, stop, target)
        return RetailRiskDecision(True, "approved", quantity, cost, per_unit,
                                  signal.expected_move_bps - signal.uncertainty_bps - cost,
                                  collateral_buffer)

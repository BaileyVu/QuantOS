"""Directional VAMP/flow maker Alpha for HFT paper trading."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from quantos.domain.features.hft import HftFeatures


class HftSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class HftIntentAction(str, Enum):
    QUOTE = "QUOTE"
    CANCEL = "CANCEL"
    HOLD = "HOLD"
    MAKER_EXIT = "MAKER_EXIT"
    SAFETY_EXIT = "SAFETY_EXIT"


@dataclass(frozen=True, slots=True)
class HftAlphaPolicy:
    minimum_abs_obi_z: Decimal = Decimal("0")
    require_signed_flow_confirmation: bool = False
    maximum_spread_bps: Decimal = Decimal("5")
    maximum_volatility_bps: Decimal = Decimal("20")
    maximum_quote_age_ms: int = 2000
    severe_reversal_bps: Decimal = Decimal("5")

    def __post_init__(self) -> None:
        if self.minimum_abs_obi_z < 0 or self.maximum_spread_bps <= 0:
            raise ValueError("invalid HFT Alpha policy")
        if self.maximum_volatility_bps <= 0 or self.maximum_quote_age_ms < 1:
            raise ValueError("invalid HFT Alpha safety threshold")


@dataclass(frozen=True, slots=True)
class HftQuoteIntent:
    action: HftIntentAction
    symbol: str
    side: HftSide | None
    price: Decimal | None
    expected_alpha_bps: Decimal
    timestamp: datetime
    book_update_id: int
    maximum_age_ms: int
    rationale: str


class VampOrderFlowAlpha:
    def __init__(self, symbol: str, policy: HftAlphaPolicy = HftAlphaPolicy()) -> None:
        self.symbol = symbol
        self.policy = policy

    def decide(
        self,
        features: HftFeatures,
        economic_hurdle_bps: Decimal,
        inventory_quantity: Decimal = Decimal("0"),
        working_side: HftSide | None = None,
    ) -> HftQuoteIntent:
        alpha = features.alpha_bps
        if (
            features.spread_bps > self.policy.maximum_spread_bps
            or features.realized_volatility_bps > self.policy.maximum_volatility_bps
        ):
            return self._intent(HftIntentAction.CANCEL, None, None, features, "regime_filter")

        direction = HftSide.BUY if alpha > 0 else HftSide.SELL
        magnitude = abs(alpha)
        confirmed = (
            abs(features.standardized_obi) >= self.policy.minimum_abs_obi_z
            and (
                not self.policy.require_signed_flow_confirmation
                or (direction is HftSide.BUY and features.signed_trade_flow > 0)
                or (direction is HftSide.SELL and features.signed_trade_flow < 0)
            )
        )
        if inventory_quantity != 0:
            inventory_side = HftSide.BUY if inventory_quantity > 0 else HftSide.SELL
            exit_side = HftSide.SELL if inventory_quantity > 0 else HftSide.BUY
            exit_price = (
                features.best_ask
                if exit_side is HftSide.SELL
                else features.best_bid
            )
            if magnitude <= economic_hurdle_bps or not confirmed:
                return self._intent(
                    HftIntentAction.MAKER_EXIT,
                    exit_side,
                    exit_price,
                    features,
                    "alpha_mean_reversion",
                )
            if direction is not inventory_side:
                if magnitude >= self.policy.severe_reversal_bps:
                    return self._intent(
                        HftIntentAction.SAFETY_EXIT, direction, None, features,
                        "severe_alpha_reversal",
                    )
                return self._intent(
                    HftIntentAction.MAKER_EXIT,
                    exit_side,
                    exit_price,
                    features,
                    "alpha_mean_reversion",
                )
            return self._intent(HftIntentAction.HOLD, None, None, features, "inventory_open")
        if magnitude <= economic_hurdle_bps or not confirmed:
            action = HftIntentAction.CANCEL if working_side is not None else HftIntentAction.HOLD
            return self._intent(action, None, None, features, "alpha_below_hurdle")
        if working_side is not None and direction is not working_side:
            return self._intent(HftIntentAction.CANCEL, None, None, features, "alpha_reversal")
        price = features.best_bid if direction is HftSide.BUY else features.best_ask
        return self._intent(HftIntentAction.QUOTE, direction, price, features, "vamp_displacement")

    def _intent(
        self,
        action: HftIntentAction,
        side: HftSide | None,
        price: Decimal | None,
        features: HftFeatures,
        rationale: str,
    ) -> HftQuoteIntent:
        return HftQuoteIntent(
            action=action,
            symbol=self.symbol,
            side=side,
            price=price,
            expected_alpha_bps=abs(features.alpha_bps),
            timestamp=features.timestamp,
            book_update_id=features.book_update_id,
            maximum_age_ms=self.policy.maximum_quote_age_ms,
            rationale=rationale,
        )

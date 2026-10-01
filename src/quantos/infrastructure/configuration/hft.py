"""Strict configuration for the separate HFT paper runtime."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import tomllib

from quantos.domain.alpha.hft import HftAlphaPolicy
from quantos.domain.features.hft import HftFeaturePolicy
from quantos.domain.risk.hft import HftFeeSchedule, HftRiskPolicy


def _decimal(section: dict, key: str) -> Decimal:
    try:
        value = Decimal(str(section[key]))
    except Exception as error:
        raise ValueError(f"invalid HFT config value: {key}") from error
    if not value.is_finite():
        raise ValueError(f"invalid HFT config value: {key}")
    return value


@dataclass(frozen=True, slots=True)
class HftPaperConfig:
    preferred_symbol: str
    fallback_symbol: str
    starting_equity: Decimal
    maximum_staleness: timedelta
    simulated_acknowledgement_ms: int
    checkpoint_events: int
    feature: HftFeaturePolicy
    alpha: HftAlphaPolicy
    risk: HftRiskPolicy
    fees: dict[str, HftFeeSchedule]

    def __post_init__(self) -> None:
        if self.preferred_symbol != "BTCUSDC" or self.fallback_symbol != "BTCUSDT":
            raise ValueError("HFT symbols must prefer BTCUSDC and fall back to BTCUSDT")
        if self.starting_equity != Decimal("100"):
            raise ValueError("HFT V1 starting equity must be 100")
        if set(self.fees) != {"BTCUSDC", "BTCUSDT"}:
            raise ValueError("explicit BTCUSDC and BTCUSDT fees are required")
        if self.maximum_staleness <= timedelta(0):
            raise ValueError("maximum_staleness must be positive")
        if self.simulated_acknowledgement_ms < 0 or self.checkpoint_events < 1:
            raise ValueError("invalid HFT runtime configuration")


def load_hft_config(path: Path) -> HftPaperConfig:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"cannot load HFT config: {error}") from error
    runtime = raw.get("hft", {})
    feature = raw.get("features", {})
    alpha = raw.get("alpha", {})
    risk = raw.get("risk", {})
    fees = raw.get("fees", {})
    config = HftPaperConfig(
        preferred_symbol=str(runtime.get("preferred_symbol", "")),
        fallback_symbol=str(runtime.get("fallback_symbol", "")),
        starting_equity=_decimal(runtime, "starting_equity"),
        maximum_staleness=timedelta(
            milliseconds=int(runtime["maximum_staleness_ms"])
        ),
        simulated_acknowledgement_ms=int(
            runtime["simulated_acknowledgement_ms"]
        ),
        checkpoint_events=int(runtime["checkpoint_events"]),
        feature=HftFeaturePolicy(
            depth_levels=int(feature["depth_levels"]),
            obi_window=int(feature["obi_window"]),
            trade_flow_seconds=_decimal(feature, "trade_flow_seconds"),
            volatility_seconds=_decimal(feature, "volatility_seconds"),
        ),
        alpha=HftAlphaPolicy(
            minimum_abs_obi_z=_decimal(alpha, "minimum_abs_obi_z"),
            require_signed_flow_confirmation=bool(
                alpha["require_signed_flow_confirmation"]
            ),
            maximum_spread_bps=_decimal(alpha, "maximum_spread_bps"),
            maximum_volatility_bps=_decimal(alpha, "maximum_volatility_bps"),
            maximum_quote_age_ms=int(alpha["maximum_quote_age_ms"]),
            severe_reversal_bps=_decimal(alpha, "severe_reversal_bps"),
        ),
        risk=HftRiskPolicy(
            maximum_leverage=_decimal(risk, "maximum_leverage"),
            maximum_inventory_notional=_decimal(
                risk, "maximum_inventory_notional"
            ),
            maximum_position_quantity=_decimal(
                risk, "maximum_position_quantity"
            ),
            maximum_account_loss_fraction=_decimal(
                risk, "maximum_account_loss_fraction"
            ),
            daily_loss_fraction=_decimal(risk, "daily_loss_fraction"),
            adverse_fill_limit=int(risk["adverse_fill_limit"]),
            adverse_selection_bps=_decimal(risk, "adverse_selection_bps"),
            safety_buffer_bps=_decimal(risk, "safety_buffer_bps"),
            emergency_move_bps=_decimal(risk, "emergency_move_bps"),
            funding_allowance_bps=_decimal(risk, "funding_allowance_bps"),
        ),
        fees={
            symbol: HftFeeSchedule(
                maker_rate=_decimal(fees[symbol], "maker_rate"),
                taker_rate=_decimal(fees[symbol], "taker_rate"),
            )
            for symbol in ("BTCUSDC", "BTCUSDT")
        },
    )
    return config

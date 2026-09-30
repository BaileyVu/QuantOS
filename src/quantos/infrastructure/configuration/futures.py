"""Strict configuration for the autonomous Futures paper runtime."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import tomllib

from quantos.domain.execution.futures_paper import (
    FuturesPaperPolicy, PositionManagementPolicy,
)
from quantos.domain.risk.futures import FuturesRiskPolicy
from quantos.application.futures_trader import FuturesTraderConfig


def _decimal(section: dict, key: str) -> Decimal:
    try:
        value = Decimal(str(section[key]))
    except Exception as error:
        raise ValueError(f"invalid Futures config value: {key}") from error
    if not value.is_finite():
        raise ValueError(f"invalid Futures config value: {key}")
    return value


def load_futures_config(path: Path) -> FuturesTraderConfig:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"cannot load Futures config: {error}") from error
    general = raw.get("futures", {})
    risk = raw.get("risk", {})
    paper = raw.get("paper", {})
    management = raw.get("position_management", {})
    if general.get("symbol") != "BTCUSDT":
        raise ValueError("V1 Futures symbol must be BTCUSDT")
    starting = _decimal(general, "starting_equity")
    lookback = general.get("lookback")
    if not isinstance(lookback, int) or lookback < 30:
        raise ValueError("lookback must be an integer >= 30")
    timeframes = general.get("timeframes")
    if not isinstance(timeframes, list) or not all(
        isinstance(value, str) for value in timeframes
    ):
        raise ValueError("timeframes must be a TOML string array")
    entry_timeframes = general.get("entry_timeframes")
    context_timeframes = general.get("context_timeframes")
    if not isinstance(entry_timeframes, list) or not all(
        isinstance(value, str) for value in entry_timeframes
    ):
        raise ValueError("entry_timeframes must be a TOML string array")
    if not isinstance(context_timeframes, list) or not all(
        isinstance(value, str) for value in context_timeframes
    ):
        raise ValueError("context_timeframes must be a TOML string array")
    enabled = set(timeframes)
    for name, values in (
        ("entry_timeframes", entry_timeframes),
        ("context_timeframes", context_timeframes),
    ):
        if not values or len(values) != len(set(values)):
            raise ValueError(f"{name} must be non-empty and unique")
        if set(values) - enabled:
            raise ValueError(f"{name} must be a subset of timeframes")
    return FuturesTraderConfig(
        symbol="BTCUSDT",
        starting_equity=starting,
        lookback=lookback,
        timeframes=tuple(timeframes),
        risk=FuturesRiskPolicy(
            risk_fraction=_decimal(risk, "risk_fraction"),
            maximum_risk_fraction=_decimal(risk, "maximum_risk_fraction"),
            daily_loss_fraction=_decimal(risk, "daily_loss_fraction"),
            consecutive_loss_limit=int(risk["consecutive_loss_limit"]),
            consecutive_loss_cooldown_minutes=int(
                risk["consecutive_loss_cooldown_minutes"]
            ),
            consecutive_loss_reset_on_utc_day=risk[
                "consecutive_loss_reset_on_utc_day"
            ],
            margin_fraction=_decimal(risk, "margin_fraction"),
            leverage_ceiling=int(risk["leverage_ceiling"]),
            emergency_leverage_ceiling=int(risk["emergency_leverage_ceiling"]),
            liquidation_buffer_fraction=_decimal(risk, "liquidation_buffer_fraction"),
            maintenance_margin_fraction=_decimal(risk, "maintenance_margin_fraction"),
            maximum_slippage_rate=_decimal(risk, "maximum_slippage_rate"),
            maximum_fee_rate=_decimal(risk, "maximum_fee_rate"),
            minimum_net_reward_risk=_decimal(risk, "minimum_net_reward_risk"),
            minimum_reward_to_cost_multiple=_decimal(
                risk, "minimum_reward_to_cost_multiple"
            ),
        ),
        execution=FuturesPaperPolicy(
            taker_fee_rate=_decimal(paper, "taker_fee_rate"),
            slippage_rate=_decimal(paper, "slippage_rate"),
            management=PositionManagementPolicy(
                breakeven_activation_r=_decimal(
                    management, "breakeven_activation_r"
                ),
                breakeven_safety_buffer_r=_decimal(
                    management, "breakeven_safety_buffer_r"
                ),
                profit_lock_activation_r=_decimal(
                    management, "profit_lock_activation_r"
                ),
                profit_lock_floor_r=_decimal(management, "profit_lock_floor_r"),
                runner_activation_r=_decimal(management, "runner_activation_r"),
                minimum_development_r=_decimal(
                    management, "minimum_development_r"
                ),
                atr_multiplier_tight=_decimal(
                    management, "atr_multiplier_tight"
                ),
                atr_multiplier_medium=_decimal(
                    management, "atr_multiplier_medium"
                ),
                atr_multiplier_wide=_decimal(
                    management, "atr_multiplier_wide"
                ),
                structure_lookback=int(management["structure_lookback"]),
                minimum_stop_adjustment_ticks=int(
                    management["minimum_stop_adjustment_ticks"]
                ),
                time_stop_bars_1m=int(management["time_stop_bars_1m"]),
                time_stop_bars_3m=int(management["time_stop_bars_3m"]),
                time_stop_bars_5m=int(management["time_stop_bars_5m"]),
                time_stop_bars_15m=int(management["time_stop_bars_15m"]),
                time_stop_bars_30m=int(management["time_stop_bars_30m"]),
                time_stop_bars_1h=int(management["time_stop_bars_1h"]),
            ),
        ),
        entry_timeframes=tuple(entry_timeframes),
        context_timeframes=tuple(context_timeframes),
    )


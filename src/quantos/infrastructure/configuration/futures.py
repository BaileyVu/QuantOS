"""Strict configuration for the autonomous Futures paper runtime."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import tomllib

from quantos.domain.execution.futures_paper import FuturesPaperPolicy
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
        ),
        execution=FuturesPaperPolicy(
            taker_fee_rate=_decimal(paper, "taker_fee_rate"),
            slippage_rate=_decimal(paper, "slippage_rate"),
        ),
    )


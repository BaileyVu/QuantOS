"""Stateless, causal calculations for the candidate-v1 feature schema."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    FloatOperation,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    Underflow,
    localcontext,
)

from quantos.domain.common import require_utc
from quantos.domain.features.contracts import FeatureVector
from quantos.domain.market_data.contracts import Candle

FEATURE_VERSION = "candidate-v1"
FEATURE_NAMES = (
    "return_1m",
    "return_15m",
    "realized_volatility_20m",
    "efficiency_ratio_20m",
    "sma_spread_5_20",
    "momentum_balance_14",
    "volume_ratio_20",
    "true_range_pct",
    "range_position_20",
    "completed_5m_return",
)

# Twenty close-to-close transitions require twenty-one candles.
MIN_HISTORY = 21


class FeatureEngineError(ValueError):
    """Invalid/causally unavailable input or unrepresentable feature arithmetic."""


def _validate_input(candles: tuple[Candle, ...], decision_time: datetime) -> None:
    if type(candles) is not tuple or not candles:
        raise FeatureEngineError("candles must be a non-empty built-in tuple")
    if type(decision_time) is not datetime:
        raise FeatureEngineError("decision_time must be a built-in UTC datetime")
    require_utc(decision_time, "decision_time")

    previous: Candle | None = None
    for candle in candles:
        if type(candle) is not Candle:
            raise FeatureEngineError("candles must contain canonical Candle values")
        # Revalidate even if a caller bypassed the frozen dataclass constructor.
        Candle.__post_init__(candle)
        if candle.close_time > decision_time:
            raise FeatureEngineError("every candle must be complete at decision_time")
        if previous is not None:
            if candle.symbol != previous.symbol:
                raise FeatureEngineError("candles must contain one symbol only")
            if candle.open_time - previous.open_time != timedelta(minutes=1):
                raise FeatureEngineError("candle opens must be strictly ascending and exactly 1m apart")
        previous = candle


def _latest_completed_bucket(candles: tuple[Candle, ...]) -> tuple[Candle, ...] | None:
    # All inputs have already passed continuity and explicit completion checks.
    # An exact UTC M..M+4 sequence is required; never round a shifted open time.
    for start in range(len(candles) - 5, -1, -1):
        opened = candles[start].open_time
        if opened.minute % 5 == 0 and opened.second == 0 and opened.microsecond == 0:
            return candles[start:start + 5]
    return None


def _calculate(candles: tuple[Candle, ...], decision_time: datetime) -> FeatureVector | None:
    if len(candles) < MIN_HISTORY:
        return None
    window = candles[-MIN_HISTORY:]
    bucket = _latest_completed_bucket(window)
    if bucket is None:
        return None

    zero = Decimal("0")
    one = Decimal("1")
    twenty = Decimal("20")
    closes = tuple(candle.close for candle in window)
    recent = window[-20:]

    # Every previous close is a required simple-return denominator.
    if any(close == zero for close in closes[:-1]) or bucket[0].open == zero:
        return None
    changes = tuple(current - previous for previous, current in zip(closes, closes[1:]))
    movement_20 = sum((abs(change) for change in changes), zero)
    movement_14 = sum((abs(change) for change in changes[-14:]), zero)
    mean_20 = sum(closes[-20:], zero) / twenty
    mean_volume = sum((candle.volume for candle in recent), zero) / twenty
    lowest = min(candle.low for candle in recent)
    price_range = max(candle.high for candle in recent) - lowest
    if any(value == zero for value in (movement_20, movement_14, mean_20, mean_volume, price_range)):
        return None

    returns = tuple(current / previous - one for previous, current in zip(closes, closes[1:]))
    current = window[-1]
    true_range = max(
        current.high - current.low,
        abs(current.high - closes[-2]),
        abs(current.low - closes[-2]),
    )
    values = (
        returns[-1],
        closes[-1] / closes[-16] - one,
        (sum((value ** 2 for value in returns), zero) / twenty).sqrt(),
        abs(closes[-1] - closes[0]) / movement_20,
        (sum(closes[-5:], zero) / Decimal("5")) / mean_20 - one,
        sum(changes[-14:], zero) / movement_14,
        current.volume / mean_volume - one,
        true_range / closes[-2],
        (closes[-1] - lowest) / price_range,
        bucket[-1].close / bucket[0].open - one,
    )
    if any(type(value) is not Decimal or not value.is_finite() for value in values):
        raise FeatureEngineError("features must be finite built-in Decimal values")
    return FeatureVector(
        timestamp=decision_time,
        symbol=current.symbol,
        feature_version=FEATURE_VERSION,
        values=dict(zip(FEATURE_NAMES, values, strict=True)),
    )


def compute_feature_vector(
    candles: tuple[Candle, ...], *, decision_time: datetime,
) -> FeatureVector | None:
    """Compute all ten candidate features, or None for unavailable mathematics.

    The final input is the decision candle. Every input must be complete at
    the supplied UTC decision_time; malformed input is an error even during
    warm-up. Twenty returns require 21 candles, plus an exact completed UTC
    five-minute bucket. No input is sorted, repaired, or silently discarded.

    Arithmetic uses a fresh precision-34, ROUND_HALF_EVEN context with fixed
    exponent limits and traps, independent of both ambient and DefaultContext
    settings. Overflow, inexact underflow, and invalid arithmetic fail closed.
    Ordinary rounding is permitted; values are never quantized for display.
    """
    context = Context(
        prec=34, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
        capitals=1, clamp=0, flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow, Underflow, FloatOperation],
    )
    try:
        with localcontext(context):
            _validate_input(candles, decision_time)
            return _calculate(candles, decision_time)
    except FeatureEngineError:
        raise
    except (ValueError, TypeError, AttributeError, DecimalException) as error:
        raise FeatureEngineError(f"cannot compute feature vector: {error}") from error

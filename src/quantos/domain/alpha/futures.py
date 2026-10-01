"""Deterministic regime classification and Futures strategy selection."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from quantos.domain.market_data import Candle
from quantos.domain.market_data.timeframes import (
    SUPPORTED_SIGNAL_TIMEFRAMES, timeframe_minutes,
)


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    HOLD = "HOLD"


class MarketRegime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    BREAKOUT_OR_EXPANSION = "BREAKOUT_OR_EXPANSION"
    UNCERTAIN = "UNCERTAIN"


PRODUCTION_STRATEGY_IDS = (
    "trend_continuation",
    "trend_pullback",
    "breakout_expansion",
    "range_mean_reversion",
)


@dataclass(frozen=True, slots=True)
class RegimeState:
    regime: MarketRegime
    ema_fast: Decimal
    ema_slow: Decimal
    atr: Decimal
    recent_high: Decimal
    recent_low: Decimal
    rationale: str


@dataclass(frozen=True, slots=True)
class StrategySignal:
    timestamp: object
    symbol: str
    direction: Direction
    strategy_id: str
    regime: MarketRegime
    entry: Decimal
    stop: Decimal
    target: Decimal
    strength: Decimal
    evidence: tuple[str, ...]
    rationale: str
    timeframe: str = "1m"
    higher_timeframe_context: tuple[str, ...] = ()
    signal_id: str = ""
    atr: Decimal = Decimal(0)
    soft_structure_reference: Decimal | None = None
    continuation_objective: Decimal | None = None


@dataclass(frozen=True, slots=True)
class TradeCandidate:
    signal: StrategySignal


@dataclass(frozen=True, slots=True)
class Selection:
    direction: Direction
    candidate: TradeCandidate | None
    reason: str
    signals: tuple[StrategySignal, ...]


@dataclass(frozen=True, slots=True)
class StrategyEvaluation:
    timestamp: object
    symbol: str
    strategy_id: str
    direction: Direction
    timeframe: str
    detected_regime: MarketRegime
    regime_compatible: bool
    signal_created: bool
    rejection_reason: str | None = None


def _ema(values, period: int) -> Decimal:
    values = tuple(values)
    if not values:
        raise ValueError("EMA needs values")
    alpha = Decimal(2) / Decimal(period + 1)
    result = values[0]
    for value in values[1:]:
        result = value * alpha + result * (Decimal(1) - alpha)
    return result


def _atr(candles: tuple[Candle, ...], period: int = 14) -> Decimal:
    window = candles[-(period + 1):]
    if len(window) < 2:
        return Decimal(0)
    values = []
    for previous, current in zip(window, window[1:]):
        values.append(max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        ))
    return sum(values, Decimal(0)) / Decimal(len(values))


def classify_regime(candles) -> RegimeState:
    candles = tuple(candles)
    if len(candles) < 30:
        last = candles[-1].close if candles else Decimal(0)
        return RegimeState(MarketRegime.UNCERTAIN, last, last, Decimal(0), last, last,
                           "fewer than 30 completed candles")
    closes = tuple(c.close for c in candles[-40:])
    fast = _ema(closes, 9)
    slow = _ema(closes, 21)
    prior_fast = _ema(closes[:-3], 9)
    atr = _atr(candles)
    recent = candles[-21:-1]
    high = max(c.high for c in recent)
    low = min(c.low for c in recent)
    last = candles[-1]
    previous_ranges = tuple(c.high - c.low for c in candles[-11:-1])
    average_range = sum(previous_ranges, Decimal(0)) / Decimal(len(previous_ranges))
    expansion = average_range > 0 and (last.high - last.low) >= average_range * Decimal("1.5")
    if expansion and (last.close > high or last.close < low):
        regime = MarketRegime.BREAKOUT_OR_EXPANSION
        reason = "completed candle broke the prior 20-candle range with expansion"
    elif atr > 0 and fast > slow and fast > prior_fast and fast - slow >= atr * Decimal(".25"):
        regime = MarketRegime.TREND_UP
        reason = "fast EMA is above slow EMA with positive slope"
    elif atr > 0 and fast < slow and fast < prior_fast and slow - fast >= atr * Decimal(".25"):
        regime = MarketRegime.TREND_DOWN
        reason = "fast EMA is below slow EMA with negative slope"
    elif atr > 0 and abs(fast - slow) <= atr * Decimal(".35"):
        regime = MarketRegime.RANGE
        reason = "EMA separation is small relative to ATR"
    else:
        regime = MarketRegime.UNCERTAIN
        reason = "trend, range, and breakout evidence are insufficient"
    return RegimeState(regime, fast, slow, atr, high, low, reason)


def evaluate_strategies_with_diagnostics(
    candles,
    state: RegimeState,
    timeframe: str = "1m",
    higher_context=(),
) -> tuple[tuple[StrategySignal, ...], tuple[StrategyEvaluation, ...]]:
    candles = tuple(candles)
    if timeframe not in SUPPORTED_SIGNAL_TIMEFRAMES:
        raise ValueError("unsupported signal timeframe")
    if len(candles) < 30 or state.atr <= 0:
        return (), ()
    last, previous = candles[-1], candles[-2]
    result: list[StrategySignal] = []
    evaluations: list[StrategyEvaluation] = []

    def add(direction: Direction, strategy: str, stop: Decimal, target: Decimal,
            strength: str, evidence: tuple[str, ...], rationale: str,
            compatible_regimes: tuple[MarketRegime, ...],
            soft_structure_reference: Decimal | None = None,
            continuation_objective: Decimal | None = None) -> None:
        objective = continuation_objective or target
        compatible = state.regime in compatible_regimes
        if not compatible:
            evaluations.append(StrategyEvaluation(
                last.close_time, last.symbol, strategy, direction, timeframe,
                state.regime, False, False, "regime_incompatible",
            ))
            return
        if direction is Direction.LONG and not (stop < last.close < objective):
            evaluations.append(StrategyEvaluation(
                last.close_time, last.symbol, strategy, direction, timeframe,
                state.regime, True, False, "invalid_signal_geometry",
            ))
            return
        if direction is Direction.SHORT and not (objective < last.close < stop):
            evaluations.append(StrategyEvaluation(
                last.close_time, last.symbol, strategy, direction, timeframe,
                state.regime, True, False, "invalid_signal_geometry",
            ))
            return
        context_labels = tuple(
            f"{context_timeframe}:{context_regime.value}"
            for context_timeframe, context_regime in higher_context
        )
        adjustment = Decimal(0)
        for _, context_regime in higher_context:
            aligned = (
                direction is Direction.LONG and context_regime is MarketRegime.TREND_UP
            ) or (
                direction is Direction.SHORT and context_regime is MarketRegime.TREND_DOWN
            )
            opposed = (
                direction is Direction.LONG and context_regime is MarketRegime.TREND_DOWN
            ) or (
                direction is Direction.SHORT and context_regime is MarketRegime.TREND_UP
            )
            if aligned:
                adjustment += Decimal(".04")
            elif opposed:
                adjustment -= Decimal(".04")
        adjusted_strength = min(Decimal(1), max(Decimal(0), Decimal(strength) + adjustment))
        signal_id = "|".join((
            last.symbol, timeframe, strategy, direction.value,
            last.close_time.isoformat(),
        ))
        result.append(StrategySignal(
            last.close_time, last.symbol, direction, strategy, state.regime,
            last.close, stop, objective, adjusted_strength, evidence, rationale,
            timeframe, context_labels, signal_id, state.atr,
            soft_structure_reference or target, objective,
        ))
        evaluations.append(StrategyEvaluation(
            last.close_time, last.symbol, strategy, direction, timeframe,
            state.regime, True, True,
        ))

    if last.close > previous.high:
        stop = last.close - state.atr * Decimal("1.5")
        reference_target = last.close + (last.close - stop) * 2
        continuation = max(
            reference_target,
            last.close + (last.close - state.recent_low),
        )
        add(Direction.LONG, "trend_continuation", stop,
            continuation, ".72",
            ("ema_alignment", "higher_close"), "uptrend continuation above prior high",
            (MarketRegime.TREND_UP,), previous.high, continuation)
    if last.close < previous.low:
        stop = last.close + state.atr * Decimal("1.5")
        reference_target = last.close - (stop - last.close) * 2
        continuation = min(
            reference_target,
            last.close - (state.recent_high - last.close),
        )
        add(Direction.SHORT, "trend_continuation", stop,
            continuation, ".72",
            ("ema_alignment", "lower_close"), "downtrend continuation below prior low",
            (MarketRegime.TREND_DOWN,), previous.low, continuation)
    if last.low <= state.ema_fast < last.close:
        stop = min(c.low for c in candles[-5:]) - state.atr * Decimal(".25")
        add(Direction.LONG, "trend_pullback", stop,
            last.close + (last.close - stop) * Decimal("1.8"), ".66",
            ("ema_pullback", "trend_alignment"), "pullback reclaimed the fast EMA",
            (MarketRegime.TREND_UP,))
    if last.high >= state.ema_fast > last.close:
        stop = max(c.high for c in candles[-5:]) + state.atr * Decimal(".25")
        add(Direction.SHORT, "trend_pullback", stop,
            last.close - (stop - last.close) * Decimal("1.8"), ".66",
            ("ema_pullback", "trend_alignment"), "pullback rejected the fast EMA",
            (MarketRegime.TREND_DOWN,))
    prior_ranges = tuple(c.high - c.low for c in candles[-11:-1])
    average_range = sum(prior_ranges, Decimal(0)) / Decimal(len(prior_ranges))
    expanding = (
        average_range > 0
        and last.high - last.low >= average_range * Decimal("1.5")
    )
    if expanding and last.close > state.recent_high:
        stop = max(state.recent_high - state.atr * Decimal(".25"),
                   last.close - state.atr * Decimal("1.5"))
        reference_target = last.close + (last.close - stop) * 2
        continuation = max(
            reference_target,
            last.close + (state.recent_high - state.recent_low),
        )
        add(Direction.LONG, "breakout_expansion", stop,
            continuation, ".78",
            ("range_break", "range_expansion"), "upside expansion breakout",
            (MarketRegime.BREAKOUT_OR_EXPANSION,), state.recent_high,
            continuation)
    elif expanding and last.close < state.recent_low:
        stop = min(state.recent_low + state.atr * Decimal(".25"),
                   last.close + state.atr * Decimal("1.5"))
        reference_target = last.close - (stop - last.close) * 2
        continuation = min(
            reference_target,
            last.close - (state.recent_high - state.recent_low),
        )
        add(Direction.SHORT, "breakout_expansion", stop,
            continuation, ".78",
            ("range_break", "range_expansion"), "downside expansion breakout",
            (MarketRegime.BREAKOUT_OR_EXPANSION,), state.recent_low,
            continuation)
    width = state.recent_high - state.recent_low
    if width > 0 and last.close <= state.recent_low + width * Decimal(".2"):
        stop = state.recent_low - state.atr * Decimal(".5")
        add(Direction.LONG, "range_mean_reversion", stop,
            state.recent_low + width * Decimal(".5"), ".60",
            ("range_support",), "price is near established range support",
            (MarketRegime.RANGE,))
    if width > 0 and last.close >= state.recent_high - width * Decimal(".2"):
        stop = state.recent_high + state.atr * Decimal(".5")
        add(Direction.SHORT, "range_mean_reversion", stop,
            state.recent_low + width * Decimal(".5"), ".60",
            ("range_resistance",), "price is near established range resistance",
            (MarketRegime.RANGE,))
    return tuple(result), tuple(evaluations)


def evaluate_strategies(candles, state: RegimeState, timeframe: str = "1m",
                        higher_context=()) -> tuple[StrategySignal, ...]:
    signals, _ = evaluate_strategies_with_diagnostics(
        candles, state, timeframe, higher_context
    )
    return signals


def select_candidate(signals,
                     minimum_strength: Decimal = Decimal(".55"),
                     conflict_margin: Decimal = Decimal(".15")) -> Selection:
    raw = tuple(s for s in signals if s.direction is not Direction.HOLD
                and s.strength >= minimum_strength)
    deduplicated = {}
    for signal in raw:
        setup = (signal.symbol, signal.strategy_id, signal.direction, signal.timestamp)
        current = deduplicated.get(setup)
        if current is None or (
            signal.strength, timeframe_minutes(signal.timeframe)
        ) > (
            current.strength, timeframe_minutes(current.timeframe)
        ):
            deduplicated[setup] = signal
    eligible = tuple(deduplicated.values())
    if not eligible:
        return Selection(Direction.HOLD, None, "no eligible strategy signal", eligible)
    score = lambda s: (s.strength, timeframe_minutes(s.timeframe), s.signal_id)
    long_best = max((s for s in eligible if s.direction is Direction.LONG),
                    key=score, default=None)
    short_best = max((s for s in eligible if s.direction is Direction.SHORT),
                     key=score, default=None)
    if long_best and short_best:
        if abs(long_best.strength - short_best.strength) < conflict_margin:
            return Selection(Direction.HOLD, None, "material LONG/SHORT conflict", eligible)
        winner = long_best if long_best.strength > short_best.strength else short_best
    else:
        winner = long_best or short_best
    assert winner is not None
    return Selection(winner.direction, TradeCandidate(winner),
                     f"selected {winner.strategy_id}", eligible)


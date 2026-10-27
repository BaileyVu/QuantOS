"""Transparent AF1 reference hypotheses used only as examples and test fixtures."""

from __future__ import annotations

from decimal import Decimal

from quantos.domain.evaluation.alpha_funnel import (
    AlphaFunnelError,
    Direction,
    HypothesisMetadata,
    ResearchHypothesis,
    af1_decimal_context,
)


def short_return_extreme(
    *, lookback_minutes: int, threshold: Decimal,
    human_explainability_score: int | None = None,
) -> ResearchHypothesis:
    if type(lookback_minutes) is not int or lookback_minutes < 1:
        raise AlphaFunnelError("lookback_minutes must be positive")
    if type(threshold) is not Decimal or not threshold.is_finite() or threshold >= 0:
        raise AlphaFunnelError("short-return extreme threshold must be a negative Decimal")

    def evaluate(window: tuple[object, ...]) -> bool:
        with af1_decimal_context():
            first, last = window[0], window[-1]
            if first.close == 0:
                return False
            return last.close / first.close - Decimal(1) <= threshold

    return ResearchHypothesis(
        HypothesisMetadata(
            stable_id=f"reference.short-return-extreme.{lookback_minutes}m",
            implementation_id="af1-reference-short-return-extreme-v1",
            family="short-return-extreme",
            description="Example event: a causal short-horizon close return is below a fixed threshold.",
            direction=Direction.UP,
            interpretation="Tests subsequent upward returns after an explicitly configured price decline.",
            required_inputs=("close",),
            parameters={"lookback_minutes": lookback_minutes, "threshold": str(threshold)},
            causal_lookback=lookback_minutes + 1,
            parameter_neighborhood={"supplied": False},
            human_explainability_score=human_explainability_score,
        ),
        evaluate,
    )


def realized_volatility_ratio(
    *, short_window: int, long_window: int, threshold: Decimal,
    human_explainability_score: int | None = None,
) -> ResearchHypothesis:
    if type(short_window) is not int or type(long_window) is not int or not 1 <= short_window < long_window:
        raise AlphaFunnelError("require 1 <= short_window < long_window")
    if type(threshold) is not Decimal or not threshold.is_finite() or threshold <= 0:
        raise AlphaFunnelError("volatility-ratio threshold must be a positive Decimal")

    def evaluate(window: tuple[object, ...]) -> bool:
        with af1_decimal_context():
            returns = []
            for left, right in zip(window[:-1], window[1:], strict=True):
                if left.close == 0:
                    return False
                returns.append(right.close / left.close - Decimal(1))
            if len(returns) != long_window:
                raise AlphaFunnelError("realized-volatility window length mismatch")
            long_rms = (
                sum(value * value for value in returns) / Decimal(long_window)
            ).sqrt()
            if long_rms == 0:
                return False
            short = returns[-short_window:]
            short_rms = (
                sum(value * value for value in short) / Decimal(short_window)
            ).sqrt()
            return short_rms / long_rms >= threshold

    return ResearchHypothesis(
        HypothesisMetadata(
            stable_id=f"reference.realized-volatility-ratio.{short_window}-{long_window}",
            implementation_id="af1-reference-realized-volatility-ratio-v1",
            family="realized-volatility-ratio",
            description="Example event: short-window realized RMS return exceeds long-window RMS return.",
            direction=Direction.UP,
            interpretation="Tests forward returns after a causal volatility expansion.",
            required_inputs=("close",),
            parameters={
                "short_window": short_window,
                "long_window": long_window,
                "threshold": str(threshold),
            },
            causal_lookback=long_window + 1,
            parameter_neighborhood={"supplied": False},
            human_explainability_score=human_explainability_score,
        ),
        evaluate,
    )


def close_location_extreme(
    *, maximum_location: Decimal, human_explainability_score: int | None = None,
) -> ResearchHypothesis:
    if (
        type(maximum_location) is not Decimal or not maximum_location.is_finite()
        or not Decimal(0) <= maximum_location <= Decimal(1)
    ):
        raise AlphaFunnelError("maximum_location must be a Decimal in [0, 1]")

    def evaluate(window: tuple[object, ...]) -> bool:
        with af1_decimal_context():
            candle = window[-1]
            width = candle.high - candle.low
            return (
                width > 0
                and (candle.close - candle.low) / width <= maximum_location
            )

    return ResearchHypothesis(
        HypothesisMetadata(
            stable_id="reference.close-location-extreme.1m",
            implementation_id="af1-reference-close-location-extreme-v1",
            family="close-location-range",
            description="Example event: the completed candle closes near its low within its own range.",
            direction=Direction.UP,
            interpretation="Tests subsequent upward returns after a low close-location observation.",
            required_inputs=("high", "low", "close"),
            parameters={"maximum_location": str(maximum_location)},
            causal_lookback=1,
            parameter_neighborhood={"supplied": False},
            human_explainability_score=human_explainability_score,
        ),
        evaluate,
    )


def af1_reference_hypotheses() -> tuple[ResearchHypothesis, ...]:
    """Return three fixed examples; they are not strategy candidates or approvals."""
    return (
        short_return_extreme(lookback_minutes=5, threshold=Decimal("-0.005")),
        realized_volatility_ratio(short_window=5, long_window=20, threshold=Decimal("1.5")),
        close_location_extreme(maximum_location=Decimal("0.10")),
    )

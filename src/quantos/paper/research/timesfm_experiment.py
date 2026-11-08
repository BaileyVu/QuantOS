"""Offline, no-lookahead TimesFM 2.5 experiment primitives.

This module deliberately has no TimesFM or torch import. Forecast generation is
injected, keeping tests and ordinary QuantOS installs weight-free.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Iterable, Mapping, Sequence

from .registry import ResearchRegistry

HOUR_MS = 3_600_000
BUILD_START_MS = 1_577_836_800_000  # 2020-01-01 UTC
BUILD_END_MS = 1_688_169_600_000    # 2023-07-01 UTC, exclusive
HELD_START_MS = BUILD_END_MS
HELD_END_MS = 1_735_689_600_000     # 2025-01-01 UTC, exclusive


@dataclass(frozen=True)
class Forecast:
    """One forecast origin's point medians and q10/q90 in price units."""
    origin_ms: int
    origin_price: float
    median: Mapping[int, float]
    q10: Mapping[int, float]
    q90: Mapping[int, float]


@dataclass(frozen=True)
class VolatilityForecast:
    """Causal volatility prediction aligned to future return indices."""
    origin_index: int
    target_start_index: int
    target_end_exclusive: int
    volatility: float


def _positive(value: float, field: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field} must be finite and positive")
    return value


def _nonnegative(value: float, field: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be finite and nonnegative")
    return value


def features_at(forecast: Forecast, *, completed_close_ms: int,
                completed_close: float, prior_forecast: Forecast | None) -> dict:
    """Extract features using forecasts made at this bar and the prior bar.

    The origin is the completed bar close. Drift forecasts are strictly future
    horizons. Surprise may use only a prior forecast targeting this completed
    close; future-origin forecasts are rejected.
    """
    if forecast.origin_ms != completed_close_ms:
        raise ValueError("forecast origin must equal the completed bar timestamp")
    price = _positive(completed_close, "completed_close")
    if not math.isclose(price, _positive(forecast.origin_price, "origin_price"), rel_tol=0, abs_tol=1e-10):
        raise ValueError("forecast origin price must match completed close")
    out = {"origin_ms": completed_close_ms}
    for horizon in (4, 12, 24):
        if horizon not in forecast.median:
            raise ValueError(f"missing median horizon {horizon}")
        median = _positive(forecast.median[horizon], "median")
        out[f"drift_{horizon}h"] = median / price - 1.0
    if 24 not in forecast.q10 or 24 not in forecast.q90:
        raise ValueError("missing 24h quantile bounds")
    lower, upper = _positive(forecast.q10[24], "q10"), _positive(forecast.q90[24], "q90")
    median_24 = _positive(forecast.median[24], "median")
    if not lower <= median_24 <= upper:
        raise ValueError("forecast quantiles must satisfy q10 <= median <= q90")
    out["width_24h"] = (upper - lower) / price
    if prior_forecast is None:
        out["surprise"] = None
    else:
        if prior_forecast.origin_ms >= completed_close_ms:
            raise ValueError("prior forecast must predate the completed bar")
        elapsed = completed_close_ms - prior_forecast.origin_ms
        if elapsed % HOUR_MS:
            raise ValueError("prior forecast origin must be hour-aligned")
        horizon = elapsed // HOUR_MS
        expected = prior_forecast.median.get(horizon)
        if expected is None:
            raise ValueError("prior forecast does not target this completed bar")
        out["surprise"] = (price - _positive(expected, "prior median")) / price
    return out


def assert_period(timestamp_ms: int, period: str) -> None:
    """Enforce date-only period eligibility; no 2025+ timestamp can pass."""
    if period == "BUILD":
        ok = BUILD_START_MS <= timestamp_ms < BUILD_END_MS
    elif period == "HELD":
        ok = HELD_START_MS <= timestamp_ms < HELD_END_MS
    else:
        raise ValueError("period must be BUILD or HELD")
    if not ok:
        raise ValueError(f"timestamp is outside the permitted {period} window")


def persistence_volatility(realized_vol: Sequence[float], *,
                           horizon: int = 1) -> list[VolatilityForecast]:
    """Persistence forecasts for future blocks of ``horizon`` returns.

    Input values are per-bar volatility estimates known at each completed bar.
    Forecast at index i applies to target indices [i+1, i+horizon+1).
    """
    if horizon < 1:
        raise ValueError("horizon must be positive")
    values = [_nonnegative(x, "realized_vol") for x in realized_vol]
    return [VolatilityForecast(i, i + 1, i + horizon + 1, values[i])
            for i in range(max(0, len(values) - horizon))]


def ewma_volatility(returns: Sequence[float], *, alpha: float = 0.06,
                    horizon: int = 1) -> list[VolatilityForecast]:
    """Forecast future RMS volatility from causal EWMA return variance.

    The state at origin i uses returns through i only. Forecast rows target the
    next ``horizon`` returns, indices [i+1, i+horizon+1). One-step variance is
    initialized with the first observed squared return; later observations use
    ``alpha*r^2 + (1-alpha)*variance``. For a multi-bar target, the per-bar
    volatility remains sqrt(variance), suitable for comparison to future RMS.
    """
    if not 0 < alpha <= 1:
        raise ValueError("alpha must be in (0, 1]")
    if horizon < 1:
        raise ValueError("horizon must be positive")
    values = []
    for value in returns:
        x = float(value)
        if not math.isfinite(x):
            raise ValueError("returns must be finite")
        values.append(x)
    if len(values) <= horizon:
        return []
    state = values[0] ** 2
    result = []
    for origin in range(len(values) - horizon):
        if origin:
            state = alpha * values[origin] ** 2 + (1 - alpha) * state
        result.append(VolatilityForecast(origin, origin + 1, origin + horizon + 1,
                                         math.sqrt(state)))
    return result


def atr_percent(bars: Sequence[tuple[float, float, float]], *, window: int = 14) -> list[float]:
    """Causal ATR/close estimate; each output uses bars ending at that index."""
    if window < 1:
        raise ValueError("window must be positive")
    trs, result = [], []
    previous = None
    for high, low, close in bars:
        high, low, close = (_positive(high, "high"), _positive(low, "low"), _positive(close, "close"))
        if low > close or close > high:
            raise ValueError("OHLC must satisfy low <= close <= high")
        tr = high - low if previous is None else max(high - low, abs(high - previous), abs(low - previous))
        trs.append(tr)
        previous = close
        if len(trs) >= window:
            result.append(sum(trs[-window:]) / window / close)
    return result


def align_volatility_forecasts(values: Sequence[float], *, horizon: int = 1,
                               first_index: int = 0) -> list[VolatilityForecast]:
    """Align causal per-bar estimates (for example ATR/close) to future blocks."""
    if horizon < 1 or first_index < 0:
        raise ValueError("horizon must be positive and first_index nonnegative")
    estimates = [_nonnegative(value, "volatility") for value in values]
    return [VolatilityForecast(first_index + i, first_index + i + 1,
                               first_index + i + horizon + 1, estimates[i])
            for i in range(max(0, len(estimates) - horizon))]


def mean_absolute_error(predicted: Sequence[float], realized: Sequence[float]) -> float:
    """Matched-series MAE helper; callers must construct causal aligned pairs."""
    if len(predicted) != len(realized) or not predicted:
        raise ValueError("predicted and realized must be nonempty and equally sized")
    pairs = [(float(p), float(r)) for p, r in zip(predicted, realized)]
    if any(not math.isfinite(p) or not math.isfinite(r) for p, r in pairs):
        raise ValueError("MAE inputs must be finite")
    return sum(abs(p - r) for p, r in pairs) / len(pairs)


def register_variants(registry: ResearchRegistry, *, manifest_hash: str,
                      config: Mapping | None = None) -> dict[str, str]:
    """Register every preregistered experiment family against a data manifest."""
    common = dict(config or {})
    # Model choice is frozen for this experiment; reject conflicting metadata.
    requested_model = common.pop("model", None)
    pinned_model = "google/timesfm-2.5-200m-pytorch"
    if requested_model is not None and requested_model != pinned_model:
        raise ValueError("TimesFM experiment model is pinned to google/timesfm-2.5-200m-pytorch")
    common["model"] = pinned_model
    variants = {
        "timesfm_ml1h": {
            **common,
            "features": ["drift_4h", "drift_12h", "drift_24h", "width_24h", "surprise"],
        },
        "timesfm_width_volatility": {
            **common, "target": "realized_vol", "baselines": ["persistence", "ewma", "atr"]
        },
        "timesfm_crash_dip_shadow": {
            **common,
            "strategies": ["crash_flush_e2", "alt_dip_reversal"],
            "promotion": "L3_shadow_only",
        },
    }
    return {name: registry.register_variant("timesfm25", name, params,
                                             manifest_hash=manifest_hash)
            for name, params in variants.items()}


def walk_forward_features(bars: Iterable[tuple[int, float]], predictor: Callable,
                          *, period: str) -> list[dict]:
    """Run an injected predictor on completed closes; predictor sees prefix only.

    ``predictor(prefix)`` must return ``Forecast`` with origin at prefix's final
    timestamp. The model adapter and any data acquisition remain caller-owned.
    """
    rows = list(bars)
    timestamps = [row[0] for row in rows]
    if any(b <= a for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError("bar timestamps must be strictly increasing")
    if any(b - a != HOUR_MS for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError("bars must be contiguous completed hourly closes")
    output = []
    forecasts: list[Forecast] = []
    for i in range(len(rows)):
        timestamp, close = rows[i]
        assert_period(timestamp, period)
        forecast = predictor(rows[:i + 1])
        if not isinstance(forecast, Forecast):
            raise TypeError("predictor must return Forecast")
        if forecast.origin_ms != timestamp:
            raise ValueError("predictor returned wrong origin timestamp")
        prior = forecasts[-1] if forecasts else None
        output.append(features_at(
            forecast, completed_close_ms=timestamp, completed_close=close,
            prior_forecast=prior,
        ))
        forecasts.append(forecast)
    return output


def timesfm25_predictor(model: object) -> Callable[[Sequence[tuple[int, float]]], Forecast]:
    """Adapt an already-loaded/compiled official TimesFM 2.5 model.

    Calling the returned function performs inference. This factory itself does
    not load weights; callers must explicitly load the pinned checkpoint.
    Upstream's quantile columns are point/median at 0/5, q10 at 1, q90 at 9.
    """
    device = getattr(getattr(model, "model", None), "device", None)
    device_type = getattr(device, "type", device)
    if device_type != "cpu":
        raise ValueError("TimesFM experiment requires a model running on CPU")

    def predict(prefix: Sequence[tuple[int, float]]) -> Forecast:
        import numpy as np
        if not prefix:
            raise ValueError("forecast context cannot be empty")
        timestamp, price = prefix[-1]
        point, quantiles = model.forecast(
            horizon=24,
            inputs=[np.asarray([p for _, p in prefix], dtype=float)],
        )
        point_row = point[0]
        quantile_row = quantiles[0]
        if len(point_row) < 24 or quantile_row.shape[0] < 24 or quantile_row.shape[1] < 10:
            raise ValueError("TimesFM returned incomplete 24h forecast")
        return Forecast(
            origin_ms=timestamp,
            origin_price=float(price),
            median={h: float(quantile_row[h - 1, 5]) for h in (1, 4, 12, 24)},
            q10={24: float(quantile_row[23, 1])},
            q90={24: float(quantile_row[23, 9])},
        )
    return predict

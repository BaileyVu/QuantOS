"""Causal, training-only signal cache for portfolio research.

This module calls the registered candidate strategy classes directly. It stores
decisions only; it does not simulate fills, exits, PnL, or portfolio risk.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from bisect import bisect_right
import hashlib
import importlib
import json
from pathlib import Path
from typing import Iterable

from ..strategies.alt_dip_reversal import (
    AltDipReversalStrategy,
    HOLDING_HOURS as ALT_HOLDING_HOURS,
    CUTOFF_QUANTILES as ALT_CUTOFF_QUANTILES,
    STOP_FRACTION as ALT_STOP, SENSITIVITY_STOP_FRACTION as ALT_STOP_SENS,
)
from ..strategies.candidate_features import (
    ALT_SOURCE_UNIVERSE, HOUR_MS, TimedValue, calibrate_quarterly_return_cutoff,
    rolling_24h_return,
)
from ..strategies.crash_flush_e2 import (
    CrashFlushE2Strategy, MAJORS as CRASH_MAJORS,
    STOP_FRACTION as CRASH_STOP, SENSITIVITY_STOP_FRACTION as CRASH_STOP_SENS,
)
from ..strategies.ema_level_2h import (
    EmaLevel2hStrategy, MAJORS as EMA_MAJORS, _ema as _ema_source,
    FAST_EMA, SLOW_EMA, ATR_PERIOD, PIVOT_K, LEVEL_LOOKBACK, ZONE_TOL_ATR,
    MIN_PRIOR_TOUCHES, LEAVE_ATR, RETEST_BARS, NEAR_ATR, STOP_BUFFER_ATR,
    TARGET_R, MAX_HOLD_SECONDS,
)
from ..types import Candle, Instrument, OpenInterestValue, Quote, Signal, Snapshot
from .data import Bar, OpenInterestMetric

D = Decimal
TRAINING_START_MS = 1_633_046_400_000  # 2021-10-01T00:00:00Z
TRAINING_END_MS = 1_735_689_600_000    # 2025-01-01T00:00:00Z (exclusive)
_TICK = D("0.00000001")
_EMA_PARAMETERS = tuple((key, str(value)) for key, value in (
    ("timeframe", "2h"), ("fast_ema", FAST_EMA), ("slow_ema", SLOW_EMA),
    ("atr_period", ATR_PERIOD), ("pivot_half_width", PIVOT_K),
    ("level_lookback", LEVEL_LOOKBACK), ("zone_tolerance_atr", ZONE_TOL_ATR),
    ("minimum_prior_touches", MIN_PRIOR_TOUCHES), ("leave_atr", LEAVE_ATR),
    ("retest_bars", RETEST_BARS), ("near_atr", NEAR_ATR),
    ("stop_buffer_atr", STOP_BUFFER_ATR), ("target_r", TARGET_R),
    ("maximum_hold_seconds", MAX_HOLD_SECONDS),
))


@dataclass(frozen=True)
class SignalRecord:
    """One strategy decision with its causal boundary and frozen parameters."""

    decision_ms: int
    signal: Signal
    parameters: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if self.decision_ms != self.signal.observed_ms:
            raise ValueError("signal decision time must equal strategy observed time")


@dataclass(frozen=True)
class SignalCache:
    records: tuple[SignalRecord, ...]
    training_window: tuple[int, int]
    source_hash: str
    data_hash: str
    filter_hash: str
    strategy_hash: str
    parameter_hash: str

    def __iter__(self):
        return iter(self.records)


def _candles(rows: tuple[Bar, ...]) -> tuple[Candle, ...]:
    return tuple(row.to_candle() for row in rows)


def _instrument(symbol: str) -> Instrument:
    return Instrument(symbol, "USDT", "TRADING", _TICK, _TICK, D(0), D("1e20"), D(0))


def _quarter_start_ms(time_ms: int) -> int:
    from datetime import datetime, timezone
    now = datetime.fromtimestamp(time_ms / 1000, timezone.utc)
    start = datetime(now.year, ((now.month - 1) // 3) * 3 + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp() * 1000)


def _oi_values(rows: tuple[OpenInterestMetric, ...]) -> tuple[OpenInterestValue, ...]:
    # Select only an observation whose raw archive label is exactly on a UTC
    # hour. Keep its source label and one-hour conservative availability proxy;
    # do not aggregate, forward-fill, or choose a nearest 5m row.
    return tuple(OpenInterestValue(row.label_ms, row.conservative_available_ms,
                                   row.sum_open_interest_value)
                 for row in rows if row.label_ms % HOUR_MS == 0)


def _snapshot(symbol: str, at_ms: int, instrument: Instrument,
              hourly: tuple[Candle, ...], two_hour: tuple[Candle, ...],
              oi: tuple[OpenInterestValue, ...], cutoff: Decimal | None = None,
              calibration_end_ms: int | None = None) -> Snapshot:
    # The close is the only causal quote available to these research strategies.
    bar = hourly[-1] if hourly else two_hour[-1]
    quote = Quote(symbol, at_ms, at_ms, bar.close, bar.close, D(0), D(0), bar.close)
    return Snapshot(at_ms, instrument, quote, (), (), candles_1h=hourly,
                    candles_2h=two_hour, oi_values=oi,
                    quarterly_return_cutoff=cutoff,
                    calibration_end_ms=calibration_end_ms)


def _params(strategy) -> tuple[tuple[str, str], ...]:
    values = []
    for name in ("cutoff_quantile", "holding_hours", "stop_fraction"):
        if hasattr(strategy, name):
            values.append((name, str(getattr(strategy, name))))
    return tuple(values)


def _quarter_cutoffs(bars_by_symbol: dict[str, tuple[Bar, ...]],
                     start_ms: int, quantiles: tuple[Decimal, ...]) -> dict[tuple[int, Decimal], Decimal | None]:
    """Build frozen ALT thresholds; any incomplete member makes that quarter unusable."""
    cache: dict[tuple[int, Decimal], Decimal | None] = {}
    if not set(ALT_SOURCE_UNIVERSE).issubset(bars_by_symbol):
        return {(start_ms, q): None for q in quantiles}
    returns = {}
    for symbol in ALT_SOURCE_UNIVERSE:
        hourly = _candles(bars_by_symbol[symbol])
        series = rolling_24h_return(hourly)
        returns[symbol] = series or ()
    for quantile in quantiles:
        calibrated = calibrate_quarterly_return_cutoff(returns, start_ms, quantile)
        cache[(start_ms, quantile)] = calibrated.threshold if calibrated else None
    return cache


def build_signal_cache(index, *, alt_quantiles: Iterable[Decimal] = tuple(sorted(ALT_CUTOFF_QUANTILES)),
                       alt_holds: Iterable[int] = tuple(sorted(ALT_HOLDING_HOURS)),
                       include_stop_sensitivity: bool = True,
                       include_crash: bool = True, include_alt: bool = True,
                       include_ema: bool = True,
                       filter_snapshot_path: str | Path = "data/portfolio/filters/current_usdt_proxy_20261029.json",
                       symbols: Iterable[str] | None = None) -> SignalCache:
    """Evaluate candidates at completed training bars and cache only signals.

    `index` is the strict research DatasetIndex. Strategy snapshots contain only
    the as-of candle prefixes, so strategy implementations cannot see future
    bars. ALT uses the fixed pooled 29-symbol [quarter-90d, quarter-3d) cutoff;
    missing any required quarter sample fails closed.
    """
    if tuple(index.window) != ("2021-10-01", "2024-12-31"):
        raise ValueError("signal generation is restricted to the 2021-10-01..2024-12-31 training index")
    filter_path = Path(filter_snapshot_path)
    filter_bytes = filter_path.read_bytes()
    filter_payload = json.loads(filter_bytes)
    if "not point-in-time" not in filter_payload.get("note", ""):
        raise ValueError("instrument filter snapshot must be declared as a current historical proxy")
    current_symbols = {item.get("symbol") for item in filter_payload.get("symbols", ())
                       if item.get("status") == "TRADING" and item.get("marginAsset") == "USDT"
                       and item.get("quoteAsset") == "USDT" and item.get("contractType") == "PERPETUAL"}
    wanted = set(symbols) if symbols is not None else None
    by_bars: dict[str, tuple[Bar, ...]] = index.bars
    selected = sorted((wanted or set(by_bars)) & set(by_bars) & current_symbols)
    alt_qs, alt_hs = tuple(alt_quantiles), tuple(alt_holds)
    stop_grid = (ALT_STOP, ALT_STOP_SENS) if include_stop_sensitivity else (ALT_STOP,)
    crash_stops = (CRASH_STOP, CRASH_STOP_SENS) if include_stop_sensitivity else (CRASH_STOP,)
    records: list[SignalRecord] = []
    quarters: dict[int, dict[tuple[int, Decimal], Decimal | None]] = {}
    hourly_cache: dict[str, tuple[Candle, ...]] = {}
    two_hour_cache: dict[str, tuple[Candle, ...]] = {}
    oi_cache = {symbol: _oi_values(index.metrics.get(symbol, ())) for symbol in selected}
    oi_available = {symbol: tuple(value.available_ms for value in values)
                    for symbol, values in oi_cache.items()}
    instruments = {symbol: _instrument(symbol) for symbol in selected}

    for symbol in selected:
        raw = by_bars[symbol]
        hourly = _candles(raw)
        hourly_cache[symbol] = hourly
        # Hourly rows begin on hour boundaries, hence this aggregation is epoch-aligned.
        two_hour_cache[symbol] = tuple(
            Candle(hourly[i].open_ms, hourly[i + 1].close_ms, hourly[i].open,
                   max(hourly[i].high, hourly[i + 1].high),
                   min(hourly[i].low, hourly[i + 1].low), hourly[i + 1].close,
                   hourly[i].volume + hourly[i + 1].volume)
            for i in range(0, len(hourly) - 1, 2)
            if hourly[i].open_ms % (2 * HOUR_MS) == 0
            and hourly[i + 1].open_ms == hourly[i].open_ms + HOUR_MS
        )

    # Quarterly calibration is prepared once per quarter and per quantile.
    if include_alt:
        for symbol in selected:
            for row in hourly_cache[symbol]:
                if TRAINING_START_MS <= row.close_ms + 1 < TRAINING_END_MS:
                    quarter = _quarter_start_ms(row.close_ms + 1)
                    if quarter not in quarters:
                        quarters[quarter] = _quarter_cutoffs(by_bars, quarter, alt_qs)
                    break
        # Ensure later quarters are calibrated even if this symbol has a gap.
        all_times = sorted({_quarter_start_ms(row.close_ms + 1)
                            for values in hourly_cache.values() for row in values
                            if TRAINING_START_MS <= row.close_ms + 1 < TRAINING_END_MS})
        for quarter in all_times:
            if quarter not in quarters:
                quarters[quarter] = _quarter_cutoffs(by_bars, quarter, alt_qs)

    for symbol in selected:
        hourly = hourly_cache[symbol]
        two_hour = two_hour_cache[symbol]
        oi = oi_cache[symbol]
        instrument = instruments[symbol]
        alt_strategies = ([AltDipReversalStrategy(q, hold, stop)
                           for q in alt_qs for hold in alt_hs for stop in stop_grid]
                          if include_alt and symbol in ALT_SOURCE_UNIVERSE else [])
        crash_strategies = ([CrashFlushE2Strategy(hold, stop)
                             for hold in (24, 48, 72) for stop in crash_stops]
                            if include_crash and symbol in CRASH_MAJORS else [])
        ema_strategy = EmaLevel2hStrategy() if include_ema and symbol in EMA_MAJORS else None
        # Map decision boundaries to source indexes once. ALT and crash rules only
        # require bounded causal history; the EMA's first-observation seed requires
        # its entire prefix and is evaluated only on the source EMA crossover bars.
        by_hour = {bar.close_ms + 1: i + 1 for i, bar in enumerate(hourly)}
        by_2h = {bar.close_ms + 1: i + 1 for i, bar in enumerate(two_hour)}
        ema_crosses: set[int] = set()
        if ema_strategy is not None and two_hour:
            closes = tuple(bar.close for bar in two_hour)
            fast, slow = _ema_source(closes, 12), _ema_source(closes, 26)
            ema_crosses = {two_hour[i].close_ms + 1 for i in range(1, len(two_hour))
                           if ((fast[i - 1] <= slow[i - 1] and fast[i] > slow[i])
                               or (fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]))}
        for at_ms in sorted(set(by_hour) | set(by_2h)):
            if not TRAINING_START_MS <= at_ms < TRAINING_END_MS:
                continue
            hi = by_hour.get(at_ms, 0)
            ti = by_2h.get(at_ms, 0)
            # Strategies receive only the history their rules need. A missing
            # bar ages out after 25 hourly bars; crash OI gaps age out after its
            # 725-observation z-score window. No forward-filled samples enter.
            h_prefix = hourly[max(0, hi - 25):hi] if hi else ()
            t_prefix = two_hour[:ti] if ti and at_ms in ema_crosses else ()
            oi_end = bisect_right(oi_available[symbol], at_ms)
            oi_prefix = oi[max(0, oi_end - 725):oi_end]
            snap = _snapshot(symbol, at_ms, instrument, h_prefix, t_prefix, oi_prefix)
            history_valid = hi > 0
            if alt_strategies and history_valid:
                qstart = _quarter_start_ms(at_ms)
                # Candidate code validates calibration_end_ms against S-3d.
                cal_end = qstart - 3 * 24 * HOUR_MS
                cutoffs = quarters.get(qstart, {})
                for strategy in alt_strategies:
                    cutoff = cutoffs.get((qstart, strategy.cutoff_quantile))
                    if cutoff is None:
                        continue
                    signal = strategy.evaluate(_snapshot(symbol, at_ms, instrument,
                        h_prefix, t_prefix, oi_prefix, cutoff, cal_end))
                    if signal:
                        records.append(SignalRecord(at_ms, signal, _params(strategy)))
            latest_oi_matches = bool(oi_prefix and oi_prefix[-1].time_ms == at_ms - HOUR_MS)
            if crash_strategies and history_valid and latest_oi_matches:
                # All preregistered hold/stop settings share the same entry
                # predicates in the production strategy class. Evaluate the
                # primary once; only on an actual candidate signal, ask the
                # remaining current strategy instances for their exact records.
                signal = crash_strategies[0].evaluate(snap)
                if signal:
                    records.append(SignalRecord(at_ms, signal, _params(crash_strategies[0])))
                    for strategy in crash_strategies[1:]:
                        variant_signal = strategy.evaluate(snap)
                        if variant_signal:
                            records.append(SignalRecord(at_ms, variant_signal, _params(strategy)))
            if ema_strategy is not None and ti and at_ms in ema_crosses:
                signal = ema_strategy.evaluate(snap)
                if signal:
                    records.append(SignalRecord(at_ms, signal, _EMA_PARAMETERS))
    records.sort(key=lambda row: (row.decision_ms, row.signal.symbol,
                                  row.signal.strategy_id, row.parameters, row.signal.signal_id))
    strategy_modules = (
        "quantos.paper.strategies.alt_dip_reversal",
        "quantos.paper.strategies.candidate_features",
        "quantos.paper.strategies.crash_flush_e2",
        "quantos.paper.strategies.ema_level_2h",
        "quantos.paper.strategies.indicators",
    )
    strategy_digest = hashlib.sha256()
    for module_name in strategy_modules:
        module_path = Path(importlib.import_module(module_name).__file__)
        strategy_digest.update(module_name.encode() + b"\0" + module_path.read_bytes())
    strategy_hash = strategy_digest.hexdigest()
    filter_hash = hashlib.sha256(filter_bytes).hexdigest()
    parameter_payload = {
        "alt_quantiles": sorted(map(str, alt_qs)), "alt_holds": sorted(alt_hs),
        "stop_sensitivity": include_stop_sensitivity, "include_alt": include_alt,
        "include_crash": include_crash, "include_ema": include_ema,
        "symbols": selected, "training_window": [TRAINING_START_MS, TRAINING_END_MS],
    }
    parameter_hash = hashlib.sha256(json.dumps(parameter_payload, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    composite = hashlib.sha256((index.content_hash + filter_hash + strategy_hash + parameter_hash).encode()).hexdigest()
    return SignalCache(tuple(records), (TRAINING_START_MS, TRAINING_END_MS), composite,
                       index.content_hash, filter_hash, strategy_hash, parameter_hash)

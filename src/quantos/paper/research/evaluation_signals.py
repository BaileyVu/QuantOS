"""Sealed evaluation adapter; the original training generator stays immutable.

The causal generation loop is copied from the frozen training implementation.
Only the fixed window, explicit ALT cohort and provenance envelope differ.
No fills or account outcomes are computed here.
"""
from bisect import bisect_right
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
from typing import Iterable
from . import signals as training
from .signals import (
    SignalCache, SignalRecord, ALT_CUTOFF_QUANTILES, ALT_HOLDING_HOURS,
    ALT_STOP, ALT_STOP_SENS, CRASH_STOP, CRASH_STOP_SENS, CRASH_MAJORS,
    EMA_MAJORS, HOUR_MS, Candle, Bar, TimedValue, AltDipReversalStrategy,
    CrashFlushE2Strategy, EmaLevel2hStrategy, _ema_source, _EMA_PARAMETERS,
    _strategy_source_hash, _candles, _oi_values, _quarter_start_ms,
    _snapshot, _evaluate_variants, _params, _source_hash, _records_hash,
    _record_from_payload, resample_hourly, rolling_24h_return,
    calibrate_quarterly_return_cutoff, parse_exchange_info,
)
from .protocol import fingerprint
D = Decimal
EVALUATION_START_MS = 1735689600000
EVALUATION_END_MS = 1793232000000  # 2026-10-29T00:00:00Z


def _generator_source_hash():
    return fingerprint({"adapter": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                        "training_helpers": training._generator_source_hash()})


def validate_context(index, sealed, cohort, symbols):
    if fingerprint(sealed["payload"]) != sealed["sha256"]:
        raise ValueError("modified evaluation seal")
    plan = sealed["payload"]["plan"]
    if (plan.get("evaluation_start") != "2025-01-01T00:00:00Z" or
        plan.get("evaluation_end_exclusive") != "2026-10-29T00:00:00Z" or
        tuple(index.window) != ("2024-10-01", "2026-10-28")):
        raise ValueError("invalid evaluation window")
    if cohort not in {"primary", "verified_volume"}:
        raise ValueError("unknown evaluation cohort")
    selected = tuple(sealed["payload"]["selection"][cohort + "_symbols"])
    if len(selected) != 29 or len(set(selected)) != 29:
        raise ValueError("invalid sealed cohort")
    if symbols is None or set(symbols) != set(selected):
        raise ValueError("explicit symbols must match the complete sealed cohort")
    if set(index.bars) != set(selected):
        raise ValueError("index must contain exactly the sealed cohort")
    if any(a.gaps or a.duplicates for a in index.audit if a.dataset in {"klines", "markPriceKlines"}):
        raise ValueError("gapped price history requires repair or an explicit not-evaluable report")
    return selected


def _cohort_cutoffs(returns_by_symbol, quarter, quantiles, cohort):
    result = {}
    for q in quantiles:
        calibration = calibrate_quarterly_return_cutoff(returns_by_symbol, quarter, q,
                                                       symbols=tuple(cohort))
        result[(quarter, q)] = calibration.threshold if calibration else None
    return result


def evaluation_parameter_hash(sealed, cohort, selected, qs, holds, sensitivity=True,
                              alt=True, crash=True, ema=True):
    return fingerprint({"seal": sealed["sha256"], "cohort": cohort,
        "symbols": sorted(selected), "alt_quantiles": sorted(map(str, qs)),
        "alt_holds": sorted(holds), "sensitivity": sensitivity,
        "alt": alt, "crash": crash, "ema": ema,
        "window": [EVALUATION_START_MS, EVALUATION_END_MS]})


def load_evaluation_signal_cache(path, *, sealed, cohort, index, filters_path):
    symbols = sealed["payload"]["selection"][cohort + "_symbols"]
    validate_context(index, sealed, cohort, symbols)
    raw = Path(path).read_bytes()
    envelope = json.loads(gzip.decompress(raw) if str(path).endswith(".gz") else raw)
    payload = envelope["payload"]
    if payload.get("schema") != 2 or fingerprint(payload) != envelope["sha256"]:
        raise ValueError("cache content checksum mismatch")
    expected = {"data_hash": index.content_hash,
        "filter_hash": training.filter_snapshot_hash(filters_path),
        "strategy_hash": _strategy_source_hash(), "generator_hash": _generator_source_hash(),
        "parameter_hash": evaluation_parameter_hash(sealed, cohort, symbols,
            tuple(sorted(ALT_CUTOFF_QUANTILES)), tuple(sorted(ALT_HOLDING_HOURS)))}
    for k, v in expected.items():
        if payload["provenance"].get(k) != v:
            raise ValueError(f"stale evaluation cache {k}")
    records = tuple(_record_from_payload(r) for r in payload["records"])
    p = payload["provenance"]
    if p["records_hash"] != _records_hash(records):
        raise ValueError("cache records checksum mismatch")
    window = tuple(payload["training_window"])
    if window != (EVALUATION_START_MS, EVALUATION_END_MS) or any(
            not window[0] <= r.decision_ms < window[1] for r in records):
        raise ValueError("out-of-window evaluation signal")
    if p["source_hash"] != _source_hash(*(p[k] for k in
            ("data_hash", "filter_hash", "strategy_hash", "generator_hash", "parameter_hash"))):
        raise ValueError("cache provenance checksum mismatch")
    return SignalCache(records, window, p["source_hash"], p["data_hash"], p["filter_hash"],
                       p["strategy_hash"], p["generator_hash"], p["parameter_hash"], p["records_hash"])


def build_evaluation_signal_cache(index, *, sealed: dict, cohort: str, alt_quantiles: Iterable[Decimal] = tuple(sorted(ALT_CUTOFF_QUANTILES)),
                       alt_holds: Iterable[int] = tuple(sorted(ALT_HOLDING_HOURS)),
                       include_stop_sensitivity: bool = True,
                       include_crash: bool = True, include_alt: bool = True,
                       include_ema: bool = True,
                       filter_snapshot_path: str | Path = "data/portfolio/filters/current_usdt_proxy_20261029.json",
                       symbols: Iterable[str] | None = None) -> SignalCache:
    """Evaluate unchanged candidate classes at completed held-out bars and cache only signals.

    `index` is the strict research DatasetIndex. Strategy snapshots contain only
    the as-of candle prefixes, so strategy implementations cannot see future
    bars. ALT uses the fixed pooled 29-symbol [quarter-90d, quarter-3d) cutoff;
    missing any required quarter sample fails closed.
    """
    alt_universe = validate_context(index, sealed, cohort, symbols)
    generator_hash_before = _generator_source_hash()
    strategy_hash_before = _strategy_source_hash()
    filter_path = Path(filter_snapshot_path)
    filter_bytes = filter_path.read_bytes()
    filter_payload = json.loads(filter_bytes)
    if "not point-in-time" not in filter_payload.get("note", ""):
        raise ValueError("instrument filter snapshot must be declared as a current historical proxy")
    current_instruments = parse_exchange_info(filter_payload, margin_asset="USDT")
    current_instruments = {symbol: item for symbol, item in current_instruments.items()
                           if item.status == "TRADING"}
    wanted = set(symbols) if symbols is not None else None
    by_bars: dict[str, tuple[Bar, ...]] = index.bars
    selected = sorted((wanted or set(by_bars)) & set(by_bars) & set(current_instruments))
    if set(selected) != set(alt_universe):
        raise ValueError("missing current filter proxy for sealed cohort; cannot silently shrink universe")
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
    instruments = {symbol: current_instruments[symbol] for symbol in selected}

    for symbol in selected:
        raw = by_bars[symbol]
        hourly = _candles(raw)
        hourly_cache[symbol] = hourly
        # Bucket by UTC epoch timestamps, and omit incomplete or gapped groups.
        resampled = resample_hourly(hourly, 2, as_of_ms=hourly[-1].close_ms) if hourly else None
        two_hour_cache[symbol] = resampled or ()

    # Quarterly calibration is prepared once per quarter and per quantile.
    if include_alt:
        returns_by_symbol = {}
        for symbol in alt_universe:
            if symbol in by_bars:
                returns_by_symbol[symbol] = rolling_24h_return(_candles(by_bars[symbol])) or ()
        # Ensure later quarters are calibrated even if this symbol has a gap.
        all_times = sorted({_quarter_start_ms(row.close_ms + 1)
                            for values in hourly_cache.values() for row in values
                            if EVALUATION_START_MS <= row.close_ms + 1 < EVALUATION_END_MS})
        for quarter in all_times:
            quarters[quarter] = _cohort_cutoffs(returns_by_symbol, quarter, alt_qs, alt_universe)

    for symbol in selected:
        hourly = hourly_cache[symbol]
        two_hour = two_hour_cache[symbol]
        oi = oi_cache[symbol]
        instrument = instruments[symbol]
        alt_strategies = ([AltDipReversalStrategy(q, hold, stop)
                           for q in alt_qs for hold in alt_hs for stop in stop_grid]
                          if include_alt and symbol in alt_universe else [])
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
            if not EVALUATION_START_MS <= at_ms < EVALUATION_END_MS:
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
                by_quantile: dict[Decimal, list[AltDipReversalStrategy]] = {}
                for strategy in alt_strategies:
                    by_quantile.setdefault(strategy.cutoff_quantile, []).append(strategy)
                for quantile, variants in by_quantile.items():
                    cutoff = cutoffs.get((qstart, quantile))
                    if cutoff is None:
                        continue
                    alt_snapshot = _snapshot(symbol, at_ms, instrument,
                        h_prefix, t_prefix, oi_prefix, cutoff, cal_end)
                    # Entry predicates do not depend on hold or stop settings;
                    # sibling classes are still called to construct each exact signal.
                    for strategy, signal in _evaluate_variants(variants, alt_snapshot):
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
    strategy_hash = _strategy_source_hash()
    generator_hash = _generator_source_hash()
    if strategy_hash != strategy_hash_before:
        raise RuntimeError("candidate strategy source changed during signal-cache generation")
    if generator_hash != generator_hash_before:
        raise RuntimeError("signal-cache generator source changed during generation")
    filter_hash = hashlib.sha256(filter_bytes).hexdigest()
    parameter_hash = evaluation_parameter_hash(sealed, cohort, selected, alt_qs, alt_hs,
        include_stop_sensitivity, include_alt, include_crash, include_ema)
    composite = _source_hash(index.content_hash, filter_hash, strategy_hash,
                             generator_hash, parameter_hash)
    frozen_records = tuple(records)
    return SignalCache(tuple(records), (EVALUATION_START_MS, EVALUATION_END_MS), composite,
                       index.content_hash, filter_hash, strategy_hash, generator_hash,
                       parameter_hash, _records_hash(frozen_records))

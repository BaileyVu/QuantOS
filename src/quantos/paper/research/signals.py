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
import gzip
import os
from pathlib import Path
import tempfile
from typing import Iterable

from ..strategies.alt_dip_reversal import (
    AltDipReversalStrategy,
    HOLDING_HOURS as ALT_HOLDING_HOURS,
    CUTOFF_QUANTILES as ALT_CUTOFF_QUANTILES,
    STOP_FRACTION as ALT_STOP, SENSITIVITY_STOP_FRACTION as ALT_STOP_SENS,
)
from ..strategies.candidate_features import (
    ALT_SOURCE_UNIVERSE, HOUR_MS, TimedValue, calibrate_quarterly_return_cutoff,
    resample_hourly, rolling_24h_return,
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
from ..market import parse_exchange_info
from .data import Bar, OpenInterestMetric
from .dataset import DatasetIndex

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
    generator_hash: str
    parameter_hash: str
    records_hash: str

    def __iter__(self):
        return iter(self.records)


def _strategy_source_hash() -> str:
    modules = (
        "quantos.paper.strategies.alt_dip_reversal",
        "quantos.paper.strategies.candidate_features",
        "quantos.paper.strategies.crash_flush_e2",
        "quantos.paper.strategies.ema_level_2h",
        "quantos.paper.strategies.indicators",
    )
    digest = hashlib.sha256()
    for module_name in modules:
        module_path = Path(importlib.import_module(module_name).__file__)
        digest.update(module_name.encode() + b"\0" + module_path.read_bytes())
    return digest.hexdigest()


def _generator_source_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def filter_snapshot_hash(path: str | Path =
                         "data/portfolio/filters/current_usdt_proxy_20261029.json") -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _parameter_hash(selected: Iterable[str], alt_quantiles: Iterable[Decimal],
                    alt_holds: Iterable[int], include_stop_sensitivity: bool,
                    include_alt: bool, include_crash: bool, include_ema: bool) -> str:
    payload = {
        "alt_quantiles": sorted(map(str, alt_quantiles)),
        "alt_holds": sorted(alt_holds),
        "stop_sensitivity": include_stop_sensitivity, "include_alt": include_alt,
        "include_crash": include_crash, "include_ema": include_ema,
        "symbols": sorted(selected), "training_window": [TRAINING_START_MS, TRAINING_END_MS],
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def expected_signal_parameter_hash(index, *, alt_quantiles: Iterable[Decimal] =
                                   tuple(sorted(ALT_CUTOFF_QUANTILES)),
                                   alt_holds: Iterable[int] = tuple(sorted(ALT_HOLDING_HOURS)),
                                   include_stop_sensitivity: bool = True,
                                   include_crash: bool = True, include_alt: bool = True,
                                   include_ema: bool = True,
                                   filter_snapshot_path: str | Path =
                                   "data/portfolio/filters/current_usdt_proxy_20261029.json",
                                   symbols: Iterable[str] | None = None) -> str:
    """Recompute the requested grid fingerprint without evaluating any bars."""
    payload = json.loads(Path(filter_snapshot_path).read_text(encoding="utf-8"))
    instruments = parse_exchange_info(payload, margin_asset="USDT")
    instruments = {symbol: item for symbol, item in instruments.items() if item.status == "TRADING"}
    wanted = set(symbols) if symbols is not None else set(index.bars)
    selected = sorted(wanted & set(index.bars) & set(instruments))
    return _parameter_hash(selected, tuple(alt_quantiles), tuple(alt_holds),
                           include_stop_sensitivity, include_alt, include_crash, include_ema)


def _source_hash(data_hash: str, filter_hash: str, strategy_hash: str,
                 generator_hash: str, parameter_hash: str) -> str:
    return hashlib.sha256(_canonical({"data": data_hash, "filter": filter_hash,
        "strategy": strategy_hash, "generator": generator_hash,
        "parameters": parameter_hash})).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _record_payload(record: SignalRecord) -> dict:
    signal = record.signal
    return {
        "decision_ms": record.decision_ms,
        "parameters": list(record.parameters),
        "signal": {
            "strategy_id": signal.strategy_id, "strategy_version": signal.strategy_version,
            "signal_id": signal.signal_id, "symbol": signal.symbol, "side": signal.side,
            "observed_ms": signal.observed_ms, "expires_ms": signal.expires_ms,
            "entry_type": signal.entry_type, "entry_price": str(signal.entry_price),
            "stop_loss": str(signal.stop_loss),
            "take_profit": None if signal.take_profit is None else str(signal.take_profit),
            "maximum_hold_seconds": signal.maximum_hold_seconds, "reason": signal.reason,
            "features": [[name, str(value)] for name, value in signal.features],
            "trend_regime": signal.trend_regime, "vol_regime": signal.vol_regime,
        },
    }


def _records_hash(records: Iterable[SignalRecord]) -> str:
    return hashlib.sha256(_canonical([_record_payload(record) for record in records])).hexdigest()


def _record_from_payload(row: dict) -> SignalRecord:
    from ..types import Signal
    raw = row["signal"]
    signal = Signal(
        raw["strategy_id"], raw["strategy_version"], raw["signal_id"], raw["symbol"],
        raw["side"], int(raw["observed_ms"]), int(raw["expires_ms"]), raw["entry_type"],
        D(raw["entry_price"]), D(raw["stop_loss"]),
        None if raw["take_profit"] is None else D(raw["take_profit"]),
        int(raw["maximum_hold_seconds"]), raw["reason"],
        tuple((str(name), D(value)) for name, value in raw["features"]),
        raw["trend_regime"], raw["vol_regime"],
    )
    return SignalRecord(int(row["decision_ms"]), signal,
                        tuple((str(k), str(v)) for k, v in row["parameters"]))


def save_signal_cache(cache: SignalCache, path: str | Path) -> str:
    """Atomically create an immutable JSON or .gz cache without overwriting."""
    target = Path(path)
    payload = {
        "schema": 2, "training_window": list(cache.training_window),
        "provenance": {"source_hash": cache.source_hash, "data_hash": cache.data_hash,
                       "filter_hash": cache.filter_hash, "strategy_hash": cache.strategy_hash,
                       "generator_hash": cache.generator_hash,
                       "parameter_hash": cache.parameter_hash,
                       "records_hash": cache.records_hash},
        "records": [_record_payload(record) for record in cache.records],
    }
    actual_records_hash = hashlib.sha256(_canonical(payload["records"])).hexdigest()
    if actual_records_hash != cache.records_hash:
        raise ValueError("signal cache records changed after generation")
    if cache.source_hash != _source_hash(cache.data_hash, cache.filter_hash,
            cache.strategy_hash, cache.generator_hash, cache.parameter_hash):
        raise ValueError("signal cache provenance hash mismatch")
    serialized = _canonical(payload)
    envelope = _canonical({"payload": payload,
                           "sha256": hashlib.sha256(serialized).hexdigest()}) + b"\n"
    content = gzip.compress(envelope, mtime=0) if target.suffix == ".gz" else envelope
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".signal-cache-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # A hard-link publication is atomic and fails if target already exists.
        os.link(temp_name, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return hashlib.sha256(serialized).hexdigest()
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def load_signal_cache(path: str | Path, *, expected_data_hash: str,
                      expected_filter_hash: str, expected_parameter_hash: str,
                      expected_strategy_hash: str | None = None,
                      expected_generator_hash: str | None = None,
                      filter_snapshot_path: str | Path =
                      "data/portfolio/filters/current_usdt_proxy_20261029.json") -> SignalCache:
    """Load a cache only when current data, rules, filters, and params match."""
    source_path = Path(path)
    content = source_path.read_bytes()
    raw = gzip.decompress(content) if source_path.suffix == ".gz" else content
    envelope = json.loads(raw)
    payload = envelope["payload"]
    if (payload.get("schema") != 2
            or hashlib.sha256(_canonical(payload)).hexdigest() != envelope.get("sha256")):
        raise ValueError("signal cache checksum or schema mismatch")
    provenance = payload["provenance"]
    if filter_snapshot_hash(filter_snapshot_path) != expected_filter_hash:
        raise ValueError("requested filter hash does not match the current filter snapshot")
    strategy_hash = expected_strategy_hash or _strategy_source_hash()
    generator_hash = expected_generator_hash or _generator_source_hash()
    required = {
        "data_hash": expected_data_hash, "filter_hash": expected_filter_hash,
        "parameter_hash": expected_parameter_hash, "strategy_hash": strategy_hash,
        "generator_hash": generator_hash,
    }
    for name, expected in required.items():
        if provenance.get(name) != expected:
            raise ValueError(f"signal cache {name} is stale")
    records = tuple(_record_from_payload(row) for row in payload["records"])
    records_hash = hashlib.sha256(_canonical(payload["records"])).hexdigest()
    if records_hash != provenance.get("records_hash"):
        raise ValueError("signal cache record checksum mismatch")
    cache = SignalCache(records,
                        tuple(int(x) for x in payload["training_window"]),
                        provenance["source_hash"], provenance["data_hash"],
                        provenance["filter_hash"], provenance["strategy_hash"],
                        provenance["generator_hash"], provenance["parameter_hash"],
                        provenance["records_hash"])
    if cache.training_window != (TRAINING_START_MS, TRAINING_END_MS):
        raise ValueError("signal cache training window is invalid")
    if any(not TRAINING_START_MS <= record.decision_ms < TRAINING_END_MS
           for record in cache.records):
        raise ValueError("signal cache contains an out-of-window decision")
    if cache.source_hash != _source_hash(cache.data_hash, cache.filter_hash,
            cache.strategy_hash, cache.generator_hash, cache.parameter_hash):
        raise ValueError("signal cache provenance checksum mismatch")
    return cache


def attest_legacy_signal_cache(path: str | Path, target: str | Path, *,
                               expected_data_hash: str, expected_filter_hash: str,
                               expected_strategy_hash: str, expected_parameter_hash: str,
                               verified_generator_hash: str, generator_revision: str,
                               filter_snapshot_path: str | Path =
                               "data/portfolio/filters/current_usdt_proxy_20261029.json"
                               ) -> SignalCache:
    """Upgrade a v1 cache only after caller verifies its generating source revision.

    The v1 envelope already checksums the complete record payload. The caller
    must provide the generator SHA derived from the recorded Git revision and
    confirm the generation command ran with those unchanged source bytes.
    """
    if len(verified_generator_hash) != 64 or not generator_revision:
        raise ValueError("legacy generator provenance needs a source revision and SHA-256")
    if filter_snapshot_hash(filter_snapshot_path) != expected_filter_hash:
        raise ValueError("legacy cache filter hash differs from the current snapshot")
    source_path = Path(path)
    raw = gzip.decompress(source_path.read_bytes()) if source_path.suffix == ".gz" else source_path.read_bytes()
    envelope = json.loads(raw)
    payload = envelope.get("payload", {})
    if (payload.get("schema") != 1
            or hashlib.sha256(_canonical(payload)).hexdigest() != envelope.get("sha256")):
        raise ValueError("legacy cache checksum or schema mismatch")
    old = payload["provenance"]
    for name, expected in (("data_hash", expected_data_hash),
                           ("filter_hash", expected_filter_hash),
                           ("strategy_hash", expected_strategy_hash),
                           ("parameter_hash", expected_parameter_hash)):
        if old.get(name) != expected:
            raise ValueError(f"legacy cache {name} does not match the verified source")
    old_source_hash = hashlib.sha256((expected_data_hash + expected_filter_hash
        + expected_strategy_hash + expected_parameter_hash).encode()).hexdigest()
    if old.get("source_hash") != old_source_hash:
        raise ValueError("legacy cache composite source hash mismatch")
    records = tuple(_record_from_payload(row) for row in payload["records"])
    window = tuple(int(x) for x in payload["training_window"])
    if window != (TRAINING_START_MS, TRAINING_END_MS) or any(
            not TRAINING_START_MS <= record.decision_ms < TRAINING_END_MS for record in records):
        raise ValueError("legacy cache includes an invalid training window")
    records_hash = _records_hash(records)
    source_hash = _source_hash(expected_data_hash, expected_filter_hash,
        expected_strategy_hash, verified_generator_hash, expected_parameter_hash)
    upgraded = SignalCache(records, window, source_hash, expected_data_hash,
        expected_filter_hash, expected_strategy_hash, verified_generator_hash,
        expected_parameter_hash, records_hash)
    save_signal_cache(upgraded, target)
    return upgraded


def validate_cache_provenance(cache: SignalCache, *, data_hash: str,
                              filter_hash: str, parameter_hash: str) -> None:
    """Fail closed unless a loaded cache matches the current research inputs/code."""
    expected_strategy = _strategy_source_hash()
    expected_generator = _generator_source_hash()
    if cache.data_hash != data_hash:
        raise ValueError("signal cache data hash is stale")
    if cache.filter_hash != filter_hash:
        raise ValueError("signal cache filter hash is stale")
    if cache.parameter_hash != parameter_hash:
        raise ValueError("signal cache parameter grid is stale")
    if cache.strategy_hash != expected_strategy:
        raise ValueError("signal cache strategy source is stale")
    if cache.generator_hash != expected_generator:
        raise ValueError("signal cache generator source is stale")
    if _records_hash(cache.records) != cache.records_hash:
        raise ValueError("signal cache records were modified")
    if cache.source_hash != _source_hash(cache.data_hash, cache.filter_hash,
            cache.strategy_hash, cache.generator_hash, cache.parameter_hash):
        raise ValueError("signal cache provenance checksum mismatch")


def _candles(rows: tuple[Bar, ...]) -> tuple[Candle, ...]:
    return tuple(row.to_candle() for row in rows)


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


def _evaluate_variants(variants, snapshot):
    """Evaluate a parameter group once for entry, then materialize exact sibling signals."""
    if not variants:
        return ()
    primary = variants[0].evaluate(snapshot)
    if primary is None:
        return ()
    result = [(variants[0], primary)]
    for strategy in variants[1:]:
        signal = strategy.evaluate(snapshot)
        if signal is not None:
            result.append((strategy, signal))
    return tuple(result)


def _quarter_cutoffs(returns_by_symbol: dict[str, tuple[TimedValue, ...]],
                     start_ms: int, quantiles: tuple[Decimal, ...]) -> dict[tuple[int, Decimal], Decimal | None]:
    """Build frozen ALT thresholds; any incomplete member makes that quarter unusable."""
    cache: dict[tuple[int, Decimal], Decimal | None] = {}
    if not set(ALT_SOURCE_UNIVERSE).issubset(returns_by_symbol):
        return {(start_ms, q): None for q in quantiles}
    for quantile in quantiles:
        calibrated = calibrate_quarterly_return_cutoff(returns_by_symbol, start_ms, quantile)
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
        for symbol in ALT_SOURCE_UNIVERSE:
            if symbol in by_bars:
                returns_by_symbol[symbol] = rolling_24h_return(_candles(by_bars[symbol])) or ()
        # Ensure later quarters are calibrated even if this symbol has a gap.
        all_times = sorted({_quarter_start_ms(row.close_ms + 1)
                            for values in hourly_cache.values() for row in values
                            if TRAINING_START_MS <= row.close_ms + 1 < TRAINING_END_MS})
        for quarter in all_times:
            quarters[quarter] = _quarter_cutoffs(returns_by_symbol, quarter, alt_qs)

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
    parameter_hash = _parameter_hash(selected, alt_qs, alt_hs, include_stop_sensitivity,
                                     include_alt, include_crash, include_ema)
    composite = _source_hash(index.content_hash, filter_hash, strategy_hash,
                             generator_hash, parameter_hash)
    frozen_records = tuple(records)
    return SignalCache(tuple(records), (TRAINING_START_MS, TRAINING_END_MS), composite,
                       index.content_hash, filter_hash, strategy_hash, generator_hash,
                       parameter_hash, _records_hash(frozen_records))

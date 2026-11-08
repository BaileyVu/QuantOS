"""Preregistered BUILD-only, non-overlapping 24h TimesFM volatility comparison.

The protocol is fixed to BTCUSDT, ETHUSDT and SOLUSDT proxy series for 2022.
No strategy/risk changes, training, HELD evaluation, or signal promotion occur.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
import platform
from pathlib import Path
import random
import subprocess
import sys

from .registry import (ResearchRegistry, canonical_json, sha256_bytes,
                       verify_dataset_manifest)
from .timesfm_experiment import HOUR_MS, assert_period, features_at, register_variants
from .timesfm_model import MODEL_ID, MODEL_REVISION, WEIGHTS_SHA256

HORIZON = 24
CONTEXT = 512
STRIDE = 24
NORMAL_80_WIDTH = 2.5631031310892007
NORMAL_RANGE_MEAN = math.sqrt(8 / math.pi)
EWMA_ALPHA = 0.06
ATR_WINDOW = 14
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
MARKET = "Binance USD-M USDT proxy"
TIMEFRAME = "1h"
TIMESTAMP_SEMANTICS = "completed_close_exclusive_ms"
START_DATE = date(2022, 1, 1)
END_DATE = date(2022, 12, 31)
BOOTSTRAP_BLOCK = 7
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 0
BASELINES = ("persistence", "ewma", "atr")
PREDICTIONS = ("timesfm",) + BASELINES
PROGRESS_NAME = "progress.jsonl"
PROGRESS_ZERO = "0" * 64
SOURCE_FILES = (
    "src/quantos/paper/research/timesfm_volatility.py",
    "src/quantos/paper/research/timesfm_experiment.py",
    "src/quantos/paper/research/timesfm_model.py",
    "src/quantos/paper/research/registry.py",
    "requirements-timesfm25.txt",
)


def _utc_ms(day: date, *, hour: int = 0) -> int:
    return int(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc).timestamp() * 1000)


def load_build_bars(manifest_path, expected_hash):
    """Verify one complete, manifested 2022 series before using its bar file."""
    path = Path(manifest_path).resolve()
    # Read only manifest metadata before checksum/data access; reject non-pilot
    # ranges and series declarations before opening the underlying bar file.
    metadata = json.loads(path.read_text(encoding="utf-8"))
    window = metadata.get("range", {})
    if window != {"start": START_DATE.isoformat(), "end": END_DATE.isoformat()}:
        raise ValueError("pilot requires the full 2022 BUILD date range")
    series = metadata.get("series")
    if (not isinstance(series, dict)
            or set(series) != {"symbol", "market", "timeframe", "timestamp_semantics"}
            or series.get("symbol") not in SYMBOLS
            or series.get("market") != MARKET
            or series.get("timeframe") != TIMEFRAME
            or series.get("timestamp_semantics") != TIMESTAMP_SEMANTICS):
        raise ValueError("manifest series does not match the predeclared 1h USDT proxy contract")
    symbol = series["symbol"]
    manifest = verify_dataset_manifest(path, expected_hash=expected_hash)
    if manifest["missing_ranges"]:
        raise ValueError("gapped dataset is not eligible for the fixed 2022 pilot")
    start_ms = _utc_ms(START_DATE)
    end_exclusive_ms = _utc_ms(date(2023, 1, 1))
    expected_count = (end_exclusive_ms - start_ms) // HOUR_MS
    rows = []
    with (path.parent / manifest["dataset"]["path"]).open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            item = json.loads(line)
            if item.get("symbol") != symbol:
                raise ValueError(f"row {line_number} symbol differs from manifest series")
            ts = item.get("time_ms")
            if (not isinstance(ts, int) or isinstance(ts, bool) or ts % HOUR_MS
                    or not start_ms <= ts < end_exclusive_ms):
                raise ValueError(f"row {line_number} timestamp outside full 2022 UTC close range")
            assert_period(ts, "BUILD")
            values = (item.get("high"), item.get("low"), item.get("close"))
            if any(isinstance(value, bool) for value in values):
                raise ValueError(f"row {line_number} OHLC values must be numeric, not booleans")
            try:
                high, low, close = (float(value) for value in values)
            except (TypeError, ValueError):
                raise ValueError(f"row {line_number} has invalid OHLC") from None
            if (not all(math.isfinite(x) and x > 0 for x in (high, low, close))
                    or not low <= close <= high):
                raise ValueError(f"row {line_number} has invalid positive OHLC")
            if rows and ts - rows[-1][0] != HOUR_MS:
                raise ValueError("hourly close timestamps must be strictly contiguous")
            rows.append((ts, high, low, close))
    if (len(rows) != expected_count or not rows or rows[0][0] != start_ms
            or rows[-1][0] != end_exclusive_ms - HOUR_MS):
        raise ValueError("series must cover every 2022 hourly close from 00:00 through 23:00 UTC")
    return symbol, rows, manifest


def expected_origins(rows):
    return {rows[i][0]: (rows[i][0] + HOUR_MS,
                         rows[i + HORIZON][0])
            for i in range(CONTEXT - 1, len(rows) - HORIZON, STRIDE)}


def _finite(value, field):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def _progress_entry(payload, previous_hash):
    entry = {**payload, "previous_sha256": previous_hash}
    entry["record_sha256"] = sha256_bytes(canonical_json(entry).encode("utf-8"))
    return entry


def read_progress(path: Path, *, plan_hash: str,
                  expected: dict[str, dict[int, tuple[int, int]]]):
    """Read a hash-chained progress log; reject tampering, duplicates and extras."""
    completed = {symbol: {} for symbol in expected}
    previous_hash = PROGRESS_ZERO
    if not path.exists():
        return completed, previous_hash
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                raise ValueError(f"invalid progress JSON at line {line_no}; refusing resume") from None
            if not isinstance(entry, dict) or entry.get("previous_sha256") != previous_hash:
                raise ValueError(f"progress chain mismatch at line {line_no}")
            claimed = entry.get("record_sha256")
            unhashed = dict(entry)
            unhashed.pop("record_sha256", None)
            actual = sha256_bytes(canonical_json(unhashed).encode("utf-8"))
            if claimed != actual:
                raise ValueError(f"progress checksum mismatch at line {line_no}")
            previous_hash = claimed
            if entry.get("plan_sha256") != plan_hash:
                raise ValueError("progress belongs to a different frozen plan")
            symbol = entry.get("symbol")
            origin = entry.get("origin_ms")
            if symbol not in expected or isinstance(origin, bool) or not isinstance(origin, int):
                raise ValueError("progress contains unknown symbol/origin")
            if origin not in expected[symbol]:
                raise ValueError("progress contains unknown origin")
            start_ms, end_ms = expected[symbol][origin]
            if (entry.get("target_start_ms") != start_ms
                    or entry.get("target_end_ms") != end_ms):
                raise ValueError("progress target interval differs from frozen origin")
            if origin in completed[symbol]:
                raise ValueError("duplicate completed origin in progress log")
            observation = entry.get("observation")
            if not isinstance(observation, dict):
                raise ValueError("progress observation must be an object")
            _finite(observation.get("realized"), "realized")
            predicted = observation.get("predicted")
            if not isinstance(predicted, dict) or set(predicted) != set(PREDICTIONS):
                raise ValueError("progress predictions do not match frozen methods")
            for value in predicted.values():
                _finite(value, "predicted volatility")
            features = observation.get("features")
            if not isinstance(features, dict):
                raise ValueError("progress features must be an object")
            for key, value in features.items():
                if value is not None:
                    _finite(value, f"feature {key}")
            completed[symbol][origin] = observation
    return completed, previous_hash


def append_progress(path: Path, *, payload: dict, previous_hash: str) -> str:
    entry = _progress_entry(payload, previous_hash)
    encoded = (canonical_json(entry) + "\n").encode("utf-8")
    with path.open("ab") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return entry["record_sha256"]


def _origin_observation(rows, origin, predictor):
    closes = [row[3] for row in rows]
    returns = [0.0] + [math.log(b / a) for a, b in zip(closes, closes[1:])]
    variances = [0.0] * len(rows)
    # First actual log return seeds EWMA state; each subsequent state uses the
    # same alpha recurrence. This is an initialization, not a forecast error.
    for i in range(1, origin + 1):
        variances[i] = returns[i] ** 2 if i == 1 else EWMA_ALPHA * returns[i] ** 2 + (1 - EWMA_ALPHA) * variances[i - 1]
    context = [(row[0], row[3]) for row in rows[origin - CONTEXT + 1:origin + 1]]
    forecast = predictor(context)
    features = features_at(forecast, completed_close_ms=rows[origin][0],
                           completed_close=closes[origin], prior_forecast=None)
    realized = math.sqrt(sum(r * r for r in returns[origin + 1:origin + HORIZON + 1]))
    spot = closes[origin]
    log_width = math.log(forecast.q90[HORIZON] / spot) - math.log(forecast.q10[HORIZON] / spot)
    atr_ranges = [max(rows[i][1] - rows[i][2], abs(rows[i][1] - closes[i - 1]),
                      abs(rows[i][2] - closes[i - 1])) / closes[i]
                  for i in range(origin - ATR_WINDOW + 1, origin + 1)]
    predictions = {
        "timesfm": log_width / NORMAL_80_WIDTH,
        "persistence": math.sqrt(sum(r * r for r in returns[origin - HORIZON + 1:origin + 1])),
        "ewma": math.sqrt(HORIZON * variances[origin]),
        "atr": sum(atr_ranges) / len(atr_ranges) / NORMAL_RANGE_MEAN * math.sqrt(HORIZON),
    }
    return {"realized": realized, "predicted": predictions, "features": features}


def compare(rows, predictor, *, symbol="BTCUSDT", completed=None, on_record=None):
    """Daily origins; cached origins resume without another predictor call."""
    completed = completed or {}
    targets = expected_origins(rows)
    observations = []
    for origin_ms, (target_start, target_end) in targets.items():
        index = (origin_ms - rows[0][0]) // HOUR_MS
        observation = completed.get(origin_ms)
        if observation is None:
            observation = _origin_observation(rows, index, predictor)
            if on_record is not None:
                on_record({"symbol": symbol, "origin_ms": origin_ms,
                           "target_start_ms": target_start, "target_end_ms": target_end,
                           "observation": observation})
        observations.append({"symbol": symbol, "origin_ms": origin_ms,
                             "target_start_ms": target_start, "target_end_ms": target_end,
                             **observation})
    mae = {name: sum(abs(row["predicted"][name] - row["realized"])
                     for row in observations) / len(observations)
           for name in PREDICTIONS}
    return {"n": len(observations), "mae": mae, "observations": observations,
            "target": "sqrt(sum of next 24 hourly log returns squared)",
            "units": "24h cumulative log-return volatility",
            "assumptions": "log q90/q10 width divided by Normal 80% width 2.5631; ATR/sqrt(8/pi); no fitted rescaling"}


def paired_week_block_bootstrap(per_symbol, *, seed=BOOTSTRAP_SEED,
                                block=BOOTSTRAP_BLOCK, resamples=BOOTSTRAP_RESAMPLES):
    """Pooled paired MAE deltas, resampling seven-day blocks across all symbols."""
    symbols = tuple(symbol for symbol in SYMBOLS if symbol in per_symbol)
    counts = {per_symbol[symbol]["n"] for symbol in symbols}
    if len(symbols) != len(SYMBOLS) or len(counts) != 1:
        raise ValueError("bootstrap requires all fixed symbols with aligned counts")
    n = counts.pop()
    if n < 14:
        return {"status": "INSUFFICIENT_N", "n_per_symbol": n,
                "minimum_n_per_symbol": 14, "block_days": block,
                "resamples": resamples, "seed": seed}
    by_symbol = {s: per_symbol[s]["observations"] for s in symbols}
    origins = [[row["origin_ms"] for row in by_symbol[s]] for s in symbols]
    if any(values != origins[0] for values in origins[1:]):
        raise ValueError("symbol observation origins must align before pooled bootstrap")
    rng = random.Random(seed)
    deltas = {baseline: [] for baseline in BASELINES}
    for _ in range(resamples):
        indexes = []
        while len(indexes) < n:
            start = rng.randrange(n)
            indexes.extend((start + offset) % n for offset in range(block))
        indexes = indexes[:n]
        for baseline in BASELINES:
            errors = [abs(by_symbol[s][i]["predicted"]["timesfm"] - by_symbol[s][i]["realized"])
                      - abs(by_symbol[s][i]["predicted"][baseline] - by_symbol[s][i]["realized"])
                      for s in symbols for i in indexes]
            deltas[baseline].append(sum(errors) / len(errors))
    summary = {}
    for baseline, values in deltas.items():
        ordered = sorted(values)
        summary[baseline] = {
            "mean_mae_delta_timesfm_minus_baseline": sum(values) / len(values),
            "ci95": [ordered[int(.025 * (resamples - 1))],
                     ordered[int(.975 * (resamples - 1))]],
        }
    return {"status": "COMPLETE", "n_per_symbol": n, "n_symbols": len(symbols),
            "block_days": block, "resamples": resamples, "seed": seed,
            "deltas": summary}


def _source_provenance():
    research = Path(__file__).resolve()
    root = research.parents[4]
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", *SOURCE_FILES],
                           capture_output=True, text=True, check=True).stdout
    if status.strip():
        raise ValueError("research source files are dirty; commit the frozen runner before preregistration")
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    digest = hashlib.sha256()
    for relative in SOURCE_FILES:
        content = (root / relative).read_bytes()
        digest.update(relative.encode("utf-8") + b"\0" + hashlib.sha256(content).digest())
    return {"commit": commit, "source_sha256": digest.hexdigest(),
            "files": list(SOURCE_FILES)}


def _dependency_versions():
    names = ("timesfm", "torch", "numpy", "huggingface-hub", "safetensors")
    direct = {name: importlib.metadata.version(name) for name in names}
    resolved = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            resolved[name.lower().replace("_", "-")] = distribution.version
    return {"direct": direct, "resolved": dict(sorted(resolved.items())),
            "python": sys.version.split()[0], "platform": platform.platform()}


def _plan_document(plan):
    # Commits that change only a local checkpoint must not invalidate exact
    # cached origins. Relevant source bytes still belong to plan identity.
    identity = json.loads(canonical_json(plan))
    if isinstance(identity.get("source"), dict):
        identity["source"].pop("commit", None)
    checksum = sha256_bytes(canonical_json(identity).encode("utf-8"))
    audit_checksum = sha256_bytes(canonical_json(plan).encode("utf-8"))
    return {"plan": plan, "plan_sha256": checksum,
            "audit_sha256": audit_checksum}, checksum


def _persist_plan(output: Path, plan_document: dict):
    if output.exists() and not output.is_dir():
        raise ValueError("output-dir must be a directory")
    output.mkdir(parents=True, exist_ok=True)
    target = output / "preregistered_plan.json"
    encoded = json.dumps(plan_document, sort_keys=True, allow_nan=False, indent=2) + "\n"
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        saved_plan = existing.get("plan")
        if (not isinstance(saved_plan, dict)
                or _plan_document(saved_plan)[0]["audit_sha256"] != existing.get("audit_sha256")
                or _plan_document(saved_plan)[0]["plan_sha256"] != existing.get("plan_sha256")
                or existing.get("plan_sha256") != plan_document["plan_sha256"]):
            raise ValueError("existing output directory belongs to a different frozen plan")
        return existing
    elif any(output.iterdir()):
        raise ValueError("refusing output directory with files but no matching plan")
    else:
        target.write_text(encoded, encoding="utf-8")
    return plan_document


def _acquire_lock(path: Path, plan_hash: str):
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise RuntimeError(f"runner lock exists ({path}); ensure no run is active before clearing it") from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"pid": os.getpid(), "plan_sha256": plan_hash}, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _write_report(path: Path, report: dict):
    encoded = json.dumps(report, sort_keys=True, allow_nan=False, indent=2) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise ValueError("existing report differs from resumed observations; refusing overwrite")
        return
    path.write_text(encoded, encoding="utf-8")


def run_protocol(manifest_inputs, registry_path, cache_dir, output_dir):
    if len(manifest_inputs) != len(SYMBOLS):
        raise ValueError(f"exactly one manifest per predeclared symbol is required: {SYMBOLS}")
    loaded = [load_build_bars(path, digest) for path, digest in manifest_inputs]
    by_symbol = {symbol: rows for symbol, rows, _ in loaded}
    manifests = {symbol: manifest for symbol, _, manifest in loaded}
    if tuple(sorted(by_symbol)) != tuple(sorted(SYMBOLS)):
        raise ValueError(f"exactly one manifest per predeclared symbol is required: {SYMBOLS}")
    expected = {symbol: expected_origins(by_symbol[symbol]) for symbol in SYMBOLS}
    sources = _source_provenance()
    deps = _dependency_versions()
    base_plan = {
        "protocol": "timesfm25_usdt_proxy_vol_2022_v1",
        "period": {"start_inclusive": "2022-01-01T00:00:00Z",
                   "end_exclusive": "2023-01-01T00:00:00Z"},
        "symbols": list(SYMBOLS), "series": {
            symbol: {"source": manifests[symbol]["source"],
                     "manifest_sha256": manifests[symbol]["manifest_sha256"],
                     "source_provenance": manifests[symbol].get("source_provenance"),
                     "series": manifests[symbol]["series"],
                     "origin_count": len(expected[symbol])}
            for symbol in SYMBOLS
        },
        "context": CONTEXT, "horizon": HORIZON, "stride": STRIDE,
        "target": "sqrt(sum of next 24 hourly squared log returns)",
        "forecast_width": "(log(q90/spot)-log(q10/spot))/2.5631031310892007",
        "persistence": "sqrt(previous 24 hourly squared log returns)",
        "ewma": {"alpha": EWMA_ALPHA, "variance_seed": "first actual squared log return",
                 "forecast": "sqrt(24 * current EWMA variance)"},
        "atr": {"window": ATR_WINDOW, "conversion": "mean true range / close / sqrt(8/pi) * sqrt(24)"},
        "model_provenance": {"id": MODEL_ID, "revision": MODEL_REVISION,
                             "weights_sha256": WEIGHTS_SHA256, "device": "cpu",
                             "torch_num_threads": 2, "dependencies": deps},
        "dependencies": deps, "source": sources,
        "bootstrap": {"method": "paired circular 7-day block", "resamples": BOOTSTRAP_RESAMPLES,
                      "seed": BOOTSTRAP_SEED, "min_n_per_symbol": 14},
        "acceptance": "descriptive BUILD only; no L2/L3 acceptance",
        "corpus_overlap": "unknown: pretrained TimesFM corpus may overlap this historical period",
    }
    variant_keys = {}
    with ResearchRegistry(registry_path) as registry:
        for symbol in SYMBOLS:
            variant_config = json.loads(canonical_json({
                **base_plan, "symbol": symbol, "model": MODEL_ID,
            }))
            # Keep an auditable HEAD in the plan, but avoid making a checkpoint-
            # only commit an extra trial or invalidating progress for same bytes.
            variant_config["source"].pop("commit", None)
            variant_keys[symbol] = register_variants(
                registry, manifest_hash=manifests[symbol]["manifest_sha256"],
                config=variant_config)
    plan = {**base_plan, "variant_keys": variant_keys}
    plan_document, plan_hash = _plan_document(plan)
    output = Path(output_dir).resolve()
    plan_document = _persist_plan(output, plan_document)
    plan = plan_document["plan"]
    progress_path = output / PROGRESS_NAME
    completed, tail_hash = read_progress(progress_path, plan_hash=plan_hash, expected=expected)
    lock_path = output / ".runner.lock"
    _acquire_lock(lock_path, plan_hash)
    try:
        completed, tail_hash = read_progress(progress_path, plan_hash=plan_hash, expected=expected)
        missing = any(len(completed[symbol]) != len(expected[symbol]) for symbol in SYMBOLS)
        if missing:
            from .timesfm_model import load_model
            model, loaded_metadata = load_model(cache_dir=cache_dir)
            expected_model = plan["model_provenance"]
            checks = {"model_id": expected_model["id"],
                      "model_revision": expected_model["revision"],
                      "weights_sha256": expected_model["weights_sha256"],
                      "device": expected_model["device"],
                      "torch_num_threads": expected_model["torch_num_threads"],
                      "dependencies": expected_model["dependencies"]["direct"]}
            if any(loaded_metadata.get(key) != value for key, value in checks.items()):
                raise ValueError("loaded model provenance differs from the preregistered plan")
            predictor = __import__(
                "quantos.paper.research.timesfm_experiment", fromlist=["timesfm25_predictor"]
            ).timesfm25_predictor(model)
        else:
            predictor = None

        per_symbol = {}
        for symbol in SYMBOLS:
            def save_record(payload, _symbol=symbol):
                nonlocal tail_hash
                payload = {"plan_sha256": plan_hash, **payload}
                tail_hash = append_progress(progress_path, payload=payload, previous_hash=tail_hash)
                completed[_symbol][payload["origin_ms"]] = payload["observation"]

            per_symbol[symbol] = compare(
                by_symbol[symbol], predictor, symbol=symbol,
                completed=completed[symbol], on_record=save_record,
            )

        pooled_mae = {method: sum(per_symbol[symbol]["mae"][method] for symbol in SYMBOLS) / len(SYMBOLS)
                      for method in PREDICTIONS}
        report = {"plan": plan, "plan_sha256": plan_hash, "per_symbol": per_symbol,
                  "pooled_equal_symbol_mae": pooled_mae,
                  "paired_week_block_bootstrap": paired_week_block_bootstrap(per_symbol),
                  "model": plan["model_provenance"],
                  "status": "DESCRIPTIVE_BUILD_ONLY_NO_PROMOTION"}
        report_path = output / "report.json"
        _write_report(report_path, report)
        with ResearchRegistry(registry_path) as registry:
            for symbol in SYMBOLS:
                registry.record_observation(
                    variant_keys[symbol]["timesfm_width_volatility"],
                    period="BUILD 2022-01..2022-12",
                    stats={"symbol": symbol, "n": per_symbol[symbol]["n"],
                           "mae": per_symbol[symbol]["mae"],
                           "paired_bootstrap": report["paired_week_block_bootstrap"],
                           "status": report["status"]},
                    source_path=report_path,
                )
        return report
    finally:
        lock_path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="append", required=True,
                        help="repeat once per fixed symbol, same order as --manifest-hash")
    parser.add_argument("--manifest-hash", action="append", required=True,
                        help="verified manifest SHA-256; repeat with each --manifest")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--output-dir", required=True,
                        help="new output directory; an exact matching plan resumes progress")
    args = parser.parse_args(argv)
    if len(args.manifest) != len(SYMBOLS) or len(args.manifest_hash) != len(SYMBOLS):
        parser.error("provide exactly three --manifest/--manifest-hash pairs")
    report = run_protocol(list(zip(args.manifest, args.manifest_hash)), args.registry,
                          args.cache_dir, args.output_dir)
    print(json.dumps({"status": report["status"], "mae": report["pooled_equal_symbol_mae"],
                      "bootstrap": report["paired_week_block_bootstrap"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

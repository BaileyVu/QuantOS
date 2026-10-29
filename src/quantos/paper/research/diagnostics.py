"""Training-only, non-selection portfolio diagnostics under the shared simulator."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable

from ..market import parse_exchange_info
from ..risk import ResearchPolicy
from ..types import Candle, Quote, Signal, Snapshot, D
from . import experiment
from .baselines import delay_market_intent_one_hour, matched_random_signals, price_only_signals
from .data import Bar
from .dataset import DatasetIndex, build_index
from .metrics import summarize, max_drawdown
from .protocol import _exclusive_json, fingerprint
from .signals import SignalCache, load_signal_cache, filter_snapshot_hash, expected_signal_parameter_hash
from .simulator import Simulator

TRAIN_START = experiment.TRAIN_START
TRAIN_END = experiment.TRAIN_END
MAJORS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
          "ADAUSDT", "AVAXUSDT", "LINKUSDT")


def _member_key(member: dict) -> tuple:
    return (member["strategy_id"], tuple(sorted(member["parameters"].items())),
            member["risk_fraction"])


def chosen_cells(selection: dict) -> tuple[dict, ...]:
    """Return saved chosen individuals and saved overall cell without retuning."""
    choices = selection.get("individual_choices", {})
    if set(choices) != {"crash_flush_e2", "alt_dip_reversal", "ema_level_2h"}:
        raise ValueError("saved selection does not contain all three chosen individuals")
    rows = [choices[name] for name in sorted(choices)]
    picked = selection.get("selection", {}).get("cell")
    return tuple(rows + ([picked] if picked is not None else []))


def _json_counts(counts: Counter) -> list[dict]:
    """Stable JSON representation for symbol/month strata."""
    return [{"symbol": symbol, "month": month, "count": int(count)}
            for (symbol, month), count in sorted(counts.items())]


def _records_for_cell(cache: SignalCache, cell: dict) -> tuple[Signal, ...]:
    members = cell["members"]
    out = []
    for record in cache.records:
        params = dict(record.parameters)
        if any(record.signal.strategy_id == member["strategy_id"] and
               all(params.get(k) == v for k, v in member["parameters"].items())
               for member in members):
            if TRAIN_START <= record.decision_ms < TRAIN_END:
                out.append(record.signal)
    return tuple(out)


def _stop_variant(cell: dict) -> dict:
    clone = json.loads(json.dumps(cell))
    for member in clone["members"]:
        if member["strategy_id"] in {"crash_flush_e2", "alt_dip_reversal"}:
            member["parameters"]["stop_fraction"] = "0.15"
    return clone


def _sensitivity_signals(cache: SignalCache, cell: dict) -> tuple[Signal, ...]:
    """Use cached 15% siblings for wide-stop members; keep EMA member unchanged."""
    wide = [m for m in cell["members"] if m["strategy_id"] in {"crash_flush_e2", "alt_dip_reversal"}]
    if not wide:
        return ()
    variant = _stop_variant(cell)
    candidates = _records_for_cell(cache, variant)
    keys = {(s.strategy_id, s.symbol, s.observed_ms): s for s in candidates}
    baseline = _records_for_cell(cache, cell)
    expected = {(m["strategy_id"]) for m in wide}
    result = []
    for signal in baseline:
        if signal.strategy_id in expected:
            replacement = keys.get((signal.strategy_id, signal.symbol, signal.observed_ms))
            if replacement is None:
                raise ValueError(f"missing cached 15% sibling for {signal.strategy_id}/{signal.symbol}@{signal.observed_ms}")
            result.append(replacement)
        else:
            result.append(signal)
    return tuple(result)


def _snapshot_for(index: DatasetIndex, symbol: str, instrument, at_ms: int) -> Snapshot | None:
    if instrument is None:
        return None
    row = index.bar_by_open_ms.get(symbol, {}).get(at_ms - 3_600_000)
    if row is None or row.close_ms + 1 != at_ms:
        return None
    # The delay helper consumes only the just-completed close; a one-bar
    # snapshot avoids repeatedly copying the multi-year history per signal.
    candles = (row.to_candle(),)
    close = candles[-1].close
    quote = Quote(symbol, at_ms, at_ms, close, close, D(1), D(1), close)
    return Snapshot(at_ms, instrument, quote, (), (), candles_1h=candles)


def _crash_price_snapshots(index: DatasetIndex, instruments: dict) -> Iterable[Snapshot]:
    """Yield 24-closed-bar causal windows for the nine crash majors."""
    for symbol in MAJORS:
        instrument = instruments.get(symbol)
        if instrument is None or instrument.status != "TRADING":
            continue
        rows = index.bars.get(symbol, ())
        window: list[Bar] = []
        for row in rows:
            window.append(row)
            if len(window) > 25:
                window.pop(0)
            boundary = row.close_ms + 1
            if len(window) == 25 and TRAIN_START <= boundary < TRAIN_END:
                if any(b.open_ms != a.close_ms + 1 for a, b in zip(window, window[1:])):
                    continue
                candles = tuple(b.to_candle() for b in window)
                close = candles[-1].close
                quote = Quote(symbol, boundary, boundary, close, close, D(1), D(1), close)
                yield Snapshot(boundary, instrument, quote, (), (), candles_1h=candles)


def _engine_run(index: DatasetIndex, filters: dict, cell: dict, signals: Iterable[Signal], *,
                slippage: Decimal = D("0.0015")) -> dict:
    signals = tuple(signals)
    risk_map = {m["strategy_id"]: D(m["risk_fraction"]) for m in cell["members"]}
    risk = max(risk_map.values())
    policy = ResearchPolicy(risk_fraction=risk, leverage=D(cell["leverage"]), slippage=slippage)
    instruments = parse_exchange_info(filters, "USDT")
    result = Simulator(index.bar_by_open_ms, index.mark_by_open_ms, index.funding,
        instruments, policy, risk_by_strategy=risk_map).run(signals, start_ms=TRAIN_START, end_ms=TRAIN_END)
    stats = summarize([(t, float(v)) for t, v in result.points], result.trades)
    stats["adverse_bar_stress_drawdown"] = max_drawdown(
        [float(v) for _, v in sorted(result.points + result.adverse_points)])
    return experiment._plain({"status": "COMPLETE", "metrics": stats,
        "signal_count": sum(TRAIN_START <= s.observed_ms < TRAIN_END for s in signals),
        "rejections": dict(result.rejections), "approvals": dict(result.approvals),
        "paused_at_ms": result.paused_at_ms, "trades": result.trades,
        "funding_events": result.funding_events, "points": result.points,
        "adverse_points": result.adverse_points, "assumptions": result.assumptions})


def _base_result_path(selection_dir: Path, cell: dict) -> Path:
    return selection_dir / "cells" / f"{experiment.cell_key(cell)}.json"


def _diagnostic_code_hash() -> str:
    digest = hashlib.sha256()
    for name in ("diagnostics.py", "baselines.py"):
        path = Path(__file__).with_name(name)
        digest.update(name.encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def run_training_diagnostics(index: DatasetIndex, cache: SignalCache, filters_path: str | Path,
                             selection_dir: str | Path, output_dir: str | Path) -> dict:
    """Run/resume frozen training diagnostics only; never modifies selection cells."""
    if tuple(index.window) != ("2021-10-01", "2024-12-31") or index.content_hash != cache.data_hash:
        raise ValueError("diagnostics require matching approved training index and signal cache")
    selection_root, output = Path(selection_dir), Path(output_dir)
    selection = json.loads((selection_root / "selection.json").read_text())
    claim = json.loads((selection_root / "training_claim.json").read_text())
    if selection.get("training_claim_sha256") != fingerprint(claim):
        raise ValueError("saved selection does not match its training claim")
    if claim.get("data_hash") != index.content_hash or claim.get("cache_hash") != cache.source_hash:
        raise ValueError("saved training claim provenance differs from loaded inputs")
    filters_raw = Path(filters_path).read_bytes()
    filters_hash = hashlib.sha256(filters_raw).hexdigest()
    if filters_hash != cache.filter_hash:
        raise ValueError("filter snapshot differs from the archived primary run")
    filters = json.loads(filters_raw)
    cells = chosen_cells(selection)
    jobs = []
    for i, cell in enumerate(cells):
        key = experiment.cell_key(cell)
        base_path = _base_result_path(selection_root, cell)
        if not base_path.is_file():
            raise FileNotFoundError(f"selected base cell missing: {base_path}")
        base = json.loads(base_path.read_text())
        if base.get("cell") != cell or base.get("status") != "COMPLETE":
            raise ValueError(f"selected base cell is not a completed matching result: {key}")
        jobs.append({"id": f"selected_{i}_{key[:12]}", "kind": "reuse_base",
                     "cell": cell, "base_path": base_path.relative_to(selection_root).as_posix(),
                     "base_sha256": hashlib.sha256(base_path.read_bytes()).hexdigest()})
        jobs.extend((
            {"id": f"selected_{i}_{key[:12]}_slippage_2x", "kind": "slippage_2x", "cell": cell},
            {"id": f"selected_{i}_{key[:12]}_delay_1h", "kind": "delay_1h", "cell": cell},
        ))
        if any(m["strategy_id"] in {"crash_flush_e2", "alt_dip_reversal"} for m in cell["members"]):
            jobs.append({"id": f"selected_{i}_{key[:12]}_stop_15pct", "kind": "stop_15pct", "cell": cell})
    crash_cell = selection["individual_choices"]["crash_flush_e2"]
    jobs.extend(({"id": "crash_price_only", "kind": "crash_price_only", "cell": crash_cell},
                 {"id": "crash_matched_random", "kind": "crash_matched_random", "cell": crash_cell,
                  "seed": 28102026}))
    diag_hash = _diagnostic_code_hash()
    runtime_hash = experiment.source_content_hash()
    run_claim = {"schema": 1, "window": ["2022-01-01", "2024-12-31"],
        "training_claim_sha256": fingerprint(claim), "selection_sha256": hashlib.sha256(
            (selection_root / "selection.json").read_bytes()).hexdigest(),
        "archived_training_source_hash": claim["source_content_hash"],
        "diagnostics_code_hash": diag_hash, "simulator_runtime_hash": runtime_hash,
        "data_hash": index.content_hash,
        "cache_hash": cache.source_hash, "filter_hash": filters_hash,
        "overlay_hash": index.overlay_hash, "jobs": jobs,
        "non_selection": True, "heldout_outcomes": False}
    output.mkdir(parents=True, exist_ok=True)
    claim_path = output / "diagnostics_claim.json"
    if claim_path.exists():
        if json.loads(claim_path.read_text()) != run_claim:
            raise ValueError("diagnostics claim differs; use a new output directory")
    else:
        _exclusive_json(claim_path, run_claim)

    instruments = parse_exchange_info(filters, "USDT")
    base_signals: dict[str, tuple[Signal, ...]] = {}
    random_candidates: tuple[Signal, ...] | None = None
    price_cache: tuple[Signal, ...] | None = None
    results = []
    for job in jobs:
        target = output / f"{job['id']}.json"
        if target.exists():
            prior = json.loads(target.read_text())
            if prior.get("job") != job or prior.get("claim_hash") != fingerprint(run_claim):
                raise ValueError(f"diagnostic output identity mismatch: {target}")
            results.append(prior)
            continue
        if experiment.source_content_hash() != runtime_hash:
            raise ValueError("simulator/risk/runtime source changed during diagnostics; stop and use a new claim")
        cell, kind = job["cell"], job["kind"]
        if kind == "reuse_base":
            base_path = selection_root / job["base_path"]
            if hashlib.sha256(base_path.read_bytes()).hexdigest() != job["base_sha256"]:
                raise ValueError(f"saved base cell changed during diagnostics: {job['id']}")
            base = json.loads(base_path.read_text())
            result = {"status": "REUSED", "metrics": base["metrics"], "cell_key": experiment.cell_key(cell)}
        else:
            signals = base_signals.setdefault(experiment.cell_key(cell), _records_for_cell(cache, cell))
            if kind == "slippage_2x":
                result = _engine_run(index, filters, cell, signals, slippage=D("0.003"))
            elif kind == "delay_1h":
                delayed = []
                for signal in signals:
                    snapshot = _snapshot_for(index, signal.symbol, instruments.get(signal.symbol),
                                             signal.observed_ms + 3_600_000)
                    if snapshot is not None:
                        item = delay_market_intent_one_hour(signal, snapshot)
                        if item is not None:
                            delayed.append(item.delayed_decision)
                result = _engine_run(index, filters, cell, tuple(delayed))
                result["delayed_intent_count"] = len(delayed)
                result["omitted_delay_count"] = len(signals) - len(delayed)
            elif kind == "stop_15pct":
                result = _engine_run(index, filters, cell, _sensitivity_signals(cache, cell))
            elif kind in {"crash_price_only", "crash_matched_random"}:
                holding = int(crash_cell["members"][0]["parameters"]["holding_hours"])
                if price_cache is None:
                    price_cache = price_only_signals(_crash_price_snapshots(index, instruments), holding_hours=holding)
                if kind == "crash_price_only":
                    selected_signals = price_cache
                else:
                    actual = _records_for_cell(cache, crash_cell)
                    counts = Counter((s.symbol, datetime.fromtimestamp(s.observed_ms / 1000, timezone.utc).strftime("%Y-%m"))
                                     for s in actual)
                    if random_candidates is None:
                        random_candidates = matched_random_signals(
                            _crash_price_snapshots(index, instruments), counts, holding_hours=holding, seed=job["seed"])
                    selected_signals = random_candidates
                result = _engine_run(index, filters, cell, selected_signals)
                result["baseline_signal_count"] = len(selected_signals)
                result["matched_counts"] = _json_counts(counts) if kind == "crash_matched_random" else None
                result["seed"] = job.get("seed")
            else:
                raise ValueError(f"unsupported diagnostic job kind: {kind}")
        row = {"job": job, "claim_hash": fingerprint(run_claim), "result": result}
        _exclusive_json(target, row)
        results.append(row)
        print(json.dumps({"event": "DIAGNOSTIC_COMPLETE", "job": job["id"]}), flush=True)
    return {"claim": run_claim, "results": results}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="data/portfolio/manifests/train_2021-10-01_to_2024-12-31.json")
    parser.add_argument("--overlay", default="data/portfolio/manifests/daily_repairs_2021-10-01_to_2024-12-31.json")
    parser.add_argument("--filters", default="data/portfolio/filters/current_usdt_proxy_20261029.json")
    parser.add_argument("--cache", default="data/portfolio/signals/train_2022-01-01_to_2024-12-31_trusted_71fad258_v2.json.gz")
    parser.add_argument("--selection", default="data/portfolio/training/primary_20261029_v1")
    parser.add_argument("--output", default="data/portfolio/diagnostics/train_20261029_v1")
    args = parser.parse_args(argv)
    print(json.dumps({"event": "DIAGNOSTICS_STARTED", "pid": os.getpid()}), flush=True)
    index = build_index(args.manifest, overlay_manifest=args.overlay)
    cache = load_signal_cache(args.cache, expected_data_hash=index.content_hash,
        expected_filter_hash=filter_snapshot_hash(args.filters),
        expected_parameter_hash=expected_signal_parameter_hash(index, filter_snapshot_path=args.filters),
        filter_snapshot_path=args.filters)
    run_training_diagnostics(index, cache, args.filters, args.selection, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

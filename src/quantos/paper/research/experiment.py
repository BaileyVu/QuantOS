"""Resumable training selection; this module cannot execute a 2025+ cell.

The complete individual grid is fixed before outcomes. Combined cells use the
training-selected individual settings, not settings selected on held-out data.
"""
from __future__ import annotations

from itertools import combinations
import json
from pathlib import Path
from typing import Iterable

from ..market import parse_exchange_info
from ..risk import ResearchPolicy
from ..types import D, dumps
from .metrics import summarize, kelly_fraction, max_drawdown
from .protocol import _exclusive_json, fingerprint
from .simulator import Simulator, IncompleteHistory
from .signals import validate_cache_provenance, expected_signal_parameter_hash

TRAIN_START = 1_640_995_200_000  # 2022-01-01 UTC
TRAIN_END = 1_735_689_600_000    # 2025-01-01 UTC
WIDE_RISKS = ("0.01", "0.02", "0.04", "0.06", "0.08")
NARROW_RISKS = ("0.005", "0.01", "0.02", "0.03")


def individual_grid() -> list[dict]:
    cells = []
    variants = []
    for hold in (24, 48, 72):
        variants.append(("crash_flush_e2", {"holding_hours": str(hold), "stop_fraction": "0.25"}, WIDE_RISKS))
    for quantile in ("0.005", "0.01"):
        for hold in (12, 24, 72):
            variants.append(("alt_dip_reversal", {"cutoff_quantile": quantile,
                            "holding_hours": str(hold), "stop_fraction": "0.25"}, WIDE_RISKS))
    variants.append(("ema_level_2h", {}, NARROW_RISKS))
    for strategy, parameters, risks in variants:
        for risk in risks:
            for leverage in ("1", "2", "3"):
                cells.append({"members": [{"strategy_id": strategy, "parameters": parameters,
                                           "risk_fraction": risk}], "leverage": leverage,
                              "role": "individual_primary"})
    return cells


def _plain(value):
    return json.loads(dumps(value))


def cell_key(cell): return fingerprint(cell)


def _signals(cache, cell):
    result = []
    for record in cache.records:
        for member in cell["members"]:
            if record.signal.strategy_id != member["strategy_id"]: continue
            parameters = dict(record.parameters)
            if all(parameters.get(k) == v for k, v in member["parameters"].items()):
                result.append(record.signal)
                break
    return result


def run_cell(index, cache, filters: dict, cell: dict, *, start_ms=TRAIN_START,
             end_ms=TRAIN_END) -> dict:
    if not TRAIN_START <= start_ms < end_ms <= TRAIN_END:
        raise ValueError("only training outcomes permitted")
    if tuple(index.window) != ("2021-10-01", "2024-12-31"):
        raise ValueError("only training data index permitted")
    if index.content_hash != cache.data_hash: raise ValueError("cache data provenance mismatch")
    members = cell["members"]
    names = [m["strategy_id"] for m in members]
    if not names or len(set(names)) != len(names): raise ValueError("one setting per strategy required")
    risk_map = {m["strategy_id"]: D(m["risk_fraction"]) for m in members}
    risk = max(risk_map.values())
    policy = ResearchPolicy(risk_fraction=risk, leverage=D(cell["leverage"]))
    instruments = parse_exchange_info(filters, "USDT")
    signals = _signals(cache, cell)
    try:
        result = Simulator(index.bar_by_open_ms, index.mark_by_open_ms, index.funding,
                           instruments, policy, risk_by_strategy=risk_map).run(
                               signals, start_ms=start_ms, end_ms=end_ms)
    except IncompleteHistory as exc:
        return {"cell": cell, "status": "INVALID_DATA", "reason": str(exc),
                "metrics": None, "kelly": None}
    stats = summarize([(t, float(v)) for t, v in result.points], result.trades)
    # This stress path is not a synchronized observed MTM series.
    adverse = [float(v) for _, v in sorted(result.points + result.adverse_points)]
    stats["adverse_bar_stress_drawdown"] = max_drawdown(adverse)
    kelly = kelly_fraction([float(t["net_r"]) for t in result.trades])
    return _plain({"cell": cell, "status": "COMPLETE", "metrics": stats, "kelly": kelly,
        "signal_count": sum(start_ms <= s.observed_ms < end_ms for s in signals),
        "rejections": dict(result.rejections), "approvals": dict(result.approvals),
        "paused_at_ms": result.paused_at_ms, "assumptions": result.assumptions,
        "points": result.points, "adverse_points": result.adverse_points,
        "trades": result.trades, "funding_events": result.funding_events})


def eligible(result: dict) -> bool:
    if result.get("status") != "COMPLETE": return False
    metrics, kelly = result["metrics"], result["kelly"]
    half = kelly.get("half_fraction")
    return (metrics["net_return"] > 0 and metrics["max_drawdown"] < .50
            and metrics.get("adverse_bar_stress_drawdown", metrics["max_drawdown"]) < .50
            and half is not None and half > 0
            and all(float(m["risk_fraction"]) <= half for m in result["cell"]["members"]))


def rank(result: dict):
    """Max training CAGR, then smaller DD/risk/leverage, then canonical ID."""
    return (-result["metrics"]["cagr"], result["metrics"]["max_drawdown"],
            max(float(m["risk_fraction"]) for m in result["cell"]["members"]),
            float(result["cell"]["leverage"]), cell_key(result["cell"]))


def select_individuals(results: Iterable[dict]) -> dict[str, dict]:
    """Eligible choice when available; otherwise diagnostic best, never advice."""
    by_name = {}
    for result in results:
        if result.get("status") != "COMPLETE" or len(result["cell"]["members"]) != 1: continue
        name = result["cell"]["members"][0]["strategy_id"]
        by_name.setdefault(name, []).append(result)
    return {name: min([r for r in rows if eligible(r)] or rows, key=rank)
            for name, rows in by_name.items()}


def combined_grid(individuals: dict[str, dict]) -> list[dict]:
    """At most four subsets of 2–3 strategies, each at ceilings 1/2/3.

    Per-strategy holds/quantiles/risks are selected on training first. The common
    leverage ceiling is swept again; member risk fractions remain fixed.
    """
    cells = []
    names = sorted(individuals)
    for size in range(2, len(names)+1):
        for subset in combinations(names, size):
            members = [individuals[n]["cell"]["members"][0] for n in subset]
            for leverage in ("1", "2", "3"):
                cells.append({"members": members, "leverage": leverage,
                              "role": "combined_training_selected_parameters"})
    return cells


def recommendation(results: Iterable[dict], individuals: dict[str, dict]) -> dict:
    candidates = []
    for result in results:
        if not eligible(result): continue
        if len(result["cell"]["members"]) > 1 and any(
                not eligible(individuals[m["strategy_id"]]) for m in result["cell"]["members"]):
            continue
        candidates.append(result)
    if not candidates:
        return {"status": "NO_ELIGIBLE_TRAINING_PORTFOLIO", "cell": None,
                "reason": "No positive training cell passed drawdown and half-Kelly constraints; cash is the frozen candidate."}
    winner = min(candidates, key=rank)
    return {"status": "TRAINING_PICK_NOT_YET_EVALUATED", "cell": winner["cell"],
            "cell_key": cell_key(winner["cell"]), "training_metrics": winner["metrics"],
            "training_kelly": winner["kelly"]}


def source_content_hash() -> str:
    import hashlib
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def run_training(index, cache, filter_path: Path, directory: Path, *, code_hash: str | None = None, progress=None) -> dict:
    """Resume exact cells only when all causal data/code/filter inputs match.

    This stores full accounting evidence locally. It never seals a recommendation
    for OOS automatically; the main-agent review and separate protocol seal remain
    required after diagnostics, baselines and requested sensitivities are ready.
    """
    import hashlib
    raw = filter_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != cache.filter_hash:
        raise ValueError("cache filter provenance mismatch")
    validate_cache_provenance(cache, data_hash=index.content_hash,
        filter_hash=hashlib.sha256(raw).hexdigest(), parameter_hash=expected_signal_parameter_hash(index))
    actual_hash = source_content_hash()
    if code_hash is not None and code_hash != actual_hash: raise ValueError("source content hash mismatch")
    code_hash = actual_hash
    filters = json.loads(raw)
    grid = individual_grid()
    claim = {"schema": 1, "training_start": TRAIN_START, "training_end_exclusive": TRAIN_END,
             "data_hash": index.content_hash, "cache_hash": cache.source_hash,
             "filters_hash": cache.filter_hash, "source_content_hash": code_hash,
             "individual_grid": grid, "selection_rule": "max_CAGR_then_min_DD_risk_leverage_ID",
             "constraints": "positive net, MTM and adverse-bar stress DD<50%, risk<=half empirical training Kelly; mixed members also individually eligible",
             "combined_rule": "training-selected individual settings; subsets of2/3; ceilings1/2/3",
             "cash_alternative": True}
    path = directory / "training_claim.json"
    if path.exists():
        if json.loads(path.read_text()) != claim: raise ValueError("training provenance changed; do not reuse cells")
    else:
        _exclusive_json(path, claim)
    def execute(cell):
        target = directory / "cells" / (cell_key(cell) + ".json")
        if target.exists():
            result = json.loads(target.read_text())
            if result["cell"] != cell: raise ValueError("cell identity mismatch")
            return result
        result = run_cell(index, cache, filters, cell)
        _exclusive_json(target, result)
        if progress is not None: progress({"cell": cell_key(cell), "status": result["status"]})
        return result
    results = [execute(cell) for cell in grid]
    individuals = select_individuals(results)
    combined = combined_grid(individuals)
    # Derived solely from completed training cells; persisted before their outcomes.
    combo_path = directory / "combined_grid.json"
    if combo_path.exists():
        if json.loads(combo_path.read_text()) != combined: raise ValueError("derived grid changed")
    else:
        # Exclusive writer accepts any JSON-compatible payload despite its annotation.
        _exclusive_json(combo_path, combined)
    results.extend(execute(cell) for cell in combined)
    selected = recommendation(results, individuals)
    summary = {"selection": selected, "individual_choices": {n: r["cell"] for n,r in individuals.items()},
               "completed_cells": sum(r["status"] == "COMPLETE" for r in results),
               "invalid_cells": sum(r["status"] != "COMPLETE" for r in results),
               "evaluation_grid": grid+combined, "training_claim_sha256": fingerprint(claim)}
    summary_path = directory / "selection.json"
    if summary_path.exists():
        if json.loads(summary_path.read_text()) != summary: raise ValueError("selection changed")
    else: _exclusive_json(summary_path, summary)
    return summary


def main(argv=None):
    import argparse
    import os
    import time
    from .dataset import build_index
    from .signals import load_signal_cache, filter_snapshot_hash
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="data/portfolio/manifests/train_2021-10-01_to_2024-12-31.json")
    parser.add_argument("--overlay", default="data/portfolio/manifests/daily_repairs_2021-10-01_to_2024-12-31.json")
    parser.add_argument("--filters", default="data/portfolio/filters/current_usdt_proxy_20261029.json")
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--wait-cache-seconds", type=int, default=0)
    args = parser.parse_args(argv)
    if not 0 <= args.wait_cache_seconds <= 7200: parser.error("cache wait must be0..7200seconds")
    code = source_content_hash()
    print(json.dumps({"event": "TRAIN_PROCESS_STARTED", "pid": os.getpid(), "source_hash": code}), flush=True)
    deadline = time.monotonic()+args.wait_cache_seconds
    while not Path(args.cache).is_file():
        if time.monotonic() >= deadline: raise FileNotFoundError(args.cache)
        time.sleep(5)
    # Fail rather than silently resume against code edited during the wait.
    if code != source_content_hash(): raise ValueError("source changed while waiting for cache")
    index = build_index(args.manifest, overlay_manifest=args.overlay)
    cache = load_signal_cache(args.cache, expected_data_hash=index.content_hash,
        expected_filter_hash=filter_snapshot_hash(args.filters),
        expected_parameter_hash=expected_signal_parameter_hash(index), filter_snapshot_path=args.filters)
    print(json.dumps({"event": "TRAIN_INPUTS_VALIDATED", "signals": len(cache.records),
                     "data_hash": index.content_hash}), flush=True)
    summary = run_training(index, cache, Path(args.filters), Path(args.output), code_hash=code,
        progress=lambda row: print(json.dumps({"event": "TRAIN_CELL_COMPLETE", **row}), flush=True))
    print(json.dumps({"event": "TRAIN_FINISHED", "completed": summary["completed_cells"],
        "invalid": summary["invalid_cells"], "selection_status": summary["selection"]["status"],
        "output": args.output}), flush=True)
    return 0


if __name__ == "__main__": raise SystemExit(main())

"""One immutable held-out wave; never chooses replacement settings from outcomes."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from ..market import parse_exchange_info
from ..risk import ResearchPolicy
from ..types import D, Quote, Snapshot
from . import experiment
from .baselines import delay_market_intent_one_hour, price_only_signals, matched_random_signals
from .diagnostics import _snapshot_for, _stop_variant, _json_counts, MAJORS
from .evaluation_signals import EVALUATION_START_MS as START, EVALUATION_END_MS as END
from .metrics import summarize, max_drawdown, drawdown_monte_carlo, DAY_MS
from .protocol import _exclusive_json, fingerprint
from .signals import _records_hash, _source_hash, _strategy_source_hash, filter_snapshot_hash
from .evaluation_signals import _generator_source_hash, evaluation_parameter_hash
from .signals import ALT_CUTOFF_QUANTILES, ALT_HOLDING_HOURS
from .simulator import Simulator, IncompleteHistory


def wave_jobs(selection):
    """Freeze primary exploratory grid and all diagnostics before held-out access."""
    jobs = [{"cohort": "primary", "kind": "base", "cell": c}
            for c in selection["evaluation_grid"]]
    chosen = list(selection["individual_choices"].values())
    pick = selection["pick"].get("cell")
    if pick is not None:
        chosen.append(pick)
    seen = set()
    for cell in chosen:
        key = fingerprint(cell)
        if key in seen:
            continue
        seen.add(key)
        for kind in ("slippage_2x", "delay_1h"):
            jobs.append({"cohort": "primary", "kind": kind, "cell": cell})
        if any(m["strategy_id"] in {"alt_dip_reversal", "crash_flush_e2"} for m in cell["members"]):
            jobs.append({"cohort": "primary", "kind": "stop_15pct", "cell": cell})
        jobs.append({"cohort": "verified_volume", "kind": "base", "cell": cell})
    crash = selection["individual_choices"]["crash_flush_e2"]
    for kind in ("crash_price_only", "crash_matched_random"):
        jobs.append({"cohort": "primary", "kind": kind, "cell": crash, "seed": 28102026})
    if len({fingerprint(j) for j in jobs}) != len(jobs):
        raise ValueError("duplicate frozen evaluation job")
    return jobs


def cache_signals(cache, cell):
    return tuple(s for s in experiment._signals(cache, cell) if START <= s.observed_ms < END)


def price_snapshots(index, instruments):
    for symbol in MAJORS:
        instrument = instruments.get(symbol)
        if instrument is None:
            raise ValueError(f"missing baseline filter: {symbol}")
        rows = index.bars.get(symbol, ())
        for i in range(24, len(rows)):
            window = rows[i-24:i+1]
            boundary = window[-1].close_ms + 1
            if not START <= boundary < END:
                continue
            if any(b.open_ms != a.close_ms + 1 for a, b in zip(window, window[1:])):
                continue
            candles = tuple(b.to_candle() for b in window)
            close = candles[-1].close
            yield Snapshot(boundary, instrument,
                Quote(symbol, boundary, boundary, close, close, D(1), D(1), close),
                (), (), candles_1h=candles)


def monte_carlo(result):
    points = [(int(t), float(v)) for t, v in result["points"] if t % DAY_MS == 0]
    daily = [b[1]/a[1]-1 for a, b in zip(points, points[1:]) if a[1] > 0]
    return drawdown_monte_carlo([float(t["account_return"]) for t in result["trades"]], daily,
                               draws=1000, block_days=7, seed=28102026)


def run_job(index, cache, filters, job):
    cell, kind = job["cell"], job["kind"]
    instruments = parse_exchange_info(filters, "USDT")
    signals = cache_signals(cache, cell)
    notes = {}
    if kind == "stop_15pct":
        candidates = cache_signals(cache, _stop_variant(cell))
        lookup = {(s.strategy_id, s.symbol, s.observed_ms): s for s in candidates}
        if len(lookup) != len(candidates):
            raise ValueError("duplicate stop-sensitivity sibling")
        signals = tuple(lookup[(s.strategy_id, s.symbol, s.observed_ms)] for s in signals)
    elif kind == "delay_1h":
        delayed = []
        for signal in signals:
            snapshot = _snapshot_for(index, signal.symbol, instruments.get(signal.symbol),
                                     signal.observed_ms + 3_600_000)
            intent = delay_market_intent_one_hour(signal, snapshot) if snapshot else None
            if intent and intent.delayed_decision.observed_ms < END:
                delayed.append(intent.delayed_decision)
        notes = {"original_signals": len(signals), "omitted_delayed_intents": len(signals)-len(delayed)}
        signals = tuple(delayed)
    elif kind in {"crash_price_only", "crash_matched_random"}:
        hold = int(cell["members"][0]["parameters"]["holding_hours"])
        opportunities = price_snapshots(index, instruments)
        if kind == "crash_price_only":
            signals = price_only_signals(opportunities, holding_hours=hold)
        else:
            counts = Counter((s.symbol, datetime.fromtimestamp(s.observed_ms/1000,
                timezone.utc).strftime("%Y-%m")) for s in signals)
            signals = matched_random_signals(opportunities, counts, holding_hours=hold, seed=job["seed"])
            notes = {"matched_counts": _json_counts(counts), "seed": job["seed"]}
    elif kind not in {"base", "slippage_2x"}:
        raise ValueError("unregistered diagnostic kind")
    risks = {m["strategy_id"]: D(m["risk_fraction"]) for m in cell["members"]}
    policy = ResearchPolicy(risk_fraction=max(risks.values()), leverage=D(cell["leverage"]),
                            slippage=D("0.003") if kind == "slippage_2x" else D("0.0015"))
    try:
        result = Simulator(index.bar_by_open_ms, index.mark_by_open_ms, index.funding,
            instruments, policy, risk_by_strategy=risks).run(signals, start_ms=START, end_ms=END)
    except IncompleteHistory as exc:
        return {"status": "INVALID_DATA", "reason": str(exc), "metrics": None}
    stats = summarize([(t, float(v)) for t, v in result.points], result.trades)
    stats["adverse_bar_stress_drawdown"] = max_drawdown(
        [float(v) for _, v in sorted(result.points + result.adverse_points)])
    return experiment._plain({"status": "COMPLETE", "metrics": stats,
        "signal_count": len(signals), "rejections": dict(result.rejections),
        "approvals": dict(result.approvals), "paused_at_ms": result.paused_at_ms,
        "trades": result.trades, "funding_events": result.funding_events,
        "points": result.points, "adverse_points": result.adverse_points,
        "assumptions": result.assumptions, "diagnostic": notes})


def validate_inputs(sealed, indexes, caches, filter_paths):
    if fingerprint(sealed["payload"]) != sealed["sha256"]:
        raise ValueError("modified seal")
    if experiment.source_content_hash() != sealed["payload"]["code_sha"]:
        raise ValueError("evaluation source differs from pre-outcome seal")
    for cohort in ("primary", "verified_volume"):
        index, cache = indexes[cohort], caches[cohort]
        symbols = sealed["payload"]["selection"][cohort + "_symbols"]
        if tuple(index.window) != ("2024-10-01", "2026-10-28") or set(index.bars) != set(symbols):
            raise ValueError("wrong evaluation index scope")
        if cache.training_window != (START, END) or cache.data_hash != index.content_hash:
            raise ValueError("cache has wrong data/window")
        expected = evaluation_parameter_hash(sealed, cohort, symbols,
                    tuple(sorted(ALT_CUTOFF_QUANTILES)), tuple(sorted(ALT_HOLDING_HOURS)))
        if (cache.parameter_hash != expected or cache.filter_hash != filter_snapshot_hash(filter_paths[cohort])
                or cache.strategy_hash != _strategy_source_hash()
                or cache.generator_hash != _generator_source_hash()
                or cache.records_hash != _records_hash(cache.records)
                or cache.source_hash != _source_hash(cache.data_hash, cache.filter_hash,
                    cache.strategy_hash, cache.generator_hash, cache.parameter_hash)
                or any(not START <= r.decision_ms < END for r in cache.records)):
            raise ValueError("stale or modified evaluation cache")
        if any(a.gaps or a.duplicates for a in index.audit if a.dataset in {"klines", "markPriceKlines"}):
            raise ValueError("price/mark gaps require source repair or explicit invalid-data report")
        expected_filter = sealed["payload"]["plan"]["filter_hashes"][cohort]
        if cache.filter_hash != expected_filter:
            raise ValueError("filter proxy changed after seal")


def run_wave(sealed, indexes, caches, filter_paths, output):
    """Resume exact saved jobs only. All completed cells are checksum-verified."""
    validate_inputs(sealed, indexes, caches, filter_paths)
    jobs = sealed["payload"]["plan"]["evaluation_jobs"]
    if jobs != wave_jobs(sealed["payload"]["selection"]):
        raise ValueError("frozen jobs differ from preregistered grid/diagnostics")
    output = Path(output)
    claim = {"schema": 1, "seal_sha256": sealed["sha256"],
        "data": {k: v.content_hash for k, v in indexes.items()},
        "caches": {k: v.source_hash for k, v in caches.items()},
        "code_sha": sealed["payload"]["code_sha"], "jobs_sha256": fingerprint(jobs)}
    claim_path = output / "evaluation_claim.json"
    if claim_path.exists():
        if json.loads(claim_path.read_text()) != claim:
            raise ValueError("another evaluation wave already claimed this directory")
    else:
        _exclusive_json(claim_path, claim)
    filters = {k: json.loads(Path(v).read_text()) for k, v in filter_paths.items()}
    rows = []
    for job in jobs:
        path = output / "cells" / (fingerprint(job) + ".json")
        if path.exists():
            row = json.loads(path.read_text())
            if row["sha256"] != fingerprint(row["payload"]) or row["payload"]["job"] != job or row["payload"]["claim"] != fingerprint(claim):
                raise ValueError("modified evaluation result")
        else:
            if experiment.source_content_hash() != claim["code_sha"]:
                raise ValueError("source changed during evaluation; no further outcomes permitted")
            cohort = job["cohort"]
            result = run_job(indexes[cohort], caches[cohort], filters[cohort], job)
            payload = {"job": job, "claim": fingerprint(claim), "result": result}
            row = {"payload": payload, "sha256": fingerprint(payload)}
            _exclusive_json(path, row)
            print(json.dumps({"event": "EVALUATION_CELL_COMPLETE", "key": fingerprint(job),
                              "status": result["status"]}), flush=True)
        rows.append(row["payload"])
    selection = sealed["payload"]["selection"]
    pick = selection["pick"].get("cell")
    primary = [r for r in rows if r["job"]["cohort"] == "primary" and r["job"]["kind"] == "base"]
    eligible = [r for r in primary if r["result"]["status"] == "COMPLETE"]
    best = min(eligible, key=lambda r: (-r["result"]["metrics"]["cagr"], fingerprint(r["job"]))) if eligible else None
    frozen = next((r for r in primary if r["job"]["cell"] == pick), None)
    report = {"claim": fingerprint(claim), "completed_jobs": len(rows),
        "frozen_pick": pick, "frozen_pick_result_key": fingerprint(frozen["job"]) if frozen else None,
        "cash_selected": pick is None,
        "exploratory_best_key": fingerprint(best["job"]) if best else None,
        "exploratory_label": "best held-out sweep cell; not an unbiased recommendation",
        "invalid_jobs": [fingerprint(r["job"]) for r in rows if r["result"]["status"] != "COMPLETE"],
        "frozen_pick_monte_carlo": monte_carlo(frozen["result"]) if frozen and frozen["result"]["status"] == "COMPLETE" else None}
    summary = output / "summary.json"
    if summary.exists():
        if json.loads(summary.read_text()) != report:
            raise ValueError("evaluation summary changed")
    else:
        _exclusive_json(summary, report)
    return report

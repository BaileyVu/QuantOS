"""Offline reach/baseline and SENTIMENT_SHADOW forward-return report.

Consumes only precollected reach JSONL, a read-only QuantOS journal, and the
runtime's recorded ``market/*.jsonl.gz`` quote stream. It performs no network
access or state mutation. Returns use the last received quote at/before the
signal time and the first received quote at/after the requested horizon.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import gzip
import json
import math
import random
from pathlib import Path
import sqlite3
import sys
from typing import Iterable

from .sentibot_reach_shadow import ASSETS

HORIZONS_MS = {"4h": 4 * 60 * 60 * 1000, "24h": 24 * 60 * 60 * 1000}
DEFAULT_GATED_STRATEGIES = frozenset({"tsmom_4h", "turtle_55", "vol_squeeze_breakout"})


def load_reach_jsonl(path: str | Path) -> list[dict]:
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                raise ValueError(f"invalid reach JSONL at line {line_number}") from None
            if not isinstance(row, dict) or not isinstance(row.get("t"), (int, float)):
                raise ValueError(f"invalid reach row at line {line_number}")
            rows.append(row)
    return rows


def load_journal_events(path: str | Path) -> list[tuple[int, str, dict]]:
    """Read journal events using SQLite read-only mode."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    try:
        rows = db.execute("SELECT time_ms,kind,payload FROM events ORDER BY time_ms,id").fetchall()
    finally:
        db.close()
    return [(int(at), str(kind), json.loads(payload)) for at, kind, payload in rows]


def load_market_quotes(market_dir: str | Path,
                       symbols: set[str] | None = None) -> dict[str, list[tuple[int, float]]]:
    """Extract received-time marks from runtime-compressed quote-feed journals."""
    path = Path(market_dir)
    files = sorted(path.glob("*.jsonl.gz")) if path.is_dir() else [path]
    quotes: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for file_path in files:
        with gzip.open(file_path, "rt", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    raise ValueError(f"invalid market log {file_path.name}:{line_number}") from None
                if row.get("kind") != "feed":
                    continue
                received_ms = row.get("received_ms")
                for item in row.get("payload", ()):
                    if not isinstance(item, (list, tuple)) or len(item) != 2 or item[0] != "quote":
                        continue
                    quote = item[1]
                    if not isinstance(quote, dict):
                        continue
                    symbol = quote.get("symbol")
                    if not isinstance(symbol, str) or (symbols is not None and symbol not in symbols):
                        continue
                    at = quote.get("received_ms", received_ms)
                    mark = quote.get("mark")
                    try:
                        at, mark = max(int(at), int(received_ms)), float(mark)
                        mark_at = int(quote.get("mark_time_ms", at))
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if at >= 0 and 0 <= at - mark_at <= 60_000 and math.isfinite(mark) and mark > 0:
                        quotes[symbol].append((at, mark))
    for series in quotes.values():
        series.sort(key=lambda pair: pair[0])
    return dict(quotes)


def _as_score(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and -1 <= number <= 1 else None


def _asset_quote_symbol(asset: str) -> str:
    return asset[:-4] + "USDC" if asset.endswith("USDT") else asset


def _forward_return(quotes: dict[str, list[tuple[int, float]]], symbol: str,
                    at_ms: int, horizon_ms: int, max_entry_age_ms: int) -> tuple[float | None, str | None]:
    series = quotes.get(symbol, ())
    if not series:
        return None, "no_symbol_quotes"
    times = [row[0] for row in series]
    entry_index = bisect_right(times, at_ms) - 1
    if entry_index < 0:
        return None, "no_entry_quote"
    entry_time, entry_mark = series[entry_index]
    if at_ms - entry_time > max_entry_age_ms:
        return None, "stale_entry_quote"
    target_ms = at_ms + horizon_ms
    exit_index = bisect_left(times, target_ms)
    if exit_index >= len(series):
        return None, "no_forward_quote"
    if series[exit_index][0] - target_ms > max_entry_age_ms:
        return None, "late_forward_quote"
    exit_mark = series[exit_index][1]
    return (exit_mark / entry_mark - 1) * 10000, None


def _pearson(values: list[tuple[float, float]]) -> float | None:
    if len(values) < 2:
        return None
    xs, ys = [x for x, _ in values], [y for _, y in values]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in values) / math.sqrt(vx * vy)


def _score_summary(pairs: list[tuple[float, float]], *, total: int,
                   missing: Counter) -> dict:
    directional = [(1 if score > 0 else -1, ret) for score, ret in pairs if score != 0]
    signed = [direction * ret for direction, ret in directional]
    return {
        "n": len(pairs),
        "potential": total,
        "coverage": len(pairs) / total if total else 0.0,
        "mean_score": sum(score for score, _ in pairs) / len(pairs) if pairs else None,
        "mean_forward_return_bps": sum(ret for _, ret in pairs) / len(pairs) if pairs else None,
        "score_return_pearson": _pearson(pairs),
        "directional_n": len(signed),
        "mean_directional_return_bps": sum(signed) / len(signed) if signed else None,
        "directional_hit_rate": sum(value > 0 for value in signed) / len(signed) if signed else None,
        "missing": dict(sorted(missing.items())),
    }


def _candidate_map(events: Iterable[tuple[int, str, dict]]) -> dict[str, tuple[int, dict]]:
    candidates = {}
    for at_ms, kind, payload in events:
        if kind == "CANDIDATE" and isinstance(payload, dict):
            signal_id = payload.get("signal_id")
            if isinstance(signal_id, str):
                candidates.setdefault(signal_id, (at_ms, payload))
    return candidates


def _gate_observations(events: list[tuple[int, str, dict]],
                       candidates: dict[str, tuple[int, dict]],
                       gated_strategies: set[str]) -> tuple[list[tuple[int, str, str, str]], dict]:
    observations, missing, seen = [], Counter(), set()
    for at_ms, kind, verdict in events:
        if kind != "SENTIMENT_SHADOW" or not isinstance(verdict, dict):
            continue
        signal_id = verdict.get("signal_id")
        if signal_id in seen:
            missing["duplicate_signal_verdict"] += 1
            continue
        candidate = candidates.get(signal_id) if isinstance(signal_id, str) else None
        if candidate is None:
            missing["candidate_not_found"] += 1
            continue
        candidate_ms, signal = candidate
        # A verdict cannot predate its candidate. Measure only once the verdict
        # is actually available, not at an earlier candidate or collection start.
        if at_ms < candidate_ms:
            missing["verdict_precedes_candidate"] += 1
            continue
        strategy = signal.get("strategy_id")
        if strategy not in gated_strategies:
            missing["not_in_gate_strategy_set"] += 1
            continue
        symbol, side = signal.get("symbol"), signal.get("side")
        label = verdict.get("verdict")
        if not isinstance(symbol, str) or side not in {"LONG", "SHORT"}:
            missing["candidate_missing_symbol_or_side"] += 1
            continue
        if label not in {"WOULD_BLOCK", "WOULD_ALLOW", "UNAVAILABLE", "NO_SCORE"}:
            missing["unknown_verdict"] += 1
            continue
        seen.add(signal_id)
        observations.append((at_ms, symbol, side, label))
    return observations, dict(missing)


def analyze(reach_rows: list[dict], journal_events: list[tuple[int, str, dict]],
            quotes: dict[str, list[tuple[int, float]]], *,
            gated_strategies: set[str] | None = None,
            max_entry_age_ms: int = 60_000,
            minimum_observations: int = 50) -> dict:
    """Produce descriptive returns and coverage flags; no statistical promotion."""
    if max_entry_age_ms < 0 or minimum_observations < 1:
        raise ValueError("invalid coverage thresholds")
    gated = set(gated_strategies or DEFAULT_GATED_STRATEGIES)
    candidates = _candidate_map(journal_events)
    gate_obs, gate_join_missing = _gate_observations(journal_events, candidates, gated)
    report = {"schema": 1, "scope": "offline descriptive only",
              "returns": "gross mark returns, not tradable P&L or evidence of gate profitability",
              "gate_sampling": "first observed verdict per signal, anchored at verdict availability",
              "ci_method": "seeded iid percentile bootstrap; overlapping observations are dependent",
              "gated_strategies": sorted(gated), "minimum_observations": minimum_observations,
              "reach_rows": len(reach_rows), "journal_sentiment_events": sum(k == "SENTIMENT_SHADOW" for _, k, _ in journal_events),
              "acceptance": "UNVERIFIED: requires three-day observation and gap review",
              "quote_symbols": sorted(quotes), "horizons": {}}

    for horizon_name, horizon_ms in HORIZONS_MS.items():
        score_pairs = {"reach": [], "baseline": []}
        score_missing = {"reach": Counter(), "baseline": Counter()}
        score_total = 0
        for row in reach_rows:
            try:
                at_ms = int(float(row.get("available_at", row["t"])) * 1000)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            for asset in sorted({k for source in ("reach", "baseline_news")
                                 for k in (row.get(source) or {}) if not k.startswith("_")}):
                reach_score = _as_score((row.get("reach") or {}).get(asset))
                baseline_score = _as_score((row.get("baseline_news") or {}).get(asset))
                if (row.get("reach") or {}).get("_n") == 0: reach_score = None
                if (row.get("baseline_news") or {}).get("_n") == 0: baseline_score = None
                if reach_score is None and baseline_score is None:
                    continue
                score_total += 1
                symbol = _asset_quote_symbol(asset)
                forward, reason = (_forward_return(quotes, symbol, at_ms, horizon_ms, max_entry_age_ms)
                                   if "available_at" in row else (None, "score_availability_unverified"))
                for source, score in (("reach", reach_score), ("baseline", baseline_score)):
                    if score is None:
                        score_missing[source]["score_unavailable"] += 1
                    elif reason:
                        score_missing[source][reason] += 1
                    else:
                        score_pairs[source].append((score, forward))
        score_summaries = {source: _score_summary(score_pairs[source], total=score_total,
                                                   missing=score_missing[source])
                           for source in ("reach", "baseline")}
        for source, summary in score_summaries.items():
            summary["status"] = ("OK" if summary["n"] >= minimum_observations
                                 else "INSUFFICIENT_COVERAGE")

        gate_stats = {"WOULD_BLOCK": [], "WOULD_ALLOW": [], "UNAVAILABLE": [], "NO_SCORE": []}
        gate_missing = Counter(gate_join_missing)
        gate_eligible = 0
        for at_ms, symbol, side, verdict in gate_obs:
            gate_eligible += 1
            ret, reason = _forward_return(quotes, symbol, at_ms, horizon_ms, max_entry_age_ms)
            if reason:
                gate_missing[reason] += 1
                continue
            signed_return = ret if side == "LONG" else -ret
            gate_stats[verdict].append(signed_return)
        gate_summary = {}
        for label, returns in gate_stats.items():
            gate_summary[label] = {
                "n": len(returns), "mean_directional_return_bps": sum(returns) / len(returns) if returns else None,
                "hit_rate": sum(x > 0 for x in returns) / len(returns) if returns else None,
                "ci90_mean_bps": _ci90(returns),
            }
        blocked, allowed = gate_stats["WOULD_BLOCK"], gate_stats["WOULD_ALLOW"]
        gate_summary["blocked_minus_allowed_mean_bps"] = (
            sum(blocked) / len(blocked) - sum(allowed) / len(allowed) if blocked and allowed else None)
        gate_summary["status"] = ("OK" if min(len(blocked), len(allowed)) >= minimum_observations
                                  else "INSUFFICIENT_COVERAGE")
        gate_summary["potential_gated_events"] = gate_eligible
        gate_summary["missing"] = dict(sorted(gate_missing.items()))
        report["horizons"][horizon_name] = {
            "score_comparison": score_summaries,
            "sentiment_gate": gate_summary,
        }
    return report


def _ci90(values):
    if len(values) < 2: return None
    rng = random.Random(0)
    means = sorted(sum(rng.choices(values, k=len(values))) / len(values) for _ in range(2000))
    return [means[99], means[1899]]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reach", required=True, help="precollected reach_shadow.jsonl")
    parser.add_argument("--journal", required=True, help="read-only profile journal SQLite file")
    parser.add_argument("--market-dir", required=True, help="runtime state/market directory of .jsonl.gz quotes")
    parser.add_argument("--output", required=True, help="separate report JSON path")
    parser.add_argument("--gated-strategy", action="append", default=None,
                        help="gated strategy id (repeatable; defaults to current Demo200Policy gate)")
    parser.add_argument("--minimum-observations", type=int, default=50)
    args = parser.parse_args(argv)
    reach_rows = load_reach_jsonl(args.reach)
    journal_events = load_journal_events(args.journal)
    needed = {_asset_quote_symbol(asset) for asset in ASSETS}
    needed.update(_asset_quote_symbol(asset) for row in reach_rows for source in ("reach", "baseline_news")
                  for asset in (row.get(source) or {}) if not asset.startswith("_"))
    for _at, kind, payload in journal_events:
        if kind == "CANDIDATE" and isinstance(payload, dict) and isinstance(payload.get("symbol"), str):
            needed.add(payload["symbol"])
    quotes = load_market_quotes(args.market_dir, needed)
    report = analyze(reach_rows, journal_events, quotes,
                     gated_strategies=set(args.gated_strategy) if args.gated_strategy else None,
                     minimum_observations=args.minimum_observations)
    target = Path(args.output)
    if target.name == "paper_perp_signals.jsonl" or target.exists() or target.is_symlink():
        raise ValueError("report output must be a new file, never a baseline signal file")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(target), "status": {
        horizon: {"reach": values["score_comparison"]["reach"]["status"],
                  "baseline": values["score_comparison"]["baseline"]["status"],
                  "gate": values["sentiment_gate"]["status"]}
        for horizon, values in report["horizons"].items()}}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

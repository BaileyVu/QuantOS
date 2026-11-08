"""Preregistered BUILD-only, non-overlapping 24h TimesFM volatility comparison.

No strategy/risk changes, training, HELD evaluation, or promotion occur here.
Input: manifested JSONL of completed hourly {time_ms, high, low, close} bars.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path

from .registry import ResearchRegistry, verify_dataset_manifest
from .timesfm_experiment import (
    HOUR_MS, assert_period, features_at,
    register_variants, timesfm25_predictor,
)

HORIZON = 24
CONTEXT = 512
NORMAL_80_WIDTH = 2.5631031310892007
NORMAL_RANGE_MEAN = math.sqrt(8 / math.pi)


def load_build_bars(manifest_path, expected_hash):
    path = Path(manifest_path).resolve()
    # Check range before hashing or opening dataset bytes. HELD is deliberately
    # unavailable here until the full comparison protocol is frozen.
    metadata = json.loads(path.read_text())
    window = metadata["range"]
    if not (date(2020, 1, 1) <= date.fromisoformat(window["start"]) <=
            date.fromisoformat(window["end"]) < date(2023, 7, 1)):
        raise ValueError("volatility comparison accepts BUILD-only manifests")
    manifest = verify_dataset_manifest(path, expected_hash=expected_hash)
    if manifest["missing_ranges"]:
        raise ValueError("gapped dataset is not eligible for this comparison")
    rows = []
    with (path.parent / manifest["dataset"]["path"]).open() as source:
        for line in source:
            item = json.loads(line)
            ts = item["time_ms"]
            if not isinstance(ts, int) or isinstance(ts, bool) or ts % HOUR_MS:
                raise ValueError("timestamps must be completed UTC hourly boundaries")
            assert_period(ts, "BUILD")
            high, low, close = (float(item[k]) for k in ("high", "low", "close"))
            if not all(math.isfinite(x) and x > 0 for x in (high, low, close)) or not low <= close <= high:
                raise ValueError("invalid positive OHLC")
            if rows and ts - rows[-1][0] != HOUR_MS:
                raise ValueError("hourly bars must be strictly contiguous")
            day = datetime.fromtimestamp(ts / 1000, timezone.utc).date()
            if not date.fromisoformat(window["start"]) <= day <= date.fromisoformat(window["end"]):
                raise ValueError("bar lies outside declared manifest range")
            rows.append((ts, high, low, close))
    if len(rows) < CONTEXT + HORIZON:
        raise ValueError("at least 536 contiguous hourly bars are required")
    return rows, manifest


def compare(rows, predictor):
    """Daily origins; targets never overlap and no target is passed to predictor."""
    closes = [row[3] for row in rows]
    returns = [0.0] + [math.log(b / a) for a, b in zip(closes, closes[1:])]
    variances = [0.0] * len(rows)
    for i in range(1, len(rows)):
        variances[i] = returns[i] ** 2 if i == 1 else .06 * returns[i] ** 2 + .94 * variances[i - 1]
    observations = []
    for origin in range(CONTEXT - 1, len(rows) - HORIZON, HORIZON):
        context = [(row[0], row[3]) for row in rows[origin - CONTEXT + 1:origin + 1]]
        forecast = predictor(context)
        features = features_at(forecast, completed_close_ms=rows[origin][0],
                               completed_close=closes[origin], prior_forecast=None)
        realized = math.sqrt(sum(r * r for r in returns[origin + 1:origin + HORIZON + 1]))
        ranges = [max(rows[i][1] - rows[i][2], abs(rows[i][1] - closes[i - 1]),
                      abs(rows[i][2] - closes[i - 1])) / closes[i]
                  for i in range(origin - 13, origin + 1)]
        predictions = {
            "timesfm": features["width_24h"] / NORMAL_80_WIDTH,
            "persistence": math.sqrt(sum(r * r for r in returns[origin - HORIZON + 1:origin + 1])),
            "ewma": math.sqrt(HORIZON * variances[origin]),
            "atr": sum(ranges) / len(ranges) / NORMAL_RANGE_MEAN * math.sqrt(HORIZON),
        }
        observations.append({"origin_ms": rows[origin][0],
                             "target_end_ms": rows[origin + HORIZON][0],
                             "realized": realized, "predicted": predictions,
                             "features": features})
    mae = {name: sum(abs(row["predicted"][name] - row["realized"]) for row in observations) / len(observations)
           for name in ("timesfm", "persistence", "ewma", "atr")}
    return {"n": len(observations), "mae": mae, "observations": observations,
            "beats_persistence_and_ewma": mae["timesfm"] < min(mae["persistence"], mae["ewma"]),
            "acceptance": "UNVERIFIED: descriptive BUILD comparison only; no L2/L3 acceptance",
            "target": "sqrt(sum of next 24 hourly log returns squared)",
            "assumptions": "Gaussian 80% width/2.563; ATR/sqrt(8/pi); no fitted rescaling; non-overlapping daily targets",
            "costs": "forecast-error metric only; no tradable returns or cost-model change",
            "pretraining_overlap": "UNVERIFIED: foundation-model corpus may include historical evaluation targets"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--manifest-hash", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    rows, manifest = load_build_bars(args.manifest, args.manifest_hash)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=False)
    plan = {"period": "BUILD", "context": CONTEXT, "horizon": HORIZON, "stride": HORIZON,
            "manifest_hash": manifest["manifest_sha256"], "ewma_alpha": .06,
            "target": "24h realized quadratic variation", "normal_80_width": NORMAL_80_WIDTH,
            "normal_range_mean": NORMAL_RANGE_MEAN, "atr_window": 14,
            "acceptance": "descriptive only; paired uncertainty and L2/L3 still required"}
    with ResearchRegistry(args.registry) as registry:
        plan["variants"] = register_variants(registry, manifest_hash=manifest["manifest_sha256"], config=plan)
    (output / "preregistered_plan.json").write_text(json.dumps(plan, sort_keys=True, indent=2) + "\n")
    from .timesfm_model import load_model
    model, provenance = load_model(cache_dir=args.cache_dir)
    report = compare(rows, timesfm25_predictor(model))
    report["plan"] = plan
    report["model"] = provenance
    (output / "report.json").write_text(json.dumps(report, sort_keys=True, allow_nan=False, indent=2) + "\n")
    print(json.dumps({"n": report["n"], "mae": report["mae"], "acceptance": report["acceptance"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

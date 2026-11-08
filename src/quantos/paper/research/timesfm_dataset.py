"""Export the fixed 2022 BTC/ETH/SOL BUILD pilot from verified USDT archives.

This is a forecast comparison, never historical USDC execution evidence.
The fixed selection is declared before inspecting any forecast outcomes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .dataset import build_index, persist_audit
from .registry import canonical_json, create_dataset_manifest, sha256_bytes, sha256_file

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
START_MS = 1_640_995_200_000  # 2022-01-01 00:00 UTC, completed close boundary
END_MS = 1_672_531_200_000    # 2023-01-01 00:00 UTC, exclusive
HOUR_MS = 3_600_000
BASE_SHA = "e3464f95f66e438c42c78c30187dd639da74014d25fce6b22c60bfe2ac551810"
OVERLAY_SHA = "8fbb13eadcfa148c6479d444b7a2a3edf14811a35f3a53fb5afaa3e2c85cee9f"


def export_index(index, output_dir, provenance):
    """Validate all three full series before creating any export files."""
    if index.selection_bounds != ("2021-12-31", "2023-01-01"):
        raise ValueError("index must use the preregistered BUILD selection")
    series = {}
    expected = list(range(START_MS, END_MS, HOUR_MS))
    for symbol in SYMBOLS:
        bars = [bar for bar in index.bars.get(symbol, ())
                if START_MS <= bar.close_exclusive_ms < END_MS]
        if [bar.close_exclusive_ms for bar in bars] != expected:
            raise ValueError(f"{symbol}: exact completed-hour 2022 coverage is required")
        if any(bar.symbol != symbol or bar.interval != "1h" for bar in bars):
            raise ValueError("mixed-series export rejected")
        series[symbol] = bars
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    persist_audit(index, output / "source_index_audit.json")
    summary = {"scope": "fixed BUILD pilot; USDT proxy only", "symbols": list(SYMBOLS),
               "start_ms": START_MS, "end_exclusive_ms": END_MS,
               "source": provenance, "index_manifest_hash": index.manifest_hash, "datasets": {}}
    for symbol, bars in series.items():
        data = output / f"{symbol}.jsonl"
        with data.open("x") as handle:
            for bar in bars:
                handle.write(canonical_json({"symbol": symbol, "time_ms": bar.close_exclusive_ms,
                    "high": str(bar.high), "low": str(bar.low), "close": str(bar.close)}) + "\n")
        manifest_path = output / f"{symbol}.manifest.json"
        manifest = create_dataset_manifest(data, manifest_path,
            source="Binance USD-M USDT proxy archives; fixed three-asset BUILD pilot",
            start="2022-01-01", end="2022-12-31", missing_ranges=[])
        manifest.pop("manifest_sha256")
        manifest["series"] = {"symbol": symbol, "market": "Binance USD-M USDT proxy",
            "timeframe": "1h", "timestamp_semantics": "completed_close_exclusive_ms"}
        manifest["source_provenance"] = {**provenance, "index_manifest_hash": index.manifest_hash,
            "selection_bounds": list(index.selection_bounds), "rows": len(bars),
            "first_close_ms": bars[0].close_exclusive_ms, "last_close_ms": bars[-1].close_exclusive_ms}
        # This newly created manifest is enriched before publication or any run.
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
        summary["datasets"][symbol] = {"manifest": str(manifest_path.resolve()),
            "manifest_hash": sha256_bytes(canonical_json(manifest).encode()), "rows": len(bars)}
    (output / "export.json").write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--overlay", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    # Metadata checks before any archive is opened; corpus identity is fixed.
    if sha256_file(args.manifest) != BASE_SHA or sha256_file(args.overlay) != OVERLAY_SHA:
        raise ValueError("source metadata differs from preregistered corpus")
    if Path(args.output_dir).exists():
        raise FileExistsError("export output must be a new directory")
    index = build_index(args.manifest, overlay_manifest=args.overlay, symbols=SYMBOLS,
                        start_date="2021-12-31", end_exclusive="2023-01-01")
    result = export_index(index, args.output_dir, {"base_manifest_sha256": BASE_SHA,
                          "overlay_manifest_sha256": OVERLAY_SHA,
                          "exporter_sha256": sha256_file(__file__)})
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

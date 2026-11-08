"""Export fixed C21-intersection BUILD OHLCV for TimesFM ml1h research.

The export is a historical USDT proxy feature dataset, not USDC-M execution
evidence. The period and 16-symbol intersection are fixed before archive reads.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .dataset import build_index, persist_audit
from .registry import canonical_json, create_dataset_manifest, sha256_bytes, sha256_file

SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
    "LINKUSDT", "AVAXUSDT", "LTCUSDT", "BCHUSDT", "FILUSDT", "ADAUSDT",
    "ETCUSDT", "TRXUSDT", "XLMUSDT", "ATOMUSDT",
)
START_MS = 1_633_046_400_000  # 2021-10-01 00:00 UTC source open, inclusive
END_MS = 1_688_169_600_000    # 2023-07-01 00:00 UTC, exclusive close boundary
HOUR_MS = 3_600_000
EXPECTED_ROWS = (END_MS - START_MS) // HOUR_MS - 1  # close_ms starts at START_MS + 1h
SELECTION_BOUNDS = ("2021-10-01", "2023-07-01")
BASE_SHA = "e3464f95f66e438c42c78c30187dd639da74014d25fce6b22c60bfe2ac551810"
OVERLAY_SHA = "8fbb13eadcfa148c6479d444b7a2a3edf14811a35f3a53fb5afaa3e2c85cee9f"
MARKET = "Binance USD-M USDT proxy"
FIELDS = ("time_ms", "open_ms", "open", "high", "low", "close", "volume")


def export_index(index, output_dir, provenance):
    """Validate the complete fixed universe before writing any export files."""
    if index.selection_bounds != SELECTION_BOUNDS:
        raise ValueError("index must use the preregistered BUILD selection")
    if set(index.bars) != set(SYMBOLS):
        raise ValueError("index must contain exactly the fixed 16-symbol intersection")
    expected = tuple(range(START_MS + HOUR_MS, END_MS, HOUR_MS))
    if len(expected) != EXPECTED_ROWS:
        raise RuntimeError("fixed BUILD coverage arithmetic is inconsistent")
    checked = {}
    for symbol in SYMBOLS:
        bars = index.bars[symbol]
        if len(bars) != EXPECTED_ROWS:
            raise ValueError(f"{symbol}: exact full BUILD hourly coverage is required")
        close_times = tuple(bar.close_exclusive_ms for bar in bars)
        if close_times != expected:
            raise ValueError(f"{symbol}: exact full BUILD hourly coverage is required")
        previous_open = None
        for bar in bars:
            if bar.symbol != symbol or bar.interval != "1h":
                raise ValueError("mixed-series export rejected")
            if bar.open_ms != bar.close_exclusive_ms - HOUR_MS:
                raise ValueError(f"{symbol}: open and completed-close timestamps disagree")
            if previous_open is not None and bar.open_ms - previous_open != HOUR_MS:
                raise ValueError(f"{symbol}: source open timestamps are not contiguous")
            previous_open = bar.open_ms
            if any(value is None for value in (bar.open, bar.high, bar.low, bar.close, bar.volume)):
                raise ValueError(f"{symbol}: incomplete OHLCV row")
            if not (bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high and bar.volume >= 0):
                raise ValueError(f"{symbol}: invalid OHLCV row")
        checked[symbol] = bars

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    persist_audit(index, output / "source_index_audit.json")
    summary = {
        "scope": "fixed BUILD TimesFM ml1h OHLCV feature input; USDT proxy only",
        "symbols": list(SYMBOLS), "start_open_ms": START_MS,
        "first_completed_close_ms": START_MS + HOUR_MS,
        "end_exclusive_close_ms": END_MS, "rows_per_symbol": EXPECTED_ROWS,
        "source": provenance, "index_manifest_hash": index.manifest_hash,
        "datasets": {},
    }
    for symbol in SYMBOLS:
        bars = checked[symbol]
        data = output / f"{symbol}.jsonl"
        with data.open("x", encoding="utf-8") as handle:
            for bar in bars:
                row = {"symbol": symbol, "time_ms": bar.close_exclusive_ms,
                       "open_ms": bar.open_ms, "open": str(bar.open),
                       "high": str(bar.high), "low": str(bar.low),
                       "close": str(bar.close), "volume": str(bar.volume)}
                handle.write(canonical_json(row) + "\n")
        manifest_path = output / f"{symbol}.manifest.json"
        manifest = create_dataset_manifest(
            data, manifest_path, source="Binance USD-M USDT proxy archives; fixed C21 intersection BUILD OHLCV",
            start="2021-10-01", end="2023-06-30", missing_ranges=[],
            series={"symbol": symbol, "market": MARKET, "timeframe": "1h",
                    "timestamp_semantics": "completed_close_exclusive_ms"})
        manifest.pop("manifest_sha256")
        manifest["data_fields"] = list(FIELDS)
        manifest["source_provenance"] = {
            **provenance, "index_manifest_hash": index.manifest_hash,
            "selection_bounds": list(index.selection_bounds), "rows": len(bars),
            "first_open_ms": bars[0].open_ms,
            "first_completed_close_ms": bars[0].close_exclusive_ms,
            "last_completed_close_ms": bars[-1].close_exclusive_ms,
        }
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        summary["datasets"][symbol] = {
            "manifest": str(manifest_path.resolve()),
            "manifest_hash": sha256_bytes(canonical_json(manifest).encode()),
            "rows": len(bars),
        }
    (output / "export.json").write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--overlay", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    # Verify only the two declared metadata files before build_index opens data.
    if sha256_file(args.manifest) != BASE_SHA or sha256_file(args.overlay) != OVERLAY_SHA:
        raise ValueError("source metadata differs from the fixed C21 BUILD corpus")
    if Path(args.output_dir).exists():
        raise FileExistsError("export output must be a new directory")
    index = build_index(args.manifest, overlay_manifest=args.overlay, symbols=SYMBOLS,
                        start_date=SELECTION_BOUNDS[0], end_exclusive=SELECTION_BOUNDS[1])
    result = export_index(index, args.output_dir, {
        "base_manifest_sha256": BASE_SHA, "overlay_manifest_sha256": OVERLAY_SHA,
        "exporter_sha256": sha256_file(__file__),
    })
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

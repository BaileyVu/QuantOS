from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from quantos.paper.research import ml1h_dataset as dataset
from quantos.paper.research.registry import verify_dataset_manifest


class Ml1hDatasetTest(unittest.TestCase):
    def fake_index(self):
        bars = {}
        start = dataset.START_MS + dataset.HOUR_MS
        for symbol in dataset.SYMBOLS:
            bars[symbol] = [SimpleNamespace(
                symbol=symbol, interval="1h", open_ms=ts - dataset.HOUR_MS,
                close_exclusive_ms=ts, open=Decimal("100"), high=Decimal("102"),
                low=Decimal("99"), close=Decimal("101"), volume=Decimal("3.5"))
                for ts in range(start, dataset.END_MS, dataset.HOUR_MS)]
        return SimpleNamespace(selection_bounds=dataset.SELECTION_BOUNDS,
                               manifest_hash="a" * 64, bars=bars, audit=(),
                               window=dataset.SELECTION_BOUNDS, content_hash="b" * 64,
                               overlay_manifest="overlay.json", overlay_hash="c" * 64,
                               overlay_rows=0, unavailable_partitions=())

    def test_fixed_intersection_and_boundary_contract(self):
        self.assertEqual(len(dataset.SYMBOLS), 16)
        self.assertEqual(len(set(dataset.SYMBOLS)), 16)
        self.assertEqual(dataset.EXPECTED_ROWS, 15_311)
        self.assertEqual(dataset.START_MS + dataset.HOUR_MS, 1_633_050_000_000)
        self.assertEqual(dataset.END_MS - dataset.HOUR_MS, 1_688_166_000_000)

    def test_missing_asset_or_hour_refuses_before_output_creation(self):
        index = self.fake_index()
        index.bars[dataset.SYMBOLS[-1]].pop()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out"
            with self.assertRaisesRegex(ValueError, "exact full BUILD"):
                dataset.export_index(index, output, {})
            self.assertFalse(output.exists())
        index = self.fake_index()
        index.bars.pop(dataset.SYMBOLS[-1])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "exactly the fixed 16"):
                dataset.export_index(index, Path(tmp) / "out", {})

    def test_successful_export_preserves_open_and_completed_close(self):
        index = self.fake_index()
        # Exercise serialization cheaply with the same fixed-contract checker.
        short_start = dataset.START_MS + dataset.HOUR_MS
        with patch.object(dataset, "EXPECTED_ROWS", 2), \
             patch.object(dataset, "START_MS", short_start - dataset.HOUR_MS), \
             patch.object(dataset, "END_MS", short_start + 2 * dataset.HOUR_MS), \
             patch.object(dataset, "SYMBOLS", ("BTCUSDT",)), \
             patch.object(dataset, "persist_audit"):
            index.selection_bounds = dataset.SELECTION_BOUNDS
            index.bars = {"BTCUSDT": index.bars["BTCUSDT"][:2]}
            # Reset test rows to the temporarily narrowed fixed date range.
            for i, bar in enumerate(index.bars["BTCUSDT"]):
                bar.open_ms = short_start - dataset.HOUR_MS + i * dataset.HOUR_MS
                bar.close_exclusive_ms = bar.open_ms + dataset.HOUR_MS
            with tempfile.TemporaryDirectory() as tmp:
                result = dataset.export_index(index, Path(tmp) / "export", {"base": "fixed"})
                entry = result["datasets"]["BTCUSDT"]
                manifest = verify_dataset_manifest(entry["manifest"], expected_hash=entry["manifest_hash"])
                self.assertEqual(manifest["data_fields"], list(dataset.FIELDS))
                row = __import__("json").loads(Path(entry["manifest"]).parent.joinpath("BTCUSDT.jsonl").read_text().splitlines()[0])
                self.assertEqual(row["time_ms"], row["open_ms"] + dataset.HOUR_MS)
                self.assertEqual(row["open"], "100")
                self.assertEqual(row["volume"], "3.5")

    def test_cli_checks_hashes_before_index_and_passes_strict_cutoff(self):
        with patch.object(dataset, "sha256_file", side_effect=[dataset.BASE_SHA, dataset.OVERLAY_SHA, "exporter"]) as sha, \
             patch.object(dataset, "build_index") as build, \
             patch.object(dataset, "export_index", return_value={"ok": True}) as export:
            self.assertEqual(dataset.main(["--manifest", "base", "--overlay", "overlay",
                                           "--output-dir", "/tmp/nonexistent-ml1h-export"]), 0)
            self.assertEqual(sha.call_count, 3)
            build.assert_called_once_with("base", overlay_manifest="overlay", symbols=dataset.SYMBOLS,
                                          start_date="2021-10-01", end_exclusive="2023-07-01")
            self.assertEqual(export.call_args.args[2]["base_manifest_sha256"], dataset.BASE_SHA)


if __name__ == "__main__":
    unittest.main()

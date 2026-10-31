from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from quantos.paper.research.acquire import (
    build_plan, download_batch, iter_cached_archives, load_universe, save_plan,
)
from quantos.paper.research.data import ArchiveRef


class ListingStore:
    def list_archives(self, dataset, symbol, interval, start, end, *, granularity):
        period = "2022-01" if granularity == "monthly" else "2022-01-01"
        return (ArchiveRef(dataset, symbol, period,
                           f"https://data.binance.vision/{dataset}/{symbol}/{period}.zip",
                           interval, 100),)


class PortfolioAcquireTests(unittest.TestCase):
    def test_reads_exact_literal_universe_and_hashes_source_without_import(self):
        content = 'UNIVERSE = ("BTCUSDT ETHUSDT").split()\n'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.py"
            path.write_text(content)
            symbols, digest = load_universe(path)
        self.assertEqual(symbols, ("BTCUSDT", "ETHUSDT"))
        self.assertEqual(digest, hashlib.sha256(content.encode()).hexdigest())

    def test_plan_records_missing_months_and_preserves_training_scope(self):
        source = 'UNIVERSE = ("BTCUSDT ETHUSDT").split()\n'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.py"
            path.write_text(source)
            plan = build_plan(path, date(2022, 1, 1), date(2022, 2, 28),
                              store=ListingStore(), audit_workers=2)
        self.assertEqual(plan["universe"]["symbols"], ["BTCUSDT", "ETHUSDT"])
        self.assertEqual(plan["universe"]["sha256"], hashlib.sha256(source.encode()).hexdigest())
        self.assertEqual(plan["summary"]["by_dataset"]["klines"]["available"], 2)
        self.assertEqual(plan["summary"]["by_dataset"]["klines"]["missing"], 2)
        self.assertEqual(plan["summary"]["by_dataset"]["metrics"]["tasks"], 9 * 59)
        self.assertIn("Spot", plan["universe"]["selection_note"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            saved = save_plan(path, plan)
            self.assertEqual(json.loads(path.read_text())["summary"], saved["summary"])

    def test_plan_rejects_any_out_of_sample_end_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "universe.py"
            path.write_text('UNIVERSE = ("BTCUSDT").split()\n')
            with self.assertRaisesRegex(ValueError, "training window"):
                build_plan(path, date(2021, 10, 1), date(2025, 1, 1), store=ListingStore())

    def test_download_batch_is_bounded_and_keeps_manifest_resumable(self):
        class FakeStore:
            def __init__(self, root): self.root = root; self.calls = []
            def fetch(self, dataset, symbol, period, interval):
                self.calls.append((dataset, symbol, period, interval))
                self.root.mkdir(parents=True, exist_ok=True)
                path = self.root / f"{symbol}-{dataset}-{period}.zip"
                path.write_bytes(b"verified-test-archive")
                return path
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            tasks = []
            for i in range(3):
                tasks.append({"id": str(i), "dataset": "klines", "symbol": "BTCUSDT",
                              "interval": "1h", "period": f"2022-0{i+1}", "status": "available",
                              "url": "https://data.binance.vision/example", "expected_bytes": 10})
            manifest = {"tasks": tasks, "summary": {}, "listing_errors": [], "window": {},
                        "universe": {"symbols": ["BTCUSDT"], "sha256": "x"}}
            manifest_path.write_text(json.dumps(manifest))
            store = FakeStore(Path(tmp) / "cache")
            result = download_batch(manifest_path, limit=2, workers=2, store=store)
            self.assertEqual(len(store.calls), 2)
            self.assertEqual(result["summary"]["downloaded"], 2)
            self.assertEqual(len(list(iter_cached_archives(manifest_path, dataset="klines"))), 2)
            again = download_batch(manifest_path, limit=2, workers=2, store=store)
            self.assertEqual(len(store.calls), 3)
            self.assertEqual(again["summary"]["downloaded"], 3)
            self.assertEqual(len(list(iter_cached_archives(manifest_path, dataset="klines"))), 3)


if __name__ == "__main__":
    unittest.main()

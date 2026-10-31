from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from quantos.paper.research.dataset import build_index, persist_audit


def archive(name: str, csv: str) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, csv)
    return stream.getvalue()


class PortfolioDatasetTests(unittest.TestCase):
    def test_loads_sorted_bars_and_reports_gaps_with_exact_timestamp_lookup(self):
        header = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
        base = 1633046400000  # 2021-10-01 UTC
        csv = header + f"{base},10,12,9,11,3,{base+3599999},31,2,1,10,0\n"
        later = base + 2 * 3600000
        csv += f"{later},11,12,10,11,0,{later+3599999},0,0,0,0,0\n"
        blob = archive("BTCUSDT-1h-2021-10.csv", csv)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "bar.zip"
            path.write_bytes(blob)
            digest = hashlib.sha256(blob).hexdigest()
            manifest = {"window": {"start": "2021-10-01", "end": "2024-12-31"},
                        "universe": {"symbols": ["BTCUSDT"]},
                        "tasks": [{"id": "klines:BTCUSDT:1h:2021-10", "dataset": "klines",
                                   "symbol": "BTCUSDT", "interval": "1h", "status": "downloaded",
                                   "cached_path": str(path), "sha256": digest}]}
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            index = build_index(manifest_path)
            self.assertEqual(len(index.bars["BTCUSDT"]), 2)
            self.assertEqual(index.bar_by_open_ms["BTCUSDT"][base].close_exclusive_ms,
                             base + 3600000)
            kline_audit = next(x for x in index.audit if x.dataset == "klines")
            self.assertEqual(kline_audit.gaps, ((base, later),))
            audit_path = root / "audit.json"
            persist_audit(index, audit_path)
            saved = json.loads(audit_path.read_text())
            self.assertEqual(saved["window"], ["2021-10-01", "2024-12-31"])
            self.assertEqual(saved["content_hash"], index.content_hash)

    def test_rejects_manifest_outside_training_window_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps({"window": {"start": "2021-10-01", "end": "2025-01-01"},
                                        "universe": {"symbols": []}, "tasks": []}))
            with self.assertRaisesRegex(ValueError, "training window"):
                build_index(path)

    def test_checksum_mismatch_fails_closed(self):
        header = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
        blob = archive("x.csv", header)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bar.zip"
            path.write_bytes(blob)
            manifest = {"window": {"start": "2021-10-01", "end": "2024-12-31"},
                        "universe": {"symbols": ["BTCUSDT"]},
                        "tasks": [{"id": "x", "dataset": "klines", "symbol": "BTCUSDT",
                                   "interval": "1h", "status": "downloaded", "cached_path": str(path),
                                   "sha256": "0" * 64}]}
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "checksum"):
                build_index(manifest_path)

    def test_rows_outside_manifest_window_fail_before_indexing(self):
        header = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
        opened = 1632960000000  # 2021-09-30 UTC
        blob = archive("x.csv", header +
                       f"{opened},10,12,9,11,3,{opened+3599999},31,2,1,10,0\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bar.zip"
            path.write_bytes(blob)
            manifest = {"window": {"start": "2021-10-01", "end": "2024-12-31"},
                        "universe": {"symbols": ["BTCUSDT"]},
                        "tasks": [{"id": "x", "dataset": "klines", "symbol": "BTCUSDT",
                                   "interval": "1h", "status": "downloaded", "cached_path": str(path),
                                   "sha256": hashlib.sha256(blob).hexdigest()}]}
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "outside declared manifest window"):
                build_index(manifest_path, training_only=False)

    def test_pending_archive_status_contributes_to_content_hash_and_evidence(self):
        base = {"window": {"start": "2021-10-01", "end": "2024-12-31"},
                "universe": {"symbols": ["BTCUSDT"]}, "tasks": []}
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.json"
            empty.write_text(json.dumps(base))
            task = {"id": "klines:BTCUSDT:1h:2021-10", "dataset": "klines",
                    "symbol": "BTCUSDT", "interval": "1h", "period": "2021-10",
                    "status": "available"}
            pending = Path(tmp) / "pending.json"
            pending.write_text(json.dumps({**base, "tasks": [task]}))
            a = build_index(empty)
            b = build_index(pending)
        self.assertNotEqual(a.content_hash, b.content_hash)
        self.assertEqual(b.unavailable_partitions[0]["status"], "available")


if __name__ == "__main__":
    unittest.main()

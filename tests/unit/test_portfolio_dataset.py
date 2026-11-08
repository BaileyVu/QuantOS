from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from quantos.paper.research.acquire import iter_cached_archives
from quantos.paper.research.dataset import build_index, manifest_content_hash, persist_audit


def archive(name: str, csv: str) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, csv)
    return stream.getvalue()


class PortfolioDatasetTests(unittest.TestCase):
    def test_full_manifest_hash_binds_source_metadata_beyond_partition_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            manifest = {"window": {"start": "2021-10-01", "end": "2024-12-31"},
                        "universe": {"symbols": []}, "tasks": [], "source": "fixture one"}
            path.write_text(json.dumps(manifest))
            first = build_index(path)
            manifest["source"] = "fixture two"
            path.write_text(json.dumps(manifest))
            second = build_index(path)
            self.assertEqual(first.content_hash, second.content_hash)
            self.assertNotEqual(first.manifest_hash, second.manifest_hash)

    def test_archive_iterator_skips_post_cutoff_paths_before_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            june = root / "june.zip"
            june.write_bytes(b"selected")
            manifest = {"tasks": [
                {"id": "june", "dataset": "klines", "symbol": "BTCUSDT", "period": "2023-06",
                 "interval": "1h", "status": "downloaded", "cached_path": str(june),
                 "sha256": hashlib.sha256(b"selected").hexdigest()},
                {"id": "july", "dataset": "klines", "symbol": "BTCUSDT", "period": "2023-07",
                 "interval": "1h", "status": "downloaded", "cached_path": str(root / "held-must-not-open.zip"),
                 "sha256": "0" * 64},
                {"id": "other-symbol", "dataset": "klines", "symbol": "ETHUSDT", "period": "2023-06",
                 "interval": "1h", "status": "downloaded", "cached_path": str(root / "unselected-symbol.zip"),
                 "sha256": "0" * 64},
            ]}
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest))
            rows = list(iter_cached_archives(path, dataset="klines", interval="1h",
                                              symbols={"BTCUSDT"}, start_date="2020-01-01",
                                              end_exclusive="2023-07-01"))
            self.assertEqual([row["id"] for row in rows], ["june"])

    def test_build_index_excludes_bar_closing_at_cutoff_and_hashes_bounds(self):
        header = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
        june_30 = 1688169600000  # 2023-07-01 00:00 UTC
        csv = header
        for opened in (june_30 - 2 * 3_600_000, june_30 - 3_600_000):
            csv += f"{opened},10,12,9,11,3,{opened+3_599_999},31,2,1,10,0\n"
        blob = archive("BTCUSDT-1h-2023-06.csv", csv)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            june = root / "june.zip"
            june.write_bytes(blob)
            manifest = {"window": {"start": "2021-10-01", "end": "2024-12-31"},
                        "universe": {"symbols": ["BTCUSDT"]}, "tasks": [
                {"id": "june", "dataset": "klines", "symbol": "BTCUSDT", "period": "2023-06",
                 "interval": "1h", "status": "downloaded", "cached_path": str(june),
                 "sha256": hashlib.sha256(blob).hexdigest()},
                {"id": "july", "dataset": "klines", "symbol": "BTCUSDT", "period": "2023-07",
                 "interval": "1h", "status": "downloaded", "cached_path": str(root / "held.zip"),
                 "sha256": "0" * 64},
            ]}
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest))
            build = build_index(path, start_date="2020-01-01", end_exclusive="2023-07-01")
            self.assertEqual(len(build.bars["BTCUSDT"]), 1)
            self.assertEqual(build.bars["BTCUSDT"][0].open_ms, june_30 - 2 * 3_600_000)
            later = build_index(path, start_date="2020-01-01", end_exclusive="2023-06-30")
            self.assertNotEqual(build.manifest_hash, later.manifest_hash)

    def test_overlay_keeps_full_base_binding_but_reads_only_build_repair_rows(self):
        header = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
        cutoff = 1688169600000
        opens = (cutoff - 2 * 3_600_000, cutoff - 3_600_000)
        csv = header + "".join(f"{opened},10,12,9,11,3,{opened+3_599_999},31,2,1,10,0\n" for opened in opens)
        blob = archive("BTCUSDT-1h-2023-06-30.csv", csv)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_archive = root / "base.zip"
            base_archive.write_bytes(archive("empty.csv", header))
            repair_archive = root / "repair.zip"
            repair_archive.write_bytes(blob)
            base_manifest = {"window": {"start": "2021-10-01", "end": "2024-12-31"},
                             "universe": {"symbols": ["BTCUSDT"]}, "tasks": [
                {"id": "klines:BTCUSDT:1h:2023-06", "dataset": "klines", "symbol": "BTCUSDT",
                 "period": "2023-06", "interval": "1h", "status": "downloaded",
                 "cached_path": str(base_archive), "sha256": hashlib.sha256(base_archive.read_bytes()).hexdigest()},
                {"id": "klines:BTCUSDT:1h:2023-07", "dataset": "klines", "symbol": "BTCUSDT",
                 "period": "2023-07", "interval": "1h", "status": "downloaded",
                 "cached_path": str(root / "held-base.zip"), "sha256": "0" * 64},
            ]}
            base_path = root / "manifest.json"
            base_path.write_text(json.dumps(base_manifest))
            overlay = {"status": "complete", "window": ["2021-10-01", "2024-12-31"],
                       "base_content_hash": manifest_content_hash(base_manifest), "tasks": [
                {"id": "build-repair", "dataset": "klines", "symbol": "BTCUSDT", "period": "2023-06-30",
                 "status": "downloaded", "cached_path": str(repair_archive),
                 "sha256": hashlib.sha256(blob).hexdigest(), "missing_open_ms": list(opens)},
                {"id": "held-repair", "dataset": "klines", "symbol": "BTCUSDT", "period": "2023-07-01",
                 "status": "missing", "cached_path": str(root / "held-repair.zip"),
                 "missing_open_ms": [cutoff]},
            ]}
            overlay_path = root / "overlay.json"
            overlay_path.write_text(json.dumps(overlay))
            index = build_index(base_path, overlay_manifest=overlay_path,
                                start_date="2020-01-01", end_exclusive="2023-07-01")
            self.assertEqual(index.overlay_rows, 1)
            self.assertEqual([bar.open_ms for bar in index.bars["BTCUSDT"]], [opens[0]])

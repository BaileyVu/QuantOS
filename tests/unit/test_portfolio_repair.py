from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from quantos.paper.research.data import ArchiveRef
from quantos.paper.research.dataset import build_index
from quantos.paper.research.repair import build_repair_manifest, tasks_from_audit

HOUR = 3_600_000
DAY0 = 1733011200000  # 2024-12-01 00:00 UTC
WINDOW = {"start": "2021-10-01", "end": "2024-12-31"}
HEADER = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"


def zip_rows(name: str, opens: list[int], *, bad_price_at: int | None = None) -> bytes:
    rows = []
    for opened in opens:
        price = 100 if opened == bad_price_at else 10
        rows.append(f"{opened},{price},{price+2},{price-1},{price+1},3,{opened+HOUR-1},31,2,1,10,0\n")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, "".join(rows))
    return stream.getvalue()


class FakeStore:
    def __init__(self, archive_path: Path, blob: bytes):
        self.archive_path, self.blob = archive_path, blob
        self.listed = []
        self.fetched = []

    def list_archives(self, dataset, symbol, interval, start=None, end=None, *, granularity="both"):
        self.listed.append((dataset, symbol, start.isoformat(), granularity))
        return (ArchiveRef(dataset, symbol, start.isoformat(), "https://example/archive.zip", interval, len(self.blob)),)

    def fetch(self, dataset, symbol, period, interval):
        self.fetched.append((dataset, symbol, period))
        self.archive_path.write_bytes(self.blob)
        return self.archive_path


class PortfolioRepairTests(unittest.TestCase):
    def _base_manifest(self, root: Path, blob: bytes) -> tuple[Path, dict]:
        path = root / "monthly.zip"
        path.write_bytes(blob)
        task = {"id": "klines:BTCUSDT:1h:2024-12", "dataset": "klines",
                "symbol": "BTCUSDT", "period": "2024-12", "interval": "1h",
                "status": "downloaded", "cached_path": str(path),
                "sha256": hashlib.sha256(blob).hexdigest()}
        manifest = {"window": WINDOW, "universe": {"symbols": ["BTCUSDT"]}, "tasks": [task]}
        manifest_path = root / "base.json"
        manifest_path.write_text(json.dumps(manifest))
        return manifest_path, manifest

    def test_tasks_include_only_exact_missing_timestamps_from_bar_and_mark_gaps(self):
        audit = {"content_hash": "h", "window": [WINDOW["start"], WINDOW["end"]],
                 "series": [{"dataset": "klines", "symbol": "BTCUSDT", "gaps": [[DAY0, DAY0 + 24 * HOUR]]},
                            {"dataset": "fundingRate", "symbol": "BTCUSDT", "gaps": [[DAY0, DAY0 + 24 * HOUR]]}]}
        tasks = tasks_from_audit(audit)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["dataset"], "klines")
        self.assertEqual(tasks[0]["missing_open_ms"], list(range(DAY0 + HOUR, DAY0 + 24 * HOUR, HOUR)))

    def test_daily_archive_download_and_overlay_fill_exact_gaps_without_rewriting_monthly(self):
        opens = [DAY0, DAY0 + 24 * HOUR]
        base_blob = zip_rows("BTCUSDT-1h-2024-12.csv", opens)
        day_blob = zip_rows("BTCUSDT-1h-2024-12-01.csv", [DAY0 + i * HOUR for i in range(24)])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_path, base = self._base_manifest(root, base_blob)
            before = build_index(base_path)
            audit_path = root / "audit.json"
            audit_path.write_text(json.dumps({"content_hash": before.content_hash,
                                              "window": list(WINDOW.values()),
                                              "series": [{"dataset": "klines", "symbol": "BTCUSDT",
                                                          "gaps": [[DAY0, DAY0 + 24 * HOUR]]}]}))
            store = FakeStore(root / "daily.zip", day_blob)
            repair_path = root / "repair.json"
            repair = build_repair_manifest(audit_path, repair_path, store=store,
                                           base_manifest_path=base_path, workers=2)
            self.assertEqual(repair["status"], "complete")
            self.assertEqual(len(repair["tasks"]), 1)
            self.assertEqual(store.fetched, [("klines", "BTCUSDT", "2024-12-01")])
            # Migrate the previous single-hash manifest shape on safe resume.
            repair["base_content_hash"] = repair["source_audit_hash"]
            repair.pop("source_audit_hash")
            repair_path.write_text(json.dumps(repair))
            repair = build_repair_manifest(audit_path, repair_path, store=store,
                                           base_manifest_path=base_path, workers=2)
            self.assertEqual(store.fetched, [("klines", "BTCUSDT", "2024-12-01")])
            self.assertEqual(Path(repair["tasks"][0]["cached_path"]).read_bytes(), day_blob)
            after = build_index(base_path, overlay_manifest=repair_path)
            self.assertEqual(len(after.bars["BTCUSDT"]), 25)
            self.assertEqual(after.overlay_rows, 23)
            self.assertEqual(after.audit[0].gaps, ())
            self.assertNotEqual(after.content_hash, before.content_hash)
            self.assertEqual(Path(base["tasks"][0]["cached_path"]).read_bytes(), base_blob)

    def test_conflicting_duplicate_daily_row_fails_overlay_closed(self):
        base_blob = zip_rows("BTCUSDT-1h-2024-12.csv", [DAY0, DAY0 + 24 * HOUR])
        day_blob = zip_rows("BTCUSDT-1h-2024-12-01.csv",
                            [DAY0 + i * HOUR for i in range(24)], bad_price_at=DAY0)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_path, _ = self._base_manifest(root, base_blob)
            before = build_index(base_path)
            audit_path = root / "audit.json"
            audit_path.write_text(json.dumps({"content_hash": before.content_hash,
                                              "window": list(WINDOW.values()),
                                              "series": [{"dataset": "klines", "symbol": "BTCUSDT",
                                                          "gaps": [[DAY0, DAY0 + 24 * HOUR]]}]}))
            repair_path = root / "repair.json"
            build_repair_manifest(audit_path, repair_path,
                                  store=FakeStore(root / "daily.zip", day_blob),
                                  base_manifest_path=base_path)
            with self.assertRaisesRegex(ValueError, "conflicting duplicate repair row"):
                build_index(base_path, overlay_manifest=repair_path)

    def test_overlay_checks_digest_and_base_content_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_path, _ = self._base_manifest(root, zip_rows("x.csv", [DAY0]))
            repair = {"status": "complete", "window": list(WINDOW.values()),
                      "base_content_hash": "wrong", "tasks": []}
            repair_path = root / "repair.json"
            repair_path.write_text(json.dumps(repair))
            with self.assertRaisesRegex(ValueError, "different base dataset hash"):
                build_index(base_path, overlay_manifest=repair_path)

    def test_overlay_base_hash_is_verified_for_full_manifest_when_indexing_subset(self):
        base_blob = zip_rows("BTCUSDT-1h-2024-12.csv", [DAY0, DAY0 + 24 * HOUR])
        day_blob = zip_rows("BTCUSDT-1h-2024-12-01.csv", [DAY0 + i * HOUR for i in range(24)])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_path, base = self._base_manifest(root, base_blob)
            base["universe"]["symbols"].append("ETHUSDT")
            base["tasks"].append({"id": "klines:ETHUSDT:1h:2024-12", "dataset": "klines",
                                  "symbol": "ETHUSDT", "period": "2024-12", "interval": "1h",
                                  "status": "missing", "availability_class": "internal_archive_gap"})
            base_path.write_text(json.dumps(base))
            before = build_index(base_path, symbols=("BTCUSDT",))
            audit_path = root / "audit.json"
            audit_path.write_text(json.dumps({"content_hash": before.content_hash,
                                              "window": list(WINDOW.values()),
                                              "series": [{"dataset": "klines", "symbol": "BTCUSDT",
                                                          "gaps": [[DAY0, DAY0 + 24 * HOUR]]}]}))
            repair_path = root / "repair.json"
            build_repair_manifest(audit_path, repair_path,
                                  store=FakeStore(root / "daily.zip", day_blob),
                                  base_manifest_path=base_path)
            after = build_index(base_path, symbols=("BTCUSDT",), overlay_manifest=repair_path)
        self.assertEqual(after.overlay_rows, 23)


if __name__ == "__main__":
    unittest.main()

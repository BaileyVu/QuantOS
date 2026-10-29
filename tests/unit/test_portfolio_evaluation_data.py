from __future__ import annotations

import json
import hashlib
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from quantos.paper.research.protocol import fingerprint
from quantos.paper.research.evaluation_data import (
    validate_seal, validate_seal_from_plan, build_evaluation_plan,
    download_evaluation_batch, run_evaluation_acquisition, _evaluation_run_lock,
)


def sealed_value():
    primary = [f"P{i:02d}USDT" for i in range(29)]
    volume = [f"V{i:02d}USDT" for i in range(29)]
    payload = {"schema": 1,
               "plan": {"evaluation_start": "2025-01-01T00:00:00Z",
                        "evaluation_end_exclusive": "2026-10-29T00:00:00Z",
                        "warmup_start": "2024-10-01T00:00:00Z"},
               "selection": {"primary_symbols": primary,
                             "verified_volume_symbols": volume,
                             "pick": {"frozen": True}},
               "code_sha": "sealed-code", "training_data_sha": "a" * 64}
    return {"payload": payload, "sha256": fingerprint(payload)}


class EvaluationDataTests(unittest.TestCase):
    def test_validates_fixed_seal_and_exact_symbol_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seal.json"
            path.write_text(json.dumps(sealed_value()))
            _, primary, volume = validate_seal(path)
        self.assertEqual(len(primary), 29)
        self.assertEqual(len(volume), 29)

    def test_modified_or_wrong_window_seal_fails_before_listing(self):
        class NoListing:
            called = False
            def list_archives(self, *args, **kwargs):
                self.called = True
                raise AssertionError("listing must not occur before seal validation")
        store = NoListing()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seal.json"
            bad = sealed_value()
            bad["payload"]["plan"]["evaluation_end_exclusive"] = "2026-10-30T00:00:00Z"
            path.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError, "seal modified"):
                build_evaluation_plan(path, store=store)
            bad = sealed_value()
            bad["payload"]["plan"]["evaluation_end_exclusive"] = "2026-10-30T00:00:00Z"
            bad["sha256"] = fingerprint(bad["payload"])
            path.write_text(json.dumps(bad))
            with self.assertRaisesRegex(ValueError, "end"):
                build_evaluation_plan(path, store=store)
        self.assertFalse(store.called)

    def test_unsealed_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                validate_seal(Path(tmp) / "missing-seal.json")

    def test_warmup_reuses_only_checksum_verified_local_rows(self):
        class EmptyListingStore:
            def list_archives(self, *args, **kwargs): return ()
        with tempfile.TemporaryDirectory() as tmp:
            seal_path = Path(tmp) / "seal.json"
            seal_path.write_text(json.dumps(sealed_value()))
            raw = Path(tmp) / "warmup.zip"
            raw.write_bytes(b"cached-source")
            digest = hashlib.sha256(raw.read_bytes()).hexdigest()
            metric_raw = Path(tmp) / "warmup-metrics.zip"
            metric_raw.write_bytes(b"cached-metrics")
            metric_digest = hashlib.sha256(metric_raw.read_bytes()).hexdigest()
            warmup_manifest = Path(tmp) / "training.json"
            warmup_manifest.write_text(json.dumps({"tasks": [{
                "id": "klines:P00USDT:1h:2024-10", "dataset": "klines",
                "symbol": "P00USDT", "interval": "1h", "period": "2024-10",
                "status": "downloaded", "cached_path": str(raw), "sha256": digest,
                "actual_bytes": raw.stat().st_size}, {
                "id": "metrics:BTCUSDT:-:2024-10-01", "dataset": "metrics",
                "symbol": "BTCUSDT", "interval": None, "period": "2024-10-01",
                "status": "downloaded", "cached_path": str(metric_raw), "sha256": metric_digest,
                "actual_bytes": metric_raw.stat().st_size}]}))
            plan = build_evaluation_plan(seal_path, store=EmptyListingStore(),
                                         warmup_manifest_path=warmup_manifest)
            row = next(task for task in plan["tasks"] if task["id"] == "klines:P00USDT:1h:2024-10")
            self.assertEqual(row["status"], "downloaded")
            self.assertEqual(row["sha256"], digest)
            self.assertEqual(row["warmup_source_task_id"], row["id"])
            self.assertEqual(row["warmup_source_manifest_sha256"],
                             hashlib.sha256(warmup_manifest.read_bytes()).hexdigest())
            oi_row = next(task for task in plan["tasks"]
                          if task["id"] == "metrics:BTCUSDT:-:2024-10-01")
            self.assertEqual(oi_row["status"], "downloaded")
            self.assertEqual(oi_row["sha256"], metric_digest)

    def test_run_lock_blocks_active_owner_and_recovers_dead_local_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "evaluation.json"
            lock = Path(str(manifest) + ".lock")
            with _evaluation_run_lock(manifest, "a" * 64):
                self.assertTrue(lock.is_file())
                with self.assertRaisesRegex(RuntimeError, "already active"):
                    with _evaluation_run_lock(manifest, "a" * 64):
                        pass
            self.assertFalse(lock.exists())
            lock.write_text(json.dumps({"pid": 2**30, "pid_start": "gone",
                                        "host": socket.gethostname(), "token": "stale"}))
            with _evaluation_run_lock(manifest, "a" * 64):
                self.assertTrue(lock.is_file())
            self.assertFalse(lock.exists())

    def test_download_attempts_are_bounded_and_persisted_without_network(self):
        class EmptyListingStore:
            def list_archives(self, *args, **kwargs): return ()
        class FailingStore:
            def __init__(self, root):
                self.root, self.max_archive_bytes, self.calls = Path(root), 1000, 0
            def _get(self, url, limit):
                self.calls += 1
                raise RuntimeError("offline test failure")
        with tempfile.TemporaryDirectory() as tmp:
            seal_path = Path(tmp) / "seal.json"
            seal_path.write_text(json.dumps(sealed_value()))
            manifest_path = Path(tmp) / "evaluation.json"
            plan = build_evaluation_plan(seal_path, store=EmptyListingStore(),
                                         warmup_manifest_path=Path(tmp) / "no-training-manifest")
            manifest_path.write_text(json.dumps(plan))
            fake_store = FailingStore(Path(tmp) / "cache")
            for _ in range(3):
                download_evaluation_batch(seal_path, manifest_path, limit=1,
                                          workers=1, store=fake_store)
            result = json.loads(manifest_path.read_text())
            failed = next(t for t in result["tasks"] if t["id"].startswith("fundingRate-api:"))
            self.assertEqual(failed["attempts"], 3)
            self.assertEqual(failed["status"], "error")
            self.assertIn("maximum 3 attempts", failed["error"])
            self.assertEqual(fake_store.calls, 3)
            progress = Path(str(manifest_path) + ".progress.jsonl").read_text().splitlines()
            self.assertEqual(len(progress), 3)

    def test_cli_runner_resumes_existing_exact_manifest_without_relisting(self):
        class EmptyListingStore:
            def list_archives(self, *args, **kwargs): return ()
        with tempfile.TemporaryDirectory() as tmp:
            seal_path = Path(tmp) / "seal.json"
            seal_path.write_text(json.dumps(sealed_value()))
            manifest_path = Path(tmp) / "evaluation.json"
            manifest = build_evaluation_plan(seal_path, store=EmptyListingStore(),
                                             warmup_manifest_path=Path(tmp) / "no-training-manifest")
            for task in manifest["tasks"]:
                if task["evaluation_source"] == "public_funding_api":
                    task.update(status="downloaded", cached_path=str(Path(tmp) / "api.json"),
                                sha256="b" * 64, actual_bytes=2)
            manifest_path.write_text(json.dumps(manifest))
            with patch("quantos.paper.research.evaluation_data.build_evaluation_plan",
                       side_effect=AssertionError("existing sealed manifest must not be rebuilt")):
                result = run_evaluation_acquisition(seal_path, manifest_path,
                    workers=1, cache_root=Path(tmp) / "cache")
            self.assertEqual(result["seal_sha256"], manifest["seal_sha256"])
            self.assertEqual(len(result["tasks"]), len(manifest["tasks"]))

    def test_manifest_task_cannot_escape_window_or_sealed_symbols(self):
        class EmptyListingStore:
            def __init__(self): self.calls = []
            def list_archives(self, dataset, symbol, interval, start, end, *, granularity):
                self.calls.append((dataset, symbol, interval, start, end, granularity))
                if dataset == "metrics" and symbol == "BTCUSDT":
                    raise RuntimeError("simulated listing failure")
                return ()

        with tempfile.TemporaryDirectory() as tmp:
            seal_path = Path(tmp) / "seal.json"
            seal = sealed_value()
            seal_path.write_text(json.dumps(seal))
            store = EmptyListingStore()
            manifest = build_evaluation_plan(seal_path, store=store,
                                             warmup_manifest_path=Path(tmp) / "no-training-manifest")
            validate_seal_from_plan(manifest)
            self.assertLess(len(store.calls), 500)  # grouped by symbol and bounded date range
            self.assertEqual(manifest["window"], {
                "start": "2024-10-01", "end": "2026-10-28",
                "evaluation_start": "2025-01-01", "evaluation_end_exclusive": "2026-10-29"})
            self.assertTrue(all(task["status"] in {"missing", "unverified"} for task in manifest["tasks"]
                                if task["evaluation_source"] == "archive"))
            self.assertTrue(all(task.get("error") for task in manifest["tasks"]
                                if task["status"] in {"missing", "unverified"}))
            self.assertTrue(manifest["listing_errors"])
            self.assertTrue(all(t["status"] == "unverified" and t["error"]
                                for t in manifest["tasks"]
                                if t["dataset"] == "metrics" and t["symbol"] == "BTCUSDT"))
            self.assertEqual(sum(t["phase"] == "warmup" for t in manifest["tasks"]),
                             58 * 3 * 3 + 9 * 92)
            self.assertEqual(sum(t["phase"] == "warmup" and t["dataset"] == "metrics"
                                 for t in manifest["tasks"]), 9 * 92)

            # Every expected period is required; duplicates and truncated ledgers fail closed.
            manifest["tasks"] = manifest["tasks"][:-1]
            with self.assertRaisesRegex(ValueError, "incomplete"):
                validate_seal_from_plan(manifest)
            manifest = build_evaluation_plan(seal_path, store=store,
                                             warmup_manifest_path=Path(tmp) / "no-training-manifest")
            daily = next(t for t in manifest["tasks"] if t["dataset"] == "klines"
                         and t["period"] == "2026-10-01")
            daily["period"] = "2026-10"
            with self.assertRaisesRegex(ValueError, "fixed period/date"):
                validate_seal_from_plan(manifest)
            manifest = build_evaluation_plan(seal_path, store=store,
                                             warmup_manifest_path=Path(tmp) / "no-training-manifest")
            manifest["tasks"].append(dict(manifest["tasks"][0]))
            with self.assertRaisesRegex(ValueError, "duplicate"):
                validate_seal_from_plan(manifest)


if __name__ == "__main__":
    unittest.main()

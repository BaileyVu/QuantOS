from decimal import Decimal as D
from datetime import datetime, timezone
from io import BytesIO, StringIO
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from quantos.paper.research import evaluation_data
from quantos.paper.research.evaluation_index import (
    EVALUATION_END_EXCLUSIVE, EVALUATION_START, _funding_api_rows,
    _funding_audit, _task_rows, _validate_window, build_evaluation_index,
)
from quantos.paper.research.protocol import fingerprint


class EvaluationIndexTests(unittest.TestCase):
    def test_only_sealed_evaluation_window_is_accepted(self):
        plan = {"window": {"start": "2024-10-01", "end": "2026-10-28",
                            "evaluation_start": EVALUATION_START,
                            "evaluation_end_exclusive": EVALUATION_END_EXCLUSIVE},
                "warmup_reuse": {"manifest": "training.json", "from": "2024-10-01T00:00:00Z",
                                  "through_exclusive": "2025-01-01T00:00:00Z", "acquire_again": False}}
        _validate_window(plan)
        with self.assertRaises(ValueError):
            _validate_window({**plan, "window": {**plan["window"], "evaluation_start": "2024-12-31"}})

    def test_public_funding_rows_keep_actual_time_and_seeded_interval(self):
        rows = [{"symbol": "BTCUSDT", "fundingTime": 1_790_000_000_123,
                 "fundingRate": "0.000125"},
                {"symbol": "BTCUSDT", "fundingTime": 1_790_028_800_456,
                 "fundingRate": "-0.00025"}]
        parsed = _funding_api_rows(rows, "BTCUSDT", 8)
        self.assertEqual([x.event_ms for x in parsed], [1_790_000_000_123, 1_790_028_800_456])
        self.assertEqual([x.rate for x in parsed], [D("0.000125"), D("-0.00025")])
        self.assertEqual([x.interval_hours for x in parsed], [8, 8])

    def test_public_funding_rejects_unordered_or_duplicate_events(self):
        rows = [{"fundingTime": 10, "fundingRate": "0"},
                {"fundingTime": 10, "fundingRate": "0"}]
        with self.assertRaises(ValueError):
            _funding_api_rows(rows, "BTCUSDT", 8)

    def test_funding_gap_is_audited_instead_of_relabelled_as_interval_switch(self):
        rows = _funding_api_rows([
            {"fundingTime": 0, "fundingRate": "0"},
            {"fundingTime": 4 * 3_600_000, "fundingRate": "0"},
        ], "BTCUSDT", 8)
        audit = _funding_audit("BTCUSDT", list(rows))
        self.assertEqual(audit.gaps, ((0, 4 * 3_600_000),))

    def test_duplicate_manifest_task_ids_fail_closed(self):
        with self.assertRaises(ValueError):
            _task_rows({"tasks": [{"id": "same"}, {"id": "same"}]})

    def test_offline_plan_validates_and_indexes_new_robustness_warmup(self):
        primary = [f"P{i:03d}USDT" for i in range(29)]
        volume = primary[:9] + [f"V{i:03d}USDT" for i in range(20)]
        selection = {"primary_symbols": primary, "verified_volume_symbols": volume,
                     "pick": {"strategy": "frozen"}}
        payload = {"schema": 1, "plan": {
            "warmup_start": "2024-10-01T00:00:00Z",
            "evaluation_start": "2025-01-01T00:00:00Z",
            "evaluation_end_exclusive": "2026-10-29T00:00:00Z"},
            "selection": selection, "code_sha": "a" * 64, "training_data_sha": "b" * 64}
        seal = {"payload": payload, "sha256": fingerprint(payload)}
        archive_path = "/virtual/V000USDT-1h-2024-10.csv"
        opened = int(datetime(2024, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
        close = opened + 3_600_000 - 1
        csv_bytes = ("open_time,open,high,low,close,volume,close_time,quote_volume,count,"
                    "taker_buy_volume,taker_buy_quote_volume,ignore\n"
                    f"{opened},100,101,99,100,10,{close},1000,1,5,500,0\n").encode()
        class ListingOnly:
            def list_archives(self, *args, **kwargs):
                return ()
        plan_path, seal_path = "/virtual/evaluation.json", "/virtual/seal.json"
        old_read_text, old_open, old_is_file = Path.read_text, Path.open, Path.is_file
        def read_text(path, *args, **kwargs):
            if str(path) == seal_path:
                return json.dumps(seal)
            if str(path) == plan_path:
                return json.dumps(plan)
            return old_read_text(path, *args, **kwargs)
        def open_file(path, mode="r", *args, **kwargs):
            if str(path) == archive_path:
                return BytesIO(csv_bytes) if "b" in mode else StringIO(csv_bytes.decode("utf-8-sig"))
            return old_open(path, mode, *args, **kwargs)
        def is_file(path):
            if str(path) == archive_path:
                return True
            return False
        with patch.object(Path, "read_text", read_text), patch.object(Path, "open", open_file), \
             patch.object(Path, "is_file", is_file):
            plan = evaluation_data.build_evaluation_plan(
                seal_path, store=ListingOnly(), warmup_manifest_path="/virtual/training.json")
            self.assertEqual(evaluation_data.validate_seal_from_plan(plan), seal)
            task = next(row for row in plan["tasks"]
                        if row["id"] == "klines:V000USDT:1h:2024-10")
            task.update({"status": "downloaded", "cached_path": archive_path,
                         "sha256": hashlib.sha256(csv_bytes).hexdigest(),
                         "actual_bytes": len(csv_bytes), "error": None})
            index = build_evaluation_index(plan_path)
        self.assertEqual(index.window, ("2024-10-01", "2026-10-28"))
        self.assertEqual(len(index.bars["V000USDT"]), 1)
        self.assertEqual(index.bar_by_open_ms["V000USDT"][opened].close, D(100))
        self.assertTrue(any(row.get("phase") == "evaluation" for row in index.unavailable_partitions))


if __name__ == "__main__":
    unittest.main()

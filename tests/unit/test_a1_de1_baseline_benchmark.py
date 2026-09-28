"""Tiny synthetic-only checks for the A1 baseline measurement harness."""

from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
import inspect
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from research import a1_de1_baseline_benchmark as benchmark
from quantos.domain.market_data.research_events import AggressorSide


class BaselineBenchmarkTests(unittest.TestCase):
    def test_optional_cold_warm_benchmark_publishes_once_and_consumes_zero_warm_events(self):
        with TemporaryDirectory(prefix="quantos-a1-o2-test-") as directory:
            with patch("socket.socket", side_effect=AssertionError("network forbidden")), \
                 patch.object(benchmark, "publish_day", wraps=benchmark.publish_day) as publication:
                stdout, stderr = StringIO(), StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(benchmark.main([
                        "--root", str(Path(directory) / "cache-run"),
                        "--events-per-minute", "1", "--minute-cache",
                    ]), 0)
                result = json.loads(stdout.getvalue())
            publication.assert_called_once()
            self.assertTrue(result["minute_cache_exact_parity"])
            self.assertEqual(result["minute_cache_cold_diagnostics"]["raw_events_consumed"], 1440)
            self.assertEqual(result["minute_cache_warm_diagnostics"]["raw_events_consumed"], 0)
            self.assertEqual(result["minute_cache_warm_diagnostics"]["cache_hit_partition_count"], 1)
            self.assertGreater(result["minute_cache_cold_wall_seconds"], 0)
            self.assertGreater(result["minute_cache_warm_wall_seconds"], 0)
            self.assertIn("minute_cache_warm", stderr.getvalue())

    def test_independent_runs_have_identical_scientific_evidence_and_cli(self):
        with TemporaryDirectory(prefix="quantos-a1-test-") as directory:
            root = Path(directory)
            parameters = benchmark.Parameters(events_per_minute=1, batch_size=127)
            # A network attempt is an error, even if the injected fixture regresses.
            with patch("socket.socket", side_effect=AssertionError("network forbidden")):
                with patch("zipfile.time.localtime", return_value=(2025, 2, 3, 4, 5, 6)):
                    first = benchmark.run_benchmark(root / "first", parameters)
                stdout, stderr = StringIO(), StringIO()
                with (redirect_stdout(stdout), redirect_stderr(stderr),
                      patch("zipfile.time.localtime", return_value=(2026, 3, 4, 5, 6, 8))):
                    self.assertEqual(benchmark.main([
                        "--root", str(root / "second"), "--days", "1",
                        "--events-per-minute", "1", "--batch-size", "127",
                    ]), 0)
                second = json.loads(stdout.getvalue())
            for key in (
                "range_id", "minute_state_dataset_id", "minute_state_content_sha256",
                "actual_range_total_event_count", "actual_minute_state_count",
                "zero_event_minute_count", "expected_total_event_count",
                "expected_minute_count", "minute_materialization_input_events",
            ):
                self.assertEqual(first[key], second[key], key)
            self.assertEqual(first["actual_range_total_event_count"], 1440)
            self.assertEqual(first["actual_minute_state_count"], 1440)
            self.assertEqual(first["zero_event_minute_count"], 0)
            for phase in ("publication", "catalog_rebuild", "range_compose",
                          "minute_materialization", "total"):
                for clock in ("wall", "cpu"):
                    self.assertGreaterEqual(first[f"{phase}_{clock}_seconds"], 0)
                self.assertIn(phase, stderr.getvalue())
            for phase in ("range_compose", "minute_materialization"):
                self.assertEqual(first[f"{phase}_input_events"], 1440)
                self.assertGreater(first[f"{phase}_events_per_wall_second"], 0)
            for key in ("python_version", "duckdb_version", "pyarrow_version",
                        "numpy_version", "benchmark_schema_version"):
                self.assertTrue(first[key])
            with self.assertRaisesRegex(ValueError, "must be new"):
                benchmark.run_benchmark(root / "first", parameters)

    def test_generated_rows_are_ordered_unique_exact_and_inside_each_day(self):
        parameters = benchmark.Parameters(days=2, events_per_minute=2)
        previous_id = previous_timestamp = 0
        flags = set()
        for day_index in range(2):
            rows = benchmark.synthetic_rows(parameters, day_index)
            self.assertEqual(rows, benchmark.synthetic_rows(parameters, day_index))
            start = benchmark.START_TIMESTAMP_US + day_index * 86_400_000_000
            for identifier, price, quantity, first, last, timestamp, maker, best in rows:
                self.assertEqual(int(identifier), previous_id + 1)
                self.assertEqual(first, identifier)
                self.assertEqual(last, identifier)
                self.assertGreater(int(timestamp), previous_timestamp)
                self.assertTrue(start <= int(timestamp) < start + 86_400_000_000)
                self.assertIsInstance(price, str)
                self.assertIsInstance(quantity, str)
                self.assertEqual(Decimal(price).as_tuple().exponent, -4)
                self.assertEqual(Decimal(quantity).as_tuple().exponent, -8)
                self.assertGreater(Decimal(price), 0)
                self.assertGreater(Decimal(quantity), 0)
                self.assertIn(best, ("True", "False"))
                flags.add(maker)
                previous_id, previous_timestamp = int(identifier), int(timestamp)
        self.assertEqual(flags, {"True", "False"})

    def test_canonical_ingestion_preserves_decimals_and_both_aggressors(self):
        with TemporaryDirectory(prefix="quantos-a1-events-") as directory:
            fetched, _ = benchmark.publish_day(
                Path(directory), benchmark.Parameters(events_per_minute=1), 0
            )
            events = fetched.archive.sequence.events
            self.assertEqual({event.aggressor_side for event in events},
                             {AggressorSide.BUY, AggressorSide.SELL})
            self.assertEqual(events[1].price.as_tuple(), Decimal("30000.0001").as_tuple())
            self.assertEqual(events[1].quantity.as_tuple(), Decimal("0.00000002").as_tuple())

    def test_generator_contains_no_float_math(self):
        tree = ast.parse(inspect.getsource(benchmark.synthetic_rows))
        for node in ast.walk(tree):
            self.assertFalse(isinstance(node, ast.Constant) and isinstance(node.value, float))
            self.assertFalse(isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotEqual(node.func.id, "float")

    def test_invalid_parameters_fail_before_writes(self):
        for field in ("days", "events_per_minute", "batch_size"):
            for value in (0, -1, True, 1.5, "2", None, 10**20):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    benchmark.Parameters(**{field: value})
        with self.assertRaises(ValueError):
            benchmark.Parameters(symbol="BNBUSDT")
        for day in (-1, 1, True):
            with self.assertRaises(ValueError):
                benchmark.synthetic_rows(benchmark.Parameters(), day)
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "invalid"
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
                benchmark.main(["--root", str(destination), "--days", "0"])
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(destination.exists())

    def test_protected_and_ambiguous_paths_rejected_without_filesystem_access(self):
        unsafe = (
            r"G:\QuantOS", r"g:\quantos\nested", r"G:\QuantOS-A1\bench",
            r"G:\QuantOS-A1\..\QuantOS\nested",
            r"G:\QuantOS-Data\research\alpha-funnel-af4b\bench",
            r"\\server\share\bench", r"\\?\G:\QuantOS\bench",
            r"G:\QuantOS.\bench", r"G:\elsewhere\bench:stream",
        )
        with patch.object(Path, "lstat", side_effect=AssertionError("must not inspect")):
            for path in unsafe:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    benchmark.validate_root(Path(path))

    def test_reparse_ancestors_and_existing_roots_fail_closed(self):
        with TemporaryDirectory() as directory:
            parent = Path(directory)
            with self.assertRaisesRegex(ValueError, "must be new"):
                benchmark.validate_root(parent)
            with self.assertRaisesRegex(ValueError, "parent must already exist"):
                benchmark.validate_root(parent / "missing" / "run")
            with patch.object(Path, "lstat", return_value=SimpleNamespace(
                st_mode=0, st_file_attributes=0x400
            )), self.assertRaisesRegex(ValueError, "links or junctions"):
                benchmark.validate_root(parent / "run")
            self.assertEqual(benchmark.validate_root(parent / "run"), parent.resolve() / "run")


if __name__ == "__main__":
    unittest.main()

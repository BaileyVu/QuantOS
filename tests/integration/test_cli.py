"""Smoke tests for the no-side-effect Phase 1 CLI."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from quantos.domain.market_data.research_events import (
    aggregate_trade_archive_manifest_id,
)
from tests.aggregate_trade_range_fixtures import publish_partition

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CliSmokeTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        source_root = str(PROJECT_ROOT / "src")
        environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, "-m", "quantos", *arguments],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            check=False,
            text=True,
        )

    def test_cli_starts_and_stops_in_paper_mode(self) -> None:
        result = self.run_cli("--config", "configs/default.toml")

        self.assertEqual(result.returncode, 0, result.stderr)
        events = [json.loads(line) for line in result.stderr.splitlines()]
        self.assertEqual(
            [event["event"] for event in events],
            ["application_started", "application_ready", "application_stopped"],
        )
        self.assertTrue(all(event["context"]["runtime_mode"] == "paper" for event in events))

    def test_cli_returns_structured_configuration_failure(self) -> None:
        result = self.run_cli("--config", "missing-config.toml")

        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["event"], "configuration_error")
        self.assertIn("not found", payload["error"])

    def write_research_config(self, directory: Path, data_root: Path) -> Path:
        path = directory / "research.toml"
        path.write_text(
            "\n".join(
                (
                    "[quantos]",
                    'runtime_mode = "research"',
                    'symbols = ["BTCUSDT", "ETHUSDT"]',
                    'timeframe = "1m"',
                    f'data_dir = "{data_root.as_posix()}"',
                    'log_level = "INFO"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        return path

    def test_aggregate_trade_catalog_and_compose_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "data"
            december, _ = publish_partition(
                data_root,
                symbol="BTCUSDT",
                source_date=date(2024, 12, 31),
                first_id=10,
            )
            january, _ = publish_partition(
                data_root,
                symbol="BTCUSDT",
                source_date=date(2025, 1, 1),
                first_id=20,
            )
            config = self.write_research_config(root, data_root)
            listed = self.run_cli(
                "--config", str(config), "aggregate-trades", "list", "--symbol", "BTCUSDT"
            )
            revisions = self.run_cli(
                "--config",
                str(config),
                "aggregate-trades",
                "revisions",
                "BTCUSDT",
                "2024-12-31",
            )
            inspected = self.run_cli(
                "--config",
                str(config),
                "aggregate-trades",
                "inspect",
                aggregate_trade_archive_manifest_id(december.archive.manifest),
            )
            output = root / "range.json"
            composed = self.run_cli(
                "--config",
                str(config),
                "aggregate-trades",
                "compose",
                "BTCUSDT",
                "2024-12-31",
                "2025-01-02",
                "--output",
                str(output),
            )
            repeated = self.run_cli(
                "--config",
                str(config),
                "aggregate-trades",
                "compose",
                "BTCUSDT",
                "2024-12-31",
                "2025-01-02",
                "--output",
                str(output),
            )

            for result in (listed, revisions, inspected, composed, repeated):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
            self.assertEqual(json.loads(listed.stdout)["event"], "aggregate_trade_catalog")
            self.assertEqual(len(json.loads(revisions.stdout)["revisions"]), 1)
            self.assertEqual(
                json.loads(inspected.stdout)["entry"]["manifest_id"],
                aggregate_trade_archive_manifest_id(december.archive.manifest),
            )
            manifest = json.loads(composed.stdout)
            self.assertEqual(manifest["total_accepted_event_count"], 4)
            self.assertEqual(output.read_text(encoding="utf-8"), composed.stdout)
            self.assertEqual(repeated.stdout, composed.stdout)
            self.assertEqual(
                [item["manifest_id"] for item in manifest["partitions"]],
                [
                    aggregate_trade_archive_manifest_id(december.archive.manifest),
                    aggregate_trade_archive_manifest_id(january.archive.manifest),
                ],
            )

            collision = root / "collision.json"
            collision.write_text("different", encoding="utf-8")
            rejected = self.run_cli(
                "--config",
                str(config),
                "aggregate-trades",
                "compose",
                "BTCUSDT",
                "2024-12-31",
                "2025-01-02",
                "--output",
                str(collision),
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(collision.read_text(encoding="utf-8"), "different")

    def test_aggregate_trade_cli_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self.write_research_config(root, root / "data")
            unsupported = self.run_cli(
                "--config", str(config), "aggregate-trades", "list", "--symbol", "SOLUSDT"
            )
            missing = self.run_cli(
                "--config",
                str(config),
                "aggregate-trades",
                "compose",
                "BTCUSDT",
                "2025-01-01",
                "2025-01-02",
            )

            for result in (unsupported, missing):
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(json.loads(result.stderr)["event"], "operator_error")

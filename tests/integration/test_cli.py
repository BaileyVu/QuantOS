"""Smoke tests for the no-side-effect Phase 1 CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

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


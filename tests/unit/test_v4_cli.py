import contextlib
import io
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quantos.paper.adaptive import Edge
from quantos.paper.cli import main, _strategy_from_config
from quantos.paper.journal import Journal
from quantos.paper.strategies import library
from quantos.paper.strategies.manifest import manifest_for


class V4CliTests(unittest.TestCase):
    def invoke(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = main(args)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_strategy_listing_is_complete_and_read_only_without_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "absent"
            status, stdout, _stderr = self.invoke(["strategies", "list", "--state", str(state)])
            rows = json.loads(stdout)
            self.assertEqual(status, 0)
            self.assertEqual(len(rows), 6)
            self.assertEqual({row["manifest"]["strategy_id"] for row in rows},
                             {m.strategy_id for s in library() if (m := manifest_for(s.strategy_id))})
            for row in rows:
                self.assertIn("evidence_status", row)
                self.assertEqual(row["pA"]["observations"], "none")
                self.assertEqual(row["pA"]["source"], "default_probe_no_observations")
                self.assertEqual(row["pA"]["risk_percent"], "0.2500")
            self.assertFalse(state.exists())

    def test_strategy_listing_reads_edge_and_does_not_modify_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir()
            journal = Journal(state / "journal_pA.db", "pA")
            with journal.transaction():
                journal.set("edge:tsmom_4h", asdict(Edge(state="PROBATION", n=4, s1=2, s2=3)))
                journal.set("edge:ema_atr_limit_pullback", asdict(Edge(n=3, s1=1, s2=2)))
                journal.set("paused", "LOSS_BREAKER")
                journal.set("high_water", "200")
            journal.close()
            path = state / "journal_pA.db"
            before = path.read_bytes()
            status, stdout, _stderr = self.invoke(["strategies", "list", "--state", str(state)])
            rows = json.loads(stdout)
            self.assertEqual(status, 0)
            row = next(item for item in rows if item["manifest"]["strategy_id"] == "tsmom_4h")
            self.assertEqual((row["pA"]["state"], row["pA"]["n"], row["pA"]["observations"]),
                             ("PROBATION", 4, "available"))
            self.assertEqual(path.read_bytes(), before)
            reference = next(item for item in rows if item["manifest"]["strategy_id"] == "ema_atr_ref")
            self.assertEqual(reference["pA"]["n"],3)
            self.assertEqual(Decimal(reference["pA"]["risk_fraction"]),Decimal(0))

    def test_each_preregistered_backtest_config_resolves_manifest(self):
        configs = sorted(Path("configs/strategies").glob("*.toml"))
        self.assertEqual(len(configs), 9)
        for config in configs:
            with self.subTest(config=config.name):
                strategy_id = _strategy_from_config(config)
                self.assertIsNotNone(manifest_for(strategy_id))

    def test_backtest_config_dispatches_canonical_strategy(self):
        config = Path("configs/strategies/ema_atr_ref.toml")
        with patch("quantos.paper.replay.replay", return_value={"ok": True}) as replay:
            status, stdout, _stderr = self.invoke([
                "backtest", "--input", "record.jsonl", "--state", "output", "--config", str(config)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout), {"ok": True})
        replay.assert_called_once_with([Path("record.jsonl")], Path("output"), "ema_atr_ref")

    def test_backtest_config_rejects_unknown_or_tuned_parameters(self):
        original = Path("configs/strategies/ema_atr_ref.toml").read_text()
        with tempfile.TemporaryDirectory() as directory:
            bad_config = Path(directory) / "bad.toml"
            state = Path(directory) / "untouched"
            for text, message in ((original.replace('id = "ema_atr_ref"', 'id = "unknown"'), "unknown strategy"),
                                  (original.replace("fast_ema = 20", "fast_ema = 21"), "preregistered manifest")):
                with bad_config.open("w") as file:
                    file.write(text)
                status, _stdout, stderr = self.invoke([
                    "backtest", "--input", "record.jsonl", "--state", str(state), "--config", str(bad_config)])
                self.assertEqual(status, 1)
                self.assertIn(message, stderr)
                self.assertFalse(state.exists())

    def test_start_and_service_accept_quote_volume_floor(self):
        with patch("quantos.paper.cli.Runtime") as runtime:
            runtime.return_value.run.return_value = 0
            status, _stdout, _stderr = self.invoke([
                "start", "--state", "state", "--min-quote-volume", "123.45"])
            self.assertEqual(status, 0)
            self.assertEqual(runtime.call_args.kwargs["min_quote_volume"], Decimal("123.45"))
        with patch("quantos.paper.service.install_service", return_value=Path("service.plist")) as install:
            status, _stdout, _stderr = self.invoke([
                "install-service", "--state", "state", "--min-quote-volume", "25000000"])
            self.assertEqual(status, 0)
            self.assertEqual(install.call_args.kwargs["min_quote_volume"], Decimal("25000000"))


if __name__ == "__main__":
    unittest.main()

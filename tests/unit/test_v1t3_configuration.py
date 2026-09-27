"""Strict paper composition, with no public network calls."""
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quantos.infrastructure.configuration.config import ConfigurationError
from quantos.infrastructure.configuration.paper_runtime import load_paper_runtime_config
from quantos.interfaces.paper_runtime import paper_command, non_trading_hold, SMOKE_ALPHA_ID
from quantos.application.paper_runtime import PaperRuntimeError
from quantos.domain.alpha import AlphaAction
from quantos.domain.features import compute_feature_vector
from tests.unit.test_v1t2_evaluation import dataset


class RuntimeConfigurationTests(unittest.TestCase):
    def test_valid_external_config_and_symbol_order(self):
        config = load_paper_runtime_config("configs/paper_runtime.toml", alpha_implementation_id="test")
        self.assertEqual(config.policy.symbols, ("BTCUSDT", "ETHUSDT"))
        self.assertEqual(config.policy.mode, "paper")

    def test_invalid_runtime_configuration_fails_closed(self):
        source = Path("configs/paper_runtime.toml").read_text()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root/"paper.toml").write_text(Path("configs/paper.toml").read_text())
            variants = [
                source+'unexpected = "bad"\n',
                source.replace('mode = "paper"', 'mode = "live"'),
                source.replace('interval = "1m"', 'interval = "5m"'),
                source.replace('["BTCUSDT", "ETHUSDT"]', '["BTCUSDT", "BTCUSDT"]'),
                source.replace("5000", "true"),
                source.replace("artifacts/paper/runtime.json", "artifacts/paper/execution.jsonl"),
                source.replace("artifacts/paper/minutes.jsonl", "artifacts/paper/runtime.json.tmp"),
            ]
            for text in variants:
                with self.subTest(text=text):
                    path = root/"runtime.toml"
                    path.write_text(text)
                    with self.assertRaises(ConfigurationError):
                        load_paper_runtime_config(path, alpha_implementation_id="test")

    def test_missing_alpha_fails_before_feed_composition(self):
        with patch("quantos.interfaces.paper_runtime.run_paper", side_effect=AssertionError("feed created")):
            with self.assertRaisesRegex(PaperRuntimeError, "Alpha is not selected"):
                paper_command(SimpleNamespace(non_trading_smoke=False), logging.getLogger("quantos"))

    def test_explicit_smoke_can_only_hold(self):
        candles = dataset(count=21).candles
        feature = compute_feature_vector(candles, decision_time=candles[-1].close_time)
        evaluated = non_trading_hold(feature)
        self.assertEqual(evaluated.decision.action, AlphaAction.HOLD)
        self.assertEqual(evaluated.decision.strategy_version, SMOKE_ALPHA_ID)
        self.assertEqual(evaluated.gross_edge_rate, 0)

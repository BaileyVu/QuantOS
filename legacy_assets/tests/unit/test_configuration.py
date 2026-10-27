"""Tests for strict Phase 1 configuration loading."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quantos.infrastructure.configuration import ConfigurationError, RuntimeMode, load_config

VALID_CONFIG = """[quantos]
runtime_mode = "paper"
symbols = ["BTCUSDT", "ETHUSDT"]
timeframe = "1m"
data_dir = "data"
log_level = "INFO"
"""


class ConfigurationTests(unittest.TestCase):
    def write_config(self, contents: str) -> Path:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "config.toml"
        path.write_text(contents, encoding="utf-8")
        return path

    def test_loads_safe_paper_defaults(self) -> None:
        config = load_config(self.write_config(VALID_CONFIG))

        self.assertEqual(config.runtime_mode, RuntimeMode.PAPER)
        self.assertEqual(config.symbols, ("BTCUSDT", "ETHUSDT"))
        self.assertEqual(config.timeframe, "1m")

    def test_allows_each_supported_symbol_as_a_single_symbol_run(self) -> None:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            with self.subTest(symbol=symbol):
                config = load_config(
                    self.write_config(VALID_CONFIG.replace('["BTCUSDT", "ETHUSDT"]', f'["{symbol}"]'))
                )

                self.assertEqual(config.symbols, (symbol,))

    def test_rejects_empty_duplicate_and_unsupported_symbols(self) -> None:
        invalid_symbols = (
            ("[]", "must not be empty"),
            ('["BTCUSDT", "BTCUSDT"]', "must not contain duplicates"),
            ('["BTCUSDT", "SOLUSDT"]', "limited to BTCUSDT and ETHUSDT"),
        )
        for symbols, error in invalid_symbols:
            with self.subTest(symbols=symbols):
                path = self.write_config(VALID_CONFIG.replace('["BTCUSDT", "ETHUSDT"]', symbols))

                with self.assertRaisesRegex(ConfigurationError, error):
                    load_config(path)

    def test_rejects_invalid_timeframe_and_runtime_mode(self) -> None:
        invalid_configs = (
            (VALID_CONFIG.replace('timeframe = "1m"', 'timeframe = "5m"'), "timeframe"),
            (VALID_CONFIG.replace('runtime_mode = "paper"', 'runtime_mode = "invalid"'), "runtime_mode"),
            (VALID_CONFIG.replace('runtime_mode = "paper"', 'runtime_mode = "live"'), "unavailable during Phase 1"),
        )
        for contents, error in invalid_configs:
            with self.subTest(error=error):
                with self.assertRaisesRegex(ConfigurationError, error):
                    load_config(self.write_config(contents))

    def test_rejects_empty_or_whitespace_data_paths(self) -> None:
        for value in ('""', '"   "'):
            with self.subTest(value=value):
                path = self.write_config(VALID_CONFIG.replace('data_dir = "data"', f"data_dir = {value}"))

                with self.assertRaisesRegex(ConfigurationError, "data_dir must not be empty"):
                    load_config(path)

    def test_normalizes_missing_and_unreadable_configuration_paths(self) -> None:
        with TemporaryDirectory() as temp_dir:
            missing_path = Path(temp_dir) / "missing.toml"
            with self.assertRaisesRegex(ConfigurationError, "not found"):
                load_config(missing_path)

            with self.assertRaisesRegex(ConfigurationError, "could not be read"):
                load_config(Path(temp_dir))


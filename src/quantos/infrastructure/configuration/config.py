"""Strict Phase 1 TOML configuration outside business logic."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import tomllib
from typing import Any

from quantos.domain.common import V1_INTERVAL, V1_SYMBOLS


class ConfigurationError(ValueError):
    """Raised when configuration is missing, malformed, or unsafe."""


class RuntimeMode(str, Enum):
    RESEARCH = "research"
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True, slots=True)
class AppConfig:
    """The minimum safe configuration required by the Phase 1 runtime."""

    runtime_mode: RuntimeMode
    symbols: tuple[str, ...]
    timeframe: str
    data_dir: Path
    log_level: str

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_mode, RuntimeMode):
            raise ConfigurationError("runtime_mode must be a supported runtime mode")
        if self.runtime_mode is RuntimeMode.LIVE:
            raise ConfigurationError("live mode is unavailable during Phase 1")
        if isinstance(self.symbols, str):
            raise ConfigurationError("symbols must be a collection of symbols")
        try:
            symbols = tuple(self.symbols)
        except TypeError as error:
            raise ConfigurationError("symbols must be a collection of symbols") from error
        if not symbols:
            raise ConfigurationError("symbols must not be empty")
        if not all(isinstance(symbol, str) for symbol in symbols):
            raise ConfigurationError("symbols must be a collection of strings")
        if len(set(symbols)) != len(symbols):
            raise ConfigurationError("symbols must not contain duplicates")
        if not set(symbols).issubset(V1_SYMBOLS):
            raise ConfigurationError("symbols must be limited to BTCUSDT and ETHUSDT")
        object.__setattr__(self, "symbols", symbols)
        if self.timeframe != V1_INTERVAL:
            raise ConfigurationError("timeframe must be '1m'")
        if not str(self.data_dir):
            raise ConfigurationError("data_dir must not be empty")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("log_level must be a standard uppercase logging level")

    def log_context(self) -> dict[str, object]:
        """Return a deliberately non-secret context for lifecycle logging."""
        return {
            "runtime_mode": self.runtime_mode.value,
            "symbols": list(self.symbols),
            "timeframe": self.timeframe,
            "data_dir": str(self.data_dir),
        }


_REQUIRED_KEYS = {"runtime_mode", "symbols", "timeframe", "data_dir", "log_level"}


def load_config(path: str | Path) -> AppConfig:
    """Load and validate the one supported Phase 1 TOML configuration section."""
    config_path = Path(path)
    try:
        with config_path.open("rb") as config_file:
            document: Any = tomllib.load(config_file)
    except FileNotFoundError as error:
        raise ConfigurationError(f"configuration file not found: {config_path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigurationError(f"configuration file is invalid TOML: {config_path}") from error
    except OSError as error:
        raise ConfigurationError(f"configuration file could not be read: {config_path}") from error

    if set(document) != {"quantos"} or not isinstance(document.get("quantos"), dict):
        raise ConfigurationError("configuration must contain only a [quantos] table")
    section = document["quantos"]
    if set(section) != _REQUIRED_KEYS:
        missing = sorted(_REQUIRED_KEYS - set(section))
        unknown = sorted(set(section) - _REQUIRED_KEYS)
        details: list[str] = []
        if missing:
            details.append(f"missing keys: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown keys: {', '.join(unknown)}")
        raise ConfigurationError("invalid [quantos] keys (" + "; ".join(details) + ")")
    if not isinstance(section["runtime_mode"], str):
        raise ConfigurationError("runtime_mode must be a string")
    if not isinstance(section["symbols"], list) or not all(
        isinstance(symbol, str) for symbol in section["symbols"]
    ):
        raise ConfigurationError("symbols must be an array of strings")
    if not isinstance(section["timeframe"], str):
        raise ConfigurationError("timeframe must be a string")
    if not isinstance(section["data_dir"], str):
        raise ConfigurationError("data_dir must be a string")
    if not section["data_dir"].strip():
        raise ConfigurationError("data_dir must not be empty")
    if not isinstance(section["log_level"], str):
        raise ConfigurationError("log_level must be a string")
    try:
        runtime_mode = RuntimeMode(section["runtime_mode"])
    except ValueError as error:
        raise ConfigurationError("runtime_mode must be research, backtest, paper, or live") from error
    return AppConfig(
        runtime_mode=runtime_mode,
        symbols=tuple(section["symbols"]),
        timeframe=section["timeframe"],
        data_dir=Path(section["data_dir"]),
        log_level=section["log_level"],
    )

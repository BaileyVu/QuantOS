"""Minimal local CLI for the Phase 1 foundation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from quantos.application import run
from quantos.infrastructure.configuration import ConfigurationError, load_config
from quantos.infrastructure.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the QuantOS V1 Phase 1 foundation.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/default.toml"),
        help="Path to a Phase 1 QuantOS TOML configuration file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigurationError as error:
        print(json.dumps({"event": "configuration_error", "error": str(error)}), file=sys.stderr)
        return 2
    logger = configure_logging(config.log_level)
    return run(logger, config.log_context())

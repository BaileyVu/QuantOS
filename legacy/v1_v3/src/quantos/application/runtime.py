"""Legacy entry point; continuous paper operation requires explicit composition."""

from __future__ import annotations

from collections.abc import Mapping
import logging


def run(logger: logging.Logger, runtime_context: Mapping[str, object]) -> int:
    """Preserve non-paper startup checks and reject uncomposed paper startup."""
    context = dict(runtime_context)
    logger.info("application_started", extra={"event": "application_started", "context": context})
    try:
        if context.get("runtime_mode") == "paper":
            context["reason"] = "production Alpha is not selected; use explicit paper composition or paper --non-trading-smoke"
            logger.error("paper_alpha_required", extra={"event": "paper_alpha_required", "context": context})
            return 2
        logger.info("application_ready", extra={"event": "application_ready", "context": context})
        return 0
    finally:
        logger.info("application_stopped", extra={"event": "application_stopped", "context": context})

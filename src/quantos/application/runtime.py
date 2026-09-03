"""Phase 1 local runtime with no market, model, or execution side effects."""

from __future__ import annotations

from collections.abc import Mapping
import logging


def run(logger: logging.Logger, runtime_context: Mapping[str, object]) -> int:
    """Initialize Phase 1 services and exit cleanly without trading activity."""
    context = dict(runtime_context)
    logger.info("application_started", extra={"event": "application_started", "context": context})
    try:
        logger.info("application_ready", extra={"event": "application_ready", "context": context})
        return 0
    finally:
        logger.info("application_stopped", extra={"event": "application_stopped", "context": context})

"""Small JSON logging adapter for application lifecycle events."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any, Mapping

_SENSITIVE_TOKENS = ("secret", "password", "token", "credential", "api_key")


def _redact(value: Any, key: str = "") -> Any:
    """Remove values whose key indicates credentials or another secret."""
    if any(token in key.lower() for token in _SENSITIVE_TOKENS):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(child_key): _redact(child_value, str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """Render each supported application log record as one JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
        }
        context = getattr(record, "context", None)
        if context is not None:
            payload["context"] = _redact(context)
        return json.dumps(payload, sort_keys=True, default=str)


def configure_logging(log_level: str) -> logging.Logger:
    """Configure the dedicated QuantOS application logger without root side effects."""
    logger = logging.getLogger("quantos")
    logger.setLevel(log_level)
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


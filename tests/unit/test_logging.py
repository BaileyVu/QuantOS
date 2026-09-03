"""Tests for centralized structured logging."""

from __future__ import annotations

import io
import json
import logging
import unittest

from quantos.infrastructure.logging import JsonFormatter, configure_logging


class LoggingTests(unittest.TestCase):
    def test_json_formatter_redacts_secret_context(self) -> None:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="quantos",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="configuration_error",
            args=(),
            exc_info=None,
        )
        record.event = "configuration_error"
        record.context = {"api_key": "do-not-log", "runtime_mode": "paper"}

        payload = json.loads(formatter.format(record))
        self.assertEqual(payload["event"], "configuration_error")
        self.assertEqual(payload["context"]["api_key"], "[REDACTED]")

    def test_configured_logger_emits_json(self) -> None:
        logger = configure_logging("INFO")
        stream = io.StringIO()
        logger.handlers[0].setStream(stream)
        logger.info("application_started", extra={"event": "application_started", "context": {}})

        self.assertEqual(json.loads(stream.getvalue())["event"], "application_started")


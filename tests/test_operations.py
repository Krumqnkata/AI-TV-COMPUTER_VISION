"""Operational logging, metrics, scheduler and reconnect contracts."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import unittest

from tools.load_reconnect import run_load_scenario
from utils.logger import JsonFormatter, bind_request_context, reset_request_context
from web.services.metrics import MetricsRegistry


class TestStructuredLogging(unittest.TestCase):
    def test_json_formatter_carries_correlation_without_traceback_or_secret(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setFormatter(JsonFormatter())
        logger = logging.Logger("structured-test")
        logger.addHandler(handler)
        tokens = bind_request_context("request-123", "device-7")
        try:
            try:
                raise RuntimeError("secret database password")
            except RuntimeError:
                logger.exception(
                    "Safe reduced failure",
                    extra={"event": "test.failed"},
                )
        finally:
            reset_request_context(tokens)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["request_id"], "request-123")
        self.assertEqual(payload["device_id"], "device-7")
        self.assertEqual(payload["event"], "test.failed")
        self.assertEqual(payload["error_type"], "RuntimeError")
        self.assertNotIn("secret database password", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())


class TestMetricsRegistry(unittest.TestCase):
    def test_http_and_qr_metrics_use_only_bounded_labels(self):
        registry = MetricsRegistry()
        registry.record_http("GET", 200, 0.2)
        registry.record_http("BREW", 503, 0.4)
        registry.record_qr_result("success")
        registry.record_qr_result("unexpected-secret-value")

        snapshot = registry.snapshot()
        self.assertEqual(snapshot["http_requests"][("GET", "2xx")], 1)
        self.assertEqual(snapshot["http_requests"][("OTHER", "5xx")], 1)
        self.assertEqual(snapshot["http_errors"], 1)
        self.assertEqual(snapshot["qr_results"]["success"], 1)
        self.assertEqual(snapshot["qr_results"]["error"], 1)
        self.assertNotIn("unexpected-secret-value", repr(snapshot))


class TestReconnectLoadBaseline(unittest.TestCase):
    def test_expected_device_count_survives_reconnect_churn(self):
        result = asyncio.run(run_load_scenario(100, 5))
        self.assertEqual(result["devices"], 100)
        self.assertEqual(result["registrations"], 600)
        self.assertEqual(result["final_connections"], 99)
        self.assertGreater(result["registrations_per_second"], 0)


if __name__ == "__main__":
    unittest.main()

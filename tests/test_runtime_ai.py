"""Provider configuration and operational guard contracts for external AI."""

from __future__ import annotations

import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


ai_runtime = None
metrics_registry = None


class TestAIRuntime(unittest.TestCase):
    def setUp(self):
        # Defer application imports until test execution so discovery can
        # install any suite-specific DATABASE_URL before Config is cached.
        global ai_runtime, metrics_registry
        if ai_runtime is None or metrics_registry is None:
            from web.services import ai_runtime as runtime_module
            from web.services.metrics import metrics_registry as registry

            ai_runtime = runtime_module
            metrics_registry = registry
        ai_runtime.reset_ai_runtime_state()
        metrics_registry.reset()

    def tearDown(self):
        ai_runtime.reset_ai_runtime_state()
        metrics_registry.reset()

    @staticmethod
    def _guard_setting(_db, key):
        return {
            "assistant.external_calls_per_minute": 20,
            "assistant.circuit_failure_threshold": 2,
            "assistant.circuit_reset_seconds": 60,
        }[key]

    def test_provider_specific_models_are_selected_independently(self):
        settings = {
            "assistant.provider": "gemini",
            "assistant.gemini_model": "gemini-school-test",
            "assistant.ollama_model": "ollama-school-test",
            "assistant.temperature": 0.25,
        }

        def get_value(_db, key):
            return settings[key]

        with (
            patch.object(ai_runtime, "get_setting", side_effect=get_value),
            patch.object(
                ai_runtime,
                "read_secret",
                side_effect=lambda _db, key: (
                    "test-gemini-key" if key == "gemini.api_key" else None
                ),
            ),
        ):
            gemini = ai_runtime._runtime_options(object())
            settings["assistant.provider"] = "ollama"
            ollama = ai_runtime._runtime_options(object())

        self.assertEqual(gemini["model"], "gemini-school-test")
        self.assertEqual(ollama["model"], "ollama-school-test")
        self.assertEqual(gemini["provider"], "gemini")
        self.assertEqual(ollama["provider"], "ollama")

    def test_rate_limit_blocks_the_second_external_call(self):
        options = {
            "provider": "gemini",
            "model": "test-model",
            "temperature": 0,
            "api_key": "test-key",
            "timeout_seconds": 1,
            "configured": True,
        }

        def setting(_db, key):
            values = {
                "assistant.external_calls_per_minute": 1,
                "assistant.circuit_failure_threshold": 3,
                "assistant.circuit_reset_seconds": 60,
            }
            return values[key]

        with (
            patch.object(ai_runtime, "_runtime_options", return_value=options),
            patch.object(ai_runtime, "get_setting", side_effect=setting),
            patch.object(ai_runtime, "_call_provider", return_value="OK") as call,
        ):
            first = ai_runtime._run_generation(object(), "test", "system")
            second = ai_runtime._run_generation(object(), "test", "system")

        self.assertEqual(first.outcome, "success")
        self.assertEqual(second.outcome, "rate_limited")
        self.assertEqual(call.call_count, 1)
        snapshot = metrics_registry.snapshot()
        self.assertEqual(
            snapshot["ai_requests"][("gemini", "rate_limited")],
            1,
        )

    def test_circuit_breaker_opens_after_configured_failures(self):
        options = {
            "provider": "ollama",
            "model": "test-model",
            "temperature": 0,
            "api_key": None,
            "timeout_seconds": 1,
            "configured": True,
        }
        with (
            patch.object(ai_runtime, "_runtime_options", return_value=options),
            patch.object(
                ai_runtime,
                "get_setting",
                side_effect=self._guard_setting,
            ),
            patch.object(
                ai_runtime,
                "_call_provider",
                side_effect=TimeoutError("sensitive upstream detail"),
            ) as call,
        ):
            first = ai_runtime._run_generation(object(), "test", "system")
            second = ai_runtime._run_generation(object(), "test", "system")
            third = ai_runtime._run_generation(object(), "test", "system")

        self.assertEqual(first.outcome, "error")
        self.assertEqual(second.outcome, "error")
        self.assertEqual(third.outcome, "circuit_open")
        self.assertEqual(call.call_count, 2)
        status = ai_runtime._runtime_guard.snapshot("ollama", 20)
        self.assertTrue(status["circuit_open"])
        self.assertEqual(status["consecutive_failures"], 2)
        self.assertEqual(status["last_error_type"], "TimeoutError")
        self.assertNotIn("sensitive upstream detail", repr(status))

    def test_connection_probe_returns_only_sanitized_failure(self):
        configuration = {
            "provider": "gemini",
            "model": "test-model",
            "temperature": 0,
            "api_key": "test-key",
            "timeout_seconds": 1,
            "configured": True,
        }
        with (
            patch.object(
                ai_runtime,
                "_runtime_configuration",
                return_value=configuration,
            ),
            patch.object(
                ai_runtime,
                "get_setting",
                side_effect=self._guard_setting,
            ),
            patch.object(
                ai_runtime,
                "_call_provider",
                side_effect=RuntimeError("secret diagnostic from provider"),
            ),
        ):
            result = ai_runtime.probe_ai_connection(object())

        self.assertFalse(result["ok"])
        self.assertEqual(result["outcome"], "error")
        self.assertNotIn("secret diagnostic from provider", repr(result))
        self.assertNotIn("test-key", repr(result))

    def test_sdk_clients_receive_the_global_timeout(self):
        google_module = ModuleType("google")
        gemini_models = Mock()
        gemini_models.generate_content.return_value = SimpleNamespace(text=" OK ")
        gemini_client = SimpleNamespace(models=gemini_models, close=Mock())
        fake_genai = SimpleNamespace(
            Client=Mock(return_value=gemini_client),
            types=SimpleNamespace(
                HttpOptions=lambda **values: SimpleNamespace(**values),
                GenerateContentConfig=lambda **values: SimpleNamespace(**values),
            ),
        )
        google_module.genai = fake_genai

        ollama_module = ModuleType("ollama")
        ollama_client = Mock()
        ollama_client.chat.return_value = {"message": {"content": " OK "}}
        ollama_module.Client = Mock(return_value=ollama_client)

        with patch.dict(
            sys.modules,
            {"google": google_module, "ollama": ollama_module},
        ):
            gemini_text = ai_runtime._call_provider(
                {
                    "provider": "gemini",
                    "model": "gemini-test",
                    "temperature": 0,
                    "api_key": "test-key",
                    "timeout_seconds": 2.5,
                },
                "test",
                "system",
            )
            ollama_text = ai_runtime._call_provider(
                {
                    "provider": "ollama",
                    "model": "ollama-test",
                    "temperature": 0,
                    "api_key": None,
                    "timeout_seconds": 3.5,
                },
                "test",
                "system",
            )

        self.assertEqual(gemini_text, "OK")
        self.assertEqual(ollama_text, "OK")
        gemini_http_options = fake_genai.Client.call_args.kwargs["http_options"]
        self.assertEqual(gemini_http_options.timeout, 2500)
        self.assertEqual(
            ollama_module.Client.call_args.kwargs["timeout"],
            3.5,
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from preference_agent.backends import HeuristicBackend, ModelRequestError, OpenAICompatibleBackend
from preference_agent.engine import PreferenceEngine
from preference_agent.hooks import PreferenceHookManager
from preference_agent.mcp_server import handle_request
from preference_agent.models import PreferenceRecord
from preference_agent.store import MarkdownPreferenceStore


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _FailingMergeBackend(HeuristicBackend):
    def merge_decision(self, candidate: PreferenceRecord, existing: list[PreferenceRecord]) -> dict[str, object]:
        raise RuntimeError("model request failed: HTTP 400 invalid temperature")


class _ProviderBodyFailingMergeBackend(HeuristicBackend):
    def merge_decision(self, candidate: PreferenceRecord, existing: list[PreferenceRecord]) -> dict[str, object]:
        raise ModelRequestError(
            'model request failed: HTTP 400 {"error":"secret provider detail"}',
            status_code=400,
            response_body='{"error":"secret provider detail"}',
        )


class ModelCompatibilityTests(unittest.TestCase):
    def test_openai_backend_retries_without_temperature_after_invalid_temperature(self) -> None:
        requests: list[dict[str, object]] = []

        def fake_urlopen(request: object, timeout: float) -> _FakeResponse:
            del timeout
            body = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
            requests.append(body)
            if len(requests) == 1:
                raise urllib.error.HTTPError(
                    request.full_url,  # type: ignore[attr-defined]
                    400,
                    "Bad Request",
                    hdrs=None,
                    fp=io.BytesIO(b'{"error":"invalid temperature"}'),
                )
            return _FakeResponse({"choices": [{"message": {"content": "{\"ok\": true}"}}]})

        env = {
            "PREFERENCE_MODEL_BASE_URL": "https://example.test/v1",
            "PREFERENCE_MODEL_API_KEY": "test-key",
            "PREFERENCE_MODEL_NAME": "strict-model",
            "PREFERENCE_MODEL_TEMPERATURE": "0.1",
            "PREFERENCE_MODEL_SEND_TEMPERATURE": "true",
        }
        with patch.dict(os.environ, env, clear=False), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = OpenAICompatibleBackend()._chat_json("system", "user")

        self.assertEqual(result, {"ok": True})
        self.assertIn("temperature", requests[0])
        self.assertNotIn("temperature", requests[1])

    def test_openai_backend_retries_without_response_format_after_unsupported_response_format(self) -> None:
        requests: list[dict[str, object]] = []

        def fake_urlopen(request: object, timeout: float) -> _FakeResponse:
            del timeout
            body = json.loads(request.data.decode("utf-8"))  # type: ignore[attr-defined]
            requests.append(body)
            if len(requests) == 1:
                raise urllib.error.HTTPError(
                    request.full_url,  # type: ignore[attr-defined]
                    400,
                    "Bad Request",
                    hdrs=None,
                    fp=io.BytesIO(b'{"error":"unsupported response_format"}'),
                )
            return _FakeResponse({"choices": [{"message": {"content": "{\"ok\": true}"}}]})

        env = {
            "PREFERENCE_MODEL_BASE_URL": "https://example.test/v1",
            "PREFERENCE_MODEL_API_KEY": "test-key",
            "PREFERENCE_MODEL_NAME": "strict-model",
            "PREFERENCE_MODEL_SEND_TEMPERATURE": "false",
            "PREFERENCE_MODEL_RESPONSE_FORMAT": "json_object",
        }
        with patch.dict(os.environ, env, clear=False), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = OpenAICompatibleBackend()._chat_json("system", "user")

        self.assertEqual(result, {"ok": True})
        self.assertIn("response_format", requests[0])
        self.assertNotIn("response_format", requests[1])

    def test_openai_backend_can_omit_temperature_by_configuration(self) -> None:
        requests: list[dict[str, object]] = []

        def fake_urlopen(request: object, timeout: float) -> _FakeResponse:
            del timeout
            requests.append(json.loads(request.data.decode("utf-8")))  # type: ignore[attr-defined]
            return _FakeResponse({"choices": [{"message": {"content": "{\"ok\": true}"}}]})

        env = {
            "PREFERENCE_MODEL_BASE_URL": "https://example.test/v1",
            "PREFERENCE_MODEL_API_KEY": "test-key",
            "PREFERENCE_MODEL_NAME": "strict-model",
            "PREFERENCE_MODEL_TEMPERATURE": "0.1",
            "PREFERENCE_MODEL_SEND_TEMPERATURE": "false",
        }
        with patch.dict(os.environ, env, clear=False), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = OpenAICompatibleBackend()._chat_json("system", "user")

        self.assertEqual(result, {"ok": True})
        self.assertNotIn("temperature", requests[0])

    def test_openai_backend_can_omit_response_format_by_configuration(self) -> None:
        requests: list[dict[str, object]] = []

        def fake_urlopen(request: object, timeout: float) -> _FakeResponse:
            del timeout
            requests.append(json.loads(request.data.decode("utf-8")))  # type: ignore[attr-defined]
            return _FakeResponse({"choices": [{"message": {"content": "{\"ok\": true}"}}]})

        env = {
            "PREFERENCE_MODEL_BASE_URL": "https://example.test/v1",
            "PREFERENCE_MODEL_API_KEY": "test-key",
            "PREFERENCE_MODEL_NAME": "strict-model",
            "PREFERENCE_MODEL_RESPONSE_FORMAT": "omit",
        }
        with patch.dict(os.environ, env, clear=False), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = OpenAICompatibleBackend()._chat_json("system", "user")

        self.assertEqual(result, {"ok": True})
        self.assertNotIn("response_format", requests[0])

    def test_action_hook_uses_fallback_backend_when_merge_rejects_temperature(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Verification",
                        applies_to="After code changes",
                        preference="After code changes, run relevant tests by default.",
                        status="active",
                        confidence="high",
                    )
                ]
            )
            engine = PreferenceEngine(
                MarkdownPreferenceStore(store),
                backend=_FailingMergeBackend(),
                fallback_backend=HeuristicBackend(),
            )
            hooks = PreferenceHookManager(engine, hooks_dir=root / "hooks")

            result = hooks.on_action_executed(
                action="run_tests_before_done",
                result="pytest passed before reporting completion",
                agent="codex",
                session_id="s-model-compat",
            )

            self.assertTrue(result["captured"])
            backend_errors = result["capture"]["backend_errors"]
            self.assertEqual(len(backend_errors), 1)
            self.assertEqual(backend_errors[0]["stage"], "merge_decision")
            self.assertIn("invalid temperature", backend_errors[0]["message"])

    def test_mcp_action_hook_returns_degraded_payload_instead_of_jsonrpc_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Verification",
                        applies_to="After code changes",
                        preference="After code changes, run relevant tests by default.",
                        status="active",
                        confidence="high",
                    )
                ]
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=_FailingMergeBackend())

            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "hook_action_executed",
                        "arguments": {
                            "agent": "codex",
                            "session_id": "s-model-compat",
                            "action": "run_tests_before_done",
                            "result": "pytest passed before reporting completion",
                        },
                    },
                },
                engine,
            )

            self.assertNotIn("error", response)
            payload = json.loads(response["result"]["content"][0]["text"])
            self.assertTrue(payload["ok"])
            self.assertFalse(payload["captured"])
            self.assertTrue(payload["degraded"])
            self.assertEqual(payload["reason"], "capture_degraded")
            self.assertIn("invalid temperature", payload["error"]["summary"])
            self.assertNotIn("message", payload["error"])

    def test_mcp_compact_action_hook_redacts_provider_error_body(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Verification",
                        applies_to="After code changes",
                        preference="After code changes, run relevant tests by default.",
                        status="active",
                        confidence="high",
                    )
                ]
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=_ProviderBodyFailingMergeBackend())

            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "hook_action_executed",
                        "arguments": {
                            "agent": "codex",
                            "session_id": "s-redacted",
                            "action": "run_tests_before_done",
                            "result": "pytest passed before reporting completion",
                        },
                    },
                },
                engine,
            )

            payload = json.loads(response["result"]["content"][0]["text"])
            self.assertEqual(payload["error"]["status_code"], 400)
            self.assertEqual(payload["error"]["summary"], "ModelRequestError: HTTP 400")
            self.assertNotIn("message", payload["error"])
            self.assertNotIn("secret provider detail", json.dumps(payload, ensure_ascii=False))

    def test_mcp_action_hook_remains_non_error_when_degraded_logging_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Verification",
                        applies_to="After code changes",
                        preference="After code changes, run relevant tests by default.",
                        status="active",
                        confidence="high",
                    )
                ]
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=_FailingMergeBackend())

            with patch("preference_agent.hooks.log_injection_event", side_effect=OSError("log path denied")):
                response = handle_request(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "hook_action_executed",
                            "arguments": {
                                "agent": "codex",
                                "session_id": "s-log-failure",
                                "action": "run_tests_before_done",
                                "result": "pytest passed before reporting completion",
                            },
                        },
                    },
                    engine,
                )

            self.assertNotIn("error", response)
            payload = json.loads(response["result"]["content"][0]["text"])
            self.assertTrue(payload["degraded"])
            self.assertEqual(payload["reason"], "capture_degraded")
            self.assertEqual(payload["log_error"]["summary"], "log path denied")


if __name__ == "__main__":
    unittest.main()

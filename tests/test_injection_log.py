from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from preference_agent.backends import HeuristicBackend
from preference_agent.engine import PreferenceEngine
from preference_agent.hooks import PreferenceHookManager
from preference_agent.injection_log import default_injection_log, log_injection_event, read_injection_log
from preference_agent.mcp_server import handle_request
from preference_agent.models import PreferenceRecord
from preference_agent.store import MarkdownPreferenceStore
from preference_agent.ui.server import build_manifesto


class InjectionLogTests(unittest.TestCase):
    def test_engine_decide_writes_injection_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Tests",
                        applies_to="After the agent changes code",
                        preference="After code changes, run relevant tests by default.",
                        status="active",
                        confidence="high",
                    )
                ]
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())

            decision = engine.decide("Ready to reply to the user after completing code", context={"session_id": "s1"}, agent="codex")
            events = read_injection_log(store)

            self.assertEqual(decision["decision"], "apply")
            self.assertEqual(events[0]["hook"], "decide")
            self.assertEqual(events[0]["agent"], "codex")
            self.assertEqual(events[0]["session_id"], "s1")
            self.assertTrue(events[0]["injected"])
            self.assertGreaterEqual(events[0]["matched_count"], 1)

    def test_injection_log_preserves_full_agent_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            long_instruction = (
                "Apply these user preferences before replying or acting: "
                + "Run relevant verification before reporting completion. " * 40
                + "FINAL_COMPLETE_MARKER."
            )

            log_injection_event(
                store_path=store,
                hook="decide",
                agent="codex",
                decision={
                    "decision": "apply",
                    "matched_preferences": [],
                    "agent_instruction": long_instruction,
                },
            )
            event = read_injection_log(store)[0]

            self.assertEqual(event["agent_instruction"], long_instruction)
            self.assertTrue(event["agent_instruction"].endswith("FINAL_COMPLETE_MARKER."))

    def test_mcp_preference_hook_is_compact_by_default_and_debuggable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Tests",
                        applies_to="After the agent changes code",
                        preference="After code changes, run relevant tests by default.",
                        status="active",
                        confidence="high",
                    )
                ]
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())

            compact = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "hook_user_message",
                        "arguments": {
                            "agent": "codex",
                            "session_id": "s1",
                            "message": "Ready to reply to the user after completing code",
                        },
                    },
                },
                engine,
            )
            compact_text = compact["result"]["content"][0]["text"]
            compact_payload = json.loads(compact_text)

            self.assertEqual(compact_payload["decision"], "apply")
            self.assertTrue(compact_payload["injected"])
            self.assertIn("After code changes", compact_payload["agent_instruction"])
            self.assertIn("matched_preferences", compact_payload)
            self.assertEqual(set(compact_payload["matched_preferences"][0]), {"id", "title", "instruction"})
            self.assertNotIn("ok", compact_payload)
            self.assertNotIn("tool", compact_payload)
            self.assertNotIn("enabled", compact_payload)
            self.assertNotIn("hook", compact_payload)
            self.assertNotIn("agent", compact_payload)
            self.assertNotIn("session_id", compact_payload)
            self.assertNotIn("debug_ref", compact_payload)
            self.assertNotIn("gate_summary", compact_payload)
            self.assertNotIn("confidence_factors", compact_text)
            self.assertNotIn("confidence_reasons", compact_text)
            self.assertNotIn('"decision": {', compact_text)

            debug = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "hook_user_message",
                        "arguments": {
                            "agent": "codex",
                            "session_id": "s1",
                            "message": "Ready to reply to the user after completing code",
                            "include_debug": True,
                        },
                    },
                },
                engine,
            )
            debug_text = debug["result"]["content"][0]["text"]
            debug_payload = json.loads(debug_text)

            self.assertIsInstance(debug_payload["decision"], dict)
            self.assertIn("confidence_factors", debug_text)

    def test_mcp_preference_hook_no_preference_visible_payload_is_minimal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Tests",
                        applies_to="After the agent changes code",
                        preference="After code changes, run relevant tests by default.",
                        status="active",
                        confidence="high",
                    )
                ]
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())

            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "hook_user_message",
                        "arguments": {
                            "agent": "codex",
                            "session_id": "s1",
                            "message": "What is the weather?",
                        },
                    },
                },
                engine,
            )
            payload = json.loads(response["result"]["content"][0]["text"])

            self.assertEqual(payload, {"decision": "no_preference", "injected": False})

    def test_mcp_session_start_omits_full_prewarm_metadata_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Tests",
                        applies_to="After the agent changes code",
                        preference="After code changes, run relevant tests by default.",
                        status="active",
                        confidence="high",
                    )
                ]
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())

            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "hook_session_start",
                        "arguments": {
                            "agent": "codex",
                            "session_id": "s1",
                            "task": "Ready to reply to the user after completing code",
                            "output_dir": str(root / "inject"),
                        },
                    },
                },
                engine,
            )
            text = response["result"]["content"][0]["text"]
            payload = json.loads(text)

            self.assertEqual(payload["decision"], "apply")
            self.assertTrue(payload["injected"])
            self.assertNotIn("session_cache_file", payload)
            self.assertNotIn("fallback_file", payload)
            self.assertNotIn('"prewarm"', text)
            self.assertNotIn("confidence_factors", text)

    def test_hooks_write_observable_session_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Tests",
                        applies_to="After the agent changes code",
                        preference="After code changes, run relevant tests by default.",
                        status="active",
                        confidence="high",
                    )
                ]
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
            manager = PreferenceHookManager(engine, hooks_dir=root / ".hooks")

            manager.on_session_start(agent="codex", session_id="s1", task="Ready to reply to the user after completing code")
            manager.on_user_message(message="Ready to reply to the user after completing code", agent="codex", session_id="s1")
            manager.on_turn_complete(
                user_message="From now on, give the conclusion first.",
                assistant_response="Acknowledged.",
                agent="codex",
                session_id="s1",
            )

            events = read_injection_log(store)
            hooks = [item["hook"] for item in events]
            self.assertIn("session_start", hooks)
            self.assertIn("user_message", hooks)
            self.assertIn("prewarm", hooks)
            self.assertIn("turn_complete", hooks)
            self.assertTrue(any(item["hook"] == "session_start" and item["source"] == "hook" and item["injected"] for item in events))
            self.assertTrue(any(item["hook"] == "user_message" and item["source"] == "hook" and item["injected"] for item in events))
            self.assertFalse(any(item["hook"] in {"session_start", "user_message"} and item["source"] == "engine" for item in events))

    def test_mcp_session_start_and_user_message_generate_injection_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Tests",
                        applies_to="After the agent changes code",
                        preference="After code changes, run relevant tests by default.",
                        status="active",
                        confidence="high",
                    )
                ]
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())

            handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "hook_session_start",
                        "arguments": {"agent": "codex", "session_id": "s1", "task": "Ready to reply to the user after completing code"},
                    },
                },
                engine,
            )
            handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "hook_user_message",
                        "arguments": {"agent": "codex", "session_id": "s1", "message": "Ready to reply to the user after completing code"},
                    },
                },
                engine,
            )

            self.assertTrue(default_injection_log(store).exists())
            events = read_injection_log(store)
            self.assertTrue(any(item["hook"] == "session_start" and item["source"] == "hook" for item in events))
            self.assertTrue(any(item["hook"] == "user_message" and item["source"] == "hook" for item in events))

    def test_manifesto_exposes_injection_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Tests",
                        applies_to="After the agent changes code",
                        preference="After code changes, run relevant tests by default.",
                        status="active",
                        confidence="high",
                    )
                ]
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
            engine.decide("Ready to reply to the user after completing code", agent="codex")

            manifesto = build_manifesto(store_path=store)

            self.assertIn("injection_log", manifesto)
            self.assertEqual(manifesto["injection_log"]["summary"]["total"], 1)
            self.assertEqual(manifesto["injection_log"]["items"][0]["agent"], "codex")


if __name__ == "__main__":
    unittest.main()

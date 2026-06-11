from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preference_agent.backends import HeuristicBackend
from preference_agent.engine import PreferenceEngine
from preference_agent.hooks import PreferenceHookManager
from preference_agent.injection_log import read_injection_log
from preference_agent.kimi_hooks import handle_kimi_hook
from preference_agent.mcp_server import handle_request
from preference_agent.models import PreferenceRecord
from preference_agent.store import MarkdownPreferenceStore


def _isolated_fitting_env(root: Path):
    return patch.dict(
        os.environ,
        {
            "PREFERENCE_UI_DIR": str(root / ".ui"),
            "DATAILOR_FITTING_DIR": str(root / ".fitting"),
        },
        clear=False,
    )


class PreferenceHookTests(unittest.TestCase):
    def test_turn_complete_buffers_and_flushes_to_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
            hooks = PreferenceHookManager(engine, hooks_dir=root / "hooks", max_turns=2)

            with _isolated_fitting_env(root):
                first = hooks.on_turn_complete(
                    user_message="From now on, give me the conclusion first.",
                    assistant_response="Acknowledged.",
                    agent="codex",
                    session_id="s1",
                )
                self.assertTrue(first["buffered"])
                self.assertNotIn("flush", first)

                second = hooks.on_turn_complete(
                    user_message="From now on, run relevant tests by default after code changes.",
                    assistant_response="Acknowledged.",
                    agent="codex",
                    session_id="s1",
                )

            self.assertTrue(second["flush"]["flushed"])
            records = MarkdownPreferenceStore(store).load()
            self.assertGreaterEqual(len(records), 1)
            self.assertFalse(any((root / "hooks" / "turn-buffers").glob("*.json")))

    def test_session_end_flushes_remaining_turn_buffer(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
            hooks = PreferenceHookManager(engine, hooks_dir=root / "hooks", max_turns=5)

            with _isolated_fitting_env(root):
                hooks.on_turn_complete(
                    user_message="From now on, reply to me in English by default.",
                    assistant_response="Acknowledged.",
                    agent="codex",
                    session_id="s2",
                )
                ended = hooks.on_session_end(agent="codex", session_id="s2")

            self.assertTrue(ended["buffer_flush"]["flushed"])
            self.assertTrue(MarkdownPreferenceStore(store).load())

    def test_turn_and_session_fitting_triggers_run_synchronously(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
            hooks = PreferenceHookManager(engine, hooks_dir=root / "hooks", max_turns=1)

            with _isolated_fitting_env(root), patch(
                "preference_agent.hooks.maybe_run_fitting_after_capture",
                return_value={"ok": True, "triggered": False},
            ) as fitting:
                hooks.on_turn_complete(
                    user_message="From now on, give me the conclusion first.",
                    assistant_response="Acknowledged.",
                    agent="codex",
                    session_id="s-background",
                )
                self.assertFalse(fitting.call_args.kwargs["background"])

                fitting.reset_mock()
                hooks.on_session_end(agent="codex", session_id="s-background")
                self.assertFalse(fitting.call_args.kwargs["background"])

    def test_action_hook_creates_pending_behavior_preference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
            hooks = PreferenceHookManager(engine, hooks_dir=root / "hooks")

            result = hooks.on_action_executed(
                action="run_tests_before_done",
                result="pytest passed before reporting completion",
                agent="codex",
                session_id="s3",
            )

            self.assertTrue(result["captured"])
            records = MarkdownPreferenceStore(store).load()
            self.assertEqual(records[0].status, "needs_review")
            self.assertIn("verification", records[0].preference.casefold())

    def test_mcp_exposes_turn_hook_tool(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = PreferenceEngine(MarkdownPreferenceStore(root / "prefs.md"), backend=HeuristicBackend())
            tools = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, engine)
            self.assertIn("hook_turn_complete", json.dumps(tools, ensure_ascii=False))

            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "hook_turn_complete",
                        "arguments": {
                            "agent": "codex",
                            "session_id": "s4",
                            "user_message": "From now on, give the conclusion first.",
                            "assistant_response": "Acknowledged.",
                            "dry_run": True,
                        },
                    },
                },
                engine,
            )
            self.assertIn("buffered", response["result"]["content"][0]["text"])

    def test_mcp_action_hook_compacts_capture_metadata_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = PreferenceEngine(MarkdownPreferenceStore(root / "prefs.md"), backend=HeuristicBackend())

            compact = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "hook_action_executed",
                        "arguments": {
                            "agent": "codex",
                            "session_id": "s5",
                            "action": "run_tests_before_done",
                            "result": "pytest passed before reporting completion",
                        },
                    },
                },
                engine,
            )
            compact_text = compact["result"]["content"][0]["text"]
            compact_payload = json.loads(compact_text)

            self.assertTrue(compact_payload["captured"])
            self.assertIn("capture", compact_payload)
            self.assertIn("added_count", compact_payload["capture"])
            self.assertNotIn("executive_summary_file", compact_text)

            debug = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "hook_action_executed",
                        "arguments": {
                            "agent": "codex",
                            "session_id": "s5",
                            "action": "run_tests_before_done",
                            "result": "pytest passed before reporting completion",
                            "include_debug": True,
                        },
                    },
                },
                engine,
            )
            self.assertIn("executive_summary_file", debug["result"]["content"][0]["text"])

    def test_kimi_hook_runner_maps_lifecycle_events_to_datailor_hooks(self) -> None:
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
            env = {
                "PREFERENCE_HOOKS_DIR": str(root / ".hooks"),
                "PREFERENCE_UI_DIR": str(root / ".ui"),
                "DATAILOR_FITTING_DIR": str(root / ".fitting"),
            }
            with patch.dict(os.environ, env, clear=False):
                handle_kimi_hook(
                    {
                        "hook_event_name": "SessionStart",
                        "session_id": "k1",
                        "cwd": str(root),
                        "source": "startup",
                    },
                    store_path=store,
                )
                handle_kimi_hook(
                    {
                        "hook_event_name": "UserPromptSubmit",
                        "session_id": "k1",
                        "cwd": str(root),
                        "prompt": "Ready to reply to the user after completing code. From now on, give the conclusion first.",
                    },
                    store_path=store,
                )
                handle_kimi_hook(
                    {
                        "hook_event_name": "PostToolUse",
                        "session_id": "k1",
                        "cwd": str(root),
                        "tool_name": "Shell",
                        "tool_output": "pytest passed before reporting completion",
                    },
                    store_path=store,
                )
                handle_kimi_hook(
                    {
                        "hook_event_name": "Stop",
                        "session_id": "k1",
                        "cwd": str(root),
                    },
                    store_path=store,
                )

            events = read_injection_log(store)
            hooks = [item["hook"] for item in events]
            self.assertIn("session_start", hooks)
            self.assertIn("user_message", hooks)
            self.assertIn("action_executed", hooks)
            self.assertIn("turn_complete", hooks)
            self.assertTrue(any(item["agent"] == "kimi" for item in events))


if __name__ == "__main__":
    unittest.main()

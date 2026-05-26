from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from preference_agent.backends import HeuristicBackend
from preference_agent.engine import PreferenceEngine
from preference_agent.hooks import PreferenceHookManager
from preference_agent.mcp_server import handle_request
from preference_agent.store import MarkdownPreferenceStore


class PreferenceHookTests(unittest.TestCase):
    def test_turn_complete_buffers_and_flushes_to_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
            hooks = PreferenceHookManager(engine, hooks_dir=root / "hooks", max_turns=2)

            first = hooks.on_turn_complete(
                user_message="以后默认先给我结论。",
                assistant_response="好的。",
                agent="codex",
                session_id="s1",
            )
            self.assertTrue(first["buffered"])
            self.assertNotIn("flush", first)

            second = hooks.on_turn_complete(
                user_message="以后代码改完默认跑相关测试。",
                assistant_response="收到。",
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

            hooks.on_turn_complete(
                user_message="以后默认回复我中文。",
                assistant_response="好的。",
                agent="codex",
                session_id="s2",
            )
            ended = hooks.on_session_end(agent="codex", session_id="s2")

            self.assertTrue(ended["buffer_flush"]["flushed"])
            self.assertTrue(MarkdownPreferenceStore(store).load())

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
            self.assertIn("验证", records[0].preference)

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
                            "user_message": "以后默认先给结论。",
                            "assistant_response": "好的。",
                            "dry_run": True,
                        },
                    },
                },
                engine,
            )
            self.assertIn("buffered", response["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()

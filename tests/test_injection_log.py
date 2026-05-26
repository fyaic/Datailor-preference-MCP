from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from preference_agent.backends import HeuristicBackend
from preference_agent.engine import PreferenceEngine
from preference_agent.hooks import PreferenceHookManager
from preference_agent.injection_log import default_injection_log, read_injection_log
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
                        title="测试",
                        applies_to="当 agent 修改代码后",
                        preference="代码改动完成后默认运行相关测试。",
                        status="active",
                        confidence="high",
                    )
                ]
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())

            decision = engine.decide("代码实现完成后准备回复用户", context={"session_id": "s1"}, agent="codex")
            events = read_injection_log(store)

            self.assertEqual(decision["decision"], "apply")
            self.assertEqual(events[0]["hook"], "decide")
            self.assertEqual(events[0]["agent"], "codex")
            self.assertEqual(events[0]["session_id"], "s1")
            self.assertTrue(events[0]["injected"])
            self.assertGreaterEqual(events[0]["matched_count"], 1)

    def test_hooks_write_observable_session_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="测试",
                        applies_to="当 agent 修改代码后",
                        preference="代码改动完成后默认运行相关测试。",
                        status="active",
                        confidence="high",
                    )
                ]
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
            manager = PreferenceHookManager(engine, hooks_dir=root / ".hooks")

            manager.on_session_start(agent="codex", session_id="s1", task="代码实现完成后准备回复用户")
            manager.on_user_message(message="代码实现完成后准备回复用户", agent="codex", session_id="s1")
            manager.on_turn_complete(
                user_message="以后默认先给结论。",
                assistant_response="收到。",
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
                        title="测试",
                        applies_to="当 agent 修改代码后",
                        preference="代码改动完成后默认运行相关测试。",
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
                        "arguments": {"agent": "codex", "session_id": "s1", "task": "代码实现完成后准备回复用户"},
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
                        "arguments": {"agent": "codex", "session_id": "s1", "message": "代码实现完成后准备回复用户"},
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
                        title="测试",
                        applies_to="当 agent 修改代码后",
                        preference="代码改动完成后默认运行相关测试。",
                        status="active",
                        confidence="high",
                    )
                ]
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
            engine.decide("代码实现完成后准备回复用户", agent="codex")

            manifesto = build_manifesto(store_path=store)

            self.assertIn("injection_log", manifesto)
            self.assertEqual(manifesto["injection_log"]["summary"]["total"], 1)
            self.assertEqual(manifesto["injection_log"]["items"][0]["agent"], "codex")


if __name__ == "__main__":
    unittest.main()

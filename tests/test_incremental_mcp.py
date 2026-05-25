from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from preference_agent.capture_runner import CaptureConfig
from preference_agent.engine import PreferenceEngine
from preference_agent.incremental import scan_incremental
from preference_agent.mcp_server import handle_request
from preference_agent.store import MarkdownPreferenceStore


class IncrementalMcpTests(unittest.TestCase):
    def test_incremental_scan_detects_changed_files_in_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "sessions"
            source.mkdir()
            session = source / "one.jsonl"
            session.write_text(json.dumps({"role": "user", "content": "以后默认先给我大纲。"}, ensure_ascii=False), encoding="utf-8")
            config = CaptureConfig(
                project_root=root,
                candidate_dir=root / "debug",
                checkpoint_dir=root / "state",
                store_path=root / "prefs.md",
                mode="recall-only",
            )
            result = scan_incremental(source, config, state_file=root / "incremental.json", dry_run=True)
            self.assertEqual(result.scanned_files, 1)
            self.assertEqual(len(result.changed_files), 1)
            self.assertFalse((root / "incremental.json").exists())

    def test_mcp_prewarm_and_feedback_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            store.write_text(
                "# 个人偏好\n\n## 已确认偏好\n\n- 当 agent 修改代码后，默认运行相关测试。\n\n## 待观察偏好\n\n暂无待观察偏好。\n",
                encoding="utf-8",
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store))
            tools = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, engine)
            tool_text = json.dumps(tools, ensure_ascii=False)
            self.assertIn("prewarm_preferences", tool_text)
            self.assertIn("report_preference_feedback", tool_text)
            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "prewarm_preferences",
                        "arguments": {
                            "agent": "codex",
                            "task": "代码写完后准备回复",
                            "output_dir": str(root / "inject"),
                        },
                    },
                },
                engine,
            )
            self.assertEqual(response["id"], 2)
            self.assertIn("session_cache_file", response["result"]["content"][0]["text"])
            feedback = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "report_preference_feedback",
                        "arguments": {
                            "feedback_type": "confirmation",
                            "user_feedback": "对，就是这样。",
                            "preference_text": "默认运行相关测试",
                        },
                    },
                },
                engine,
            )
            self.assertEqual(feedback["id"], 3)
            self.assertIn("feedback_log", feedback["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()

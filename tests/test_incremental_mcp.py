from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preference_agent.backends import HeuristicBackend
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
            session.write_text(json.dumps({"role": "user", "content": "From now on, give me an outline first."}), encoding="utf-8")
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
                "# Personal Preferences\n\n## Active Preferences\n\n- After the agent changes code, run relevant tests by default.\n\n## Observed Preferences\n\nNo observed preferences yet.\n",
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
                            "task": "Ready to reply after finishing code",
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
                            "user_feedback": "Yes, exactly.",
                            "preference_text": "run relevant tests by default",
                        },
                    },
                },
                engine,
            )
            self.assertEqual(feedback["id"], 3)
            self.assertIn("feedback_log", feedback["result"]["content"][0]["text"])

    def test_mcp_feedback_updates_preference_store_when_target_is_known(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            store.write_text(
                "# Personal Preferences\n\n## Active Preferences\n\nNo active preferences yet.\n\n## Observed Preferences\n\n- Give the outline first by default.\n",
                encoding="utf-8",
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store))
            item = MarkdownPreferenceStore(store).load()[0]

            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "report_preference_feedback",
                        "arguments": {
                            "feedback_type": "confirmation",
                            "user_feedback": "Yes, exactly.",
                            "preference_id": item.id,
                            "preference_text": item.preference,
                        },
                    },
                },
                engine,
            )

            text = response["result"]["content"][0]["text"]
            self.assertIn("preference_update", text)
            self.assertEqual(MarkdownPreferenceStore(store).load()[0].status, "active")

    def test_mcp_preferences_prompt_exposes_slash_command_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            engine = PreferenceEngine(MarkdownPreferenceStore(store))

            init = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, engine)
            self.assertIn("prompts", init["result"]["capabilities"])

            prompts = handle_request({"jsonrpc": "2.0", "id": 2, "method": "prompts/list"}, engine)
            prompt_names = [item["name"] for item in prompts["result"]["prompts"]]
            self.assertIn("preferences", prompt_names)

            prompt = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "prompts/get",
                    "params": {
                        "name": "preferences",
                        "arguments": {"open_browser": False, "port": 8080},
                    },
                },
                engine,
            )
            text = prompt["result"]["messages"][0]["content"]["text"]
            self.assertIn("open_preference_panel", text)
            self.assertIn("port=8080", text)

    def test_mcp_discovers_agents_and_auto_captures_when_source_is_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            history = home / ".claude" / "history.jsonl"
            history.parent.mkdir(parents=True)
            history.write_text(
                json.dumps({"role": "user", "content": "From now on, give the conclusion first."}) + "\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "PREFERENCE_DISCOVERY_HOME": str(home),
                    "PREFERENCE_CHECKPOINT_DIR": str(root / "state"),
                    "PREFERENCE_CANDIDATE_DIR": str(root / "debug"),
                },
                clear=False,
            ):
                engine = PreferenceEngine(MarkdownPreferenceStore(root / "prefs.md"))
                tools = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, engine)
                tool_text = json.dumps(tools, ensure_ascii=False)
                self.assertIn("discover_agents", tool_text)

                discovered = handle_request(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "discover_agents", "arguments": {"agent": "claude"}},
                    },
                    engine,
                )
                self.assertIn("Claude", discovered["result"]["content"][0]["text"])

                captured = handle_request(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "capture_preferences_from_session",
                            "arguments": {"agent": "claude", "dry_run": True},
                        },
                    },
                    engine,
                )
                text = captured["result"]["content"][0]["text"]
            self.assertIn("changed_files", text)
            self.assertIn(str(history).replace("\\", "\\\\"), text)

    def test_mcp_decision_auto_scans_discovered_history_when_store_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            history = home / ".codex" / "history.jsonl"
            history.parent.mkdir(parents=True)
            history.write_text(
                json.dumps({"role": "user", "content": "From now on, give the conclusion first."}) + "\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "PREFERENCE_DISCOVERY_HOME": str(home),
                    "PREFERENCE_CHECKPOINT_DIR": str(root / "state"),
                    "PREFERENCE_CANDIDATE_DIR": str(root / "debug"),
                    "PREFERENCE_AUTO_DISCOVERY": "1",
                    "PREFERENCE_AUTO_DISCOVERY_MODE": "recall-only",
                },
                clear=False,
            ):
                store = root / "prefs.md"
                engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
                response = handle_request(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {
                            "name": "get_preference_decision",
                            "arguments": {"agent": "codex", "task": "Ready to reply to the user"},
                        },
                    },
                    engine,
                )
                text = response["result"]["content"][0]["text"]
                self.assertIn("auto_discovery", text)
                self.assertIn("changed_files", text)
                self.assertTrue(MarkdownPreferenceStore(store).load())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from preference_agent.backends import HeuristicBackend
from preference_agent.capture_runner import CaptureConfig, CaptureRunner, _extract_messages, _message_from_object


class CaptureRunnerTests(unittest.TestCase):
    def test_openclaw_message_record_extracts_only_user_text_parts(self) -> None:
        record = {
            "type": "message",
            "message": {
                "role": "user",
                "content": [
                    {"type": "text", "text": "From now on, keep project codenames unchanged."},
                    {"type": "toolResult", "text": "tool output must stay out"},
                    {"type": "image", "url": "file:///tmp/image.png"},
                ],
            },
            "session_id": "openclaw-session",
        }

        messages = _extract_messages(record)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"], "From now on, keep project codenames unchanged.")
        self.assertEqual(messages[0]["session_id"], "openclaw-session")
        self.assertNotIn("tool output", messages[0]["content"])
        self.assertNotIn("image.png", messages[0]["content"])

    def test_openclaw_assistant_message_is_not_defaulted_to_user(self) -> None:
        record = {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "I will remember that."}],
            },
        }

        messages = _extract_messages(record)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "assistant")

    def test_content_only_jsonl_is_treated_as_user_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "kimi-user-history.jsonl"
            source.write_text(
                json.dumps({"content": "From now on, give me the conclusion first, then expand."}) + "\n",
                encoding="utf-8",
            )
            config = CaptureConfig(
                project_root=root,
                candidate_dir=root / "debug",
                checkpoint_dir=root / "state",
                store_path=root / "prefs.md",
                mode="recall-only",
            )
            result = CaptureRunner(config).run(source)
            self.assertEqual(result.user_messages_seen, 1)
            self.assertGreaterEqual(result.preferences_added, 1)
            self.assertIn("conclusion", (root / "prefs.md").read_text(encoding="utf-8").casefold())

    def test_one_off_linear_task_is_not_promoted_to_preference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "kimi-user-history.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "content": "In Linear, no issue from the last week should remain in the K-N Knowledge project; bulk move all of them to the T-B Knowledge Base project."
                    },
                )
                + "\n",
                encoding="utf-8",
            )
            config = CaptureConfig(
                project_root=root,
                candidate_dir=root / "debug",
                checkpoint_dir=root / "state",
                store_path=root / "prefs.md",
                mode="recall-only",
            )
            result = CaptureRunner(config).run(source)
            self.assertEqual(result.user_messages_seen, 1)
            self.assertEqual(result.preferences_added, 0)
            pref_text = (root / "prefs.md").read_text(encoding="utf-8")
            self.assertNotIn("K-N Knoledge", pref_text)

    def test_recall_only_streams_user_inputs_and_agent_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "AGENTS.md").write_text(
                "# Rules\n\n- After writing to an external system, read back the content and verify it is intact.\n- Ordinary note without a preference signal.\n",
                encoding="utf-8",
            )
            source = root / "history.jsonl"
            source.write_text(
                "\n".join(
                    [
                        json.dumps({"role": "assistant", "content": "The code is changed. Should I run tests?"}),
                        json.dumps({"role": "user", "content": "From now on, run relevant tests by default after code changes; do not ask."}),
                        json.dumps({"role": "assistant", "content": "The weather is nice."}),
                        json.dumps({"role": "user", "content": "What is the weather today?"}),
                        json.dumps(
                            {
                                "messages": [
                                    {"role": "assistant", "content": "Should I organize this into documentation?"},
                                    {"role": "user", "content": "Yes. For solution design and complex breakdowns, proactively ask whether to save the conclusions."},
                                ]
                            },
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            config = CaptureConfig(
                project_root=root,
                candidate_dir=root / "data" / "candidates",
                checkpoint_dir=root / "data" / "checkpoints",
                store_path=root / "data" / "personal-preferences.md",
                mode="recall-only",
                max_minutes=1,
            )
            result = CaptureRunner(config).run(source)
            self.assertGreaterEqual(result.candidates_written, 3)
            self.assertEqual(result.bad_lines, 0)
            self.assertGreaterEqual(result.user_messages_seen, 3)
            pref_text = Path(result.store_file).read_text(encoding="utf-8")
            self.assertIn("read back", pref_text.casefold())
            self.assertIn("run relevant tests", pref_text.casefold())
            self.assertIn("ask whether to save", pref_text.casefold())
            self.assertNotIn("weather today", pref_text.casefold())
            self.assertEqual(result.candidate_file, "")
            self.assertEqual(result.summary_file, "")
            checkpoint = json.loads(Path(result.checkpoint_file).read_text(encoding="utf-8"))
            self.assertTrue(checkpoint["completed"])

    def test_wrapper_with_content_and_messages_prefers_inner_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "history.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "content": "summary wrapper text",
                        "messages": [
                            {"role": "assistant", "content": "Should I organize this into documentation?"},
                            {"role": "user", "content": "Yes. For solution design and complex breakdowns, proactively ask whether to save the conclusions."},
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            config = CaptureConfig(
                project_root=root,
                candidate_dir=root / "data" / "candidates",
                checkpoint_dir=root / "data" / "checkpoints",
                store_path=root / "data" / "personal-preferences.md",
                mode="recall-only",
                max_minutes=1,
            )
            result = CaptureRunner(config).run(source)
            # 应该提取内部 messages，而不是只返回 summary wrapper text
            self.assertGreaterEqual(result.user_messages_seen, 1)
            pref_text = Path(result.store_file).read_text(encoding="utf-8")
            self.assertIn("ask whether to save", pref_text.casefold())
            self.assertNotIn("summary wrapper text", pref_text.casefold())

    def test_type_kind_source_are_not_defaulted_to_user(self) -> None:
        # 验证 type/kind/source 被正确提取为 role，不会默认当作 user
        assistant_by_type = _message_from_object({"type": "assistant", "content": "I will do that."})
        self.assertIsNotNone(assistant_by_type)
        self.assertEqual(assistant_by_type["role"], "assistant")

        tool_by_kind = _message_from_object({"kind": "tool", "content": "Tool output here."})
        self.assertIsNotNone(tool_by_kind)
        self.assertEqual(tool_by_kind["role"], "tool")

        system_by_source = _message_from_object({"source": "system", "content": "System message."})
        self.assertIsNotNone(system_by_source)
        self.assertEqual(system_by_source["role"], "system")

        # 纯 content-only（Kimi JSONL）仍应正确识别为 user
        user_content_only = _message_from_object({"content": "From now on, give me the conclusion first."})
        self.assertIsNotNone(user_content_only)
        self.assertEqual(user_content_only["role"], "user")

        # type/kind/source 为 user 时应正确识别
        user_by_type = _message_from_object({"type": "user", "content": "Hello."})
        self.assertIsNotNone(user_by_type)
        self.assertEqual(user_by_type["role"], "user")

        # 含 messages 的 wrapper 应优先提取内部消息
        wrapper = {
            "content": "summary wrapper text",
            "messages": [
                {"role": "user", "content": "From now on, use tabs."},
            ],
        }
        extracted = _extract_messages(wrapper)
        self.assertEqual(len(extracted), 1)
        self.assertEqual(extracted[0]["role"], "user")
        self.assertEqual(extracted[0]["content"], "From now on, use tabs.")

        # 有 structural hint 但 role 无法识别时，不应默认 user（返回 None）
        unknown_type = _message_from_object({"type": None, "content": "Some text."})
        self.assertIsNone(unknown_type)

        empty_source = _message_from_object({"source": "", "content": "Some text."})
        self.assertIsNone(empty_source)

    def test_recall_extract_writes_extracted_records_without_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "history.jsonl"
            source.write_text(
                json.dumps(
                    {"role": "user", "content": "From now on, run relevant tests by default after code changes; do not ask."},
                )
                + "\n",
                encoding="utf-8",
            )
            config = CaptureConfig(
                project_root=root,
                candidate_dir=root / "data" / "candidates",
                checkpoint_dir=root / "data" / "checkpoints",
                store_path=root / "data" / "personal-preferences.md",
                mode="recall-extract",
                batch_size=1,
                max_minutes=1,
            )
            result = CaptureRunner(config, backend=HeuristicBackend()).run(source)
            self.assertGreaterEqual(result.candidates_written, 1)
            self.assertGreaterEqual(result.extracted_records_written, 1)
            pref_text = Path(result.store_file).read_text(encoding="utf-8")
            self.assertIn("run relevant tests", pref_text.casefold())
            self.assertEqual(result.extracted_file, "")


if __name__ == "__main__":
    unittest.main()

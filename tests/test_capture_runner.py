from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from preference_agent.backends import HeuristicBackend
from preference_agent.capture_runner import CaptureConfig, CaptureRunner


class CaptureRunnerTests(unittest.TestCase):
    def test_content_only_jsonl_is_treated_as_user_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "kimi-user-history.jsonl"
            source.write_text(
                json.dumps({"content": "以后默认先给我结论，再展开说明。"}, ensure_ascii=False) + "\n",
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
            self.assertIn("先给结论", (root / "prefs.md").read_text(encoding="utf-8"))

    def test_one_off_linear_task_is_not_promoted_to_preference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "kimi-user-history.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "content": "我的linear里面过去一周不应该出现任何K-N Knoledge这个项目的issue 应该全部给T-B 知识沉淀 那个项目 帮我批量修改。"
                    },
                    ensure_ascii=False,
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
                "# Rules\n\n- 必须回读确认中文正常。\n- 普通说明，不含偏好信号。\n",
                encoding="utf-8",
            )
            source = root / "history.jsonl"
            source.write_text(
                "\n".join(
                    [
                        json.dumps({"role": "assistant", "content": "代码改完了，要不要测试？"}, ensure_ascii=False),
                        json.dumps({"role": "user", "content": "以后代码改动默认跑相关测试，不要问。"}, ensure_ascii=False),
                        json.dumps({"role": "assistant", "content": "天气不错。"}, ensure_ascii=False),
                        json.dumps({"role": "user", "content": "今天天气如何？"}, ensure_ascii=False),
                        json.dumps(
                            {
                                "messages": [
                                    {"role": "assistant", "content": "要不要整理文档？"},
                                    {"role": "user", "content": "要，方案设计和复杂问题拆解要主动问我要不要沉淀。"},
                                ]
                            },
                            ensure_ascii=False,
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            config = CaptureConfig(
                project_root=root,
                candidate_dir=root / "data" / "candidates",
                checkpoint_dir=root / "data" / "checkpoints",
                store_path=root / "data" / "个人偏好.md",
                mode="recall-only",
                max_minutes=1,
            )
            result = CaptureRunner(config).run(source)
            self.assertGreaterEqual(result.candidates_written, 3)
            self.assertEqual(result.bad_lines, 0)
            self.assertGreaterEqual(result.user_messages_seen, 3)
            pref_text = Path(result.store_file).read_text(encoding="utf-8")
            self.assertIn("必须回读确认中文正常", pref_text)
            self.assertIn("默认运行相关测试", pref_text)
            self.assertIn("主动询问是否沉淀", pref_text)
            self.assertNotIn("今天天气如何", pref_text)
            self.assertEqual(result.candidate_file, "")
            self.assertEqual(result.summary_file, "")
            checkpoint = json.loads(Path(result.checkpoint_file).read_text(encoding="utf-8"))
            self.assertTrue(checkpoint["completed"])

    def test_recall_extract_writes_extracted_records_without_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "history.jsonl"
            source.write_text(
                json.dumps(
                    {"role": "user", "content": "以后代码改动默认跑相关测试，不要问。"},
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            config = CaptureConfig(
                project_root=root,
                candidate_dir=root / "data" / "candidates",
                checkpoint_dir=root / "data" / "checkpoints",
                store_path=root / "data" / "个人偏好.md",
                mode="recall-extract",
                batch_size=1,
                max_minutes=1,
            )
            result = CaptureRunner(config, backend=HeuristicBackend()).run(source)
            self.assertGreaterEqual(result.candidates_written, 1)
            self.assertGreaterEqual(result.extracted_records_written, 1)
            pref_text = Path(result.store_file).read_text(encoding="utf-8")
            self.assertIn("默认运行相关测试", pref_text)
            self.assertEqual(result.extracted_file, "")


if __name__ == "__main__":
    unittest.main()

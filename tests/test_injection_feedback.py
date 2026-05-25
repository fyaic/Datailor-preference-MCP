from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from preference_agent.feedback import feedback_report, record_feedback
from preference_agent.injection import MANAGED_START, prewarm_session, sync_injection_artifacts
from preference_agent.store import MarkdownPreferenceStore


class InjectionFeedbackTests(unittest.TestCase):
    def test_sync_injection_generates_files_and_managed_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            store.write_text(
                "\n".join(
                    [
                        "# 个人偏好",
                        "",
                        "## 已确认偏好",
                        "",
                        "- 当 agent 修改代码后，默认运行相关测试。",
                        "- 当写入包含中文的外部系统后，必须回读确认中文正常。",
                        "",
                        "## 待观察偏好",
                        "",
                        "暂无待观察偏好。",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            target = root / "AGENTS.md"
            result = sync_injection_artifacts(store, root / "inject", [target])
            self.assertTrue(Path(result.static_rules_file).exists())
            self.assertTrue(Path(result.fallback_json_file).exists())
            self.assertTrue(Path(result.fallback_md_file).exists())
            self.assertIn("默认运行相关测试", Path(result.static_rules_file).read_text(encoding="utf-8"))
            self.assertIn("必须回读确认中文正常", target.read_text(encoding="utf-8"))
            self.assertEqual(target.read_text(encoding="utf-8").count(MANAGED_START), 1)
            sync_injection_artifacts(store, root / "inject", [target])
            self.assertEqual(target.read_text(encoding="utf-8").count(MANAGED_START), 1)
            data = json.loads(Path(result.fallback_json_file).read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(data["fallback_rules"]), 1)

    def test_prewarm_session_writes_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                MarkdownPreferenceStore(root / "missing.md")._load_simple_bullets(
                    "# 个人偏好\n\n## 已确认偏好\n\n- 当 agent 修改代码后，默认运行相关测试。\n"
                )
            )
            result = prewarm_session(store, "代码实现完成后准备回复用户", agent="codex", output_dir=root / "inject")
            self.assertTrue(Path(result.session_cache_file).exists())
            self.assertIn("测试", Path(result.session_cache_file).read_text(encoding="utf-8"))
            self.assertTrue(result.agent_instruction)

    def test_feedback_log_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "feedback.jsonl"
            record_feedback(
                feedback_type="correction",
                preference_id="pref-1",
                preference_text="默认先给大纲",
                user_feedback="这次不用大纲，直接给完整方案。",
                log_path=log,
            )
            record_feedback(
                feedback_type="confirmation",
                preference_id="pref-1",
                user_feedback="对，就是这样。",
                log_path=log,
            )
            report = feedback_report(log)
            self.assertEqual(report["total"], 2)
            self.assertEqual(report["by_preference"][0]["preference"], "pref-1")


if __name__ == "__main__":
    unittest.main()

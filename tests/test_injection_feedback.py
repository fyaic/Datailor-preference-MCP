from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from preference_agent.feedback import feedback_report, record_feedback
from preference_agent.injection import MANAGED_START, prewarm_session, sync_injection_artifacts
from preference_agent.models import PreferenceRecord
from preference_agent.store import MarkdownPreferenceStore


class InjectionFeedbackTests(unittest.TestCase):
    def test_sync_injection_generates_files_and_managed_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            store.write_text(
                "\n".join(
                    [
                        "# Personal Preferences",
                        "",
                        "## Active Preferences",
                        "",
                        "- After the agent changes code, run relevant tests by default.",
                        "- After writing to an external system, read back and verify the content is intact.",
                        "",
                        "## Observed Preferences",
                        "",
                        "No observed preferences yet.",
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
            self.assertIn("run relevant tests", Path(result.static_rules_file).read_text(encoding="utf-8"))
            self.assertIn("read back", target.read_text(encoding="utf-8"))
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
                    "# Personal Preferences\n\n## Active Preferences\n\n- After the agent changes code, run relevant tests by default.\n"
                )
            )
            result = prewarm_session(store, "Ready to reply to the user after completing code", agent="codex", output_dir=root / "inject")
            self.assertTrue(Path(result.session_cache_file).exists())
            self.assertIn("tests", Path(result.session_cache_file).read_text(encoding="utf-8"))
            self.assertTrue(result.agent_instruction)

    def test_prewarm_session_applies_conflict_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Detailed replies",
                        applies_to="When replying to user questions",
                        preference="When replying to user questions, answer with detailed full explanations.",
                        status="active",
                        confidence="high",
                    ),
                    PreferenceRecord(
                        title="Concise replies",
                        applies_to="When replying to user questions",
                        preference="When replying to user questions, keep answers concise and short; avoid long-form explanations.",
                        status="active",
                        confidence="high",
                    ),
                ]
            )

            result = prewarm_session(store, "Please answer the user question.", agent="codex", output_dir=root / "inject")

            self.assertEqual(result.decision, "escalate")
            self.assertIn("resolve_preference_conflict", result.agent_instruction)
            self.assertTrue((root / ".asked_conflicts.jsonl").exists())

    def test_feedback_log_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "feedback.jsonl"
            record_feedback(
                feedback_type="correction",
                preference_id="pref-1",
                preference_text="Give the outline first by default",
                user_feedback="Do not use an outline this time; give the complete plan directly.",
                log_path=log,
            )
            record_feedback(
                feedback_type="confirmation",
                preference_id="pref-1",
                user_feedback="Yes, exactly.",
                log_path=log,
            )
            report = feedback_report(log)
            self.assertEqual(report["total"], 2)
            self.assertEqual(report["by_preference"][0]["preference"], "pref-1")


if __name__ == "__main__":
    unittest.main()

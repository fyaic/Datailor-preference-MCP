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

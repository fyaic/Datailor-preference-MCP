from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from preference_agent.backends import HeuristicBackend
from preference_agent.executive_summary import read_executive_summary, refresh_executive_summary
from preference_agent.models import PreferenceRecord
from preference_agent.store import MarkdownPreferenceStore


class ExecutiveSummaryTests(unittest.TestCase):
    def test_refresh_writes_readable_summary_from_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store_path = root / "prefs.md"
            record = PreferenceRecord(
                title="Verification",
                applies_to="Code changes",
                preference="After code changes, run relevant tests by default and explain if tests cannot run.",
                status="active",
                confidence="high",
            )
            MarkdownPreferenceStore(store_path).save([record])

            update = refresh_executive_summary(
                store_path,
                changed_records=[record],
                backend=HeuristicBackend(),
            )
            summary = read_executive_summary(store_path)

            self.assertTrue(update.updated)
            self.assertEqual(summary.status, "ready")
            self.assertIn("After code changes", summary.text)
            self.assertIn("系统目前理解到", summary.text)

    def test_refresh_skips_without_delta_unless_forced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store_path = root / "prefs.md"
            MarkdownPreferenceStore(store_path).save(
                [
                    PreferenceRecord(
                        title="Concise",
                        applies_to="Replies",
                        preference="Prefer concise replies.",
                        status="active",
                    )
                ]
            )

            skipped = refresh_executive_summary(store_path, backend=HeuristicBackend())
            forced = refresh_executive_summary(store_path, backend=HeuristicBackend(), force=True)

            self.assertFalse(skipped.updated)
            self.assertEqual(skipped.skipped_reason, "no_delta")
            self.assertTrue(forced.updated)


if __name__ == "__main__":
    unittest.main()

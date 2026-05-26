from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from preference_agent.models import PreferenceRecord
from preference_agent.snapshots import list_store_snapshots, restore_store_snapshot
from preference_agent.store import MarkdownPreferenceStore


class SnapshotTests(unittest.TestCase):
    def test_save_creates_pre_save_snapshot_for_existing_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store_path = root / "prefs.md"
            store = MarkdownPreferenceStore(store_path)

            store.save(
                [
                    PreferenceRecord(
                        title="Concise",
                        applies_to="Replies",
                        preference="Prefer concise replies.",
                        status="active",
                    )
                ]
            )
            self.assertEqual(list_store_snapshots(store_path), [])

            store.save(
                [
                    PreferenceRecord(
                        title="Detailed",
                        applies_to="Replies",
                        preference="Prefer detailed replies.",
                        status="active",
                    )
                ]
            )

            snapshots = list_store_snapshots(store_path)
            self.assertEqual(len(snapshots), 1)
            snapshot_text = Path(snapshots[0].path).read_text(encoding="utf-8")
            self.assertIn("Prefer concise replies.", snapshot_text)
            self.assertIn("Prefer detailed replies.", store_path.read_text(encoding="utf-8"))

    def test_restore_snapshot_preserves_current_store_before_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store_path = root / "prefs.md"
            store = MarkdownPreferenceStore(store_path)

            store.save(
                [
                    PreferenceRecord(
                        title="First",
                        applies_to="Replies",
                        preference="Prefer concise replies.",
                        status="active",
                    )
                ]
            )
            store.save(
                [
                    PreferenceRecord(
                        title="Second",
                        applies_to="Replies",
                        preference="Prefer detailed replies.",
                        status="active",
                    )
                ]
            )
            original_snapshot = list_store_snapshots(store_path)[0]

            result = restore_store_snapshot(original_snapshot.path, store_path)

            restored_text = store_path.read_text(encoding="utf-8")
            self.assertIn("Prefer concise replies.", restored_text)
            self.assertTrue(result["pre_restore_snapshot"]["created"])
            snapshots = list_store_snapshots(store_path)
            self.assertGreaterEqual(len(snapshots), 2)


if __name__ == "__main__":
    unittest.main()

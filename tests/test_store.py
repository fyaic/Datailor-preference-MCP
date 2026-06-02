from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from preference_agent.models import Evidence, PreferenceRecord
from preference_agent.store import MarkdownPreferenceStore


class MarkdownPreferenceStoreTests(unittest.TestCase):
    def test_save_load_preserves_evidence_and_session_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = MarkdownPreferenceStore(Path(temp) / "prefs.md")
            record = PreferenceRecord(
                id="pref-stats",
                title="Stats",
                summary="Preserve statistics.",
                applies_to="Preference capture",
                preference="Preserve evidence statistics across store writes.",
                confidence="high",
                status="active",
                evidence=[
                    Evidence(source="history.jsonl#1", session_id="session-a", quote="first", role="user"),
                    Evidence(source="history.jsonl#2", session_id="session-a", quote="second", role="user"),
                    Evidence(source="history.jsonl#3", session_id="session-b", quote="third", role="user"),
                ],
            )

            store.save([record])
            text = store.path.read_text(encoding="utf-8")
            loaded = store.load()

            self.assertIn("```json preference-record", text)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].id, "pref-stats")
            self.assertEqual(len(loaded[0].evidence), 3)
            self.assertEqual({item.session_id for item in loaded[0].evidence}, {"session-a", "session-b"})

    def test_structured_records_are_canonical_when_bullets_are_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "prefs.md"
            payload = PreferenceRecord(
                id="pref-structured",
                title="Structured",
                applies_to="Store load",
                preference="Use structured records as the canonical store data.",
                evidence=[Evidence(source="history.jsonl#1", session_id="session-a", quote="explicit", role="user")],
            ).to_dict()
            path.write_text(
                "\n".join(
                    [
                        "# Personal Preferences",
                        "",
                        "## Active Preferences",
                        "",
                        "- Use structured records as the canonical store data.",
                        "",
                        "<!-- preference-agent:records-start -->",
                        "```json preference-record",
                        json.dumps(payload, ensure_ascii=False, indent=2),
                        "```",
                        "<!-- preference-agent:records-end -->",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            loaded = MarkdownPreferenceStore(path).load()

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].id, "pref-structured")
            self.assertEqual(len(loaded[0].evidence), 1)

    def test_legacy_bullet_only_store_still_loads(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "prefs.md"
            path.write_text(
                "# Personal Preferences\n\n## Active Preferences\n\n- Prefer concise replies.\n",
                encoding="utf-8",
            )

            loaded = MarkdownPreferenceStore(path).load()

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].preference, "Prefer concise replies.")
            self.assertEqual(loaded[0].status, "active")


if __name__ == "__main__":
    unittest.main()

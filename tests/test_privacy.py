from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from preference_agent.models import PreferenceRecord
from preference_agent.privacy import redact_sensitive
from preference_agent.store import MarkdownPreferenceStore


class PrivacyTests(unittest.TestCase):
    def test_redacts_common_secret_shapes(self) -> None:
        openai_like = "sk-" + "abcdefghijklmnopqrstuvwxyz123456"
        bigmodel_like = "905962480032499aa0bdf7eb1e46627e" + "." + "UWLlzHRLof1xCiw4"
        text = f"key {openai_like} and {bigmodel_like}"
        redacted = redact_sensitive(text)
        self.assertIn("[REDACTED_SECRET]", redacted)
        self.assertNotIn(openai_like, redacted)
        self.assertNotIn("UWLlzHRLof1xCiw4", redacted)

    def test_removes_replacement_characters_from_source_logs(self) -> None:
        redacted = redact_sensitive("输出异常 ������ 修复方案")
        self.assertEqual(redacted, "输出异常  修复方案")
        self.assertNotIn("�", redacted)

    def test_store_render_redacts_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = MarkdownPreferenceStore(Path(temp) / "prefs.md")
            store.save(
                [
                    PreferenceRecord(
                        title="Secret",
                        applies_to="Configuration",
                        preference="Use token " + "sk-" + "abcdefghijklmnopqrstuvwxyz123456 when testing.",
                    )
                ]
            )
            text = store.path.read_text(encoding="utf-8")
            self.assertIn("[REDACTED_SECRET]", text)
            self.assertNotIn("sk-" + "abcdefghijklmnopqrstuvwxyz123456", text)


if __name__ == "__main__":
    unittest.main()

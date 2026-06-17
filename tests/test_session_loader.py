from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from preference_agent.session_loader import load_sessions


class SessionLoaderTests(unittest.TestCase):
    def test_openclaw_message_content_parts_join_text_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "openclaw.jsonl"
            source.write_text(
                json.dumps(
                    {
                        "type": "message",
                        "session_id": "openclaw-session",
                        "message": {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Keep release notes short."},
                                {"type": "toolResult", "text": "internal tool output"},
                                {"type": "image", "url": "file:///tmp/image.png"},
                            ],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            sessions = load_sessions(source)

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].session_id, "openclaw-session")
        self.assertEqual(sessions[0].messages[0].role, "user")
        self.assertEqual(sessions[0].messages[0].content, "Keep release notes short.")
        self.assertNotIn("tool output", sessions[0].messages[0].content)
        self.assertNotIn("image.png", sessions[0].messages[0].content)


if __name__ == "__main__":
    unittest.main()

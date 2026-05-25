from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from preference_agent.engine import PreferenceEngine
from preference_agent.feedback import record_feedback
from preference_agent.mcp_server import handle_request
from preference_agent.models import PreferenceRecord
from preference_agent.store import MarkdownPreferenceStore
from preference_agent.ui.server import build_manifesto, ensure_ui_server, shutdown_ui_server


class UiPanelTests(unittest.TestCase):
    def tearDown(self) -> None:
        shutdown_ui_server()

    def test_manifesto_view_summarizes_markdown_store_and_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Language",
                        applies_to="Replies",
                        preference="Respond in Chinese unless the user asks otherwise.",
                        status="active",
                        confidence="high",
                    ),
                    PreferenceRecord(
                        title="Outline",
                        applies_to="Planning",
                        preference="Start with an outline before detailed plans.",
                        status="needs_review",
                        confidence="medium",
                    ),
                ]
            )
            feedback_log = root / "feedback.jsonl"
            record_feedback(
                feedback_type="confirmation",
                user_feedback="Yes, keep this style.",
                preference_text="Respond in Chinese unless the user asks otherwise.",
                log_path=feedback_log,
            )

            manifesto = build_manifesto(
                store_path=store,
                feedback_log=feedback_log,
                settings_path=root / "settings.json",
                event_log=root / "events.jsonl",
            )

            self.assertEqual(manifesto["summary"]["active"], 1)
            self.assertEqual(manifesto["summary"]["pending"], 1)
            self.assertEqual(manifesto["mode"], "autonomous")
            self.assertEqual(len(manifesto["preferences"]), 2)
            self.assertEqual(manifesto["feedback"]["total"], 1)

    def test_local_server_serves_manifesto_and_settings_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Testing",
                        applies_to="Code changes",
                        preference="Run relevant tests after code changes.",
                        status="active",
                    )
                ]
            )
            info = ensure_ui_server(store_path=store, port=0, ui_dir=root / "ui")

            with urlopen(info.url + "/api/manifesto", timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
            self.assertEqual(data["summary"]["active"], 1)
            self.assertIn("Run relevant tests", json.dumps(data))

            request = Request(
                info.url + "/api/settings",
                data=json.dumps({"mode": "curated", "theme": "dark"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                updated = json.loads(response.read().decode("utf-8"))
            self.assertEqual(updated["settings"]["mode"], "curated")
            self.assertEqual(updated["settings"]["theme"], "dark")

    def test_mcp_open_preference_panel_returns_local_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Concise",
                        applies_to="Replies",
                        preference="Prefer concise replies.",
                        status="active",
                    )
                ]
            )
            engine = PreferenceEngine(MarkdownPreferenceStore(store))
            tools = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, engine)
            self.assertIn("open_preference_panel", json.dumps(tools))
            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "open_preference_panel", "arguments": {"port": 0}},
                },
                engine,
            )
            text = response["result"]["content"][0]["text"]
            self.assertIn("http://127.0.0.1:", text)


if __name__ == "__main__":
    unittest.main()

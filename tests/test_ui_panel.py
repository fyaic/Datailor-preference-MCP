from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from preference_agent.backends import HeuristicBackend
from preference_agent.engine import PreferenceEngine
from preference_agent.executive_summary import refresh_executive_summary
from preference_agent.feedback import record_feedback
from preference_agent.mcp_server import handle_request
from preference_agent.models import PreferenceRecord
from preference_agent.store import MarkdownPreferenceStore
from preference_agent.ui.server import build_manifesto, ensure_ui_server, shutdown_ui_server


def post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


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
            refresh_executive_summary(
                store,
                changed_records=MarkdownPreferenceStore(store).load(),
                backend=HeuristicBackend(),
                force=True,
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
            self.assertEqual(manifesto["language"], "en")
            self.assertEqual(len(manifesto["preferences"]), 2)
            self.assertIn("live_confidence", manifesto["preferences"][0])
            self.assertIn("recency_decay", manifesto["preferences"][0]["live_confidence_factors"])
            self.assertEqual(manifesto["feedback"]["total"], 1)
            self.assertIn("Respond in Chinese", manifesto["executive_summary"]["text"])

    def test_manifesto_builds_conflict_comparison_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Concise replies",
                        applies_to="When replying to users",
                        preference="When replying to users, prefer concise direct answers.",
                        status="active",
                        confidence="high",
                    ),
                    PreferenceRecord(
                        title="Detailed replies",
                        applies_to="When replying to users",
                        preference="When replying to users, provide exhaustive detailed explanations by default.",
                        status="needs_review",
                        confidence="medium",
                    ),
                ]
            )

            manifesto = build_manifesto(
                store_path=store,
                feedback_log=root / "feedback.jsonl",
                settings_path=root / "settings.json",
                event_log=root / "events.jsonl",
            )

            self.assertEqual(manifesto["summary"]["conflicts"], 1)
            group = manifesto["conflict_groups"][0]
            self.assertIn("concise", group["left"]["statement"])
            self.assertIn("exhaustive", group["right"]["statement"])

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
                data=json.dumps({"mode": "curated", "theme": "dark", "language": "zh"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                updated = json.loads(response.read().decode("utf-8"))
            self.assertEqual(updated["settings"]["mode"], "curated")
            self.assertEqual(updated["settings"]["theme"], "dark")
            self.assertEqual(updated["settings"]["language"], "zh")

            with urlopen(info.url + "/api/manifesto", timeout=5) as response:
                localized = json.loads(response.read().decode("utf-8"))
            self.assertEqual(localized["language"], "zh")

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

    def test_ui_confirmation_promotes_pending_preference_in_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"PREFERENCE_EXECUTIVE_SUMMARY_BACKEND": ""},
            clear=False,
        ):
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Outline",
                        applies_to="Planning",
                        preference="Start with an outline before detailed plans.",
                        status="needs_review",
                    )
                ]
            )
            info = ensure_ui_server(store_path=store, port=0, ui_dir=root / "ui")

            with urlopen(info.url + "/api/manifesto", timeout=5) as response:
                before = json.loads(response.read().decode("utf-8"))
            item = before["preferences"][0]
            result = post_json(
                info.url + "/api/feedback",
                {
                    "feedback_type": "confirmation",
                    "user_feedback": "Confirmed.",
                    "preference_id": item["id"],
                    "preference_text": item["statement"],
                },
            )

            self.assertTrue(result["preference_update"]["ok"])
            after = build_manifesto(
                store_path=store,
                feedback_log=root / ".feedback" / "feedback-log.jsonl",
                settings_path=root / "ui" / "settings.json",
                event_log=root / "ui" / "events.jsonl",
            )
            self.assertEqual(after["summary"]["active"], 1)
            self.assertEqual(after["summary"]["pending"], 0)
            self.assertEqual(MarkdownPreferenceStore(store).load()[0].status, "active")

    def test_ui_rejection_removes_preference_from_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"PREFERENCE_EXECUTIVE_SUMMARY_BACKEND": ""},
            clear=False,
        ):
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Verbose",
                        applies_to="Replies",
                        preference="Prefer long exploratory replies.",
                        status="active",
                    )
                ]
            )
            info = ensure_ui_server(store_path=store, port=0, ui_dir=root / "ui")

            with urlopen(info.url + "/api/manifesto", timeout=5) as response:
                before = json.loads(response.read().decode("utf-8"))
            item = before["preferences"][0]
            result = post_json(
                info.url + "/api/feedback",
                {
                    "feedback_type": "rejection",
                    "user_feedback": "Reject this.",
                    "preference_id": item["id"],
                    "preference_text": item["statement"],
                },
            )

            self.assertTrue(result["preference_update"]["removed"])
            after = build_manifesto(
                store_path=store,
                feedback_log=root / ".feedback" / "feedback-log.jsonl",
                settings_path=root / "ui" / "settings.json",
                event_log=root / "ui" / "events.jsonl",
            )
            self.assertEqual(after["summary"]["active"], 0)
            self.assertEqual(after["summary"]["pending"], 0)
            self.assertEqual(MarkdownPreferenceStore(store).load(), [])

    def test_ui_correction_updates_preference_text_and_activates_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {"PREFERENCE_EXECUTIVE_SUMMARY_BACKEND": ""},
            clear=False,
        ):
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save(
                [
                    PreferenceRecord(
                        title="Speed",
                        applies_to="Implementation",
                        preference="Ship quickly even without verification.",
                        status="needs_review",
                    )
                ]
            )
            info = ensure_ui_server(store_path=store, port=0, ui_dir=root / "ui")

            with urlopen(info.url + "/api/manifesto", timeout=5) as response:
                before = json.loads(response.read().decode("utf-8"))
            item = before["preferences"][0]
            result = post_json(
                info.url + "/api/feedback",
                {
                    "feedback_type": "correction",
                    "user_feedback": "After code changes, run relevant verification before reporting completion.",
                    "preference_id": item["id"],
                    "preference_text": item["statement"],
                },
            )

            update = result["preference_update"]
            self.assertTrue(update["ok"])
            self.assertIn("run relevant verification", update["current_text"])
            saved = MarkdownPreferenceStore(store).load()
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0].status, "active")
            self.assertIn("run relevant verification", saved[0].preference)


if __name__ == "__main__":
    unittest.main()

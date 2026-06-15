from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from preference_agent.backends import HeuristicBackend
from preference_agent.engine import PreferenceEngine
from preference_agent.mcp_server import handle_request
from preference_agent.models import Session, SessionMessage
from preference_agent.store import MarkdownPreferenceStore


ROOT = Path(__file__).resolve().parents[1]


class PreferenceEngineTests(unittest.TestCase):
    def make_engine(self, store_path: Path) -> PreferenceEngine:
        return PreferenceEngine(MarkdownPreferenceStore(store_path), backend=HeuristicBackend())

    def test_cold_start_returns_no_preference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            engine = self.make_engine(store)
            decision = engine.decide("I am preparing to change code and submit the final reply.", agent="codex")
            self.assertEqual(decision["decision"], "no_preference")
            self.assertTrue(decision["escalate"])
            self.assertIn("Preference store", decision["reason"])

    def test_initial_capture_writes_markdown_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            engine = self.make_engine(store)
            result = engine.capture_path(ROOT / "examples" / "cold_start_session.md")
            self.assertGreaterEqual(result.candidates_seen, 2)
            records = MarkdownPreferenceStore(store).load()
            self.assertGreaterEqual(len(records), 2)
            self.assertGreater(sum(len(record.evidence) for record in records), 0)
            self.assertTrue(any(item.session_id for record in records for item in record.evidence))
            text = store.read_text(encoding="utf-8")
            self.assertIn("verification", text.casefold())
            self.assertIn("```json preference-record", text)
            self.assertIn("## Active Preferences", text)

    def test_semantic_decision_matches_variant_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            engine = self.make_engine(store)
            engine.capture_path(ROOT / "examples" / "cold_start_session.md")
            decision = engine.decide(
                "After implementation, should I verify first and check regression risk?",
                context={"language": "Python"},
                agent="codex",
            )
            self.assertEqual(decision["decision"], "apply")
            instructions = json.dumps(decision, ensure_ascii=False)
            self.assertIn("verification", instructions.casefold())

    def test_incremental_capture_replaces_explicit_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            engine = self.make_engine(store)
            engine.capture_path(ROOT / "examples" / "cold_start_session.md")
            result = engine.capture_path(ROOT / "examples" / "incremental_session.md")
            records = MarkdownPreferenceStore(store).load()
            self.assertGreaterEqual(len(records), 2)
            self.assertTrue(result.replaced or result.merged or result.added)
            self.assertIn("explain why", store.read_text(encoding="utf-8"))

    def test_capture_dry_run_filters_raw_one_off_fragments(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            engine = self.make_engine(store)
            session = Session(
                source="quality-regression",
                session_id="bad-fragments",
                messages=[
                    SessionMessage(role="user", content="B and it must be headless."),
                    SessionMessage(role="user", content="I tweaked it slightly; keep it and do not overwrite."),
                    SessionMessage(role="user", content="It should be in the local Obsidian .plugin directory; can it be one folder?"),
                    SessionMessage(role="user", content="I want to preview this in the WeChat IDE, but it never loads. Check the code."),
                ],
            )

            result = engine.capture_session(session, dry_run=True)

            self.assertEqual(result.added, [])
            self.assertEqual(result.merged, [])
            self.assertGreaterEqual(result.filtered_candidates, 0)
            self.assertEqual(MarkdownPreferenceStore(store).load(), [])

    def test_capture_dry_run_keeps_generalized_preference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            engine = self.make_engine(store)
            session = Session(
                source="quality-regression",
                session_id="good-preference",
                messages=[SessionMessage(role="user", content="From now on, reply to me in English by default.")],
            )

            result = engine.capture_session(session, dry_run=True)

            self.assertEqual(len(result.added), 1)
            self.assertEqual(MarkdownPreferenceStore(store).load(), [])

    def test_capture_chinese_preference_keeps_session_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            engine = self.make_engine(store)
            session = Session(
                source="kimi-history.jsonl#1",
                session_id="kimi-session-a",
                messages=[SessionMessage(role="user", content="以后回复请简洁分点，重要结论先说，不要大段平铺。")],
            )

            result = engine.capture_session(session)
            records = MarkdownPreferenceStore(store).load()

            self.assertEqual(len(result.added), 1)
            self.assertEqual(len(records), 1)
            self.assertEqual(len(records[0].evidence), 1)
            self.assertEqual(records[0].evidence[0].session_id, "kimi-session-a")

    def test_mcp_tools_call_decide(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            engine = self.make_engine(store)
            engine.capture_path(ROOT / "examples" / "cold_start_session.md")
            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "get_preference_decision",
                        "arguments": {
                            "agent": "openclaw",
                            "task": "Ready to reply to the user after finishing the code",
                            "context": {"risk": "medium"},
                        },
                    },
                },
                engine,
            )
            self.assertEqual(response["id"], 1)
            payload = json.loads(response["result"]["content"][0]["text"])
            self.assertEqual(payload["decision"], "no_preference")
            self.assertFalse(payload["injected"])
            self.assertNotIn("matched_preferences", payload)


if __name__ == "__main__":
    unittest.main()

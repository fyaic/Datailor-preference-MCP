from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from preference_agent.backends import HeuristicBackend
from preference_agent.decision_conflicts import resolve_preference_conflict
from preference_agent.engine import PreferenceEngine
from preference_agent.mcp_server import handle_request
from preference_agent.models import PreferenceRecord
from preference_agent.store import MarkdownPreferenceStore


class DecisionConflictTests(unittest.TestCase):
    def test_decide_escalates_once_for_conflicting_active_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            _write_conflicting_store(store)
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())

            first = engine.decide("请回复用户问题，说明怎么处理。", agent="codex")
            self.assertEqual(first["decision"], "escalate")
            self.assertTrue(first["escalate"])
            self.assertIn("conflict", first)
            self.assertIn("resolve_preference_conflict", first["agent_instruction"])
            self.assertIn("详细", first["agent_instruction"])
            self.assertIn("简洁", first["agent_instruction"])

            second = engine.decide("请回复用户问题，说明怎么处理。", agent="codex")
            self.assertEqual(second["decision"], "no_preference")
            self.assertFalse(second["escalate"])
            self.assertIn("conflict_suppressed", second)
            self.assertNotIn("详细展开；", second.get("agent_instruction", ""))

    def test_resolve_conflict_prefers_one_and_deactivates_the_other(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            records = _write_conflicting_store(store)
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
            decision = engine.decide("请回复用户问题。", agent="codex")
            conflict_key = decision["conflict"]["key"]

            result = resolve_preference_conflict(
                store_path=store,
                conflict_key_value=conflict_key,
                resolution="prefer",
                selected_preference_id=records[1].id,
            )

            self.assertTrue(result["ok"])
            saved = {record.preference: record.status for record in MarkdownPreferenceStore(store).load()}
            self.assertEqual(saved["回复用户问题时，回答要简洁短一点，不要长文。"], "active")
            self.assertEqual(saved["回复用户问题时，回答要详细展开，充分解释。"], "needs_review")

    def test_resolve_conflict_neither_moves_both_to_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            _write_conflicting_store(store)
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
            conflict_key = engine.decide("请回复用户问题。", agent="codex")["conflict"]["key"]

            result = resolve_preference_conflict(
                store_path=store,
                conflict_key_value=conflict_key,
                resolution="neither",
                user_feedback="这个上下文两个都不适用。",
            )

            self.assertTrue(result["ok"])
            self.assertTrue(all(record.status == "needs_review" for record in MarkdownPreferenceStore(store).load()))

    def test_mcp_resolve_preference_conflict_tool_updates_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            records = _write_conflicting_store(store)
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
            conflict_key = engine.decide("请回复用户问题。", agent="codex")["conflict"]["key"]

            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "resolve_preference_conflict",
                        "arguments": {
                            "conflict_key": conflict_key,
                            "resolution": "prefer",
                            "selected_preference_id": records[0].id,
                        },
                    },
                },
                engine,
            )

            text = response["result"]["content"][0]["text"]
            self.assertIn("updated_preferences", text)
            self.assertTrue(json.loads(text)["ok"])


def _write_conflicting_store(store: Path) -> list[PreferenceRecord]:
    records = [
        PreferenceRecord(
            title="详细回复",
            applies_to="回复用户问题时",
            preference="回复用户问题时，回答要详细展开，充分解释。",
            status="active",
            confidence="high",
        ),
        PreferenceRecord(
            title="简洁回复",
            applies_to="回复用户问题时",
            preference="回复用户问题时，回答要简洁短一点，不要长文。",
            status="active",
            confidence="high",
        ),
    ]
    MarkdownPreferenceStore(store).save(records)
    return MarkdownPreferenceStore(store).load()


if __name__ == "__main__":
    unittest.main()

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
            decision = engine.decide("我准备改代码并提交最终回复", agent="codex")
            self.assertEqual(decision["decision"], "no_preference")
            self.assertTrue(decision["escalate"])
            self.assertIn("偏好库", decision["reason"])

    def test_initial_capture_writes_markdown_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            engine = self.make_engine(store)
            result = engine.capture_path(ROOT / "examples" / "cold_start_session.md")
            self.assertGreaterEqual(result.candidates_seen, 2)
            records = MarkdownPreferenceStore(store).load()
            self.assertGreaterEqual(len(records), 2)
            text = store.read_text(encoding="utf-8")
            self.assertIn("验证", text)
            self.assertNotIn("```json preference-record", text)
            self.assertIn("## 已确认偏好", text)

    def test_semantic_decision_matches_variant_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            engine = self.make_engine(store)
            engine.capture_path(ROOT / "examples" / "cold_start_session.md")
            decision = engine.decide(
                "实现完成后，我是否应该先做验证并检查回归风险？",
                context={"language": "Python"},
                agent="codex",
            )
            self.assertEqual(decision["decision"], "apply")
            instructions = json.dumps(decision, ensure_ascii=False)
            self.assertIn("验证", instructions)

    def test_incremental_capture_replaces_explicit_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            engine = self.make_engine(store)
            engine.capture_path(ROOT / "examples" / "cold_start_session.md")
            result = engine.capture_path(ROOT / "examples" / "incremental_session.md")
            records = MarkdownPreferenceStore(store).load()
            self.assertGreaterEqual(len(records), 2)
            self.assertTrue(result.replaced or result.merged or result.added)
            self.assertIn("明确说明", store.read_text(encoding="utf-8"))

    def test_capture_dry_run_filters_raw_one_off_fragments(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = Path(temp) / "prefs.md"
            engine = self.make_engine(store)
            session = Session(
                source="quality-regression",
                session_id="bad-fragments",
                messages=[
                    SessionMessage(role="user", content="B 而且必须无头。"),
                    SessionMessage(role="user", content="我稍微调整了一下 请保留不要覆盖。"),
                    SessionMessage(role="user", content="应该就在obsidian本地目录的.plugin 请问能否合成一个文件夹？"),
                    SessionMessage(role="user", content="我希望在微信ide里面预览但是一直没有加载出来，请检查以下代码。"),
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
                messages=[SessionMessage(role="user", content="以后默认回复我中文。")],
            )

            result = engine.capture_session(session, dry_run=True)

            self.assertEqual(len(result.added), 1)
            self.assertEqual(MarkdownPreferenceStore(store).load(), [])

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
                            "task": "代码写完后准备直接回复用户",
                            "context": {"risk": "medium"},
                        },
                    },
                },
                engine,
            )
            self.assertEqual(response["id"], 1)
            text = response["result"]["content"][0]["text"]
            self.assertIn("matched_preferences", text)


if __name__ == "__main__":
    unittest.main()

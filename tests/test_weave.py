from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from preference_agent.cli import main as cli_main
from preference_agent.mcp_server import _engine as build_mcp_engine
from preference_agent.mcp_server import handle_request
from preference_agent.models import PreferenceRecord
from preference_agent.store import MarkdownPreferenceStore
from preference_agent.ui.server import build_manifesto
from preference_agent.weave import analyze_memory_rot, apply_weave_plan, run_weave
from preference_agent.weave_models import WeaveInstruction


class WeaveTests(unittest.TestCase):
    def test_instruction_parses_focus_and_ignore(self) -> None:
        instruction = WeaveInstruction.from_text(
            "focus on UI writing preferences; ignore one-off install commands"
        )

        self.assertEqual(instruction.focus, ["UI writing preferences"])
        self.assertEqual(instruction.ignore, ["one-off install commands"])
        self.assertTrue(instruction.matches_focus("The user has UI copy writing preferences."))
        self.assertTrue(instruction.should_ignore("This is a one-off install command."))

    def test_run_weave_generates_typed_report_without_modifying_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store_path = root / "prefs.md"
            source_path = root / "history.md"
            weave_dir = root / ".weave"
            store = MarkdownPreferenceStore(store_path)
            store.save(
                [
                    PreferenceRecord(
                        title="测试",
                        applies_to="代码修改",
                        preference="代码修改后默认运行相关测试。",
                        status="active",
                    )
                ]
            )
            before = store_path.read_text(encoding="utf-8")
            source_path.write_text(
                "\n".join(
                    [
                        "用户: 以后修改代码后，默认先跑相关测试再交付。如果没法跑，要明确说明。",
                        "用户: 咱们这类长任务先读仓库，再写 Harness，再按 PRD 自检，不要一上来直接改代码。",
                        "用户: PowerShell 里中文写入外部系统后必须回读，有时候终端显示正常不代表外部系统正常。",
                        "用户: 你经常在没回读中文的情况下说完成，这会导致乱码问题被漏掉。以后写入后先回读。",
                        "用户: pipx install git+https://github.com/fyaic/Datailor-preference-MCP.git",
                    ]
                ),
                encoding="utf-8",
            )

            result = run_weave(
                store_path=store_path,
                source=source_path,
                instructions="ignore one-off install commands",
                weave_dir=weave_dir,
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(store_path.read_text(encoding="utf-8"), before)
            self.assertTrue(Path(result.report_file).exists())
            self.assertTrue(Path(result.result_file).exists())
            kinds = {item.kind for item in result.insights}
            self.assertIn("preference", kinds)
            self.assertIn("workflow", kinds)
            self.assertIn("tool_quirk", kinds)
            self.assertIn("error_pattern", kinds)
            self.assertGreaterEqual(result.stats["ignored_by_instruction"], 1)
            report = Path(result.report_file).read_text(encoding="utf-8")
            self.assertIn("## Instructions", report)
            self.assertIn("## Memory Rot Suggestions", report)
            self.assertIn("## Ignored By Instructions", report)

    def test_memory_rot_flags_duplicate_conflict_stale_and_negative_feedback(self) -> None:
        duplicate_a = PreferenceRecord(
            id="pref-a",
            title="测试",
            applies_to="代码修改",
            preference="代码修改后默认运行相关测试。",
            status="active",
        )
        duplicate_b = PreferenceRecord(
            id="pref-b",
            title="测试前置",
            applies_to="代码修改",
            preference="修改代码后默认先执行相关测试。",
            status="active",
        )
        concise = PreferenceRecord(
            id="pref-c",
            title="简洁",
            applies_to="回复",
            preference="回复时默认保持简洁，少废话。",
            status="active",
        )
        detailed = PreferenceRecord(
            id="pref-d",
            title="详细",
            applies_to="回复",
            preference="回复时默认提供详细长文解释。",
            status="active",
        )
        stale = PreferenceRecord(
            id="pref-e",
            title="旧偏好",
            applies_to="文档",
            preference="写文档时默认加入很长的背景说明。",
            status="active",
            confidence="low",
            updated_at="2025-01-01T00:00:00+00:00",
        )

        suggestions = analyze_memory_rot(
            [duplicate_a, duplicate_b, concise, detailed, stale],
            {
                "by_preference": [
                    {
                        "preference": "pref-e",
                        "usage": 0,
                        "correction": 0,
                        "confirmation": 0,
                        "rejection": 1,
                        "recommendation": "review_needed",
                    }
                ]
            },
        )
        types = {item.type for item in suggestions}

        self.assertIn("duplicate", types)
        self.assertIn("conflict", types)
        self.assertIn("negative_feedback", types)

    def test_apply_weave_plan_applies_only_accepted_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store_path = root / "prefs.md"
            source_path = root / "history.md"
            weave_dir = root / ".weave"
            store = MarkdownPreferenceStore(store_path)
            store.ensure()
            source_path.write_text("用户: 以后修改代码后，默认先跑相关测试再交付。", encoding="utf-8")
            result = run_weave(store_path=store_path, source=source_path, weave_dir=weave_dir)
            change = next(item for item in result.apply_plan.changes if item.type == "add_preference")

            applied = apply_weave_plan(
                job_id=result.job_id,
                accepted_change_ids=[change.change_id],
                store_path=store_path,
                weave_dir=weave_dir,
            )

            self.assertTrue(applied["ok"])
            self.assertTrue(applied["readback_ok"])
            records = MarkdownPreferenceStore(store_path).load()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].status, "needs_review")
            self.assertIn("测试", records[0].preference)

    def test_cli_weave_outputs_json_and_human_report_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "history.md"
            source.write_text("用户: 以后修改代码后，默认先跑相关测试再交付。", encoding="utf-8")

            out = _run_cli(
                [
                    "--store",
                    str(root / "prefs.md"),
                    "weave",
                    "--source",
                    str(source),
                    "--weave-dir",
                    str(root / ".weave"),
                    "--instructions",
                    "ignore one-off install commands",
                    "--json",
                ]
            )

            self.assertTrue(out["ok"])
            self.assertEqual(out["status"], "completed")
            self.assertTrue(Path(out["report_file"]).exists())

    def test_mcp_weave_tools_and_ui_latest_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "history.md"
            store = root / "prefs.md"
            weave_dir = root / ".weave"
            source.write_text("用户: 以后修改代码后，默认先跑相关测试再交付。", encoding="utf-8")
            engine = build_mcp_engine(store)
            with patch.dict("os.environ", {"DATAILOR_WEAVE_DIR": str(weave_dir)}, clear=False):
                started = handle_request(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "start_weave_consolidation",
                            "arguments": {
                                "source": str(source),
                                "instructions": "ignore one-off install commands",
                            },
                        },
                    },
                    engine,
                )

                text = started["result"]["content"][0]["text"]
                payload = __import__("json").loads(text)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["status"], "completed")

                latest = build_manifesto(store_path=store)
                self.assertIn("weave", latest)
                self.assertIsNotNone(latest["weave"])
                self.assertEqual(latest["weave"]["job_id"], payload["job_id"])


def _run_cli(argv: list[str]) -> dict:
    import json

    buffer = StringIO()
    with redirect_stdout(buffer):
        code = cli_main(argv)
    if code != 0:
        raise AssertionError(f"CLI exited with {code}: {buffer.getvalue()}")
    return json.loads(buffer.getvalue())


if __name__ == "__main__":
    unittest.main()

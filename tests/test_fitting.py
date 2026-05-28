from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from preference_agent.cli import main as cli_main
from preference_agent.mcp_server import _engine as build_mcp_engine
from preference_agent.mcp_server import handle_request
from preference_agent.models import PreferenceRecord
from preference_agent.store import MarkdownPreferenceStore
from preference_agent.ui.server import build_manifesto
from preference_agent.fitting import FittingInput, analyze_memory_rot, apply_fitting_plan, extract_insights, run_fitting
from preference_agent.fitting_models import FittingInstruction


class FittingTests(unittest.TestCase):
    def test_instruction_parses_focus_and_ignore(self) -> None:
        instruction = FittingInstruction.from_text(
            "focus on UI writing preferences; ignore one-off install commands"
        )

        self.assertEqual(instruction.focus, ["UI writing preferences"])
        self.assertEqual(instruction.ignore, ["one-off install commands"])
        self.assertTrue(instruction.matches_focus("The user has UI copy writing preferences."))
        self.assertTrue(instruction.should_ignore("This is a one-off install command."))

    def test_run_fitting_generates_typed_report_without_modifying_store(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store_path = root / "prefs.md"
            source_path = root / "history.md"
            fitting_dir = root / ".fitting"
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

            result = run_fitting(
                store_path=store_path,
                source=source_path,
                instructions="ignore one-off install commands",
                fitting_dir=fitting_dir,
            )

            self.assertEqual(result.status, "completed")
            self.assertEqual(store_path.read_text(encoding="utf-8"), before)
            self.assertTrue(Path(result.report_file).exists())
            self.assertTrue(Path(result.result_file).exists())
            self.assertEqual(_read_json(Path(result.result_file))["version"], "0.1.0")
            self.assertEqual(_read_json(fitting_dir / "jobs" / result.job_id / "apply-plan.json")["version"], "0.1.0")
            self.assertEqual(_read_json(fitting_dir / "jobs" / result.job_id / "status.json")["version"], "0.1.0")
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

    def test_run_fitting_auto_discovers_sources_when_source_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_path = root / "codex-history.md"
            source_path.write_text("用户: 以后修改代码后，默认先跑相关测试再交付。", encoding="utf-8")

            with patch(
                "preference_agent.fitting.auto_discover_sources",
                return_value=[SimpleNamespace(path=str(source_path))],
            ):
                result = run_fitting(
                    store_path=root / "prefs.md",
                    agent="codex",
                    fitting_dir=root / ".fitting",
                    max_files=1,
                )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.stats["auto_discovered_sources"], 1)
            self.assertTrue(any(str(source_path) in item for item in result.inputs))
            self.assertTrue(result.insights)

    def test_extract_insights_covers_all_fitting_insight_kinds(self) -> None:
        inputs = [
            FittingInput(
                source="fixture",
                text="\n".join(
                    [
                        "如果测试失败，先重新运行相关命令，仍失败就说明阻塞原因。",
                        "阶段性成果完成后，询问是否需要更新 Linear issue。",
                        "用户常用 Obsidian 管理长期文档。",
                        "我习惯先给结论再给验证结果。",
                        "项目文档必须统一使用 Datailor 和 Fitting 命名。",
                    ]
                ),
            )
        ]

        kinds = {item.kind for item in extract_insights(inputs)}

        self.assertIn("recovery_strategy", kinds)
        self.assertIn("handoff_pattern", kinds)
        self.assertIn("app_usage", kinds)
        self.assertIn("personal_habit", kinds)
        self.assertIn("project_convention", kinds)

    def test_run_fitting_writes_failed_job_when_pipeline_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_path = root / "history.md"
            fitting_dir = root / ".fitting"
            source_path.write_text("用户: 以后修改代码后，默认先跑相关测试再交付。", encoding="utf-8")

            with patch("preference_agent.fitting.extract_insights", side_effect=RuntimeError("boom")):
                result = run_fitting(store_path=root / "prefs.md", source=source_path, fitting_dir=fitting_dir)

            job_dir = fitting_dir / "jobs" / result.job_id
            self.assertEqual(result.status, "failed")
            self.assertIn("boom", result.error)
            self.assertEqual(_read_json(job_dir / "status.json")["status"], "failed")
            self.assertEqual(_read_json(job_dir / "result.json")["status"], "failed")
            self.assertIn("## Error", (job_dir / "report.md").read_text(encoding="utf-8"))

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

    def test_apply_fitting_plan_applies_only_accepted_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store_path = root / "prefs.md"
            source_path = root / "history.md"
            fitting_dir = root / ".fitting"
            store = MarkdownPreferenceStore(store_path)
            store.ensure()
            source_path.write_text("用户: 以后修改代码后，默认先跑相关测试再交付。", encoding="utf-8")
            result = run_fitting(store_path=store_path, source=source_path, fitting_dir=fitting_dir)
            change = next(item for item in result.apply_plan.changes if item.type == "add_preference")

            applied = apply_fitting_plan(
                job_id=result.job_id,
                accepted_change_ids=[change.change_id],
                store_path=store_path,
                fitting_dir=fitting_dir,
            )

            self.assertTrue(applied["ok"])
            self.assertTrue(applied["readback_ok"])
            records = MarkdownPreferenceStore(store_path).load()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].status, "needs_review")
            self.assertIn("测试", records[0].preference)

    def test_cli_fitting_outputs_json_and_human_report_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "history.md"
            source.write_text("用户: 以后修改代码后，默认先跑相关测试再交付。", encoding="utf-8")

            out = _run_cli(
                [
                    "--store",
                    str(root / "prefs.md"),
                    "fitting",
                    "--source",
                    str(source),
                    "--fitting-dir",
                    str(root / ".fitting"),
                    "--instructions",
                    "ignore one-off install commands",
                    "--json",
                ]
            )

            self.assertTrue(out["ok"])
            self.assertEqual(out["status"], "completed")
            self.assertTrue(Path(out["report_file"]).exists())

    def test_mcp_fitting_tools_and_ui_latest_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "history.md"
            store = root / "prefs.md"
            fitting_dir = root / ".fitting"
            source.write_text("用户: 以后修改代码后，默认先跑相关测试再交付。", encoding="utf-8")
            engine = build_mcp_engine(store)
            with patch.dict("os.environ", {"DATAILOR_FITTING_DIR": str(fitting_dir)}, clear=False):
                listed = handle_request(
                    {"jsonrpc": "2.0", "id": 0, "method": "tools/list"},
                    engine,
                )
                tool_names = {item["name"] for item in listed["result"]["tools"]}
                self.assertIn("start_fitting", tool_names)
                self.assertEqual(sum(1 for name in tool_names if name.startswith("start_fitting")), 1)
                started = handle_request(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "start_fitting",
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
                self.assertIn("fitting", latest)
                self.assertIsNotNone(latest["fitting"])
                self.assertEqual(latest["fitting"]["job_id"], payload["job_id"])
                self.assertIn("# Datailor Fitting Report", latest["fitting"]["report_markdown"])


def _run_cli(argv: list[str]) -> dict:
    import json

    buffer = StringIO()
    with redirect_stdout(buffer):
        code = cli_main(argv)
    if code != 0:
        raise AssertionError(f"CLI exited with {code}: {buffer.getvalue()}")
    return json.loads(buffer.getvalue())


def _read_json(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

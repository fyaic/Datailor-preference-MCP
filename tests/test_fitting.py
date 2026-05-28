from __future__ import annotations

import json
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
from preference_agent.fitting_store import FittingJobStore


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
                        title="Tests",
                        applies_to="Code changes",
                        preference="Run relevant tests by default after code changes.",
                        status="active",
                    )
                ]
            )
            before = store_path.read_text(encoding="utf-8")
            source_path.write_text(
                "\n".join(
                    [
                        "User: From now on, after changing code, run relevant tests before delivery. If tests cannot run, clearly explain why.",
                        "User: For this kind of long-running task, first read the repo, then write the Harness, then self-check against the PRD; do not jump straight into code changes.",
                        "User: In PowerShell, after writing to an external system, you must read the content back because terminal output alone does not prove the external system is correct.",
                        "User: You often say done without reading external writes back, which misses encoding problems. From now on, read back after writing.",
                        "User: pipx install git+https://github.com/fyaic/Datailor-preference-MCP.git",
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
            source_path.write_text("User: From now on, after changing code, run relevant tests before delivery.", encoding="utf-8")

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
                        "If tests fail, first rerun the relevant command, and if it still fails, explain the blocker.",
                        "After a phase is completed, ask whether the Linear issue should be updated.",
                        "The user uses Obsidian to manage long-term documentation.",
                        "I usually give the conclusion before verification results.",
                        "Project documentation must consistently use the Datailor and Fitting names.",
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
            source_path.write_text("User: From now on, after changing code, run relevant tests before delivery.", encoding="utf-8")

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
            title="Tests",
            applies_to="Code changes",
            preference="Run relevant tests by default after code changes.",
            status="active",
        )
        duplicate_b = PreferenceRecord(
            id="pref-b",
            title="Test first",
            applies_to="Code changes",
            preference="Run relevant tests first after modifying code.",
            status="active",
        )
        concise = PreferenceRecord(
            id="pref-c",
            title="Concise",
            applies_to="Replies",
            preference="Keep replies concise and less verbose by default.",
            status="active",
        )
        detailed = PreferenceRecord(
            id="pref-d",
            title="Detailed",
            applies_to="Replies",
            preference="Provide detailed long-form explanations by default when replying.",
            status="active",
        )
        stale = PreferenceRecord(
            id="pref-e",
            title="Old preference",
            applies_to="Documentation",
            preference="Include very long background explanations by default when writing documentation.",
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
            source_path.write_text("User: From now on, after changing code, run relevant tests before delivery.", encoding="utf-8")
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
            self.assertIn("test", records[0].preference.casefold())

    def test_cli_fitting_outputs_json_and_human_report_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "history.md"
            source.write_text("User: From now on, after changing code, run relevant tests before delivery.", encoding="utf-8")

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

    def test_cli_fitting_no_review_passes_false_to_runner(self) -> None:
        class FakeResult:
            def to_dict(self) -> dict[str, object]:
                return {"job_id": "fake", "status": "completed", "stats": {}, "report_file": ""}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("preference_agent.cli.run_fitting", return_value=FakeResult()) as mocked:
                out = _run_cli(["--store", str(root / "prefs.md"), "fitting", "--no-review", "--json"])

            self.assertEqual(out["status"], "completed")
            self.assertFalse(mocked.call_args.kwargs["review"])

    def test_fitting_show_report_handles_empty_report_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "history.md"
            fitting_dir = root / ".fitting"
            source.write_text("User: From now on, after changing code, run relevant tests before delivery.", encoding="utf-8")
            result = run_fitting(store_path=root / "prefs.md", source=source, fitting_dir=fitting_dir)
            result_path = Path(result.result_file)
            data = _read_json(result_path)
            data["report_file"] = ""
            result_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            text = _run_cli_text(["fitting-show", result.job_id, "--fitting-dir", str(fitting_dir), "--report"])

            self.assertEqual(text, "\n")

    def test_fitting_store_and_cli_report_missing_jobs_semantically(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fitting_dir = root / ".fitting"
            store = FittingJobStore(fitting_dir)

            with self.assertRaisesRegex(ValueError, "job not found: missing"):
                store.read_result("missing")
            with self.assertRaisesRegex(ValueError, "job not found: missing"):
                store.read_apply_plan("missing")

            shown = _run_cli(["fitting-show", "missing", "--fitting-dir", str(fitting_dir), "--json"])
            applied = _run_cli(
                [
                    "--store",
                    str(root / "prefs.md"),
                    "fitting-apply",
                    "missing",
                    "--fitting-dir",
                    str(fitting_dir),
                    "--accept",
                    "change-missing",
                ]
            )

            self.assertFalse(shown["ok"])
            self.assertIn("job not found: missing", shown["error"])
            self.assertFalse(applied["ok"])
            self.assertIn("job not found: missing", applied["error"])

    def test_mcp_fitting_tools_and_ui_latest_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "history.md"
            store = root / "prefs.md"
            fitting_dir = root / ".fitting"
            source.write_text("User: From now on, after changing code, run relevant tests before delivery.", encoding="utf-8")
            engine = build_mcp_engine(store)
            with patch.dict("os.environ", {"DATAILOR_FITTING_DIR": str(fitting_dir)}, clear=False):
                listed = handle_request(
                    {"jsonrpc": "2.0", "id": 0, "method": "tools/list"},
                    engine,
                )
                tool_names = {item["name"] for item in listed["result"]["tools"]}
                self.assertIn("start_fitting", tool_names)
                self.assertEqual(sum(1 for name in tool_names if name.startswith("start_fitting")), 1)
                self.assertIn("mode=auto", json.dumps(listed, ensure_ascii=False))
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


def _run_cli_text(argv: list[str]) -> str:
    buffer = StringIO()
    with redirect_stdout(buffer):
        code = cli_main(argv)
    if code != 0:
        raise AssertionError(f"CLI exited with {code}: {buffer.getvalue()}")
    return buffer.getvalue()


def _read_json(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

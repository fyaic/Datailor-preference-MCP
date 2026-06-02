from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preference_agent.capture_runner import CaptureConfig
from preference_agent.cli import main as cli_main
from preference_agent.engine import PreferenceEngine
from preference_agent.mcp_server import _engine as build_mcp_engine
from preference_agent.mcp_server import handle_request
from preference_agent.onboarding import get_onboarding_status
from preference_agent.paths import default_store_path, package_version
from preference_agent.store import MarkdownPreferenceStore


class OnboardingTests(unittest.TestCase):
    def test_status_reports_empty_store_and_next_datailor_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            _write_history(home / ".codex" / "history.jsonl", "From now on, give the conclusion first.")
            with patch.dict(os.environ, {"PREFERENCE_DISCOVERY_HOME": str(home)}, clear=False):
                status = get_onboarding_status(root / "prefs.md", agent_hint="codex")

            data = status.to_dict()
            self.assertEqual(data["product"], "Datailor")
            self.assertEqual(data["cli"], "datailor")
            self.assertEqual(data["state"], "not_initialized")
            self.assertEqual(data["supported_sources"], 1)
            self.assertIn("datailor onboard --agent codex", data["commands"]["onboard"])
            self.assertEqual(data["commands"]["integrate_status"], "datailor integrate status --client all")
            self.assertEqual(data["commands"]["integrate_preview"], "datailor integrate install --client all --dry-run")
            self.assertIn("datailor integrate status --client all", "\n".join(data["next_steps"]))

    def test_datailor_doctor_cli_outputs_agent_specific_next_steps(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = _run_cli(["--store", str(root / "prefs.md"), "doctor", "--agent", "kimi"])

            self.assertEqual(out["product"], "Datailor")
            self.assertEqual(out["cli"], "datailor")
            self.assertIn("state", out)
            self.assertIn("commands", out)
            self.assertIn("install", out)
            self.assertIn("paths", out)
            self.assertIn("mcp", out)
            self.assertEqual(out["store"]["path"], str(root / "prefs.md"))
            self.assertIn("datailor onboard --agent kimi", out["commands"]["onboard"])
            self.assertIn("datailor onboard --agent kimi", "\n".join(out["next_steps"]))
            self.assertIn("datailor mcp-config --agent kimi", out["commands"]["mcp_config"])
            self.assertIn("datailor-mcp --agent kimi", out["commands"]["mcp_stdio"])
            self.assertIn("datailor integrate status --client all", out["commands"]["integrate_status"])
            self.assertIn("datailor integrate install --client all --dry-run", "\n".join(out["next_steps"]))
            self.assertEqual(out["mcp"]["command"], "datailor-mcp")

    def test_datailor_onboard_dry_run_discovers_history_without_writing_preferences(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            _write_history(home / ".kimi" / "user-history" / "one.jsonl", "From now on, give the outline first.")
            with patch.dict(
                os.environ,
                {
                    "PREFERENCE_DISCOVERY_HOME": str(home),
                    "PREFERENCE_CHECKPOINT_DIR": str(root / "state"),
                    "PREFERENCE_CANDIDATE_DIR": str(root / "debug"),
                },
                clear=False,
            ):
                out = _run_cli(
                    [
                        "--store",
                        str(root / "prefs.md"),
                        "onboard",
                        "--agent",
                        "kimi",
                        "--mode",
                        "recall-only",
                        "--dry-run",
                        "--json",
                    ]
                )

            self.assertTrue(out["dry_run"])
            self.assertEqual(out["scan"]["changed_files"], 1)
            self.assertEqual(MarkdownPreferenceStore(root / "prefs.md").load(), [])

    def test_datailor_install_agent_rules_uses_managed_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "AGENTS.md"
            target.write_text("# Existing\n\nKeep this.\n", encoding="utf-8")

            first = _run_cli(["install-agent-rules", "--target", str(target)])
            second = _run_cli(["install-agent-rules", "--target", str(target)])
            text = target.read_text(encoding="utf-8")

            self.assertTrue(first["changed"])
            self.assertTrue(str(first["snippet"]).startswith("package:preference_agent.resources/"))
            self.assertEqual(second["action"], "replace")
            self.assertEqual(text.count("<!-- datailor-preference:start -->"), 1)
            self.assertNotIn("<!-- bondie-preference:start -->", text)
            self.assertIn("hook_session_start", text)
            self.assertIn("Keep this.", text)
            self.assertIn("managed_start", first)
            self.assertIn("snippet_preview", first)

    def test_datailor_install_agent_rules_replaces_legacy_bondie_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "AGENTS.md"
            target.write_text(
                "# Existing\n\n<!-- bondie-preference:start -->\nold\n<!-- bondie-preference:end -->\n",
                encoding="utf-8",
            )

            result = _run_cli(["install-agent-rules", "--target", str(target)])
            text = target.read_text(encoding="utf-8")

            self.assertEqual(result["action"], "replace")
            self.assertIn("<!-- datailor-preference:start -->", text)
            self.assertNotIn("<!-- bondie-preference:start -->", text)
            self.assertIn("hook_session_start", text)

    def test_datailor_install_kimi_hooks_updates_config_toml(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / ".kimi" / "config.toml"
            target.parent.mkdir(parents=True)
            target.write_text(
                'theme = "dark"\n'
                "hooks = []\n"
                "merge_all_available_skills = false\n"
                "\n"
                "[mcp.client]\n"
                "tool_call_timeout_ms = 60000\n",
                encoding="utf-8",
            )

            first = _run_cli(
                [
                    "--store",
                    str(root / "prefs.md"),
                    "install-kimi-hooks",
                    "--target",
                    str(target),
                    "--agent",
                    "kimi",
                ]
            )
            second = _run_cli(
                [
                    "--store",
                    str(root / "prefs.md"),
                    "install-kimi-hooks",
                    "--target",
                    str(target),
                    "--agent",
                    "kimi",
                ]
            )
            text = target.read_text(encoding="utf-8")

            self.assertTrue(first["changed"])
            self.assertEqual(second["action"], "replace")
            self.assertFalse(second["changed"])
            self.assertIn("# hooks = []  # disabled by Datailor", text)
            self.assertIn("# datailor:kimi-hooks:start", text)
            self.assertEqual(text.count("[[hooks]]"), 6)
            self.assertIn('event = "SessionStart"', text)
            self.assertIn('event = "PostToolUseFailure"', text)
            self.assertIn("datailor-kimi-hook", text)
            self.assertTrue(Path(first["backup"]).exists())

    def test_default_paths_use_user_data_dir_for_pipx_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "Datailor"
            env = {
                "DATAILOR_DATA_DIR": str(data_dir),
                "PREFERENCE_STORE_PATH": "",
                "PREFERENCE_CHECKPOINT_DIR": "",
                "PREFERENCE_CANDIDATE_DIR": "",
                "PREFERENCE_INCREMENTAL_STATE": "",
                "PREFERENCE_HOOKS_DIR": "",
                "PREFERENCE_INJECTION_DIR": "",
                "PREFERENCE_UI_DIR": "",
                "PREFERENCE_FEEDBACK_LOG": "",
                "PREFERENCE_EMBEDDING_CACHE_PATH": "",
            }
            with patch.dict(os.environ, env, clear=False):
                store_path = default_store_path()
                config = CaptureConfig.from_env()
                engine = build_mcp_engine()

            self.assertEqual(store_path, data_dir / "personal-preferences.md")
            self.assertEqual(config.store_path, data_dir / "personal-preferences.md")
            self.assertEqual(config.checkpoint_dir, data_dir / ".capture-state")
            self.assertEqual(config.candidate_dir, data_dir / ".debug-capture")
            self.assertEqual(engine.store.path, data_dir / "personal-preferences.md")

    def test_default_store_prefers_existing_legacy_store_when_default_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data_dir = root / "Datailor"
            data_dir.mkdir()
            (data_dir / "personal-preferences.md").write_text(
                "# Personal Preferences\n\n## Active Preferences\n\nNo active preferences yet.\n",
                encoding="utf-8",
            )
            legacy = data_dir / "个人偏好.md"
            legacy.write_text(
                "# Personal Preferences\n\n## Active Preferences\n\n- Prefer direct answers.\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"DATAILOR_DATA_DIR": str(data_dir), "PREFERENCE_STORE_PATH": ""}, clear=False):
                self.assertEqual(default_store_path(), legacy)

    def test_mcp_config_uses_datailor_mcp_command_without_repo_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(os.environ, {"PREFERENCE_STORE_PATH": ""}, clear=False):
                default_config = _run_cli(["mcp-config", "--agent", "kimi", "--no-agent-rules", "--no-kimi-hooks"])
                custom_store_config = _run_cli(
                    [
                        "mcp-config",
                        "--agent",
                        "kimi",
                        "--store",
                        str(root / "prefs.md"),
                        "--no-agent-rules",
                        "--no-kimi-hooks",
                    ]
                )

            server = default_config["mcpServers"]["datailor-preferences"]
            self.assertEqual(server["command"], "datailor-mcp")
            self.assertEqual(server["args"], ["--agent", "kimi"])
            self.assertNotIn("cwd", server)
            self.assertNotIn("PREFERENCE_STORE_PATH", server["env"])

            custom_server = custom_store_config["mcpServers"]["datailor-preferences"]
            self.assertEqual(custom_server["env"]["PREFERENCE_STORE_PATH"], str(root / "prefs.md"))

    def test_mcp_config_auto_installs_agent_rules_without_polluting_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "AGENTS.md"

            config = _run_cli(["mcp-config", "--agent", "codex", "--agent-rules-target", str(target)])
            text = target.read_text(encoding="utf-8")

            self.assertIn("mcpServers", config)
            self.assertIn("datailor-preferences", config["mcpServers"])
            self.assertIn("<!-- datailor-preference:start -->", text)
            self.assertIn("hook_session_start", text)

    def test_mcp_config_kimi_auto_installs_kimi_hooks_without_polluting_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agents_target = root / "AGENTS.md"
            kimi_target = root / ".kimi" / "config.toml"

            config = _run_cli(
                [
                    "--store",
                    str(root / "prefs.md"),
                    "mcp-config",
                    "--agent",
                    "kimi",
                    "--agent-rules-target",
                    str(agents_target),
                    "--kimi-hooks-target",
                    str(kimi_target),
                ]
            )
            text = kimi_target.read_text(encoding="utf-8")

            self.assertIn("mcpServers", config)
            self.assertIn("datailor-preferences", config["mcpServers"])
            self.assertIn("# datailor:kimi-hooks:start", text)
            self.assertIn('event = "UserPromptSubmit"', text)
            self.assertIn(str(root / "prefs.md").replace("\\", "\\\\"), text)

    def test_onboard_auto_installs_agent_rules_unless_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "AGENTS.md"

            out = _run_cli(
                [
                    "--store",
                    str(root / "prefs.md"),
                    "onboard",
                    "--agent",
                    "codex",
                    "--no-capture",
                    "--agent-rules-target",
                    str(target),
                    "--json",
                ]
            )

            self.assertTrue(out["agent_rules"]["changed"])
            self.assertEqual(out["agent_rules"]["target"], str(target))
            self.assertIn("hook_session_start", target.read_text(encoding="utf-8"))

            disabled_target = root / "disabled-AGENTS.md"
            disabled = _run_cli(
                [
                    "--store",
                    str(root / "disabled-prefs.md"),
                    "onboard",
                    "--agent",
                    "codex",
                    "--no-capture",
                    "--agent-rules-target",
                    str(disabled_target),
                    "--no-agent-rules",
                    "--json",
                ]
            )

            self.assertIsNone(disabled["agent_rules"])
            self.assertFalse(disabled_target.exists())

    def test_onboard_kimi_auto_installs_kimi_hooks_unless_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agents_target = root / "AGENTS.md"
            kimi_target = root / ".kimi" / "config.toml"

            out = _run_cli(
                [
                    "--store",
                    str(root / "prefs.md"),
                    "onboard",
                    "--agent",
                    "kimi",
                    "--no-capture",
                    "--agent-rules-target",
                    str(agents_target),
                    "--kimi-hooks-target",
                    str(kimi_target),
                    "--json",
                ]
            )

            self.assertTrue(out["kimi_hooks"]["changed"])
            self.assertEqual(out["kimi_hooks"]["target"], str(kimi_target))
            self.assertIn('event = "SessionEnd"', kimi_target.read_text(encoding="utf-8"))

            disabled_target = root / ".kimi-disabled" / "config.toml"
            disabled = _run_cli(
                [
                    "--store",
                    str(root / "disabled-prefs.md"),
                    "onboard",
                    "--agent",
                    "kimi",
                    "--no-capture",
                    "--kimi-hooks-target",
                    str(disabled_target),
                    "--no-kimi-hooks",
                    "--json",
                ]
            )

            self.assertIsNone(disabled["kimi_hooks"])
            self.assertFalse(disabled_target.exists())

    def test_capture_job_honors_global_store_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "history.jsonl"
            store = root / "custom-store.md"
            source.write_text(
                json.dumps({"role": "user", "content": "From now on, give the conclusion first, then expand."}) + "\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "PREFERENCE_CHECKPOINT_DIR": str(root / "state"),
                    "PREFERENCE_CANDIDATE_DIR": str(root / "debug"),
                },
                clear=False,
            ):
                out = _run_cli(
                    [
                        "--store",
                        str(store),
                        "capture-job",
                        "--source",
                        str(source),
                        "--project-root",
                        str(root),
                        "--mode",
                        "recall-only",
                    ]
                )

            self.assertEqual(out["store_file"], str(store))
            self.assertTrue(store.exists())

    def test_cold_start_scan_defaults_to_human_summary_and_json_remains_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            store = root / "prefs.md"
            _write_history(home / ".kimi" / "user-history" / "one.jsonl", "From now on, give the conclusion first.")
            env = {
                "PREFERENCE_DISCOVERY_HOME": str(home),
                "PREFERENCE_CHECKPOINT_DIR": str(root / "state"),
                "PREFERENCE_CANDIDATE_DIR": str(root / "debug"),
            }
            with patch.dict(os.environ, env, clear=False):
                human = _run_cli_text(
                    [
                        "--store",
                        str(store),
                        "cold-start-scan",
                        "--agent",
                        "kimi",
                        "--mode",
                        "recall-only",
                        "--dry-run",
                    ]
                )
                data = _run_cli(
                    [
                        "--store",
                        str(store),
                        "cold-start-scan",
                        "--agent",
                        "kimi",
                        "--mode",
                        "recall-only",
                        "--dry-run",
                        "--json",
                    ]
                )

            self.assertIn("Datailor cold-start scan", human)
            self.assertIn("Sources discovered", human)
            self.assertIn("Scanning", human)
            self.assertIn("Result", human)
            self.assertIn("Completed:", human)
            self.assertIn("datailor integrate status --client all", human)
            self.assertFalse(human.lstrip().startswith("{"))
            self.assertEqual(data["store"], str(store))
            self.assertIn("summary", data)
            self.assertIn("summary_text", data)

    def test_onboard_defaults_to_human_summary_and_quiet_is_compact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            _write_history(home / ".codex" / "history.jsonl", "From now on, give the conclusion first.")
            env = {
                "PREFERENCE_DISCOVERY_HOME": str(home),
                "PREFERENCE_CHECKPOINT_DIR": str(root / "state"),
                "PREFERENCE_CANDIDATE_DIR": str(root / "debug"),
            }
            with patch.dict(os.environ, env, clear=False):
                human = _run_cli_text(
                    [
                        "--store",
                        str(root / "prefs.md"),
                        "onboard",
                        "--agent",
                        "codex",
                        "--mode",
                        "recall-only",
                        "--dry-run",
                    ]
                )
                quiet = _run_cli_text(
                    [
                        "--store",
                        str(root / "prefs.md"),
                        "onboard",
                        "--agent",
                        "codex",
                        "--mode",
                        "recall-only",
                        "--dry-run",
                        "--quiet",
                    ]
                )

            self.assertIn("Datailor onboard", human)
            self.assertIn("Result", human)
            self.assertLess(len(quiet.splitlines()), len(human.splitlines()))
            self.assertIn("Next: datailor ui", quiet)
            self.assertIn("IDE: datailor integrate status --client all", quiet)

    def test_mcp_exposes_onboarding_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = PreferenceEngine(MarkdownPreferenceStore(root / "prefs.md"))
            init = handle_request({"jsonrpc": "2.0", "id": 0, "method": "initialize"}, engine)
            self.assertEqual(init["result"]["serverInfo"]["name"], "datailor-preference-mcp")
            self.assertEqual(init["result"]["serverInfo"]["version"], package_version())

            tools = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, engine)
            tool_text = json.dumps(tools, ensure_ascii=False)
            self.assertIn("get_onboarding_status", tool_text)
            self.assertIn("start_cold_start_capture", tool_text)

            response = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "get_onboarding_status", "arguments": {"agent": "codex"}},
                },
                engine,
            )
            text = response["result"]["content"][0]["text"]
            self.assertIn("datailor", text)

            capture = handle_request(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "start_cold_start_capture",
                        "arguments": {"agent": "codex", "dry_run": True, "max_files": 0},
                    },
                },
                engine,
            )
            capture_text = capture["result"]["content"][0]["text"]
            self.assertIn("summary_text", capture_text)


def _write_history(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"role": "user", "content": text}, ensure_ascii=False) + "\n", encoding="utf-8")


def _run_cli(argv: list[str]) -> dict[str, object]:
    buffer = StringIO()
    with redirect_stdout(buffer):
        code = cli_main(argv)
    if code != 0:
        raise AssertionError(f"CLI exited with {code}")
    return json.loads(buffer.getvalue())


def _run_cli_text(argv: list[str]) -> str:
    buffer = StringIO()
    with redirect_stdout(buffer):
        code = cli_main(argv)
    if code != 0:
        raise AssertionError(f"CLI exited with {code}")
    return buffer.getvalue()


if __name__ == "__main__":
    unittest.main()

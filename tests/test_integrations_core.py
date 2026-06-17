from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from preference_agent.cli import main as cli_main
from preference_agent.integrations import run_integrations
from preference_agent.integrations.registry import expand_clients


class IntegrationCoreTests(unittest.TestCase):
    def test_openclaw_is_explicit_and_not_in_default_all_clients(self) -> None:
        self.assertNotIn("openclaw", expand_clients("all"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(os.environ, {"DATAILOR_INTEGRATION_HOME": str(root)}, clear=False):
                status = _run_cli(["integrate", "status", "--client", "all", "--json"])

        self.assertTrue(status["ok"])
        self.assertEqual(len(status["results"]), 3)
        self.assertNotIn("openclaw", {item["client"] for item in status["results"]})

    def test_openclaw_install_registers_plugin_and_is_idempotent(self) -> None:
        state = {"plugin": False, "enabled": False, "hooks": False, "mcp": False}

        def fake_run(args, **kwargs):
            if args[1:3] == ["mcp", "list"]:
                stdout = "datailor-preferences\n" if state["mcp"] else ""
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
            if args[1:4] == ["plugins", "list", "--json"]:
                plugins = [_openclaw_plugin_list_entry(state)] if state["plugin"] else []
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=json.dumps({"plugins": plugins}), stderr="")
            if args[1:4] == ["plugins", "inspect", "datailor-preferences"]:
                hooks = _openclaw_hook_payload() if state["hooks"] and state["enabled"] else {"typedHooks": []}
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=json.dumps(hooks), stderr="")
            if args[1:3] == ["plugins", "install"]:
                state["plugin"] = True
                state["enabled"] = True
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            if args[1:3] == ["plugins", "enable"]:
                state["enabled"] = True
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            if args[1:3] == ["config", "patch"]:
                self.assertIn("allowConversationAccess", kwargs["input"])
                state["hooks"] = True
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            if args[1:3] == ["mcp", "set"]:
                state["mcp"] = True
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected command: {args}")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            command = str(root / "openclaw.CMD")
            with patch.dict(os.environ, {"DATAILOR_INTEGRATION_HOME": str(root)}, clear=False):
                with patch("preference_agent.integrations.installer.which", return_value=command):
                    with patch("preference_agent.integrations.installer.subprocess.run", side_effect=fake_run) as run:
                        first = run_integrations(action="install", client="openclaw", scope="user")
                        second = run_integrations(action="install", client="openclaw", scope="user")
            plugin_path = Path(first["results"][0]["details"]["plugin_path"])
            self.assertTrue((plugin_path / "index.js").exists())
            self.assertTrue((plugin_path / "openclaw.plugin.json").exists())

        self.assertTrue(first["ok"])
        self.assertTrue(first["results"][0]["changed"])
        self.assertTrue(second["ok"])
        self.assertFalse(second["results"][0]["changed"])
        calls = [call.args[0] for call in run.call_args_list]
        self.assertIn([command, "plugins", "install", str(plugin_path), "--link"], calls)
        mcp_set = [args for args in calls if args[1:3] == ["mcp", "set"]][0]
        self.assertEqual(mcp_set[:4], [command, "mcp", "set", "datailor-preferences"])
        self.assertIn('"--agent"', mcp_set[4])
        self.assertIn('"openclaw"', mcp_set[4])

    def test_openclaw_custom_server_name_keeps_fixed_plugin_id(self) -> None:
        state = {"plugin": False, "enabled": False, "hooks": False, "mcp": False}
        seen_inspect_ids: list[str] = []
        seen_patch = ""

        def fake_run(args, **kwargs):
            nonlocal seen_patch
            if args[1:3] == ["mcp", "list"]:
                stdout = "datailor-review-test\n" if state["mcp"] else ""
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
            if args[1:4] == ["plugins", "list", "--json"]:
                plugins = [_openclaw_plugin_list_entry(state)] if state["plugin"] else []
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=json.dumps({"plugins": plugins}), stderr="")
            if args[1:3] == ["plugins", "inspect"]:
                seen_inspect_ids.append(args[3])
                hooks = _openclaw_hook_payload() if state["hooks"] and state["enabled"] else {"typedHooks": []}
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=json.dumps(hooks), stderr="")
            if args[1:3] == ["plugins", "install"]:
                state["plugin"] = True
                state["enabled"] = True
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            if args[1:3] == ["config", "patch"]:
                seen_patch = kwargs["input"]
                state["hooks"] = True
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            if args[1:3] == ["mcp", "set"]:
                self.assertEqual(args[3], "datailor-review-test")
                state["mcp"] = True
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected command: {args}")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            command = str(root / "openclaw.CMD")
            with patch.dict(os.environ, {"DATAILOR_INTEGRATION_HOME": str(root)}, clear=False):
                with patch("preference_agent.integrations.installer.which", return_value=command):
                    with patch("preference_agent.integrations.installer.subprocess.run", side_effect=fake_run):
                        result = run_integrations(
                            action="install",
                            client="openclaw",
                            scope="user",
                            server_name="datailor-review-test",
                        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["results"][0]["details"]["server_name"], "datailor-review-test")
        self.assertEqual(result["results"][0]["details"]["plugin_id"], "datailor-preferences")
        self.assertIn("datailor-preferences", seen_inspect_ids)
        self.assertNotIn("datailor-review-test", seen_inspect_ids)
        self.assertIn('"datailor-preferences"', seen_patch)
        self.assertNotIn('"datailor-review-test"', seen_patch)

    def test_openclaw_remove_disables_plugin_hooks_and_is_idempotent(self) -> None:
        state = {"plugin": True, "enabled": True, "hooks": True, "mcp": True}

        def fake_run(args, **kwargs):
            if args[1:3] == ["mcp", "list"]:
                stdout = "datailor-preferences\n" if state["mcp"] else ""
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
            if args[1:4] == ["plugins", "list", "--json"]:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=json.dumps({"plugins": [_openclaw_plugin_list_entry(state)]}), stderr="")
            if args[1:4] == ["plugins", "inspect", "datailor-preferences"]:
                hooks = _openclaw_hook_payload() if state["hooks"] and state["enabled"] else {"typedHooks": []}
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=json.dumps(hooks), stderr="")
            if args[1:3] == ["mcp", "unset"]:
                state["mcp"] = False
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            if args[1:3] == ["plugins", "disable"]:
                self.assertEqual(args[3], "datailor-preferences")
                state["enabled"] = False
                state["hooks"] = False
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected command: {args}")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            command = str(root / "openclaw.CMD")
            with patch.dict(os.environ, {"DATAILOR_INTEGRATION_HOME": str(root)}, clear=False):
                with patch("preference_agent.integrations.installer.which", return_value=command):
                    with patch("preference_agent.integrations.installer.subprocess.run", side_effect=fake_run) as run:
                        first = run_integrations(action="remove", client="openclaw", scope="user")
                        second = run_integrations(action="remove", client="openclaw", scope="user")

        self.assertTrue(first["ok"])
        self.assertTrue(first["results"][0]["changed"])
        self.assertEqual(first["results"][0]["state"], "not_configured")
        self.assertFalse(first["results"][0]["details"]["plugin_enabled"])
        self.assertFalse(first["results"][0]["details"]["hooks_ready"])
        self.assertTrue(second["ok"])
        self.assertFalse(second["results"][0]["changed"])
        calls = [call.args[0] for call in run.call_args_list]
        self.assertIn([command, "plugins", "disable", "datailor-preferences"], calls)

    def test_openclaw_install_returns_structured_failure_if_cli_launch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            command = str(root / "openclaw.CMD")
            with patch.dict(os.environ, {"DATAILOR_INTEGRATION_HOME": str(root)}, clear=False):
                with patch("preference_agent.integrations.installer.which", return_value=command):
                    with patch("preference_agent.integrations.installer.subprocess.run", side_effect=FileNotFoundError("missing")):
                        result = run_integrations(action="install", client="openclaw", scope="user")

        self.assertFalse(result["ok"])
        self.assertEqual(result["results"][0]["state"], "error")
        self.assertIn("failed to launch", result["results"][0]["warnings"][0])

    def test_openclaw_doctor_requires_hook_plugin_template(self) -> None:
        def fake_run(args, **kwargs):
            if args[1:3] == ["mcp", "list"]:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="datailor-preferences\n", stderr="")
            if args[1:4] == ["plugins", "list", "--json"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=json.dumps({"plugins": [_openclaw_plugin_list_entry({"plugin": True, "enabled": True, "hooks": True, "mcp": True})]}),
                    stderr="",
                )
            if args[1:4] == ["plugins", "inspect", "datailor-preferences"]:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout=json.dumps(_openclaw_hook_payload()), stderr="")
            raise AssertionError(f"unexpected command: {args}")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            command = str(root / "openclaw.CMD")
            with patch.dict(os.environ, {"DATAILOR_INTEGRATION_HOME": str(root)}, clear=False):
                with patch("preference_agent.integrations.installer.which", return_value=command):
                    with patch("preference_agent.integrations.installer.subprocess.run", side_effect=fake_run):
                        result = run_integrations(action="doctor", client="openclaw", scope="user")

        self.assertFalse(result["ok"])
        self.assertEqual(result["results"][0]["state"], "configured")
        self.assertIn("plugin files are not installed", result["results"][0]["warnings"][0])

    def test_openclaw_export_plugin_creates_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = _run_cli(["integrate", "export-plugin", "--client", "openclaw", "--output", str(root)])
            destination = Path(out["results"][0]["details"]["destination"])

            self.assertTrue((destination / "package.json").exists())
            self.assertTrue((destination / "index.js").exists())
            self.assertTrue((destination / "README.md").exists())
            self.assertTrue((destination / "openclaw.plugin.json").exists())
            package = json.loads((destination / "package.json").read_text(encoding="utf-8"))
            self.assertIn("./index.js", package["openclaw"]["extensions"])
            self.assertIn("./index.js", package["openclaw"]["runtimeExtensions"])
            manifest = json.loads((destination / "openclaw.plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["id"], "datailor-preferences")
            plugin_text = (destination / "index.js").read_text(encoding="utf-8")
            self.assertIn("before_prompt_build", plugin_text)
            self.assertIn('api.on("agent_end"', plugin_text)
            self.assertNotIn('api.on("llm_output"', plugin_text)
            self.assertIn("datailor-openclaw-hook", plugin_text)
            self.assertIn("openclaw/plugin-sdk/plugin-entry", plugin_text)
            self.assertIn("openclaw/plugin-sdk/process-runtime", plugin_text)
            self.assertNotIn("child_process", plugin_text)
            self.assertIn("(payload, context)", plugin_text)
            self.assertIn("context", plugin_text)

    def test_codex_install_is_idempotent_and_remove_preserves_existing_toml(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            codex_config = root / ".codex" / "config.toml"
            codex_config.parent.mkdir(parents=True)
            codex_config.write_text(
                'model = "gpt-5"\n\n'
                "[mcp_servers.other]\n"
                'command = "other-mcp"\n',
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"DATAILOR_INTEGRATION_HOME": str(root)}, clear=False):
                first = run_integrations(action="install", client="codex", scope="user", store=root / "prefs.md")
                second = run_integrations(action="install", client="codex", scope="user", store=root / "prefs.md")
                removed = run_integrations(action="remove", client="codex", scope="user")

            self.assertTrue(first["results"][0]["changed"])
            self.assertFalse(second["results"][0]["changed"])
            self.assertTrue(Path(first["results"][0]["backup_path"]).exists())
            text = codex_config.read_text(encoding="utf-8")
            self.assertNotIn("# datailor:integrations:start", text)
            self.assertIn("[mcp_servers.other]", text)
            self.assertTrue(removed["results"][0]["changed"])

    def test_kimi_json_install_dry_run_backup_and_remove(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            kimi_config = root / ".kimi" / "mcp.json"
            kimi_config.parent.mkdir(parents=True)
            kimi_config.write_text(
                json.dumps({"mcpServers": {"other": {"command": "other"}}}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"DATAILOR_INTEGRATION_HOME": str(root)}, clear=False):
                dry = run_integrations(action="install", client="kimi", scope="user", dry_run=True)
                self.assertNotIn("datailor-preferences", kimi_config.read_text(encoding="utf-8"))
                installed = run_integrations(action="install", client="kimi", scope="user")
                removed = run_integrations(action="remove", client="kimi", scope="user")

            self.assertTrue(dry["results"][0]["changed"])
            self.assertTrue(installed["results"][0]["changed"])
            self.assertTrue(Path(installed["results"][0]["backup_path"]).exists())
            data = json.loads(kimi_config.read_text(encoding="utf-8"))
            self.assertIn("other", data["mcpServers"])
            self.assertNotIn("datailor-preferences", data["mcpServers"])
            self.assertTrue(removed["results"][0]["changed"])

    def test_claude_project_config_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            project.mkdir()
            config = project / ".mcp.json"
            config.write_text(
                json.dumps({"mcpServers": {"other": {"command": "other"}}}, ensure_ascii=False),
                encoding="utf-8",
            )

            result = run_integrations(action="install", client="claude", scope="project", project_root=project)
            data = json.loads(config.read_text(encoding="utf-8"))

            self.assertTrue(result["results"][0]["changed"])
            self.assertIn("other", data["mcpServers"])
            self.assertEqual(data["mcpServers"]["datailor-preferences"]["args"], ["--agent", "claude"])

    def test_claude_cli_default_scope_uses_project_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            with patch.dict(os.environ, {"DATAILOR_INTEGRATION_HOME": str(root)}, clear=False):
                out = _run_cli(
                    [
                        "integrate",
                        "install",
                        "--client",
                        "claude",
                        "--project-root",
                        str(project),
                        "--dry-run",
                    ]
                )

            result = out["results"][0]
            self.assertEqual(result["scope"], "project")
            self.assertTrue(result["dry_run"])
            self.assertEqual(Path(result["target_path"]), project / ".mcp.json")
            self.assertFalse((root / ".claude.json").exists())
            self.assertFalse((project / ".mcp.json").exists())

    def test_cli_integrate_status_and_dry_run_do_not_write_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(os.environ, {"DATAILOR_INTEGRATION_HOME": str(root)}, clear=False):
                status = _run_cli(["integrate", "status", "--client", "all", "--json"])
                dry = _run_cli(["integrate", "install", "--client", "codex", "--dry-run"])

            self.assertTrue(status["ok"])
            self.assertEqual(len(status["results"]), 3)
            self.assertTrue(dry["results"][0]["dry_run"])
            self.assertFalse((root / ".codex" / "config.toml").exists())

    def test_cli_export_plugin_creates_templates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = _run_cli(["integrate", "export-plugin", "--client", "codex", "--output", str(root)])
            destination = Path(out["results"][0]["details"]["destination"])

            self.assertTrue((destination / ".codex-plugin" / "plugin.json").exists())
            self.assertTrue((destination / ".mcp.json").exists())
            self.assertIn("datailor-preferences", (destination / ".mcp.json").read_text(encoding="utf-8"))

    def test_cli_export_plugin_dry_run_does_not_write_or_delete_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "kimi-datailor"
            destination.mkdir()
            sentinel = destination / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")

            out = _run_cli(["integrate", "export-plugin", "--client", "kimi", "--output", str(root), "--dry-run"])

            result = out["results"][0]
            self.assertTrue(result["dry_run"])
            self.assertTrue(result["changed"])
            self.assertEqual(result["state"], "planned")
            self.assertTrue(result["details"]["would_replace"])
            self.assertEqual(Path(result["details"]["destination"]), destination)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertFalse((destination / "plugin.json").exists())

    def test_kimi_export_plugin_uses_current_python_and_absolute_tool_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            out = _run_cli(["integrate", "export-plugin", "--client", "kimi", "--output", str(root)])
            destination = Path(out["results"][0]["details"]["destination"])
            plugin = json.loads((destination / "plugin.json").read_text(encoding="utf-8"))
            command = plugin["tools"][0]["command"]

            self.assertEqual(command[0], sys.executable)
            self.assertTrue(Path(command[1]).is_absolute())
            self.assertEqual(Path(command[1]), destination / "scripts" / "datailor_tool.py")
            self.assertTrue(Path(command[1]).exists())
            script = Path(command[1]).read_text(encoding="utf-8")
            self.assertIn("preference_agent.cli", script)
            self.assertNotIn('["datailor"', script)

            env = os.environ.copy()
            env["PATH"] = ""
            completed = subprocess.run(
                command,
                input=json.dumps({"command": "doctor"}),
                text=True,
                capture_output=True,
                env=env,
                timeout=15,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["returncode"], 0)
            self.assertIn("Datailor", payload["stdout"])

    def test_onboard_explicit_integrate_client_uses_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = {"DATAILOR_INTEGRATION_HOME": str(root)}
            with patch.dict(os.environ, env, clear=False):
                out = _run_cli(
                    [
                        "--store",
                        str(root / "prefs.md"),
                        "onboard",
                        "--agent",
                        "codex",
                        "--no-capture",
                        "--no-agent-rules",
                        "--integrate-client",
                        "codex",
                        "--integrate-dry-run",
                        "--json",
                    ]
                )

            self.assertTrue(out["integrations"][0]["results"][0]["dry_run"])
            self.assertFalse((root / ".codex" / "config.toml").exists())

    def test_codex_generated_toml_is_parseable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(os.environ, {"DATAILOR_INTEGRATION_HOME": str(root)}, clear=False):
                run_integrations(action="install", client="codex", scope="user")

            config = root / ".codex" / "config.toml"
            data = tomllib.loads(config.read_text(encoding="utf-8"))
            self.assertIn("datailor-preferences", data["mcp_servers"])


def _run_cli(argv: list[str]) -> dict[str, object]:
    buffer = StringIO()
    with redirect_stdout(buffer):
        code = cli_main(argv)
    if code != 0:
        raise AssertionError(f"CLI exited with {code}")
    return json.loads(buffer.getvalue())


def _openclaw_hook_payload() -> dict[str, object]:
    return {
        "typedHooks": [
            {"name": "session_start"},
            {"name": "before_prompt_build"},
            {"name": "agent_turn_prepare"},
            {"name": "after_tool_call"},
            {"name": "agent_end"},
            {"name": "session_end"},
        ]
    }


def _openclaw_plugin_list_entry(state: dict[str, bool]) -> dict[str, object]:
    return {
        "id": "datailor-preferences",
        "enabled": state["enabled"],
        "status": "loaded" if state["enabled"] else "disabled",
    }


if __name__ == "__main__":
    unittest.main()

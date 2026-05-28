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


class IntegrationCoreTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

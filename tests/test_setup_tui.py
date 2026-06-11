from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preference_agent import config_env
from preference_agent.cli import _onboard_is_bare, _should_run_setup_tui


class ConfigEnvTests(unittest.TestCase):
    def test_update_env_file_upserts_and_preserves_other_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "datailor.env"
            target.write_text("# header\nPREFERENCE_MODEL_BACKEND=heuristic\nKEEP=1\n", encoding="utf-8")

            config_env.update_env_file(
                {"PREFERENCE_MODEL_BACKEND": "openai-compatible", "PREFERENCE_MODEL_NAME": "gpt x"},
                path=target,
                apply_to_process=False,
            )
            text = target.read_text(encoding="utf-8")

            self.assertIn("# header", text)
            self.assertIn("KEEP=1", text)
            self.assertIn("PREFERENCE_MODEL_BACKEND=openai-compatible", text)
            self.assertEqual(text.count("PREFERENCE_MODEL_BACKEND="), 1)
            self.assertIn('PREFERENCE_MODEL_NAME="gpt x"', text)

    def test_load_env_file_does_not_override_real_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "datailor.env"
            target.write_text("PREFERENCE_MODEL_BACKEND=openai-compatible\nDATAILOR_TEST_ONLY=fromfile\n", encoding="utf-8")
            with patch.dict(os.environ, {"PREFERENCE_MODEL_BACKEND": "heuristic"}, clear=False):
                os.environ.pop("DATAILOR_TEST_ONLY", None)
                config_env.load_env_file(target)
                self.assertEqual(os.environ["PREFERENCE_MODEL_BACKEND"], "heuristic")
                self.assertEqual(os.environ["DATAILOR_TEST_ONLY"], "fromfile")

    def test_is_datailor_enabled_real_environment_overrides_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "datailor.env"
            target.write_text("DATAILOR_ENABLED=false\n", encoding="utf-8")
            with patch.dict(os.environ, {"DATAILOR_ENV_FILE": str(target), "DATAILOR_ENABLED": "true"}, clear=False):
                self.assertTrue(config_env.is_datailor_enabled())


class OnboardGatingTests(unittest.TestCase):
    def test_onboard_is_bare(self) -> None:
        self.assertTrue(_onboard_is_bare(["onboard"]))
        self.assertTrue(_onboard_is_bare(["--store", "x", "onboard"]))
        self.assertTrue(_onboard_is_bare(["onboard", "--agent", "codex"]))
        self.assertFalse(_onboard_is_bare(["onboard", "--json"]))
        self.assertFalse(_onboard_is_bare(["onboard", "--agent", "codex", "--mode", "recall-only"]))

    def test_should_run_setup_tui_respects_flags_and_tty(self) -> None:
        ns = argparse.Namespace(json=False, quiet=False, interactive=None)
        # forced off
        ns.interactive = False
        self.assertFalse(_should_run_setup_tui(ns, ["onboard"]))
        # forced on
        ns.interactive = True
        self.assertTrue(_should_run_setup_tui(ns, ["onboard", "--mode", "x"]))
        # json wins over interactive auto
        ns.interactive = None
        ns.json = True
        self.assertFalse(_should_run_setup_tui(ns, ["onboard"]))
        # auto: bare + tty
        ns.json = False
        with patch("sys.stdin") as stdin, patch("sys.stdout") as stdout:
            stdin.isatty.return_value = True
            stdout.isatty.return_value = True
            self.assertTrue(_should_run_setup_tui(ns, ["onboard"]))
            self.assertFalse(_should_run_setup_tui(ns, ["onboard", "--mode", "x"]))
            stdout.isatty.return_value = False
            self.assertFalse(_should_run_setup_tui(ns, ["onboard"]))


class _Stub:
    def __init__(self, value):
        self._value = value

    def ask(self):
        return self._value


class _FakeQuestionary:
    """Minimal questionary stand-in returning scripted answers per method."""

    def __init__(self, answers: dict[str, list]):
        self._answers = {key: list(value) for key, value in answers.items()}

    class Choice:  # noqa: D401 - mirrors questionary.Choice
        def __init__(self, title, value=None, checked=False):
            self.title = title
            self.value = value if value is not None else title
            self.checked = checked

    def _next(self, kind):
        return _Stub(self._answers[kind].pop(0))

    def select(self, *_a, **_k):
        return self._next("select")

    def confirm(self, *_a, **_k):
        return self._next("confirm")

    def text(self, *_a, **_k):
        return self._next("text")

    def password(self, *_a, **_k):
        return self._next("password")

    def checkbox(self, *_a, **_k):
        return self._next("checkbox")


class WizardFlowTests(unittest.TestCase):
    def test_wizard_runs_end_to_end_with_scripted_answers(self) -> None:
        from rich.console import Console

        from preference_agent.setup_tui import _Wizard

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            (home / ".codex").mkdir(parents=True)
            (home / ".codex" / "history.jsonl").write_text(
                json.dumps({"role": "user", "content": "From now on, give the conclusion first."}) + "\n",
                encoding="utf-8",
            )
            data_dir = root / "data"
            answers = {
                "select": ["heuristic", "curate", "skip"],  # backend, mode, scan
                "confirm": [False, False, False],  # preview?, install rules? (skip: avoid touching ~/AGENTS.md), open ui?
                "checkbox": [[]],  # no clients
            }
            console = Console(file=open(os.devnull, "w", encoding="utf-8"), force_terminal=False)
            env = {
                "DATAILOR_DATA_DIR": str(data_dir),
                "PREFERENCE_DISCOVERY_HOME": str(home),
                "PREFERENCE_STORE_PATH": "",
            }
            with patch.dict(os.environ, env, clear=False):
                wizard = _Wizard(
                    console,
                    _FakeQuestionary(answers),
                    root / "prefs.md",
                    agent_hint="codex",
                    backend="heuristic",
                )
                code = wizard.run()

            self.assertEqual(code, 0)
            self.assertTrue((data_dir / "datailor.env").exists())
            self.assertIn("Fitting mode set to curate.", "\n".join(wizard.actions))


if __name__ == "__main__":
    unittest.main()

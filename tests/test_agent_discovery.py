from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from preference_agent.agent_discovery import auto_discover_sources, cold_start_scan, detect_installed_agents
from preference_agent.capture_runner import CaptureConfig


class AgentDiscoveryTests(unittest.TestCase):
    def test_detects_supported_jsonl_agents_and_sorts_by_activity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            claude = home / ".claude" / "history.jsonl"
            codex = home / ".codex" / "history.jsonl"
            kimi = home / ".kimi" / "user-history" / "one.jsonl"
            _write_jsonl(claude, "From now on, give the conclusion first.")
            _write_jsonl(codex, "Run tests by default after code changes.")
            _write_jsonl(kimi, "Keep replies concise.")
            os.utime(claude, (100, 100))
            os.utime(codex, (300, 300))
            os.utime(kimi, (200, 200))

            agents = detect_installed_agents(home=home, system="windows")
            installed = {agent.name: agent for agent in agents if agent.installed}
            self.assertIn("claude", installed)
            self.assertIn("codex", installed)
            self.assertIn("kimi", installed)

            sources = auto_discover_sources(home=home, system="windows")
            self.assertEqual([Path(source.path).name for source in sources], ["history.jsonl", "one.jsonl", "history.jsonl"])
            self.assertEqual([source.agent for source in sources], ["codex", "kimi", "claude"])

    def test_agent_hint_moves_current_agent_first_then_uses_recency(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            claude = home / ".claude" / "history.jsonl"
            codex = home / ".codex" / "history.jsonl"
            _write_jsonl(claude, "From now on, give the conclusion first.")
            _write_jsonl(codex, "Run tests by default after code changes.")
            os.utime(claude, (100, 100))
            os.utime(codex, (300, 300))

            sources = auto_discover_sources(agent_hint="claude", home=home, system="windows")

            self.assertEqual([source.agent for source in sources], ["claude", "codex"])

    def test_cold_start_scan_uses_incremental_scan_for_discovered_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            home = root / "home"
            source = home / ".kimi" / "user-history" / "one.jsonl"
            _write_jsonl(source, "From now on, give me an outline first.")
            config = CaptureConfig(
                project_root=root,
                candidate_dir=root / "debug",
                checkpoint_dir=root / "state",
                store_path=root / "prefs.md",
                mode="recall-only",
            )

            result = cold_start_scan(
                store_path=root / "prefs.md",
                config=config,
                dry_run=True,
                home=home,
            )

            self.assertTrue(result.ok)
            self.assertEqual(result.scanned_sources, 1)
            self.assertEqual(result.changed_files, 1)
            self.assertEqual(result.scan_results[0]["dry_run"], True)


def _write_jsonl(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"role": "user", "content": text}, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()

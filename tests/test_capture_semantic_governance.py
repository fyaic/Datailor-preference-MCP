from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from preference_agent.backends import HeuristicBackend, ModelRequestError, PreferenceModelBackend, backend_status
from preference_agent.capture_governance import classify_capture_candidate, evaluate_capture_candidate
from preference_agent.capture_runner import CaptureConfig, CaptureRunner
from preference_agent.cli import main as cli_main
from preference_agent.engine import PreferenceEngine
from preference_agent.hooks import PreferenceHookManager
from preference_agent.models import PreferenceRecord, Session
from preference_agent.quality import should_recall_user_text
from preference_agent.recall import MultiRouteRecallEngine, RecallConfig, RecallInput
from preference_agent.store import MarkdownPreferenceStore
from preference_agent.ui.server import build_manifesto


class _FailingClassifierBackend(PreferenceModelBackend):
    def extract_preferences(self, session: Session) -> list[PreferenceRecord]:
        return []

    def merge_decision(self, candidate: PreferenceRecord, existing: list[PreferenceRecord]) -> dict[str, object]:
        return {"action": "new", "reason": "test"}

    def decide(
        self,
        task: str,
        context: dict[str, object],
        records: list[PreferenceRecord],
        agent: str = "agent",
    ) -> dict[str, object]:
        return {"decision": "no_preference", "matched_preferences": [], "agent_instruction": ""}

    def classify_preference_candidate(self, text: str, context_hint: str = "") -> dict[str, object]:
        raise ModelRequestError("model unavailable", status_code=503)


class _ReviewFirstClassifierBackend(_FailingClassifierBackend):
    def classify_preference_candidate(self, text: str, context_hint: str = "") -> dict[str, object]:
        return {
            "has_preference_signal": True,
            "signal_type": "execution_policy",
            "scope": "global",
            "durability": "likely_durable",
            "confidence": 0.88,
            "reason_codes": ["semantic_candidate"],
            "should_extract": True,
            "summary": "Review first, then patch after approval.",
        }


class CaptureSemanticGovernanceTests(unittest.TestCase):
    def test_gold_corpus_candidate_gate_covers_implicit_chinese_and_english(self) -> None:
        examples = [
            ("你又直接改了，我刚才是要你先分析，不要动代码。", True),
            ("That's not what I meant; I want review first, then patches only after I approve.", False),
        ]

        for text, should_miss_old_gate in examples:
            with self.subTest(text=text):
                if should_miss_old_gate:
                    self.assertFalse(should_recall_user_text(text))
                diagnostic = evaluate_capture_candidate(text)
                self.assertTrue(diagnostic.is_candidate)
                self.assertTrue({"heuristic_candidate", "semantic_candidate"} & set(diagnostic.reason_codes))
                self.assertTrue(diagnostic.should_extract)

    def test_one_off_path_gets_explainable_skip_without_active_storage(self) -> None:
        diagnostic = evaluate_capture_candidate(r"帮我打开 C:\tmp\a.md。")

        self.assertFalse(diagnostic.is_candidate)
        self.assertIn("one_off_filtered", diagnostic.reason_codes)
        self.assertFalse(diagnostic.should_extract)

    def test_hook_turn_complete_buffers_and_stores_implicit_chinese_preference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
            hooks = PreferenceHookManager(engine, hooks_dir=root / "hooks", max_turns=1)

            result = hooks.on_turn_complete(
                user_message="你又直接改了，我刚才是要你先分析，不要动代码。",
                assistant_response="收到。",
                agent="codex",
                session_id="s-implicit-zh",
            )

            self.assertTrue(result["buffered"])
            self.assertIn("capture_diagnostic", result)
            self.assertIn("semantic_candidate", result["capture_diagnostic"]["reason_codes"])
            saved = MarkdownPreferenceStore(store).load()
            self.assertEqual(len(saved), 1)
            self.assertIn("analyze", saved[0].preference.casefold())
            self.assertIn("before modifying code", saved[0].preference.casefold())

    def test_hook_turn_complete_buffers_and_stores_implicit_english_preference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
            hooks = PreferenceHookManager(engine, hooks_dir=root / "hooks", max_turns=1)

            result = hooks.on_turn_complete(
                user_message="That's not what I meant; I want review first, then patches only after I approve.",
                assistant_response="Understood.",
                agent="codex",
                session_id="s-implicit-en",
            )

            self.assertTrue(result["buffered"])
            self.assertIn("semantic_candidate", result["capture_diagnostic"]["reason_codes"])
            saved = MarkdownPreferenceStore(store).load()
            self.assertEqual(len(saved), 1)
            self.assertIn("review", saved[0].preference.casefold())
            self.assertIn("approval", saved[0].preference.casefold())

    def test_hook_turn_complete_nosignal_has_specific_reason_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = PreferenceEngine(MarkdownPreferenceStore(root / "prefs.md"), backend=HeuristicBackend())
            hooks = PreferenceHookManager(engine, hooks_dir=root / "hooks", max_turns=1)

            result = hooks.on_turn_complete(
                user_message="Tell me a fun fact.",
                assistant_response="Here is one.",
                agent="codex",
                session_id="s-nosignal",
            )

            self.assertFalse(result["buffered"])
            self.assertEqual(result["reason"], "fast_gate_no_match")
            self.assertEqual(result["capture_diagnostic"]["reason_codes"], ["fast_gate_no_match"])

    def test_model_classifier_failure_falls_back_with_reason_code(self) -> None:
        diagnostic = classify_capture_candidate(
            "That's not what I meant; I want review first, then patches only after I approve.",
            backend=_FailingClassifierBackend(),
        )

        self.assertTrue(diagnostic.is_candidate)
        self.assertIn("model_failed_fallback", diagnostic.reason_codes)
        self.assertEqual(diagnostic.backend, "heuristic")

    def test_capture_runner_exposes_model_classifier_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "history.jsonl"
            source.write_text(
                json.dumps(
                    {"role": "user", "content": "That's not what I meant; I want review first, then patches only after I approve."},
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            config = CaptureConfig(
                project_root=root,
                candidate_dir=root / "debug",
                checkpoint_dir=root / "state",
                store_path=root / "prefs.md",
                mode="recall-extract",
                batch_size=1,
            )

            result = CaptureRunner(config, backend=_FailingClassifierBackend()).run(source)

            self.assertGreaterEqual(result.candidates_written, 1)
            self.assertGreaterEqual(result.classified_candidates, 1)
            self.assertGreaterEqual(result.model_fallbacks, 1)

    def test_model_classifier_result_uses_taxonomy(self) -> None:
        diagnostic = classify_capture_candidate(
            "The exact text does not matter here.",
            backend=_ReviewFirstClassifierBackend(),
        )

        self.assertTrue(diagnostic.is_candidate)
        self.assertEqual(diagnostic.signal_type, "execution_policy")
        self.assertEqual(diagnostic.scope, "global")
        self.assertIn("semantic_candidate", diagnostic.reason_codes)

    def test_multi_route_recall_inspects_text_old_gate_rejected(self) -> None:
        engine = MultiRouteRecallEngine(config=RecallConfig(strategy="multi", semantic_threshold=0.80, final_threshold=0.80))
        candidates = engine.recall_batch(
            [
                RecallInput(source="gold:zh", content="你又直接改了，我刚才是要你先分析，不要动代码。"),
                RecallInput(source="gold:task", content=r"帮我打开 C:\tmp\a.md。"),
            ]
        )

        texts = "\n".join(item.content for item in candidates)
        self.assertIn("先分析", texts)
        self.assertNotIn(r"C:\tmp", texts)

    def test_manifesto_exposes_recent_capture_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            engine = PreferenceEngine(MarkdownPreferenceStore(store), backend=HeuristicBackend())
            hooks = PreferenceHookManager(engine, hooks_dir=root / "hooks", max_turns=1)
            hooks.on_turn_complete(
                user_message="你又直接改了，我刚才是要你先分析，不要动代码。",
                assistant_response="收到。",
                agent="codex",
                session_id="s-ui",
            )

            manifesto = build_manifesto(store_path=store)

            diagnostics = manifesto["capture_diagnostics"]["items"]
            self.assertTrue(diagnostics)
            encoded = json.dumps(diagnostics, ensure_ascii=False)
            self.assertIn("semantic_candidate", encoded)
            self.assertIn("你又直接改了", encoded)

    def test_backend_auto_status_activates_model_semantics_when_api_is_configured(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PREFERENCE_MODEL_BACKEND": "auto",
                "PREFERENCE_MODEL_API_KEY": "test-key",
                "PREFERENCE_MODEL_BASE_URL": "https://example.test/v1",
            },
            clear=False,
        ):
            status = backend_status()

        self.assertEqual(status["configured"], "auto")
        self.assertEqual(status["effective"], "openai-compatible")
        self.assertTrue(status["model_semantics_active"])

    def test_mcp_config_defaults_to_auto_backend_not_heuristic(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ,
            {
                "PREFERENCE_MODEL_BACKEND": "",
                "PREFERENCE_STORE_PATH": "",
                "DATAILOR_DATA_DIR": str(Path(temp) / "Datailor"),
            },
            clear=False,
        ):
            payload = _run_cli_json(["mcp-config", "--agent", "codex", "--no-agent-rules", "--no-kimi-hooks"])

        server = payload["mcpServers"]["datailor-preferences"]
        self.assertEqual(server["env"]["PREFERENCE_MODEL_BACKEND"], "auto")
        self.assertNotEqual(server["env"]["PREFERENCE_MODEL_BACKEND"], "heuristic")


if __name__ == "__main__":
    unittest.main()


def _run_cli_json(argv: list[str]) -> dict[str, object]:
    buffer = StringIO()
    with redirect_stdout(buffer):
        code = cli_main(argv)
    if code != 0:
        raise AssertionError(f"CLI exited with {code}")
    return json.loads(buffer.getvalue())

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from preference_agent.backends import HeuristicBackend, OpenAICompatibleBackend
from preference_agent.injection import prewarm_session
from preference_agent.injection_log import read_injection_log
from preference_agent.models import PreferenceRecord
from preference_agent.store import MarkdownPreferenceStore


class _CapturingOpenAIBackend(OpenAICompatibleBackend):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict[str, object]] = []

    def _chat_json(self, system: str, user: str) -> dict[str, object]:
        del system
        payload = json.loads(user)
        self.calls.append(payload)
        return {
            "decision": "apply",
            "matched_preferences": [
                {
                    "id": item["id"],
                    "title": item["title"],
                    "instruction": item["preference"],
                }
                for item in payload["preferences"]
            ],
            "agent_instruction": "model selected candidate",
            "escalate": False,
            "reason": "mocked model",
        }


class DecisionSelectivityTests(unittest.TestCase):
    def test_unrelated_prd_task_returns_no_preference_for_code_tests(self) -> None:
        decision = HeuristicBackend().decide(
            task="Help me write a product requirements document.",
            context={},
            records=[_code_tests()],
            agent="codex",
        )

        self.assertEqual(decision["decision"], "no_preference")
        self.assertEqual(decision["agent_instruction"], "")
        self.assertEqual(decision["matched_preferences"], [])
        self.assertEqual(decision["gate_summary"]["passed"], 0)

    def test_localhost_guardrail_requires_localhost_task_signal(self) -> None:
        record = PreferenceRecord(
            title="Localhost safety",
            applies_to="When starting localhost servers",
            preference="When starting localhost servers, avoid terminating other running code sessions.",
            status="active",
            confidence="high",
        )

        negative = HeuristicBackend().decide(
            task="Analyze product website feature copy and improve the use cases section.",
            context={},
            records=[record],
            agent="codex",
        )
        positive = HeuristicBackend().decide(
            task="Start the local dev server on localhost and preview the site.",
            context={},
            records=[record],
            agent="codex",
        )

        self.assertEqual(negative["decision"], "no_preference")
        self.assertEqual(positive["decision"], "apply")
        self.assertEqual(positive["matched_preferences"][0]["gate_reason"], "passed_guardrail_explicit_trigger")

    def test_codebase_documentation_does_not_trigger_test_workflow(self) -> None:
        negative = HeuristicBackend().decide(
            task="Rewrite product documentation to describe the codebase architecture.",
            context={},
            records=[_code_tests()],
            agent="codex",
        )
        positive = HeuristicBackend().decide(
            task="After implementing the code change, verify regression risk.",
            context={},
            records=[_code_tests()],
            agent="codex",
        )

        self.assertEqual(negative["decision"], "no_preference")
        self.assertEqual(positive["decision"], "apply")
        self.assertEqual(positive["matched_preferences"][0]["category"], "workflow")

    def test_short_font_question_does_not_match_external_write_or_code_review(self) -> None:
        records = [
            PreferenceRecord(
                title="External write readback",
                applies_to="After writing content to an external system",
                preference="After writing content to an external system, read back and verify Chinese text and Markdown structure.",
                status="active",
                confidence="high",
            ),
            PreferenceRecord(
                title="Code review",
                applies_to="During code review",
                preference="During code review, prioritize bugs, regression risk, and missing tests before adding a summary.",
                status="active",
                confidence="high",
            ),
        ]

        decision = HeuristicBackend().decide(
            task="这样很好看，这是啥字体？标题一般我要求用熊猫体。",
            context={},
            records=records,
            agent="codex",
        )

        self.assertEqual(decision["decision"], "no_preference")
        self.assertEqual(decision["agent_instruction"], "")

    def test_deep_discussion_documentation_requires_deep_discussion_signal(self) -> None:
        record = PreferenceRecord(
            title="Deep discussion docs",
            applies_to="When deep discussion or solution design produces reusable conclusions",
            preference="When deep discussion or solution design produces reusable conclusions, ask whether to save Markdown documentation.",
            status="active",
            confidence="high",
        )

        negative = HeuristicBackend().decide("请帮我把这句话改得更短。", {}, [record], agent="codex")
        positive = HeuristicBackend().decide("我们讨论一下 Datailor 注入判定架构怎么改，分析利弊和方案。", {}, [record], agent="codex")

        self.assertEqual(negative["decision"], "no_preference")
        self.assertEqual(positive["decision"], "apply")
        self.assertEqual(positive["matched_preferences"][0]["category"], "documentation")

    def test_ordinary_documentation_tasks_do_not_match_deep_discussion_docs(self) -> None:
        record = PreferenceRecord(
            title="Deep discussion docs",
            applies_to="When deep discussion or solution design produces reusable conclusions",
            preference="When deep discussion or solution design produces reusable conclusions, ask whether to save Markdown documentation.",
            status="active",
            confidence="high",
        )

        for task in (
            "Write product documentation for this feature.",
            "Fix typos in the documentation.",
        ):
            with self.subTest(task=task):
                decision = HeuristicBackend().decide(task, {}, [record], agent="codex")
                self.assertEqual(decision["decision"], "no_preference")
                self.assertEqual(decision["agent_instruction"], "")

    def test_external_write_guardrail_does_not_match_read_only_github_issue_task(self) -> None:
        record = PreferenceRecord(
            title="External write readback",
            applies_to="After writing content to an external system",
            preference="After writing content to an external system, read back and verify Chinese text and Markdown structure.",
            status="active",
            confidence="high",
        )

        negative = HeuristicBackend().decide(
            task="Summarize the GitHub issue locally without writing comments.",
            context={},
            records=[record],
            agent="codex",
        )
        positive = HeuristicBackend().decide(
            task="Post a comment to the GitHub issue and read back the content.",
            context={},
            records=[record],
            agent="codex",
        )

        self.assertEqual(negative["decision"], "no_preference")
        self.assertEqual(positive["decision"], "apply")
        self.assertEqual(positive["matched_preferences"][0]["category"], "guardrail")

    def test_prewarm_no_match_keeps_fallback_separate_from_dynamic_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "prefs.md"
            MarkdownPreferenceStore(store).save([_code_tests()])

            result = prewarm_session(
                store,
                "Help me write a product requirements document.",
                agent="codex",
                output_dir=root / "inject",
            )

            self.assertEqual(result.decision, "no_preference")
            self.assertEqual(result.agent_instruction, "")
            self.assertTrue(result.fallback_instruction)
            self.assertTrue(result.fallback_applied)
            cache = Path(result.session_cache_file).read_text(encoding="utf-8")
            self.assertIn("No dynamic preference matched.", cache)
            events = read_injection_log(store)
            self.assertFalse(events[0]["injected"])
            self.assertTrue(events[0]["fallback_applied"])

    def test_openai_compatible_decision_prefilters_candidates_before_model_call(self) -> None:
        active_relevant = _code_tests()
        active_unrelated = PreferenceRecord(
            title="Localhost safety",
            applies_to="When starting localhost servers",
            preference="When starting localhost servers, avoid terminating other running code sessions.",
            status="active",
            confidence="high",
        )
        needs_review_relevant = PreferenceRecord(
            title="Needs review tests",
            applies_to="After code changes",
            preference="After code changes, run relevant tests by default.",
            status="needs_review",
            confidence="high",
        )
        backend = _CapturingOpenAIBackend()

        decision = backend.decide(
            task="After implementing the code change, verify regression risk.",
            context={},
            records=[active_relevant, active_unrelated, needs_review_relevant],
            agent="codex",
        )

        sent = backend.calls[0]["preferences"]
        self.assertEqual(decision["decision"], "apply")
        self.assertEqual([item["id"] for item in sent], [active_relevant.id])
        self.assertEqual(sent[0]["gate_reason"], "passed_workflow_explicit_trigger")

    def test_openai_compatible_no_candidates_returns_no_preference_without_model_call(self) -> None:
        backend = _CapturingOpenAIBackend()

        decision = backend.decide(
            task="这样很好看，这是啥字体？",
            context={},
            records=[_code_tests()],
            agent="codex",
        )

        self.assertEqual(decision["decision"], "no_preference")
        self.assertEqual(backend.calls, [])


def _code_tests() -> PreferenceRecord:
    return PreferenceRecord(
        title="Code tests",
        applies_to="After the agent changes code",
        preference="After code changes, run relevant tests by default.",
        status="active",
        confidence="high",
    )


if __name__ == "__main__":
    unittest.main()

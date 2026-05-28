from __future__ import annotations

from datetime import datetime, timezone
import unittest

from preference_agent.backends import HeuristicBackend
from preference_agent.live_confidence import live_confidence
from preference_agent.models import Evidence, PreferenceRecord


NOW = datetime(2026, 5, 26, tzinfo=timezone.utc)


class LiveConfidenceTests(unittest.TestCase):
    def test_evidence_recency_and_relevance_factors_are_exposed(self) -> None:
        record = PreferenceRecord(
            title="Code verification",
            applies_to="After the agent changes code",
            preference="After code changes, run relevant tests by default.",
            confidence="high",
            updated_at="2026-05-20T00:00:00+00:00",
            evidence=[
                Evidence(source="ui_feedback", quote="confirmed", source_type="user_confirm"),
                Evidence(source="session", quote="run tests by default from now on", source_type="user_explicit"),
                Evidence(source="behavior:test", quote="pytest passed", role="system", source_type="action_signal"),
            ],
        )

        live = live_confidence(record, relevance_score=0.36, now=NOW)

        self.assertEqual(live.label, "high")
        self.assertEqual(live.recency_decay, 0.0)
        self.assertGreater(live.evidence_boost, 0.14)
        self.assertEqual(live.relevance_bonus, 0.3)
        self.assertIn("evidence_boost", live.reasons)

    def test_stale_preference_is_softly_downgraded(self) -> None:
        record = PreferenceRecord(
            title="Old reply preference",
            applies_to="When replying to user questions",
            preference="Use detailed explanations when replying to user questions.",
            confidence="high",
            updated_at="2025-01-01T00:00:00+00:00",
        )

        live = live_confidence(record, relevance_score=0.26, now=NOW)

        self.assertEqual(live.recency_decay, 0.4)
        self.assertEqual(live.label, "medium")
        self.assertLess(live.score, 0.75)

    def test_needs_review_conflict_does_not_reach_injection_threshold(self) -> None:
        record = PreferenceRecord(
            title="Unstable reply preference",
            applies_to="When replying to user questions",
            preference="Use detailed explanations when replying to user questions.",
            confidence="high",
            status="needs_review",
            conflict_notes=["Conflicts with the concise reply preference"],
        )

        live = live_confidence(record, relevance_score=0.36, now=NOW)

        self.assertEqual(live.base, 0.35)
        self.assertEqual(live.consistency_penalty, 0.3)
        self.assertEqual(live.label, "low")

    def test_decide_ranks_recent_live_confidence_above_stale_static_high(self) -> None:
        old_detailed = PreferenceRecord(
            title="Old reply tone",
            applies_to="When replying to user questions",
            preference="Use a formal tone when replying to user questions.",
            confidence="high",
            updated_at="2025-01-01T00:00:00+00:00",
        )
        recent_brief = PreferenceRecord(
            title="Recent conclusion first",
            applies_to="When replying to user questions",
            preference="Give the conclusion first when replying to user questions.",
            confidence="medium",
            updated_at="2026-05-26T00:00:00+00:00",
        )

        decision = HeuristicBackend().decide(
            task="Please answer the user question and explain what to do.",
            context={},
            records=[old_detailed, recent_brief],
            agent="codex",
        )

        self.assertEqual(decision["decision"], "apply")
        self.assertEqual(decision["matched_preferences"][0]["id"], recent_brief.id)
        self.assertIn("live_confidence", decision["matched_preferences"][0])
        self.assertIn("recency_decay", decision["matched_preferences"][1]["confidence_factors"])

    def test_decide_escalates_before_combining_conflicting_top_matches(self) -> None:
        detailed = PreferenceRecord(
            title="Detailed replies",
            applies_to="When replying to user questions",
            preference="When replying to user questions, answer with detailed full explanations.",
            confidence="high",
        )
        concise = PreferenceRecord(
            title="Concise replies",
            applies_to="When replying to user questions",
            preference="When replying to user questions, keep answers concise and short; avoid long-form explanations.",
            confidence="high",
        )

        decision = HeuristicBackend().decide(
            task="Please answer the user question and explain what to do.",
            context={},
            records=[detailed, concise],
            agent="codex",
        )

        self.assertEqual(decision["decision"], "escalate")
        self.assertTrue(decision["clarification_required"])
        self.assertIn("conflict", decision)
        self.assertNotIn("detailed full explanations; When replying to user questions, keep answers concise", decision["agent_instruction"])

    def test_needs_review_with_strong_evidence_is_not_injected(self) -> None:
        record = PreferenceRecord(
            title="Needs-review detailed replies",
            applies_to="When replying to user questions",
            preference="When replying to user questions, answer with detailed full explanations.",
            confidence="high",
            status="needs_review",
            conflict_notes=["Conflicts with the concise reply preference"],
            evidence=[
                Evidence(source="session-1", quote="detailed", source_type="user_explicit"),
                Evidence(source="session-2", quote="expand", source_type="user_explicit"),
                Evidence(source="ui_feedback", quote="confirmed but later conflicted", source_type="user_confirm"),
            ],
        )

        decision = HeuristicBackend().decide(
            task="Please answer the user question and explain what to do.",
            context={},
            records=[record],
            agent="codex",
        )

        self.assertEqual(decision["decision"], "no_preference")

    def test_unrelated_high_confidence_preference_is_not_injected(self) -> None:
        record = PreferenceRecord(
            title="Code tests",
            applies_to="After the agent changes code",
            preference="After code changes, run relevant tests by default.",
            confidence="high",
            evidence=[
                Evidence(source="ui_feedback", quote="confirmed", source_type="user_confirm"),
                Evidence(source="session", quote="test by default from now on", source_type="user_explicit"),
                Evidence(source="session", quote="test every time", source_type="user_explicit"),
            ],
        )

        decision = HeuristicBackend().decide(
            task="Help me write a product requirements document.",
            context={},
            records=[record],
            agent="codex",
        )

        self.assertEqual(decision["decision"], "no_preference")

    def test_missing_source_type_is_inferred_for_legacy_evidence(self) -> None:
        evidence = Evidence.from_dict({"source": "behavior:codex:pytest", "quote": "passed", "role": "system"})

        self.assertEqual(evidence.source_type, "action_signal")


if __name__ == "__main__":
    unittest.main()

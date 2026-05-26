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
            title="代码验证",
            applies_to="当 agent 修改代码后",
            preference="代码改动完成后默认运行相关测试。",
            confidence="high",
            updated_at="2026-05-20T00:00:00+00:00",
            evidence=[
                Evidence(source="ui_feedback", quote="确认", source_type="user_confirm"),
                Evidence(source="session", quote="以后默认测试", source_type="user_explicit"),
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
            title="旧回复偏好",
            applies_to="当 agent 回复用户问题时",
            preference="回复用户问题时要详细展开。",
            confidence="high",
            updated_at="2025-01-01T00:00:00+00:00",
        )

        live = live_confidence(record, relevance_score=0.26, now=NOW)

        self.assertEqual(live.recency_decay, 0.4)
        self.assertEqual(live.label, "medium")
        self.assertLess(live.score, 0.75)

    def test_needs_review_conflict_does_not_reach_injection_threshold(self) -> None:
        record = PreferenceRecord(
            title="摇摆回复偏好",
            applies_to="当 agent 回复用户问题时",
            preference="回复用户问题时要详细展开。",
            confidence="high",
            status="needs_review",
            conflict_notes=["与简洁回复偏好冲突"],
        )

        live = live_confidence(record, relevance_score=0.36, now=NOW)

        self.assertEqual(live.base, 0.35)
        self.assertEqual(live.consistency_penalty, 0.3)
        self.assertEqual(live.label, "low")

    def test_decide_ranks_recent_live_confidence_above_stale_static_high(self) -> None:
        old_detailed = PreferenceRecord(
            title="旧回复语气",
            applies_to="当 agent 回复用户问题时",
            preference="回复用户问题时，使用正式语气。",
            confidence="high",
            updated_at="2025-01-01T00:00:00+00:00",
        )
        recent_brief = PreferenceRecord(
            title="近期结论优先",
            applies_to="当 agent 回复用户问题时",
            preference="回复用户问题时，先给结论。",
            confidence="medium",
            updated_at="2026-05-26T00:00:00+00:00",
        )

        decision = HeuristicBackend().decide(
            task="请回复用户问题，说明怎么处理。",
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
            title="详细回复",
            applies_to="当 agent 回复用户问题时",
            preference="回复用户问题时，回答要详细展开，充分解释。",
            confidence="high",
        )
        concise = PreferenceRecord(
            title="简洁回复",
            applies_to="当 agent 回复用户问题时",
            preference="回复用户问题时，回答要简洁短一点，不要长文。",
            confidence="high",
        )

        decision = HeuristicBackend().decide(
            task="请回复用户问题，说明怎么处理。",
            context={},
            records=[detailed, concise],
            agent="codex",
        )

        self.assertEqual(decision["decision"], "escalate")
        self.assertTrue(decision["clarification_required"])
        self.assertIn("conflict", decision)
        self.assertNotIn("详细展开；回复用户问题时，回答要简洁", decision["agent_instruction"])

    def test_needs_review_with_strong_evidence_is_not_injected(self) -> None:
        record = PreferenceRecord(
            title="待审详细回复",
            applies_to="当 agent 回复用户问题时",
            preference="回复用户问题时，回答要详细展开，充分解释。",
            confidence="high",
            status="needs_review",
            conflict_notes=["与简洁回复偏好冲突"],
            evidence=[
                Evidence(source="session-1", quote="详细", source_type="user_explicit"),
                Evidence(source="session-2", quote="展开", source_type="user_explicit"),
                Evidence(source="ui_feedback", quote="确认过但后来冲突", source_type="user_confirm"),
            ],
        )

        decision = HeuristicBackend().decide(
            task="请回复用户问题，说明怎么处理。",
            context={},
            records=[record],
            agent="codex",
        )

        self.assertEqual(decision["decision"], "no_preference")

    def test_unrelated_high_confidence_preference_is_not_injected(self) -> None:
        record = PreferenceRecord(
            title="代码测试",
            applies_to="当 agent 修改代码后",
            preference="代码改动完成后默认运行相关测试。",
            confidence="high",
            evidence=[
                Evidence(source="ui_feedback", quote="确认", source_type="user_confirm"),
                Evidence(source="session", quote="以后默认测试", source_type="user_explicit"),
                Evidence(source="session", quote="每次都测", source_type="user_explicit"),
            ],
        )

        decision = HeuristicBackend().decide(
            task="帮我写一份产品需求文档。",
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

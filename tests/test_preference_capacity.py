from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preference_agent.backends import HeuristicBackend
from preference_agent.capacity import enforce_preference_cap, preference_limit
from preference_agent.engine import PreferenceEngine
from preference_agent.fitting import apply_fitting_plan, run_fitting
from preference_agent.models import Evidence, PreferenceRecord
from preference_agent.store import MarkdownPreferenceStore
from preference_agent.ui.server import build_manifesto


class PreferenceCapacityTests(unittest.TestCase):
    def test_env_override_controls_preference_limit(self) -> None:
        with patch.dict("os.environ", {"PREFERENCE_STORE_MAX_RECORDS": "5"}, clear=False):
            self.assertEqual(preference_limit(), 5)

    def test_duplicate_merge_preserves_evidence(self) -> None:
        left = _record(
            "Tests A",
            "After code changes, run relevant tests by default.",
            evidence_quote="first explicit signal",
        )
        right = _record(
            "Tests B",
            "After code changes, run relevant tests by default.",
            evidence_quote="second explicit signal",
        )

        result = enforce_preference_cap([left, right], limit=50)

        self.assertEqual(result.after_count, 1)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(len(result.records[0].evidence), 2)
        self.assertEqual(len(result.merged), 1)

    def test_overlap_or_conflict_requires_review_not_merge(self) -> None:
        detailed = _record(
            "Detailed replies",
            "When replying to user questions, provide detailed full explanations.",
            confidence="high",
        )
        concise = _record(
            "Concise replies",
            "When replying to user questions, keep answers concise and avoid long-form explanations.",
            confidence="high",
        )

        result = enforce_preference_cap([detailed, concise], limit=1)

        self.assertTrue(result.review_required)
        self.assertEqual(result.after_count, 1)
        self.assertEqual(len(result.review_candidates), 1)
        self.assertEqual(result.merged, [])

    def test_store_save_enforces_cap_and_writes_review_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"PREFERENCE_STORE_MAX_RECORDS": "5"}, clear=False):
            store_path = Path(temp) / "prefs.md"
            records = [
                _record(f"UI preference {index}", f"When reviewing interface copy variant {index}, prefer concise scannable output.")
                for index in range(8)
            ]

            MarkdownPreferenceStore(store_path).save(records)

            saved = MarkdownPreferenceStore(store_path).load()
            self.assertLessEqual(len(saved), 5)
            review_file = store_path.with_name("prefs.cap-review.jsonl")
            self.assertTrue(review_file.exists())
            payload = json.loads(review_file.read_text(encoding="utf-8").splitlines()[-1])
            self.assertIn("review_candidates", payload)
            self.assertGreater(payload["overflow_record_count"], 0)
            self.assertEqual(payload["overflow_record_count"], len(payload["overflow_records"]))
            self.assertTrue(payload["overflow_records"][0]["evidence"])
            self.assertIn("quote", payload["overflow_records"][0]["evidence"][0])

    def test_capture_records_applies_cap_without_silent_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"PREFERENCE_STORE_MAX_RECORDS": "5"}, clear=False):
            store_path = Path(temp) / "prefs.md"
            existing = [
                _record(f"UI preference {index}", f"When reviewing interface copy variant {index}, prefer concise scannable output.")
                for index in range(5)
            ]
            MarkdownPreferenceStore(store_path).save(existing)
            engine = PreferenceEngine(MarkdownPreferenceStore(store_path), backend=HeuristicBackend())

            result = engine.capture_records(
                [
                    _record(
                        "Linear phase updates",
                        "After a phase is completed, ask whether the Linear issue should be updated.",
                        confidence="high",
                    )
                ],
                source="capacity-test",
            )

            saved = MarkdownPreferenceStore(store_path).load()
            self.assertLessEqual(len(saved), 5)
            self.assertTrue(result.cap["review_required"])
            self.assertEqual(result.cap["after_count"], 5)

    def test_capture_records_does_not_report_overflow_as_added(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"PREFERENCE_STORE_MAX_RECORDS": "1"}, clear=False):
            store_path = Path(temp) / "prefs.md"
            existing = _record(
                "Run tests",
                "When changing code, run relevant tests before delivery.",
                confidence="high",
                evidence_quote="stable high value signal",
            )
            existing.evidence.extend(
                Evidence(source=f"test:Run tests:{index}", quote=f"reinforcement {index}", role="user")
                for index in range(4)
            )
            MarkdownPreferenceStore(store_path).save([existing])
            engine = PreferenceEngine(MarkdownPreferenceStore(store_path), backend=HeuristicBackend())
            candidate = _record(
                "Linear phase updates",
                "When finishing a project phase, ask whether to update the Linear issue.",
                confidence="low",
                evidence_quote="single low confidence signal",
            )

            result = engine.capture_records([candidate], source="capacity-test")

            saved_ids = {record.id for record in MarkdownPreferenceStore(store_path).load()}
            self.assertEqual(saved_ids, {existing.id})
            self.assertNotIn(candidate.id, result.added)
            self.assertIn(candidate.id, result.cap["added_removed_by_cap"])
            self.assertEqual(result.cap["overflow_record_ids"], [candidate.id])

    def test_fitting_apply_obeys_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"PREFERENCE_STORE_MAX_RECORDS": "5"}, clear=False):
            root = Path(temp)
            store_path = root / "prefs.md"
            source_path = root / "history.md"
            fitting_dir = root / ".fitting"
            existing = [
                _record(f"UI preference {index}", f"When reviewing interface copy variant {index}, prefer concise scannable output.")
                for index in range(5)
            ]
            MarkdownPreferenceStore(store_path).save(existing)
            source_path.write_text("User: From now on, after changing code, run relevant tests before delivery.", encoding="utf-8")
            fitting = run_fitting(store_path=store_path, source=source_path, fitting_dir=fitting_dir)
            change = next(item for item in fitting.apply_plan.changes if item.type == "add_preference")

            applied = apply_fitting_plan(
                job_id=fitting.job_id,
                accepted_change_ids=[change.change_id],
                store_path=store_path,
                fitting_dir=fitting_dir,
            )

            self.assertTrue(applied["ok"])
            self.assertLessEqual(len(MarkdownPreferenceStore(store_path).load()), 5)
            self.assertTrue(applied["cap"]["review_required"])

    def test_manifesto_summary_exposes_cap_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"PREFERENCE_STORE_MAX_RECORDS": "5"}, clear=False):
            store_path = Path(temp) / "prefs.md"
            MarkdownPreferenceStore(store_path).save([_record("Concise", "Prefer concise replies.")])

            manifesto = build_manifesto(store_path=store_path)

            self.assertEqual(manifesto["summary"]["preference_limit"], 5)
            self.assertEqual(manifesto["summary"]["preference_count"], 1)
            self.assertFalse(manifesto["summary"]["over_limit"])


def _record(
    title: str,
    preference: str,
    confidence: str = "medium",
    evidence_quote: str = "explicit preference",
) -> PreferenceRecord:
    return PreferenceRecord(
        title=title,
        summary=preference,
        applies_to=preference if preference.casefold().startswith("when ") else "When the agent follows user preferences",
        preference=preference,
        triggers=[title],
        confidence=confidence,
        status="active",
        evidence=[Evidence(source=f"test:{title}", quote=evidence_quote, role="user")],
    )


if __name__ == "__main__":
    unittest.main()

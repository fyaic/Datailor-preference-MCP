from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from preference_agent.fitting import apply_fitting_plan, reject_fitting_plan
from preference_agent.fitting_background import mark_fitting_plan_reviewed, run_fitting_for_mode
from preference_agent.fitting_store import FittingJobStore
from preference_agent.fitting_trigger import (
    default_mode_settings,
    read_mode_settings,
    record_session_and_decide,
    should_trigger_fitting,
    write_mode_settings,
)
from preference_agent.models import PreferenceRecord
from preference_agent.store import MarkdownPreferenceStore


def _clean_env(root: Path) -> dict[str, str]:
    return {
        "APPDATA": str(root / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(root / "AppData" / "Local"),
        "USERPROFILE": str(root),
    }


class FittingModeTests(unittest.TestCase):
    def test_trigger_uses_explicit_curate_mode_and_record_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store_path = root / "prefs.md"
            settings_path = root / "settings.json"
            fitting_dir = root / ".fitting"
            MarkdownPreferenceStore(store_path).ensure()

            settings = default_mode_settings()
            settings["fitting"]["trigger_records"] = 2
            settings["fitting"]["cooldown_minutes"] = 0
            write_mode_settings(settings, settings_path)

            with patch.dict(os.environ, _clean_env(root), clear=True):
                first = record_session_and_decide(
                    store_path=store_path,
                    changed_records=1,
                    settings_path=settings_path,
                    fitting_dir=fitting_dir,
                )
                second = record_session_and_decide(
                    store_path=store_path,
                    changed_records=1,
                    settings_path=settings_path,
                    fitting_dir=fitting_dir,
                )

            self.assertFalse(first.should_trigger)
            self.assertEqual(first.reason, "below_threshold")
            self.assertTrue(second.should_trigger)
            self.assertEqual(second.mode, "curate")
            self.assertTrue(second.review)
            self.assertFalse(second.auto_apply)

    def test_zero_record_threshold_only_triggers_first_run_when_store_has_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store_path = root / "prefs.md"
            settings = default_mode_settings()
            settings["fitting"]["trigger_records"] = 0
            settings["fitting"]["cooldown_minutes"] = 0
            MarkdownPreferenceStore(store_path).ensure()

            with patch.dict(os.environ, _clean_env(root), clear=True):
                empty = should_trigger_fitting(store_path=store_path, settings=settings, fitting_dir=root / ".fitting")

            self.assertFalse(empty.should_trigger)
            self.assertEqual(empty.reason, "below_threshold")

            MarkdownPreferenceStore(store_path).save(
                [
                    PreferenceRecord(
                        title="English replies",
                        applies_to="When the agent replies to the user",
                        preference="Use English when replying to the user by default.",
                    )
                ]
            )
            with patch.dict(os.environ, _clean_env(root), clear=True):
                first_run = should_trigger_fitting(store_path=store_path, settings=settings, fitting_dir=root / ".fitting")

            self.assertTrue(first_run.should_trigger)
            self.assertEqual(first_run.reason, "first_run")

    def test_zero_record_threshold_streams_store_record_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = default_mode_settings()
            settings["fitting"]["trigger_records"] = 0
            settings["fitting"]["cooldown_minutes"] = 0
            fitting_dir = root / ".fitting"
            store_path = root / "prefs.md"

            store_path.write_text("# Datailor\n\n- No preferences yet.\n", encoding="utf-8")
            with patch.dict(os.environ, _clean_env(root), clear=True):
                empty = should_trigger_fitting(store_path=store_path, settings=settings, fitting_dir=fitting_dir)
            self.assertFalse(empty.should_trigger)
            self.assertEqual(empty.reason, "below_threshold")

            store_path.write_text("header\n```json preference-record\n{}\n```\n", encoding="utf-8")
            with patch.dict(os.environ, _clean_env(root), clear=True):
                fenced = should_trigger_fitting(store_path=store_path, settings=settings, fitting_dir=fitting_dir)
            self.assertTrue(fenced.should_trigger)
            self.assertEqual(fenced.reason, "first_run")

    def test_auto_mode_applies_only_high_confidence_preferences_as_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store_path = root / "prefs.md"
            source = root / "history.md"
            settings_path = root / "settings.json"
            fitting_dir = root / ".fitting"
            source.write_text(
                "\n".join(
                    [
                        "User: From now on, reply to me in English by default.",
                        "User: From now on, reply to me in English by default.",
                        "User: From now on, reply to me in English by default.",
                    ]
                ),
                encoding="utf-8",
            )
            settings = default_mode_settings()
            settings["mode"] = "auto"
            write_mode_settings(settings, settings_path)

            with patch.dict(os.environ, _clean_env(root), clear=True):
                payload = run_fitting_for_mode(
                    store_path=store_path,
                    mode="auto",
                    agent="codex",
                    source=source,
                    settings_path=settings_path,
                    fitting_dir=fitting_dir,
                )

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["mode"], "auto")
            self.assertTrue(payload["auto_apply"]["applied"])
            records = MarkdownPreferenceStore(store_path).load()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].status, "active")

            plan = FittingJobStore(fitting_dir).read_apply_plan(payload["job_id"])
            self.assertEqual(plan.changes[0].status, "applied")
            settings = read_mode_settings(settings_path)
            self.assertEqual(settings["fitting"]["pending_plan_job_id"], None)
            self.assertGreaterEqual(settings["fitting"]["auto_applied_count"], 1)

    def test_curate_mode_marks_pending_review_and_reject_closes_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store_path = root / "prefs.md"
            source = root / "history.md"
            settings_path = root / "settings.json"
            fitting_dir = root / ".fitting"
            source.write_text("User: From now on, reply to me in English by default.", encoding="utf-8")
            settings = default_mode_settings()
            settings["mode"] = "curate"
            settings["fitting"]["cooldown_minutes"] = 0
            write_mode_settings(settings, settings_path)

            with patch.dict(os.environ, _clean_env(root), clear=True):
                payload = run_fitting_for_mode(
                    store_path=store_path,
                    mode="curate",
                    agent="codex",
                    source=source,
                    settings_path=settings_path,
                    fitting_dir=fitting_dir,
                )

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["status"], "pending_review")
            self.assertEqual(read_mode_settings(settings_path)["fitting"]["pending_plan_job_id"], payload["job_id"])

            with patch.dict(os.environ, _clean_env(root), clear=True):
                blocked = record_session_and_decide(
                    store_path=store_path,
                    changed_records=5,
                    settings_path=settings_path,
                    fitting_dir=fitting_dir,
                )

            self.assertFalse(blocked.should_trigger)
            self.assertEqual(blocked.reason, "pending_plan_requires_review")

            rejected = reject_fitting_plan(payload["job_id"], fitting_dir=fitting_dir)
            mark_fitting_plan_reviewed(payload["job_id"], settings_path=settings_path, rejected=True)

            self.assertTrue(rejected["ok"])
            self.assertEqual(rejected["remaining_pending"], 0)
            self.assertEqual(read_mode_settings(settings_path)["fitting"]["pending_plan_job_id"], None)
            result = FittingJobStore(fitting_dir).read_result(payload["job_id"])
            plan = FittingJobStore(fitting_dir).read_apply_plan(payload["job_id"])
            self.assertEqual(result.status, "reviewed")
            self.assertEqual(plan.changes[0].status, "rejected")

    def test_partial_apply_keeps_plan_pending_until_remaining_changes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store_path = root / "prefs.md"
            source = root / "history.md"
            fitting_dir = root / ".fitting"
            source.write_text(
                "\n".join(
                    [
                        "User: From now on, reply to me in English by default.",
                        "User: From now on, keep replies concise by default.",
                    ]
                ),
                encoding="utf-8",
            )
            payload = run_fitting_for_mode(
                store_path=store_path,
                mode="curate",
                agent="codex",
                source=source,
                fitting_dir=fitting_dir,
            )
            changes = payload["result"]["apply_plan"]["changes"]
            self.assertGreaterEqual(len(changes), 2)

            applied = apply_fitting_plan(
                job_id=payload["job_id"],
                accepted_change_ids=[changes[0]["change_id"]],
                store_path=store_path,
                fitting_dir=fitting_dir,
            )
            self.assertTrue(applied["ok"])
            self.assertEqual(applied["remaining_pending"], len(changes) - 1)
            self.assertEqual(FittingJobStore(fitting_dir).read_result(payload["job_id"]).status, "pending_review")

            rejected = reject_fitting_plan(payload["job_id"], fitting_dir=fitting_dir)
            self.assertEqual(rejected["remaining_pending"], 0)
            result = FittingJobStore(fitting_dir).read_result(payload["job_id"])
            statuses = [change.status for change in FittingJobStore(fitting_dir).read_apply_plan(payload["job_id"]).changes]
            self.assertEqual(result.status, "reviewed")
            self.assertIn("applied", statuses)
            self.assertIn("rejected", statuses)


if __name__ == "__main__":
    unittest.main()

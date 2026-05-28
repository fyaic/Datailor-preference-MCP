from __future__ import annotations

import unittest

from preference_agent.models import PreferenceRecord
from preference_agent.refinement import refine_records
from preference_agent.quality import should_recall_user_text


class QualityGateTests(unittest.TestCase):
    def test_rejects_generic_applies_to_and_one_off_paths(self) -> None:
        records = refine_records(
            [
                PreferenceRecord(
                    title="Bad",
                    applies_to="When the agent prepares to reply, execute tasks, or make confirmation decisions",
                    preference="Make this block background transparent.",
                ),
                PreferenceRecord(
                    title="Bad path",
                    applies_to="When the agent changes code",
                    preference=r"Please modify C:\path\to\file.md.",
                ),
            ]
        )
        self.assertEqual(records, [])

    def test_keeps_actionable_generalized_preference(self) -> None:
        records = refine_records(
            [
                PreferenceRecord(
                    title="External write readback",
                    applies_to="After the agent writes content to an external system",
                    preference="After writing content to an external system, read it back and confirm text and Markdown structure are intact.",
                )
            ]
        )
        self.assertEqual(len(records), 1)

    def test_rejects_raw_fragments_from_one_off_user_turns(self) -> None:
        raw_fragments = [
            "B and it must be headless.",
            "I tweaked it slightly; keep it and do not overwrite.",
            "It should be in the local Obsidian .plugin directory; can it be one folder?",
            "> > **Trustworthy AI** The team believes enterprise agents are not throwaway tools.",
            "I want to preview this in the WeChat IDE, but it never loads. Check the code.",
            "When the agent needs to choose response style or execution cadence, B and it must be headless.",
            "When a discussion is reusable and forms a plan or decision, migrate related business and data with minimal user impact, then save documentation.",
            "When the agent changes code, completes implementation, or discusses test/review standards, skip that real-scenario verification section.",
        ]
        records = refine_records(
            [
                PreferenceRecord(
                    title="Raw",
                    applies_to="When the agent chooses response style or execution cadence",
                    preference=fragment,
                )
                for fragment in raw_fragments
            ]
        )

        self.assertEqual(records, [])

    def test_raw_one_off_turns_are_not_recalled(self) -> None:
        self.assertFalse(should_recall_user_text("It should be in the local Obsidian .plugin directory; can it be one folder?"))
        self.assertFalse(should_recall_user_text("I want to preview this in the WeChat IDE, but it never loads. Check the code."))


if __name__ == "__main__":
    unittest.main()

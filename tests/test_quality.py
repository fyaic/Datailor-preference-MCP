from __future__ import annotations

import unittest

from preference_agent.models import PreferenceRecord
from preference_agent.refinement import refine_records


class QualityGateTests(unittest.TestCase):
    def test_rejects_generic_applies_to_and_one_off_paths(self) -> None:
        records = refine_records(
            [
                PreferenceRecord(
                    title="Bad",
                    applies_to="当 agent 准备回复、执行任务或做确认决策时",
                    preference="改成透明的不要这个块儿背景色。",
                ),
                PreferenceRecord(
                    title="Bad path",
                    applies_to="当 agent 修改代码时",
                    preference=r"请修改 C:\path\to\file.md 这个文件。",
                ),
            ]
        )
        self.assertEqual(records, [])

    def test_keeps_actionable_generalized_preference(self) -> None:
        records = refine_records(
            [
                PreferenceRecord(
                    title="中文回读",
                    applies_to="当 agent 把包含中文的内容写入外部系统后",
                    preference="写入包含中文的外部系统后，必须回读确认中文正常且 Markdown 结构未损坏。",
                )
            ]
        )
        self.assertEqual(len(records), 1)


if __name__ == "__main__":
    unittest.main()

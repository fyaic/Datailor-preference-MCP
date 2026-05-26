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

    def test_rejects_raw_fragments_from_one_off_user_turns(self) -> None:
        raw_fragments = [
            "B 而且必须无头。",
            "我稍微调整了一下 请保留不要覆盖。",
            "应该就在obsidian本地目录的.plugin 请问能否合成一个文件夹？",
            "> > **信任负责的AI** 班底团队相信，企业级智能体不是用完即走的工具。",
            "我希望在微信ide里面预览但是一直没有加载出来，请检查以下代码。",
            "当 agent 需要选择回复方式或执行节奏时，B 而且必须无头。",
            "当讨论有复用价值、形成方案或决策时，），相关业务与数据怎么迁移，让用户尽可能无感 后者估计比较难无感但是至少应该能迁移 然后沉淀文档。",
            "当 agent 修改代码、完成实现、询问测试或 review 标准时，不需要*真实场景验证那一段。",
        ]
        records = refine_records(
            [
                PreferenceRecord(
                    title="Raw",
                    applies_to="当 agent 需要选择回复方式或执行节奏时",
                    preference=fragment,
                )
                for fragment in raw_fragments
            ]
        )

        self.assertEqual(records, [])

    def test_raw_one_off_turns_are_not_recalled(self) -> None:
        self.assertFalse(should_recall_user_text("应该就在obsidian本地目录的.plugin 请问能否合成一个文件夹？"))
        self.assertFalse(should_recall_user_text("我希望在微信ide里面预览但是一直没有加载出来，请检查以下代码。"))


if __name__ == "__main__":
    unittest.main()

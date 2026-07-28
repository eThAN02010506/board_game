import unittest

from ai_kp.director.prompts import KP_SYSTEM_PROMPT


class WorldExpansionPromptPolicyTests(unittest.TestCase):
    def test_module_outline_is_not_treated_as_the_complete_world(self) -> None:
        self.assertIn("权威故事大纲而不是完整世界清单", KP_SYSTEM_PROMPT)
        self.assertIn("module_canon", KP_SYSTEM_PROMPT)
        self.assertIn("module_anchor", KP_SYSTEM_PROMPT)
        self.assertIn("关键内容必须保持可达", KP_SYSTEM_PROMPT)
        self.assertIn("不得强迫玩家", KP_SYSTEM_PROMPT)

    def test_generated_expansion_cannot_silently_become_world_fact(self) -> None:
        self.assertIn("generated candidate", KP_SYSTEM_PROMPT)
        self.assertIn("草稿与审批边界", KP_SYSTEM_PROMPT)
        self.assertIn("玩家实际确认前", KP_SYSTEM_PROMPT)
        self.assertIn("已确认的游戏事实高于", KP_SYSTEM_PROMPT)

    def test_plausibility_is_contextual_not_a_fixed_town_template(self) -> None:
        self.assertIn("时代、地域、规模", KP_SYSTEM_PROMPT)
        self.assertIn("不得假定每个小镇必有同一种设施", KP_SYSTEM_PROMPT)
        self.assertIn("合理事物", KP_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()

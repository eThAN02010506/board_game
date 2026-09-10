import unittest

from ai_kp.application.action_adjudication_service import ActionAdjudicationService
from ai_kp.application.turn_service import TurnService
from ai_kp.director.context_builder import ContextAssembly
from ai_kp.director.turn_output import KpTurnOutput


class _Cursor:
    def fetchone(self):
        return {"id": "campaign-1", "system": "coc7"}


class _AliasRepo:
    connection = None

    def __init__(self):
        self.connection = self

    def execute(self, *_args, **_kwargs):
        return _Cursor()

    def resolve_skill_target(self, _campaign_id, _pc_id, skill_name):
        if skill_name == "侦查":
            return {"skill_key": "coc7.spot_hidden", "target": 50}
        if skill_name == "聆听":
            return {"skill_key": "coc7.listen", "target": 40}
        return None

    def list_character_skill_targets(self, _campaign_id, _pc_id):
        return [
            {"skill_name": "侦查", "skill_key": "coc7.spot_hidden", "target": 50},
            {"skill_name": "聆听", "skill_key": "coc7.listen", "target": 40},
        ]


class ActionAdjudicationAliasTests(unittest.TestCase):
    def setUp(self):
        self.service = ActionAdjudicationService(_AliasRepo())
        self.action = {
            "campaign_id": "campaign-1",
            "pc_id": "pc-1",
            "action_text": "",
        }

    def test_standard_english_name_resolves_to_localized_sheet_skill(self):
        resolved = self.service._resolve_sheet_skill(self.action, "Spot Hidden")

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved[0], "侦查")
        self.assertEqual(resolved[1]["target"], 50)

    def test_ambiguous_concept_is_left_for_player_choice(self):
        self.assertIsNone(self.service._resolve_sheet_skill(self.action, "Perception"))

    def test_concept_word_does_not_turn_an_automatic_action_into_a_check(self):
        self.assertIsNone(
            self.service._resolve_sheet_skill(
                self.action, "观察", allow_concepts=False
            )
        )

    def test_one_explicit_player_skill_outranks_a_different_model_guess(self):
        self.action["action_text"] = (
            "我大声喊话，然后用自己的侦查观察对方是否有细微反应。"
        )

        resolved = self.service._explicit_action_skill(self.action)

        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved[0], "侦查")

    def test_multiple_named_skills_do_not_guess_the_players_choice(self):
        self.action["action_text"] = "我选择侦查或选择聆听，但还没有决定。"

        self.assertIsNone(self.service._explicit_action_skill(self.action))

    def test_skill_subject_mention_is_not_a_player_choice(self):
        self.action["action_text"] = "我呼叫医生，因为这个人可能需要急救。"

        self.assertIsNone(self.service._explicit_action_skill(self.action))

    def test_unseen_scene_baseline_is_revealed_before_a_check_preview(self):
        output = KpTurnOutput.model_validate(
            {
                "public_narration": "你准备仔细搜索房间。",
                "kp_notes": "",
                "action_ruling": {
                    "goal": "寻找隐藏物",
                    "method": "侦查",
                    "target": "当前房间",
                    "feasibility": "possible",
                    "resolution": "check",
                    "reason": "隐藏细节存在不确定性",
                    "maximum_effect": "找到隐藏细节",
                },
                "proposed_checks": [
                    {
                        "skill": "侦查",
                        "difficulty": "regular",
                        "reason": "寻找隐藏细节",
                    }
                ],
            }
        )
        context = ContextAssembly(
            messages=[],
            included_sources=[
                {
                    "kind": "module_scene_baseline",
                    "content": "正中央的平台上躺着一个一动不动的人。",
                }
            ],
            excluded_sources=[],
            token_estimate=0,
            visibility_scope="kp",
        )

        guarded = TurnService._reveal_unseen_scene_baseline(output, context)

        self.assertTrue(
            guarded.public_narration.startswith(
                "正中央的平台上躺着一个一动不动的人。"
            )
        )

    def test_near_duplicate_scene_baselines_are_revealed_once(self):
        output = KpTurnOutput.model_validate(
            {
                "public_narration": "你准备记笔记。",
                "kp_notes": "",
                "action_ruling": {
                    "goal": "听取委托",
                    "method": "交谈",
                    "target": "房东",
                    "feasibility": "possible",
                    "resolution": "automatic",
                    "reason": "普通交谈",
                    "maximum_effect": "取得委托",
                },
                "proposed_checks": [],
            }
        )
        context = ContextAssembly(
            messages=[],
            included_sources=[
                {
                    "kind": "module_scene_baseline",
                    "content": "房东委托你调查老房子，并把钥匙交给你。",
                },
                {
                    "kind": "module_scene_baseline",
                    "content": "房东委托你们调查这座老房子，随后把房门钥匙交给你们。",
                },
            ],
            excluded_sources=[],
            token_estimate=0,
            visibility_scope="kp",
        )

        guarded = TurnService._reveal_unseen_scene_baseline(output, context)

        self.assertEqual(guarded.public_narration.count("房东委托"), 1)


if __name__ == "__main__":
    unittest.main()

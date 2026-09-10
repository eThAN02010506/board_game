from __future__ import annotations

import unittest

from ai_kp.platform.modules.public_opening import extract_public_opening


class PublicOpeningTests(unittest.TestCase):
    def test_projects_only_italic_prose_after_explicit_read_aloud_instruction(self) -> None:
        chunks = [
            {"id": "secret", "order_index": 1, "semantic_kind": "text", "text": "凶手是管家。"},
            {
                "id": "instruction", "order_index": 2, "semantic_kind": "text",
                "text": "给守密人的提示：大声读出以下内容，然后扮演委托人。",
                "style_annotations": ["italic"],
            },
            {"id": "heading", "order_index": 3, "semantic_kind": "heading", "text": "文字材料 1"},
            {
                "id": "opening-1", "order_index": 4, "semantic_kind": "text",
                "text": "一位忧心忡忡的委托人请你调查旧宅。", "style_annotations": ["italic"],
            },
            {
                "id": "opening-2", "order_index": 5, "semantic_kind": "text",
                "text": "他把地址和钥匙交给了你。", "style_annotations": ["italic"],
            },
            {"id": "keeper", "order_index": 6, "semantic_kind": "check", "text": "要求侦查检定。"},
        ]

        self.assertEqual(
            extract_public_opening(chunks),
            "一位忧心忡忡的委托人请你调查旧宅。\n\n他把地址和钥匙交给了你。",
        )

    def test_fails_closed_without_explicit_source_authority(self) -> None:
        self.assertIsNone(extract_public_opening([{
            "id": "secret", "order_index": 1, "semantic_kind": "text",
            "text": "这段斜体其实是秘密。", "style_annotations": ["italic"],
        }]))

    def test_general_description_of_boxed_text_is_not_an_opening_anchor(self) -> None:
        self.assertIsNone(extract_public_opening([
            {
                "id": "convention", "order_index": 1, "semantic_kind": "text",
                "text": "模组中的带框文字是为了朗读给玩家。",
            },
            {
                "id": "later", "order_index": 2, "semantic_kind": "text",
                "text": "这不是紧随开场指令的文字。", "style_annotations": ["italic"],
            },
        ]))

    def test_does_not_project_role_only_guidance(self) -> None:
        self.assertIsNone(extract_public_opening([
            {"id": "anchor", "order_index": 1, "text": "Read the following aloud."},
            {
                "id": "keeper", "order_index": 2, "semantic_kind": "text",
                "text": "Keeper should reveal the monster now.", "style_annotations": ["italic"],
            },
        ]))


if __name__ == "__main__":
    unittest.main()

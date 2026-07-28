import unittest
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings
from ai_kp.planning.capabilities import (
    CAPABILITIES,
    Capability,
    list_capabilities,
    validate_capabilities,
)


class CapabilityPlaceholderTests(unittest.TestCase):
    def test_catalog_has_unique_ids_and_valid_dependencies(self) -> None:
        validate_capabilities()
        ids = {capability.id for capability in CAPABILITIES}
        self.assertEqual(len(ids), len(CAPABILITIES))
        for capability in CAPABILITIES:
            self.assertTrue(set(capability.dependencies) <= ids)
            self.assertTrue(capability.acceptance)

    def test_all_unfinished_product_areas_have_explicit_placeholders(self) -> None:
        unfinished = {
            item["id"]: item["status"] for item in list_capabilities(include_available=False)
        }
        expected = {
            "module_library",
            "module_document_import",
            "world_expansion",
            "party_route_planning",
            "check_resolution",
            "ruleset_plugins",
            "character_timeline",
            "npc_reappearance",
            "memory_workspace",
            "world_fact_ledger",
            "semantic_memory_search",
            "map_asset_revisions",
            "map_reveal_editor",
            "image_map_generation",
            "model_quantization_profiles",
            "campaign_backup_restore",
            "human_kp_modes",
            "voice_companion",
            "webrtc_rooms",
        }
        self.assertEqual(expected, set(unfinished))
        self.assertTrue(all(status in {"partial", "planned"} for status in unfinished.values()))

    def test_world_fact_plan_preserves_event_authority_and_epistemic_boundaries(self) -> None:
        capability = next(
            item for item in CAPABILITIES if item.id == "world_fact_ledger"
        )
        plan_text = " ".join((capability.summary, *capability.acceptance))

        self.assertEqual(capability.status, "partial")
        self.assertIn("权威事件流", plan_text)
        self.assertIn("追加", plan_text)
        self.assertIn("角色认知", plan_text)
        self.assertIn("AI hypothesis", plan_text)
        self.assertIn("proposal_approval", capability.dependencies)

    def test_quantization_plan_keeps_four_and_eight_bit_profiles_deferred(self) -> None:
        capability = next(
            item for item in CAPABILITIES if item.id == "model_quantization_profiles"
        )
        plan_text = " ".join((capability.summary, *capability.acceptance))

        self.assertEqual("planned", capability.status)
        self.assertIn("4 位", plan_text)
        self.assertIn("8 位", plan_text)
        self.assertIn("不实现", plan_text)
        self.assertIn("真实 AI KP 样例", plan_text)
        self.assertIn("model_management", capability.dependencies)

    def test_module_import_is_pdf_word_only_and_preserves_embedded_images(
        self,
    ) -> None:
        capability = next(
            item for item in CAPABILITIES if item.id == "module_document_import"
        )

        self.assertEqual("PDF/Word KP 本导入", capability.label)
        self.assertIn("照片", capability.summary)
        self.assertTrue(
            any("PDF/Word 以外" in item for item in capability.acceptance)
        )
        self.assertTrue(
            any("没有视觉模型" in item for item in capability.acceptance)
        )

    def test_world_expansion_keeps_generated_content_below_authoritative_facts(
        self,
    ) -> None:
        capability = next(
            item for item in CAPABILITIES if item.id == "world_expansion"
        )
        plan_text = " ".join((capability.summary, *capability.acceptance))

        self.assertEqual("partial", capability.status)
        self.assertIn("Canon", plan_text)
        self.assertIn("剧情锚点保持可达", plan_text)
        self.assertIn("生成内容在批准", plan_text)
        self.assertIn("时代、地域、聚落规模", plan_text)
        self.assertIn("world_fact_ledger", capability.dependencies)
        self.assertIn("proposal_approval", capability.dependencies)

    def test_public_catalog_endpoint_can_filter_available_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            app = create_app(Settings(db_path=Path(tmpdir) / "capabilities.sqlite3"))
            with TestClient(app) as client:
                response = client.get("/capabilities", params={"include_available": False})

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload)
        self.assertTrue(all(item["status"] in {"partial", "planned"} for item in payload))
        self.assertTrue(all(item["acceptance"] for item in payload))

    def test_character_sheet_plan_keeps_player_ownership_and_excel_import(self) -> None:
        character_sheets = next(
            capability
            for capability in CAPABILITIES
            if capability.id == "character_sheets"
        )
        plan_text = " ".join((character_sheets.summary, *character_sheets.acceptance))

        self.assertIn("独立调查员页面", plan_text)
        self.assertIn("不可变草稿版本已可用", plan_text)
        self.assertIn("按团提交", plan_text)
        self.assertIn("Excel", plan_text)
        self.assertIn("预览", plan_text)
        self.assertIn("不执行工作簿公式或宏", plan_text)
        self.assertIn("确定性规则服务重新计算", plan_text)
        self.assertIn("退回修改", plan_text)
        self.assertIn("修改意见", plan_text)
        self.assertIn("每个团的 KP 独立审核", plan_text)
        self.assertIn("未批准版本不得进入该团或提供给 AI", plan_text)
        self.assertIn("不同团次", plan_text)
        self.assertIn("已批准版本不可变", plan_text)
        self.assertIn("团内运行状态", plan_text)
        self.assertIn("seat_invitations", character_sheets.dependencies)

    def test_rule_capabilities_require_traceable_deterministic_results(self) -> None:
        capabilities = {capability.id: capability for capability in CAPABILITIES}
        check_text = " ".join(
            (
                capabilities["check_resolution"].summary,
                *capabilities["check_resolution"].acceptance,
            )
        )
        plugin_text = " ".join(
            (
                capabilities["ruleset_plugins"].summary,
                *capabilities["ruleset_plugins"].acceptance,
            )
        )

        self.assertIn("章节页码", check_text)
        self.assertIn("重放", check_text)
        self.assertIn("可选规则", plugin_text)
        self.assertIn("AI 不能绕过规则插件", plugin_text)

    def test_catalog_rejects_dependency_cycles(self) -> None:
        cyclic = (
            Capability(
                id="first",
                label="First",
                status="planned",
                phase="test",
                audience="all",
                summary="test",
                dependencies=("second",),
                acceptance=("test",),
            ),
            Capability(
                id="second",
                label="Second",
                status="planned",
                phase="test",
                audience="all",
                summary="test",
                dependencies=("first",),
                acceptance=("test",),
            ),
        )
        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            validate_capabilities(cyclic)


if __name__ == "__main__":
    unittest.main()

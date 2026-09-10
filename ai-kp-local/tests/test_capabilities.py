import tempfile
import unittest
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
            "scene_director",
            "world_expansion",
            "check_resolution",
            "parallel_action_resolution",
            "ruleset_plugins",
            "npc_reappearance",
            "world_fact_ledger",
            "long_campaign_validation",
            "semantic_memory_search",
            "map_asset_revisions",
            "map_reveal_editor",
            "image_map_generation",
            "model_quantization_profiles",
            "player_handouts",
            "simulated_campaign_evaluation",
            "operational_safety",
            "voice_companion",
            "webrtc_rooms",
        }
        self.assertEqual(expected, set(unfinished))
        self.assertTrue(all(status in {"partial", "planned"} for status in unfinished.values()))

    def test_long_term_product_journeys_are_reported_conservatively(self) -> None:
        capabilities = {item.id: item for item in CAPABILITIES}

        self.assertEqual("available", capabilities["session_zero_safety"].status)
        self.assertTrue(capabilities["session_zero_safety"].evidence_refs)
        self.assertEqual("available", capabilities["observer_access"].status)
        self.assertEqual("available", capabilities["table_communications"].status)
        self.assertTrue(capabilities["observer_access"].evidence_refs)
        self.assertTrue(capabilities["table_communications"].evidence_refs)
        self.assertEqual("available", capabilities["session_continuity"].status)
        self.assertEqual("available", capabilities["combat_encounters"].status)
        self.assertEqual("available", capabilities["inventory_economy"].status)
        self.assertTrue(capabilities["inventory_economy"].evidence_refs)
        self.assertEqual("available", capabilities["character_lifecycle"].status)
        self.assertTrue(capabilities["character_lifecycle"].evidence_refs)
        self.assertEqual("available", capabilities["human_kp_modes"].status)
        self.assertTrue(capabilities["human_kp_modes"].evidence_refs)
        human_kp_text = " ".join(
            (
                capabilities["human_kp_modes"].summary,
                *capabilities["human_kp_modes"].acceptance,
            )
        )
        self.assertIn("来源无关准备器", human_kp_text)
        self.assertIn("Need Help", human_kp_text)
        self.assertIn("preview hash", human_kp_text)
        self.assertIn("并行行动", human_kp_text)
        self.assertIn("request_abandoned", human_kp_text)
        self.assertEqual("partial", capabilities["long_campaign_validation"].status)

        combat_text = " ".join(
            (
                capabilities["combat_encounters"].summary,
                *capabilities["combat_encounters"].acceptance,
            )
        )
        self.assertIn("玩家已有本人回合", combat_text)
        self.assertIn("真实玩家浏览器", combat_text)
        self.assertIn("关闭并重开数据库", combat_text)
        self.assertIn("20 Session/3000 分钟", capabilities["long_campaign_validation"].summary)

    def test_new_prd_capabilities_have_stable_requirement_traceability(self) -> None:
        capabilities = {item.id: item for item in CAPABILITIES}
        expected_requirements = {
            "session_zero_safety": ("FR-16",),
            "observer_access": ("FR-17",),
            "table_communications": ("FR-17",),
            "session_continuity": ("FR-18",),
            "parallel_action_resolution": ("FR-09",),
            "combat_encounters": ("FR-19",),
            "inventory_economy": ("FR-20",),
            "character_lifecycle": ("FR-21",),
            "human_kp_modes": ("FR-13A",),
            "long_campaign_validation": ("NFR-05", "AC-LONG"),
        }

        for capability_id, requirement_ids in expected_requirements.items():
            capability = capabilities[capability_id]
            self.assertEqual(requirement_ids, capability.requirement_ids)
            if capability.status == "partial":
                self.assertTrue(capability.evidence_refs)

    def test_catalog_evidence_references_resolve_inside_the_repository(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]

        for capability in CAPABILITIES:
            for evidence_ref in capability.evidence_refs:
                evidence_path = repository_root / evidence_ref
                self.assertTrue(
                    evidence_path.is_file(),
                    f"{capability.id} references missing evidence: {evidence_ref}",
                )

    def test_module_and_npc_claims_match_current_release_boundaries(self) -> None:
        capabilities = {item.id: item for item in CAPABILITIES}
        module_text = " ".join(
            (
                capabilities["module_library"].summary,
                *capabilities["module_library"].acceptance,
            )
        )
        npc_text = " ".join(
            (
                capabilities["npc_reappearance"].summary,
                *capabilities["npc_reappearance"].acceptance,
            )
        )

        self.assertIn("持久化每个分区结果", module_text)
        self.assertIn("覆盖率", module_text)
        self.assertNotIn("分区持久化/恢复和覆盖率闸门尚未完成", module_text)
        self.assertIn("已存在且已关联本团", npc_text)
        self.assertIn("不会自动建档或入团", npc_text)

    def test_backup_is_available_and_semantic_search_has_a_lexical_baseline(self) -> None:
        capabilities = {item.id: item for item in CAPABILITIES}

        self.assertEqual(capabilities["campaign_backup_restore"].status, "available")
        self.assertIn("SHA-256", capabilities["campaign_backup_restore"].summary)
        self.assertEqual(capabilities["semantic_memory_search"].status, "partial")
        self.assertIn("FTS5", capabilities["semantic_memory_search"].summary)

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
        self.assertTrue(all("requirement_ids" in item for item in payload))
        self.assertTrue(all("evidence_refs" in item for item in payload))

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

    def test_catalog_rejects_duplicate_or_empty_traceability_entries(self) -> None:
        invalid_cases = (
            Capability(
                id="duplicate-requirement",
                label="Duplicate",
                status="planned",
                phase="test",
                audience="all",
                summary="test",
                acceptance=("test",),
                requirement_ids=("FR-X", "FR-X"),
            ),
            Capability(
                id="empty-evidence",
                label="Empty",
                status="partial",
                phase="test",
                audience="all",
                summary="test",
                acceptance=("test",),
                evidence_refs=("",),
            ),
        )

        for capability in invalid_cases:
            with self.assertRaises(ValueError):
                validate_capabilities((capability,))


if __name__ == "__main__":
    unittest.main()

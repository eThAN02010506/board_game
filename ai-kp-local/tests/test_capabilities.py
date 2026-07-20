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
            "character_sheets",
            "seat_invitations",
            "module_library",
            "module_document_import",
            "party_route_planning",
            "check_resolution",
            "ruleset_plugins",
            "character_timeline",
            "npc_reappearance",
            "memory_workspace",
            "semantic_memory_search",
            "map_asset_revisions",
            "map_reveal_editor",
            "image_map_generation",
            "model_management",
            "campaign_backup_restore",
            "human_kp_modes",
            "voice_companion",
            "webrtc_rooms",
        }
        self.assertEqual(expected, set(unfinished))
        self.assertTrue(all(status in {"partial", "planned"} for status in unfinished.values()))

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

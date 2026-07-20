import ast
import importlib
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ai_kp.api import main as compatibility_main
from ai_kp.application.world_service import WorldService
from ai_kp.core.config import Settings
from ai_kp.core.db import connect
from ai_kp.core.repository import Repository
from ai_kp.kp.context_repository import ContextAssemblyRepository
from ai_kp.maps.repository import MapRepository
from ai_kp.realtime.repository import RealtimeRepository
from ai_kp.security.repository import SecurityRepository
from ai_kp.storage.repositories.turns import TurnRepository
from ai_kp.storage.repositories.world import WorldRepository
from ai_kp.storage.migrations import LATEST_SCHEMA_VERSION, MIGRATIONS
from ai_kp.storage.sqlite import SQLiteRepository


PROJECT_ROOT = Path(__file__).parents[1]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _service_method_calls(path: Path) -> set[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    service_variables: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
            continue
        if not value.func.id.endswith("Service"):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                service_variables[target.id] = value.func.id

    calls: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        receiver = node.func.value
        if (
            isinstance(receiver, ast.Call)
            and isinstance(receiver.func, ast.Name)
            and receiver.func.id.endswith("Service")
        ):
            calls.add((receiver.func.id, node.func.attr))
        elif isinstance(receiver, ast.Name) and receiver.id in service_variables:
            calls.add((service_variables[receiver.id], node.func.attr))
    return calls


def test_legacy_api_entrypoint_remains_an_identity_alias() -> None:
    composition = importlib.import_module("ai_kp.api.app")

    assert compatibility_main.create_app is composition.create_app
    assert compatibility_main.app is composition.app
    assert set(compatibility_main.__all__) == {
        "OpenAICompatibleClient",
        "app",
        "create_app",
    }
    # Existing real-case tests patch this public compatibility seam.
    assert compatibility_main.OpenAICompatibleClient is not None


def test_application_layer_does_not_depend_on_fastapi_or_http_adapters() -> None:
    application_dir = PROJECT_ROOT / "src" / "ai_kp" / "application"
    violations: dict[str, list[str]] = {}
    for path in sorted(application_dir.glob("*.py")):
        forbidden = sorted(
            name
            for name in _imports(path)
            if name == "fastapi"
            or name.startswith("fastapi.")
            or name == "ai_kp.api"
            or name.startswith("ai_kp.api.")
        )
        if forbidden:
            violations[path.name] = forbidden

    assert violations == {}


def test_mutating_http_routes_delegate_to_application_services() -> None:
    routers_dir = PROJECT_ROOT / "src" / "ai_kp" / "api" / "routers"
    expected = {
        "campaigns.py": {
            ("CampaignService", "create"),
        },
        "sessions.py": {
            ("SessionService", "create"),
            ("SessionService", "join"),
            ("SessionService", "revoke_member_and_rotate_code"),
            ("SessionService", "assign_member_pc"),
            ("SessionService", "rotate_join_code"),
            ("SessionService", "close"),
        },
        "world.py": {
            ("WorldService", "create_pc"),
            ("WorldService", "append_event"),
            ("WorldService", "add_memory"),
            ("WorldService", "import_module"),
            ("WorldService", "create_npc"),
            ("WorldService", "create_campaign_npc"),
            ("WorldService", "link_npc"),
        },
        "maps.py": {
            ("MapService", "generate_and_save"),
            ("MapService", "publish"),
            ("MapService", "unpublish"),
            ("MapService", "place_token"),
            ("MapService", "move_token"),
        },
        "turns.py": {
            ("TurnService", "submit_player_action"),
            ("TurnService", "create_manual_proposal"),
            ("TurnService", "approve"),
            ("TurnService", "reject"),
            ("TurnService", "create_ai_proposal"),
        },
    }

    for filename, required_calls in expected.items():
        actual_calls = _service_method_calls(routers_dir / filename)
        assert required_calls <= actual_calls, (
            f"{filename} bypassed its application service: "
            f"{sorted(required_calls - actual_calls)}"
        )


def test_repository_facade_has_the_intended_mro_and_no_method_copies() -> None:
    assert Repository.__bases__ == (
        WorldRepository,
        TurnRepository,
        MapRepository,
        ContextAssemblyRepository,
        SecurityRepository,
        RealtimeRepository,
    )
    assert Repository.__mro__.count(SQLiteRepository) == 1
    assert Repository.create_campaign is WorldRepository.create_campaign
    assert Repository.create_turn_proposal is TurnRepository.create_turn_proposal
    assert Repository.create_map is MapRepository.create_map
    assert Repository.create_context_assembly is ContextAssemblyRepository.create_context_assembly
    assert Repository.create_campaign_session is SecurityRepository.create_campaign_session
    assert Repository.append_realtime_event is RealtimeRepository.append_realtime_event


def test_formal_migration_registry_keeps_all_five_legacy_upgrades() -> None:
    assert LATEST_SCHEMA_VERSION == 5
    assert [(item.version, item.name) for item in MIGRATIONS] == [
        (1, "add_proposed_checks_to_turn_proposals"),
        (2, "add_player_action_idempotency"),
        (3, "add_map_status"),
        (4, "add_map_token_version"),
        (5, "add_proposed_npc_updates_to_turn_proposals"),
    ]


def test_failed_http_use_case_rolls_back_its_partial_write(tmp_path: Path) -> None:
    db_path = tmp_path / "request-rollback.sqlite3"
    settings = Settings(
        db_path=db_path,
        admin_token="architecture-test-admin",
        local_admin_enabled=False,
    )
    app = compatibility_main.create_app(settings)
    admin_headers = {"X-AI-KP-Admin-Token": "architecture-test-admin"}

    with TestClient(app) as client:
        campaign_response = client.post(
            "/campaigns",
            headers=admin_headers,
            json={"title": "Rollback boundary"},
        )
        assert campaign_response.status_code == 200
        campaign = campaign_response.json()
        session_response = client.post(
            f"/campaigns/{campaign['id']}/sessions",
            headers=admin_headers,
            json={"kp_display_name": "KP"},
        )
        assert session_response.status_code == 200
        kp_headers = {
            "Authorization": f"Bearer {session_response.json()['access_token']}"
        }

        def write_then_fail(self, campaign_id, command):
            self.repo.create_npc(name=command.name)
            raise ValueError("forced failure after write")

        with patch.object(WorldService, "create_campaign_npc", write_then_fail):
            response = client.post(
                f"/campaigns/{campaign['id']}/npcs",
                headers=kp_headers,
                json={"name": "Must roll back"},
            )
        assert response.status_code == 409

    connection = connect(db_path)
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM npcs WHERE name = ?", ("Must roll back",)
        ).fetchone()[0]
        assert count == 0
    finally:
        connection.close()

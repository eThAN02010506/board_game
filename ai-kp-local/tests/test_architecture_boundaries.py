import ast
import importlib
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ai_kp.api import main as compatibility_main
from ai_kp.application.world_service import WorldService
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.character_timelines import CharacterTimelineRepository
from ai_kp.infrastructure.database.checks import SkillCheckRepository
from ai_kp.infrastructure.database.context_assemblies import ContextAssemblyRepository
from ai_kp.infrastructure.database.evaluations import EvaluationRepository
from ai_kp.infrastructure.database.facts import FactRepository
from ai_kp.infrastructure.database.handouts import HandoutRepository
from ai_kp.infrastructure.database.investigators import InvestigatorRepository
from ai_kp.infrastructure.database.maps import MapRepository
from ai_kp.infrastructure.database.memory_timeline import MemoryTimelineRepository
from ai_kp.infrastructure.database.migrations import LATEST_SCHEMA_VERSION, MIGRATIONS
from ai_kp.infrastructure.database.model_configuration import ModelConfigurationRepository
from ai_kp.infrastructure.database.module_graph import ModuleGraphRepository
from ai_kp.infrastructure.database.module_imports import ModuleImportRepository
from ai_kp.infrastructure.database.module_knowledge import ModuleKnowledgeRepository
from ai_kp.infrastructure.database.module_runs import ModuleRunRepository
from ai_kp.infrastructure.database.npc_reappearances import NpcReappearanceRepository
from ai_kp.infrastructure.database.private_random_resolutions import (
    PrivateRandomResolutionRepository,
)
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.rulebooks import RulebookRepository
from ai_kp.infrastructure.database.schema import connect
from ai_kp.infrastructure.database.security import SecurityRepository
from ai_kp.infrastructure.database.session_recaps import SessionRecapRepository
from ai_kp.infrastructure.database.session_seats import SessionSeatRepository
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.infrastructure.database.travel_graph import TravelGraphRepository
from ai_kp.infrastructure.database.turns import TurnRepository
from ai_kp.infrastructure.database.world import WorldRepository
from ai_kp.infrastructure.database.world_expansion_materializations import (
    WorldExpansionMaterializationRepository,
)
from ai_kp.infrastructure.realtime.outbox import RealtimeRepository

PROJECT_ROOT = Path(__file__).parents[1]


def test_rules_reference_declares_source_identity_and_ai_boundary() -> None:
    reference = (PROJECT_ROOT / "docs" / "RULES_REFERENCE.md").read_text(
        encoding="utf-8"
    )

    assert "coc7-keeper-cn-2002c" in reference
    assert "f6113754ea095de0a60b6fb593f38df0041e0573858c28f41204a1edd039f707" in reference
    assert "coc7_core" in reference
    assert "platform_policy" in reference
    assert "deterministic ruleset services" in reference
    assert "must not commit facts that depend on an unresolved roll" in reference


def test_character_sheet_design_separates_identity_revision_review_and_runtime() -> None:
    reference = (PROJECT_ROOT / "docs" / "CHARACTER_SHEET_MODEL.md").read_text(
        encoding="utf-8"
    )

    for token in (
        "eeb4ceea026bf172cd3337f42f1815da897c5c7fa46fb61641b83ef146759545",
        "investigators",
        "investigator_revisions",
        "campaign_investigators",
        "investigator_campaign_state",
        "approved_revision_id",
        "changes_requested",
        "禁止运行宏",
        "后端按",
        "逐阶段真实验收",
    ):
        assert token in reference


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_source_tree_has_no_comment_only_leaf_placeholders() -> None:
    python_placeholders: list[str] = []
    for path in sorted((PROJECT_ROOT / "src" / "ai_kp").rglob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if (
            len(tree.body) == 1
            and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)
        ):
            python_placeholders.append(str(path.relative_to(PROJECT_ROOT)))

    frontend_placeholders: list[str] = []
    frontend_root = PROJECT_ROOT / "apps" / "web" / "src"
    for path in sorted((*frontend_root.rglob("*.ts"), *frontend_root.rglob("*.tsx"))):
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if lines and all(line.startswith("//") for line in lines):
            frontend_placeholders.append(str(path.relative_to(PROJECT_ROOT)))

    assert python_placeholders == []
    assert frontend_placeholders == []


def test_legacy_compatibility_paths_contain_no_second_implementation() -> None:
    legacy_roots = (
        "characters",
        "kp",
        "llm",
        "maps",
        "memory",
        "modules",
        "realtime",
        "rulebook",
        "rules",
        "security",
        "storage",
    )
    paths = [
        path
        for root_name in legacy_roots
        for path in (PROJECT_ROOT / "src" / "ai_kp" / root_name).rglob("*.py")
    ]
    paths.extend(
        PROJECT_ROOT / "src" / "ai_kp" / "core" / filename
        for filename in ("config.py", "db.py", "repository.py")
    )
    implementations: list[str] = []
    for path in sorted(paths):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            for node in ast.walk(tree)
        ):
            implementations.append(str(path.relative_to(PROJECT_ROOT)))

    assert implementations == []


def _service_method_calls(path: Path) -> set[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    service_variables: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for argument in node.args.args:
            if isinstance(argument.annotation, ast.Name) and argument.annotation.id.endswith(
                "Service"
            ):
                service_variables[argument.arg] = argument.annotation.id
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
        if not isinstance(node, ast.Attribute):
            continue
        receiver = node.value
        if (
            isinstance(receiver, ast.Call)
            and isinstance(receiver.func, ast.Name)
            and receiver.func.id.endswith("Service")
        ):
            calls.add((receiver.func.id, node.attr))
        elif isinstance(receiver, ast.Name) and receiver.id in service_variables:
            calls.add((service_variables[receiver.id], node.attr))
    return calls


def test_legacy_api_entrypoint_remains_an_identity_alias() -> None:
    composition = importlib.import_module("ai_kp.bootstrap.composition")

    assert compatibility_main.create_app is composition.create_app
    assert compatibility_main.app is composition.app
    assert set(compatibility_main.__all__) == {
        "OpenAICompatibleClient",
        "app",
        "create_app",
    }
    # Existing real-case tests patch this public compatibility seam.
    assert compatibility_main.OpenAICompatibleClient is not None


def test_application_layer_depends_on_ports_not_delivery_or_infrastructure() -> None:
    application_dir = PROJECT_ROOT / "src" / "ai_kp" / "application"
    violations: dict[str, list[str]] = {}
    forbidden_prefixes = (
        "ai_kp.api",
        "ai_kp.bootstrap",
        "ai_kp.infrastructure",
    )
    for path in sorted(application_dir.rglob("*.py")):
        forbidden = sorted(
            name
            for name in _imports(path)
            if name == "fastapi"
            or name.startswith("fastapi.")
            or any(
                name == prefix or name.startswith(f"{prefix}.")
                for prefix in forbidden_prefixes
            )
        )
        if forbidden:
            violations[str(path.relative_to(application_dir))] = forbidden

    assert violations == {}


def test_director_and_rule_authoring_do_not_import_infrastructure() -> None:
    violations: dict[str, list[str]] = {}
    for relative_dir in ("director", "rule_authoring"):
        root = PROJECT_ROOT / "src" / "ai_kp" / relative_dir
        for path in sorted(root.rglob("*.py")):
            forbidden = sorted(
                name
                for name in _imports(path)
                if name == "ai_kp.infrastructure"
                or name.startswith("ai_kp.infrastructure.")
            )
            if forbidden:
                violations[str(path.relative_to(PROJECT_ROOT))] = forbidden

    assert violations == {}


def test_generic_layers_do_not_import_ruleset_implementations_directly() -> None:
    violations: dict[str, list[str]] = {}
    for relative_dir in ("application", "api", "storage"):
        root = PROJECT_ROOT / "src" / "ai_kp" / relative_dir
        for path in sorted(root.rglob("*.py")):
            forbidden = sorted(
                name
                for name in _imports(path)
                if name == "ai_kp.rules" or name.startswith("ai_kp.rules.")
            )
            if forbidden:
                violations[str(path.relative_to(PROJECT_ROOT))] = forbidden

    assert violations == {}


def test_active_layers_do_not_depend_on_migrated_compatibility_packages() -> None:
    legacy_prefixes = (
        "ai_kp.characters",
        "ai_kp.kp",
        "ai_kp.llm",
        "ai_kp.maps",
        "ai_kp.memory",
        "ai_kp.modules",
        "ai_kp.realtime",
        "ai_kp.rulebook",
        "ai_kp.rules",
        "ai_kp.security",
        "ai_kp.storage",
    )
    violations: dict[str, list[str]] = {}
    for relative_dir in (
        "api",
        "application",
        "bootstrap",
        "director",
        "infrastructure",
        "platform",
        "rule_authoring",
        "rulesets",
    ):
        root = PROJECT_ROOT / "src" / "ai_kp" / relative_dir
        for path in sorted(root.rglob("*.py")):
            forbidden = sorted(
                name
                for name in _imports(path)
                if any(
                    name == prefix or name.startswith(f"{prefix}.")
                    for prefix in legacy_prefixes
                )
            )
            if forbidden:
                violations[str(path.relative_to(PROJECT_ROOT))] = forbidden

    assert violations == {}


def test_coc7_plugin_uses_canonical_ruleset_modules_not_compatibility_shims() -> None:
    imports = _imports(
        PROJECT_ROOT / "src" / "ai_kp" / "rulesets" / "coc7" / "plugin.py"
    )

    assert not {
        name
        for name in imports
        if name in {"ai_kp.rules", "ai_kp.characters"}
        or name.startswith(("ai_kp.rules.", "ai_kp.characters."))
    }


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
            ("SessionService", "rotate_join_code"),
            ("SessionService", "close"),
            ("SessionService", "create_seat"),
            ("SessionService", "claim_seat"),
            ("SessionService", "recover_seat"),
            ("SessionService", "reissue_seat_invitation"),
            ("SessionService", "revoke_seat"),
        },
        "checks.py": {
            ("CheckService", "create"),
            ("CheckService", "resolve"),
            ("CheckService", "override"),
            ("CheckService", "cancel"),
            ("CheckService", "push"),
        },
        "world.py": {
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
            ("CheckConsequenceService", "generate"),
            ("TurnService", "submit_player_action"),
            ("TurnService", "create_manual_proposal"),
            ("TurnService", "approve"),
            ("TurnService", "reject"),
            ("TurnService", "create_ai_proposal"),
        },
        "investigators.py": {
            ("InvestigatorService", "create_profile"),
            ("InvestigatorService", "preview_excel"),
            ("InvestigatorService", "create_investigator"),
            ("InvestigatorService", "revise_investigator"),
        },
        "rulebooks.py": {
            ("RulebookService", "ingest_pdf"),
            ("RulebookService", "index_source"),
            ("RulebookService", "extract_rules"),
            ("RulebookService", "query"),
            ("RulebookService", "execute"),
        },
        "module_graph.py": {
            ("ModuleGraphService", "create_entity"),
            ("ModuleGraphService", "create_relation"),
            ("ModuleGraphService", "check_reachability"),
        },
        "memory.py": {
            ("MemoryTimelineService", "list_timeline"),
            ("MemoryTimelineService", "curate"),
            ("SessionRecapService", "generate"),
            ("SessionRecapService", "latest"),
            ("SessionRecapService", "review"),
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
        EvaluationRepository,
        FactRepository,
        HandoutRepository,
        TurnRepository,
        MapRepository,
        ContextAssemblyRepository,
        SecurityRepository,
        RealtimeRepository,
        InvestigatorRepository,
        SessionSeatRepository,
        SkillCheckRepository,
        RulebookRepository,
        ModuleImportRepository,
        ModuleKnowledgeRepository,
        ModuleGraphRepository,
        ModuleRunRepository,
        ModelConfigurationRepository,
        WorldExpansionMaterializationRepository,
        NpcReappearanceRepository,
        TravelGraphRepository,
        PrivateRandomResolutionRepository,
        MemoryTimelineRepository,
        SessionRecapRepository,
        CharacterTimelineRepository,
    )
    assert Repository.__mro__.count(SQLiteRepository) == 1
    assert Repository.create_campaign is WorldRepository.create_campaign
    assert Repository.append_fact_entry is FactRepository.append_fact_entry
    assert Repository.create_turn_proposal is TurnRepository.create_turn_proposal
    assert Repository.create_map is MapRepository.create_map
    assert Repository.create_context_assembly is ContextAssemblyRepository.create_context_assembly
    assert Repository.create_campaign_session is SecurityRepository.create_campaign_session
    assert Repository.append_realtime_event is RealtimeRepository.append_realtime_event
    assert Repository.create_investigator is InvestigatorRepository.create_investigator
    assert Repository.create_session_seat is SessionSeatRepository.create_session_seat
    assert Repository.create_skill_check is SkillCheckRepository.create_skill_check
    assert (
        Repository.create_npc_hidden_appearance_resolution
        is PrivateRandomResolutionRepository.create_npc_hidden_appearance_resolution
    )
    assert (
        Repository.list_memory_timeline
        is MemoryTimelineRepository.list_memory_timeline
    )
    assert (
        Repository.create_session_recap_run
        is SessionRecapRepository.create_session_recap_run
    )
    assert (
        Repository.get_character_timeline
        is CharacterTimelineRepository.get_character_timeline
    )
    assert Repository.create_rule_source is RulebookRepository.create_rule_source
    assert (
        Repository.save_model_configuration
        is ModelConfigurationRepository.save_model_configuration
    )
    assert (
        Repository.save_image_model_configuration
        is ModelConfigurationRepository.save_image_model_configuration
    )


def test_formal_migration_registry_keeps_ordered_legacy_upgrades() -> None:
    assert LATEST_SCHEMA_VERSION == 41
    assert [(item.version, item.name) for item in MIGRATIONS] == [
        (1, "add_proposed_checks_to_turn_proposals"),
        (2, "add_player_action_idempotency"),
        (3, "add_map_status"),
        (4, "add_map_token_version"),
        (5, "add_proposed_npc_updates_to_turn_proposals"),
        (6, "add_player_owned_investigator_library"),
        (7, "add_rulebook_dual_storage"),
        (8, "add_campaign_investigator_review_and_runtime_state"),
        (9, "add_model_configuration"),
        (10, "add_per_seat_invitations"),
        (11, "add_replayable_skill_checks"),
        (12, "add_versioned_map_specs_and_assets"),
        (13, "backfill_map_revisions_and_guard_pointers"),
        (14, "add_image_model_configuration"),
        (15, "add_memory_fts5_index"),
        (16, "add_module_document_imports"),
        (17, "add_module_knowledge_review"),
        (18, "add_module_entity_graph"),
        (19, "add_module_document_structure"),
        (20, "add_campaign_module_runs"),
        (21, "add_module_run_version"),
        (22, "add_knowledge_extraction_attempts"),
        (23, "enforce_session_assignment_uniqueness"),
        (24, "scope_rule_source_hash_by_ruleset"),
        (25, "add_scene_director_runtime"),
        (26, "add_world_expansion_materializations"),
        (27, "add_investigator_npc_encounters"),
        (28, "add_npc_appearance_gating"),
        (29, "add_campaign_travel_graph"),
        (30, "add_private_random_resolutions"),
        (31, "add_memory_curation_actions"),
        (32, "add_session_recap_reviews"),
        (33, "add_cross_campaign_character_timelines"),
        (34, "add_proposed_world_facts"),
        (35, "add_persisted_opposed_checks"),
        (36, "add_director_control_handoff"),
        (37, "add_player_handouts"),
        (38, "add_map_fog_regions"),
        (39, "add_simulated_campaign_evaluations"),
        (40, "add_opposed_check_reroll_lineage"),
        (41, "add_director_control_event_sequence"),
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

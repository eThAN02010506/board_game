from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.modules.ingestion import ModuleChunk
from ai_kp.platform.scenes.builtin_setting_packs import US_1920S
from ai_kp.platform.scenes.setting_profiles import (
    ConfiguredEntityBinding,
    ConfiguredLocationBinding,
    ConfiguredRegion,
    SettingProfileDocument,
    build_setting_profile_document,
    validate_setting_profile,
)


def _document() -> SettingProfileDocument:
    return build_setting_profile_document(
        US_1920S,
        (
            ConfiguredRegion(
                region_id="essex-county",
                title="埃塞克斯县",
                pattern_id="county_town_network",
                role_counts={"nearby": 2, "rural": 3},
            ),
            ConfiguredRegion(
                region_id="coastal-route",
                title="沿海交通带",
                pattern_id="port_hinterland",
                role_counts={"nearby": 2},
            ),
        ),
    )


def _with_binding(
    document: SettingProfileDocument,
    *,
    entity_id: str,
    slot_id: str | None,
) -> SettingProfileDocument:
    first = document.settlements[0].model_copy(
        update={
            "title": "阿卡姆",
            "location_bindings": (
                ConfiguredLocationBinding(
                    module_entity_id=entity_id,
                    slot_id=slot_id,
                ),
            ),
        }
    )
    return document.model_copy(update={"settlements": (first, *document.settlements[1:])})


def test_profile_builds_every_node_across_multiple_region_patterns() -> None:
    document = _document()

    assert len(document.regions) == 2
    assert {item.region_id for item in document.settlements} == {
        "essex-county",
        "coastal-route",
    }
    assert len(document.settlements) == 12


def test_profile_validates_module_location_and_explicit_function_slot() -> None:
    document = _with_binding(
        _document(),
        entity_id="location-arkham-police",
        slot_id="law_enforcement",
    )
    result = validate_setting_profile(
        US_1920S,
        document,
        (
            {
                "id": "location-arkham-police",
                "entity_type": "location",
                "name": "阿卡姆警察局",
            },
            {
                "id": "location-innsmouth",
                "entity_type": "location",
                "name": "印斯茅斯",
            },
        ),
    )

    assert len(result.region_skeletons) == 2
    assert result.unassigned_location_entity_ids == ("location-innsmouth",)


def test_profile_rejects_missing_nodes_wrong_entity_types_and_unknown_slots() -> None:
    document = _document()
    with pytest.raises(ValueError, match="coverage mismatch"):
        validate_setting_profile(
            US_1920S,
            document.model_copy(update={"settlements": document.settlements[:-1]}),
            (),
        )

    npc_binding = _with_binding(document, entity_id="npc-1", slot_id=None)
    with pytest.raises(ValueError, match="not a location"):
        validate_setting_profile(
            US_1920S,
            npc_binding,
            ({"id": "npc-1", "entity_type": "npc", "name": "治安官"},),
        )

    unknown_slot = _with_binding(
        document,
        entity_id="location-1",
        slot_id="orbital_spaceport",
    )
    with pytest.raises(ValueError, match="unknown scene slot"):
        validate_setting_profile(
            US_1920S,
            unknown_slot,
            ({"id": "location-1", "entity_type": "location", "name": "港口"},),
        )


def test_profile_rejects_one_module_location_assigned_to_two_settlements() -> None:
    document = _document()
    binding = ConfiguredLocationBinding(module_entity_id="location-1")
    settlements = list(document.settlements)
    settlements[0] = settlements[0].model_copy(update={"location_bindings": (binding,)})
    settlements[1] = settlements[1].model_copy(update={"location_bindings": (binding,)})

    with pytest.raises(ValueError, match="only one settlement"):
        SettingProfileDocument(
            regions=document.regions,
            settlements=tuple(settlements),
        )


def test_profile_binds_existing_entities_to_typed_archetypes_and_scene_slots() -> None:
    document = _document()
    settlement_id = document.settlements[0].settlement_id
    document = document.model_copy(
        update={
            "entity_bindings": (
                ConfiguredEntityBinding(
                    module_entity_id="npc-sheriff",
                    archetype_id="public_safety_officer",
                    settlement_id=settlement_id,
                    slot_id="law_enforcement",
                ),
                ConfiguredEntityBinding(
                    module_entity_id="org-county",
                    archetype_id="public_safety_agency",
                ),
            )
        }
    )
    result = validate_setting_profile(
        US_1920S,
        document,
        (
            {"id": "npc-sheriff", "entity_type": "npc", "name": "治安官"},
            {"id": "org-county", "entity_type": "organization", "name": "县警署"},
            {"id": "event-arrival", "entity_type": "event", "name": "午夜列车"},
        ),
    )

    assert result.unassigned_entity_ids == ("event-arrival",)


def test_profile_rejects_incompatible_or_misplaced_entity_archetypes() -> None:
    document = _document()
    settlement_id = document.settlements[0].settlement_id
    incompatible = document.model_copy(
        update={
            "entity_bindings": (
                ConfiguredEntityBinding(
                    module_entity_id="npc-1",
                    archetype_id="public_safety_agency",
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="incompatible"):
        validate_setting_profile(
            US_1920S,
            incompatible,
            ({"id": "npc-1", "entity_type": "npc", "name": "人物"},),
        )

    misplaced = document.model_copy(
        update={
            "entity_bindings": (
                ConfiguredEntityBinding(
                    module_entity_id="npc-1",
                    archetype_id="public_safety_officer",
                    settlement_id=settlement_id,
                    slot_id="worship",
                ),
            )
        }
    )
    with pytest.raises(ValueError, match="does not apply"):
        validate_setting_profile(
            US_1920S,
            misplaced,
            ({"id": "npc-1", "entity_type": "npc", "name": "人物"},),
        )


def test_profile_versions_are_immutable_and_run_selection_is_audited(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "setting-profiles.sqlite3")
    try:
        init_db(connection)
        repo = Repository(connection)
        campaign = repo.create_campaign("Setting Profile 测试")
        module = repo.create_module(
            campaign["id"],
            "多区域模组",
            [
                ModuleChunk(
                    title="正文",
                    text="测试模组正文",
                    visibility="kp",
                    order_index=0,
                )
            ],
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key="opening",
            active_spoiler_tags=[],
            state={},
            started_by_member_id=None,
        )
        original = _document()
        profile = repo.create_module_setting_profile(
            module_id=module["id"],
            title="1920 年代新英格兰",
            setting_pack_id=US_1920S.setting_pack_id,
            setting_pack_version=US_1920S.pack_version,
            document=original.model_dump(mode="json"),
            member_id=None,
        )
        selected_id = original.settlements[0].settlement_id
        first = repo.set_module_run_setting_selection(
            run["id"],
            expected_run_version=run["version"],
            profile_id=profile["id"],
            profile_version=1,
            settlement_id=selected_id,
            reason="开场位于该聚落",
            member_id=None,
        )

        renamed = original.model_copy(
            update={
                "settlements": (
                    original.settlements[0].model_copy(update={"title": "阿卡姆"}),
                    *original.settlements[1:],
                )
            }
        )
        updated = repo.update_module_setting_profile(
            profile["id"],
            expected_version=1,
            title="1920 年代新英格兰",
            setting_pack_version=US_1920S.pack_version,
            document=renamed.model_dump(mode="json"),
            member_id=None,
        )

        pinned = repo.get_module_run_setting_selection(run["id"])
        assert first["run"]["version"] == run["version"] + 1
        assert pinned is not None
        assert pinned["profile_version"] == 1
        assert pinned["profile"]["document"]["settlements"][0]["title"] != "阿卡姆"
        assert updated["current_version"] == 2
        assert updated["document"]["settlements"][0]["title"] == "阿卡姆"

        second = repo.set_module_run_setting_selection(
            run["id"],
            expected_run_version=first["run"]["version"],
            profile_id=profile["id"],
            profile_version=2,
            settlement_id=selected_id,
            reason="采用复核后的聚落名称",
            member_id=None,
        )
        assert second["selection"]["version"] == 2
        assert len(repo.list_module_run_setting_selection_events(run["id"])) == 2

        with pytest.raises(ValueError, match="refresh"):
            repo.set_module_run_setting_selection(
                run["id"],
                expected_run_version=first["run"]["version"],
                profile_id=profile["id"],
                profile_version=2,
                settlement_id=selected_id,
                reason="过期页面",
                member_id=None,
            )
    finally:
        connection.close()


def test_setting_profile_api_is_kp_only_and_selects_a_run_context(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=tmp_path / "setting-profile-api.sqlite3",
        admin_token="setting-profile-admin",
    )
    app = create_app(settings)
    setup = connect(settings.db_path)
    try:
        repo = Repository(setup)
        campaign = repo.create_campaign("Profile API 团")
        session = repo.create_campaign_session(
            campaign["id"], kp_display_name="新手 KP"
        )
        player = repo.join_campaign_session(
            session["join_code"], display_name="玩家"
        )
        module = repo.create_module(
            campaign["id"],
            "Profile API 模组",
            [
                ModuleChunk(
                    title="正文",
                    text="模组正文",
                    visibility="kp",
                    order_index=0,
                )
            ],
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key="opening",
            active_spoiler_tags=[],
            state={},
            started_by_member_id=session["member"]["id"],
        )
        repo.commit()
    finally:
        setup.close()

    kp_headers = {"Authorization": f"Bearer {session['access_token']}"}
    player_headers = {"Authorization": f"Bearer {player['access_token']}"}
    with TestClient(app) as client:
        forbidden = client.get(
            f"/modules/{module['id']}/setting-profiles",
            headers=player_headers,
        )
        created = client.post(
            f"/modules/{module['id']}/setting-profiles",
            headers=kp_headers,
            json={
                "title": "新英格兰区域",
                "setting_pack_id": "us.1920s",
                "regions": [
                    {
                        "region_id": "new-england",
                        "title": "新英格兰",
                        "pattern_id": "metropolitan_satellites",
                        "role_counts": {"nearby": 2, "rural": 2},
                    }
                ],
            },
        )
        assert created.status_code == 200
        profile = created.json()
        settlement_id = profile["document"]["settlements"][0]["settlement_id"]
        selected = client.put(
            f"/module-runs/{run['id']}/setting-selection",
            headers=kp_headers,
            json={
                "expected_run_version": run["version"],
                "profile_id": profile["id"],
                "profile_version": profile["version"],
                "settlement_id": settlement_id,
                "reason": "当前场景位于区域枢纽",
            },
        )
        analysis = client.get(
            f"/setting-profiles/{profile['id']}/settlements/{settlement_id}/analysis",
            params={"version": profile["version"]},
            headers=kp_headers,
        )
        listed = client.get(
            f"/modules/{module['id']}/setting-profiles",
            headers=kp_headers,
        )

    assert forbidden.status_code == 403
    assert selected.status_code == 200
    assert selected.json()["run"]["version"] == run["version"] + 1
    assert analysis.status_code == 200
    assert analysis.json()["write_policy"] == "read_only_kp_review_required"
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [profile["id"]]

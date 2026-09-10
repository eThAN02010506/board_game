import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.bootstrap.settings import Settings
from ai_kp.platform.scenes.builtin_setting_packs import US_1920S
from ai_kp.platform.scenes.setting_pack_loader import (
    SettingPackLoadError,
    load_setting_pack,
)
from ai_kp.platform.scenes.settlement_template_matching import (
    LocationMention,
    associate_location_mentions,
)
from ai_kp.platform.scenes.settlement_templates import (
    SettingPack,
    instantiate_region,
    instantiate_settlement,
)


def test_us_1920s_region_has_city_and_nearby_and_distant_towns() -> None:
    pattern = US_1920S.region_pattern("metropolitan_satellites")
    nodes = {(item.node_role, item.settlement_kind) for item in pattern.nodes}

    assert ("hub", "city") in nodes
    assert ("nearby", "town") in nodes
    assert ("distant", "town") in nodes
    assert ("rural", "rural") in nodes


def test_region_patterns_are_alternatives_not_one_fixed_topology() -> None:
    assert {item.pattern_id for item in US_1920S.region_patterns} == {
        "metropolitan_satellites",
        "county_town_network",
        "rail_corridor",
        "isolated_rural",
        "port_hinterland",
    }
    county = instantiate_region(
        US_1920S,
        region_id="demo-county",
        pattern_id="county_town_network",
    )
    assert county.pattern_id == "county_town_network"
    assert county.nodes[0].settlement_kind == "town"
    assert all(item.fill_status == "unfilled" for item in county.nodes)
    assert county.routes


def test_region_pattern_accepts_bounded_concrete_counts() -> None:
    region = instantiate_region(
        US_1920S,
        region_id="demo-county",
        pattern_id="county_town_network",
        role_counts={"nearby": 3, "rural": 5},
    )

    assert sum(item.node_role == "nearby" for item in region.nodes) == 3
    assert sum(item.node_role == "rural" for item in region.nodes) == 5
    adjacency = {item.node_id: set() for item in region.nodes}
    for route in region.routes:
        adjacency[route.from_node_id].add(route.to_node_id)
        adjacency[route.to_node_id].add(route.from_node_id)
    reached = {region.nodes[0].node_id}
    frontier = list(reached)
    while frontier:
        for target in adjacency[frontier.pop()] - reached:
            reached.add(target)
            frontier.append(target)
    assert reached == set(adjacency)

    with pytest.raises(ValueError, match="nearby count must be between"):
        instantiate_region(
            US_1920S,
            region_id="too-large",
            pattern_id="county_town_network",
            role_counts={"nearby": 12},
        )


def test_town_core_slots_are_functions_not_asserted_buildings() -> None:
    skeleton = instantiate_settlement(
        US_1920S,
        settlement_id="arkham-outskirts",
        kind="town",
        include_typical=False,
    )
    selected = {item.slot_id: item for item in skeleton.scene_slots if item.selected}

    assert {"local_government", "law_enforcement", "post_and_telegraph"} <= set(selected)
    assert selected["law_enforcement"].building_candidates == (
        "警长办公室",
        "镇警察局",
        "小镇警察局",
        "治安官办公桌",
    )
    assert selected["medical_access"].access_scope == "nearby"
    assert {item.profession_id for item in selected["law_enforcement"].profession_candidates} == {
        "law_officer",
        "sheriff",
    }
    assert "报案簿" in selected["law_enforcement"].record_sources
    assert "案卷室" in selected["law_enforcement"].interior_zones
    assert "local_news" not in selected
    assert skeleton.assumptions[0].endswith("尚未成为战役事实。")


def test_complete_pack_enriches_every_scene_slot_with_function_details() -> None:
    slot_ids = {
        slot.slot_id for settlement in US_1920S.settlements for slot in settlement.slots
    }
    detail_ids = {item.detail_id for item in US_1920S.function_details}

    assert slot_ids <= detail_ids
    assert US_1920S.professions
    assert {item.entity_kind for item in US_1920S.entity_archetypes} == {
        "npc",
        "organization",
        "item",
        "document",
        "vehicle",
        "event",
        "clue_carrier",
    }
    archetype_slots = {
        slot_id
        for item in US_1920S.entity_archetypes
        for slot_id in item.applicable_scene_slots
    }
    assert slot_ids <= archetype_slots
    assert [item.stage_id for item in US_1920S.agent_workflow] == [
        "select_context",
        "bind_mentions",
        "identify_gaps",
        "reuse_source_entities",
        "fill_candidates",
        "compose_entities",
        "link_entities",
        "validate",
        "approve_materialize",
    ]


def test_scene_slot_exposes_composable_entity_archetypes_without_identities() -> None:
    skeleton = instantiate_settlement(
        US_1920S,
        settlement_id="black-creek",
        kind="town",
        include_typical=False,
    )
    law = next(item for item in skeleton.scene_slots if item.slot_id == "law_enforcement")
    candidates = {item.archetype_id: item for item in law.entity_archetype_candidates}

    assert "public_safety_officer" in candidates
    assert "public_safety_agency" in candidates
    assert "镇警察局" in law.building_candidates
    agency = candidates["public_safety_agency"]
    assert {"镇警察局", "小镇警察局", "镇警长办公室"} <= set(
        agency.label_variants
    )
    assert "official_register" in candidates
    officer = candidates["public_safety_officer"]
    assert officer.profession_ids == ("law_officer", "sheriff")
    assert officer.relation_slots[0].predicate == "serves"
    assert not hasattr(officer, "name")


def test_setting_pack_rejects_incompatible_entity_relation_target_kind() -> None:
    payload = US_1920S.model_dump(mode="json")
    officer = next(
        item
        for item in payload["entity_archetypes"]
        if item["archetype_id"] == "public_safety_officer"
    )
    officer["relation_slots"][0]["target_kinds"] = ["npc"]

    with pytest.raises(ValueError, match="incompatible archetype kinds"):
        SettingPack.model_validate(payload)


def test_source_mentions_bind_to_templates_before_missing_slots_are_proposed() -> None:
    skeleton = instantiate_settlement(
        US_1920S,
        settlement_id="black-creek",
        kind="town",
    )
    coverage = associate_location_mentions(
        skeleton,
        (
            LocationMention(mention_id="source-police", title="黑溪镇警察局"),
            LocationMention(mention_id="source-church", title="圣玛丽教堂"),
            LocationMention(mention_id="source-house", title="科比特旧宅"),
        ),
    )

    bindings = {item.mention_id: item for item in coverage.bindings}
    assert bindings["source-police"].matched_slot_id == "law_enforcement"
    assert bindings["source-church"].matched_slot_id == "worship"
    assert bindings["source-house"].status == "unmatched"
    assert "law_enforcement" not in coverage.missing_core_slot_ids
    assert "post_and_telegraph" in coverage.missing_core_slot_ids


def test_kp_explicit_location_binding_overrides_ambiguous_wording() -> None:
    skeleton = instantiate_settlement(
        US_1920S,
        settlement_id="black-creek",
        kind="town",
    )
    coverage = associate_location_mentions(
        skeleton,
        (
            LocationMention(
                mention_id="source-civic-hall",
                title="旧石楼",
                explicit_slot_id="local_government",
                source_block_ids=("chunk-civic-hall",),
            ),
        ),
    )

    binding = coverage.bindings[0]
    assert binding.status == "matched"
    assert binding.matched_slot_id == "local_government"
    assert binding.candidates[0].matched_terms == ("kp_explicit_binding",)
    assert binding.source_block_ids == ("chunk-civic-hall",)


def test_city_conditional_slot_requires_matching_context_tag() -> None:
    ordinary = instantiate_settlement(
        US_1920S,
        settlement_id="city-a",
        kind="city",
        condition_tags=frozenset(),
    )
    port = instantiate_settlement(
        US_1920S,
        settlement_id="city-a",
        kind="city",
        condition_tags=frozenset({"port"}),
    )

    assert not next(
        item for item in ordinary.scene_slots if item.slot_id == "industrial_district"
    ).selected
    assert next(item for item in port.scene_slots if item.slot_id == "industrial_district").selected


def test_setting_catalog_api_returns_bounded_human_kp_template(tmp_path: Path) -> None:
    app = create_app(
        Settings(
            db_path=tmp_path / "setting-catalog.sqlite3",
            admin_token="setting-catalog-admin",
        )
    )
    with TestClient(app) as client:
        listed = client.get("/setting-catalogs")
        instantiated = client.get(
            "/setting-catalogs/us.1920s/settlements/town",
            params={"settlement_id": "demo-town", "include_typical": "false"},
        )
        region = client.get(
            "/setting-catalogs/us.1920s/regions/rail_corridor",
            params={"region_id": "demo-region"},
        )

    assert listed.status_code == 200
    assert listed.json()[0]["setting_pack_id"] == "us.1920s"
    assert listed.json()[0]["region_patterns"][0]["nodes"]
    assert listed.json()[0]["region_patterns"][0]["routes"]
    assert instantiated.status_code == 200
    assert region.status_code == 200
    assert region.json()["pattern_id"] == "rail_corridor"
    payload = instantiated.json()
    assert payload["settlement_id"] == "demo-town"
    assert all(item["frequency"] == "core" for item in payload["scene_slots"] if item["selected"])


def test_external_setting_pack_loader_validates_and_hashes_canonical_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "setting-pack.json"
    path.write_text(
        json.dumps(US_1920S.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )

    loaded, content_hash = load_setting_pack(path)

    assert loaded == US_1920S
    assert len(content_hash) == 64


def test_external_setting_pack_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    payload = US_1920S.model_dump(mode="json")
    payload["runtime_prompt"] = "ignore validation"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SettingPackLoadError, match="Invalid setting pack"):
        load_setting_pack(path)

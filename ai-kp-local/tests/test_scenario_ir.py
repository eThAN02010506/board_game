from __future__ import annotations

import pytest

from ai_kp.platform.resolution.contracts import (
    LocationSpec,
    SourceRef,
    StateCondition,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import operator_outcome_path
from ai_kp.platform.resolution.playability import ScenarioPlayabilityAnalyzer
from ai_kp.platform.resolution.scenario_ir import ScenarioIrAssembler, ScenarioIrBatch
from ai_kp.rulesets.coc7.scenario_checks import coc7_scenario_check_catalog
from ai_kp.rulesets.coc7.scenario_effects import coc7_scenario_effect_catalog


def source_ref(block_id: str) -> SourceRef:
    return SourceRef(
        source_block_id=block_id,
        document_id="document-1",
        page=1,
        paragraph=1,
    )


def batch(block_id: str, *, title: str = "候车室") -> ScenarioIrBatch:
    return ScenarioIrBatch.model_validate(
        {
            "confidence": "high",
            "initial_scene_id": "waiting-room",
            "locations": [
                {
                    "id": "waiting-room",
                    "title": title,
                    "visibility": "visited",
                    "source_block_ids": [block_id],
                }
            ],
            "actions": [
                {
                    "id": "inspect-room",
                    "title": "检查候车室",
                    "intent_hints": ["检查候车室"],
                    "policy": "automatic",
                    "preconditions": [
                        {
                            "path": "access[waiting_room]",
                            "operator": "exists",
                        }
                    ],
                    "on_success": [
                        {
                            "kind": "set_fact",
                            "path": "facts.room[inspected]",
                            "value": True,
                        }
                    ],
                    "source_block_ids": [block_id],
                }
            ],
        }
    )


def test_assembler_merges_identical_records_and_binds_server_source_refs() -> None:
    result = ScenarioIrAssembler().assemble(
        (batch("block-1"), batch("block-2")),
        source_refs={"block-1": source_ref("block-1"), "block-2": source_ref("block-2")},
        contract_id="server-contract",
        source_version=4,
        ruleset_id="coc7",
        title="真实模组名",
        corpus_truncated=False,
    )

    assert result.assumptions == ()
    assert result.contract.contract_id == "server-contract"
    assert result.contract.source_version == 4
    assert len(result.contract.locations) == 1
    assert {ref.source_block_id for ref in result.contract.locations[0].source_refs} == {
        "block-1",
        "block-2",
    }
    operator = result.contract.operators[0]
    assert operator.preconditions[0].path == "facts.access.waiting_room"
    assert operator.success_commands[0].path == "room.inspected"


def test_assembler_normalizes_duplicate_intent_hints_before_strict_contract() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["actions"][0]["intent_hints"] = ["查看记录", "查看记录", "翻阅账簿"]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="模组",
        corpus_truncated=False,
    )

    assert result.contract.operators[0].intent_hints == ("查看记录", "翻阅账簿")


@pytest.mark.parametrize(
    ("location_id", "location_title", "action_title"),
    (
        ("central_library", "中央图书馆", "查阅馆藏资料"),
        ("newspaper_office", "波士顿环球报社", "查阅旧报纸"),
        ("chapel", "沉思礼拜堂", "检查礼拜堂痕迹"),
        ("guest_room", "客房", "检查床架"),
    ),
)
def test_evidence_backed_action_location_is_server_bound_to_scene(
    location_id: str, location_title: str, action_title: str
) -> None:
    payload = {
        "locations": [{
            "id": location_id,
            "title": location_title,
            "source_block_ids": ["place-and-action"],
        }],
        "actions": [{
            "id": f"act_{location_id}",
            "title": action_title,
            "policy": "automatic",
            "location_slot": 0,
            "on_success": [{
                "kind": "set_fact",
                "path": f"facts.actions.{location_id}",
                "value": True,
            }],
            "source_block_ids": ["place-and-action"],
        }],
    }

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(payload),),
        source_refs={"place-and-action": source_ref("place-and-action")},
        contract_id=f"scoped-{location_id}",
        source_version=1,
        ruleset_id="coc7",
        title="Scoped scenario",
        corpus_truncated=False,
        source_texts={
            "place-and-action": f"调查员在{location_title}{action_title}。"
        },
        source_titles={"place-and-action": location_title},
    )

    operator = next(
        item for item in result.contract.operators if item.operator_id == f"act_{location_id}"
    )
    assert operator.preconditions[0].path == "scene_id"
    assert operator.preconditions[0].operator == "eq"
    assert operator.preconditions[0].value == location_id


@pytest.mark.parametrize(
    ("scope_update", "source_text", "diagnostic"),
    (
        ({}, "调查员查阅资料。", "without a location scope"),
        (
            {"location_id": "invented_archive"},
            "调查员在中央图书馆查阅资料。",
            "unknown location id",
        ),
        (
            {"location_id": "central_library"},
            "调查员查阅资料，没有说明所在地。",
            "not anchored by its cited source",
        ),
        (
            {"global_action": True},
            "调查员在中央图书馆查阅资料。",
            "unsupported global action",
        ),
    ),
)
def test_evidence_backed_action_scope_fails_closed(
    scope_update: dict[str, object], source_text: str, diagnostic: str
) -> None:
    payload = {
        "locations": [{
            "id": "central_library",
            "title": "中央图书馆",
            "source_block_ids": ["block-1"],
        }],
        "actions": [{
            "id": "research",
            "title": "查阅资料",
            "policy": "automatic",
            "on_success": [{
                "kind": "set_fact", "path": "facts.researched", "value": True,
            }],
            "source_block_ids": ["block-1"],
            **scope_update,
        }],
    }

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(payload),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="fail-closed-scope",
        source_version=1,
        ruleset_id="coc7",
        title="Scope rejection",
        corpus_truncated=False,
        source_texts={"block-1": source_text},
        allow_empty_actions=True,
    )

    assert result.contract.operators == ()
    assert any(diagnostic in assumption for assumption in result.assumptions)


def test_action_scope_rejects_ambiguous_selectors_and_accepts_proven_global() -> None:
    with pytest.raises(ValueError, match="must select one"):
        ScenarioIrBatch.model_validate({
            "actions": [{
                "id": "ambiguous",
                "title": "Ambiguous",
                "policy": "automatic",
                "location_slot": 0,
                "location_id": "library",
                "source_block_ids": ["block-1"],
            }],
        })

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate({
            "actions": [{
                "id": "listen_for_alarm",
                "title": "Listen for the alarm",
                "policy": "automatic",
                "global_action": True,
                "on_success": [{
                    "kind": "set_fact", "path": "facts.alarm_heard", "value": True,
                }],
                "source_block_ids": ["block-1"],
            }],
        }),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="proven-global",
        source_version=1,
        ruleset_id="coc7",
        title="Global action",
        corpus_truncated=False,
        source_texts={
            "block-1": "Investigators may listen for the alarm from any location."
        },
    )

    assert result.contract.operators[0].preconditions == ()


def test_action_location_may_be_anchored_by_exact_source_title_only() -> None:
    payload = {
        "locations": [{
            "id": "central_library",
            "title": "Central Library",
            "source_block_ids": ["block-1"],
        }],
        "actions": [{
            "id": "research",
            "title": "Research the records",
            "policy": "automatic",
            "location_id": "central_library",
            "on_success": [{
                "kind": "set_fact", "path": "facts.researched", "value": True,
            }],
            "source_block_ids": ["block-1"],
        }],
    }

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(payload),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="source-title-scope",
        source_version=1,
        ruleset_id="coc7",
        title="Source title scope",
        corpus_truncated=False,
        source_texts={"block-1": "Investigators may research the records here."},
        source_titles={"block-1": "Central Library"},
    )

    assert result.contract.operators[0].preconditions[0].value == "central_library"


def test_action_location_does_not_accept_inexact_source_title_alias() -> None:
    payload = {
        "locations": [{
            "id": "boston_globe_archive",
            "title": "Boston Globe Archive",
            "source_block_ids": ["block-1"],
        }],
        "actions": [{
            "id": "newspaper_search",
            "title": "Search old newspapers",
            "policy": "automatic",
            "location_id": "boston_globe_archive",
            "on_success": [{
                "kind": "set_fact", "path": "facts.paper_found", "value": True,
            }],
            "source_block_ids": ["block-1"],
        }],
    }

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(payload),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="source-title-alias-rejected",
        source_version=1,
        ruleset_id="coc7",
        title="Source title alias",
        corpus_truncated=False,
        source_texts={"block-1": "Search the newspapers."},
        source_titles={"block-1": "The Globe"},
        allow_empty_actions=True,
    )

    assert result.contract.operators == ()
    assert any(
        "not anchored by its cited source" in assumption
        for assumption in result.assumptions
    )


def test_action_location_uses_canonical_section_ancestry_exact_alias() -> None:
    payload = {
        "locations": [{
            "id": "room_2",
            "title": "2号房间：儿童房",
            "source_block_ids": ["block-1"],
        }],
        "actions": [{
            "id": "inspect_bed",
            "title": "检查床架",
            "policy": "automatic",
            "location_id": "room_2",
            "on_success": [{
                "kind": "set_fact", "path": "facts.bed_checked", "value": True,
            }],
            "source_block_ids": ["block-1"],
        }],
    }

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(payload),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="section-ancestry-scope",
        source_version=1,
        ruleset_id="coc7",
        title="Section ancestry",
        corpus_truncated=False,
        source_texts={"block-1": "检查床架。"},
        source_titles={"block-1": "床架攻击"},
        source_section_paths={
            "block-1": ("第二章", "2 号房间: 儿童房", "床架攻击")
        },
    )

    assert result.contract.operators[0].preconditions[0].value == "room_2"


def test_action_location_scene_key_disambiguates_duplicate_titles() -> None:
    action = ScenarioIrBatch.model_validate({
        "actions": [{
            "id": "inspect_room",
            "title": "Inspect room",
            "policy": "automatic",
            "location_id": "guest_room_b",
            "on_success": [{
                "kind": "set_fact", "path": "facts.room_checked", "value": True,
            }],
            "source_block_ids": ["block-1"],
        }],
    })
    result = ScenarioIrAssembler().assemble(
        (action,),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="scene-key-scope",
        source_version=1,
        ruleset_id="coc7",
        title="Scene key scope",
        corpus_truncated=False,
        source_texts={"block-1": "Inspect the room."},
        source_titles={"block-1": "Guest Room"},
        source_scene_keys={"block-1": "guest_room_b"},
        external_action_locations={
            "guest_room_a": "Guest Room",
            "guest_room_b": "Guest Room",
        },
        allow_empty_actions=True,
    )

    assert result.contract.operators[0].preconditions[0].value == "guest_room_b"


def test_action_location_exact_room_ancestry_precedes_parent_scene_key() -> None:
    payload = {
        "locations": [
            {"id": "house", "title": "科比特的老房子", "source_block_ids": ["house"]},
            {"id": "empty_room", "title": "3 号房间: 空卧室", "source_block_ids": ["block-1"]},
        ],
        "actions": [{
            "id": "inspect_bed",
            "title": "检查床架",
            "policy": "automatic",
            "location_id": "empty_room",
            "on_success": [{
                "kind": "set_fact", "path": "facts.bed_checked", "value": True,
            }],
            "source_block_ids": ["block-1"],
        }],
    }

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(payload),),
        source_refs={
            "house": source_ref("house"),
            "block-1": source_ref("block-1"),
        },
        contract_id="nested-scene-key-scope",
        source_version=1,
        ruleset_id="coc7",
        title="Nested scene key scope",
        corpus_truncated=False,
        source_texts={"house": "老房子。", "block-1": "检查床架。"},
        source_titles={"house": "科比特的老房子", "block-1": "床架攻击"},
        source_section_paths={
            "house": ("场景 9: 科比特的老房子",),
            "block-1": ("场景 9: 科比特的老房子", "二楼", "3 号房间: 空卧室", "床架攻击"),
        },
        source_scene_keys={
            "house": "科比特的老房子",
            "block-1": "科比特的老房子",
        },
    )

    operator = next(item for item in result.contract.operators if item.operator_id == "inspect_bed")
    assert operator.preconditions[0].value == "empty_room"


def test_coverage_action_infers_one_external_location_without_expanding_world() -> None:
    action = ScenarioIrBatch.model_validate({
        "actions": [{
            "id": "coverage_research",
            "title": "Research the catalogue",
            "policy": "automatic",
            "on_success": [{
                "kind": "set_fact", "path": "facts.catalogue_read", "value": True,
            }],
            "source_block_ids": ["block-1"],
        }],
    })

    result = ScenarioIrAssembler().assemble(
        (action,),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="external-location-scope",
        source_version=1,
        ruleset_id="coc7",
        title="External location scope",
        corpus_truncated=False,
        source_texts={"block-1": "Research the catalogue here."},
        source_titles={"block-1": "Central Library"},
        external_action_locations={
            "central_library": "Central Library",
            "newspaper_archive": "Newspaper Archive",
        },
        infer_action_location_ids=("coverage_research",),
    )

    assert result.contract.locations == ()
    assert result.contract.operators[0].preconditions == (
        StateCondition(path="scene_id", operator="eq", value="central_library"),
    )


def test_initial_action_infers_one_exact_source_location() -> None:
    action = ScenarioIrBatch.model_validate({
        "locations": [{
            "id": "central_library",
            "title": "Central Library",
            "source_block_ids": ["library"],
        }],
        "actions": [{
            "id": "research_catalogue",
            "title": "Research the catalogue",
            "policy": "automatic",
            "on_success": [{
                "kind": "set_fact", "path": "facts.catalogue_read", "value": True,
            }],
            "source_block_ids": ["catalogue"],
        }],
    })

    result = ScenarioIrAssembler().assemble(
        (action,),
        source_refs={
            "library": source_ref("library"),
            "catalogue": source_ref("catalogue"),
        },
        contract_id="initial-location-scope",
        source_version=1,
        ruleset_id="coc7",
        title="Initial location scope",
        corpus_truncated=False,
        source_texts={
            "library": "The library is open.",
            "catalogue": "Investigators may research the catalogue here.",
        },
        source_titles={
            "library": "Central Library",
            "catalogue": "Catalogue",
        },
        source_section_paths={
            "library": ("Central Library",),
            "catalogue": ("Central Library", "Catalogue"),
        },
    )

    assert result.contract.operators[0].preconditions == (
        StateCondition(path="scene_id", operator="eq", value="central_library"),
    )


def test_coverage_action_inherits_only_a_proven_opening_scene() -> None:
    action = ScenarioIrBatch.model_validate({
        "actions": [{
            "id": "coverage_opening_sanity",
            "title": "Make the opening SAN check",
            "policy": "automatic",
            "always": [{
                "kind": "set_fact", "path": "facts.opening_seen", "value": True,
            }],
            "source_block_ids": ["opening"],
        }],
    })

    result = ScenarioIrAssembler().assemble(
        (action,),
        source_refs={"opening": source_ref("opening")},
        contract_id="opening-action-scope",
        source_version=1,
        ruleset_id="coc7",
        title="Opening action scope",
        corpus_truncated=False,
        source_texts={"opening": "Ask the players to make a Sanity roll."},
        source_titles={"opening": "Opening Scene"},
        source_section_paths={"opening": ("Introduction", "Opening Scene")},
        external_action_locations={
            "street": "Street",
            "gardiner_room": "Gardiner's Room",
        },
        external_locations=(
            LocationSpec(location_id="street", title="Street"),
            LocationSpec(location_id="gardiner_room", title="Gardiner's Room"),
        ),
        external_initial_scene_id="gardiner_room",
        infer_action_location_ids=("coverage_opening_sanity",),
    )

    assert result.contract.operators[0].preconditions == (
        StateCondition(path="scene_id", operator="eq", value="gardiner_room"),
    )


def test_coverage_action_does_not_inherit_entry_from_a_background_section() -> None:
    action = ScenarioIrBatch.model_validate({
        "actions": [{
            "id": "coverage_background_sanity",
            "title": "Make the historical SAN check",
            "policy": "automatic",
            "always": [{
                "kind": "set_fact", "path": "facts.history_seen", "value": True,
            }],
            "source_block_ids": ["background"],
        }],
    })

    result = ScenarioIrAssembler().assemble(
        (action,),
        source_refs={"background": source_ref("background")},
        contract_id="background-action-scope",
        source_version=1,
        ruleset_id="coc7",
        title="Background action scope",
        corpus_truncated=False,
        source_texts={"background": "The old fire caused 1/1D4 Sanity loss."},
        source_titles={"background": "Background"},
        source_section_paths={"background": ("Introduction", "Background")},
        external_action_locations={
            "street": "Street",
            "gardiner_room": "Gardiner's Room",
        },
        external_locations=(
            LocationSpec(location_id="street", title="Street"),
            LocationSpec(location_id="gardiner_room", title="Gardiner's Room"),
        ),
        external_initial_scene_id="gardiner_room",
        infer_action_location_ids=("coverage_background_sanity",),
        allow_empty_actions=True,
    )

    assert result.contract.operators == ()
    assert any(
        "without one uniquely source-anchored external location" in assumption
        for assumption in result.assumptions
    )


@pytest.mark.parametrize(
    ("source_title", "catalog"),
    (
        ("Records Office", {"library": "Central Library"}),
        (
            "Central Library",
            {
                "central_library_a": "Central Library",
                "central_library_b": "Central Library",
            },
        ),
    ),
)
def test_coverage_action_external_location_inference_fails_closed_unless_unique(
    source_title: str, catalog: dict[str, str]
) -> None:
    action = ScenarioIrBatch.model_validate({
        "actions": [{
            "id": "coverage_research",
            "title": "Research records",
            "policy": "automatic",
            "on_success": [{
                "kind": "set_fact", "path": "facts.researched", "value": True,
            }],
            "source_block_ids": ["block-1"],
        }],
    })
    result = ScenarioIrAssembler().assemble(
        (action,),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="external-location-rejected",
        source_version=1,
        ruleset_id="coc7",
        title="External location rejection",
        corpus_truncated=False,
        source_texts={"block-1": "Research records here."},
        source_titles={"block-1": source_title},
        external_action_locations=catalog,
        infer_action_location_ids=("coverage_research",),
        allow_empty_actions=True,
    )

    assert result.contract.operators == ()
    assert any(
        "without one uniquely source-anchored external location" in assumption
        for assumption in result.assumptions
    )


def test_assembler_canonicalizes_declared_location_and_clock_condition_aliases() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["locations"] = (*value["locations"], {
        "id": "lamp_room",
        "title": "Lamp room",
        "source_block_ids": ["block-1"],
    })
    value["location_links"] = [{
        "from_id": "waiting-room",
        "to_id": "lamp_room",
        "source_block_ids": ["block-1"],
    }]
    value["clocks"] = [{
        "id": "time_until_dawn",
        "title": "Deadline",
        "initial": 0,
        "maximum": 4,
        "source_block_ids": ["block-1"],
    }]
    wait_action = value["actions"][0]
    wait_action["preconditions"] = ()
    wait_action["on_success"] = (*wait_action["on_success"], {
        "kind": "advance_clock",
        "clock_id": "time_until_dawn",
        "delta": 1,
    })
    repair_action = {
        **wait_action,
        "id": "repair-lamp",
        "title": "Repair lamp",
        "preconditions": [{
            "path": "entities.investigator.location",
            "operator": "eq",
            "value": "lamp_room",
        }, {
            "path": "facts.clock.time_until_dawn",
            "operator": "gte",
            "value": 1,
        }],
        "on_success": ({
            "kind": "set_fact",
            "path": "lighthouse_restored",
            "value": True,
        },),
    }
    value["actions"] = (wait_action, repair_action)
    value["endings"] = [{
        "id": "good-ending",
        "title": "The lamp is repaired before dawn",
        "all_conditions": [
            {
                "path": operator_outcome_path("repair-lamp"),
                "operator": "eq",
                "value": "success",
            },
            {
                "path": "clocks.time_until_dawn.current",
                "operator": "gte",
                "value": 1,
            },
        ],
        "source_block_ids": ["block-1"],
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="Generic timed scenario",
        corpus_truncated=False,
    )

    repair = next(
        item for item in result.contract.operators if item.operator_id == "repair-lamp"
    )
    travel = next(
        item
        for item in result.contract.operators
        if item.title == "候车室 → Lamp room"
    )
    assert travel.source_refs == (source_ref("block-1"),)
    assert travel.success_commands[0] == WorldCommand(
        kind="set_scene", value="lamp_room"
    )
    assert repair.preconditions[0].path == "scene_id"
    assert repair.preconditions[1].path == "clocks.time_until_dawn"
    assert result.contract.endings[0].all_conditions[1].path == (
        "clocks.time_until_dawn"
    )
    assert (
        ScenarioPlayabilityAnalyzer()
        .analyze(result.contract)
        .proof("ending_reachability")
        .status
        == "passed"
    )


def test_assembler_routes_semantic_reactions_only_through_trigger_rules() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["entities"] = ({
        "id": "witness",
        "type": "npc",
        "title": "Witness",
        "source_block_ids": ["block-1"],
    },)
    value["reactive_policies"] = ({
        "id": "witness-rules",
        "entity_id": "witness",
        "source_block_ids": ["block-1"],
        "rules": ({
            "rule_id": "answer-contact",
            "trigger": "semantic_event",
            "event_type": "entity_contacted",
            "commands": ({
                "kind": "set_fact", "path": "witness.answered", "value": True,
            },),
            "rationale": "Answer after contact.",
        }, {
            "rule_id": "keep-waiting",
            "trigger": "background_tick",
            "commands": ({
                "kind": "emit_event", "event_type": "witness_waits",
            },),
            "rationale": "Wait in the background.",
        }),
    },)

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="semantic-trigger-routing",
        source_version=1,
        ruleset_id="coc7",
        title="Semantic trigger routing",
        corpus_truncated=False,
    )

    assert [item.trigger_id for item in result.contract.trigger_rules] == [
        "witness-rules.answer-contact"
    ]
    assert [
        rule.rule_id
        for policy in result.contract.reactive_policies
        for rule in policy.rules
    ] == ["keep-waiting"]


def test_assembler_canonicalizes_player_current_scene_aliases_end_to_end() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["locations"] = (*value["locations"], {
        "id": "archive",
        "title": "Archive",
        "source_block_ids": ["block-1"],
    })
    value["location_links"] = [{
        "from_id": "waiting-room",
        "to_id": "archive",
        "source_block_ids": ["block-1"],
    }]
    action = value["actions"][0]
    action["preconditions"] = [
        {"path": alias, "operator": "eq", "value": "archive"}
        for alias in (
            "facts.current_location",
            "facts.current_location_id",
            "facts.current_scene",
            "facts.current_scene_id",
        )
    ]
    value["endings"] = [{
        "id": "archive-found",
        "title": "The archive search is complete",
        "all_conditions": [{
            "path": operator_outcome_path("inspect-room"),
            "operator": "eq",
            "value": "success",
        }, {
            "path": "facts.room.inspected",
            "operator": "eq",
            "value": True,
        }],
        "source_block_ids": ["block-1"],
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="Generic location-gated scenario",
        corpus_truncated=False,
    )

    assert {item.path for item in result.contract.operators[0].preconditions} == {
        "scene_id"
    }
    assert (
        ScenarioPlayabilityAnalyzer()
        .analyze(result.contract)
        .proof("ending_reachability")
        .status
        == "passed"
    )


@pytest.mark.parametrize("alias", ["facts.location", "facts.current_location"])
def test_assembler_does_not_rewrite_location_alias_with_unknown_value(
    alias: str,
) -> None:
    value = batch("block-1").model_dump(mode="python")
    value["actions"][0]["preconditions"] = [{
        "path": alias,
        "operator": "eq",
        "value": "invented-location",
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="Generic scenario",
        corpus_truncated=False,
    )

    assert result.contract.operators[0].preconditions[0].path == alias


def test_assembler_does_not_project_declared_entity_location_onto_player_scene() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["entities"] = [{
        "id": "guard",
        "type": "npc",
        "title": "Guard",
        "location_id": "waiting-room",
        "source_block_ids": ["block-1"],
    }]
    value["actions"][0]["preconditions"] = [{
        "path": "entities.guard.location",
        "operator": "eq",
        "value": "waiting-room",
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="Generic scenario",
        corpus_truncated=False,
    )

    assert result.contract.operators[0].preconditions[0].path == (
        "entities.guard.location"
    )


def test_assembler_keeps_only_narrative_cues_supported_by_cited_source() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["actions"][0]["location_id"] = "waiting-room"
    value["actions"][0]["public_setup"] = "The action enters resolution."
    value["actions"][0]["narrative_cues"] = [
        {
            "outcome_key": "success",
            "public_summary": "成功：在夹层中发现航海日志",
        },
        {
            "outcome_key": "failure",
            "public_summary": "失败：凭空出现了一把钥匙",
        },
    ]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        source_texts={
            "block-1": "候车室中进行检定。成功：在夹层中发现航海日志。失败时今日不能重试。"
        },
        source_titles={"block-1": "候车室"},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="模组",
        corpus_truncated=False,
    )

    assert [
        cue.public_summary for cue in result.contract.operators[0].narrative_cues
    ] == ["成功：在夹层中发现航海日志"]
    assert any(
        "Source-unsupported narrative cue was discarded" in assumption
        for assumption in result.assumptions
    )


def test_assembler_discards_noncanonical_narrative_outcome_keys() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["actions"][0]["location_id"] = "waiting-room"
    value["actions"][0]["public_setup"] = "The floor gives way."
    value["actions"][0]["narrative_cues"] = [{
        "outcome_key": "fall_into_basement_success",
        "public_summary": "The investigator falls into the basement.",
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        source_texts={
            "block-1": "In 候车室, the investigator falls into the basement."
        },
        source_titles={"block-1": "候车室"},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="Generic scenario",
        corpus_truncated=False,
    )

    assert result.contract.operators[0].narrative_cues == ()
    assert any(
        "unsupported outcome was discarded" in assumption
        for assumption in result.assumptions
    )


def test_assembler_discards_source_text_that_claims_success_on_failure() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["actions"][0]["location_id"] = "waiting-room"
    value["actions"][0]["public_setup"] = "The action enters resolution."
    value["actions"][0]["narrative_cues"] = [{
        "outcome_key": "failure",
        "public_summary": "失败时仍然成功说服了守卫",
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        source_texts={"block-1": "候车室中，失败时仍然成功说服了守卫。"},
        source_titles={"block-1": "候车室"},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="模组",
        corpus_truncated=False,
    )

    assert result.contract.operators[0].narrative_cues == ()
    assert any(
        "Success-claiming failure narrative cue was discarded" in assumption
        for assumption in result.assumptions
    )


def test_supplement_ending_may_reference_server_advertised_base_state() -> None:
    supplement = ScenarioIrBatch.model_validate({
        "confidence": "high",
        "endings": [{
            "id": "before-deadline",
            "title": "在截止前完成",
            "all_conditions": [{
                "path": "clocks.deadline", "operator": "lt", "value": 4,
            }, {
                "path": "facts.case.resolved", "operator": "eq", "value": True,
            }],
            "source_block_ids": ["block-1"],
        }],
    })

    result = ScenarioIrAssembler().assemble(
        (supplement,),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="supplement-contract",
        source_version=1,
        ruleset_id="coc7",
        title="补充结局",
        corpus_truncated=False,
        external_produced_paths=("clocks.deadline", "facts.case.resolved"),
        allow_empty_actions=True,
    )

    assert result.contract.endings[0].ending_id == "before-deadline"
    assert result.assumptions == ()


def test_ir_rejects_empty_response_obligation_before_cross_partition_assembly() -> None:
    payload = batch("block-1").model_dump(mode="json")
    payload["entities"] = [{
        "id": "witness",
        "type": "npc",
        "title": "Witness",
        "source_block_ids": ["block-1"],
        "response_obligations": [{
            "id": "empty-response",
            "trigger_topics": ["询问经过"],
            "boundaries": ["不要泄露秘密"],
        }],
    }]

    with pytest.raises(ValueError, match="observable content"):
        ScenarioIrBatch.model_validate(payload)


def test_assembler_removes_dangling_optional_response_obligation_reference() -> None:
    payload = batch("block-1").model_dump(mode="json")
    payload["actions"][0]["response_obligation_ids"] = ["missing-response"]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(payload),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="通用悬空引用测试",
        corpus_truncated=False,
    )

    assert result.contract.operators[0].response_obligation_ids == ()
    assert any(
        "Unknown optional response obligations were removed" in assumption
        for assumption in result.assumptions
    )


def test_assembler_preserves_conflicting_cross_partition_response_obligations() -> None:
    first_payload = batch("block-1").model_dump(mode="json")
    first_payload["entities"] = [{
        "id": "first-witness",
        "type": "npc",
        "title": "First witness",
        "source_block_ids": ["block-1"],
        "response_obligations": [{
            "id": "answer-question",
            "trigger_topics": ["ask"],
            "facts_to_convey": ["The first witness saw a red car."],
        }],
    }]
    first_payload["actions"][0]["response_obligation_ids"] = ["answer-question"]
    second_payload = batch("block-2").model_dump(mode="json")
    second_payload["entities"] = [{
        "id": "second-witness",
        "type": "npc",
        "title": "Second witness",
        "source_block_ids": ["block-2"],
        "response_obligations": [{
            "id": "answer-question",
            "trigger_topics": ["ask"],
            "facts_to_convey": ["The second witness heard a bell."],
        }],
    }]
    second_payload["actions"][0]["id"] = "inspect-second-witness"
    second_payload["actions"][0]["response_obligation_ids"] = ["answer-question"]

    result = ScenarioIrAssembler().assemble(
        (
            ScenarioIrBatch.model_validate(first_payload),
            ScenarioIrBatch.model_validate(second_payload),
        ),
        source_refs={
            "block-1": source_ref("block-1"),
            "block-2": source_ref("block-2"),
        },
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="Cross-partition obligation collision",
        corpus_truncated=False,
    )

    obligations = result.contract.response_obligations
    assert len(obligations) == 2
    assert len({item.obligation_id for item in obligations}) == 2
    assert {item.facts_to_convey[0] for item in obligations} == {
        "The first witness saw a red car.",
        "The second witness heard a bell.",
    }
    obligation_by_entity = {item.entity_id: item.obligation_id for item in obligations}
    operator_refs = {
        item.operator_id: item.response_obligation_ids
        for item in result.contract.operators
    }
    assert operator_refs["inspect-room"] == (
        obligation_by_entity["first-witness"],
    )
    assert operator_refs["inspect-second-witness"] == (
        obligation_by_entity["second-witness"],
    )
    assert any(
        "response obligation ID was safely renamed" in assumption
        for assumption in result.assumptions
    )


def test_ir_drops_non_authoritative_extra_fields_and_defaults_confidence() -> None:
    parsed = ScenarioIrBatch.model_validate(
        {
            "locations": [
                {
                    "id": "lobby",
                    "title": "大堂",
                    "type": "other",
                    "status": "active",
                    "source_block_ids": ["block-1"],
                }
            ],
            "invented_root_metadata": "discard me",
        }
    )

    assert parsed.confidence == "medium"
    assert parsed.locations[0].model_dump() == {
        "source_block_ids": ("block-1",),
        "id": "lobby",
        "title": "大堂",
        "visibility": "hidden",
        "tags": (),
    }


def test_assembler_ignores_blank_model_assumptions() -> None:
    authored = batch("block-1").model_copy(
        update={"assumptions": ("", "   ", "需要审核的真实假设")}
    )

    result = ScenarioIrAssembler().assemble(
        (authored,),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="模组",
        corpus_truncated=False,
    )

    assert result.assumptions == ("需要审核的真实假设",)


def test_ir_partition_accepts_action_density_within_the_16_block_budget() -> None:
    parsed = ScenarioIrBatch.model_validate({
        "actions": [
            {
                "id": f"action-{index}",
                "title": f"Action {index}",
                "policy": "automatic",
                "source_block_ids": [f"block-{index}"],
            }
            for index in range(16)
        ]
    })

    assert len(parsed.actions) == 16


def test_assembler_repairs_only_provable_links_and_discards_unknown_checks() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["location_links"] = [{
        "from_id": "waiting-room",
        "to_id": "invented-room",
        "source_block_ids": ["block-1"],
    }]
    value["clues"] = [{
        "id": "inspection-result",
        "title": "检查结果",
        "importance": "core",
        "fact_path": "facts.room[inspected]",
        "source_block_ids": ["block-1"],
    }]
    value["actions"] = [*value["actions"], {
        "id": "unknown-check",
        "title": "未声明技能的检定",
        "policy": "optional_check",
        "on_success": [{"kind": "set_fact", "path": "unsafe", "value": True}],
        "source_block_ids": ["block-1"],
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="模组",
        corpus_truncated=False,
    )

    assert result.contract.location_links == ()
    assert result.contract.clues[0].discovery_operator_ids == ("inspect-room",)
    assert "unknown-check" not in {
        item.operator_id for item in result.contract.operators
    }
    assert any(
        "without a source-provable skill choice was discarded" in item
        for item in result.assumptions
    )


def test_assembler_discards_signal_that_projects_unproduced_state() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["consequence_signals"] = [{
        "id": "crawler-distance",
        "title": "Crawler distance",
        "source_path": "actors.investigator.distance_to_crawler",
        "bands": [{
            "band_id": "near",
            "all_conditions": [{
                "path": "actors.investigator.distance_to_crawler",
                "operator": "eq",
                "value": "near",
            }],
        }],
        "source_block_ids": ["block-1"],
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="模组",
        corpus_truncated=False,
    )

    assert result.contract.consequence_signals == ()
    assert any("unproduced state paths" in item for item in result.assumptions)


def test_assembler_removes_player_presentation_from_kp_only_signal() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["consequence_signals"] = [{
        "id": "hidden-danger",
        "title": "Hidden danger",
        "visibility": "kp",
        "source_path": "facts.room[inspected]",
        "public_title": "A danger approaches",
        "public_summary": "The table should not receive this text.",
        "bands": [{
            "band_id": "active",
            "all_conditions": [{
                "path": "facts.room[inspected]",
                "operator": "eq",
                "value": True,
            }],
            "player_visible": True,
            "public_label": "Danger",
            "public_description": "This is still KP-only.",
        }],
        "source_block_ids": ["block-1"],
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="模组",
        corpus_truncated=False,
    )

    signal = result.contract.consequence_signals[0]
    assert signal.visibility == "kp"
    assert signal.public_title == ""
    assert signal.public_summary == ""
    assert signal.bands[0].player_visible is False
    assert signal.bands[0].public_label == ""
    assert signal.bands[0].public_description == ""
    assert result.assumptions == ()
    assert [item.model_dump() for item in result.normalizations] == [{
        "code": "kp_signal_public_projection_removed",
        "record_kind": "consequence_signals",
        "record_id": "hidden-danger",
    }]


def test_assembler_downgrades_incomplete_table_signal_to_kp_visibility() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["consequence_signals"] = [{
        "id": "public-pressure",
        "title": "Public pressure",
        "visibility": "table",
        "source_path": "facts.room[inspected]",
        "public_title": "",
        "bands": [{
            "band_id": "active",
            "all_conditions": [{
                "path": "facts.room[inspected]",
                "operator": "eq",
                "value": True,
            }],
            "player_visible": True,
            "public_label": "Danger",
        }],
        "source_block_ids": ["block-1"],
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="模组",
        corpus_truncated=False,
    )

    signal = result.contract.consequence_signals[0]
    assert signal.visibility == "kp"
    assert signal.public_title == ""
    assert signal.bands[0].player_visible is False
    assert [item.code for item in result.normalizations] == [
        "table_signal_downgraded"
    ]


def test_assembler_reduces_exact_signal_precision_without_a_source_path() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["consequence_signals"] = [{
        "id": "public-stage",
        "title": "Public stage",
        "visibility": "table",
        "display_mode": "exact",
        "public_title": "Pressure",
        "bands": [{
            "band_id": "active",
            "all_conditions": [{
                "path": "facts.room[inspected]",
                "operator": "eq",
                "value": True,
            }],
            "player_visible": True,
            "public_label": "Active",
        }],
        "source_block_ids": ["block-1"],
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="模组",
        corpus_truncated=False,
    )

    signal = result.contract.consequence_signals[0]
    assert signal.visibility == "table"
    assert signal.display_mode == "stage"
    assert [item.code for item in result.normalizations] == [
        "exact_signal_precision_downgraded"
    ]


def test_assembler_keeps_first_duplicate_consequence_signal_band() -> None:
    value = batch("block-1").model_dump(mode="python")
    band = {
        "band_id": "active",
        "all_conditions": [{
            "path": "facts.room[inspected]",
            "operator": "eq",
            "value": True,
        }],
    }
    value["consequence_signals"] = [{
        "id": "private-stage",
        "title": "Private stage",
        "visibility": "kp",
        "bands": [band, {**band, "severity": 4}],
        "source_block_ids": ["block-1"],
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="模组",
        corpus_truncated=False,
    )

    assert len(result.contract.consequence_signals[0].bands) == 1
    assert result.contract.consequence_signals[0].bands[0].severity == 0
    assert [item.code for item in result.normalizations] == [
        "duplicate_signal_bands_removed"
    ]


def test_assembler_discards_one_location_id_with_conflicting_source_identity() -> None:
    result = ScenarioIrAssembler().assemble(
        (batch("block-1"), batch("block-2", title="冲突名称")),
        source_refs={"block-1": source_ref("block-1"), "block-2": source_ref("block-2")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="模组",
        corpus_truncated=False,
    )

    assert result.contract.locations == ()
    assert result.contract.initial_scene_id is None
    assert any(
        "conflicting source identities were discarded" in item
        for item in result.assumptions
    )


def test_assembler_rejects_unknown_sources_and_safely_repairs_initial_scene() -> None:
    unknown_source = batch("invented")
    with pytest.raises(ValueError, match="unknown source blocks"):
        ScenarioIrAssembler().assemble(
            (unknown_source,),
            source_refs={"block-1": source_ref("block-1")},
            contract_id="server-contract",
            source_version=1,
            ruleset_id="coc7",
            title="模组",
            corpus_truncated=False,
        )

    broken = batch("block-1").model_copy(
        update={"initial_scene_id": "missing-location"}
    )
    repaired = ScenarioIrAssembler().assemble(
        (broken,),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="模组",
        corpus_truncated=False,
    )
    assert repaired.contract.initial_scene_id == "waiting-room"
    assert any("Unknown proposed initial scene" in item for item in repaired.assumptions)


@pytest.mark.parametrize(
    ("term", "expected_keys"),
    [
        ("灵感", ("int",)),
        ("SAN", ("san",)),
        (
            "修理",
            ("coc7.electrical_repair", "coc7.mechanical_repair"),
        ),
    ],
)
def test_assembler_resolves_abstract_checks_through_ruleset_catalog(
    term: str,
    expected_keys: tuple[str, ...],
) -> None:
    value = batch("block-1").model_dump(mode="python")
    value["actions"] = [{
        "id": "source-check",
        "title": "按来源进行检定",
        "policy": "required_check",
        "abstract_checks": [{
            "term": term,
            "difficulty": "regular",
            "reason": "模组原文明确要求",
        }],
        "on_success": [{"kind": "set_fact", "path": "passed", "value": True}],
        "source_block_ids": ["block-1"],
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7-keeper-cn-2002c",
        title="模组",
        corpus_truncated=False,
        check_catalog=coc7_scenario_check_catalog(),
    )

    assert tuple(
        choice.skill_key for choice in result.contract.operators[0].skill_choices
    ) == expected_keys
    assert result.check_mappings[0].source_term == term
    assert result.check_mappings[0].status == "resolved"
    assert result.assumptions == ()


def test_assembler_replaces_unsupported_failure_stakes_with_goal_boundary() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["actions"] = [{
        "id": "research-records",
        "title": "检索记录",
        "policy": "required_check",
        "location_id": "waiting-room",
        "checks": [{
            "skill_key": "图书馆使用",
            "reason": "来源要求检定",
            "failure_stakes": "失败会凭空烧毁整栋建筑。",
        }],
        "on_success": [{"kind": "set_fact", "path": "records.found", "value": True}],
        "on_failure": [{
            "kind": "set_fact",
            "path": "action_goals.research-records.achieved",
            "value": False,
        }],
        "source_block_ids": ["block-1"],
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        source_texts={"block-1": "在候车室进行【图书馆使用】检定以检索记录。"},
        source_titles={"block-1": "候车室"},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7-keeper-cn-2002c",
        title="模组",
        corpus_truncated=False,
        check_catalog=coc7_scenario_check_catalog(),
    )

    choice = result.contract.operators[0].skill_choices[0]
    assert choice.failure_stakes == (
        "本次尝试未达成“检索记录”所述目标；"
        "来源保证的自动信息仍保留，且未规定其他普通失败后果。"
    )
    assert "烧毁" not in choice.failure_stakes


def test_assembler_projects_validated_ruleset_failure_stakes() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["actions"] = [{
        "id": "avoid-blow",
        "title": "闪避攻击",
        "policy": "required_check",
        "location_id": "waiting-room",
        "checks": [{
            "skill_key": "闪避",
            "reason": "来源要求检定",
            "failure_stakes": "失败会被传送到月球。",
        }],
        "on_success": [{"kind": "set_fact", "path": "blow.avoided", "value": True}],
        "on_failure": [{
            "kind": "apply_ruleset_effect",
            "actor_id": "$actor",
            "event_type": "damage",
            "payload": {"damage": "1d6"},
        }],
        "source_block_ids": ["block-1"],
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        source_texts={"block-1": "在候车室进行【闪避】检定。"},
        source_titles={"block-1": "候车室"},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7-keeper-cn-2002c",
        title="模组",
        corpus_truncated=False,
        check_catalog=coc7_scenario_check_catalog(),
        effect_catalog=coc7_scenario_effect_catalog(),
    )

    choice = result.contract.operators[0].skill_choices[0]
    assert choice.failure_stakes == (
        "失败将承受该分支已冻结的规则后果：生命值伤害（1d6）。"
    )
    assert "月球" not in choice.failure_stakes


def test_assembler_does_not_invent_stakes_for_opaque_event_failure() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["actions"] = [{
        "id": "inspect-window",
        "title": "检查窗户",
        "policy": "required_check",
        "location_id": "waiting-room",
        "checks": [{"skill_key": "侦查", "reason": "来源要求检定"}],
        "on_success": [{"kind": "set_fact", "path": "window.checked", "value": True}],
        "on_failure": [{"kind": "emit_event", "event_type": "bed_reacts"}],
        "source_block_ids": ["block-1"],
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        source_texts={"block-1": "在候车室进行【侦查】检定窗户。"},
        source_titles={"block-1": "候车室"},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7-keeper-cn-2002c",
        title="模组",
        corpus_truncated=False,
        check_catalog=coc7_scenario_check_catalog(),
        effect_catalog=coc7_scenario_effect_catalog(),
    )

    assert result.contract.operators[0].skill_choices[0].failure_stakes == ""


def test_assembler_preserves_source_exact_stakes_for_event_failure() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["actions"] = [{
        "id": "inspect-window",
        "title": "检查窗户",
        "policy": "required_check",
        "location_id": "waiting-room",
        "checks": [{
            "skill_key": "侦查",
            "reason": "来源要求检定",
            "failure_stakes": "失败时床会猛然撞向调查员",
        }],
        "on_success": [{"kind": "set_fact", "path": "window.checked", "value": True}],
        "on_failure": [{"kind": "emit_event", "event_type": "bed_reacts"}],
        "source_block_ids": ["block-1"],
    }]
    source = "在候车室进行【侦查】检定窗户；失败时床会猛然撞向调查员。"

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        source_texts={"block-1": source},
        source_titles={"block-1": "候车室"},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7-keeper-cn-2002c",
        title="模组",
        corpus_truncated=False,
        check_catalog=coc7_scenario_check_catalog(),
        effect_catalog=coc7_scenario_effect_catalog(),
    )

    assert result.contract.operators[0].skill_choices[0].failure_stakes == (
        "失败时床会猛然撞向调查员"
    )


def test_assembler_discards_unresolved_abstract_check_without_authority() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["actions"] = [*value["actions"], {
        "id": "unknown-check",
        "title": "未知检定",
        "policy": "required_check",
        "abstract_checks": [{
            "term": "心灵感应",
            "reason": "模型无法确定规则键",
        }],
        "on_success": [{"kind": "set_fact", "path": "unsafe", "value": True}],
        "source_block_ids": ["block-1"],
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7-keeper-cn-2002c",
        title="模组",
        corpus_truncated=False,
        check_catalog=coc7_scenario_check_catalog(),
    )

    assert "unknown-check" not in {
        item.operator_id for item in result.contract.operators
    }
    assert result.check_mappings[0].status == "unresolved"
    assert any("unresolved abstract check" in item for item in result.assumptions)


def test_assembler_discards_endings_with_unproduced_conditions() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["endings"] = [{
        "id": "invented-ending",
        "title": "凭空结局",
        "all_conditions": [{
            "path": "facts.never.produced",
            "operator": "eq",
            "value": True,
        }],
        "source_block_ids": ["block-1"],
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="模组",
        corpus_truncated=False,
    )

    assert result.contract.endings == ()
    assert any(
        "Ending invented-ending with unproduced state paths was discarded" in item
        for item in result.assumptions
    )


def test_assembler_keeps_endings_linked_to_kernel_operator_outcomes() -> None:
    value = batch("block-1").model_dump(mode="python")
    value["endings"] = [{
        "id": "inspected-ending",
        "title": "完成检查",
        "all_conditions": [{
            "path": operator_outcome_path("inspect-room"),
            "operator": "eq",
            "value": "success",
        }],
        "source_block_ids": ["block-1"],
    }]

    result = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch.model_validate(value),),
        source_refs={"block-1": source_ref("block-1")},
        contract_id="server-contract",
        source_version=1,
        ruleset_id="coc7",
        title="模组",
        corpus_truncated=False,
    )

    assert result.contract.endings[0].ending_id == "inspected-ending"

from __future__ import annotations

import json

import pytest

from ai_kp.platform.resolution.contracts import (
    EntityRuntimeState,
    ScenarioContract,
    WorldCommand,
)
from ai_kp.platform.resolution.director_brief import (
    DIRECTOR_ACTOR_LOCATION_LIMIT,
    DIRECTOR_CLOCK_LIMIT,
    DIRECTOR_ENTITIES_JSON_BYTES,
    DIRECTOR_ENTITY_LIMIT,
    DIRECTOR_EVENT_LIMIT,
    DIRECTOR_FACT_LIMIT,
    DIRECTOR_JSON_CONTAINER_ITEMS,
    DIRECTOR_JSON_MAX_DEPTH,
    DIRECTOR_JSON_STRING_CHARS,
    DIRECTOR_RESOURCE_LIMIT,
    DIRECTOR_STATE_JSON_BYTES,
    DirectorBriefProjector,
)
from ai_kp.platform.resolution.json_projection import json_byte_size


def _contract() -> ScenarioContract:
    source = {
        "source_block_id": "source-1",
        "document_id": "module-1",
        "page": 7,
    }
    return ScenarioContract.model_validate(
        {
            "contract_id": "brief-test",
            "source_version": 1,
            "ruleset_id": "coc7",
            "title": "Director brief fixture",
            "initial_scene_id": "lobby",
            "initial_facts": {"door": {"unlocked": False}},
            "locations": [
                {"location_id": "lobby", "title": "旅店大堂", "source_refs": [source]},
                {"location_id": "cellar", "title": "地下室", "source_refs": [source]},
            ],
            "entities": [
                {
                    "entity_id": "innkeeper",
                    "entity_type": "npc",
                    "title": "旅店老板",
                    "initial_location_id": "lobby",
                    "source_refs": [source],
                }
            ],
            "clocks": [
                {
                    "clock_id": "midnight",
                    "title": "子夜",
                    "maximum_value": 4,
                    "source_refs": [source],
                }
            ],
            "resources": [
                {
                    "resource_id": "light",
                    "title": "光源",
                    "initial_value": 2,
                    "maximum_value": 3,
                    "source_refs": [source],
                }
            ],
            "clues": [
                {
                    "clue_id": "guestbook",
                    "title": "旅客簿",
                    "fact_path": "facts.guestbook.found",
                    "discovery_operator_ids": ["search-desk"],
                    "public_content": ["柜台里藏着旅客簿。"],
                    "source_refs": [source],
                }
            ],
            "operators": [
                {
                    "operator_id": "search-desk",
                    "title": "搜索柜台",
                    "intent_hints": ["查找旅客簿", "搜索柜台"],
                    "public_setup": "仔细查看柜台。",
                    "policy": "required_check",
                    "skill_choices": [
                        {
                            "skill_key": "spot_hidden",
                            "reason": "发现被遮挡的簿册。",
                        }
                    ],
                    "automatic_information": ["柜台有被翻找的痕迹。"],
                    "maximum_effect": "只能找到柜台中的证据。",
                    "source_refs": [source],
                },
                {
                    "operator_id": "open-cellar",
                    "title": "打开地下室",
                    "intent_hints": ["进入地下室"],
                    "policy": "automatic",
                    "preconditions": [
                        {"path": "facts.door.unlocked", "operator": "eq", "value": True}
                    ],
                    "source_refs": [source],
                },
            ],
            "task_methods": [
                {
                    "method_id": "investigate-inn",
                    "task_key": "investigate",
                    "title": "调查旅店",
                    "intent_hints": ["彻底调查旅店"],
                    "steps": [{"step_id": "desk", "operator_id": "search-desk"}],
                    "source_refs": [source],
                }
            ],
        }
    )


def test_projects_authoritative_scene_and_available_operator() -> None:
    contract = _contract()
    snapshot = contract.initial_snapshot("run-1")

    brief = DirectorBriefProjector.project(contract, snapshot, "a" * 64, "我该如何裁定搜索柜台？")

    assert brief.state.scene_id == "lobby"
    assert brief.state.scene_title == "旅店大堂"
    assert brief.state.state_version == 0
    assert brief.state.facts["door"].value == {"unlocked": False}
    assert brief.state.facts["door"].truncated is False
    assert brief.state.facts_total_count == 1
    assert brief.state.facts_truncated is False
    assert brief.state.entities_total_count == 1
    assert brief.state.entities_truncated is False
    candidate = next(item for item in brief.candidates if item.candidate_id == "search-desk")
    assert candidate.available is True
    assert candidate.policy == "required_check"
    assert [item.skill_key for item in candidate.skill_choices] == ["spot_hidden"]
    assert candidate.automatic_information == ("柜台有被翻找的痕迹。",)
    assert {item.evidence_id for item in brief.evidence} >= {
        "location:lobby",
        "operator:search-desk",
        "clue:guestbook",
    }


def test_marks_operator_unavailable_from_snapshot_preconditions() -> None:
    contract = _contract()
    brief = DirectorBriefProjector.project(
        contract,
        contract.initial_snapshot("run-1"),
        "b" * 64,
        "玩家想进入地下室。",
    )

    candidate = next(item for item in brief.candidates if item.candidate_id == "open-cellar")
    assert candidate.available is False
    assert "facts.door.unlocked" in candidate.reason


def test_projects_ranked_task_method_without_simulating_writes() -> None:
    contract = _contract()
    brief = DirectorBriefProjector.project(
        contract,
        contract.initial_snapshot("run-1"),
        "e" * 64,
        "我想彻底调查旅店。",
    )

    candidate = next(item for item in brief.candidates if item.candidate_id == "investigate-inn")
    assert candidate.kind == "task_method"
    assert candidate.available is True
    assert candidate.step_operator_ids == ("search-desk",)
    assert candidate.evidence_ids == ("task_method:investigate-inn",)


def test_basis_hash_changes_with_question_and_state() -> None:
    contract = _contract()
    initial = contract.initial_snapshot("run-1")
    changed = initial.model_copy(update={"run_version": 1, "facts": {"door": {"unlocked": True}}})

    first = DirectorBriefProjector.project(contract, initial, "c" * 64, "搜索柜台")
    repeated = DirectorBriefProjector.project(contract, initial, "c" * 64, "搜索柜台")
    other_question = DirectorBriefProjector.project(contract, initial, "c" * 64, "进入地下室")
    other_state = DirectorBriefProjector.project(contract, changed, "c" * 64, "搜索柜台")

    assert first.basis_hash == repeated.basis_hash
    assert first.basis_hash != other_question.basis_hash
    assert first.basis_hash != other_state.basis_hash


def test_unrelated_question_has_no_candidates() -> None:
    contract = _contract()
    brief = DirectorBriefProjector.project(
        contract,
        contract.initial_snapshot("run-1"),
        "d" * 64,
        "这个问题与场景中任何行动都无关",
    )

    assert brief.candidates == ()
    assert [item.evidence_id for item in brief.evidence] == ["location:lobby"]


def test_state_projection_is_bounded_deterministic_and_read_only() -> None:
    contract = _contract()
    initial = contract.initial_snapshot("run-large")
    large_text = "非常详细的长期战役记录" * 1000
    facts = {f"fact-{index:03d}": {"payload": large_text, "sequence": index} for index in range(40)}
    facts.update(
        {
            "zz-critical-fact": {
                "__director_brief_truncated__": "authoritative-user-value",
                "message": large_text,
                "wide": list(range(DIRECTOR_JSON_CONTAINER_ITEMS + 20)),
            },
            "zz-deep-list": [[[[[large_text]]]]],
            "zz-long-text": large_text,
        }
    )
    entities = {f"entity-{index:03d}": "active" for index in range(40)}
    entities["zz-current"] = "active"
    entity_runtime = {
        entity_id: EntityRuntimeState(
            status=status,
            location_id="cellar",
        )
        for entity_id, status in entities.items()
    }
    entity_runtime["zz-current"] = EntityRuntimeState(
        status="active",
        location_id="lobby",
    )
    actor_locations = {f"actor-{index:03d}": "cellar" for index in range(40)}
    actor_locations["actor-039"] = "lobby"
    resources = {f"resource-{index:03d}": index for index in range(40)}
    clocks = {f"clock-{index:03d}": index for index in range(40)}
    events = tuple(
        {
            "event_index": index,
            "payload": large_text,
            "nested": {"levels": [[[[large_text]]]]},
        }
        for index in range(30)
    )
    snapshot = initial.model_copy(
        update={
            "facts": facts,
            "entities": entities,
            "entity_runtime": entity_runtime,
            "actor_locations": actor_locations,
            "resources": resources,
            "clocks": clocks,
            "events": events,
        }
    )
    before = snapshot.model_dump(mode="json")
    question = (
        "zz-critical-fact zz-deep-list zz-long-text entity-039 actor-039 resource-039 clock-039"
    )

    first = DirectorBriefProjector.project(contract, snapshot, "f" * 64, question)
    repeated = DirectorBriefProjector.project(contract, snapshot, "f" * 64, question)

    assert first == repeated
    assert snapshot.model_dump(mode="json") == before
    state = first.state
    assert len(state.facts) <= DIRECTOR_FACT_LIMIT
    assert len(state.entities) <= DIRECTOR_ENTITY_LIMIT
    assert len(state.actor_locations) <= DIRECTOR_ACTOR_LOCATION_LIMIT
    assert len(state.resources) <= DIRECTOR_RESOURCE_LIMIT
    assert len(state.clocks) <= DIRECTOR_CLOCK_LIMIT
    assert len(state.recent_events) <= DIRECTOR_EVENT_LIMIT
    assert state.facts_total_count == len(facts)
    assert state.entities_total_count == len(entities) + 1  # contract-only innkeeper
    assert state.actor_locations_total_count == len(actor_locations)
    assert state.resources_total_count == len(resources)
    assert state.clocks_total_count == len(clocks)
    assert state.recent_events_total_count == len(events)
    assert state.facts_truncated is True
    assert state.entities_truncated is True
    assert state.actor_locations_truncated is True
    assert state.resources_truncated is True
    assert state.clocks_truncated is True
    assert state.recent_events_truncated is True

    assert "zz-critical-fact" in state.facts
    critical = state.facts["zz-critical-fact"]
    assert isinstance(critical.value, dict)
    assert critical.value["__director_brief_truncated__"] == ("authoritative-user-value")
    assert critical.truncated is True
    assert isinstance(state.facts["zz-deep-list"].value, list)
    assert state.facts["zz-deep-list"].truncated is True
    assert isinstance(state.facts["zz-long-text"].value, str)
    assert len(state.facts["zz-long-text"].value) <= DIRECTOR_JSON_STRING_CHARS + 1
    assert state.facts["zz-long-text"].truncated is True

    assert "entity-039" in {item.entity_id for item in state.entities}
    assert "zz-current" in {item.entity_id for item in state.entities}
    assert "actor-039" in {item.actor_id for item in state.actor_locations}
    assert "resource-039" in {item.metric_id for item in state.resources}
    assert "clock-039" in {item.metric_id for item in state.clocks}
    event_indexes = [item.value["event_index"] for item in state.recent_events]
    assert event_indexes == sorted(event_indexes)
    assert event_indexes[-1] == len(events) - 1
    assert all(isinstance(item.value, dict) for item in state.recent_events)
    assert any(item.truncated for item in state.recent_events)

    encoded_state = json.dumps(
        state.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(encoded_state) <= DIRECTOR_STATE_JSON_BYTES


def test_candidate_referenced_entity_is_prioritized_before_stable_fill() -> None:
    payload = _contract().model_dump(mode="json")
    payload["entities"].extend(
        {
            "entity_id": f"filler-{index:03d}",
            "entity_type": "npc",
            "title": f"Filler {index}",
            "initial_location_id": "lobby",
        }
        for index in range(DIRECTOR_ENTITY_LIMIT + 8)
    )
    payload["entities"].append(
        {
            "entity_id": "zz-candidate-target",
            "entity_type": "npc",
            "title": "Candidate target",
            "initial_location_id": "cellar",
        }
    )
    payload["operators"][0]["always_commands"] = [
        {
            "kind": "set_entity_status",
            "entity_id": "zz-candidate-target",
            "value": "alerted",
        }
    ]
    contract = ScenarioContract.model_validate(payload)

    brief = DirectorBriefProjector.project(
        contract,
        contract.initial_snapshot("run-priority"),
        "1" * 64,
        "我该如何裁定搜索柜台？",
    )

    projected_ids = [item.entity_id for item in brief.state.entities]
    assert "zz-candidate-target" in projected_ids
    assert brief.state.entities_total_count == len(payload["entities"])
    assert brief.state.entities_truncated is True


def test_basis_hash_and_metadata_cover_unselected_fact_count() -> None:
    contract = _contract()
    initial = contract.initial_snapshot("run-count")
    facts = {"answer": True}
    facts.update({f"fact-{index:03d}": False for index in range(DIRECTOR_FACT_LIMIT)})
    first_snapshot = initial.model_copy(update={"facts": facts})
    expanded_snapshot = initial.model_copy(update={"facts": {**facts, "zz-unselected": False}})

    first = DirectorBriefProjector.project(contract, first_snapshot, "2" * 64, "answer")
    expanded = DirectorBriefProjector.project(contract, expanded_snapshot, "2" * 64, "answer")

    assert first.state.facts == expanded.state.facts
    assert expanded.state.facts_total_count == first.state.facts_total_count + 1
    assert first.state.facts_truncated is True
    assert expanded.state.facts_truncated is True
    assert first.basis_hash != expanded.basis_hash


def test_json_projection_depth_limit_keeps_container_type() -> None:
    contract = _contract()
    nested: object = "leaf"
    for _ in range(DIRECTOR_JSON_MAX_DEPTH + 3):
        nested = {"child": nested}
    snapshot = contract.initial_snapshot("run-depth").model_copy(
        update={"facts": {"nested": nested}}
    )

    brief = DirectorBriefProjector.project(
        contract,
        snapshot,
        "3" * 64,
        "nested",
    )

    preview = brief.state.facts["nested"]
    assert isinstance(preview.value, dict)
    assert preview.truncated is True


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_project_rejects_non_finite_snapshot_numbers_after_model_copy(
    number: float,
) -> None:
    contract = _contract()
    snapshot = contract.initial_snapshot("run-non-finite").model_copy(
        update={"resources": {"light": number}}
    )

    with pytest.raises(ValueError, match="finite JSON numbers"):
        DirectorBriefProjector.project(contract, snapshot, "4" * 64, "light")


@pytest.mark.parametrize(
    ("facts", "message"),
    [
        ({"unsupported": {"set-item"}}, "unsupported JSON value type set"),
        (
            {"collision": {1: "integer-key", "1": "string-key"}},
            "non-string JSON object key",
        ),
    ],
)
def test_project_rejects_python_only_fact_values_after_model_copy(
    facts: dict[str, object],
    message: str,
) -> None:
    contract = _contract()
    snapshot = contract.initial_snapshot("run-python-only").model_copy(update={"facts": facts})

    with pytest.raises(TypeError, match=message):
        DirectorBriefProjector.project(contract, snapshot, "5" * 64, "facts")


def test_project_rejects_cyclic_fact_values_after_model_copy() -> None:
    contract = _contract()
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    snapshot = contract.initial_snapshot("run-cycle").model_copy(
        update={"facts": {"cyclic": cyclic}}
    )

    with pytest.raises(ValueError, match="cyclic JSON object"):
        DirectorBriefProjector.project(contract, snapshot, "6" * 64, "cyclic")


def test_world_command_rejects_python_only_nested_values() -> None:
    with pytest.raises(TypeError, match="unsupported JSON value type set"):
        WorldCommand.model_validate(
            {
                "kind": "set_fact",
                "path": "facts.invalid",
                "value": {"nested": {"not-json"}},
            }
        )


def test_fact_insertion_order_does_not_change_projection_or_hash() -> None:
    contract = _contract()
    initial = contract.initial_snapshot("run-order")
    first_facts = {
        "alpha": {"one": 1, "two": [2, 3]},
        "beta": {"nested": {"left": True, "right": None}},
    }
    reversed_facts = {
        "beta": {"nested": {"right": None, "left": True}},
        "alpha": {"two": [2, 3], "one": 1},
    }

    first = DirectorBriefProjector.project(
        contract,
        initial.model_copy(update={"facts": first_facts}),
        "7" * 64,
        "alpha beta",
    )
    reordered = DirectorBriefProjector.project(
        contract,
        initial.model_copy(update={"facts": reversed_facts}),
        "7" * 64,
        "alpha beta",
    )

    assert first.state == reordered.state
    assert first.basis_hash == reordered.basis_hash


def test_candidate_referenced_fact_is_prioritized_before_stable_fill() -> None:
    payload = _contract().model_dump(mode="json")
    payload["operators"][0]["always_commands"] = [
        {
            "kind": "set_fact",
            "path": "zz-candidate-fact",
            "value": True,
        }
    ]
    contract = ScenarioContract.model_validate(payload)
    facts = {f"fact-{index:03d}": False for index in range(DIRECTOR_FACT_LIMIT + 8)}
    facts["zz-candidate-fact"] = False
    snapshot = contract.initial_snapshot("run-fact-priority").model_copy(update={"facts": facts})

    brief = DirectorBriefProjector.project(
        contract,
        snapshot,
        "8" * 64,
        "我该如何裁定搜索柜台？",
    )

    assert "zz-candidate-fact" in brief.state.facts
    assert brief.state.facts_truncated is True


def test_maximum_legal_referenced_entity_is_retained_with_field_metadata() -> None:
    payload = _contract().model_dump(mode="json")
    payload["entities"].append(
        {
            "entity_id": "critical",
            "entity_type": "npc",
            "title": "😀" * 240,
            "initial_status": "警" * 120,
            "initial_location_id": "lobby",
            "initial_runtime": {
                "status": "警" * 120,
                "location_id": "lobby",
                "emotional_state": "😀" * 240,
                "physical_state": "😀" * 240,
                "attitude": "😀" * 240,
                "short_term_goal": "😀" * 500,
            },
        }
    )
    payload["operators"][0]["always_commands"] = [
        {
            "kind": "set_entity_status",
            "entity_id": "critical",
            "value": "alerted",
        }
    ]
    contract = ScenarioContract.model_validate(payload)

    brief = DirectorBriefProjector.project(
        contract,
        contract.initial_snapshot("run-max-entity"),
        "9" * 64,
        "我该如何裁定搜索柜台？",
    )

    entity = next(item for item in brief.state.entities if item.entity_id == "critical")
    assert entity.truncated is True
    assert set(entity.truncated_fields) >= {
        "title",
        "emotional_state",
        "physical_state",
        "attitude",
        "short_term_goal",
    }
    assert "#" in entity.title
    assert (
        json_byte_size([item.model_dump(mode="json") for item in brief.state.entities])
        <= DIRECTOR_ENTITIES_JSON_BYTES
    )


def test_long_relevant_fact_key_is_shortened_and_retained() -> None:
    contract = _contract()
    long_key = "关键事实" * 1000
    snapshot = contract.initial_snapshot("run-long-key").model_copy(
        update={"facts": {long_key: {"answer": True}, "other": False}}
    )

    brief = DirectorBriefProjector.project(
        contract,
        snapshot,
        "0" * 64,
        "关键事实",
    )

    projected_key, preview = next(
        (key, item) for key, item in brief.state.facts.items() if item.key_truncated
    )
    assert projected_key != long_key
    assert "#" in projected_key
    assert preview.value == {"answer": True}
    assert brief.state.facts_truncated is True

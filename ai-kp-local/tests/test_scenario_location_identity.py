from __future__ import annotations

import pytest

from ai_kp.platform.resolution.contracts import SourceRef
from ai_kp.platform.resolution.scenario_ir import ScenarioIrAssembler
from ai_kp.platform.resolution.scenario_ir_models import ScenarioIrBatch
from ai_kp.platform.resolution.scenario_location_identity import (
    reconcile_location_identities,
    source_supports_playable_location,
)


def _location(location_id: str, title: str, source_id: str, **extra: object) -> dict:
    return {
        "id": location_id,
        "title": title,
        "source_block_ids": [source_id],
        **extra,
    }


def test_location_reconciliation_merges_metadata_and_rewrites_all_ir_references() -> None:
    first = ScenarioIrBatch.model_validate({
        "initial_scene_id": "archive_z",
        "locations": [
            _location(
                "archive_z", "档案馆", "archive-1", visibility="known", tags=["records"]
            ),
            _location("chapel", "礼拜堂", "chapel"),
        ],
        "location_links": [{
            "from_id": "archive_z",
            "to_id": "chapel",
            "preconditions": [{
                "path": "scene_id", "operator": "eq", "value": "archive_z",
            }],
            "source_block_ids": ["archive-1"],
        }],
        "entities": [{
            "id": "archivist",
            "type": "npc",
            "title": "Archivist",
            "location_id": "archive_z",
            "response_obligations": [{
                "id": "directions",
                "trigger_topics": ["records"],
                "conditions": [{
                    "path": "entities.archivist.location_id",
                    "operator": "eq",
                    "value": "archive_z",
                }],
                "facts_to_convey": ["The records are indexed."],
            }],
            "source_block_ids": ["archive-1"],
        }],
        "clocks": [{
            "id": "closing",
            "title": "Closing",
            "maximum": 4,
            "pressure_visibility": "kp",
            "pressure_stages": [{
                "stage_id": "closed",
                "threshold": 4,
                "commands": [{"kind": "set_scene", "value": "archive_z"}],
            }],
            "source_block_ids": ["archive-1"],
        }],
        "actions": [{
            "id": "research",
            "title": "Research",
            "policy": "automatic",
            "location_id": "archive_z",
            "preconditions": [{
                "path": "scene_id", "operator": "eq", "value": "archive_z",
            }],
            "always": [{"kind": "set_scene", "value": "archive_z"}],
            "on_success": [{
                "kind": "move_actor", "actor_id": "investigator", "value": "archive_z",
            }],
            "source_block_ids": ["archive-1"],
        }],
        "task_methods": [{
            "id": "research-method",
            "task_key": "research",
            "title": "Research method",
            "preconditions": [{
                "path": "current_location_id", "operator": "eq", "value": "archive_z",
            }],
            "steps": [{"step_id": "research", "operator_id": "research"}],
            "source_block_ids": ["archive-1"],
        }],
        "reactive_policies": [{
            "id": "archive-policy",
            "entity_id": "archivist",
            "rules": [{
                "rule_id": "return",
                "trigger": "scene_entered",
                "conditions": [{
                    "path": "scene_id", "operator": "eq", "value": "archive_z",
                }],
                "commands": [{"kind": "set_scene", "value": "archive_z"}],
            }],
            "source_block_ids": ["archive-1"],
        }],
        "consequence_signals": [{
            "id": "archive-signal",
            "title": "Archive signal",
            "bands": [{
                "band_id": "inside",
                "all_conditions": [{
                    "path": "scene_id", "operator": "eq", "value": "archive_z",
                }],
            }],
            "source_block_ids": ["archive-1"],
        }],
        "endings": [{
            "id": "archive-ending",
            "title": "Archive ending",
            "all_conditions": [{
                "path": "scene_id", "operator": "eq", "value": "archive_z",
            }],
            "commands": [{"kind": "set_scene", "value": "archive_z"}],
            "source_block_ids": ["archive-1"],
        }],
    })
    second = ScenarioIrBatch.model_validate({
        "locations": [
            _location(
                "archive_a", "档 案 馆", "archive-2", visibility="visited", tags=["clues"]
            )
        ]
    })
    assumptions: list[str] = []

    result = reconcile_location_identities(
        (first, second),
        source_section_paths={
            "archive-1": ("场景 1", "档案馆"),
            "archive-2": ("场景 1", "档 案 馆"),
            "chapel": ("场景 1", "礼拜堂"),
        },
        assumptions=assumptions,
    )

    merged = next(item for batch in result for item in batch.locations if item.id == "archive_a")
    assert merged.source_block_ids == ("archive-1", "archive-2")
    assert merged.tags == ("clues", "records")
    assert merged.visibility == "visited"
    rewritten = result[0]
    assert rewritten.initial_scene_id == "archive_a"
    assert rewritten.location_links[0].from_id == "archive_a"
    assert rewritten.location_links[0].preconditions[0].value == "archive_a"
    assert rewritten.entities[0].location_id == "archive_a"
    assert rewritten.entities[0].response_obligations[0].conditions[0].value == "archive_a"
    assert rewritten.clocks[0].pressure_stages[0].commands[0].value == "archive_a"
    assert rewritten.actions[0].location_id == "archive_a"
    assert rewritten.actions[0].preconditions[0].value == "archive_a"
    assert rewritten.actions[0].always[0].value == "archive_a"
    assert rewritten.actions[0].on_success[0].value == "archive_a"
    assert rewritten.task_methods[0].preconditions[0].value == "archive_a"
    assert rewritten.reactive_policies[0].rules[0].conditions[0].value == "archive_a"
    assert rewritten.reactive_policies[0].rules[0].commands[0].value == "archive_a"
    assert rewritten.consequence_signals[0].bands[0].all_conditions[0].value == "archive_a"
    assert rewritten.endings[0].all_conditions[0].value == "archive_a"
    assert rewritten.endings[0].commands[0].value == "archive_a"
    assert any("merged as archive_a" in item for item in assumptions)


@pytest.mark.parametrize("title", ["档案馆", "疗养院", "礼拜堂", "儿童房", "浴室", "藏身处"])
def test_format_equivalent_locations_with_one_parent_converge(title: str) -> None:
    assumptions: list[str] = []
    batches = (
        ScenarioIrBatch.model_validate({
            "locations": [_location("z_location", title, "one")]
        }),
        ScenarioIrBatch.model_validate({
            "locations": [_location("a_location", f" {title} ", "two")]
        }),
    )

    result = reconcile_location_identities(
        batches,
        source_section_paths={
            "one": ("场景 2", "一楼", title),
            "two": ("场景 2", "一楼", f" {title} "),
        },
        assumptions=assumptions,
    )

    assert [item.id for batch in result for item in batch.locations] == ["a_location"]


@pytest.mark.parametrize(
    ("title", "semantic_kind"),
    [
        ("文字材料 6", "heading"),
        ("Handout No. 4", "heading"),
        ("MP: 18", "heading"),
        ("扮演须知", "heading"),
        ("Keeper Notes", "heading"),
    ],
)
def test_document_labels_cannot_become_playable_locations(
    title: str, semantic_kind: str
) -> None:
    assert source_supports_playable_location(
        title,
        ("source",),
        source_texts={"source": title},
        source_titles={"source": title},
        source_section_paths={"source": ("场景 2: 图书馆", title)},
        source_scene_keys={"source": "图书馆"},
        source_semantic_kinds={"source": semantic_kind},
    ) is False


def test_location_role_gate_keeps_structural_and_explicit_prose_places() -> None:
    assert source_supports_playable_location(
        "中央图书馆",
        ("library",),
        source_texts={"library": "调查员可以查阅馆藏。"},
        source_titles={"library": "场景 3: 中央图书馆"},
        source_section_paths={"library": ("场景 3: 中央图书馆",)},
        source_scene_keys={"library": "中央图书馆"},
        source_semantic_kinds={"library": "check"},
    ) is True


def test_full_numbered_scene_title_merges_with_server_scene_key_title() -> None:
    assumptions: list[str] = []
    result = reconcile_location_identities(
        (
            ScenarioIrBatch.model_validate({
                "locations": [
                    _location("model_intro", "场景 1: 介绍", "heading")
                ]
            }),
            ScenarioIrBatch.model_validate({
                "locations": [
                    _location("source_intro", "介绍", "heading")
                ]
            }),
        ),
        source_section_paths={"heading": ("场景 1: 介绍",)},
        assumptions=assumptions,
    )

    locations = [item for batch in result for item in batch.locations]
    assert len(locations) == 1
    assert locations[0].title == "介绍"
    assert any("model_intro" in item and "source_intro" in item for item in assumptions)

    assert source_supports_playable_location(
        "Docking Bay Seven",
        ("sci-fi-scene",),
        source_texts={"sci-fi-scene": "The crew arrives under emergency power."},
        source_titles={"sci-fi-scene": "Scene 4: Docking Bay Seven"},
        source_section_paths={"sci-fi-scene": ("Scene 4: Docking Bay Seven",)},
        source_scene_keys={"sci-fi-scene": "Docking Bay Seven"},
        source_semantic_kinds={"sci-fi-scene": "scene"},
    ) is True
    assert source_supports_playable_location(
        "科比特的老房子",
        ("brief",),
        source_texts={
            "brief": "房东委托你们调查一座位于波士顿市中心的科比特的老房子。"
        },
        source_titles={"brief": "委托"},
        source_section_paths={"brief": ("导入",)},
        source_scene_keys={},
        source_semantic_kinds={"brief": "text"},
    ) is True


def test_role_rejected_location_discards_partition_owned_references() -> None:
    batch = ScenarioIrBatch.model_validate({
        "locations": [
            _location("library", "中央图书馆", "library"),
            _location("handout", "文字材料 6", "handout"),
        ],
        "location_links": [{
            "from_id": "library",
            "to_id": "handout",
            "source_block_ids": ["handout"],
        }],
        "entities": [{
            "id": "reader",
            "type": "npc",
            "title": "Reader",
            "location_id": "handout",
            "source_block_ids": ["handout"],
        }],
        "actions": [{
            "id": "read-handout",
            "title": "Read handout",
            "policy": "automatic",
            "location_id": "handout",
            "source_block_ids": ["handout"],
        }],
    })
    assumptions: list[str] = []

    result = reconcile_location_identities(
        (batch,),
        source_texts={"library": "Library.", "handout": "文字材料 6"},
        source_titles={"library": "中央图书馆", "handout": "文字材料 6"},
        source_section_paths={
            "library": ("场景 3: 中央图书馆",),
            "handout": ("场景 3: 中央图书馆", "文字材料 6"),
        },
        source_scene_keys={"library": "中央图书馆", "handout": "中央图书馆"},
        source_semantic_kinds={"library": "scene", "handout": "heading"},
        assumptions=assumptions,
    )[0]

    assert [item.id for item in result.locations] == ["library"]
    assert result.location_links == ()
    assert result.entities[0].location_id is None
    assert result.actions[0].location_id is None
    assert any("handout (文字材料 6)" in item for item in assumptions)


def test_room_prefix_variant_converges_only_with_matching_source_ancestry() -> None:
    assumptions: list[str] = []
    result = reconcile_location_identities(
        (
            ScenarioIrBatch.model_validate({
                "locations": [_location("child_room", "儿童房", "one")]
            }),
            ScenarioIrBatch.model_validate({
                "locations": [_location("room_2", "房间 2：儿童房", "two")]
            }),
        ),
        source_section_paths={
            "one": ("场景 2", "一楼", "2号房间：儿童房", "床架攻击"),
            "two": ("场景 2", "一楼", "房间 2：儿童房"),
        },
        assumptions=assumptions,
    )

    locations = [item for batch in result for item in batch.locations]
    assert len(locations) == 1
    assert locations[0].id == "child_room"


def test_number_only_room_prefix_formats_converge() -> None:
    result = reconcile_location_identities(
        (
            ScenarioIrBatch.model_validate({
                "locations": [_location("room_z", "2号房间", "one")]
            }),
            ScenarioIrBatch.model_validate({
                "locations": [_location("room_a", "房间2", "two")]
            }),
        ),
        source_section_paths={
            "one": ("一楼", "2号房间"),
            "two": ("一楼", "房间2"),
        },
        assumptions=[],
    )

    assert [item.id for batch in result for item in batch.locations] == ["room_a"]


def test_same_storage_title_on_different_floors_never_converges() -> None:
    assumptions: list[str] = []
    result = reconcile_location_identities(
        (
            ScenarioIrBatch.model_validate({
                "locations": [_location("first_storage", "1号储藏室", "first")]
            }),
            ScenarioIrBatch.model_validate({
                "locations": [_location("basement_storage", "1号储藏室", "basement")]
            }),
        ),
        source_section_paths={
            "first": ("场景 4", "一楼", "1号储藏室"),
            "basement": ("场景 4", "地下室", "1号储藏室"),
        },
        assumptions=assumptions,
    )

    assert {item.id for batch in result for item in batch.locations} == {
        "first_storage",
        "basement_storage",
    }
    assert any("kept separate across source parents" in item for item in assumptions)


def test_one_model_id_with_conflicting_location_identity_fails_closed() -> None:
    assumptions: list[str] = []
    result = reconcile_location_identities(
        (
            ScenarioIrBatch.model_validate({
                "initial_scene_id": "room",
                "locations": [_location("room", "浴室", "bath")],
            }),
            ScenarioIrBatch.model_validate({
                "locations": [_location("room", "儿童房", "child")],
            }),
        ),
        source_section_paths={
            "bath": ("一楼", "浴室"),
            "child": ("一楼", "儿童房"),
        },
        assumptions=assumptions,
    )

    assert not any(batch.locations for batch in result)
    assert result[0].initial_scene_id is None
    assert any("conflicting source identities were discarded" in item for item in assumptions)


def test_one_model_id_retains_the_unique_source_aligned_identity() -> None:
    assumptions: list[str] = []
    result = reconcile_location_identities(
        (
            ScenarioIrBatch.model_validate({
                "locations": [_location("library", "中央图书馆", "library")],
                "actions": [{
                    "id": "research",
                    "title": "查阅资料",
                    "policy": "automatic",
                    "location_id": "library",
                    "on_success": [{
                        "kind": "set_fact", "path": "facts.researched", "value": True,
                    }],
                    "source_block_ids": ["library"],
                }],
            }),
            ScenarioIrBatch.model_validate({
                "locations": [
                    _location("library", "中央图书馆或档案馆", "appendix")
                ],
                "actions": [{
                    "id": "bad-composite-action",
                    "title": "查阅复合地点",
                    "policy": "automatic",
                    "location_id": "library",
                    "on_success": [{
                        "kind": "set_fact", "path": "facts.bad", "value": True,
                    }],
                    "source_block_ids": ["appendix"],
                }],
            }),
        ),
        source_section_paths={
            "library": ("场景 3: 中央图书馆", "查阅资料"),
            "appendix": ("结局", "文字材料1"),
        },
        assumptions=assumptions,
    )

    assert [item.title for batch in result for item in batch.locations] == ["中央图书馆"]
    assert [item.id for item in result[0].actions] == ["research"]
    assert result[1].actions[0].location_id is None
    assert any("outside the unique source identity" in item for item in assumptions)


def test_assembler_reconciles_partition_locations_before_action_scope() -> None:
    first = ScenarioIrBatch.model_validate({
        "initial_scene_id": "archive_z",
        "locations": [
            _location("archive_z", "档案馆", "archive-location-1", visibility="visited")
        ],
        "actions": [{
            "id": "search-archive",
            "title": "Search archive",
            "policy": "automatic",
            "location_id": "archive_z",
            "on_success": [{
                "kind": "set_fact", "path": "archive.searched", "value": True,
            }],
            "source_block_ids": ["archive-action"],
        }],
    })
    second = ScenarioIrBatch.model_validate({
        "locations": [
            _location("archive_a", "档 案 馆", "archive-location-2", visibility="known")
        ]
    })
    source_ids = ("archive-location-1", "archive-location-2", "archive-action")

    result = ScenarioIrAssembler().assemble(
        (first, second),
        source_refs={
            source_id: SourceRef(
                source_block_id=source_id,
                document_id="document-1",
                paragraph=index,
            )
            for index, source_id in enumerate(source_ids, start=1)
        },
        source_texts={
            "archive-location-1": "档案馆。",
            "archive-location-2": "档案馆。",
            "archive-action": "Search the indexed records.",
        },
        source_titles={
            "archive-location-1": "档案馆",
            "archive-location-2": "档 案 馆",
            "archive-action": "Search indexed records",
        },
        source_section_paths={
            "archive-location-1": ("场景 1", "档案馆"),
            "archive-location-2": ("场景 1", "档 案 馆"),
            "archive-action": ("场景 1", "档案馆", "Search indexed records"),
        },
        contract_id="location-reconciliation",
        source_version=1,
        ruleset_id="coc7",
        title="Location reconciliation",
        corpus_truncated=False,
    )

    assert [item.location_id for item in result.contract.locations] == ["archive_a"]
    assert result.contract.initial_scene_id == "archive_a"
    assert result.contract.operators[0].preconditions[0].value == "archive_a"
    assert not any(
        item.operator_id.startswith("travel:") for item in result.contract.operators
    )


def test_root_scene_components_converge_to_the_exact_combined_scene() -> None:
    batches = (
        ScenarioIrBatch.model_validate({
            "locations": [
                _location("joint", "高等法院; 中央警察局", "scene-heading"),
                _location("court", "高等法院", "scene-heading"),
                _location("police", "中央警察局", "scene-heading"),
                _location("court-room", "高等法院", "court-room"),
            ],
            "actions": [{
                "id": "request-records",
                "title": "申请查看记录",
                "policy": "automatic",
                "location_id": "court",
                "on_success": [{
                    "kind": "set_fact", "path": "records.allowed", "value": True,
                }],
                "source_block_ids": ["scene-heading"],
            }],
        }),
    )
    assumptions: list[str] = []

    result = reconcile_location_identities(
        batches,
        source_section_paths={
            "scene-heading": ("场景 5: 高等法院; 中央警察局",),
            "court-room": (
                "场景 5: 高等法院; 中央警察局",
                "高等法院",
            ),
        },
        assumptions=assumptions,
    )

    assert [(item.id, item.title) for item in result[0].locations] == [
        ("joint", "高等法院; 中央警察局"),
        ("court-room", "高等法院"),
    ]
    assert result[0].actions[0].location_id == "joint"
    assert any("court, joint, police" in item for item in assumptions)

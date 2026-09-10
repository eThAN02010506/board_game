from __future__ import annotations

from ai_kp.platform.resolution.contracts import SourceRef
from ai_kp.platform.resolution.scenario_ir import ScenarioIrAssembler
from ai_kp.platform.resolution.scenario_ir_models import ScenarioIrBatch
from ai_kp.platform.resolution.source_scene_locations import (
    materialize_source_scene_locations,
)


def test_materializes_only_explicit_server_classified_scene_heading() -> None:
    assumptions: list[str] = []

    result = materialize_source_scene_locations(
        (ScenarioIrBatch(),),
        source_texts={
            "scene": "场景 9: 科比特的老房子",
            "text": "老房子已经荒废多年。",
            "handout": "科比特的日记写道……",
        },
        source_scene_keys={
            "scene": "科比特的老房子",
            "text": "科比特的老房子",
            "handout": "科比特的老房子",
        },
        source_semantic_kinds={
            "scene": "scene",
            "text": "text",
            "handout": "handout",
        },
        assumptions=assumptions,
    )

    assert len(result) == 2
    assert [item.title for item in result[-1].locations] == ["科比特的老房子"]
    assert result[-1].locations[0].source_block_ids == ("scene",)
    assert assumptions == [
        (
            "Server source scene locations materialized: "
            f"{result[-1].locations[0].id} (科比特的老房子)"
        )
    ]


def test_requires_source_text_to_be_an_explicit_scene_heading() -> None:
    result = materialize_source_scene_locations(
        (ScenarioIrBatch(),),
        source_texts={"misclassified": "守秘人须知"},
        source_scene_keys={"misclassified": "Basement Laboratory"},
        source_semantic_kinds={"misclassified": "scene"},
        assumptions=[],
    )

    assert result == (ScenarioIrBatch(),)


def test_chunks_large_scene_catalog_without_exceeding_ir_batch_limit() -> None:
    scene_ids = tuple(f"scene-{index}" for index in range(13))
    result = materialize_source_scene_locations(
        (ScenarioIrBatch(),),
        source_texts={source_id: f"Scene {index}: Room {index}" for index, source_id in enumerate(scene_ids)},
        source_scene_keys={source_id: f"Room {index}" for index, source_id in enumerate(scene_ids)},
        source_semantic_kinds={source_id: "scene" for source_id in scene_ids},
        assumptions=[],
    )

    assert [len(batch.locations) for batch in result] == [0, 12, 1]
    assert result[-1].locations[0].title == "Room 12"


def test_assembler_merges_model_location_with_source_scene_and_rewrites_refs() -> None:
    model_batch = ScenarioIrBatch.model_validate(
        {
            "locations": [
                {
                    "id": "z_model_house",
                    "title": "科比特的老房子",
                    "source_block_ids": ["prose"],
                }
            ],
            "entities": [
                {
                    "id": "caretaker",
                    "type": "npc",
                    "title": "管理员",
                    "location_id": "z_model_house",
                    "source_block_ids": ["prose"],
                }
            ],
        }
    )
    source_refs = {
        source_id: SourceRef(
            source_block_id=source_id,
            document_id="document",
            page=1,
            paragraph=index,
        )
        for index, source_id in enumerate(("heading", "prose"), start=1)
    }

    result = ScenarioIrAssembler().assemble(
        (model_batch,),
        source_refs=source_refs,
        contract_id="source-scenes",
        source_version=1,
        ruleset_id="generic",
        title="Source scenes",
        corpus_truncated=False,
        source_texts={
            "heading": "场景 9: 科比特的老房子",
            "prose": "调查员可以进入科比特的老房子。",
        },
        source_titles={"heading": "场景 9: 科比特的老房子", "prose": "老房子"},
        source_section_paths={
            "heading": ("场景 9: 科比特的老房子",),
            "prose": ("场景 9: 科比特的老房子", "管理员"),
        },
        source_scene_keys={"heading": "科比特的老房子", "prose": "科比特的老房子"},
        source_semantic_kinds={"heading": "scene", "prose": "text"},
        allow_empty_actions=True,
    )

    assert len(result.contract.locations) == 1
    location = result.contract.locations[0]
    assert location.location_id.startswith("source_scene_")
    assert {ref.source_block_id for ref in location.source_refs} == {"heading", "prose"}
    assert result.contract.entities[0].initial_location_id == location.location_id

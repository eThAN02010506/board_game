from __future__ import annotations

from ai_kp.platform.resolution.contracts import (
    ActionIntent,
    LocationSpec,
    ScenarioContract,
    ScenarioSnapshot,
    SourceRef,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.location_travel import (
    materialize_location_travel_operators,
)
from ai_kp.platform.resolution.playability import ScenarioPlayabilityAnalyzer
from ai_kp.platform.resolution.scenario_ir import ScenarioIrAssembler
from ai_kp.platform.resolution.scenario_ir_models import ScenarioIrBatch
from ai_kp.platform.resolution.source_structure_navigation import (
    materialize_source_structure_navigation,
)


def ref(source_id: str, paragraph: int = 1) -> SourceRef:
    return SourceRef(
        source_block_id=source_id,
        document_id="document",
        paragraph=paragraph,
    )


def contract(*locations: LocationSpec, initial: str | None = "house") -> ScenarioContract:
    return ScenarioContract(
        contract_id="contract",
        source_version=1,
        ruleset_id="coc7",
        title="House",
        initial_scene_id=initial,
        locations=locations,
    )


def test_materializes_nearest_exact_parent_and_is_idempotent() -> None:
    raw = contract(
        LocationSpec(location_id="house", title="科比特的老房子"),
        LocationSpec(location_id="ground", title="一楼", source_refs=(ref("floor"),)),
        LocationSpec(
            location_id="living",
            title="4 号房间: 客厅",
            source_refs=(ref("living"),),
        ),
    )
    paths = {
        "floor": ("场景 9: 科比特的老房子", "一楼"),
        "living": ("场景 9: 科比特的老房子", "一楼", "4 号房间: 客厅"),
    }
    repaired = materialize_source_structure_navigation(
        raw,
        source_texts={"floor": "一楼", "living": "4 号房间: 客厅"},
        source_section_paths=paths,
        source_scene_keys={"floor": "科比特的老房子", "living": "科比特的老房子"},
    )

    assert tuple(
        (item.from_location_id, item.to_location_id)
        for item in repaired.location_links
    ) == (("house", "ground"), ("ground", "living"))
    assert (
        materialize_source_structure_navigation(
            repaired,
            source_texts={"floor": "一楼", "living": "4 号房间: 客厅"},
            source_section_paths=paths,
            source_scene_keys={"floor": "科比特的老房子", "living": "科比特的老房子"},
        )
        == repaired
    )


def test_skips_secret_hidden_and_conditional_children() -> None:
    raw = contract(
        LocationSpec(location_id="house", title="科比特的老房子"),
        LocationSpec(location_id="ordinary", title="2 号房间: 卧室", source_refs=(ref("ordinary"),)),
        LocationSpec(location_id="lair", title="3 号房间: 隐秘老巢", source_refs=(ref("lair"),)),
        LocationSpec(location_id="hideout", title="4 号房间: 藏身处", source_refs=(ref("hideout"),)),
        LocationSpec(location_id="behind", title="墙后房间", source_refs=(ref("behind"),)),
        LocationSpec(location_id="locked", title="5 号房间: 储物室", source_refs=(ref("locked"),)),
    )
    child_titles = {
        "ordinary": "2 号房间: 卧室",
        "lair": "3 号房间: 隐秘老巢",
        "hideout": "4 号房间: 藏身处",
        "behind": "墙后房间",
        "locked": "5 号房间: 储物室",
    }
    paths = {
        source_id: ("场景 9: 科比特的老房子", title)
        for source_id, title in child_titles.items()
    }
    texts = dict(child_titles)
    texts["locked"] = "如果发现暗门并打开后，可以进入 5 号房间: 储物室。"

    repaired = materialize_source_structure_navigation(
        raw,
        source_texts=texts,
        source_section_paths=paths,
        source_scene_keys={source_id: "科比特的老房子" for source_id in paths},
    )

    assert tuple(item.to_location_id for item in repaired.location_links) == (
        "ordinary",
    )


def test_story_secret_mention_does_not_hide_an_ordinary_room() -> None:
    raw = contract(
        LocationSpec(location_id="house", title="科比特的老房子"),
        LocationSpec(
            location_id="bedroom",
            title="3 号房间: 空卧室",
            source_refs=(ref("heading"), ref("story")),
        ),
    )
    path = ("场景 9: 科比特的老房子", "二楼", "3 号房间: 空卧室")

    repaired = materialize_source_structure_navigation(
        raw,
        source_texts={
            "heading": "3 号房间: 空卧室",
            "story": "科比特想让调查员以为这里是整栋房子秘密的中心。",
        },
        source_section_paths={"heading": path, "story": path},
        source_scene_keys={"heading": "科比特的老房子", "story": "科比特的老房子"},
    )

    assert tuple(item.to_location_id for item in repaired.location_links) == (
        "bedroom",
    )


def test_access_relation_under_sibling_source_blocks_automatic_child_link() -> None:
    raw = contract(
        LocationSpec(location_id="house", title="科比特的老房子"),
        LocationSpec(
            location_id="room-2",
            title="房间 2: 空的储物柜",
            source_refs=(ref("room-2-heading"),),
        ),
    )
    basement = ("场景 9: 科比特的老房子", "地下室")

    repaired = materialize_source_structure_navigation(
        raw,
        source_texts={
            "room-2-heading": "房间 2: 空的储物柜",
            "room-1-wall": "粗略检查会发现木板后的空洞 (2 号和 3 号房间)。",
        },
        source_section_paths={
            "room-2-heading": (*basement, "房间 2: 空的储物柜"),
            "room-1-wall": (*basement, "1 号房间: 储藏室"),
        },
        source_scene_keys={
            "room-2-heading": "科比特的老房子",
            "room-1-wall": "科比特的老房子",
        },
    )

    assert repaired.location_links == ()


def test_same_named_parent_is_ambiguous_and_fails_closed() -> None:
    raw = contract(
        LocationSpec(location_id="house-a", title="House"),
        LocationSpec(location_id="house-b", title="House"),
        LocationSpec(location_id="room", title="Room 1", source_refs=(ref("room"),)),
        initial=None,
    )

    repaired = materialize_source_structure_navigation(
        raw,
        source_texts={"room": "Room 1"},
        source_section_paths={"room": ("Scene 1: House", "Room 1")},
        source_scene_keys={"room": "House"},
    )

    assert repaired.location_links == ()


def test_canonical_operators_are_runtime_executable_and_playability_reachable() -> None:
    raw = contract(
        LocationSpec(location_id="house", title="House"),
        LocationSpec(location_id="floor", title="First Floor", source_refs=(ref("floor"),)),
        LocationSpec(location_id="room", title="Room 1", source_refs=(ref("room"),)),
    )
    repaired = materialize_source_structure_navigation(
        raw,
        source_texts={"floor": "First Floor", "room": "Room 1"},
        source_section_paths={
            "floor": ("Scene 1: House", "First Floor"),
            "room": ("Scene 1: House", "First Floor", "Room 1"),
        },
        source_scene_keys={"floor": "House", "room": "House"},
    )
    canonical = materialize_location_travel_operators(repaired)

    report = ScenarioPlayabilityAnalyzer().analyze(canonical)
    assert report.proof("scene_reachability").status == "passed"
    outward = next(
        item
        for item in canonical.operators
        if item.success_commands[0].value == "floor"
        and item.preconditions[0].value == "house"
    )
    snapshot = ScenarioSnapshot(
        run_id="run",
        contract_id="contract",
        scenario_version=1,
        run_version=1,
        scene_id="house",
    )
    preview = ActionResolutionKernel.from_contract(canonical).preview(
        snapshot,
        ActionIntent(
            action_id="travel",
            actor_id="investigator",
            goal="Go upstairs",
            method="Walk",
            operator_id=outward.operator_id,
        ),
    )
    assert preview.allowed is True
    assert (
        ActionResolutionKernel.from_contract(canonical)
        .preflight(snapshot, preview.success_commands)
        .scene_id
        == "floor"
    )


def test_external_parent_location_can_anchor_incremental_child() -> None:
    raw = contract(
        LocationSpec(location_id="external-house", title="House"),
        LocationSpec(location_id="new-room", title="Room 2", source_refs=(ref("room"),)),
        initial="external-house",
    )

    repaired = materialize_source_structure_navigation(
        raw,
        source_texts={"room": "Room 2"},
        source_section_paths={"room": ("Scene 1: House", "Room 2")},
        source_scene_keys={"room": "House"},
    )

    assert repaired.location_links[0].from_location_id == "external-house"
    assert repaired.location_links[0].to_location_id == "new-room"


def test_scenario_ir_assembly_materializes_structure_before_canonical_travel() -> None:
    assembled = ScenarioIrAssembler().assemble(
        (
            ScenarioIrBatch.model_validate(
                {
                    "confidence": "high",
                    "initial_scene_id": "house",
                    "locations": [
                        {
                            "id": "house",
                            "title": "House",
                            "visibility": "visited",
                            "source_block_ids": ["house"],
                        },
                        {
                            "id": "floor",
                            "title": "First Floor",
                            "source_block_ids": ["floor"],
                        },
                        {
                            "id": "room",
                            "title": "Room 1",
                            "source_block_ids": ["room"],
                        },
                    ],
                }
            ),
        ),
        source_refs={source_id: ref(source_id) for source_id in ("house", "floor", "room")},
        source_texts={
            "house": "Scene 1: House",
            "floor": "First Floor",
            "room": "Room 1",
        },
        source_section_paths={
            "house": ("Scene 1: House",),
            "floor": ("Scene 1: House", "First Floor"),
            "room": ("Scene 1: House", "First Floor", "Room 1"),
        },
        source_scene_keys={source_id: "House" for source_id in ("house", "floor", "room")},
        contract_id="assembled",
        source_version=1,
        ruleset_id="coc7",
        title="House",
        corpus_truncated=False,
    )

    assert len(assembled.contract.location_links) == 2
    assert len(assembled.contract.operators) == 4
    assert (
        ScenarioPlayabilityAnalyzer()
        .analyze(assembled.contract)
        .proof("scene_reachability")
        .status
        == "passed"
    )

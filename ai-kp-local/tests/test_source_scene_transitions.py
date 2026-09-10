from __future__ import annotations

import pytest

from ai_kp.platform.resolution.contracts import (
    ActionIntent,
    ActionOperator,
    LocationSpec,
    ScenarioContract,
    ScenarioSnapshot,
    SkillChoice,
    SourceRef,
    StateCondition,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.playability import ScenarioPlayabilityAnalyzer
from ai_kp.platform.resolution.scenario_ir import ScenarioIrAssembler, ScenarioIrBatch
from ai_kp.platform.resolution.source_scene_transitions import (
    materialize_source_scene_transitions,
)
from ai_kp.platform.resolution.source_secret_passages import (
    materialize_source_secret_passages,
)


def ref(source_id: str) -> SourceRef:
    return SourceRef(source_block_id=source_id, document_id="document")


def location(location_id: str, title: str, source_id: str) -> LocationSpec:
    return LocationSpec(
        location_id=location_id,
        title=title,
        source_refs=(ref(source_id),),
    )


def contract(
    *,
    locations: tuple[LocationSpec, ...],
    operators: tuple[ActionOperator, ...] = (),
    initial_scene_id: str,
) -> ScenarioContract:
    return ScenarioContract(
        contract_id="source-transitions",
        source_version=1,
        ruleset_id="coc7",
        title="Source transitions",
        initial_scene_id=initial_scene_id,
        locations=locations,
        operators=operators,
    )


def room_paths() -> dict[str, tuple[str, ...]]:
    return {
        "room3": ("Scene 9: Old House", "Basement", "Room 3: Rat Lair"),
        "room4": ("Scene 9: Old House", "Basement", "Room 4: Hidden Body"),
        "bathroom": ("Scene 9: Old House", "First Floor", "Room 4: Bathroom"),
        "break-wall": (
            "Scene 9: Old House",
            "Basement",
            "Room 3: Rat Lair",
            "Swarm",
        ),
    }


def test_explicit_player_passage_is_one_way_scene_scoped_and_idempotent() -> None:
    source_id = "break-wall"
    base = contract(
        initial_scene_id="room3",
        locations=(
            location("room3", "Room 3: Rat Lair", "room3"),
            location("room4", "Room 4: Hidden Body", "room4"),
        ),
    )
    kwargs = {
        "source_refs": {source_id: ref(source_id)},
        "source_texts": {
            source_id: (
                "If the investigators break through the partition wall, "
                "they can enter Room 4."
            )
        },
        "source_section_paths": room_paths(),
        "source_scene_keys": {
            key: "Old House" for key in room_paths()
        },
    }

    repaired = materialize_source_scene_transitions(base, **kwargs)

    assert repaired.location_links == ()
    assert len(repaired.operators) == 1
    transition = repaired.operators[0]
    assert transition.preconditions == (
        StateCondition(path="scene_id", operator="eq", value="room3"),
    )
    assert transition.success_commands == (
        WorldCommand(kind="set_scene", value="room4"),
    )
    assert materialize_source_scene_transitions(repaired, **kwargs) == repaired

    kernel = ActionResolutionKernel.from_contract(repaired)
    intent = ActionIntent(
        action_id="break-wall",
        actor_id="investigator",
        goal="Enter the hidden room",
        method="Break the wall",
        operator_id=transition.operator_id,
    )
    room3 = ScenarioSnapshot(
        run_id="run",
        contract_id=base.contract_id,
        scenario_version=1,
        run_version=1,
        scene_id="room3",
    )
    assert kernel.preview(room3, intent).allowed is True
    assert kernel.preview(
        room3.model_copy(update={"scene_id": "room4"}), intent
    ).allowed is False
    assert kernel.preflight(
        room3, kernel.preview(room3, intent).success_commands
    ).scene_id == "room4"
    assert (
        ScenarioPlayabilityAnalyzer()
        .analyze(repaired)
        .proof("scene_reachability")
        .status
        == "passed"
    )


def test_reserved_passage_id_conflict_fails_closed() -> None:
    source_id = "break-wall"
    base = contract(
        initial_scene_id="room3",
        locations=(
            location("room3", "Room 3: Rat Lair", "room3"),
            location("room4", "Room 4: Hidden Body", "room4"),
        ),
    )
    kwargs = {
        "source_refs": {source_id: ref(source_id)},
        "source_texts": {
            source_id: (
                "If the investigators break through the partition wall, "
                "they can enter Room 4."
            )
        },
        "source_section_paths": room_paths(),
        "source_scene_keys": {key: "Old House" for key in room_paths()},
    }
    generated = materialize_source_scene_transitions(base, **kwargs).operators[0]
    poisoned = base.model_copy(update={
        "operators": (
            generated.model_copy(update={"title": "Model-owned collision"}),
        )
    })

    with pytest.raises(ValueError, match="reserved id conflicts"):
        materialize_source_scene_transitions(poisoned, **kwargs)


def test_explicit_chinese_player_passage_uses_numbered_room_alias() -> None:
    source_id = "break-wall"
    base = contract(
        initial_scene_id="room3",
        locations=(
            location("room3", "房间3：鼠群巢穴", "room3"),
            location("room4", "房间4：隐藏的尸体", "room4"),
        ),
    )

    repaired = materialize_source_scene_transitions(
        base,
        source_refs={source_id: ref(source_id)},
        source_texts={
            source_id: "如果调查员打穿隔墙，他们就会来到房间4。",
        },
        source_section_paths={
            "room3": ("老宅", "地下室", "房间3：鼠群巢穴"),
            "room4": ("老宅", "地下室", "房间4：隐藏的尸体"),
            source_id: ("老宅", "地下室", "房间3：鼠群巢穴", "鼠群"),
        },
        source_scene_keys={
            "room3": "老宅",
            "room4": "老宅",
            source_id: "老宅",
        },
    )

    assert tuple(
        command
        for operator in repaired.operators
        for command in operator.success_commands
    ) == (WorldCommand(kind="set_scene", value="room4"),)


@pytest.mark.parametrize(
    "text",
    [
        "If the wall breaks, the investigators can enter Room 4.",
        "If the investigators inspect the wall, Room 4 is nearby.",
        "The investigators break through the wall and enter Room 4.",
        "If the investigators break through the wall, they can enter Room 5.",
    ],
)
def test_passage_without_exact_actor_condition_and_relation_fails_closed(
    text: str,
) -> None:
    source_id = "break-wall"
    base = contract(
        initial_scene_id="room3",
        locations=(
            location("room3", "Room 3: Rat Lair", "room3"),
            location("room4", "Room 4: Hidden Body", "room4"),
        ),
    )

    assert materialize_source_scene_transitions(
        base,
        source_refs={source_id: ref(source_id)},
        source_texts={source_id: text},
        source_section_paths=room_paths(),
        source_scene_keys={key: "Old House" for key in room_paths()},
    ) == base


def test_passage_ambiguous_or_in_a_different_container_fails_closed() -> None:
    source_id = "break-wall"
    text = (
        "If the investigators break through the partition wall, "
        "they can enter Room 4."
    )
    ambiguous = contract(
        initial_scene_id="room3",
        locations=(
            location("room3", "Room 3: Rat Lair", "room3"),
            location("room4-a", "Room 4: Hidden Body", "room4"),
            location("room4-b", "Room 4: Other Chamber", "room4-other"),
        ),
    )
    paths = {
        **room_paths(),
        "room4-other": (
            "Scene 9: Old House", "Basement", "Room 4: Other Chamber",
        ),
    }
    different_container = contract(
        initial_scene_id="room3",
        locations=(
            location("room3", "Room 3: Rat Lair", "room3"),
            location("bathroom", "Room 4: Bathroom", "bathroom"),
        ),
    )
    kwargs = {
        "source_refs": {source_id: ref(source_id)},
        "source_texts": {source_id: text},
        "source_scene_keys": {key: "Old House" for key in paths},
    }

    assert materialize_source_scene_transitions(
        ambiguous, source_section_paths=paths, **kwargs
    ) == ambiguous
    assert materialize_source_scene_transitions(
        different_container, source_section_paths=paths, **kwargs
    ) == different_container


def chapel_contract() -> ScenarioContract:
    source_id = "ruins-checks"
    scene = StateCondition(path="scene_id", operator="eq", value="ruins")
    return contract(
        initial_scene_id="ruins",
        locations=(
            location("ruins", "Contemplation Chapel", "ruins"),
            location("basement", "Contemplation Chapel Basement", "basement"),
        ),
        operators=(
            ActionOperator(
                operator_id="luck-check",
                title="Luck Check",
                policy="required_check",
                preconditions=(scene,),
                skill_choices=(
                    SkillChoice(skill_key="coc7.luck", reason="Test the floor."),
                ),
                source_refs=(ref(source_id),),
            ),
            ActionOperator(
                operator_id="jump-check",
                title="Jump Check",
                policy="required_check",
                preconditions=(scene,),
                skill_choices=(
                    SkillChoice(skill_key="coc7.jump", reason="Reach safety."),
                ),
                failure_commands=(
                    WorldCommand(
                        kind="set_fact", path="chapel.fall_damage", value=True
                    ),
                ),
                source_refs=(ref(source_id),),
            ),
        ),
    )


def chapel_paths() -> dict[str, tuple[str, ...]]:
    scene = "Scene 8: Contemplation Chapel"
    return {
        "ruins": (scene,),
        "basement": (scene, "If an investigator falls"),
        "ruins-checks": (scene, "Explore the ruins"),
    }


def test_checked_failure_appends_scene_change_and_preserves_existing_effects() -> None:
    source_id = "ruins-checks"
    base = chapel_contract()
    text = (
        "Investigators who fail the [Luck] check must attempt a [Jump] check "
        "to reach safety; otherwise they fall into the Basement."
    )
    kwargs = {
        "source_refs": {source_id: ref(source_id)},
        "source_texts": {source_id: text},
        "source_section_paths": chapel_paths(),
        "source_scene_keys": {
            key: "Contemplation Chapel" for key in chapel_paths()
        },
    }

    repaired = materialize_source_scene_transitions(base, **kwargs)

    luck = next(item for item in repaired.operators if item.operator_id == "luck-check")
    jump = next(item for item in repaired.operators if item.operator_id == "jump-check")
    assert luck.failure_commands == ()
    assert jump.failure_commands == (
        WorldCommand(kind="set_fact", path="chapel.fall_damage", value=True),
        WorldCommand(kind="set_scene", value="basement"),
    )
    assert materialize_source_scene_transitions(repaired, **kwargs) == repaired
    kernel = ActionResolutionKernel.from_contract(repaired)
    snapshot = ScenarioSnapshot(
        run_id="run",
        contract_id=base.contract_id,
        scenario_version=1,
        run_version=1,
        scene_id="ruins",
    )
    fallen = kernel.preflight(snapshot, jump.failure_commands)
    assert fallen.scene_id == "basement"
    assert fallen.facts["chapel"]["fall_damage"] is True
    assert (
        ScenarioPlayabilityAnalyzer()
        .analyze(repaired)
        .proof("scene_reachability")
        .status
        == "passed"
    )


def test_checked_failure_requires_exact_source_check_and_scene_container() -> None:
    source_id = "ruins-checks"
    text = "Otherwise they fall into the Basement."
    base = chapel_contract()
    wrong_source = base.model_copy(update={
        "operators": tuple(
            item.model_copy(update={"source_refs": (ref("other"),)})
            for item in base.operators
        )
    })
    ambiguous_destination = base.model_copy(update={
        "locations": (
            *base.locations,
            location("other-basement", "Contemplation Chapel Basement", "other-basement"),
        )
    })
    paths = {
        **chapel_paths(),
        "other": ("Other Scene",),
        "other-basement": (
            "Scene 8: Contemplation Chapel", "Other Basement",
        ),
    }
    kwargs = {
        "source_refs": {source_id: ref(source_id)},
        "source_texts": {source_id: text},
        "source_section_paths": paths,
        "source_scene_keys": {
            **{key: "Contemplation Chapel" for key in chapel_paths()},
            "other": "Other Scene",
            "other-basement": "Contemplation Chapel",
        },
    }

    assert materialize_source_scene_transitions(wrong_source, **kwargs) == wrong_source
    assert (
        materialize_source_scene_transitions(ambiguous_destination, **kwargs)
        == ambiguous_destination
    )


def test_broad_otherwise_without_named_check_does_not_mutate_unique_branch() -> None:
    source_id = "ruins-checks"
    base = chapel_contract()

    repaired = materialize_source_scene_transitions(
        base,
        source_refs={source_id: ref(source_id)},
        source_texts={source_id: "Otherwise they fall into the Basement."},
        source_section_paths=chapel_paths(),
        source_scene_keys={
            key: "Contemplation Chapel" for key in chapel_paths()
        },
    )

    assert repaired == base


def test_scenario_ir_assembles_source_passage_before_canonical_travel() -> None:
    batch = ScenarioIrBatch.model_validate({
        "initial_scene_id": "room3",
        "locations": [{
            "id": "room3", "title": "Room 3: Rat Lair",
            "visibility": "visited", "source_block_ids": ["room3"],
        }, {
            "id": "room4", "title": "Room 4: Hidden Body",
            "source_block_ids": ["room4"],
        }],
    })
    source_ids = ("room3", "room4", "break-wall")
    paths = room_paths()
    result = ScenarioIrAssembler().assemble(
        (batch,),
        source_refs={source_id: ref(source_id) for source_id in source_ids},
        source_texts={
            "room3": "The rat lair.",
            "room4": "The hidden body.",
            "break-wall": (
                "If the investigators break through the partition wall, "
                "they can enter Room 4."
            ),
        },
        source_titles={
            "room3": "Room 3: Rat Lair",
            "room4": "Room 4: Hidden Body",
            "break-wall": "Swarm",
        },
        source_section_paths={key: paths[key] for key in source_ids},
        source_scene_keys={source_id: "Old House" for source_id in source_ids},
        contract_id="ir-source-transition",
        source_version=1,
        ruleset_id="coc7",
        title="IR source transition",
        corpus_truncated=False,
    )

    assert result.contract.location_links == ()
    assert len(result.contract.operators) == 1
    assert result.contract.operators[0].success_commands == (
        WorldCommand(kind="set_scene", value="room4"),
    )


def secret_chain_paths() -> dict[str, tuple[str, ...]]:
    basement = ("场景 9: 老宅", "地下室")
    return {
        "origin": (*basement, "1 号房间: 储藏室"),
        "discovery": (*basement, "1 号房间: 储藏室", "墙板"),
        "decoy": (*basement, "房间 2: 煤柜"),
        "destination": (*basement, "房间 3: 隐蔽空间"),
        "exposure": (*basement, "房间 3: 隐蔽空间", "入口"),
    }


def test_source_secret_passage_requires_discovery_before_hidden_transition() -> None:
    paths = secret_chain_paths()
    base = contract(
        initial_scene_id="origin",
        locations=(
            location("origin", "1 号房间: 储藏室", "origin"),
            location("destination", "房间 3: 隐蔽空间", "destination"),
        ),
    )
    source_ids = tuple(paths)
    kwargs = {
        "source_refs": {source_id: ref(source_id) for source_id in source_ids},
        "source_texts": {
            "origin": "储藏室。",
            "discovery": "一次粗略的检查就会发现木板后的空洞（3 号房间）。",
            "decoy": "煤柜。",
            "destination": "隐蔽空间。",
            "exposure": "如果墙壁被破坏或者移除，就会暴露出墙壁夹层里的狭窄空间。",
        },
        "source_section_paths": paths,
        "source_scene_keys": {source_id: "老宅" for source_id in source_ids},
    }

    repaired = materialize_source_secret_passages(base, **kwargs)

    assert repaired.location_links == ()
    assert tuple(item.initial_visibility for item in repaired.locations) == (
        "hidden",
        "hidden",
    )
    assert len(repaired.operators) == 2
    discovery = next(
        item
        for item in repaired.operators
        if item.success_commands[0].kind == "set_fact"
    )
    transition = next(
        item
        for item in repaired.operators
        if item.success_commands[0].kind == "set_scene"
    )
    fact = discovery.success_commands[0].path
    assert fact is not None and fact.startswith("source_scene_passages.")
    assert transition.preconditions == (
        StateCondition(path="scene_id", operator="eq", value="origin"),
        StateCondition(path=f"facts.{fact}", operator="eq", value=True),
    )
    assert transition.success_commands == (
        WorldCommand(kind="set_scene", value="destination"),
    )
    public_action_text = " ".join(
        (
            transition.title,
            transition.public_setup,
            *transition.intent_hints,
        )
    )
    assert "房间 3" not in public_action_text
    assert "隐蔽空间" not in public_action_text

    kernel = ActionResolutionKernel.from_contract(repaired)
    snapshot = ScenarioSnapshot(
        run_id="run",
        contract_id=base.contract_id,
        scenario_version=1,
        run_version=1,
        scene_id="origin",
    )
    transition_intent = ActionIntent(
        action_id="open",
        actor_id="investigator",
        goal="Open the discovered route",
        operator_id=transition.operator_id,
    )
    assert kernel.preview(snapshot, transition_intent).allowed is False
    discovered = kernel.preflight(snapshot, discovery.success_commands)
    assert kernel.preview(discovered, transition_intent).allowed is True
    entered = kernel.preflight(discovered, transition.success_commands)
    assert entered.scene_id == "destination"
    assert materialize_source_secret_passages(repaired, **kwargs) == repaired


def test_source_secret_passage_reuses_one_direct_authored_discovery_fact() -> None:
    paths = secret_chain_paths()
    scene = StateCondition(path="scene_id", operator="eq", value="origin")
    authored = ActionOperator(
        operator_id="inspect-wall",
        title="Inspect the wall",
        policy="required_check",
        preconditions=(scene,),
        skill_choices=(
            SkillChoice(
                skill_key="coc7.spot_hidden",
                reason="Inspect the boards.",
                failure_stakes="The opening remains undiscovered.",
            ),
        ),
        success_commands=(
            WorldCommand(
                kind="set_fact",
                path="action_goals.inspect-wall.achieved",
                value=True,
            ),
        ),
        source_refs=(ref("discovery"),),
    )
    base = contract(
        initial_scene_id="origin",
        locations=(
            location("origin", "1 号房间: 储藏室", "origin"),
            location("destination", "房间 3: 隐蔽空间", "destination"),
        ),
        operators=(authored,),
    )

    repaired = materialize_source_secret_passages(
        base,
        source_refs={source_id: ref(source_id) for source_id in paths},
        source_texts={
            "discovery": (
                "A search discovers an opening behind the wall leading toward Room 3."
            ),
            "exposure": (
                "When the wall is removed, it reveals a narrow space in Room 3."
            ),
        },
        source_section_paths=paths,
        source_scene_keys={source_id: "Old House" for source_id in paths},
    )

    assert len(repaired.operators) == 2
    transition = repaired.operators[-1]
    assert transition.preconditions[-1] == StateCondition(
        path="facts.action_goals.inspect-wall.achieved",
        operator="eq",
        value=True,
    )


def test_exact_source_arbitrary_fact_cannot_unlock_secret_passage() -> None:
    paths = secret_chain_paths()
    unrelated = ActionOperator(
        operator_id="inspect-crates",
        title="Inspect crates",
        policy="automatic",
        preconditions=(
            StateCondition(path="scene_id", operator="eq", value="origin"),
        ),
        success_commands=(
            WorldCommand(kind="set_fact", path="house.crates_checked", value=True),
        ),
        source_refs=(ref("discovery"),),
    )
    base = contract(
        initial_scene_id="origin",
        locations=(
            location("origin", "1 号房间: 储藏室", "origin"),
            location("destination", "房间 3: 隐蔽空间", "destination"),
        ),
        operators=(unrelated,),
    )

    repaired = materialize_source_secret_passages(
        base,
        source_refs={source_id: ref(source_id) for source_id in paths},
        source_texts={
            "discovery": "A successful search discovers an opening behind the wall to Room 3.",
            "exposure": "When the wall is removed, it reveals a narrow space in Room 3.",
        },
        source_section_paths=paths,
        source_scene_keys={source_id: "Old House" for source_id in paths},
    )

    assert repaired == base


def test_secret_passage_ambiguous_destination_exposure_fails_closed() -> None:
    paths = secret_chain_paths()
    paths["other-exposure"] = paths["decoy"] + ("入口",)
    base = contract(
        initial_scene_id="origin",
        locations=(
            location("origin", "1 号房间: 储藏室", "origin"),
            location("decoy", "房间 2: 煤柜", "decoy"),
            location("destination", "房间 3: 隐蔽空间", "destination"),
        ),
    )
    refs = {source_id: ref(source_id) for source_id in paths}

    repaired = materialize_source_secret_passages(
        base,
        source_refs=refs,
        source_texts={
            "discovery": "一次粗略检查会发现木板后的空洞（2 号和 3 号房间）。",
            "exposure": "墙壁被移除会暴露出墙壁夹层里的狭窄空间。",
            "other-exposure": "木板被移除会露出后面的房间。",
        },
        source_section_paths=paths,
        source_scene_keys={source_id: "老宅" for source_id in paths},
    )

    assert repaired == base


def test_malformed_weak_discovery_output_uses_only_safe_source_fallback() -> None:
    paths = secret_chain_paths()
    malformed = ActionOperator(
        operator_id="weak-inspection",
        title="Inspect",
        policy="automatic",
        # The weak output lacks a scene scope and asserts only false. It cannot
        # become the authority fact for the secret transition.
        success_commands=(
            WorldCommand(kind="set_fact", path="invented.secret", value=False),
        ),
        source_refs=(ref("discovery"),),
    )
    base = contract(
        initial_scene_id="origin",
        locations=(
            location("origin", "1 号房间: 储藏室", "origin"),
            location("destination", "房间 3: 隐蔽空间", "destination"),
        ),
        operators=(malformed,),
    )

    repaired = materialize_source_secret_passages(
        base,
        source_refs={source_id: ref(source_id) for source_id in paths},
        source_texts={
            "discovery": "粗略检查就会发现墙后空洞（3 号房间）。",
            "exposure": "墙壁被破坏会暴露出夹层里的空间。",
        },
        source_section_paths=paths,
        source_scene_keys={source_id: "老宅" for source_id in paths},
    )

    generated_fact_commands = tuple(
        command
        for operator in repaired.operators
        if operator.operator_id != malformed.operator_id
        for command in operator.success_commands
        if command.kind == "set_fact"
    )
    assert len(generated_fact_commands) == 1
    assert generated_fact_commands[0].value is True
    assert generated_fact_commands[0].path != "invented.secret"


def test_unrelated_same_section_fact_cannot_unlock_secret_passage() -> None:
    paths = secret_chain_paths()
    paths["unrelated"] = paths["discovery"]
    unrelated = ActionOperator(
        operator_id="inspect-crates",
        title="Inspect crates",
        policy="automatic",
        preconditions=(
            StateCondition(path="scene_id", operator="eq", value="origin"),
        ),
        success_commands=(
            WorldCommand(kind="set_fact", path="house.crates_checked", value=True),
        ),
        source_refs=(ref("unrelated"),),
    )
    base = contract(
        initial_scene_id="origin",
        locations=(
            location("origin", "1 号房间: 储藏室", "origin"),
            location("destination", "房间 3: 隐蔽空间", "destination"),
        ),
        operators=(unrelated,),
    )

    repaired = materialize_source_secret_passages(
        base,
        source_refs={source_id: ref(source_id) for source_id in paths},
        source_texts={
            # A real check is required, so deterministic automatic fallback is
            # forbidden; only an exact-source producer could authorize it.
            "discovery": "A successful search discovers an opening behind the wall to Room 3.",
            "unrelated": "A search of the crates reveals old tools.",
            "exposure": "When the wall is removed, it reveals a narrow space in Room 3.",
        },
        source_section_paths=paths,
        source_scene_keys={source_id: "Old House" for source_id in paths},
    )

    assert repaired == base

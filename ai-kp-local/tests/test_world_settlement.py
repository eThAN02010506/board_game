from __future__ import annotations

import pytest

from ai_kp.platform.resolution import causal_validation
from ai_kp.platform.resolution import world_settlement as world_settlement_module
from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    ClockSpec,
    EndingRule,
    EntitySpec,
    LocationSpec,
    PressureStage,
    PressureTrackSpec,
    ReactivePolicy,
    ReactiveRule,
    ResourceSpec,
    ScenarioContract,
    SourceRef,
    StateCondition,
    TriggerRule,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.playability import ScenarioPlayabilityAnalyzer
from ai_kp.platform.resolution.reactive import ReactiveEvent, ReactivePolicyEngine
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.platform.resolution.world_settlement import (
    clear_world_settlement_cache,
    settle_world_commands,
)


def causal_contract() -> ScenarioContract:
    return ScenarioContract(
        contract_id="causal-settlement",
        source_version=1,
        ruleset_id="coc7",
        title="Causal settlement",
        initial_scene_id="entry",
        locations=(
            LocationSpec(location_id="entry", title="Entry"),
            LocationSpec(location_id="hall", title="Hall"),
        ),
        clocks=(ClockSpec(clock_id="pressure", title="Pressure", maximum_value=10),),
        entities=(
            EntitySpec(entity_id="after", entity_type="hazard", title="After"),
            EntitySpec(entity_id="clock", entity_type="hazard", title="Clock"),
            EntitySpec(entity_id="scene", entity_type="hazard", title="Scene"),
        ),
        operators=(
            ActionOperator(
                operator_id="start",
                title="Start",
                policy="automatic",
                success_commands=(
                    WorldCommand(kind="set_fact", path="action.done", value=True),
                ),
            ),
        ),
        reactive_policies=(
            ReactivePolicy(
                policy_id="after-policy",
                entity_id="after",
                rules=(
                    ReactiveRule(
                        rule_id="raise-pressure",
                        trigger="after_action",
                        commands=(
                            WorldCommand(
                                kind="advance_clock", clock_id="pressure", delta=1
                            ),
                            WorldCommand(
                                kind="set_fact", path="after.ran", value=True
                            ),
                        ),
                    ),
                ),
            ),
            ReactivePolicy(
                policy_id="clock-policy",
                entity_id="clock",
                rules=(
                    ReactiveRule(
                        rule_id="enter-hall",
                        trigger="clock_advanced",
                        conditions=(
                            StateCondition(
                                path="facts.clock.ran", operator="not_exists"
                            ),
                        ),
                        commands=(
                            WorldCommand(kind="set_scene", value="hall"),
                            WorldCommand(
                                kind="set_fact", path="clock.ran", value=True
                            ),
                        ),
                    ),
                ),
            ),
            ReactivePolicy(
                policy_id="scene-policy",
                entity_id="scene",
                rules=(
                    ReactiveRule(
                        rule_id="record-entry",
                        trigger="scene_entered",
                        conditions=(
                            StateCondition(path="scene_id", operator="eq", value="hall"),
                        ),
                        commands=(
                            WorldCommand(
                                kind="set_fact", path="scene.ran", value=True
                            ),
                        ),
                    ),
                ),
            ),
        ),
        endings=(
            EndingRule(
                ending_id="action-finished",
                title="Action finished",
                all_conditions=(
                    StateCondition(
                        path="facts.action.done", operator="eq", value=True
                    ),
                ),
            ),
        ),
    )


def test_causal_reactions_settle_before_one_final_ending() -> None:
    contract = causal_contract()
    starting = contract.initial_snapshot("run-causal")

    commands, resulting = settle_world_commands(
        contract,
        starting,
        (WorldCommand(kind="set_fact", path="action.done", value=True),),
    )

    assert resulting.status == "completed"
    assert resulting.ending_id == "action-finished"
    assert resulting.run_version == starting.run_version + 1
    assert resulting.clocks["pressure"] == 1
    assert resulting.scene_id == "hall"
    assert resulting.facts == {
        "action": {"done": True},
        "after": {"ran": True},
        "clock": {"ran": True},
        "scene": {"ran": True},
    }
    assert [command.kind for command in commands] == [
        "set_fact",
        "advance_clock",
        "set_fact",
        "set_scene",
        "set_fact",
        "set_fact",
    ]


def test_playability_and_runtime_share_causal_settlement() -> None:
    contract = causal_contract()
    report = ScenarioPlayabilityAnalyzer().analyze(contract)

    assert report.proof("ending_reachability").status == "passed"
    assert any("start.success" in item for item in report.proof("ending_reachability").witness)


def test_playability_retains_pressure_clock_until_repeated_action_reaches_stage() -> None:
    contract = ScenarioContract(
        contract_id="pressure-projection",
        source_version=1,
        ruleset_id="coc7",
        title="Pressure projection",
        clocks=(ClockSpec(clock_id="pressure", title="Pressure", maximum_value=3),),
        operators=(
            ActionOperator(
                operator_id="wait",
                title="Wait",
                policy="automatic",
                success_commands=(
                    WorldCommand(kind="advance_clock", clock_id="pressure", delta=1),
                ),
            ),
        ),
        pressure_tracks=(
            PressureTrackSpec(
                pressure_id="danger",
                title="Danger",
                clock_id="pressure",
                stages=(
                    PressureStage(
                        stage_id="arrived",
                        threshold=2,
                        public_label="Danger arrives",
                        commands=(
                            WorldCommand(
                                kind="set_fact", path="danger.arrived", value=True
                            ),
                        ),
                    ),
                ),
            ),
        ),
        endings=(
            EndingRule(
                ending_id="danger-arrived",
                title="Danger arrived",
                all_conditions=(
                    StateCondition(
                        path="facts.danger.arrived", operator="eq", value=True
                    ),
                ),
            ),
        ),
    )

    proof = ScenarioPlayabilityAnalyzer().analyze(contract).proof(
        "ending_reachability"
    )

    assert proof.status == "passed"
    assert any("wait.success -> wait.success" in item for item in proof.witness)


def test_reactive_cycle_fails_without_mutating_starting_snapshot() -> None:
    contract = ScenarioContract(
        contract_id="bounded-cycle",
        source_version=1,
        ruleset_id="coc7",
        title="Bounded cycle",
        clocks=(ClockSpec(clock_id="loop", title="Loop", maximum_value=1000),),
        entities=(EntitySpec(entity_id="loop", entity_type="hazard", title="Loop"),),
        reactive_policies=(
            ReactivePolicy(
                policy_id="loop-policy",
                entity_id="loop",
                rules=(
                    ReactiveRule(
                        rule_id="advance-again",
                        trigger="clock_advanced",
                        commands=(
                            WorldCommand(kind="advance_clock", clock_id="loop", delta=1),
                        ),
                    ),
                ),
            ),
        ),
    )
    starting = contract.initial_snapshot("run-loop")

    with pytest.raises(ValueError, match="bounded event limit"):
        settle_world_commands(
            contract,
            starting,
            (WorldCommand(kind="advance_clock", clock_id="loop", delta=1),),
        )

    assert starting.clocks["loop"] == 0
    assert starting.run_version == 0


def test_semantic_reactive_and_background_tick_fail_compilation_closed() -> None:
    contract = ScenarioContract(
        contract_id="unsupported-reactive",
        source_version=1,
        ruleset_id="coc7",
        title="Unsupported reactive",
        entities=(EntitySpec(entity_id="npc", entity_type="npc", title="NPC"),),
        reactive_policies=(
            ReactivePolicy(
                policy_id="unsupported",
                entity_id="npc",
                rules=(
                    ReactiveRule(
                        rule_id="semantic",
                        trigger="semantic_event",
                        event_type="entity_contacted",
                        commands=(WorldCommand(kind="set_fact", path="bad", value=True),),
                    ),
                    ReactiveRule(
                        rule_id="tick",
                        trigger="background_tick",
                        commands=(WorldCommand(kind="set_fact", path="bad", value=True),),
                    ),
                ),
            ),
        ),
    )

    result = ScenarioContractCompiler().compile(contract)
    codes = {issue.code for issue in result.report.issues}

    assert result.report.valid is False
    assert "semantic_reactive_trigger_unsupported" in codes
    assert "background_tick_runtime_unavailable" in codes
    with pytest.raises(ValueError, match="authoritative TriggerRule"):
        ReactivePolicyEngine(contract).evaluate(
            contract.initial_snapshot("run-unsupported"),
            ReactiveEvent(event_id="semantic-1", trigger="semantic_event"),
        )


def test_world_expansion_semantic_event_uses_trigger_rule_without_after_action() -> None:
    contract = ScenarioContract(
        contract_id="overlay-trigger",
        source_version=2,
        ruleset_id="coc7",
        title="Overlay trigger",
        clocks=(ClockSpec(clock_id="overlay-clock", title="Overlay clock", maximum_value=3),),
        entities=(
            EntitySpec(entity_id="overlay-watch", entity_type="hazard", title="Watch"),
        ),
        reactive_policies=(
            ReactivePolicy(
                policy_id="overlay-clock-policy",
                entity_id="overlay-watch",
                rules=(
                    ReactiveRule(
                        rule_id="observe-overlay-clock",
                        trigger="clock_advanced",
                        commands=(
                            WorldCommand(
                                kind="set_fact", path="overlay.clock_reacted", value=True
                            ),
                        ),
                    ),
                ),
            ),
        ),
        trigger_rules=(
            TriggerRule(
                trigger_id="overlay-activated",
                event_type="contract_overlay_activated",
                commands=(
                    WorldCommand(kind="set_fact", path="overlay.ready", value=True),
                    WorldCommand(
                        kind="advance_clock", clock_id="overlay-clock", delta=1
                    ),
                ),
            ),
        ),
    )
    predecessor = contract.model_copy(update={"source_version": 1, "trigger_rules": ()})
    starting = predecessor.initial_snapshot("run-overlay")

    persisted_commands, resulting = settle_world_commands(
        contract,
        starting,
        (
            WorldCommand(
                kind="activate_contract_overlay",
                value="a" * 64,
                payload={"source_version": 2},
            ),
        ),
        cause="world_expansion",
    )

    assert resulting.facts["overlay"]["ready"] is True
    assert resulting.facts["overlay"]["clock_reacted"] is True
    assert resulting.clocks["overlay-clock"] == 1
    assert [event["type"] for event in resulting.events] == [
        "contract_overlay_activated",
        "trigger_fired",
    ]
    assert resulting.scenario_version == 2
    assert ActionResolutionKernel.from_contract(contract).preflight(
        starting, persisted_commands
    ) == resulting


def repeatable_ping_contract() -> ScenarioContract:
    source = SourceRef(source_block_id="block-1", document_id="document-1")
    return ScenarioContract(
        contract_id="repeatable-ping",
        source_version=1,
        ruleset_id="coc7",
        title="Repeatable ping",
        operators=(
            ActionOperator(
                operator_id="ping",
                title="Ping",
                policy="automatic",
                success_commands=(
                    WorldCommand(kind="emit_event", event_type="ping"),
                ),
                source_refs=(source,),
            ),
            ActionOperator(
                operator_id="finish",
                title="Finish",
                policy="automatic",
                preconditions=(
                    StateCondition(path="facts.pong", operator="eq", value=True),
                ),
                success_commands=(
                    WorldCommand(kind="set_fact", path="done", value=True),
                ),
                source_refs=(source,),
            ),
        ),
        trigger_rules=(
            TriggerRule(
                trigger_id="repeatable-pong",
                event_type="ping",
                once_per_run=False,
                commands=(WorldCommand(kind="set_fact", path="pong", value=True),),
                source_refs=(source,),
            ),
        ),
        endings=(
            EndingRule(
                ending_id="finished",
                title="Finished",
                all_conditions=(
                    StateCondition(path="facts.done", operator="eq", value=True),
                ),
                source_refs=(source,),
            ),
        ),
    )


def test_repeatable_trigger_markers_do_not_make_playability_unbounded() -> None:
    result = ScenarioContractCompiler().compile(repeatable_ping_contract())

    assert result.report.playability.exploration_complete is True
    assert result.report.playability.explored_state_count < 10
    assert result.report.playability.proof("ending_reachability").status == "passed"
    assert result.report.release_ready is True


def test_state_key_uses_set_like_future_eligibility_markers_only() -> None:
    contract = repeatable_ping_contract().model_copy(
        update={
            "trigger_rules": (
                *repeatable_ping_contract().trigger_rules,
                TriggerRule(
                    trigger_id="once-only",
                    event_type="once",
                    once_per_run=True,
                    commands=(
                        WorldCommand(kind="set_fact", path="once", value=True),
                    ),
                ),
            ),
        }
    )
    analyzer = ScenarioPlayabilityAnalyzer()
    kernel = ActionResolutionKernel.from_contract(contract)
    relevant_paths = analyzer._relevant_state_paths(contract)
    once_ids = frozenset({"once-only"})
    base = contract.initial_snapshot("run-marker-key")
    once_marker = {"type": "trigger_fired", "payload": {"trigger_id": "once-only"}}
    repeat_marker = {
        "type": "trigger_fired",
        "payload": {"trigger_id": "repeatable-pong", "source_event_index": 1},
    }
    pressure_marker = {
        "type": "pressure_stage_reached",
        "payload": {"pressure_stage_id": "danger:warning", "value": 2},
    }
    first = base.model_copy(
        update={"events": (once_marker, repeat_marker, pressure_marker)}
    )
    repeated = base.model_copy(
        update={
            "events": (
                once_marker,
                once_marker,
                repeat_marker,
                repeat_marker,
                pressure_marker,
                pressure_marker,
            )
        }
    )
    missing_once = base.model_copy(
        update={"events": (repeat_marker, repeat_marker, pressure_marker)}
    )

    first_key = analyzer._state_key(first, relevant_paths, kernel, once_ids)
    repeated_key = analyzer._state_key(repeated, relevant_paths, kernel, once_ids)
    missing_once_key = analyzer._state_key(
        missing_once, relevant_paths, kernel, once_ids
    )

    assert first_key == repeated_key
    assert first_key != missing_once_key


def test_two_clocks_changed_in_one_wave_activate_policy_once() -> None:
    contract = ScenarioContract(
        contract_id="two-clock-wave",
        source_version=1,
        ruleset_id="coc7",
        title="Two clock wave",
        clocks=(
            ClockSpec(clock_id="first", title="First", maximum_value=3),
            ClockSpec(clock_id="second", title="Second", maximum_value=3),
        ),
        resources=(
            ResourceSpec(
                resource_id="reaction_count",
                title="Reaction count",
                maximum_value=10,
            ),
        ),
        entities=(
            EntitySpec(entity_id="watcher", entity_type="hazard", title="Watcher"),
        ),
        reactive_policies=(
            ReactivePolicy(
                policy_id="clock-watcher",
                entity_id="watcher",
                rules=(
                    ReactiveRule(
                        rule_id="count-wave",
                        trigger="clock_advanced",
                        commands=(
                            WorldCommand(
                                kind="adjust_resource",
                                path="reaction_count",
                                delta=1,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    commands, resulting = settle_world_commands(
        contract,
        contract.initial_snapshot("run-two-clock-wave"),
        (
            WorldCommand(kind="advance_clock", clock_id="first", delta=1),
            WorldCommand(kind="advance_clock", clock_id="second", delta=1),
        ),
    )

    assert resulting.resources["reaction_count"] == 1
    assert sum(command.kind == "adjust_resource" for command in commands) == 1


def test_scene_and_multiple_actor_moves_in_one_wave_activate_policy_once() -> None:
    contract = ScenarioContract(
        contract_id="multi-scene-wave",
        source_version=1,
        ruleset_id="coc7",
        title="Multi scene wave",
        initial_scene_id="entry",
        locations=(
            LocationSpec(location_id="entry", title="Entry"),
            LocationSpec(location_id="hall", title="Hall"),
        ),
        resources=(
            ResourceSpec(
                resource_id="reaction_count",
                title="Reaction count",
                maximum_value=10,
            ),
        ),
        entities=(
            EntitySpec(entity_id="watcher", entity_type="hazard", title="Watcher"),
        ),
        reactive_policies=(
            ReactivePolicy(
                policy_id="scene-watcher",
                entity_id="watcher",
                rules=(
                    ReactiveRule(
                        rule_id="count-wave",
                        trigger="scene_entered",
                        commands=(
                            WorldCommand(
                                kind="adjust_resource",
                                path="reaction_count",
                                delta=1,
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    starting = contract.initial_snapshot(
        "run-multi-scene-wave",
        actor_locations={"first": "entry", "second": "entry"},
    )

    commands, resulting = settle_world_commands(
        contract,
        starting,
        (
            WorldCommand(kind="set_scene", value="hall"),
            WorldCommand(kind="move_actor", actor_id="first", value="hall"),
            WorldCommand(kind="move_actor", actor_id="second", value="hall"),
        ),
    )

    assert resulting.resources["reaction_count"] == 1
    assert sum(command.kind == "adjust_resource" for command in commands) == 1


def test_no_reactive_fast_path_keeps_semantic_pressure_and_playability_equal() -> None:
    contract = ScenarioContract(
        contract_id="no-reactive-world-rules",
        source_version=1,
        ruleset_id="coc7",
        title="No reactive world rules",
        clocks=(ClockSpec(clock_id="pressure", title="Pressure", maximum_value=3),),
        operators=(
            ActionOperator(
                operator_id="act",
                title="Act",
                policy="automatic",
                success_commands=(
                    WorldCommand(kind="emit_event", event_type="ping"),
                    WorldCommand(kind="advance_clock", clock_id="pressure", delta=2),
                ),
            ),
        ),
        trigger_rules=(
            TriggerRule(
                trigger_id="pong",
                event_type="ping",
                commands=(
                    WorldCommand(kind="set_fact", path="semantic.ran", value=True),
                ),
            ),
        ),
        pressure_tracks=(
            PressureTrackSpec(
                pressure_id="danger",
                title="Danger",
                clock_id="pressure",
                stages=(
                    PressureStage(
                        stage_id="warning",
                        threshold=2,
                        public_label="Warning",
                        commands=(
                            WorldCommand(
                                kind="set_fact", path="pressure.ran", value=True
                            ),
                        ),
                    ),
                ),
            ),
        ),
        endings=(
            EndingRule(
                ending_id="settled",
                title="Settled",
                all_conditions=(
                    StateCondition(
                        path="facts.semantic.ran", operator="eq", value=True
                    ),
                    StateCondition(
                        path="facts.pressure.ran", operator="eq", value=True
                    ),
                ),
            ),
        ),
    )
    starting = contract.initial_snapshot("run-no-reactive")
    operator = contract.operators[0]

    _, runtime = settle_world_commands(
        contract, starting, operator.success_commands
    )
    proof = ScenarioPlayabilityAnalyzer().analyze(contract).proof(
        "ending_reachability"
    )

    assert contract.reactive_policies == ()
    assert runtime.status == "completed"
    assert runtime.facts == {"semantic": {"ran": True}, "pressure": {"ran": True}}
    assert proof.status == "passed"


def test_equivalent_contracts_reuse_one_immutable_settlement_context() -> None:
    clear_world_settlement_cache()
    contract = causal_contract()
    commands = (WorldCommand(kind="set_fact", path="action.done", value=True),)

    first = settle_world_commands(
        contract, contract.initial_snapshot("run-cache-1"), commands
    )
    second_contract = ScenarioContract.model_validate_json(contract.model_dump_json())
    second = settle_world_commands(
        second_contract,
        second_contract.initial_snapshot("run-cache-2"),
        commands,
    )
    cache_info = world_settlement_module._settlement_context.cache_info()

    assert first[0] == second[0]
    assert cache_info.misses == 1
    assert cache_info.hits == 1


def test_no_ending_kernel_skips_unsafe_ending_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_scan(contract: ScenarioContract) -> frozenset[str]:
        raise AssertionError("contracts without endings must not scan operator hashes")

    monkeypatch.setattr(
        causal_validation, "unsafe_ending_operator_ids", forbidden_scan
    )

    ActionResolutionKernel.from_contract(
        ScenarioContract(
            contract_id="no-ending-fast-path",
            source_version=1,
            ruleset_id="coc7",
            title="No ending fast path",
            operators=(
                ActionOperator(
                    operator_id="noop",
                    title="Noop",
                    policy="automatic",
                    success_commands=(
                        WorldCommand(kind="set_fact", path="noop", value=True),
                    ),
                ),
            ),
        )
    )

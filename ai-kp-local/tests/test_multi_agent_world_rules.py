from ai_kp.platform.resolution import exact_kernel_outcome
from ai_kp.platform.resolution.consequence_signals import ConsequenceSignalProjector
from ai_kp.platform.resolution.contracts import (
    ActionIntent,
    ActionOperator,
    ClockSpec,
    EntitySpec,
    OutcomeNarrativeCue,
    PressureStage,
    PressureTrackSpec,
    ResponseObligation,
    ScenarioContract,
    TriggerRule,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.narrative_adapter import deterministic_kernel_narrative
from ai_kp.platform.resolution.world_rules import DeterministicWorldRuleEngine
from tests.test_check_consequence_fingerprint import _check


def world_contract() -> ScenarioContract:
    return ScenarioContract(
        contract_id="generic-world-rules",
        source_version=1,
        ruleset_id="coc7",
        title="Generic world rules",
        entities=(
            EntitySpec(entity_id="npc", entity_type="npc", title="A witness"),
        ),
        clocks=(
            ClockSpec(clock_id="pressure", title="Escalation", maximum_value=4),
        ),
        trigger_rules=(
            TriggerRule(
                trigger_id="contact-response",
                event_type="entity_contacted",
                target_entity_id="npc",
                commands=(
                    WorldCommand(kind="set_fact", path="contact.responded", value=True),
                    WorldCommand(
                        kind="update_entity_runtime",
                        entity_id="npc",
                        payload={
                            "emotional_state": "alarmed",
                            "add_memory_ref": "contact.responded",
                        },
                    ),
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
                                kind="set_fact", path="pressure.warning", value=True
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def test_semantic_trigger_pressure_and_entity_runtime_expand_once() -> None:
    contract = world_contract()
    snapshot = contract.initial_snapshot("run-1")
    commands = (
        WorldCommand(
            kind="emit_event",
            event_type="entity_contacted",
            payload={"target_entity_id": "npc"},
        ),
        WorldCommand(kind="advance_clock", clock_id="pressure", delta=2),
    )

    first = DeterministicWorldRuleEngine(contract).expand(snapshot, commands)
    second = DeterministicWorldRuleEngine(contract).expand(snapshot, first.commands)
    resulting = ActionResolutionKernel.from_contract(contract).preflight(
        snapshot, second.commands
    )

    assert first.commands == second.commands
    assert resulting.facts == {
        "contact": {"responded": True},
        "pressure": {"warning": True},
    }
    assert resulting.entity_runtime["npc"].emotional_state == "alarmed"
    assert resulting.entity_runtime["npc"].memory_refs == ("contact.responded",)
    assert [item["type"] for item in resulting.events].count("trigger_fired") == 1
    assert [item["type"] for item in resulting.events].count(
        "pressure_stage_reached"
    ) == 1
    public_pressure = ConsequenceSignalProjector(contract).project(
        resulting, audience="table"
    )
    kp_pressure = ConsequenceSignalProjector(contract).project(
        resulting, audience="kp"
    )
    assert [(item.title, item.label) for item in public_pressure] == [
        ("Danger", "Warning")
    ]
    assert public_pressure[0].source_path is None
    assert kp_pressure[0].source_path == "clocks.pressure"


def test_deterministic_actor_fallback_preserves_response_obligations() -> None:
    contract = ScenarioContract(
        contract_id="generic-actor",
        source_version=1,
        ruleset_id="coc7",
        title="Generic actor",
        entities=(
            EntitySpec(entity_id="npc", entity_type="npc", title="A witness"),
        ),
        response_obligations=(
            ResponseObligation(
                obligation_id="visible-distress",
                entity_id="npc",
                facts_to_convey=("The witness says the east door is locked.",),
                physical_behaviors=("The witness repeatedly pulls at the observation slot.",),
            ),
        ),
        operators=(
            ActionOperator(
                operator_id="ask-witness",
                title="Ask the witness",
                public_setup="You ask the witness what happened.",
                policy="automatic",
                response_obligation_ids=("visible-distress",),
                success_commands=(
                    WorldCommand(kind="set_fact", path="witness.answered", value=True),
                ),
                narrative_cues=(
                    OutcomeNarrativeCue(
                        outcome_key="success",
                        public_summary="The witness answers the question.",
                        speaker_entity_id="npc",
                    ),
                ),
            ),
        ),
    )
    snapshot = contract.initial_snapshot("run-1")
    preview = ActionResolutionKernel.from_contract(contract).preview(
        snapshot,
        ActionIntent(
            action_id="action-1",
            actor_id="player",
            goal="Ask what happened",
            operator_id="ask-witness",
        ),
    )

    narration = deterministic_kernel_narrative(
        contract, preview, "Ask what happened", snapshot=snapshot
    ).narration_for("success")

    assert "east door is locked" in narration
    assert "observation slot" in narration


def test_pushed_check_uses_the_leaf_and_distinct_failure_branch() -> None:
    payload = {
        "preview": {
            "outcome_branches": [{"outcome_key": "pushed_failure", "commands": [{}]}]
        }
    }
    parent = _check("parent", passed=False, success_level="failure")

    assert exact_kernel_outcome(
        payload["preview"],
        [
            parent,
            _check(
                "child-success",
                passed=True,
                success_level="regular",
                pushed_from_check_id="parent",
            ),
        ],
    ) == "success"
    assert exact_kernel_outcome(
        payload["preview"],
        [
            parent,
            _check(
                "child-failure",
                passed=False,
                success_level="failure",
                pushed_from_check_id="parent",
            ),
        ],
    ) == "pushed_failure"

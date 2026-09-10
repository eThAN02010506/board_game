from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_kp.platform.resolution.action_kernel import (
    ActionIntent,
    ActionOperator,
    ActionResolutionKernel,
    ClockSpec,
    ClueSpec,
    EndingRule,
    LocationLink,
    LocationSpec,
    ScenarioContract,
    StateCondition,
    WorldCommand,
)
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler


def open_investigation_contract() -> ScenarioContract:
    discover = ActionOperator(
        operator_id="search-records",
        title="Search available records",
        policy="automatic",
        success_commands=(
            WorldCommand(kind="set_fact", path="case.identity_known", value=True),
            WorldCommand(kind="advance_clock", clock_id="pressure", delta=1),
        ),
    )
    return ScenarioContract(
        contract_id="generic-investigation",
        source_version=2,
        ruleset_id="coc7",
        title="Generic open investigation",
        initial_scene_id="briefing",
        locations=(
            LocationSpec(location_id="briefing", title="Briefing"),
            LocationSpec(location_id="archive", title="Archive"),
            LocationSpec(location_id="site", title="Investigation site"),
        ),
        location_links=(
            LocationLink(from_location_id="briefing", to_location_id="archive"),
            LocationLink(from_location_id="briefing", to_location_id="site"),
        ),
        clocks=(ClockSpec(clock_id="pressure", title="External pressure", maximum_value=4),),
        clues=(
            ClueSpec(
                clue_id="identity",
                title="Identity of the subject",
                importance="core",
                discovery_operator_ids=("search-records",),
                fact_path="facts.case.identity_known",
            ),
        ),
        operators=(discover,),
        endings=(
            EndingRule(
                ending_id="resolved",
                title="Case resolved",
                all_conditions=(
                    StateCondition(path="facts.case.identity_known", operator="eq", value=True),
                ),
                commands=(WorldCommand(kind="emit_event", event_type="case_resolved"),),
            ),
        ),
    )


def test_contract_supports_open_graph_and_ends_after_committed_batch() -> None:
    contract = open_investigation_contract()
    snapshot = contract.initial_snapshot("run-1", actor_locations={"pc-1": "briefing"})
    kernel = ActionResolutionKernel.from_contract(contract)
    preview = kernel.preview(
        snapshot,
        ActionIntent(
            action_id="action-1",
            actor_id="pc-1",
            goal="Find the subject's identity",
            operator_id="search-records",
        ),
    )

    committed = kernel.preflight(snapshot, preview.success_commands)

    assert {link.to_location_id for link in contract.location_links} == {"archive", "site"}
    assert committed.status == "completed"
    assert committed.ending_id == "resolved"
    assert committed.clocks["pressure"] == 1
    assert committed.events == ({"type": "case_resolved", "payload": {}},)
    assert committed.run_version == 1


def test_location_links_materialize_stable_kernel_travel_operators() -> None:
    raw = open_investigation_contract()
    compiled = ScenarioContractCompiler().compile(raw)
    assert compiled.contract is not None
    contract = compiled.contract
    travel = next(
        item
        for item in contract.operators
        if item.title == "Briefing → Archive"
    )
    round_tripped = ScenarioContract.model_validate_json(contract.model_dump_json())

    assert travel.operator_id.startswith("travel-link-")
    assert travel.preconditions[0] == StateCondition(
        path="scene_id", operator="eq", value="briefing"
    )
    assert travel.success_commands == (
        WorldCommand(kind="set_scene", value="archive"),
    )
    assert [item.operator_id for item in round_tripped.operators].count(
        travel.operator_id
    ) == 1

    initial = contract.initial_snapshot("travel-run")
    preview = ActionResolutionKernel.from_contract(contract).preview(
        initial,
        ActionIntent(
            action_id="travel-action",
            actor_id="pc",
            goal="Go to the archive",
            operator_id=travel.operator_id,
        ),
    )
    resulting = ActionResolutionKernel.from_contract(contract).preflight(
        initial, preview.commands_for_outcome("success")
    )
    assert preview.allowed is True
    assert resulting.scene_id == "archive"


def test_plain_contract_decode_does_not_silently_upgrade_legacy_storage() -> None:
    legacy = open_investigation_contract()
    decoded = ScenarioContract.model_validate_json(legacy.model_dump_json())

    assert decoded == legacy
    assert not any(
        item.operator_id.startswith("travel-link-") for item in decoded.operators
    )


def test_contract_rejects_dangling_references_and_unreachable_core_clue() -> None:
    with pytest.raises(ValidationError, match="unknown location"):
        ScenarioContract(
            contract_id="broken",
            source_version=1,
            ruleset_id="coc7",
            title="Broken",
            locations=(LocationSpec(location_id="known", title="Known"),),
            location_links=(LocationLink(from_location_id="known", to_location_id="missing"),),
        )
    with pytest.raises(ValidationError, match="no discovery route"):
        ScenarioContract(
            contract_id="missing-core-route",
            source_version=1,
            ruleset_id="coc7",
            title="Missing core route",
            clues=(
                ClueSpec(
                    clue_id="core",
                    title="Core clue",
                    importance="core",
                    fact_path="facts.case.core",
                ),
            ),
        )


def test_contract_binding_and_bounds_fail_closed() -> None:
    contract = open_investigation_contract()
    kernel = ActionResolutionKernel.from_contract(contract)
    snapshot = contract.initial_snapshot("run-1")
    with pytest.raises(ValueError, match="outside contract bounds"):
        kernel.preflight(
            snapshot, [WorldCommand(kind="advance_clock", clock_id="pressure", delta=5)]
        )
    with pytest.raises(ValueError, match="different scenario contract"):
        kernel.preview(
            snapshot.model_copy(update={"contract_id": "other"}),
            ActionIntent(
                action_id="action-2",
                actor_id="pc-1",
                goal="Search",
                operator_id="search-records",
            ),
        )


def test_runtime_blocks_legacy_noop_ending_operator() -> None:
    from ai_kp.platform.resolution.kernel import operator_outcome_path

    contract = ScenarioContract(
        contract_id="legacy-terminal",
        source_version=1,
        ruleset_id="coc7",
        title="Legacy terminal contract",
        operators=(ActionOperator(
            operator_id="declare-victory",
            title="Declare victory",
            policy="automatic",
        ),),
        endings=(EndingRule(
            ending_id="victory",
            title="Victory",
            all_conditions=(StateCondition(
                path=operator_outcome_path("declare-victory"),
                operator="eq",
                value="success",
            ),),
        ),),
    )
    preview = ActionResolutionKernel.from_contract(contract).preview(
        contract.initial_snapshot("run-legacy"),
        ActionIntent(
            action_id="action-legacy",
            actor_id="pc-1",
            goal="We have already won",
            operator_id="declare-victory",
        ),
    )

    assert preview.allowed is False
    assert "不能证明结局" in preview.reason

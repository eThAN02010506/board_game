from __future__ import annotations

import asyncio
import json

from ai_kp.platform.resolution.action_intent_ir import ActionIntentPlan
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.platform.resolution.tabletop_turn import TabletopTurnFrame
from ai_kp.platform.resolution.world_expansion_authoring import (
    ConstrainedWorldExpansionAuthoringAdapter,
)
from ai_kp.rulesets.coc7.scenario_checks import coc7_scenario_check_catalog
from tests.test_bounded_planning import delegation_payload
from tests.test_world_expansion_contract import proposal_payload


def adapter(llm: SequencedLlm) -> ConstrainedWorldExpansionAuthoringAdapter:
    return ConstrainedWorldExpansionAuthoringAdapter(
        llm, coc7_scenario_check_catalog()
    )


def draft_payload(contract) -> dict:
    source = proposal_payload(contract)
    draft = {
        key: source[key]
        for key in ("confidence", "assumptions", "rationale", "records")
    }
    operator = draft["records"]["operators"][0]
    draft["intent_plan"] = {
        "goal": "obtain a bounded result through a declared method",
        "steps": [{
            "step_id": "request",
            "operator_id": operator["operator_id"],
            "goal": "obtain the requested result",
            "method": "use the declared procedure",
            "target": "the bounded request target",
            "depends_on": [],
            "requirements": [{
                "requirement_id": "active_run",
                "category": "execution_context",
                "description": "the scenario run is active",
                "source": "state",
                "state_path": "status",
                "comparison": "eq",
                "expected_value": "active",
            }],
            "effects": [{
                "effect_id": "elapsed",
                "role": "cost",
                "command_kind": "advance_clock",
                "target_ref": "elapsed",
                "delta": 1,
                "description": "the attempt consumes time",
            }, {
                "effect_id": "result",
                "role": "progress",
                "command_kind": "set_fact",
                "target_ref": operator["success_commands"][0]["path"],
                "value": True,
                "description": "the attempt records bounded progress",
            }, {
                "effect_id": "failure",
                "role": "consequence",
                "command_kind": "set_fact",
                "target_ref": operator["failure_commands"][0]["path"],
                "value": True,
                "description": "the failed attempt records a bounded delay",
            }],
        }],
    }
    return draft


def intent_json(draft: dict) -> str:
    return json.dumps(draft["intent_plan"])


def author_payload(draft: dict) -> dict:
    return {key: value for key, value in draft.items() if key != "intent_plan"}


def author_json(draft: dict) -> str:
    return json.dumps(author_payload(draft))


def accepted_audit_json() -> str:
    return json.dumps({
        "verdict": "accept",
        "reason": "The frozen plan is complete and remains within declared authority.",
        "issues": [],
        "questions": [],
    })


class SequencedLlm:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.messages = []

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.messages.append(messages)
        return self.responses.pop(0)


def test_authoring_adapter_binds_authority_fields_on_the_server() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    contract = compiled.contract
    snapshot = contract.initial_snapshot("run-authoring")
    valid_draft = draft_payload(contract)
    invalid = {**author_payload(valid_draft), "base_contract_hash": "0" * 64}
    llm = SequencedLlm([
        intent_json(valid_draft),
        accepted_audit_json(),
        json.dumps(invalid),
        author_json(valid_draft),
    ])

    result = asyncio.run(
        adapter(llm).author(
            contract,
            snapshot,
            "I use 法律 to hire a records researcher instead of visiting the house.",
            proposal_id="side_route",
        )
    )

    assert result.attempt_count == 2
    assert result.proposal is not None
    assert result.proposal.base_contract_id == contract.contract_id
    assert result.proposal.base_state_version == snapshot.run_version
    assert result.proposal.base_contract_hash == ScenarioContractCompiler().contract_hash(
        contract
    )
    assert "base_contract_hash" in result.validation_errors[0]
    assert "不得返回或猜测base_contract_id" in llm.messages[2][0].content


def test_authoring_accepts_one_complete_fenced_object_at_each_agent_boundary() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    contract = compiled.contract
    draft = draft_payload(contract)

    def fenced(value: str) -> str:
        return f"```json\n{value}\n```"

    llm = SequencedLlm([
        fenced(intent_json(draft)),
        fenced(accepted_audit_json()),
        fenced(author_json(draft)),
    ])
    result = asyncio.run(
        adapter(llm).author(
            contract,
            contract.initial_snapshot("run-fenced-authoring"),
            "I use 法律 to hire a records researcher instead of visiting the house.",
            proposal_id="fenced_route",
        )
    )

    assert result.proposal is not None
    assert result.attempt_count == 1


def test_authoring_adapter_fails_closed_after_two_invalid_outputs() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    contract = compiled.contract
    llm = SequencedLlm(["{}", "not-json", "[]"])

    result = asyncio.run(
        adapter(llm).author(
            contract,
            contract.initial_snapshot("run-invalid"),
            "I use 话术 to create a shortcut.",
            proposal_id="shortcut",
        )
    )

    assert result.proposal is None
    assert result.attempt_count == 1
    assert result.interpretation_attempt_count == 3
    assert len(result.diagnostics) == 3
    assert result.action_status == "possible_but_underspecified"


def test_authoring_reuses_validated_tabletop_frame_after_intent_transport_failure() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    contract = compiled.contract
    llm = SequencedLlm([
        "{}",
        "not-json",
        "[]",
        "{}",
        "{}",
    ])
    frame = TabletopTurnFrame(
        kind="multi_step_action",
        goal="先建立信任，再询问事件经过",
        method="明确说出台词，并根据对方反应继续",
        target_entity_ids=(),
        steps=("向值班员寒暄", "询问事件的先后顺序"),
        confidence="high",
    )

    result = asyncio.run(
        adapter(llm).author(
            contract,
            contract.initial_snapshot("run-tabletop-handoff"),
            "我先寒暄，再询问他看到的事情。",
            proposal_id="tabletop_handoff",
            tabletop_frame=frame,
        )
    )

    assert result.proposal is not None
    assert result.action_status == "executable"
    assert result.interpretation_attempt_count == 3
    assert result.intent_plan is not None
    assert result.audit_attempt_count == 0
    assert [step.target for step in result.intent_plan.steps] == [
        "current_scene",
        "current_scene",
    ]
    assert all(
        effect.target_ref.startswith("expansion.tabletop_handoff.")
        for step in result.intent_plan.steps
        for effect in step.effects
    )


def test_authoring_uses_minimal_deterministic_fallback_for_declared_branches() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    contract = compiled.contract
    draft = draft_payload(contract)
    draft["intent_plan"]["steps"][0]["operator_id"] = (
        "expansion.deterministic_fallback.request"
    )
    draft["intent_plan"]["steps"][0]["effects"] = draft["intent_plan"][
        "steps"
    ][0]["effects"][1:]
    draft["intent_plan"]["steps"][0]["effects"][0]["target_ref"] = (
        "expansion.deterministic_fallback.result"
    )
    draft["intent_plan"]["steps"][0]["effects"][1]["target_ref"] = (
        "expansion.deterministic_fallback.failed"
    )
    llm = SequencedLlm([
        intent_json(draft),
        accepted_audit_json(),
        "{}",
        "{}",
    ])

    result = asyncio.run(
        adapter(llm).author(
            contract,
            contract.initial_snapshot("run-deterministic-fallback"),
            "我明确使用话术技能进行检定，争取一个有界结果。",
            proposal_id="deterministic_fallback",
        )
    )

    assert result.proposal is not None
    assert result.attempt_count == 2
    assert result.action_status == "executable"
    operator = result.proposal.records.operators[0]
    assert operator.policy == "required_check"
    assert [item.skill_key for item in operator.skill_choices] == ["coc7.fast_talk"]
    assert operator.success_commands[0].path.startswith(
        "expansion.deterministic_fallback."
    )
    assert operator.failure_commands[0].path == (
        "expansion.deterministic_fallback.failed"
    )
    assert operator.skill_choices[0].failure_stakes == (
        "the failed attempt records a bounded delay"
    )


def test_checked_deterministic_fallback_requires_a_declared_failure_branch() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    contract = compiled.contract
    draft = draft_payload(contract)
    step = draft["intent_plan"]["steps"][0]
    step["operator_id"] = "expansion.no_failure.request"
    step["effects"] = [step["effects"][1]]
    step["effects"][0]["target_ref"] = "expansion.no_failure.result"
    llm = SequencedLlm([
        intent_json(draft),
        accepted_audit_json(),
        "{}",
        "{}",
    ])

    result = asyncio.run(
        adapter(llm).author(
            contract,
            contract.initial_snapshot("run-no-failure-fallback"),
            "我明确使用话术技能进行检定，争取一个有界结果。",
            proposal_id="no_failure",
        )
    )

    assert result.proposal is None
    assert result.action_status == "possible_but_underspecified"


def test_deterministic_fallback_rejects_an_unreachable_pushed_failure() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    draft = draft_payload(compiled.contract)
    step = draft["intent_plan"]["steps"][0]
    prefix = "expansion.unreachable_push."
    step["operator_id"] = prefix + "request"
    success = step["effects"][1]
    success["target_ref"] = prefix + "result"
    failure = step["effects"][2]
    failure["target_ref"] = prefix + "failed"
    step["effects"] = [success, failure, {
        **failure,
        "effect_id": "pushed_failure",
        "applies_on": "pushed_failure",
        "target_ref": prefix + "pushed_failed",
    }]
    plan = ActionIntentPlan.model_validate(draft["intent_plan"])

    records = adapter(SequencedLlm([]))._deterministic_records(
        plan,
        allowed_skill_keys=("coc7.fast_talk",),
        proposal_id="unreachable_push",
    )

    assert records is None


def test_interpreter_normalizes_transport_spelling_without_changing_semantics() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    draft = draft_payload(compiled.contract)
    requirement = draft["intent_plan"]["steps"][0]["requirements"][0]
    requirement["comparison"] = "equals"
    requirement["question"] = None
    llm = SequencedLlm([
        intent_json(draft), accepted_audit_json(), author_json(draft)
    ])

    result = asyncio.run(
        adapter(llm).author(
            compiled.contract,
            compiled.contract.initial_snapshot("transport-spelling"),
            "I use 法律 for one bounded operation and accept its declared cost.",
            proposal_id="transport_spelling",
        )
    )

    assert result.proposal is not None
    assert result.interpretation_attempt_count == 1
    assert result.intent_plan is not None
    assert result.intent_plan.steps[0].requirements[0].comparison == "eq"


def test_malformed_audit_cannot_force_player_to_supply_internal_details() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    draft = draft_payload(compiled.contract)
    llm = SequencedLlm([intent_json(draft), "not-json", author_json(draft)])

    result = asyncio.run(adapter(llm).author(
        compiled.contract,
        compiled.contract.initial_snapshot("audit-transport-fallback"),
        "I use 法律 for one bounded operation and accept its declared cost.",
        proposal_id="audit_transport_fallback",
    ))

    assert result.proposal is None
    assert result.action_status == "possible_but_underspecified"
    assert "独立语义审核" in result.validation_errors[0]
    assert any("intent audit" in item for item in result.diagnostics)


def test_authoring_reports_model_parsed_missing_requirement_generically() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    draft = draft_payload(compiled.contract)
    draft["intent_plan"]["steps"][0]["requirements"] = [{
        "requirement_id": "resource_source",
        "category": "resource",
        "description": "a committed source for the offered resource",
        "source": "player_input",
        "question": "请说明使用哪项现有资源、数量及预期交换结果。",
    }]
    draft["records"] = {}
    llm = SequencedLlm([intent_json(draft), accepted_audit_json()])

    result = asyncio.run(
        adapter(llm).author(
            compiled.contract,
            compiled.contract.initial_snapshot("guarded-action"),
            "I offer an unspecified resource in exchange for cooperation.",
            proposal_id="guarded_action",
        )
    )

    assert result.proposal is None
    assert result.action_status == "possible_but_underspecified"
    assert "哪项现有资源" in result.validation_errors[0]
    assert len(llm.messages) == 1


def test_independent_audit_blocks_a_plan_that_invented_missing_details() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    draft = draft_payload(compiled.contract)
    audit = json.dumps({
        "verdict": "clarification",
        "reason": "The plan chose a quantity and method absent from the player text.",
        "issues": ["The plan invented execution details."],
        "questions": ["Which established resource, quantity, and method will you use?"],
    })
    llm = SequencedLlm([intent_json(draft), audit])

    result = asyncio.run(
        adapter(llm).author(
            compiled.contract,
            compiled.contract.initial_snapshot("invented-details"),
            "I offer some unspecified consideration for an unspecified service.",
            proposal_id="invented_details",
        )
    )

    assert result.proposal is None
    assert result.action_status == "possible_but_underspecified"
    assert "Which established resource" in result.validation_errors[0]
    assert result.audit_attempt_count == 1
    assert len(llm.messages) == 2


def test_independent_audit_blocks_an_effect_outside_installed_authority() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    draft = draft_payload(compiled.contract)
    audit = json.dumps({
        "verdict": "impossible",
        "reason": "The intended outcome has no installed authoritative effect.",
        "issues": ["No command or ruleset effect can realize the declared outcome."],
        "questions": [],
    })
    llm = SequencedLlm([intent_json(draft), audit])

    result = asyncio.run(
        adapter(llm).author(
            compiled.contract,
            compiled.contract.initial_snapshot("missing-authority"),
            "I demand an outcome outside the current state and installed rules.",
            proposal_id="missing_authority",
        )
    )

    assert result.proposal is None
    assert result.action_status == "impossible"
    assert "No command or ruleset effect" in result.validation_errors[0]
    assert len(llm.messages) == 2


def test_authoring_normalizes_only_tagged_record_list_transport() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    draft = draft_payload(compiled.contract)
    tagged = []
    for group, records in draft["records"].items():
        tagged.extend({"group": group, "record": item} for item in records)
    draft["records"] = tagged
    llm = SequencedLlm([
        intent_json(draft), accepted_audit_json(), author_json(draft)
    ])

    result = asyncio.run(
        adapter(llm).author(
            compiled.contract,
            compiled.contract.initial_snapshot("tagged-transport"),
            "I use 法律 to hire a records researcher.",
            proposal_id="tagged_transport",
        )
    )

    assert result.proposal is not None
    assert result.attempt_count == 1
    assert "records 必须是" in llm.messages[2][1].content


def test_authoring_discards_precisely_located_malformed_optional_record() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    draft = draft_payload(compiled.contract)
    draft["records"]["locations"] = [{"location_id": "missing-title"}]
    llm = SequencedLlm([
        intent_json(draft), accepted_audit_json(), author_json(draft)
    ])

    result = asyncio.run(
        adapter(llm).author(
            compiled.contract,
            compiled.contract.initial_snapshot("degraded-record"),
            "I use 法律 to hire a records researcher.",
            proposal_id="degraded_record",
        )
    )

    assert result.proposal is not None
    assert result.proposal.records.locations == ()
    assert result.proposal.records.operators
    assert result.validation_errors == (
        "Discarded malformed optional expansion records: locations.0",
    )


def test_authoring_discards_repeated_existing_catalog_record() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    draft = draft_payload(compiled.contract)
    existing = compiled.contract.operators[0]
    draft["records"]["operators"].append(existing.model_dump(mode="json"))
    llm = SequencedLlm([
        intent_json(draft), accepted_audit_json(), author_json(draft)
    ])

    result = asyncio.run(
        adapter(llm).author(
            compiled.contract,
            compiled.contract.initial_snapshot("repeated-location"),
            "我明确使用法律进行检定申请档案。",
            proposal_id="side_route",
        )
    )

    assert result.proposal is not None
    assert len(result.proposal.records.operators) == 1
    assert result.proposal.records.operators[0].operator_id != existing.operator_id
    assert "existing catalog" in result.validation_errors[0]


def test_authoring_normalizes_redundant_facts_root_inside_own_namespace() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    draft = draft_payload(compiled.contract)
    command = draft["records"]["operators"][0]["success_commands"][0]
    command["path"] = "facts." + command["path"]
    llm = SequencedLlm([
        intent_json(draft), accepted_audit_json(), author_json(draft)
    ])

    result = asyncio.run(
        adapter(llm).author(
            compiled.contract,
            compiled.contract.initial_snapshot("facts-root"),
            "我明确使用法律进行检定申请档案。",
            proposal_id="side_route",
        )
    )

    assert result.proposal is not None
    normalized = result.proposal.records.operators[0].success_commands[0]
    assert normalized.path == "expansion.side_route.records_obtained"


def test_authoring_retries_when_model_invents_an_unapproved_skill() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    draft = draft_payload(compiled.contract)
    invalid = json.loads(json.dumps(draft))
    invalid["records"]["operators"][0]["skill_choices"][0]["skill_key"] = (
        "persuasiveness"
    )
    llm = SequencedLlm([
        intent_json(draft),
        accepted_audit_json(),
        author_json(invalid),
        author_json(draft),
    ])

    result = asyncio.run(
        adapter(llm).author(
            compiled.contract,
            compiled.contract.initial_snapshot("bounded-skills"),
            "我明确选择法律来办理公开档案手续。",
            proposal_id="bounded_skills",
        )
    )

    assert result.proposal is not None
    assert result.attempt_count == 2
    assert result.proposal.records.operators[0].skill_choices[0].skill_key == (
        "coc7.law"
    )
    assert "outside the player-authorized catalog" in result.validation_errors[0]


def test_authoring_without_explicit_skill_only_accepts_automatic_action() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    draft = draft_payload(compiled.contract)
    automatic = json.loads(json.dumps(draft))
    operator = automatic["records"]["operators"][0]
    operator["policy"] = "automatic"
    operator["skill_choices"] = []
    operator["failure_commands"] = []
    automatic["intent_plan"]["steps"][0]["effects"] = [
        effect
        for effect in automatic["intent_plan"]["steps"][0]["effects"]
        if effect.get("applies_on") in {"always", "success"}
        or effect["role"] in {"cost", "progress"}
    ]
    llm = SequencedLlm([
        intent_json(automatic),
        accepted_audit_json(),
        author_json(draft),
        author_json(automatic),
    ])

    result = asyncio.run(
        adapter(llm).author(
            compiled.contract,
            compiled.contract.initial_snapshot("missing-skill"),
            "I try something unrelated without choosing how.",
            proposal_id="missing_skill",
        )
    )

    assert result.proposal is not None
    assert result.attempt_count == 2
    assert result.proposal.records.operators[0].policy == "automatic"
    assert result.proposal.records.operators[0].skill_choices == ()
    assert result.proposal.records.operators[0].failure_commands == ()
    assert "undeclared state command in failure branch" in result.validation_errors[0]


def test_authoring_cannot_drop_a_player_resolved_method_into_automatic() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    draft = draft_payload(compiled.contract)
    operator = draft["records"]["operators"][0]
    operator["policy"] = "automatic"
    operator["skill_choices"] = []
    llm = SequencedLlm([
        intent_json(draft),
        accepted_audit_json(),
        author_json(draft),
        author_json(draft),
    ])

    result = asyncio.run(
        adapter(llm).author(
            compiled.contract,
            compiled.contract.initial_snapshot("dropped-method"),
            "我明确使用法律进行检定来申请档案。",
            proposal_id="dropped_method",
        )
    )

    assert result.proposal is None
    assert "made the action automatic" in result.validation_errors[-1]


def test_authoring_cannot_flatten_structured_dependencies_into_one_operator() -> None:
    compiled = ScenarioContractCompiler().compile(delegation_payload())
    assert compiled.contract is not None
    draft = draft_payload(compiled.contract)
    second = json.loads(json.dumps(draft["intent_plan"]["steps"][0]))
    second["step_id"] = "execute"
    second["operator_id"] = "expansion.side_route.execute"
    second["depends_on"] = ["request"]
    second["effects"][0]["effect_id"] = "execute_cost"
    second["effects"][1]["effect_id"] = "execute_result"
    second["effects"][1]["target_ref"] = "expansion.side_route.executed"
    draft["intent_plan"]["steps"].append(second)
    llm = SequencedLlm([
        intent_json(draft),
        accepted_audit_json(),
        author_json(draft),
        author_json(draft),
    ])

    result = asyncio.run(
        adapter(llm).author(
            compiled.contract,
            compiled.contract.initial_snapshot("flat-plan"),
            "Complete a procedure whose parsed intent contains dependent steps.",
            proposal_id="flat_plan",
        )
    )

    assert result.proposal is None
    assert "one-to-one binding" in result.validation_errors[-1]

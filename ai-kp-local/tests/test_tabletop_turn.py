from __future__ import annotations

import asyncio
import json

import pytest

from ai_kp.platform.resolution.contracts import ScenarioContract
from ai_kp.platform.resolution.tabletop_turn import (
    ConstrainedTabletopTurnAdapter,
    TabletopTurnFrame,
    TabletopTurnPolicy,
    opening_participant_ids,
)


class SequenceLlm:
    def __init__(self, responses: list[object]):
        self.responses = list(responses)
        self.messages = []

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.messages.append(messages)
        response = self.responses.pop(0)
        return response if isinstance(response, str) else json.dumps(
            response, ensure_ascii=False
        )


def contract() -> ScenarioContract:
    return ScenarioContract.model_validate({
        "contract_id": "tabletop-protocol",
        "source_version": 1,
        "ruleset_id": "test-system",
        "title": "Tabletop protocol fixture",
        "initial_scene_id": "hall",
        "locations": [{
            "location_id": "hall",
            "title": "门厅",
            "initial_visibility": "visited",
        }],
        "entities": [{
            "entity_id": "host",
            "entity_type": "npc",
            "title": "主人",
            "initial_location_id": "hall",
        }],
    })


def test_plain_npc_question_routes_to_roleplay_before_rules() -> None:
    llm = SequenceLlm([{
        "kind": "npc_dialogue",
        "goal": "询问来意",
        "method": "当面提问",
        "target_entity_ids": ["host"],
        "dialogue": "你为什么把我们叫来？",
        "steps": [],
        "time_span": "",
        "ambiguity": None,
        "confidence": "high",
    }])

    result = asyncio.run(ConstrainedTabletopTurnAdapter(llm).interpret(
        contract(), contract().initial_snapshot("run-1"), "我问主人：你为什么把我们叫来？"
    ))

    assert result.route == "roleplay"
    assert result.frame.target_entity_ids == ("host",)
    assert "不选择技能" in llm.messages[0][0].content


def test_result_claim_requires_method_instead_of_becoming_success() -> None:
    frame = TabletopTurnFrame(
        kind="result_assertion",
        goal="守卫已经相信我并交出钥匙",
        confidence="high",
    )

    assert TabletopTurnPolicy.route(frame, contract()) == "clarification"
    assert "具体做法" in TabletopTurnPolicy.clarification(frame, contract())


def test_only_material_ambiguity_blocks_an_action() -> None:
    frame = TabletopTurnFrame.model_validate({
        "kind": "action",
        "goal": "离开建筑",
        "method": "从窗户出去",
        "target_entity_ids": [],
        "dialogue": "",
        "steps": [],
        "time_span": "",
        "ambiguity": {
            "field": "target",
            "question": "你准备从哪一扇窗户出去？",
            "why_material": "不同窗户的高度、锁闭状态和落点会改变风险。",
        },
        "confidence": "high",
    })

    assert TabletopTurnPolicy.route(frame, contract()) == "clarification"
    assert TabletopTurnPolicy.clarification(frame, contract()) == (
        "你准备从哪一扇窗户出去？"
    )


def test_invalid_model_output_falls_back_to_safe_clarification() -> None:
    llm = SequenceLlm([{"invented": True}, {"still": "invalid"}])

    result = asyncio.run(ConstrainedTabletopTurnAdapter(llm).interpret(
        contract(), contract().initial_snapshot("run-1"), "我做一件模型暂时没理解的事"
    ))

    assert result.route == "clarification"
    assert result.frame.kind == "action"
    assert result.frame.confidence == "low"
    assert result.frame.ambiguity is not None
    assert "换一种说法" in result.frame.ambiguity.question
    assert len(result.validation_errors) == 2


def test_visible_world_question_bypasses_an_unreliable_model() -> None:
    llm = SequenceLlm([])

    result = asyncio.run(ConstrainedTabletopTurnAdapter(llm).interpret(
        contract(), contract().initial_snapshot("run-visible-guard"),
        "我现在直接能看到什么？",
    ))

    assert result.route == "information"
    assert result.frame.kind == "world_question"
    assert result.attempt_count == 0
    assert llm.messages == []


def test_asserted_npc_result_bypasses_model_and_requires_a_method() -> None:
    llm = SequenceLlm([])

    result = asyncio.run(ConstrainedTabletopTurnAdapter(llm).interpret(
        contract(), contract().initial_snapshot("run-assertion-guard"),
        "主人已经完全信任我，并把钥匙交出来了。",
    ))

    assert result.route == "clarification"
    assert result.frame.kind == "result_assertion"
    assert "具体做法" in TabletopTurnPolicy.clarification(
        result.frame, contract()
    )
    assert llm.messages == []


def test_explicit_social_influence_bypasses_roleplay_guessing() -> None:
    llm = SequenceLlm([])

    result = asyncio.run(ConstrainedTabletopTurnAdapter(llm).interpret(
        contract(), contract().initial_snapshot("run-social-guard"),
        "我耐心列出证据，想说服主人带我们去出口。",
    ))

    assert result.route == "mechanical"
    assert result.frame.kind == "action"
    assert result.attempt_count == 0
    assert llm.messages == []


def test_completed_social_claim_stays_an_assertion_not_an_action() -> None:
    result = asyncio.run(ConstrainedTabletopTurnAdapter(SequenceLlm([])).interpret(
        contract(), contract().initial_snapshot("run-social-claim"),
        "主人已经被我说服，并交出了钥匙。",
    ))

    assert result.route == "clarification"
    assert result.frame.kind == "result_assertion"


def test_explicit_ordered_plan_survives_two_invalid_model_frames() -> None:
    llm = SequenceLlm([{"invalid": True}, {"still": "invalid"}])

    result = asyncio.run(ConstrainedTabletopTurnAdapter(llm).interpret(
        contract(), contract().initial_snapshot("run-plan-fallback"),
        "我先安抚主人，再请他指出出口，然后带大家离开。",
    ))

    assert result.route == "mechanical"
    assert result.frame.kind == "multi_step_action"
    assert result.frame.steps == (
        "我先安抚主人",
        "请他指出出口",
        "带大家离开",
    )
    assert result.attempt_count == 2
    assert len(result.validation_errors) == 2


def test_weak_model_transport_noise_is_normalized_without_changing_semantics() -> None:
    llm = SequenceLlm([{
        "kind": "NPC-DIALOGUE",
        "goal": "询问",
        "method": "当面提问",
        "target_entity_ids": ["主人"],
        "dialogue": "你为什么在这里？",
        "steps": "",
        "time_span": None,
        "ambiguity": [],
        "confidence": 0.91,
    }])

    result = asyncio.run(ConstrainedTabletopTurnAdapter(llm).interpret(
        contract(), contract().initial_snapshot("run-noise"), "我问主人为什么在这里"
    ))

    assert result.route == "roleplay"
    assert result.frame.target_entity_ids == ("host",)
    assert result.frame.steps == ()
    assert result.frame.confidence == "high"
    assert result.attempt_count == 1


def test_turn_frame_accepts_one_fenced_json_object() -> None:
    payload = {
        "kind": "npc_dialogue",
        "goal": "询问来意",
        "method": "当面提问",
        "target_entity_ids": ["host"],
        "dialogue": "你为什么在这里？",
        "steps": [],
        "time_span": "",
        "ambiguity": None,
        "confidence": "high",
    }
    raw = f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```"

    result = asyncio.run(ConstrainedTabletopTurnAdapter(SequenceLlm([raw])).interpret(
        contract(), contract().initial_snapshot("run-fenced"), "我问主人为什么在这里"
    ))

    assert result.route == "roleplay"
    assert result.attempt_count == 1


@pytest.mark.parametrize(
    "invalid",
    [
        '结果如下：{"kind":"action"}',
        "[]",
        '```json\n{"kind":"action"}\n```\n```json\n{"kind":"action"}\n```',
    ],
)
def test_turn_frame_rejects_non_envelope_outputs(invalid: str) -> None:
    result = asyncio.run(ConstrainedTabletopTurnAdapter(
        SequenceLlm([invalid, invalid])
    ).interpret(
        contract(), contract().initial_snapshot("run-invalid-envelope"), "我尝试行动"
    ))

    assert result.route == "clarification"
    assert result.frame.confidence == "low"
    assert result.frame.ambiguity is not None
    assert len(result.validation_errors) == 2


def test_kind_audit_accepts_one_fenced_json_object() -> None:
    first = {
        "kind": "action",
        "goal": "询问来意",
        "method": "当面提问",
        "target_entity_ids": ["host"],
        "dialogue": "你为什么在这里？",
        "steps": [],
        "time_span": "",
        "ambiguity": None,
        "confidence": "high",
    }
    audit = '```json\n{"kind":"npc_dialogue","reason":"只是等待 NPC 回答。"}\n```'

    result = asyncio.run(ConstrainedTabletopTurnAdapter(
        SequenceLlm([first, audit])
    ).interpret(
        contract(), contract().initial_snapshot("run-fenced-audit"), "我问主人为什么在这里"
    ))

    assert result.route == "roleplay"
    assert result.audit_count == 1


def test_structurally_conflicting_social_frame_gets_independent_kind_audit() -> None:
    llm = SequenceLlm([{
        "kind": "npc_dialogue",
        "goal": "让主人带我离开",
        "method": "用证据说服",
        "target_entity_ids": ["host"],
        "dialogue": "这些证据足够了，请带我出去。",
        "steps": ["展示证据"],
        "time_span": "",
        "ambiguity": None,
        "confidence": "high",
    }, {
        "kind": "action",
        "reason": "玩家不只等待回答，而是试图改变目标的行为。",
    }])

    result = asyncio.run(ConstrainedTabletopTurnAdapter(llm).interpret(
        contract(), contract().initial_snapshot("run-audit"),
        "我展示证据，请主人带我出去。",
    ))

    assert result.frame.kind == "action"
    assert result.route == "mechanical"
    assert result.audit_count == 1
    assert "改变目标" in result.audit_reason


def test_action_shaped_plain_dialogue_gets_independent_kind_audit() -> None:
    llm = SequenceLlm([{
        "kind": "action",
        "goal": "询问来意",
        "method": "当面提问",
        "target_entity_ids": ["host"],
        "dialogue": "你为什么把我们叫来？",
        "steps": [],
        "time_span": "",
        "ambiguity": None,
        "confidence": "high",
    }, {
        "kind": "npc_dialogue",
        "reason": "玩家只是向 NPC 发问并等待自然回答。",
    }])

    result = asyncio.run(ConstrainedTabletopTurnAdapter(llm).interpret(
        contract(), contract().initial_snapshot("run-dialogue-audit"),
        "我问主人为什么把我们叫来。",
    ))

    assert result.route == "roleplay"
    assert result.audit_count == 1


def test_visible_location_reference_is_valid_for_world_information() -> None:
    frame = TabletopTurnFrame(
        kind="world_question",
        goal="了解眼前场景",
        method="直接观察",
        target_entity_ids=("hall", "host"),
        dialogue="我现在能看到什么？",
        confidence="high",
    )

    assert TabletopTurnPolicy.route(
        frame, contract(), contract().initial_snapshot("run-visible")
    ) == "information"


def test_unknown_entity_id_cannot_reach_an_actor_agent() -> None:
    frame = TabletopTurnFrame(
        kind="npc_dialogue",
        goal="询问",
        method="当面说话",
        target_entity_ids=("invented-npc",),
        dialogue="你认识我吗？",
        confidence="high",
    )

    assert TabletopTurnPolicy.route(frame, contract()) == "clarification"


def test_non_colocated_npc_requires_a_communication_method() -> None:
    payload = contract().model_dump(mode="json")
    payload["locations"].append({
        "location_id": "street",
        "title": "街道",
        "initial_visibility": "known",
    })
    payload["entities"][0]["initial_location_id"] = "street"
    distant = ScenarioContract.model_validate(payload)
    snapshot = distant.initial_snapshot("run-remote")
    frame = TabletopTurnFrame(
        kind="npc_dialogue",
        goal="询问",
        method="说话",
        target_entity_ids=("host",),
        dialogue="你在哪里？",
        confidence="high",
    )

    assert TabletopTurnPolicy.route(frame, distant, snapshot) == "clarification"
    assert "通信方式" in TabletopTurnPolicy.clarification(
        frame, distant, snapshot
    )


def test_opening_participants_require_an_explicit_name_match() -> None:
    payload = contract().model_dump(mode="json")
    payload["entities"].extend([
        {
            "entity_id": "knott", "entity_type": "npc", "title": "Steven Knott",
            "canonical_profile": {"summary": "房东"},
        },
        {
            "entity_id": "hidden-owner", "entity_type": "npc", "title": "Walter Corbitt",
            "canonical_profile": {"summary": "旧宅主人"},
        },
    ])
    expanded = ScenarioContract.model_validate(payload)

    assert opening_participant_ids(
        expanded,
        "房东诺特先生 (Mr. Knott) 把地址和钥匙交给了你。",
    ) == ("knott",)


def test_single_colocated_npc_resolves_an_implicit_dialogue_target() -> None:
    unresolved = TabletopTurnFrame(
        kind="npc_dialogue", dialogue="你最后一次见他们时是什么样子？", confidence="high"
    )

    resolved = ConstrainedTabletopTurnAdapter._resolve_single_dialogue_target(
        unresolved, contract(), contract().initial_snapshot("single-npc")
    )

    assert resolved.target_entity_ids == ("host",)

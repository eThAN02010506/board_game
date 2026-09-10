from __future__ import annotations

import asyncio
import json

import pytest

from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.platform.resolution.semantic_adapter import ConstrainedSemanticAdapter
from tests.test_bounded_planning import delegation_payload


class SequenceLlm:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0
        self.messages = []

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.messages.append(messages)
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


def contract():
    result = ScenarioContractCompiler().compile(delegation_payload())
    assert result.contract is not None
    return result.contract


def selection(**changes: object) -> str:
    payload = {
        "kind": "operator",
        "candidate_id": "earn-funds",
        "requested_skill_key": "luck",
        "confidence": "medium",
        "clarification": None,
    }
    payload.update(changes)
    return json.dumps(payload)


def test_small_adapter_repairs_invalid_ids_and_preserves_skill_choice() -> None:
    llm = SequenceLlm(
        [selection(candidate_id="invented"), selection(requested_skill_key="luck")]
    )

    result = asyncio.run(
        ConstrainedSemanticAdapter(llm, profile="small").select(
            contract(), "我想找一份临时工作筹钱"
        )
    )

    assert result.selection.candidate_id == "earn-funds"
    assert result.selection.requested_skill_key == "luck"
    assert result.allowed_skill_keys == ("accounting", "luck")
    assert result.requires_manual_confirmation is True
    assert result.attempt_count == 2
    assert result.validation_errors
    assert all(item.kind == "operator" for item in result.offered_candidates)


def test_semantic_selection_accepts_one_fenced_json_object() -> None:
    llm = SequenceLlm([f"```json\n{selection()}\n```"])

    result = asyncio.run(
        ConstrainedSemanticAdapter(llm, profile="small").select(
            contract(), "我想找一份临时工作筹钱"
        )
    )

    assert result.selection.candidate_id == "earn-funds"
    assert result.attempt_count == 1


@pytest.mark.parametrize(
    "invalid",
    [
        f"以下是结果：{selection()}",
        "[]",
        f"```json\n{selection()}\n```\n```json\n{selection()}\n```",
    ],
)
def test_semantic_selection_rejects_non_envelope_outputs(invalid: str) -> None:
    result = asyncio.run(
        ConstrainedSemanticAdapter(
            SequenceLlm([invalid, invalid]), profile="small"
        ).select(contract(), "我想找一份临时工作筹钱")
    )

    assert result.selection.kind == "clarification"
    assert result.attempt_count == 2


def test_clarification_audit_accepts_one_fenced_json_object() -> None:
    clarification = selection(
        kind="clarification",
        candidate_id=None,
        requested_skill_key=None,
        clarification="你具体想怎么筹钱？",
    )
    llm = SequenceLlm([
        clarification,
        '```json\n{"candidate_id":"earn-funds"}\n```',
    ])

    result = asyncio.run(
        ConstrainedSemanticAdapter(llm, profile="small").select(
            contract(), "我想找一份临时工作筹钱"
        )
    )

    assert result.selection.candidate_id == "earn-funds"


def test_large_adapter_can_select_a_bounded_method_with_same_authority() -> None:
    llm = SequenceLlm(
        [
            selection(
                kind="task_method",
                candidate_id="fund-hire-delegate",
                requested_skill_key=None,
                confidence="high",
            )
        ]
    )

    result = asyncio.run(
        ConstrainedSemanticAdapter(llm, profile="large").select(
            contract(), "我不亲自调查，先赚钱雇人去查"
        )
    )

    assert result.selection.kind == "task_method"
    assert result.selection.candidate_id == "fund-hire-delegate"
    assert result.allowed_skill_keys == ()
    assert "多个有先后关系的步骤" in llm.messages[0][0].content


def test_adapter_normalizes_only_harmless_weak_model_type_variants() -> None:
    llm = SequenceLlm(
        [
            selection(
                kind="task_method",
                candidate_id="fund-hire-delegate",
                requested_skill_key=[],
                confidence=0.9,
                clarification="",
            )
        ]
    )

    result = asyncio.run(
        ConstrainedSemanticAdapter(llm, profile="large").select(
            contract(), "我不亲自调查，先赚钱雇人去查"
        )
    )

    assert result.selection.kind == "task_method"
    assert result.selection.requested_skill_key is None
    assert result.selection.confidence == "high"
    assert result.selection.clarification is None
    assert result.attempt_count == 1


def test_weak_model_failure_falls_back_to_clarification_without_authority() -> None:
    llm = SequenceLlm(["not-json", selection(candidate_id="outside-catalog")])

    result = asyncio.run(
        ConstrainedSemanticAdapter(llm, profile="small").select(
            contract(), "我想找一份临时工作筹钱"
        )
    )

    assert result.selection.kind == "clarification"
    assert result.selection.candidate_id is None
    assert result.selection.clarification
    assert result.allowed_skill_keys == ()
    assert result.attempt_count == 2


def test_nonempty_catalog_gets_one_independent_clarification_audit() -> None:
    llm = SequenceLlm(
        [
            selection(
                kind="clarification",
                candidate_id=None,
                requested_skill_key=None,
                clarification="你具体想怎么筹钱？",
            ),
            json.dumps({"candidate_id": "earn-funds"}),
        ]
    )

    result = asyncio.run(
        ConstrainedSemanticAdapter(llm, profile="small").select(
            contract(), "我想找一份临时工作筹钱"
        )
    )

    assert result.selection.kind == "operator"
    assert result.selection.candidate_id == "earn-funds"
    assert result.selection.requested_skill_key is None
    assert result.attempt_count == 2
    assert any("覆盖审计确认" in error for error in result.validation_errors)
    assert "候选覆盖审计 Agent" in llm.messages[1][0].content


def test_clarification_audit_never_forces_an_operator_choice() -> None:
    clarification = selection(
        kind="clarification",
        candidate_id=None,
        requested_skill_key=None,
        clarification="请说明目标、手段或对象。",
    )
    llm = SequenceLlm([clarification, json.dumps({"candidate_id": None})])

    result = asyncio.run(
        ConstrainedSemanticAdapter(llm, profile="small").select(
            contract(), "我想找一份临时工作筹钱"
        )
    )

    assert result.selection.kind == "clarification"
    assert result.selection.candidate_id is None
    assert result.attempt_count == 2
    assert result.validation_errors

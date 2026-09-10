import asyncio
import json

import pytest

from ai_kp.director.human_kp_help import (
    DIRECTOR_HELP_INPUT_JSON_BYTES,
    DIRECTOR_HELP_OUTPUT_JSON_BYTES,
    ConstrainedDirectorHelpAdapter,
    DirectorHelpOutput,
    parse_director_help_output,
)
from ai_kp.director.turn_output import StructuredOutputError
from ai_kp.platform.ports.llm import ChatMessage


def valid_output(**overrides: object) -> str:
    payload: dict[str, object] = {
        "status": "answered",
        "answer": "规则证据要求进行侦查检定。",
        "suggested_response": "请进行一次侦查检定。",
        "candidate_id": "check-spot-hidden",
        "requested_skill_key": "coc7.spot_hidden",
        "next_steps": ["确认检定难度"],
        "evidence_ids": ["rule-17"],
        "confidence": "high",
        "uncertainty_reasons": [],
        "assumptions": [],
        "follow_up_question": None,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class SequenceLlm:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls: list[tuple[list[ChatMessage], float]] = []

    async def complete(self, messages: list[ChatMessage], temperature: float = 0.7) -> str:
        self.calls.append((messages, temperature))
        return self.responses.pop(0)


class BrokenLlm:
    async def complete(self, messages: list[ChatMessage], temperature: float = 0.7) -> str:
        raise RuntimeError("provider unavailable")


def brief() -> dict[str, object]:
    return {
        "question": "这里该要求什么检定？",
        "offered_candidates": [
            {
                "candidate_id": "check-spot-hidden",
                "skill_keys": ["coc7.spot_hidden"],
                "evidence_ids": ["rule-17"],
            }
        ],
        "offered_skill_keys": ["coc7.spot_hidden"],
    }


def evidence() -> list[dict[str, str]]:
    return [{"evidence_id": "rule-17", "text": "发现隐藏痕迹使用侦查技能。"}]


def test_accepts_only_offered_read_only_selections_and_sends_bounded_payload() -> None:
    llm = SequenceLlm([valid_output()])

    result = asyncio.run(
        ConstrainedDirectorHelpAdapter(llm).answer(director_brief=brief(), evidence=evidence())
    )

    assert result.status == "answered"
    assert result.candidate_id == "check-spot-hidden"
    assert result.requested_skill_key == "coc7.spot_hidden"
    assert result.evidence_ids == ["rule-17"]
    assert len(llm.calls) == 1
    messages, temperature = llm.calls[0]
    assert temperature == 0.1
    assert "不能掷骰、修改世界状态" in messages[0].content
    sent = json.loads(messages[1].content)
    assert sent["director_brief"] == brief()
    assert sent["evidence"] == evidence()
    assert sent["offered_candidate_ids"] == ["check-spot-hidden"]
    assert sent["offered_candidate_skills"] == {"check-spot-hidden": ["coc7.spot_hidden"]}


def test_repairs_unknown_identifier_without_expanding_the_allowlist() -> None:
    llm = SequenceLlm(
        [
            valid_output(candidate_id="invented", evidence_ids=["invented-source"]),
            valid_output(),
        ]
    )

    result = asyncio.run(
        ConstrainedDirectorHelpAdapter(llm).answer(director_brief=brief(), evidence=evidence())
    )

    assert result.candidate_id == "check-spot-hidden"
    assert len(llm.calls) == 2
    repair_messages, repair_temperature = llm.calls[1]
    assert repair_temperature == 0.0
    assert "ID 白名单校验" in repair_messages[-1].content
    assert json.loads(repair_messages[1].content)["offered_evidence_ids"] == ["rule-17"]


def test_fails_closed_after_at_most_two_repairs() -> None:
    llm = SequenceLlm(["not-json", "still-not-json", "{}"])

    result = asyncio.run(
        ConstrainedDirectorHelpAdapter(llm).answer(director_brief=brief(), evidence=evidence())
    )

    assert len(llm.calls) == 3
    assert result == DirectorHelpOutput(
        status="no_evidence",
        answer="现有资料不足，无法可靠回答。",
        suggested_response="",
        next_steps=[],
        evidence_ids=[],
        confidence="low",
        uncertainty_reasons=["模型输出未通过只读约束校验。"],
        assumptions=[],
    )


def test_transport_failure_remains_distinguishable_from_missing_evidence() -> None:
    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(
            ConstrainedDirectorHelpAdapter(BrokenLlm()).answer(
                director_brief=brief(), evidence=evidence()
            )
        )


def test_strict_schema_rejects_extra_fields_and_overlong_lists() -> None:
    with pytest.raises(StructuredOutputError):
        parse_director_help_output(valid_output(write_world_state=True))

    with pytest.raises(StructuredOutputError):
        parse_director_help_output(valid_output(next_steps=["one", "two", "three", "four", "five"]))

    with pytest.raises(StructuredOutputError):
        parse_director_help_output(
            valid_output(evidence_ids=[f"evidence-{index}" for index in range(9)])
        )


@pytest.mark.parametrize(
    "field",
    ("next_steps", "uncertainty_reasons", "assumptions"),
)
def test_strict_schema_rejects_overlong_or_empty_list_items(field: str) -> None:
    with pytest.raises(StructuredOutputError):
        parse_director_help_output(valid_output(**{field: ["x" * 1001]}))

    with pytest.raises(StructuredOutputError):
        parse_director_help_output(valid_output(**{field: ["   "]}))


def test_strict_schema_rejects_overlong_evidence_identifier() -> None:
    with pytest.raises(StructuredOutputError):
        parse_director_help_output(valid_output(evidence_ids=["x" * 321]))


def test_raw_output_byte_budget_is_checked_before_json_decoding() -> None:
    with pytest.raises(StructuredOutputError, match="output exceeds"):
        parse_director_help_output("甲" * DIRECTOR_HELP_OUTPUT_JSON_BYTES)


def test_oversized_utf8_input_fails_closed_before_model_call() -> None:
    llm = SequenceLlm([])
    oversized_brief = {
        **brief(),
        # The character count stays below the byte budget; UTF-8 bytes exceed it.
        "untrusted_context": "甲" * (DIRECTOR_HELP_INPUT_JSON_BYTES // 2),
    }

    result = asyncio.run(
        ConstrainedDirectorHelpAdapter(llm).answer(
            director_brief=oversized_brief,
            evidence=evidence(),
        )
    )

    assert result.status == "no_evidence"
    assert llm.calls == []


@pytest.mark.parametrize(
    "untrusted_context",
    (
        {"not": {"json"}},
        {"number": float("nan")},
        {"text": "\ud800"},
    ),
)
def test_non_json_input_fails_closed_before_model_call(
    untrusted_context: object,
) -> None:
    llm = SequenceLlm([])

    result = asyncio.run(
        ConstrainedDirectorHelpAdapter(llm).answer(
            director_brief={**brief(), "untrusted_context": untrusted_context},
            evidence=evidence(),
        )
    )

    assert result.status == "no_evidence"
    assert llm.calls == []


def test_excessively_deep_input_fails_closed_before_model_call() -> None:
    llm = SequenceLlm([])
    untrusted_context: dict[str, object] = {}
    cursor = untrusted_context
    for _ in range(34):
        child: dict[str, object] = {}
        cursor["child"] = child
        cursor = child

    result = asyncio.run(
        ConstrainedDirectorHelpAdapter(llm).answer(
            director_brief={**brief(), "untrusted_context": untrusted_context},
            evidence=evidence(),
        )
    )

    assert result.status == "no_evidence"
    assert llm.calls == []


def test_excessive_input_node_count_fails_closed_before_model_call() -> None:
    llm = SequenceLlm([])

    result = asyncio.run(
        ConstrainedDirectorHelpAdapter(llm).answer(
            director_brief={
                **brief(),
                "untrusted_context": [None] * 20_001,
            },
            evidence=evidence(),
        )
    )

    assert result.status == "no_evidence"
    assert llm.calls == []


@pytest.mark.parametrize(
    "kwargs",
    (
        {"offered_candidate_ids": tuple(f"candidate-{i}" for i in range(9))},
        {"offered_skill_keys": tuple(f"skill-{i}" for i in range(65))},
        {"offered_evidence_ids": tuple(f"evidence-{i}" for i in range(21))},
        {"offered_candidate_skills": {"check-spot-hidden": tuple(f"skill-{i}" for i in range(9))}},
    ),
)
def test_oversized_allowlists_fail_closed_before_model_call(
    kwargs: dict[str, object],
) -> None:
    llm = SequenceLlm([])

    result = asyncio.run(
        ConstrainedDirectorHelpAdapter(llm).answer(
            director_brief=brief(),
            evidence=evidence(),
            **kwargs,
        )
    )

    assert result.status == "no_evidence"
    assert llm.calls == []


def test_derived_oversized_allowlists_fail_closed_before_model_call() -> None:
    llm = SequenceLlm([])
    oversized_brief = {
        "offered_candidates": [
            {"candidate_id": f"candidate-{index}", "skill_keys": []} for index in range(9)
        ]
    }

    result = asyncio.run(
        ConstrainedDirectorHelpAdapter(llm).answer(
            director_brief=oversized_brief,
            evidence=[{"evidence_id": f"evidence-{index}"} for index in range(21)],
        )
    )

    assert result.status == "no_evidence"
    assert llm.calls == []


def test_response_shape_requires_questions_only_for_clarification() -> None:
    with pytest.raises(StructuredOutputError, match="clarify requires"):
        parse_director_help_output(
            valid_output(status="clarify", candidate_id=None, requested_skill_key=None)
        )

    with pytest.raises(StructuredOutputError, match="only clarify"):
        parse_director_help_output(valid_output(follow_up_question="你指的是哪扇门？"))

    parsed = parse_director_help_output(
        valid_output(
            status="clarify",
            candidate_id=None,
            requested_skill_key=None,
            follow_up_question="你指的是哪扇门？",
        )
    )
    assert parsed.follow_up_question == "你指的是哪扇门？"

    with pytest.raises(StructuredOutputError, match="cannot select"):
        parse_director_help_output(valid_output(status="no_evidence"))


def test_skill_must_belong_to_the_selected_candidate() -> None:
    llm = SequenceLlm(
        [
            valid_output(requested_skill_key="coc7.listen"),
            valid_output(requested_skill_key=None),
        ]
    )

    result = asyncio.run(
        ConstrainedDirectorHelpAdapter(llm).answer(
            director_brief=brief(),
            evidence=evidence(),
            offered_skill_keys=("coc7.spot_hidden", "coc7.listen"),
            offered_candidate_skills={"check-spot-hidden": ("coc7.spot_hidden",)},
        )
    )

    assert result.candidate_id == "check-spot-hidden"
    assert result.requested_skill_key is None
    assert len(llm.calls) == 2


@pytest.mark.parametrize(
    "evidence_ids",
    (
        [],
        ["context-99"],
    ),
)
def test_selected_candidate_must_cite_its_own_evidence(
    evidence_ids: list[str],
) -> None:
    invalid = valid_output(evidence_ids=evidence_ids)
    llm = SequenceLlm([invalid, invalid, invalid])

    result = asyncio.run(
        ConstrainedDirectorHelpAdapter(llm).answer(
            director_brief=brief(),
            evidence=[*evidence(), {"evidence_id": "context-99", "text": "背景资料"}],
        )
    )

    assert result.status == "no_evidence"
    assert result.candidate_id is None
    assert len(llm.calls) == 3

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from ai_kp.platform.resolution.contracts import (
    ActionIntent,
    ScenarioContract,
    ScenarioSnapshot,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.narrative_adapter import (
    ConstrainedKernelNarrativeAdapter,
    deterministic_kernel_narrative,
)
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler


class SequenceLlm:
    def __init__(self, responses: list[dict | str]):
        self.responses = list(responses)
        self.calls = 0
        self.messages = []

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls += 1
        self.messages.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, str):
            return response
        if response.get("basis_hash") == "$FRAME":
            user_payload = messages[1].content.split("公开叙事依据：", 1)[1].split(
                "\n返回", 1
            )[0]
            response["basis_hash"] = json.loads(user_payload)["basis_hash"]
        return json.dumps(response, ensure_ascii=False)


def test_narrator_never_receives_entity_secrets() -> None:
    payload = narrative_contract().model_dump(mode="python")
    payload["entities"][0]["canonical_profile"] = {
        "summary": "The guard protects public records.",
        "secrets": ["The hidden archive is below the east stair."],
    }
    contract = ScenarioContract.model_validate(payload)
    llm = SequenceLlm(
        [
            {
                "basis_hash": "$FRAME",
                "preview_narration": "你向门卫提出请求，回答要等裁定后才会确定。",
                "outcomes": [
                    {
                        "outcome_key": "success",
                        "public_narration": "门卫接受了请求并允许通行。",
                        "speaker_entity_id": "guard",
                    },
                    {
                        "outcome_key": "failure",
                        "public_narration": "门卫拒绝请求，通道仍不可用。",
                        "speaker_entity_id": "guard",
                    },
                ],
            },
            {
                "accepted": True,
                "missing": [],
                "contradictions": [],
                "patch_instruction": "",
            },
        ]
    )

    bundle = asyncio.run(
        ConstrainedKernelNarrativeAdapter(llm).create(
            contract, preview(contract), "请求门卫放行"
        )
    )

    assert bundle.source == "model"
    assert "hidden archive" not in llm.messages[0][1].content


def narrative_contract() -> ScenarioContract:
    return ScenarioContract.model_validate(
        {
            "contract_id": "narrative-fixture",
            "source_version": 1,
            "ruleset_id": "coc7",
            "title": "Narrative fixture",
            "entities": [
                {"entity_id": "guard", "entity_type": "npc", "title": "门卫"}
            ],
            "operators": [
                {
                    "operator_id": "ask-guard",
                    "title": "Ask the guard",
                    "public_setup": "你向门卫提出请求，回答要等裁定后才会确定。",
                    "narrative_cues": [
                        {
                            "outcome_key": "success",
                            "public_summary": "门卫接受了请求并允许通行。",
                            "speaker_entity_id": "guard",
                            "tone": "克制",
                        },
                        {
                            "outcome_key": "failure",
                            "public_summary": "门卫拒绝请求，通道仍不可用。",
                            "speaker_entity_id": "guard",
                            "tone": "警惕",
                        },
                    ],
                    "policy": "required_check",
                    "skill_choices": [
                        {
                            "skill_key": "persuade",
                            "difficulty": "regular",
                            "reason": "请求需要说服门卫。",
                        }
                    ],
                    "success_commands": [
                        {"kind": "set_fact", "path": "guard.allowed", "value": True}
                    ],
                    "failure_commands": [
                        {"kind": "set_fact", "path": "guard.refused", "value": True}
                    ],
                }
            ],
        }
    )


def preview(contract: ScenarioContract):
    return ActionResolutionKernel.from_contract(contract).preview(
        ScenarioSnapshot(
            run_id="run-1",
            contract_id=contract.contract_id,
            scenario_version=1,
            run_version=0,
        ),
        ActionIntent(
            action_id="action-1",
            actor_id="pc-1",
            goal="请求门卫放行",
            operator_id="ask-guard",
        ),
    )


def test_model_can_only_realize_exact_public_outcome_cues() -> None:
    contract = narrative_contract()
    llm = SequenceLlm(
        [
            {
                "basis_hash": "$FRAME",
                "preview_narration": (
                    "你向门卫提出请求，回答要等裁定后才会确定。"
                    "他安静地听着。"
                ),
                "outcomes": [
                    {
                        "outcome_key": "success",
                        "public_narration": (
                            "门卫接受了请求并允许通行。"
                            "他点头说：‘可以过去。’"
                        ),
                        "speaker_entity_id": "guard",
                    },
                    {
                        "outcome_key": "failure",
                        "public_narration": (
                            "门卫拒绝请求，通道仍不可用。"
                            "他摇头说：‘我不能放你过去。’"
                        ),
                        "speaker_entity_id": "guard",
                    },
                ],
            }
        ]
    )

    bundle = asyncio.run(
        ConstrainedKernelNarrativeAdapter(llm).create(
            contract, preview(contract), "请求门卫放行"
        )
    )

    assert bundle.source == "model"
    assert "门卫接受了请求并允许通行。" in bundle.narration_for("success")
    assert "门卫拒绝请求，通道仍不可用。" in bundle.narration_for("failure")


def test_invalid_speaker_and_new_number_fall_back_without_blocking_play() -> None:
    contract = narrative_contract()
    invalid = {
        "basis_hash": "$FRAME",
        "preview_narration": "等待 30 分钟后就成功了。",
        "outcomes": [
            {
                "outcome_key": "success",
                "public_narration": "陌生人允许通行。",
                "speaker_entity_id": "stranger",
            },
            {
                "outcome_key": "failure",
                "public_narration": "门卫成功放行。",
                "speaker_entity_id": "guard",
            },
        ],
    }
    llm = SequenceLlm([dict(invalid), dict(invalid)])

    bundle = asyncio.run(
        ConstrainedKernelNarrativeAdapter(llm).create(
            contract, preview(contract), "请求门卫放行"
        )
    )

    assert bundle.source == "deterministic"
    assert bundle.attempt_count == 2
    assert len(bundle.validation_errors) == 2
    assert bundle.narration_for("failure") == "门卫拒绝请求，通道仍不可用。"


def test_invalid_verifier_output_falls_back_to_deterministic_narration() -> None:
    payload = narrative_contract().model_dump(mode="python")
    payload["entities"][0]["canonical_profile"] = {
        "summary": "门卫负责保护入口。",
    }
    contract = ScenarioContract.model_validate(payload)
    draft = {
        "basis_hash": "$FRAME",
        "preview_narration": "你向门卫提出请求，回答要等裁定后才会确定。",
        "outcomes": [
            {
                "outcome_key": "success",
                "public_narration": "门卫接受了请求并允许通行。",
                "speaker_entity_id": "guard",
            },
            {
                "outcome_key": "failure",
                "public_narration": "门卫拒绝请求，通道仍不可用。",
                "speaker_entity_id": "guard",
            },
        ],
    }
    llm = SequenceLlm(
        [dict(draft), "verifier says yes", dict(draft), '{"accepted": true']
    )

    bundle = asyncio.run(
        ConstrainedKernelNarrativeAdapter(llm).create(
            contract, preview(contract), "请求门卫放行"
        )
    )

    assert bundle.source == "deterministic"
    assert bundle.attempt_count == 2
    assert len(bundle.validation_errors) == 2
    assert all("独立审查器" in error for error in bundle.validation_errors)


def test_contract_rejects_precommitted_setup_and_successful_failure_cue() -> None:
    payload = narrative_contract().model_dump(mode="python")
    payload["operators"][0]["public_setup"] = "门卫已经成功放行。"
    with pytest.raises(ValidationError, match="public_setup"):
        ScenarioContract.model_validate(payload)

    payload = narrative_contract().model_dump(mode="python")
    payload["operators"][0]["narrative_cues"][1]["public_summary"] = "门卫成功放行。"
    with pytest.raises(ValidationError, match="Failure narrative"):
        ScenarioContract.model_validate(payload)


def test_compiler_requires_a_declared_npc_narrative_speaker() -> None:
    payload = narrative_contract().model_dump(mode="python")
    payload["entities"][0]["entity_type"] = "hazard"

    result = ScenarioContractCompiler().compile(payload)

    assert result.report.valid is False
    assert any(issue.code == "narrative_speaker_not_npc" for issue in result.report.issues)


def test_deterministic_narration_delivers_automatic_information() -> None:
    payload = narrative_contract().model_dump(mode="python")
    payload["operators"][0]["narrative_cues"] = []
    payload["operators"][0]["public_setup"] = ""
    payload["operators"][0]["automatic_information"] = (
        "书页夹层中的地址指向旧码头。",
    )
    contract = ScenarioContract.model_validate(payload)

    bundle = deterministic_kernel_narrative(
        contract, preview(contract), "查看书页夹层"
    )

    assert "书页夹层中的地址指向旧码头。" in bundle.narration_for("success")

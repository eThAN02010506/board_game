from __future__ import annotations

import asyncio
import json

import pytest

from ai_kp.platform.resolution.contracts import ScenarioContract
from ai_kp.platform.resolution.tabletop_response import (
    ConstrainedTabletopResponseAdapter,
)
from ai_kp.platform.resolution.tabletop_turn import TabletopTurnFrame


def contract() -> ScenarioContract:
    return ScenarioContract.model_validate(
        {
            "contract_id": "actor-conversation",
            "source_version": 1,
            "ruleset_id": "test-system",
            "title": "Actor conversation fixture",
            "initial_scene_id": "cell",
            "locations": [
                {
                    "location_id": "cell",
                    "title": "观察室",
                    "initial_visibility": "visited",
                }
            ],
            "entities": [
                {
                    "entity_id": "witness",
                    "entity_type": "npc",
                    "title": "维托里奥",
                    "initial_location_id": "cell",
                    "canonical_profile": {
                        "summary": "精神状态明显异常，仍能用语言回应。",
                        "known_facts": ["他认为危险正在接近。"],
                        "secrets": ["隐藏门在地下室。"],
                        "behavioral_directives": ["动作急促而且会伤害自己。"],
                    },
                    "derived_profile": {
                        "traits": ["偏执"],
                        "speech_style": ["断裂的短句"],
                        "mannerisms": ["不断靠近观察窗"],
                    },
                }
            ],
            "response_obligations": [
                {
                    "obligation_id": "warn-player",
                    "entity_id": "witness",
                    "trigger_topics": ["离开"],
                    "facts_to_convey": ["你必须离开这里。"],
                    "state_to_express": ["他已经陷入恐慌。"],
                    "physical_behaviors": ["他用指甲抓挠观察窗边缘。"],
                    "boundaries": ["隐藏门在地下室。"],
                }
            ],
        }
    )


class ActingLlm:
    def __init__(self):
        self.calls = 0

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls += 1
        if self.calls == 1:
            text = messages[-1].content
            payload_text = text.split("受限依据：", 1)[1].split("\n返回", 1)[0]
            basis = json.loads(payload_text)
            return json.dumps(
                {
                    "basis_hash": basis["basis_hash"],
                    "public_narration": (
                        "维托里奥猛地贴上玻璃，他用指甲抓挠观察窗边缘。"
                        "他已经陷入恐慌。‘你必须离开这里。现在！别再问了！’"
                        "他喘息着缩回去，盯着你下一步的动作。"
                    ),
                    "speaker_entity_ids": ["witness"],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "accepted": True,
                "missing": [],
                "contradictions": [],
                "patch_instruction": "",
            },
            ensure_ascii=False,
        )


class BrokenLlm:
    async def complete(self, messages, temperature: float = 0.7) -> str:
        return "not-json"


class IncompleteActingLlm:
    def __init__(self):
        self.calls = 0

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls += 1
        if self.calls == 1:
            text = messages[-1].content
            payload_text = text.split("受限依据：", 1)[1].split("\n返回", 1)[0]
            basis = json.loads(payload_text)
            return json.dumps(
                {
                    "basis_hash": basis["basis_hash"],
                    "public_narration": "维托里奥猛地贴上玻璃，呼吸急促地盯着你。",
                    "speaker_entity_ids": ["witness"],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "accepted": True,
                "missing": [],
                "contradictions": [],
                "patch_instruction": "",
            },
            ensure_ascii=False,
        )


class WrongHashActingLlm(ActingLlm):
    async def complete(self, messages, temperature: float = 0.7) -> str:
        response = json.loads(await super().complete(messages, temperature))
        if "basis_hash" in response:
            response["basis_hash"] = "model-copied-this-incorrectly"
        return json.dumps(response, ensure_ascii=False)


class EchoingLlm:
    async def complete(self, messages, temperature: float = 0.7) -> str:
        if "只返回 {accepted" in messages[-1].content:
            return json.dumps(
                {
                    "accepted": True,
                    "missing": [],
                    "contradictions": [],
                    "patch_instruction": "",
                }
            )
        text = messages[-1].content
        basis = json.loads(text.split("受限依据：", 1)[1].split("\n返回", 1)[0])
        return json.dumps(
            {
                "basis_hash": basis["basis_hash"],
                "public_narration": "我们现在必须离开吗？",
                "speaker_entity_ids": ["witness"],
            },
            ensure_ascii=False,
        )


class EnvelopeActingLlm(ActingLlm):
    def __init__(self, wrapper: str):
        super().__init__()
        self.wrapper = wrapper

    async def complete(self, messages, temperature: float = 0.7) -> str:
        response = await super().complete(messages, temperature)
        if self.calls == 1:
            return self.wrapper.format(payload=response)
        return response


def frame() -> TabletopTurnFrame:
    return TabletopTurnFrame(
        kind="npc_dialogue",
        goal="询问是否应该离开",
        method="当面提问",
        target_entity_ids=("witness",),
        dialogue="我们现在必须离开吗？",
        confidence="high",
    )


def test_actor_response_is_concrete_and_cannot_disclose_secret() -> None:
    llm = ActingLlm()

    result = asyncio.run(
        ConstrainedTabletopResponseAdapter(llm).create(
            contract(),
            contract().initial_snapshot("run-1"),
            "我问维托里奥：我们现在必须离开吗？",
            frame(),
            "roleplay",
        )
    )

    assert result.speaker_entity_ids == ("witness",)
    assert "你必须离开这里" in result.public_narration
    assert "抓挠观察窗" in result.public_narration
    assert "隐藏门" not in result.public_narration
    assert result.source == "model"
    assert result.attempt_count == 1
    assert result.actor_traces[0].model_dump(mode="json") == {
        "schema_version": "entity-actor-trace.v1",
        "entity_id": "witness",
        "entity_title": "维托里奥",
        "execution": "model",
        "generation_attempt_count": 1,
        "error_codes": [],
    }
    assert llm.calls == 2


def test_server_binds_basis_hash_instead_of_requiring_model_transcription() -> None:
    result = asyncio.run(
        ConstrainedTabletopResponseAdapter(WrongHashActingLlm()).create(
            contract(),
            contract().initial_snapshot("run-hash"),
            "我问维托里奥：我们现在必须离开吗？",
            frame(),
            "roleplay",
        )
    )

    assert result.source == "model"
    assert result.attempt_count == 1


def test_actor_response_accepts_one_fenced_json_object() -> None:
    result = asyncio.run(
        ConstrainedTabletopResponseAdapter(EnvelopeActingLlm("```json\n{payload}\n```")).create(
            contract(),
            contract().initial_snapshot("run-fenced"),
            "我问维托里奥：我们现在必须离开吗？",
            frame(),
            "roleplay",
        )
    )

    assert result.source == "model"
    assert result.attempt_count == 1


@pytest.mark.parametrize(
    "wrapper",
    [
        "结果如下：{payload}",
        "[{payload}]",
        "```json\n{payload}\n```\n```json\n{payload}\n```",
    ],
)
def test_actor_response_rejects_non_envelope_outputs(wrapper: str) -> None:
    result = asyncio.run(
        ConstrainedTabletopResponseAdapter(EnvelopeActingLlm(wrapper)).create(
            contract(),
            contract().initial_snapshot("run-invalid-envelope"),
            "我问维托里奥：我们现在必须离开吗？",
            frame(),
            "roleplay",
        )
    )

    assert result.source == "deterministic"
    assert result.attempt_count == 2


def test_echoed_player_question_is_rejected_as_a_non_response() -> None:
    result = asyncio.run(
        ConstrainedTabletopResponseAdapter(EchoingLlm()).create(
            contract(),
            contract().initial_snapshot("run-echo"),
            "我问维托里奥：我们现在必须离开吗？",
            frame(),
            "roleplay",
        )
    )

    assert result.source == "deterministic"
    assert "你必须离开这里" in result.public_narration
    assert result.validation_errors == (
        "witness: actor_output_rejected",
        "witness: actor_output_rejected",
    )


def test_weak_model_fallback_still_performs_required_behavior() -> None:
    result = asyncio.run(
        ConstrainedTabletopResponseAdapter(BrokenLlm()).create(
            contract(),
            contract().initial_snapshot("run-1"),
            "我问维托里奥：我们现在必须离开吗？",
            frame(),
            "roleplay",
        )
    )

    assert "你必须离开这里" in result.public_narration
    assert "抓挠观察窗" in result.public_narration
    assert "点头" not in result.public_narration
    assert "他认为危险正在接近" not in result.public_narration
    assert "隐藏门在地下室" not in result.public_narration
    assert result.source == "deterministic"
    assert result.attempt_count == 2


def test_incomplete_model_response_is_replaced_instead_of_appending_to_it() -> None:
    result = asyncio.run(
        ConstrainedTabletopResponseAdapter(IncompleteActingLlm()).create(
            contract(),
            contract().initial_snapshot("run-repair"),
            "我问维托里奥：我们现在必须离开吗？",
            frame(),
            "roleplay",
        )
    )

    assert result.source == "deterministic"
    assert result.attempt_count == 1
    assert "维托里奥猛地贴上玻璃" not in result.public_narration
    assert "你必须离开这里" in result.public_narration
    assert "抓挠观察窗" in result.public_narration


def test_world_question_basis_exposes_only_the_current_scene() -> None:
    payload = contract().model_dump(mode="json")
    payload["locations"].append(
        {
            "location_id": "remote-known",
            "title": "玩家听说过但不在眼前的地点",
            "initial_visibility": "known",
        }
    )
    expanded = ScenarioContract.model_validate(payload)

    basis = ConstrainedTabletopResponseAdapter._basis(
        expanded,
        expanded.initial_snapshot("run-current-only"),
        "我眼前有什么？",
        TabletopTurnFrame(kind="world_question", goal="观察当前环境", confidence="high"),
        "information",
    )

    assert [item["id"] for item in basis["visible_state"]["locations"]] == ["cell"]


def multi_actor_contract(actor_count: int = 2) -> ScenarioContract:
    payload = contract().model_dump(mode="json")
    payload["entities"] = []
    payload["response_obligations"] = []
    for index in range(actor_count):
        entity_id = f"witness-{index}"
        payload["entities"].append(
            {
                "entity_id": entity_id,
                "entity_type": "npc",
                "title": f"证人{index}",
                "initial_location_id": "cell",
                "canonical_profile": {
                    "summary": f"第{index}位证人。",
                    "known_facts": [f"公开事实{index}"],
                    "secrets": [f"私密边界{index}"],
                    "behavioral_directives": [f"做出动作{index}"],
                },
            }
        )
        payload["response_obligations"].append(
            {
                "obligation_id": f"answer-{index}",
                "entity_id": entity_id,
                "facts_to_convey": [f"必须回答{index}"],
            }
        )
    return ScenarioContract.model_validate(payload)


def multi_actor_frame(actor_count: int = 2) -> TabletopTurnFrame:
    return TabletopTurnFrame(
        kind="npc_dialogue",
        goal="询问所有在场证人",
        method="当面提问",
        target_entity_ids=tuple(f"witness-{index}" for index in range(actor_count)),
        dialogue="你们各自看到了什么？",
        confidence="high",
    )


class IsolatedActorLlm:
    def __init__(
        self,
        *,
        broken_actor: str | None = None,
        leaking_actor: str | None = None,
    ):
        self.broken_actor = broken_actor
        self.leaking_actor = leaking_actor
        self.actor_prompts: list[dict] = []

    async def complete(self, messages, temperature: float = 0.7) -> str:
        text = messages[-1].content
        if "待审草稿：" in text:
            return json.dumps(
                {
                    "accepted": True,
                    "missing": [],
                    "contradictions": [],
                    "patch_instruction": "",
                },
                ensure_ascii=False,
            )
        payload_text = text.split("受限依据：", 1)[1].split("\n返回", 1)[0]
        basis = json.loads(payload_text)
        self.actor_prompts.append(basis)
        entity_id = basis["actor_public_context"]["entity_id"]
        if entity_id == self.broken_actor:
            return "not-json"
        index = entity_id.rsplit("-", 1)[1]
        narration = f"证人{index}说：‘必须回答{index}。’并做出动作{index}。"
        if entity_id == self.leaking_actor:
            narration += f"私密边界{index}"
        return json.dumps(
            {
                "basis_hash": basis["basis_hash"],
                "public_narration": narration,
                "speaker_entity_ids": [entity_id],
            },
            ensure_ascii=False,
        )


def test_two_npcs_are_generated_and_verified_in_isolated_actor_frames() -> None:
    scenario = multi_actor_contract()
    llm = IsolatedActorLlm()

    result = asyncio.run(
        ConstrainedTabletopResponseAdapter(llm).create(
            scenario,
            scenario.initial_snapshot("run-two-actors"),
            "我问所有证人：你们各自看到了什么？",
            multi_actor_frame(),
            "roleplay",
        )
    )

    assert result.speaker_entity_ids == ("witness-0", "witness-1")
    assert "必须回答0" in result.public_narration
    assert "必须回答1" in result.public_narration
    assert result.source == "model"
    assert len(llm.actor_prompts) == 2
    assert llm.actor_prompts[0]["actor_public_context"]["entity_id"] == "witness-0"
    assert llm.actor_prompts[0]["private_boundaries"] == ["私密边界0"]
    assert "私密边界1" not in json.dumps(llm.actor_prompts[0], ensure_ascii=False)
    assert llm.actor_prompts[1]["private_boundaries"] == ["私密边界1"]
    assert "私密边界0" not in json.dumps(llm.actor_prompts[1], ensure_ascii=False)


@pytest.mark.parametrize(
    ("broken_actor", "leaking_actor"),
    [
        ("witness-0", None),
        (None, "witness-0"),
    ],
)
def test_one_bad_actor_cannot_pollute_or_suppress_another_actor(
    broken_actor: str | None,
    leaking_actor: str | None,
) -> None:
    scenario = multi_actor_contract()
    llm = IsolatedActorLlm(
        broken_actor=broken_actor,
        leaking_actor=leaking_actor,
    )

    result = asyncio.run(
        ConstrainedTabletopResponseAdapter(llm).create(
            scenario,
            scenario.initial_snapshot("run-isolated-failure"),
            "我问所有证人：你们各自看到了什么？",
            multi_actor_frame(),
            "roleplay",
        )
    )

    assert result.source == "model_repaired"
    assert result.speaker_entity_ids == ("witness-0", "witness-1")
    assert "必须回答0" in result.public_narration
    assert "必须回答1" in result.public_narration
    assert "私密边界0" not in result.public_narration
    assert "证人1说" in result.public_narration
    traces = {item.entity_id: item for item in result.actor_traces}
    assert traces["witness-0"].execution == "deterministic_fallback"
    assert traces["witness-0"].generation_attempt_count >= 1
    assert traces["witness-1"].execution == "model"


def test_only_four_target_entities_receive_model_actor_budget() -> None:
    scenario = multi_actor_contract(actor_count=6)
    llm = IsolatedActorLlm()

    result = asyncio.run(
        ConstrainedTabletopResponseAdapter(llm).create(
            scenario,
            scenario.initial_snapshot("run-actor-budget"),
            "我请六位证人依次回答。",
            multi_actor_frame(actor_count=6),
            "roleplay",
        )
    )

    assert [item["actor_public_context"]["entity_id"] for item in llm.actor_prompts] == [
        "witness-0",
        "witness-1",
        "witness-2",
        "witness-3",
    ]
    assert result.speaker_entity_ids == tuple(f"witness-{index}" for index in range(6))
    assert all(f"必须回答{index}" in result.public_narration for index in range(6))
    assert result.source == "model_repaired"
    assert result.actor_traces[4].error_codes == (
        "actor_model_budget_exhausted",
    )
    assert result.actor_traces[4].generation_attempt_count == 0


class SecretEchoingVerifierLlm(ActingLlm):
    async def complete(self, messages, temperature: float = 0.7) -> str:
        response = await super().complete(messages, temperature)
        if self.calls == 2:
            return json.dumps(
                {
                    "accepted": False,
                    "missing": [],
                    "contradictions": ["隐藏门在地下室。"],
                    "patch_instruction": "删掉隐藏门在地下室。",
                },
                ensure_ascii=False,
            )
        return response


def test_verifier_or_provider_text_never_enters_player_visible_diagnostics() -> None:
    result = asyncio.run(
        ConstrainedTabletopResponseAdapter(SecretEchoingVerifierLlm()).create(
            contract(),
            contract().initial_snapshot("run-secret-diagnostic"),
            "我问维托里奥：我们现在必须离开吗？",
            frame(),
            "roleplay",
        )
    )

    assert result.source == "deterministic"
    assert result.validation_errors == ("witness: actor_verifier_rejected",)
    assert result.actor_traces[0].error_codes == ("actor_verifier_rejected",)
    assert "隐藏门" not in result.public_narration
    assert "隐藏门" not in json.dumps(result.validation_errors, ensure_ascii=False)
    assert "隐藏门" not in result.actor_traces[0].model_dump_json()


def test_long_multi_actor_fallback_is_bounded_without_raising_after_aggregation() -> None:
    scenario_payload = multi_actor_contract().model_dump(mode="json")
    long_content = "可观察回应" * 340
    for obligation in scenario_payload["response_obligations"]:
        obligation["facts_to_convey"] = [f"{obligation['entity_id']}:{long_content}"]
    scenario = ScenarioContract.model_validate(scenario_payload)

    result = asyncio.run(
        ConstrainedTabletopResponseAdapter(BrokenLlm()).create(
            scenario,
            scenario.initial_snapshot("run-long-actors"),
            "我请两位证人依次说明。",
            multi_actor_frame(),
            "roleplay",
        )
    )

    assert result.source == "deterministic"
    assert len(result.public_narration) > 3_000
    assert all(f"witness-{index}:{long_content}" in result.public_narration for index in range(2))

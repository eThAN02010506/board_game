import asyncio
import json

import pytest

from ai_kp.application.encounter_action_service import EncounterActionService
from ai_kp.director.encounter_intent import (
    ConstrainedEncounterIntentAdapter,
    EncounterIntentOutput,
    parse_encounter_intent_output,
)
from ai_kp.director.turn_output import StructuredOutputError
from ai_kp.rulesets.coc7.encounter_actions import Coc7EncounterTurnAgent


class FakeLlm:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, _messages: object, temperature: float = 0.7) -> str:
        self.calls += 1
        return self.responses.pop(0)


def test_encounter_intent_agent_repairs_once_and_only_selects_authority_ids() -> None:
    response = json.dumps(
        {
            "resolution": "maneuver",
            "action_key": None,
            "target_id": "target-1",
            "maneuver_effect": "将目标压制在桌后直到下一回合",
            "public_message": "我建议按一次格斗动作处理。",
            "reason": "这是有限的非伤害控制效果。",
        },
        ensure_ascii=False,
    )
    fake = FakeLlm(["not-json", response])
    result = asyncio.run(
        ConstrainedEncounterIntentAdapter(fake).propose(
            snapshot={
                "kind": "combat",
                "actor": {"id": "actor-1", "name": "林若川"},
                "allowed_actions": [],
                "maneuver_available": True,
                "targets": [{"id": "target-1", "name": "闯入者"}],
            },
            player_action="我踹桌子吸引注意后把他按住。",
        )
    )
    assert result.resolution == "maneuver"
    assert result.target_id == "target-1"
    assert fake.calls == 2


def test_encounter_intent_contract_rejects_effect_authority_on_clarification() -> None:
    with pytest.raises(StructuredOutputError):
        parse_encounter_intent_output(
            json.dumps(
                {
                    "resolution": "clarify",
                    "action_key": "attack",
                    "target_id": None,
                    "maneuver_effect": None,
                    "public_message": "你想攻击谁？",
                    "reason": "缺少目标。",
                },
                ensure_ascii=False,
            )
        )


def test_validated_maneuver_becomes_a_bounded_ruleset_command() -> None:
    participant = {
        "participant_id": "actor-1",
        "name": "林若川",
        "build": 0,
        "action_profiles": [
            {
                "action_key": "brawl",
                "label": "格斗（斗殴）",
                "kind": "melee",
                "skill_target": 65,
                "damage_expression": "1d3",
            }
        ],
    }
    target = {
        "participant_id": "target-1",
        "name": "闯入者",
        "build": 1,
        "dodge_target": 30,
        "conditions": [],
    }
    encounter = {
        "id": "encounter-1",
        "kind": "combat",
        "status": "active",
        "state": {"participants": [participant, target]},
    }
    output = EncounterIntentOutput(
        resolution="maneuver",
        target_id="target-1",
        maneuver_effect="将目标压制在桌后直到下一回合",
        public_message="可以按格斗动作处理。",
        reason="这是有限的控制效果。",
    )
    action_key, target_id, status, preview = EncounterActionService(
        None
    )._validate_agent_proposal(
        encounter=encounter,
        participant=participant,
        output=output,
        action_text="我踹桌子吸引注意后把他按住。",
        source_model="small-model",
    )
    assert (action_key, target_id, status) == (
        "maneuver:brawl",
        "target-1",
        "awaiting_confirmation",
    )
    prepared = Coc7EncounterTurnAgent().prepare(
        encounter,
        participant,
        action_key=action_key,
        target=target,
        proposal=preview,
    )
    assert prepared.command_type == "take_turn"
    assert prepared.payload["inner"]["action_type"] == "maneuver"
    assert prepared.payload["inner"]["effect"] == preview["maneuver_effect"]
    assert 1 <= prepared.payload["inner"]["attacker_roll"] <= 100


def test_agent_cannot_bind_an_unknown_target() -> None:
    participant = {
        "participant_id": "actor-1",
        "name": "林若川",
        "build": 0,
        "action_profiles": [],
    }
    encounter = {
        "kind": "chase",
        "status": "active",
        "state": {"participants": [participant]},
    }
    output = EncounterIntentOutput(
        resolution="select",
        action_key="end_turn",
        target_id="invented-target",
        public_message="结束回合。",
        reason="模型绑定了不存在的目标。",
    )
    action_key, target_id, status, _preview = EncounterActionService(
        None
    )._validate_agent_proposal(
        encounter=encounter,
        participant=participant,
        output=output,
        action_text="我等一等。",
        source_model="small-model",
    )
    assert (action_key, target_id, status) == (
        "improvised",
        None,
        "needs_attention",
    )

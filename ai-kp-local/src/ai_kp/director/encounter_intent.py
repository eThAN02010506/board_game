"""Small-context agent for bounded encounter intent proposals."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from ai_kp.director.turn_output import StrictModel, StructuredOutputError
from ai_kp.platform.ports.llm import ChatMessage, LlmClient
from ai_kp.platform.structured_json import decode_json_object

PROMPT_VERSION = "encounter-intent.v1"


class EncounterIntentOutput(StrictModel):
    resolution: Literal["select", "maneuver", "clarify", "impossible"]
    action_key: str | None = Field(default=None, max_length=120)
    target_id: str | None = Field(default=None, max_length=120)
    maneuver_effect: str | None = Field(default=None, max_length=160)
    public_message: str = Field(min_length=1, max_length=600)
    reason: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def validate_resolution_shape(self) -> EncounterIntentOutput:
        if self.resolution == "select" and not self.action_key:
            raise ValueError("select requires action_key")
        if self.resolution == "maneuver" and (
            not self.target_id or not self.maneuver_effect
        ):
            raise ValueError("maneuver requires target_id and maneuver_effect")
        if self.resolution in {"clarify", "impossible"} and any(
            (self.action_key, self.target_id, self.maneuver_effect)
        ):
            raise ValueError("non-executable responses cannot select rules authority")
        return self


def parse_encounter_intent_output(raw: str) -> EncounterIntentOutput:
    try:
        return EncounterIntentOutput.model_validate(decode_json_object(raw))
    except (ValidationError, ValueError) as exc:
        raise StructuredOutputError(str(exc)) from exc


class ConstrainedEncounterIntentAdapter:
    """Lets a model choose a bounded rules option, never an executable command."""

    def __init__(self, llm: LlmClient):
        self.llm = llm

    async def propose(
        self,
        *,
        snapshot: dict[str, Any],
        player_action: str,
    ) -> EncounterIntentOutput:
        messages = self._messages(snapshot=snapshot, player_action=player_action)
        raw = await self.llm.complete(messages, temperature=0.1)
        try:
            return parse_encounter_intent_output(raw)
        except StructuredOutputError as exc:
            repair = [
                *messages,
                ChatMessage(role="assistant", content=raw[:4000]),
                ChatMessage(
                    role="user",
                    content=(
                        "输出未通过严格结构校验："
                        f"{str(exc)[:800]}。只返回修正后的完整 JSON。"
                    ),
                ),
            ]
            return parse_encounter_intent_output(
                await self.llm.complete(repair, temperature=0.0)
            )

    @staticmethod
    def _messages(
        *, snapshot: dict[str, Any], player_action: str
    ) -> list[ChatMessage]:
        instructions = """你是遭遇意图规划 Agent，不是 KP，也不掷骰、不叙述结果、不修改状态。
你只能把玩家的规则外做法映射为输入中的一个 allowed_action，或提出一次 CoC7 fighting
maneuver，或具体追问缺失信息，或说明当前物理条件下不可行。不得编造 action_key、target_id、
地点、道具、技能、成功结果或环境事实。信息不足时必须 clarify，问题应指出玩家需要补充的
目标、手段或预期效果。maneuver_effect 只能描述单回合内可观察、有限的控制效果，不能直接
造成伤害、死亡、永久状态、获得线索或改写事实。只返回 JSON：
{"resolution":"select|maneuver|clarify|impossible","action_key":null,
"target_id":null,"maneuver_effect":null,"public_message":"给玩家看的提案或问题",
"reason":"为何这样映射"}
select 必须填写 allowed_action 的原样 action_key；需要目标的动作必须填写 targets 中的原样 id。
maneuver 必须填写 targets 中的 target_id 和有限 maneuver_effect，其余 action_key=null。"""
        payload = {
            "player_action": player_action[:2000],
            "encounter": snapshot,
        }
        return [
            ChatMessage(role="system", content=instructions),
            ChatMessage(
                role="user",
                content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        ]


__all__ = [
    "PROMPT_VERSION",
    "ConstrainedEncounterIntentAdapter",
    "EncounterIntentOutput",
    "parse_encounter_intent_output",
]

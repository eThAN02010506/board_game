"""Small-context proposal-only agent for an NPC encounter turn."""

from __future__ import annotations

import json
from typing import Any

from pydantic import Field, ValidationError

from ai_kp.director.turn_output import StrictModel, StructuredOutputError
from ai_kp.platform.ports.llm import ChatMessage, LlmClient
from ai_kp.platform.structured_json import decode_json_object

PROMPT_VERSION = "enemy-turn.v1"


class EnemyTurnOutput(StrictModel):
    action_key: str = Field(min_length=1, max_length=120)
    target_id: str | None = Field(default=None, max_length=120)
    public_intent: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=500)


def parse_enemy_turn_output(raw: str) -> EnemyTurnOutput:
    try:
        return EnemyTurnOutput.model_validate(decode_json_object(raw))
    except (ValidationError, ValueError) as exc:
        raise StructuredOutputError(str(exc)) from exc


class ConstrainedEnemyTurnAdapter:
    """Lets a model select identifiers; rules code still prepares every command."""

    def __init__(self, llm: LlmClient):
        self.llm = llm

    async def select(
        self, *, snapshot: dict[str, Any], skill_instructions: str = ""
    ) -> EnemyTurnOutput:
        messages = self._messages(snapshot, skill_instructions=skill_instructions)
        raw = await self.llm.complete(messages, temperature=0.1)
        try:
            return parse_enemy_turn_output(raw)
        except StructuredOutputError as exc:
            repaired = await self.llm.complete(
                [
                    *messages,
                    ChatMessage(role="assistant", content=raw[:3000]),
                    ChatMessage(
                        role="user",
                        content=(
                            "结构校验失败："
                            f"{str(exc)[:600]}。只返回修正后的完整 JSON。"
                        ),
                    ),
                ],
                temperature=0.0,
            )
            return parse_enemy_turn_output(repaired)

    @staticmethod
    def _messages(
        snapshot: dict[str, Any], *, skill_instructions: str
    ) -> list[ChatMessage]:
        instructions = """你是 NPC 回合选择 Agent，不是规则引擎，也不是叙事者。
你只能从 allowed_actions 复制一个 action_key；该动作需要目标时，只能从 targets 复制一个
target_id，不需要目标时必须为 null。不要掷骰、计算命中或伤害、改变生命/位置/状态、发明
武器/法术/环境事实，也不要泄露输入中的 private_motivation。public_intent 只描述 NPC 当下可见
意图，不能声称动作成功。若信息有限，优先选 defend 或 end_turn。只返回 JSON：
{"action_key":"原样键","target_id":null,"public_intent":"可见意图","reason":"选择依据"}"""
        system = f"{skill_instructions}\n\n{instructions}".strip()
        return [
            ChatMessage(role="system", content=system),
            ChatMessage(
                role="user",
                content=json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
            ),
        ]


__all__ = [
    "PROMPT_VERSION",
    "ConstrainedEnemyTurnAdapter",
    "EnemyTurnOutput",
    "parse_enemy_turn_output",
]

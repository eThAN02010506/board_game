"""Canonical AI KP director orchestration pipeline."""

import sqlite3
from dataclasses import dataclass, replace

from ai_kp.director.context_builder import ContextAssembly, ContextBuilder, estimate_tokens
from ai_kp.director.turn_output import KpTurnOutput, StructuredOutputError, parse_kp_turn_output
from ai_kp.platform.ports.llm import ChatMessage, LlmClient


@dataclass(frozen=True)
class KpTurnResult:
    output: KpTurnOutput
    context: ContextAssembly
    repaired: bool = False


class KpOrchestrator:
    def __init__(self, connection: sqlite3.Connection, llm: LlmClient):
        self.connection = connection
        self.llm = llm

    async def handle_player_action(
        self,
        campaign_id: str,
        player_action: str,
        pc_id: str | None = None,
        location: str | None = None,
        map_id: str | None = None,
        profession_hint: str | None = None,
        active_spoiler_tags: tuple[str, ...] = (),
    ) -> KpTurnResult:
        context = ContextBuilder(self.connection).build(
            campaign_id=campaign_id,
            player_action=player_action,
            pc_id=pc_id,
            location=location,
            map_id=map_id,
            profession_hint=profession_hint,
            active_spoiler_tags=active_spoiler_tags,
            visibility_scope="kp",
        )
        request_messages = [
            ChatMessage(role=item["role"], content=item["content"]) for item in context.messages
        ]
        raw_output = await self.llm.complete(request_messages, temperature=0.3)
        try:
            output = parse_kp_turn_output(raw_output)
            return KpTurnResult(output=output, context=context)
        except StructuredOutputError as first_error:
            repair_instruction = (
                "上一次输出未通过 JSON 结构校验。"
                f"错误：{str(first_error)[:1200]}\n"
                "请仅返回修正后的完整 JSON 对象，不得添加解释。"
            )
            repair_messages = [
                *request_messages,
                ChatMessage(role="assistant", content=raw_output[:12000]),
                ChatMessage(role="user", content=repair_instruction),
            ]
            repaired_raw = await self.llm.complete(repair_messages, temperature=0.1)
            output = parse_kp_turn_output(repaired_raw)
            audited_messages = [
                {"role": item.role, "content": item.content} for item in repair_messages
            ]
            audited_context = replace(
                context,
                messages=audited_messages,
                token_estimate=sum(estimate_tokens(item["content"]) for item in audited_messages),
            )
            return KpTurnResult(output=output, context=audited_context, repaired=True)

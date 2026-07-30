"""Canonical AI KP director orchestration pipeline."""

import asyncio
import json
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from typing import Generic, Protocol, TypeVar

from ai_kp.director.check_consequence import (
    check_consequence_output_instructions,
    parse_check_consequence_output,
)
from ai_kp.director.context_builder import ContextAssembly, ContextBuilder, estimate_tokens
from ai_kp.director.errors import CampaignAiCallCancelledError
from ai_kp.director.session_recap import (
    SessionRecapOutput,
    build_session_recap_context,
    parse_session_recap_output,
)
from ai_kp.director.skills import get_ai_skill
from ai_kp.director.turn_output import KpTurnOutput, StructuredOutputError, parse_kp_turn_output
from ai_kp.director.world_expansion import (
    WORLD_EXPANSION_OUTPUT_INSTRUCTIONS,
    WorldExpansionOutput,
    parse_world_expansion_output,
)
from ai_kp.platform.ports.llm import ChatMessage, LlmClient

CHECK_CONSEQUENCE_CONTEXT_BUDGET = 12000
OutputT = TypeVar("OutputT")


class AiCallTracker(Protocol):
    def track(self, campaign_id: str) -> AbstractContextManager[None]: ...


@dataclass(frozen=True)
class KpTurnResult(Generic[OutputT]):
    output: OutputT
    context: ContextAssembly
    repaired: bool = False
    skill_id: str = ""
    skill_version: str = ""


class KpOrchestrator:
    def __init__(
        self,
        connection: sqlite3.Connection,
        llm: LlmClient,
        call_registry: AiCallTracker | None = None,
    ):
        self.connection = connection
        self.llm = llm
        self.call_registry = call_registry

    async def handle_player_action(
        self,
        campaign_id: str,
        player_action: str,
        pc_id: str | None = None,
        location: str | None = None,
        map_id: str | None = None,
        profession_hint: str | None = None,
        active_spoiler_tags: tuple[str, ...] = (),
    ) -> KpTurnResult[KpTurnOutput]:
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
        return await self._complete_structured(
            campaign_id,
            context,
            parse_kp_turn_output,
            skill_id="platform.turn_proposal",
        )

    async def handle_check_consequence(
        self,
        *,
        campaign_id: str,
        player_action: str,
        check_snapshot: dict,
        pc_id: str | None = None,
        location: str | None = None,
        map_id: str | None = None,
    ) -> KpTurnResult[KpTurnOutput]:
        hidden_batch = check_snapshot.get("has_hidden_checks") is True
        snapshot_source = {
            "kind": "verified_check_batch",
            "id": str(check_snapshot["result_fingerprint"]),
            "label": "已验证的确定性检定结果",
            "content": json.dumps(
                check_snapshot,
                ensure_ascii=False,
                sort_keys=True,
            ),
            "visibility": "kp",
            "required": True,
        }
        context = ContextBuilder(
            self.connection,
            max_context_tokens=CHECK_CONSEQUENCE_CONTEXT_BUDGET,
        ).build(
            campaign_id=campaign_id,
            player_action=player_action,
            pc_id=pc_id,
            location=location,
            map_id=map_id,
            visibility_scope="kp",
            output_instructions=check_consequence_output_instructions(
                hidden_batch=hidden_batch
            ),
            additional_sources=(snapshot_source,),
        )

        def parse_consequence(raw: str) -> KpTurnOutput:
            return parse_check_consequence_output(
                raw,
                hidden_batch=hidden_batch,
            )

        return await self._complete_structured(
            campaign_id,
            context,
            parse_consequence,
            skill_id="platform.check_consequence_narration",
        )

    async def handle_world_expansion(
        self,
        *,
        campaign_id: str,
        player_intent: str,
        analysis_snapshot: dict,
        pc_id: str | None = None,
        location: str | None = None,
        map_id: str | None = None,
        active_spoiler_tags: tuple[str, ...] = (),
    ) -> KpTurnResult[WorldExpansionOutput]:
        analysis_source = {
            "kind": "scene_director_analysis",
            "id": str(analysis_snapshot["fingerprint"]),
            "label": "确定性世界缺口分析",
            "content": json.dumps(
                analysis_snapshot,
                ensure_ascii=False,
                sort_keys=True,
            ),
            "visibility": "kp",
            "required": True,
        }
        context = ContextBuilder(self.connection).build(
            campaign_id=campaign_id,
            player_action=player_intent,
            pc_id=pc_id,
            location=location,
            map_id=map_id,
            active_spoiler_tags=active_spoiler_tags,
            visibility_scope="kp",
            output_instructions=WORLD_EXPANSION_OUTPUT_INSTRUCTIONS,
            additional_sources=(analysis_source,),
        )
        return await self._complete_structured(
            campaign_id,
            context,
            parse_world_expansion_output,
            skill_id="platform.world_expansion",
        )

    async def handle_session_recap(
        self,
        snapshot: dict,
    ) -> KpTurnResult[SessionRecapOutput]:
        return await self._complete_structured(
            str(snapshot["campaign_id"]),
            build_session_recap_context(snapshot),
            parse_session_recap_output,
            skill_id="platform.session_recap",
        )

    async def _complete_structured(
        self,
        campaign_id: str,
        context: ContextAssembly,
        parser: Callable[[str], OutputT],
        *,
        skill_id: str,
    ) -> KpTurnResult[OutputT]:
        skill = get_ai_skill(skill_id)
        if self.call_registry is None:
            return await self._complete_structured_tracked(
                context,
                parser,
                skill_id=skill.skill_id,
                skill_version=skill.version,
            )
        try:
            with self.call_registry.track(campaign_id):
                return await self._complete_structured_tracked(
                    context,
                    parser,
                    skill_id=skill.skill_id,
                    skill_version=skill.version,
                )
        except asyncio.CancelledError as exc:
            raise CampaignAiCallCancelledError(
                "Campaign AI call was cancelled by safety pause or human KP takeover"
            ) from exc

    async def _complete_structured_tracked(
        self,
        context: ContextAssembly,
        parser: Callable[[str], OutputT],
        *,
        skill_id: str,
        skill_version: str,
    ) -> KpTurnResult[OutputT]:
        request_messages = [
            ChatMessage(role=item["role"], content=item["content"]) for item in context.messages
        ]
        raw_output = await self.llm.complete(request_messages, temperature=0.3)
        try:
            output = parser(raw_output)
            return KpTurnResult(
                output=output,
                context=context,
                skill_id=skill_id,
                skill_version=skill_version,
            )
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
            output = parser(repaired_raw)
            audited_messages = [
                {"role": item.role, "content": item.content} for item in repair_messages
            ]
            audited_context = replace(
                context,
                messages=audited_messages,
                token_estimate=sum(estimate_tokens(item["content"]) for item in audited_messages),
            )
            return KpTurnResult(
                output=output,
                context=audited_context,
                repaired=True,
                skill_id=skill_id,
                skill_version=skill_version,
            )

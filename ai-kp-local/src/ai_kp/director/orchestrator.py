"""Canonical AI KP director orchestration pipeline."""

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from typing import Generic, Protocol, TypeVar

from ai_kp.director.check_consequence import (
    check_consequence_output_instructions,
    parse_check_consequence_output,
)
from ai_kp.director.context_builder import ContextAssembly, ContextBuilder, estimate_tokens
from ai_kp.director.encounter_intent import (
    ConstrainedEncounterIntentAdapter,
    EncounterIntentOutput,
)
from ai_kp.director.enemy_turn import ConstrainedEnemyTurnAdapter, EnemyTurnOutput
from ai_kp.director.errors import CampaignAiCallCancelledError
from ai_kp.director.human_kp_help import (
    ConstrainedDirectorHelpAdapter,
    DirectorHelpOutput,
)
from ai_kp.director.output_safety import remove_precommitted_world_effects
from ai_kp.director.session_recap import (
    SessionRecapOutput,
    build_session_recap_context,
    parse_session_recap_output,
)
from ai_kp.director.session_safety_policy import CampaignSafetyPolicyLlm
from ai_kp.director.skills import (
    compose_ai_skill_instructions,
    resolve_ai_skill_composition,
)
from ai_kp.director.skills.contracts import AiSkillManifest
from ai_kp.director.turn_output import KpTurnOutput, StructuredOutputError, parse_kp_turn_output
from ai_kp.director.world_expansion import (
    WorldExpansionOutput,
    normalize_world_expansion_transport,
    parse_world_expansion_output,
    validate_world_expansion_plan,
    world_expansion_output_instructions,
)
from ai_kp.platform.facts import FactLedgerEntry, project_fact_heads
from ai_kp.platform.ports.llm import ChatMessage, LlmClient
from ai_kp.platform.resolution.contracts import (
    ResolutionPreview,
    ScenarioContract,
    ScenarioSnapshot,
)
from ai_kp.platform.resolution.narrative_adapter import (
    ConstrainedKernelNarrativeAdapter,
    KernelNarrativeBundle,
)
from ai_kp.platform.resolution.semantic_adapter import (
    ConstrainedSemanticAdapter,
    SemanticSelectionResult,
)
from ai_kp.platform.resolution.tabletop_response import (
    ConstrainedTabletopResponseAdapter,
    TabletopConversationResponse,
)
from ai_kp.platform.resolution.tabletop_turn import (
    ConstrainedTabletopTurnAdapter,
    TabletopRoute,
    TabletopTurnFrame,
    TabletopTurnInterpretation,
)
from ai_kp.platform.resolution.world_expansion_authoring import (
    ConstrainedWorldExpansionAuthoringAdapter,
    WorldExpansionAuthoringResult,
)
from ai_kp.rulesets import get_ruleset

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
    skill_ids: tuple[str, ...] = ()
    skill_versions: tuple[str, ...] = ()


class KpOrchestrator:
    def __init__(
        self,
        connection: sqlite3.Connection,
        llm: LlmClient,
        call_registry: AiCallTracker | None = None,
    ):
        self.connection = connection
        self.llm = (
            llm
            if isinstance(llm, CampaignSafetyPolicyLlm)
            else CampaignSafetyPolicyLlm(connection, llm)
        )
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
        skills = resolve_ai_skill_composition("player_action")
        context = ContextBuilder(self.connection).build(
            campaign_id=campaign_id,
            player_action=player_action,
            pc_id=pc_id,
            location=location,
            map_id=map_id,
            profession_hint=profession_hint,
            active_spoiler_tags=active_spoiler_tags,
            visibility_scope="kp",
            skill_instructions=compose_ai_skill_instructions(skills),
        )
        return await self._complete_structured(
            campaign_id,
            context,
            parse_kp_turn_output,
            skills=skills,
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
        effect_ceiling: str | None = None,
    ) -> KpTurnResult[KpTurnOutput]:
        skills = resolve_ai_skill_composition("check_consequence")
        hidden_batch = check_snapshot.get("has_hidden_checks") is True
        snapshot_source = _snapshot_source(
            kind="verified_check_batch",
            source_id=str(check_snapshot["result_fingerprint"]),
            label="已验证的确定性检定结果",
            snapshot=check_snapshot,
        )
        additional_sources = [snapshot_source]
        if effect_ceiling:
            additional_sources.append(
                _snapshot_source(
                    kind="approved_action_ceiling",
                    source_id="approved-action-ceiling",
                    label="本次行动已确认的效果上限",
                    snapshot={"maximum_effect": effect_ceiling},
                )
            )
        existing_facts = self._list_active_facts(campaign_id)
        if existing_facts:
            additional_sources.append(
                _snapshot_source(
                    kind="existing_world_facts",
                    source_id="existing-world-facts",
                    label="本团已确立的世界事实（避免重复写入）",
                    snapshot={"facts": existing_facts},
                )
            )
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
            additional_sources=tuple(additional_sources),
            skill_instructions=compose_ai_skill_instructions(skills),
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
            skills=skills,
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
        skills = resolve_ai_skill_composition("world_expansion")
        analysis_source = _snapshot_source(
            kind="scene_director_analysis",
            source_id=str(analysis_snapshot["fingerprint"]),
            label="确定性世界缺口分析",
            snapshot=analysis_snapshot,
        )
        context = ContextBuilder(self.connection).build(
            campaign_id=campaign_id,
            player_action=player_intent,
            pc_id=pc_id,
            location=location,
            map_id=map_id,
            active_spoiler_tags=active_spoiler_tags,
            visibility_scope="kp",
            output_instructions=world_expansion_output_instructions(
                str(analysis_snapshot.get("requested_expansion_kind", "environment"))
            ),
            additional_sources=(analysis_source,),
            skill_instructions=compose_ai_skill_instructions(skills),
        )

        transport_repaired = False

        def parse_and_validate(raw: str) -> WorldExpansionOutput:
            nonlocal transport_repaired
            normalized_raw, changed = normalize_world_expansion_transport(
                raw, analysis_snapshot
            )
            transport_repaired = transport_repaired or changed
            output = parse_world_expansion_output(normalized_raw)
            validate_world_expansion_plan(output.candidate, analysis_snapshot)
            return output

        result = await self._complete_structured(
            campaign_id,
            context,
            parse_and_validate,
            skills=skills,
        )
        return replace(result, repaired=True) if transport_repaired else result

    async def handle_session_recap(
        self,
        snapshot: dict,
    ) -> KpTurnResult[SessionRecapOutput]:
        skills = resolve_ai_skill_composition("session_recap")
        return await self._complete_structured(
            str(snapshot["campaign_id"]),
            build_session_recap_context(
                snapshot,
                skill_instructions=compose_ai_skill_instructions(skills),
            ),
            parse_session_recap_output,
            skills=skills,
        )

    async def propose_encounter_action(
        self,
        *,
        campaign_id: str,
        snapshot: dict,
        player_action: str,
    ) -> EncounterIntentOutput:
        return await self._tracked_model_call(
            campaign_id,
            lambda: ConstrainedEncounterIntentAdapter(self.llm).propose(
                snapshot=snapshot,
                player_action=player_action,
            ),
        )

    async def select_enemy_turn(
        self,
        *,
        campaign_id: str,
        snapshot: dict,
    ) -> EnemyTurnOutput:
        skills = resolve_ai_skill_composition("enemy_turn")
        return await self._tracked_model_call(
            campaign_id,
            lambda: ConstrainedEnemyTurnAdapter(self.llm).select(
                snapshot=snapshot,
                skill_instructions=compose_ai_skill_instructions(skills),
            ),
        )

    async def select_kernel_action(
        self,
        *,
        campaign_id: str,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        player_action: str,
        profile: str,
    ) -> SemanticSelectionResult:
        return await self._tracked_model_call(
            campaign_id,
            lambda: ConstrainedSemanticAdapter(
                self.llm, profile="large" if profile == "large" else "small"
            ).select(contract, player_action, snapshot=snapshot),
        )

    async def interpret_tabletop_turn(
        self,
        *,
        campaign_id: str,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        player_action: str,
    ) -> TabletopTurnInterpretation:
        return await self._tracked_model_call(
            campaign_id,
            lambda: ConstrainedTabletopTurnAdapter(self.llm).interpret(
                contract, snapshot, player_action
            ),
        )

    async def respond_tabletop_turn(
        self,
        *,
        campaign_id: str,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        player_action: str,
        frame: TabletopTurnFrame,
        route: TabletopRoute,
    ) -> TabletopConversationResponse:
        return await self._tracked_model_call(
            campaign_id,
            lambda: ConstrainedTabletopResponseAdapter(self.llm).create(
                contract, snapshot, player_action, frame, route
            ),
        )

    async def author_kernel_world_expansion(
        self,
        *,
        campaign_id: str,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        player_action: str,
        proposal_id: str,
        tabletop_frame: TabletopTurnFrame | None = None,
    ) -> WorldExpansionAuthoringResult:
        return await self._tracked_model_call(
            campaign_id,
            lambda: ConstrainedWorldExpansionAuthoringAdapter(
                self.llm,
                get_ruleset(contract.ruleset_id).scenario_check_catalog(),
                get_ruleset(contract.ruleset_id).scenario_effect_catalog(),
            ).author(
                contract,
                snapshot,
                player_action,
                proposal_id=proposal_id,
                tabletop_frame=tabletop_frame,
            ),
        )

    async def narrate_kernel_action(
        self,
        *,
        campaign_id: str,
        contract: ScenarioContract,
        preview: ResolutionPreview,
        snapshot: ScenarioSnapshot,
        player_action: str,
    ) -> KernelNarrativeBundle:
        return await self._tracked_model_call(
            campaign_id,
            lambda: ConstrainedKernelNarrativeAdapter(self.llm).create(
                contract, preview, player_action, snapshot=snapshot
            ),
        )

    async def advise_human_kp(
        self,
        *,
        campaign_id: str,
        brief: dict,
    ) -> DirectorHelpOutput:
        """Answer one explicit KP question from an application-owned allowlist."""

        return await self._tracked_model_call(
            campaign_id,
            lambda: ConstrainedDirectorHelpAdapter(self.llm).answer(
                director_brief=brief["director_brief"],
                evidence=brief["evidence"],
                offered_candidate_ids=brief["offered_candidate_ids"],
                offered_candidate_skills=brief["offered_candidate_skills"],
                offered_skill_keys=brief["offered_skill_keys"],
                offered_evidence_ids=brief["offered_evidence_ids"],
            ),
        )

    async def _tracked_model_call(
        self,
        campaign_id: str,
        callback: Callable[[], Awaitable[OutputT]],
    ) -> OutputT:
        try:
            with self.llm.bind_campaign(campaign_id):
                if self.call_registry is None:
                    return await callback()
                with self.call_registry.track(campaign_id):
                    return await callback()
        except asyncio.CancelledError as exc:
            raise CampaignAiCallCancelledError(
                "Campaign AI call was cancelled by safety pause or human KP takeover"
            ) from exc

    async def _complete_structured(
        self,
        campaign_id: str,
        context: ContextAssembly,
        parser: Callable[[str], OutputT],
        *,
        skills: tuple[AiSkillManifest, ...],
    ) -> KpTurnResult[OutputT]:
        return await self._tracked_model_call(
            campaign_id,
            lambda: self._complete_structured_tracked(
                    context,
                    parser,
                    skills=skills,
                ),
        )

    async def _complete_structured_tracked(
        self,
        context: ContextAssembly,
        parser: Callable[[str], OutputT],
        *,
        skills: tuple[AiSkillManifest, ...],
    ) -> KpTurnResult[OutputT]:
        request_messages = [
            ChatMessage(role=item["role"], content=item["content"]) for item in context.messages
        ]
        raw_output = await self.llm.complete(request_messages, temperature=0.3)
        try:
            output = parser(raw_output)
            return _turn_result(output, context, skills)
        except StructuredOutputError as first_error:
            repair_instruction = (
                "上一次输出未通过 JSON 结构或确定性语义校验。"
                f"错误：{str(first_error)[:1200]}\n"
                "请仅返回修正后的完整 JSON 对象，不得添加解释。"
            )
            repair_messages = [
                *request_messages,
                ChatMessage(role="assistant", content=raw_output[:12000]),
                ChatMessage(role="user", content=repair_instruction),
            ]
            repaired_raw = await self.llm.complete(repair_messages, temperature=0.1)
            try:
                output = parser(repaired_raw)
            except StructuredOutputError:
                reduced_raw = remove_precommitted_world_effects(repaired_raw)
                if reduced_raw == repaired_raw:
                    raise
                output = parser(reduced_raw)
            audited_messages = [
                {"role": item.role, "content": item.content} for item in repair_messages
            ]
            audited_context = replace(
                context,
                messages=audited_messages,
                token_estimate=sum(estimate_tokens(item["content"]) for item in audited_messages),
            )
            return _turn_result(output, audited_context, skills, repaired=True)

    def _list_active_facts(self, campaign_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM events
            WHERE campaign_id = ?
              AND event_type IN ('world_fact.asserted', 'world_fact.retconned')
            ORDER BY created_at, id
            """,
            (campaign_id,),
        ).fetchall()
        heads = project_fact_heads(
            FactLedgerEntry.from_event(dict(row)) for row in rows
        )
        facts: list[dict] = []
        for entry in heads:
            fact = entry.fact
            if not entry.active or fact.category not in {
                "canonical_fact",
                "kp_secret",
                "character_belief",
            }:
                continue
            facts.append(
                {
                    "subject": fact.subject,
                    "predicate": fact.predicate,
                    "object": fact.object_text,
                    "category": fact.category,
                }
            )
            if len(facts) >= 40:
                break
        return facts


def _snapshot_source(
    *,
    kind: str,
    source_id: str,
    label: str,
    snapshot: dict,
) -> dict:
    return {
        "kind": kind,
        "id": source_id,
        "label": label,
        "content": json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
        "visibility": "kp",
        "required": True,    }


def _turn_result(
    output: OutputT,
    context: ContextAssembly,
    skills: tuple[AiSkillManifest, ...],
    *,
    repaired: bool = False,
) -> KpTurnResult[OutputT]:
    primary = skills[0]
    return KpTurnResult(
        output=output,
        context=context,
        repaired=repaired,
        skill_id=primary.skill_id,
        skill_version=primary.version,
        skill_ids=tuple(skill.skill_id for skill in skills),
        skill_versions=tuple(skill.version for skill in skills),
    )

import hashlib
import json
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ai_kp.application.ai_control_service import AiControlService
from ai_kp.application.errors import ConflictError, InvalidInputError, KpSessionEndedError
from ai_kp.application.fact_service import (
    AssertWorldFactCommand,
    FactService,
    RetconWorldFactCommand,
)
from ai_kp.application.module_run_service import ModuleRunService
from ai_kp.application.module_setting_analysis_service import (
    ModuleSettingAnalysisService,
)
from ai_kp.application.play.proposal_approval import plan_proposed_checks
from ai_kp.application.ports.director import KpDirector, WorldExpansionDirector
from ai_kp.application.ports.repositories import TurnStore
from ai_kp.application.world_entity_state_candidates import (
    validate_world_entity_state_candidates,
)
from ai_kp.application.world_entity_state_service import (
    SetWorldEntityStateCommand,
    WorldEntityStateService,
)
from ai_kp.director.turn_output import ActionRuling, KpTurnOutput
from ai_kp.director.world_expansion import (
    WorldExpansionCandidate,
    validate_world_expansion_plan,
)
from ai_kp.platform.memory.retrieval import tokenize
from ai_kp.platform.resolution.proposals import validate_unresolved_check_boundary
from ai_kp.platform.scenes.builtin_setting_packs import get_setting_pack
from ai_kp.platform.scenes.settlement_template_matching import (
    LocationMention,
    associate_location_mentions,
)
from ai_kp.platform.scenes.settlement_templates import (
    SettlementKind,
    instantiate_settlement,
)
from ai_kp.platform.sessions.models import AuthenticatedMember
from ai_kp.rulesets import get_campaign_ruleset


@dataclass(frozen=True)
class ManualProposalCommand:
    player_action: str
    public_narration: str
    player_action_id: str | None = None
    pc_id: str | None = None
    kp_notes: str = ""
    action_ruling: Any | None = None
    proposed_checks: Sequence[Any] = ()
    proposed_events: Sequence[Any] = ()
    proposed_memories: Sequence[Any] = ()
    proposed_npc_updates: Sequence[Any] = ()
    proposed_map_moves: Sequence[Any] = ()
    proposed_facts: Sequence[Any] = ()
    source_model: str = "manual-dev"


@dataclass(frozen=True)
class KpTurnCommand:
    campaign_id: str
    player_action: str
    player_action_id: str | None = None
    pc_id: str | None = None
    location: str | None = None
    map_id: str | None = None
    profession_hint: str | None = None
    active_spoiler_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorldExpansionCommand:
    run_id: str
    player_intent: str
    requested_expansion_kind: Literal[
        "environment", "reactive_branch", "anchor_bridge"
    ] = "environment"
    pc_id: str | None = None
    map_id: str | None = None
    setting_pack_id: str | None = None
    settlement_kind: SettlementKind | None = None


class TurnService:
    """Coordinate player actions, KP drafts, context audit, and approval outbox."""

    def __init__(self, repo: TurnStore):
        self.repo = repo

    def submit_player_action(
        self,
        identity: AuthenticatedMember,
        *,
        action_text: str,
        map_id: str | None = None,
        token_id: str | None = None,
        client_action_id: str | None = None,
    ) -> dict:
        # Player action creation and its idempotency lookup are one write
        # transaction. Acquire the SQLite writer slot before authority reads so
        # concurrent players wait here instead of deadlocking while upgrading
        # separate deferred transactions from read to write.
        self.repo.begin_immediate()
        lifecycle_blocker = self.repo.player_action_block_reason(identity)
        if lifecycle_blocker:
            raise ConflictError(lifecycle_blocker)
        active_run = self.repo.get_active_campaign_module_run(identity.campaign_id)
        if active_run is None:
            recent_runs = self.repo.list_campaign_module_runs(identity.campaign_id, limit=1)
            if recent_runs:
                status = str(recent_runs[0].get("status") or "")
                if status == "completed":
                    raise ConflictError(
                        "The current module run is completed; start a new run before submitting actions"
                    )
                if status == "paused":
                    raise ConflictError(
                        "The current module run is paused; resume it before submitting actions"
                    )
        return self.repo.create_player_action(
            identity,
            action_text=action_text,
            map_id=map_id,
            token_id=token_id,
            client_action_id=client_action_id,
        )

    def create_manual_proposal(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: ManualProposalCommand,
    ) -> dict:
        ruling = self._validated_action_ruling(command)
        validate_unresolved_check_boundary(
            command.proposed_checks,
            command.proposed_events,
            command.proposed_memories,
            command.proposed_npc_updates,
            command.proposed_map_moves,
            command.proposed_facts,
            (),
        )
        queued_action = self._queued_action(
            command.player_action_id,
            campaign_id,
            identity.session_id,
        )
        proposal = self.repo.create_turn_proposal(
            campaign_id=campaign_id,
            pc_id=queued_action["pc_id"] if queued_action else command.pc_id,
            player_action=(
                queued_action["action_text"] if queued_action else command.player_action
            ),
            public_narration=command.public_narration,
            kp_notes=command.kp_notes,
            proposed_checks=list(command.proposed_checks),
            proposed_events=list(command.proposed_events),
            proposed_memories=list(command.proposed_memories),
            proposed_npc_updates=list(command.proposed_npc_updates),
            proposed_map_moves=list(command.proposed_map_moves),
            proposed_facts=list(command.proposed_facts),
            proposed_world_entity_states=[],
            source_model=command.source_model,
        )
        self._attach_action_ruling(str(proposal["id"]), ruling)
        proposal = self.repo.get_turn_proposal(str(proposal["id"]))
        if queued_action:
            self.repo.link_player_action_to_proposal(
                queued_action["id"],
                proposal["id"],
                campaign_id,
            )
        self._append_proposal_created(identity.session_id, campaign_id, proposal)
        return proposal

    async def create_ai_proposal(
        self,
        command: KpTurnCommand,
        identity: AuthenticatedMember,
        director: KpDirector,
        *,
        source_model: str,
    ) -> dict:
        control = AiControlService(self.repo).authorize(
            command.campaign_id,
            "AI turn proposal",
        )
        queued_action = self._queued_action(
            command.player_action_id,
            command.campaign_id,
            identity.session_id,
        )
        player_action = queued_action["action_text"] if queued_action else command.player_action
        pc_id = queued_action["pc_id"] if queued_action else command.pc_id
        location = queued_action["location"] if queued_action else command.location
        map_id = queued_action["map_id"] if queued_action else command.map_id

        result = await director.handle_player_action(
            campaign_id=command.campaign_id,
            player_action=player_action,
            pc_id=pc_id,
            location=location,
            map_id=map_id,
            profession_hint=command.profession_hint,
            active_spoiler_tags=command.active_spoiler_tags,
        )

        # The model call intentionally runs outside the write transaction. Claim
        # the database only for the short revalidation-and-persist phase.
        self.repo.begin_immediate()
        if not self.repo.is_session_member_active(identity.member_id, identity.session_id):
            raise KpSessionEndedError("KP session ended while the model was running")
        AiControlService(self.repo).revalidate(control)

        output = self._reveal_unseen_scene_baseline(result.output, result.context)
        output = validate_world_entity_state_candidates(
            output, result.context.included_sources
        )
        proposal = self.repo.create_turn_proposal(
            campaign_id=command.campaign_id,
            pc_id=pc_id,
            player_action=player_action,
            public_narration=output.public_narration,
            kp_notes=output.kp_notes,
            proposed_checks=output.proposed_checks,
            proposed_events=output.proposed_events,
            proposed_memories=output.proposed_memories,
            proposed_npc_updates=output.proposed_npc_updates,
            proposed_map_moves=output.proposed_map_moves,
            proposed_facts=output.proposed_facts,
            proposed_world_entity_states=output.proposed_world_entity_states,
            source_model=source_model,
        )
        self._attach_action_ruling(
            str(proposal["id"]),
            output.action_ruling.model_dump(mode="json"),
        )
        self._attach_ai_skill_composition(str(proposal["id"]), result)
        proposal = self.repo.get_turn_proposal(str(proposal["id"]))
        self.repo.create_context_assembly(
            proposal_id=proposal["id"],
            campaign_id=command.campaign_id,
            visibility_scope=result.context.visibility_scope,
            final_prompt=result.context.messages,
            included_sources=result.context.included_sources,
            excluded_sources=result.context.excluded_sources,
            token_estimate=result.context.token_estimate,
        )
        if queued_action:
            self.repo.link_player_action_to_proposal(
                queued_action["id"],
                proposal["id"],
                command.campaign_id,
            )
        self._append_proposal_created(identity.session_id, command.campaign_id, proposal)
        return proposal

    @staticmethod
    def _reveal_unseen_scene_baseline(output: KpTurnOutput, context: Any) -> KpTurnOutput:
        """Reveal formatted scene-setting once, independently of later checks.

        Imported read-aloud/scene-setting text describes what is already visible.
        A weak model must not accidentally gate it behind Spot Hidden (or any
        other roll).  Recent public events suppress repetition after the scene
        has actually been shown at the table.
        """

        sources = list(getattr(context, "included_sources", ()) or ())
        baselines = [
            str(source.get("content") or "").strip()
            for source in sources
            if source.get("kind") == "module_scene_baseline"
            and str(source.get("content") or "").strip()
        ]
        if not baselines:
            return output
        distinct_baselines: list[str] = []
        for candidate in dict.fromkeys(baselines):
            candidate_tokens = tokenize(candidate)
            if any(
                candidate_tokens
                and existing_tokens
                and len(candidate_tokens & existing_tokens)
                / max(1, min(len(candidate_tokens), len(existing_tokens)))
                >= 0.55
                for existing_tokens in map(tokenize, distinct_baselines)
            ):
                continue
            distinct_baselines.append(candidate)
        baseline = "\n".join(distinct_baselines)[:3000]
        baseline_tokens = tokenize(baseline)
        if not baseline_tokens:
            return output

        def already_revealed(text: str) -> bool:
            overlap = len(baseline_tokens & tokenize(text))
            return overlap >= max(3, int(len(baseline_tokens) * 0.35))

        public_history = [
            str(source.get("content") or "")
            for source in sources
            if source.get("kind") == "recent_event"
        ]
        if already_revealed(output.public_narration) or any(
            already_revealed(text) for text in public_history
        ):
            return output
        narration = f"{baseline}\n{output.public_narration}"[:12000]
        return output.model_copy(update={"public_narration": narration})

    def _validated_action_ruling(
        self,
        command: ManualProposalCommand,
    ) -> dict[str, Any]:
        if command.action_ruling is None:
            difficulties = {
                str(
                    item.get("difficulty", "regular") if isinstance(item, dict) else item.difficulty
                )
                for item in command.proposed_checks
            }
            resolution = (
                "opposed"
                if "opposed" in difficulties
                else "check"
                if command.proposed_checks
                else "automatic"
            )
            raw_ruling: Any = {
                "goal": command.player_action,
                "method": "人类 KP 手工裁定",
                "target": "当前场景",
                "feasibility": "possible",
                "resolution": resolution,
                "reason": "该草稿由人类 KP 创建；仍受检定与世界效果边界约束。",
                "maximum_effect": "仅限本草稿明确列出的公开叙述与候选效果。",
                "alternative": "",
            }
        else:
            raw_ruling = command.action_ruling
        validated = KpTurnOutput(
            public_narration=command.public_narration,
            kp_notes=command.kp_notes,
            action_ruling=ActionRuling.model_validate(raw_ruling),
            proposed_checks=list(command.proposed_checks),
            proposed_events=list(command.proposed_events),
            proposed_memories=list(command.proposed_memories),
            proposed_npc_updates=list(command.proposed_npc_updates),
            proposed_map_moves=list(command.proposed_map_moves),
            proposed_facts=list(command.proposed_facts),
        )
        return validated.action_ruling.model_dump(mode="json")

    def _attach_action_ruling(
        self,
        proposal_id: str,
        ruling: dict[str, Any],
    ) -> None:
        self.repo.add_proposal_action(
            proposal_id,
            "action_ruling",
            actor="system",
            note="goal feasibility and effect ceiling",
            payload=ruling,
        )

    def _attach_ai_skill_composition(self, proposal_id: str, result: Any) -> None:
        skill_ids = tuple(getattr(result, "skill_ids", ()))
        skill_versions = tuple(getattr(result, "skill_versions", ()))
        if not skill_ids:
            return
        if len(skill_ids) != len(skill_versions):
            raise ValueError("AI skill IDs and versions must have matching lengths")
        self.repo.add_proposal_action(
            proposal_id,
            "ai_skill_composition",
            actor="system",
            note="allow-listed proposal-only AI skills",
            payload={
                "skills": [
                    {"skill_id": skill_id, "version": version}
                    for skill_id, version in zip(skill_ids, skill_versions, strict=True)
                ]
            },
        )

    async def create_world_expansion_proposal(
        self,
        command: WorldExpansionCommand,
        identity: AuthenticatedMember,
        director: WorldExpansionDirector,
        *,
        source_model: str,
    ) -> dict:
        run = self.repo.get_campaign_module_run(command.run_id)
        control = AiControlService(self.repo).authorize(
            str(run["campaign_id"]),
            "world expansion proposal",
        )
        intent = unicodedata.normalize("NFKC", command.player_intent).strip()
        if not intent:
            raise InvalidInputError("Player intent is required")
        if bool(command.setting_pack_id) != bool(command.settlement_kind):
            raise InvalidInputError("Setting pack and settlement kind must be selected together")
        analysis = ModuleRunService(self.repo).analyze(command.run_id, intent)
        if analysis["decision"] != "world_gap":
            raise InvalidInputError(
                "World expansion requires a confirmed world_gap analysis; "
                f"current decision is {analysis['decision']}"
            )
        if analysis["reachability"].get("has_conflicts"):
            raise InvalidInputError("World expansion is blocked by explicit module graph conflicts")
        settlement_template = self._settlement_template(command, analysis, run)
        run = self.repo.get_campaign_module_run(command.run_id)
        world_fact_heads = self.repo.list_fact_heads(str(run["campaign_id"]))
        world_fact_head_hash = self._world_fact_head_hash(world_fact_heads)
        fingerprint = self._world_expansion_fingerprint(
            run,
            intent,
            world_fact_head_hash,
            settlement_template,
            command.requested_expansion_kind,
        )
        existing = self.repo.find_live_world_expansion(
            str(run["campaign_id"]),
            fingerprint=fingerprint,
        )
        if existing is not None:
            return existing
        location = next(
            (
                str(item["name"])
                for item in analysis["entity_states"]
                if item["entity_id"] == run.get("current_location_entity_id")
            ),
            None,
        )
        analysis_snapshot = self._world_expansion_analysis_snapshot(
            run,
            analysis,
            fingerprint,
            world_fact_head_hash,
            world_fact_heads,
            self._ai_settlement_template_snapshot(settlement_template, intent),
            command.requested_expansion_kind,
        )
        result = await director.handle_world_expansion(
            campaign_id=str(run["campaign_id"]),
            player_intent=intent,
            analysis_snapshot=analysis_snapshot,
            pc_id=command.pc_id,
            location=location,
            map_id=command.map_id,
            active_spoiler_tags=tuple(run["active_spoiler_tags"]),
        )

        validate_world_expansion_plan(
            output_candidate := result.output.candidate, analysis_snapshot
        )

        # The LLM call stays outside the write transaction. Revalidate the
        # session, run version and gap decision before persisting its draft.
        self.repo.begin_immediate()
        if not self.repo.is_session_member_active(identity.member_id, identity.session_id):
            raise KpSessionEndedError("KP session ended while the model was running")
        AiControlService(self.repo).revalidate(control)
        refreshed = self.repo.get_campaign_module_run(command.run_id)
        refreshed_fact_hash = self._world_fact_head_hash(
            self.repo.list_fact_heads(str(run["campaign_id"]))
        )
        if (
            refreshed["status"] != "active"
            or refreshed["campaign_id"] != run["campaign_id"]
            or refreshed["module_id"] != run["module_id"]
            or refreshed["version"] != run["version"]
            or refreshed_fact_hash != world_fact_head_hash
        ):
            raise ConflictError(
                "Module run changed while world expansion was generated; analyze again"
            )
        refreshed_analysis = ModuleRunService(self.repo).analyze(command.run_id, intent)
        if refreshed_analysis["decision"] != "world_gap":
            raise ConflictError(
                "World gap is no longer current; analyze again before creating a proposal"
            )
        existing = self.repo.find_live_world_expansion(
            str(run["campaign_id"]),
            fingerprint=fingerprint,
        )
        if existing is not None:
            return existing

        output = result.output
        proposal = self.repo.create_turn_proposal(
            campaign_id=run["campaign_id"],
            pc_id=command.pc_id,
            player_action=intent,
            public_narration=output.public_narration,
            kp_notes=output.kp_notes,
            proposed_checks=[],
            proposed_events=[],
            proposed_memories=[],
            proposed_npc_updates=[],
            proposed_map_moves=[],
            proposed_facts=[],
            source_model=source_model,
        )
        self.repo.attach_world_expansion_basis(
            proposal["id"],
            module_run_id=command.run_id,
            module_run_version=int(run["version"]),
            fingerprint=fingerprint,
            analysis=analysis_snapshot,
            candidate=output_candidate.model_dump(mode="json"),
        )
        self._attach_ai_skill_composition(str(proposal["id"]), result)
        self.repo.create_context_assembly(
            proposal_id=proposal["id"],
            campaign_id=run["campaign_id"],
            visibility_scope=result.context.visibility_scope,
            final_prompt=result.context.messages,
            included_sources=result.context.included_sources,
            excluded_sources=result.context.excluded_sources,
            token_estimate=result.context.token_estimate,
        )
        proposal = self.repo.get_turn_proposal(proposal["id"])
        self._append_proposal_created(
            identity.session_id,
            str(run["campaign_id"]),
            proposal,
        )
        return proposal

    def approve(
        self,
        proposal_id: str,
        campaign_id: str,
        identity: AuthenticatedMember,
        *,
        note: str = "",
        override_public_narration: str | None = None,
    ) -> dict:
        return self._approve(
            proposal_id,
            campaign_id,
            identity,
            note=note,
            override_public_narration=override_public_narration,
            parallel_batch_id=None,
            parallel_phase=None,
        )

    def approve_parallel_proposal(
        self,
        proposal_id: str,
        campaign_id: str,
        identity: AuthenticatedMember,
        *,
        batch_id: str,
        phase: Literal["check_request", "commit"],
        note: str = "",
        override_public_narration: str | None = None,
    ) -> dict:
        """Use the batch-scoped repository seam unavailable to HTTP routes."""

        batch = self.repo.get_parallel_action_batch(batch_id)
        if (
            identity.role != "kp"
            or identity.campaign_id != batch.get("campaign_id")
            or identity.session_id != batch.get("session_id")
            or not self.repo.is_session_member_active(
                identity.member_id,
                identity.session_id,
            )
        ):
            raise PermissionError("Parallel proposal approval requires the active batch KP")
        return self._approve(
            proposal_id,
            campaign_id,
            identity,
            note=note,
            override_public_narration=override_public_narration,
            parallel_batch_id=batch_id,
            parallel_phase=phase,
        )

    def _approve(
        self,
        proposal_id: str,
        campaign_id: str,
        identity: AuthenticatedMember,
        **options: Any,
    ) -> dict:
        # Workers may catch a conflict and commit unrelated job bookkeeping.
        # Keep narration, facts, states and outbox atomic even in that case.
        self.repo.begin_proposal_application()
        try:
            result = self._approve_effects(proposal_id, campaign_id, identity, **options)
            self.repo.finish_proposal_application()
            return result
        except Exception:
            self.repo.rollback_proposal_application()
            raise

    def _approve_effects(
        self,
        proposal_id: str,
        campaign_id: str,
        identity: AuthenticatedMember,
        *,
        note: str,
        override_public_narration: str | None,
        parallel_batch_id: str | None,
        parallel_phase: Literal["check_request", "commit"] | None,
    ) -> dict:
        pending = self.repo.get_turn_proposal(proposal_id)
        validate_unresolved_check_boundary(
            pending["proposed_checks"],
            pending["proposed_events"],
            pending["proposed_memories"],
            pending["proposed_npc_updates"],
            pending["proposed_map_moves"],
            pending["proposed_facts"],
            pending["proposed_world_entity_states"],
        )
        campaign = self.repo.get_campaign(campaign_id)
        ruleset = get_campaign_ruleset(campaign)
        planned_checks = plan_proposed_checks(pending, ruleset)
        # Own the request transaction before the repository opens its savepoint.
        # This keeps proposal effects, concrete checks, and outbox messages in one
        # unit that the request dependency can commit or roll back together.
        self.repo.begin_immediate()
        pending = self.repo.get_turn_proposal(proposal_id)
        self._validate_world_expansion_approval(pending, campaign_id)
        if parallel_batch_id is None or parallel_phase is None:
            if parallel_batch_id is not None or parallel_phase is not None:
                raise ValueError("Parallel proposal approval authority is incomplete")
            self.repo.approve_turn_proposal(
                proposal_id,
                actor=f"kp:{identity.member_id}",
                note=note,
                override_public_narration=override_public_narration,
            )
        else:
            self.repo.approve_parallel_turn_proposal(
                proposal_id,
                batch_id=parallel_batch_id,
                phase=parallel_phase,
                actor=f"kp:{identity.member_id}",
                note=note,
                override_public_narration=override_public_narration,
            )
        self._create_dynamic_branch_if_planned(pending, identity)
        run = self.repo.get_active_campaign_module_run(campaign_id)
        automation_level = (
            str(run.get("automation_level") or "conservative")
            if run is not None
            else "conservative"
        )
        auto_dedupe = automation_level == "ai_kp"
        applied_facts = []
        if pending["proposed_facts"]:
            fact_service = FactService(self.repo)
            for candidate in pending["proposed_facts"]:
                fact_type = str(candidate["fact_type"])
                subject = str(candidate["subject"])
                predicate = str(candidate["predicate"])
                # ai_kp 自动模式下，已存在相同 subject+predicate 的活动权威事实
                # 时跳过重复写入，避免连续回合生成相似事实导致整笔审批失败。
                # 人类 KP 模式保持严格：冲突即 409 回滚，由 KP 显式处理。
                if auto_dedupe and fact_type in {"canonical_fact", "kp_secret"}:
                    existing = self.repo.find_active_fact(
                        campaign_id,
                        category=fact_type,
                        subject=subject,
                        predicate=predicate,
                    )
                    if existing is not None:
                        object_text = str(candidate["object_text"])
                        if existing.fact.object_text == object_text:
                            continue
                        fact_service.retcon_fact(
                            campaign_id,
                            existing.fact_key,
                            identity,
                            RetconWorldFactCommand(
                                expected_head_event_id=existing.event_id,
                                reason=(f"Superseded by approved AI KP proposal {proposal_id}"),
                                evidence_event_ids=tuple(candidate.get("evidence_event_ids") or ()),
                                source_reference={
                                    "kind": "approved_turn_proposal_replacement",
                                    "proposal_id": proposal_id,
                                    "source_model": str(pending["source_model"]),
                                },
                                happened_at=candidate.get("happened_at"),
                            ),
                        )
                applied_facts.append(
                    fact_service.assert_fact(
                        campaign_id,
                        identity,
                        AssertWorldFactCommand(
                            fact_type=fact_type,
                            subject=subject,
                            predicate=predicate,
                            object_text=str(candidate["object_text"]),
                            pc_id=candidate.get("pc_id"),
                            evidence_event_ids=tuple(candidate.get("evidence_event_ids") or ()),
                            source_reference={
                                "kind": "approved_turn_proposal",
                                "proposal_id": proposal_id,
                                "source_model": str(pending["source_model"]),
                            },
                            happened_at=candidate.get("happened_at"),
                        ),
                    )
                )
            self.repo.add_proposal_action(
                proposal_id,
                "proposed_facts_applied",
                actor=f"kp:{identity.member_id}",
                note="approved typed world facts",
                payload={
                    "facts": [
                        {
                            "fact_key": fact["fact_key"],
                            "event_id": fact["event_id"],
                            "fact_type": fact["fact"]["category"],
                        }
                        for fact in applied_facts
                    ]
                },
            )
        if pending["proposed_world_entity_states"]:
            applied_states = []
            state_service = WorldEntityStateService(self.repo)
            for index, candidate in enumerate(
                pending["proposed_world_entity_states"]
            ):
                applied_states.append(
                    state_service.set_state(
                        campaign_id,
                        str(candidate["entity_id"]),
                        identity,
                        SetWorldEntityStateCommand(
                            expected_version=int(candidate["expected_version"]),
                            dimension=str(candidate["dimension"]),
                            value=candidate.get("value"),
                            visibility=str(candidate.get("visibility") or "table"),
                            idempotency_key=(
                                f"proposal:{proposal_id}:world-state:{index}"
                            ),
                            note=str(candidate.get("note") or ""),
                            source_kind="ai_kp",
                        ),
                    )
                )
            self.repo.add_proposal_action(
                proposal_id,
                "proposed_world_entity_states_applied",
                actor=f"kp:{identity.member_id}",
                note="approved typed world entity states",
                payload={
                    "changes": [
                        {
                            "change_id": item["change"]["id"],
                            "entity_id": item["change"]["entity_id"],
                            "dimension": item["change"]["dimension"],
                            "state_version": item["change"]["state_version"],
                        }
                        for item in applied_states
                    ]
                },
            )
        player_action_id = self.repo.player_action_id_for_proposal(proposal_id)
        for planned_check in planned_checks:
            self.repo.create_skill_check(
                campaign_id=campaign_id,
                session_id=identity.session_id,
                requested_by_member_id=identity.member_id,
                skill_name=planned_check.skill_name,
                difficulty=planned_check.difficulty,
                ruleset_id=ruleset.manifest.ruleset_id,
                ruleset_version=ruleset.manifest.version,
                source_reference=dict(ruleset.manifest.source_reference),
                bonus_dice=planned_check.bonus_dice,
                hidden=planned_check.hidden,
                allow_push=planned_check.allow_push,
                pc_id=planned_check.pc_id,
                proposal_id=proposal_id,
                player_action_id=player_action_id,
                check_plan=planned_check.check_plan,
            )
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=campaign_id,
            audience="kp",
            event_type="proposal.approved",
            resource_type="turn_proposal",
            resource_id=proposal_id,
            payload={"status": "approved"},
        )
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=campaign_id,
            audience="session",
            event_type="world.updated",
            resource_type="campaign",
            resource_id=campaign_id,
        )
        return self.repo.get_turn_proposal(proposal_id)

    def reject(
        self,
        proposal_id: str,
        campaign_id: str,
        identity: AuthenticatedMember,
        *,
        note: str = "",
    ) -> dict:
        # reject_turn_proposal uses a savepoint for its local state transition;
        # start the request transaction first so its outbox write is atomic too.
        self.repo.begin_immediate()
        proposal = self.repo.reject_turn_proposal(
            proposal_id,
            actor=f"kp:{identity.member_id}",
            note=note,
        )
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=campaign_id,
            audience="kp",
            event_type="proposal.rejected",
            resource_type="turn_proposal",
            resource_id=proposal_id,
            payload={"status": "rejected"},
        )
        return proposal

    def _queued_action(
        self,
        action_id: str | None,
        campaign_id: str,
        session_id: str,
    ) -> dict | None:
        if action_id is None:
            return None
        return self.repo.require_submitted_player_action(
            action_id,
            campaign_id,
            session_id,
        )

    def _append_proposal_created(
        self,
        session_id: str,
        campaign_id: str,
        proposal: dict,
    ) -> None:
        self.repo.append_realtime_event(
            session_id=session_id,
            campaign_id=campaign_id,
            audience="kp",
            event_type="proposal.created",
            resource_type="turn_proposal",
            resource_id=proposal["id"],
            payload={
                "status": proposal["status"],
                "proposal_kind": proposal["proposal_kind"],
            },
        )

    def _validate_world_expansion_approval(
        self,
        proposal: dict,
        campaign_id: str,
    ) -> None:
        basis = proposal.get("world_expansion")
        if basis is None:
            return
        run = self.repo.get_campaign_module_run(str(basis["module_run_id"]))
        if (
            run["campaign_id"] != campaign_id
            or run["module_id"] != basis["module_id"]
            or run["module_source_hash"] != basis["module_source_hash"]
            or run["status"] != "active"
            or run["version"] != basis["module_run_version"]
            or self._world_fact_head_hash(self.repo.list_fact_heads(campaign_id))
            != basis["analysis"]["world_fact_head_hash"]
        ):
            raise ConflictError(
                "World expansion proposal is stale because its module run changed; "
                "reject it and analyze again"
            )
        candidate = WorldExpansionCandidate.model_validate(basis["candidate"])
        validate_world_expansion_plan(candidate, basis["analysis"])

    def _create_dynamic_branch_if_planned(
        self,
        proposal: dict,
        identity: AuthenticatedMember,
    ) -> None:
        basis = proposal.get("world_expansion")
        if basis is None:
            return
        candidate = WorldExpansionCandidate.model_validate(basis["candidate"])
        if candidate.branch_plan is None:
            return
        self.repo.create_dynamic_branch_run(
            proposal_id=str(proposal["id"]),
            campaign_id=str(proposal["campaign_id"]),
            module_run_id=str(basis["module_run_id"]),
            plan=candidate.branch_plan.model_dump(mode="json"),
            member_id=identity.member_id,
        )

    @staticmethod
    def _world_expansion_fingerprint(
        run: dict,
        intent: str,
        world_fact_head_hash: str,
        settlement_template: dict[str, Any] | None = None,
        requested_expansion_kind: str = "environment",
    ) -> str:
        basis = json.dumps(
            {
                "run_id": run["id"],
                "run_version": run["version"],
                "intent": intent,
                "world_fact_head_hash": world_fact_head_hash,
                "settlement_template": settlement_template,
                "requested_expansion_kind": requested_expansion_kind,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    @staticmethod
    def _world_expansion_analysis_snapshot(
        run: dict,
        analysis: dict,
        fingerprint: str,
        world_fact_head_hash: str,
        world_fact_heads: Sequence[Any],
        settlement_template: dict[str, Any] | None = None,
        requested_expansion_kind: str = "environment",
    ) -> dict:
        return {
            "fingerprint": fingerprint,
            "decision": analysis["decision"],
            "reasons": list(analysis["reasons"]),
            "scene": dict(analysis["scene"]),
            "module_id": run["module_id"],
            "module_title": run["module_title"],
            "module_source_hash": run["module_source_hash"],
            "module_run_id": run["id"],
            "module_run_version": run["version"],
            "active_spoiler_tags": list(run["active_spoiler_tags"]),
            "unreachable_anchor_count": analysis["unreachable_anchor_count"],
            "deferred_source_count": analysis["deferred_source_count"],
            "world_fact_head_hash": world_fact_head_hash,
            "world_fact_heads": TurnService._fact_head_snapshot(world_fact_heads),
            "settlement_template": settlement_template,
            "requested_expansion_kind": requested_expansion_kind,
            "entity_states": [
                {
                    "entity_id": item["entity_id"],
                    "entity_type": item["entity_type"],
                    "name": item["name"],
                    "status": item["status"],
                }
                for item in analysis["entity_states"]
            ],
            "anchors": [
                {
                    "entity_id": item["entity_id"],
                    "name": item["name"],
                    "reachable": item["reachable"],
                }
                for item in analysis["reachability"].get("anchors", [])
            ],
            "writes_performed": analysis["writes_performed"],
        }

    @staticmethod
    def _ai_settlement_template_snapshot(
        template: dict[str, Any] | None,
        intent: str,
    ) -> dict[str, Any] | None:
        """Keep the full profile for KP tools, but send the model only relevant choices."""

        if template is None:
            return None
        intent_tokens = tokenize(intent)
        scored_slots: list[tuple[int, str]] = []
        for slot in template.get("scene_slots") or ():
            if not slot.get("selected"):
                continue
            searchable = " ".join(
                [
                    str(slot.get("slot_id") or ""),
                    str(slot.get("function") or ""),
                    *[str(item) for item in slot.get("building_candidates") or ()],
                    *[str(item) for item in slot.get("play_affordances") or ()],
                ]
            )
            score = len(intent_tokens & tokenize(searchable))
            if score:
                scored_slots.append((score, str(slot["slot_id"])))
        scored_slots.sort(key=lambda item: (-item[0], item[1]))
        selected_ids = {slot_id for _, slot_id in scored_slots[:4]}
        selected_ids.update(
            str(binding["slot_id"])
            for binding in template.get("source_entity_bindings") or ()
            if binding.get("slot_id")
        )
        if not selected_ids:
            selected_ids.update(
                str(slot["slot_id"])
                for slot in (template.get("scene_slots") or ())[:4]
                if slot.get("selected") and slot.get("frequency") == "core"
            )

        compact_slots = []
        for slot in template.get("scene_slots") or ():
            if str(slot.get("slot_id")) not in selected_ids:
                continue
            compact_slots.append(
                {
                    key: slot[key]
                    for key in (
                        "scene_id",
                        "slot_id",
                        "function",
                        "frequency",
                        "access_scope",
                        "building_candidates",
                        "play_affordances",
                        "profession_candidates",
                        "entity_archetype_candidates",
                        "service_capabilities",
                        "record_sources",
                        "access_patterns",
                        "investigation_surfaces",
                        "event_seed_kinds",
                        "selected",
                    )
                    if key in slot
                }
            )
        return {
            key: value
            for key, value in template.items()
            if key not in {"scene_slots", "agent_workflow"}
        } | {"scene_slots": compact_slots}

    def _settlement_template(
        self,
        command: WorldExpansionCommand,
        analysis: dict[str, Any],
        run: dict[str, Any],
    ) -> dict[str, Any] | None:
        selection = self.repo.get_module_run_setting_selection(command.run_id)
        if selection is not None:
            profile = selection["profile"]
            document = profile["document"]
            configured = next(
                (
                    item
                    for item in document.get("settlements", ())
                    if item.get("settlement_id") == selection["settlement_id"]
                ),
                None,
            )
            if configured is None:
                raise InvalidInputError(
                    "Selected setting profile no longer contains its settlement"
                )
            selected_pack_id = str(profile["setting_pack_id"])
            selected_kind = str(configured["settlement_kind"])
            if command.setting_pack_id is not None and (
                command.setting_pack_id != selected_pack_id
                or command.settlement_kind != selected_kind
            ):
                raise InvalidInputError(
                    "Explicit setting parameters conflict with the run setting selection"
                )
            resolved = ModuleSettingAnalysisService(
                self.repo
            ).analyze_profile_settlement(
                profile_id=str(selection["profile_id"]),
                profile_version=int(selection["profile_version"]),
                settlement_id=str(selection["settlement_id"]),
            )
            return {
                **resolved["settlement_template"],
                "source_coverage": resolved["source_coverage"],
                "source_entity_bindings": resolved["source_entity_bindings"],
                "setting_profile": {
                    "profile_id": resolved["profile_id"],
                    "profile_version": resolved["profile_version"],
                    "profile_content_hash": resolved["profile_content_hash"],
                    "settlement_id": selection["settlement_id"],
                    "selection_version": selection["version"],
                    "run_version": run["version"],
                },
            }
        if command.setting_pack_id is None or command.settlement_kind is None:
            return None
        try:
            pack = get_setting_pack(command.setting_pack_id)
        except KeyError as exc:
            raise InvalidInputError(str(exc)) from exc
        skeleton = instantiate_settlement(
            pack,
            settlement_id=f"world-gap:{command.run_id}",
            kind=command.settlement_kind,
        )
        mentions = tuple(
            LocationMention(
                mention_id=str(item["entity_id"]),
                title=str(item["name"]),
            )
            for item in analysis.get("entity_states", ())
            if item.get("entity_type") == "location" and item.get("name")
        )
        return {
            **skeleton.model_dump(mode="json"),
            "source_coverage": associate_location_mentions(skeleton, mentions).model_dump(
                mode="json"
            ),
        }

    @staticmethod
    def _fact_head_snapshot(heads: Sequence[Any]) -> list[dict[str, Any]]:
        payload = [item.as_dict() if hasattr(item, "as_dict") else dict(item) for item in heads]
        return [
            {
                "fact_key": item.get("fact_key"),
                "category": (item.get("fact") or {}).get("category"),
                "subject": (item.get("fact") or {}).get("subject"),
                "predicate": (item.get("fact") or {}).get("predicate"),
                "object_text": (item.get("fact") or {}).get("object_text"),
            }
            for item in sorted(payload, key=lambda value: str(value.get("fact_key", "")))
        ]

    @staticmethod
    def _world_fact_head_hash(heads: Sequence[Any]) -> str:
        payload = [item.as_dict() if hasattr(item, "as_dict") else dict(item) for item in heads]
        encoded = json.dumps(
            sorted(payload, key=lambda item: str(item.get("fact_key", ""))),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

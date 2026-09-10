"""Contract-first preparation and authoritative commit for one player action."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ai_kp.application.ai_control_service import AiControlService
from ai_kp.application.errors import ConflictError, UpstreamServiceError
from ai_kp.application.ports.director import KernelDirector
from ai_kp.application.resolution_transaction_service import (
    ResolutionTransactionService,
)
from ai_kp.application.scenario_authority import (
    ScenarioAuthorityContext,
    load_active_scenario_authority_context,
    load_scenario_authority_context,
    revalidate_scenario_authority_context,
)
from ai_kp.application.scenario_overlay_service import ScenarioOverlayService
from ai_kp.application.turn_service import ManualProposalCommand, TurnService
from ai_kp.director.errors import CampaignAiCallCancelledError
from ai_kp.platform.modules.public_opening import extract_public_opening
from ai_kp.platform.resolution.action_catalog import ScenarioActionCatalogProjector
from ai_kp.platform.resolution.action_intent_ir import ActionIntentPlan
from ai_kp.platform.resolution.authority_basis import KernelAuthorityBasis
from ai_kp.platform.resolution.contracts import (
    ResolutionPreview,
    ResponseObligation,
    ScenarioContract,
)
from ai_kp.platform.resolution.explicit_operator_selection import (
    resolve_explicit_published_operator,
)
from ai_kp.platform.resolution.narrative_adapter import KernelNarrativeBundle
from ai_kp.platform.resolution.planning import BoundedTaskPlanner, PlanRequest
from ai_kp.platform.resolution.selected_action import (
    SelectedOperator,
    prepare_selected_kernel_action,
    selected_operator_from_semantic,
)
from ai_kp.platform.resolution.semantic_adapter import (
    SemanticCandidate,
    SemanticSelection,
    SemanticSelectionResult,
)
from ai_kp.platform.resolution.tabletop_response import TabletopConversationResponse
from ai_kp.platform.resolution.tabletop_turn import (
    TabletopTurnInterpretation,
    TabletopTurnPolicy,
    opening_participant_ids,
)
from ai_kp.platform.sessions.models import AuthenticatedMember

if TYPE_CHECKING:
    from ai_kp.application.parallel_action_planning_service import (
        PreparedParallelAction,
    )


class KernelActionService:
    def __init__(self, repo: Any):
        self.repo = repo
        self.turns = TurnService(repo)

    def bound_context(self, campaign_id: str) -> ScenarioAuthorityContext | None:
        return load_active_scenario_authority_context(
            self.repo,
            campaign_id,
            state_policy="initialize",
        )

    def _opening_tabletop_context(self, run_id: str, contract, snapshot):
        """Project explicitly named opening actors into conversation, without writes."""

        if (
            snapshot.scene_id is None
            or snapshot.scene_id != contract.initial_scene_id
        ):
            return contract, snapshot
        run = self.repo.get_campaign_module_run(run_id)
        opening = extract_public_opening(self.repo.list_module_chunks(
            str(run["module_id"]),
            allowed_visibility=("player", "table", "kp", "secret"),
            spoiler_tags=None,
        ))
        participants = set(opening_participant_ids(contract, opening))
        if not participants:
            return contract, snapshot

        projected_entities = []
        projected_runtime = dict(snapshot.entity_runtime)
        opening_obligations = list(contract.response_obligations)
        for entity in contract.entities:
            if entity.entity_id not in participants or entity.initial_location_id is not None:
                projected_entities.append(entity)
                continue
            runtime = projected_runtime.get(entity.entity_id, entity.initial_runtime)
            canonical_profile = entity.canonical_profile.model_copy(update={
                "known_facts": tuple(dict.fromkeys((
                    *entity.canonical_profile.known_facts,
                    opening,
                )))[:32],
            })
            projected = entity.model_copy(update={
                "initial_location_id": snapshot.scene_id,
                "canonical_profile": canonical_profile,
                "initial_runtime": (
                    runtime.model_copy(update={"location_id": snapshot.scene_id})
                    if runtime is not None else None
                ),
            })
            projected_entities.append(projected)
            obligation_id = f"opening-public:{entity.entity_id}"[:160]
            if not any(
                item.obligation_id == obligation_id for item in opening_obligations
            ):
                opening_obligations.append(ResponseObligation(
                    obligation_id=obligation_id,
                    entity_id=entity.entity_id,
                    facts_to_convey=(opening,),
                ))
            if runtime is not None:
                projected_runtime[entity.entity_id] = runtime.model_copy(
                    update={"location_id": snapshot.scene_id}
                )
        return (
            contract.model_copy(update={
                "entities": tuple(projected_entities),
                "response_obligations": tuple(opening_obligations),
            }),
            snapshot.model_copy(update={"entity_runtime": projected_runtime}),
        )

    async def prepare_unbound_tabletop(
        self,
        action: dict,
        identity: AuthenticatedMember,
        director: KernelDirector,
        *,
        source_model: str,
    ) -> dict | None:
        """Handle safe non-mechanical turns when no scenario contract is bound.

        Legacy and newly created campaigns still need the ruleset-neutral table
        protocol.  An empty authority basis deliberately cannot invent NPCs,
        locations, or world facts; mechanical frames continue through the
        compatibility proposal path until a real contract is published.
        """

        campaign_id = str(action["campaign_id"])
        contract = ScenarioContract(
            contract_id=f"unbound-tabletop:{campaign_id}",
            source_version=1,
            ruleset_id="unbound",
            title="Unbound tabletop conversation boundary",
        )
        snapshot = contract.initial_snapshot(f"unbound:{campaign_id}")
        control_service = AiControlService(self.repo)
        control = control_service.authorize(
            campaign_id, "unbound tabletop turn interpretation"
        )
        interpretation = await director.interpret_tabletop_turn(
            campaign_id=campaign_id,
            contract=contract,
            snapshot=snapshot,
            player_action=str(action["action_text"]),
        )
        control_service.revalidate(control)
        if interpretation.route == "mechanical":
            return None
        if interpretation.route == "clarification":
            self.repo.begin_immediate()
            control_service.revalidate(control)
            proposal = self._clarification_proposal(
                action,
                identity,
                TabletopTurnPolicy.clarification(
                    interpretation.frame, contract, snapshot
                ),
                source_model=source_model,
            )
            self._attach_tabletop_turn(proposal, interpretation)
            return self.repo.get_turn_proposal(str(proposal["id"]))
        control = control_service.authorize(
            campaign_id, "unbound tabletop conversation response"
        )
        response = await director.respond_tabletop_turn(
            campaign_id=campaign_id,
            contract=contract,
            snapshot=snapshot,
            player_action=str(action["action_text"]),
            frame=interpretation.frame,
            route=interpretation.route,
        )
        control_service.revalidate(control)
        self.repo.begin_immediate()
        control_service.revalidate(control)
        proposal = self._conversation_proposal(
            action,
            identity,
            interpretation,
            response,
            source_model=source_model,
        )
        self._attach_tabletop_turn(proposal, interpretation, response=response)
        return self.repo.get_turn_proposal(str(proposal["id"]))

    def manual_catalog(self, action: dict) -> list[dict[str, Any]]:
        """Expose deterministic primitive choices for a human KP without an LLM."""

        context = self.bound_context(str(action["campaign_id"]))
        if context is None:
            raise KeyError("Player action has no bound scenario contract")
        catalog = ScenarioActionCatalogProjector.project(
            context.contract,
            context.snapshot,
            str(action["action_text"]),
            include_task_methods=False,
            limit=max(1, len(context.contract.operators)),
            minimum_score=None,
        )
        return [
            {
                "candidate_id": item.candidate_id,
                "title": item.title,
                "available": item.available,
                "score": item.score,
                "skill_choices": [
                    choice.model_dump(mode="json")
                    for choice in item.skill_choices
                ],
            }
            for item in catalog.candidates
        ]

    def prepare_manual(
        self,
        action: dict,
        identity: AuthenticatedMember,
        *,
        operator_id: str,
        requested_skill_key: str | None,
    ) -> tuple[dict, ResolutionPreview]:
        """Create the same player-confirmed kernel preview selected by a human KP."""

        context = self.bound_context(str(action["campaign_id"]))
        if context is None:
            raise KeyError("Player action has no bound scenario contract")
        selection = SemanticSelection(
            kind="operator",
            candidate_id=operator_id,
            requested_skill_key=requested_skill_key,
            confidence="high",
        )
        prepared = prepare_selected_kernel_action(
            context.contract,
            context.snapshot,
            action_id=str(action["id"]),
            actor_id=str(action.get("pc_id") or action["member_id"]),
            player_action=str(action["action_text"]),
            selection=SelectedOperator(
                operator_id=operator_id,
                requested_skill_key=requested_skill_key,
            ),
        )
        proposal = self._proposal_from_preview(
            action,
            identity,
            prepared.preview,
            narrative=prepared.deterministic_narrative,
            source_model="human-kp",
        )
        allowed_skills = tuple(
            choice.skill_key for choice in prepared.operator.skill_choices
        )
        selection = SemanticSelectionResult(
            selection=selection,
            offered_candidates=(
                SemanticCandidate(
                    candidate_id=operator_id,
                    kind="operator",
                    title=prepared.operator.title,
                    allowed_skill_keys=allowed_skills,
                    available=prepared.preview.allowed,
                ),
            ),
            allowed_skill_keys=allowed_skills,
            attempt_count=0,
        )
        self._attach_selection(
            proposal,
            selection,
            preview=prepared.preview,
            run_id=context.run_id,
            narrative=prepared.deterministic_narrative,
            authority=context,
        )
        return self.repo.get_turn_proposal(str(proposal["id"])), prepared.preview

    def persist_prepared_parallel(
        self,
        action: dict[str, Any],
        identity: AuthenticatedMember,
        prepared: PreparedParallelAction,
    ) -> tuple[dict[str, Any], ResolutionPreview]:
        """Persist one read-only parallel plan inside the caller's transaction.

        The method performs no model call and does not begin or commit a
        transaction.  It recomputes the preview from current authoritative data
        so a stale or substituted prepared value fails before a proposal is
        linked to the player action.
        """

        connection = getattr(self.repo, "connection", None)
        if connection is None or not connection.in_transaction:
            raise ConflictError(
                "Parallel plan persistence requires a caller-owned transaction"
            )

        if (
            identity.role != "kp"
            or identity.campaign_id != prepared.campaign_id
            or identity.session_id != prepared.session_id
            or not self.repo.is_session_member_active(
                identity.member_id, prepared.session_id
            )
        ):
            raise PermissionError(
                "Parallel plans require the active campaign session KP"
            )
        authoritative = self.repo.require_submitted_player_action(
            str(action["id"]),
            str(action["campaign_id"]),
            str(action["session_id"]),
        )
        expected_action = {
            "id": prepared.action_id,
            "campaign_id": prepared.campaign_id,
            "session_id": prepared.session_id,
            "member_id": prepared.member_id,
            "pc_id": prepared.pc_id,
            "action_text": prepared.action_text,
        }
        if any(
            authoritative.get(key) != value
            for key, value in expected_action.items()
        ) or prepared.actor_id != str(
            authoritative.get("pc_id") or authoritative["member_id"]
        ):
            raise ValueError("Prepared parallel plan belongs to a different action")
        if not self.repo.is_session_member_active(
            str(authoritative["member_id"]), str(authoritative["session_id"])
        ):
            raise ConflictError(
                "Parallel plan persistence requires every action owner to remain active"
            )
        authority = load_active_scenario_authority_context(
            self.repo,
            prepared.campaign_id,
            state_policy="require",
        )
        if (
            authority is None
            or authority.run_id != prepared.run_id
            or authority.module_run_version != prepared.module_run_version
            or authority.director_control_mode != "ai_assist"
        ):
            raise ValueError("Scenario run changed before parallel plan persistence")
        if (
            authority.contract_version_id != prepared.contract_version_id
            or authority.contract_hash != prepared.contract_hash
            or authority.state_version != prepared.base_state_version
        ):
            raise ValueError("Scenario authority changed before parallel plan persistence")
        authoritative_preparation = prepare_selected_kernel_action(
            authority.contract,
            authority.snapshot,
            action_id=prepared.action_id,
            actor_id=prepared.actor_id,
            player_action=prepared.action_text,
            selection=SelectedOperator(
                operator_id=prepared.operator_id,
                requested_skill_key=prepared.selected_skill_key,
            ),
        )
        expected_preview = authoritative_preparation.preview
        if not expected_preview.allowed or expected_preview != prepared.preview:
            raise ValueError("Prepared parallel preview no longer matches authority")
        selected = prepared.selection.selection
        if (
            selected.kind != "operator"
            or selected.candidate_id != prepared.operator_id
            or selected.requested_skill_key != prepared.selected_skill_key
            or (
                prepared.requested_skill_key is not None
                and prepared.requested_skill_key != prepared.selected_skill_key
            )
        ):
            raise ValueError("Prepared semantic selection does not match its preview")
        expected_narrative = authoritative_preparation.deterministic_narrative
        if expected_narrative != prepared.deterministic_narrative:
            raise ValueError("Prepared deterministic narrative does not match authority")
        if prepared.narrative != expected_narrative:
            raise ValueError(
                "Parallel proposals require their exact deterministic narrative"
            )

        proposal = self._proposal_from_preview(
            authoritative,
            identity,
            expected_preview,
            narrative=prepared.narrative,
            source_model=prepared.source_model,
        )
        self._attach_selection(
            proposal,
            prepared.selection,
            preview=expected_preview,
            run_id=prepared.run_id,
            narrative=prepared.narrative,
            tabletop_turn=prepared.tabletop_turn,
            authority=authority,
        )
        return self.repo.get_turn_proposal(str(proposal["id"])), expected_preview

    async def prepare(
        self,
        action: dict,
        identity: AuthenticatedMember,
        director: KernelDirector,
        *,
        source_model: str,
        profile: str = "small",
    ) -> tuple[dict, ResolutionPreview | None]:
        context = self.bound_context(str(action["campaign_id"]))
        if context is None:
            raise KeyError("Player action has no bound scenario contract")
        authority = context
        contract = authority.contract
        snapshot = authority.snapshot
        tabletop_contract, tabletop_snapshot = self._opening_tabletop_context(
            authority.run_id, contract, snapshot
        )
        campaign_id = str(action["campaign_id"])
        player_action = str(action["action_text"])
        control_service = AiControlService(self.repo)
        explicit = resolve_explicit_published_operator(
            contract, snapshot, player_action
        )
        if explicit is None:
            control = control_service.authorize(
                campaign_id, "tabletop turn interpretation"
            )
            interpretation = await director.interpret_tabletop_turn(
                campaign_id=campaign_id,
                contract=tabletop_contract,
                snapshot=tabletop_snapshot,
                player_action=player_action,
            )
            control_service.revalidate(control)
            revalidate_scenario_authority_context(self.repo, authority)
        else:
            control = control_service.authorize(
                campaign_id, "explicit published operator selection"
            )
            interpretation = explicit.interpretation
            control_service.revalidate(control)
            if explicit.selection_result is None:
                self.repo.begin_immediate()
                control_service.revalidate(control)
                revalidate_scenario_authority_context(self.repo, authority)
                proposal = self._clarification_proposal(
                    action, identity, explicit.reason, source_model=source_model
                )
                self._attach_tabletop_turn(proposal, interpretation)
                return self.repo.get_turn_proposal(str(proposal["id"])), None
        if interpretation.route == "clarification":
            self.repo.begin_immediate()
            control_service.revalidate(control)
            revalidate_scenario_authority_context(self.repo, authority)
            prompt = TabletopTurnPolicy.clarification(
                interpretation.frame, tabletop_contract, tabletop_snapshot
            )
            proposal = self._clarification_proposal(
                action, identity, prompt, source_model=source_model
            )
            self._attach_tabletop_turn(proposal, interpretation)
            return self.repo.get_turn_proposal(str(proposal["id"])), None
        if interpretation.route in {"conversation", "information", "roleplay"}:
            control = control_service.authorize(campaign_id, "tabletop conversation response")
            response = await director.respond_tabletop_turn(
                campaign_id=campaign_id,
                contract=tabletop_contract,
                snapshot=tabletop_snapshot,
                player_action=player_action,
                frame=interpretation.frame,
                route=interpretation.route,
            )
            control_service.revalidate(control)
            revalidate_scenario_authority_context(self.repo, authority)
            self.repo.begin_immediate()
            control_service.revalidate(control)
            revalidate_scenario_authority_context(self.repo, authority)
            proposal = self._conversation_proposal(
                action,
                identity,
                interpretation,
                response,
                source_model=source_model,
            )
            self._attach_tabletop_turn(proposal, interpretation, response=response)
            return self.repo.get_turn_proposal(str(proposal["id"])), None
        if explicit is not None:
            selection_result = explicit.selection_result
            if selection_result is None:  # guarded by the deterministic branch above
                raise RuntimeError("Explicit operator selection lost its authority result")
        else:
            control = control_service.authorize(campaign_id, "kernel action selection")
            selection_result = await director.select_kernel_action(
                campaign_id=campaign_id,
                contract=contract,
                snapshot=snapshot,
                player_action=player_action,
                profile=profile,
            )
            control_service.revalidate(control)
            revalidate_scenario_authority_context(self.repo, authority)
        selection = selection_result.selection
        task_plan = None
        task_method = None
        action_status = "executable"
        intent_plan = None
        if selection.kind == "clarification":
            control = control_service.authorize(
                campaign_id, "kernel world expansion authoring"
            )
            authored = await director.author_kernel_world_expansion(
                campaign_id=campaign_id,
                contract=contract,
                snapshot=snapshot,
                player_action=player_action,
                proposal_id=str(action["id"])[:64],
                tabletop_frame=interpretation.frame,
            )
            action_status = authored.action_status
            intent_plan = authored.intent_plan
            self.repo.begin_immediate()
            control_service.revalidate(control)
            revalidate_scenario_authority_context(self.repo, authority)
            if authored.proposal is not None:
                run = self.repo.get_active_campaign_module_run(
                    str(action["campaign_id"])
                )
                if run is None or str(run["id"]) != authority.run_id:
                    selection = selection.model_copy(
                        update={
                            "clarification": (
                                "模组运行状态已变化，本次未写入世界扩展；请重新提交行动。"
                            )
                        }
                    )
                    selection_result = selection_result.model_copy(
                        update={"selection": selection}
                    )
                else:
                    overlay = ScenarioOverlayService(self.repo).propose(
                        str(run["id"]),
                        authored.proposal,
                        created_by_member_id=identity.member_id,
                    )
                    if overlay["status"] == "active":
                        # A single newly authored operator is already bound to this
                        # exact action, state version and player-authorized skill set.
                        # Selecting it again with a model adds no authority and can
                        # turn a valid weak-model result back into clarification.
                        self.repo.commit()
                        refreshed = self.bound_context(str(action["campaign_id"]))
                        if refreshed is None:
                            selection = selection.model_copy(
                                update={
                                    "clarification": (
                                        "模组运行状态已变化，请重新提交行动。"
                                    )
                                }
                            )
                            selection_result = selection_result.model_copy(
                                update={"selection": selection}
                            )
                        else:
                            authority = refreshed
                            contract = authority.contract
                            snapshot = authority.snapshot
                            new_operators = authored.proposal.records.operators
                            if len(new_operators) == 1:
                                operator = new_operators[0]
                                allowed_skills = tuple(
                                    item.skill_key for item in operator.skill_choices
                                )
                                selection_result = SemanticSelectionResult(
                                    selection=SemanticSelection(
                                        kind="operator",
                                        candidate_id=operator.operator_id,
                                        requested_skill_key=(
                                            allowed_skills[0]
                                            if len(allowed_skills) == 1
                                            else None
                                        ),
                                        confidence="high",
                                    ),
                                    offered_candidates=(SemanticCandidate(
                                        candidate_id=operator.operator_id,
                                        kind="operator",
                                        title=operator.title,
                                        allowed_skill_keys=allowed_skills,
                                        available=True,
                                    ),),
                                    allowed_skill_keys=allowed_skills,
                                    attempt_count=0,
                                )
                            elif len(authored.proposal.records.task_methods) == 1:
                                # The additive method is server-bound to this exact
                                # action and preserves the audited dependency graph.
                                # A second model selection can only lose that binding.
                                method = authored.proposal.records.task_methods[0]
                                allowed_skills = tuple(dict.fromkeys(
                                    choice.skill_key
                                    for operator in new_operators
                                    for choice in operator.skill_choices
                                ))
                                selection_result = SemanticSelectionResult(
                                    selection=SemanticSelection(
                                        kind="task_method",
                                        candidate_id=method.method_id,
                                        confidence="high",
                                    ),
                                    offered_candidates=(SemanticCandidate(
                                        candidate_id=method.method_id,
                                        kind="task_method",
                                        title=method.title,
                                        allowed_skill_keys=allowed_skills,
                                        available=True,
                                    ),),
                                    allowed_skill_keys=allowed_skills,
                                    attempt_count=0,
                                )
                            else:
                                control = control_service.authorize(
                                    campaign_id, "expanded kernel action selection"
                                )
                                selection_result = await director.select_kernel_action(
                                    campaign_id=campaign_id,
                                    contract=contract,
                                    snapshot=snapshot,
                                    player_action=player_action,
                                    profile=profile,
                                )
                                control_service.revalidate(control)
                                revalidate_scenario_authority_context(
                                    self.repo, authority
                                )
                            selection = selection_result.selection
                    elif overlay["status"] == "review_required":
                        action_status = "possible_but_underspecified"
                        selection = selection.model_copy(
                            update={
                                "clarification": (
                                    "已生成受约束世界扩展候选，等待 KP 审核后才能执行。"
                                )
                            }
                        )
                        selection_result = selection_result.model_copy(
                            update={"selection": selection}
                        )
                    elif overlay["status"] == "rejected":
                        action_status = "possible_but_underspecified"
                        selection = selection.model_copy(
                            update={
                                "clarification": (
                                    "这个补全方案没有通过规则与世界状态校验。请补充行动的"
                                    "现有依据、资源来源和预期效果上限后重新裁定。"
                                )
                            }
                        )
                        selection_result = selection_result.model_copy(
                            update={"selection": selection}
                        )
            elif authored.validation_errors:
                clarification = authored.validation_errors[-1]
                if authored.action_status == "executable":
                    clarification = (
                        "你的目标和做法已经足够具体，但自动 KP 暂未能把它编排成可安全"
                        "结算的步骤。本次没有执行也没有写入世界状态；系统可以重试，或你"
                        "可以把连续步骤分开提交。"
                    )
                selection = selection.model_copy(
                    update={"clarification": clarification}
                )
                selection_result = selection_result.model_copy(
                    update={"selection": selection}
                )
        if selection.kind == "clarification":
            self.repo.begin_immediate()
            control_service.revalidate(control)
            revalidate_scenario_authority_context(self.repo, authority)
            proposal = self._clarification_proposal(
                action,
                identity,
                selection.clarification or "请补充具体做法。",
                source_model=source_model,
            )
            self._attach_selection(
                proposal,
                selection_result,
                preview=None,
                run_id=authority.run_id,
                action_status=action_status,
                intent_plan=intent_plan,
                tabletop_turn=interpretation,
            )
            return self.repo.get_turn_proposal(str(proposal["id"])), None

        if selection.kind == "task_method":
            try:
                plan = BoundedTaskPlanner(contract).preview(
                    snapshot,
                    PlanRequest(
                        plan_id=f"plan:{action['id']}",
                        action_id=str(action["id"]),
                        actor_id=str(action.get("pc_id") or action["member_id"]),
                        task_key=next(
                            item.task_key
                            for item in contract.task_methods
                            if item.method_id == selection.candidate_id
                        ),
                        method_id=str(selection.candidate_id),
                    ),
                )
            except (KeyError, ValueError) as exc:
                self.repo.begin_immediate()
                control_service.revalidate(control)
                revalidate_scenario_authority_context(self.repo, authority)
                proposal = self._clarification_proposal(
                    action,
                    identity,
                    f"该计划还缺少可验证的参与者或前置状态：{exc}",
                    source_model=source_model,
                )
                self._attach_selection(
                    proposal,
                    selection_result,
                    preview=None,
                    run_id=authority.run_id,
                    tabletop_turn=interpretation,
                )
                return self.repo.get_turn_proposal(str(proposal["id"])), None
            task_method = next(
                item
                for item in contract.task_methods
                if item.method_id == selection.candidate_id
            )
            task_plan = plan
            first = plan.steps[0].preview
            prepared = prepare_selected_kernel_action(
                contract,
                snapshot,
                action_id=str(action["id"]),
                actor_id=str(action.get("pc_id") or action["member_id"]),
                player_action=player_action,
                selection=SelectedOperator(
                    operator_id=first.operator_id,
                    requested_skill_key=first.selected_skill_key,
                ),
            )
        else:
            prepared = prepare_selected_kernel_action(
                contract,
                snapshot,
                action_id=str(action["id"]),
                actor_id=str(action.get("pc_id") or action["member_id"]),
                player_action=player_action,
                selection=selected_operator_from_semantic(selection),
            )
        preview = prepared.preview
        narrative = prepared.deterministic_narrative
        if profile == "large":
            try:
                control = control_service.authorize(
                    campaign_id, "kernel outcome narration"
                )
                narrative = await director.narrate_kernel_action(
                    campaign_id=campaign_id,
                    contract=contract,
                    preview=preview,
                    snapshot=snapshot,
                    player_action=player_action,
                )
                control_service.revalidate(control)
                revalidate_scenario_authority_context(self.repo, authority)
            except CampaignAiCallCancelledError:
                raise
            except (RuntimeError, UpstreamServiceError):
                # Presentation is optional; authority and playability are not.
                pass
        self.repo.begin_immediate()
        control_service.revalidate(control)
        revalidate_scenario_authority_context(self.repo, authority)
        if task_plan is not None and task_method is not None:
            durable_plan = self.repo.create_kernel_plan(
                run_id=authority.run_id,
                root_action_id=str(action["id"]),
                contract_hash=authority.contract_hash,
                method_id=task_method.method_id,
                task_key=task_method.task_key,
                steps=[
                    (item.step_id, item.preview.operator_id)
                    for item in task_plan.steps
                ],
            )
            self.repo.record_kernel_plan_preview(
                str(durable_plan["id"]),
                0,
                action_id=str(action["id"]),
                preview_hash=preview.preview_hash,
            )
        proposal = self._proposal_from_preview(
            action,
            identity,
            preview,
            narrative=narrative,
            source_model=source_model,
        )
        self._attach_selection(
            proposal,
            selection_result,
            preview=preview,
            run_id=authority.run_id,
            narrative=narrative,
            intent_plan=intent_plan,
            tabletop_turn=interpretation,
            authority=authority,
        )
        return self.repo.get_turn_proposal(str(proposal["id"])), preview

    def prepare_next_plan_step(
        self,
        plan_id: str,
        identity: AuthenticatedMember,
    ) -> tuple[dict, dict, ResolutionPreview] | None:
        """Preview the next primitive against committed state, without a model call."""

        plan = self.repo.get_kernel_plan(plan_id)
        if plan["status"] != "active":
            return None
        authority = load_scenario_authority_context(
            self.repo,
            str(plan["run_id"]),
            state_policy="require",
        )
        if authority.contract_hash != plan["contract_hash"]:
            self.repo.fail_kernel_plan(
                plan_id, reason="contract changed before the next step"
            )
            return None
        index = int(plan["current_step_index"])
        step = plan["steps"][index]
        root_action = self.repo.get_player_action(str(plan["root_action_id"]))
        selection = SemanticSelection(
            kind="operator",
            candidate_id=str(step["operator_id"]),
            confidence="high",
        )
        try:
            provisional = prepare_selected_kernel_action(
                authority.contract,
                authority.snapshot,
                action_id=f"plan:{plan['id']}:{index}",
                actor_id=str(root_action.get("pc_id") or root_action["member_id"]),
                player_action=str(plan["task_key"]),
                selection=selected_operator_from_semantic(selection),
            )
        except ValueError as exc:
            self.repo.fail_kernel_plan(plan_id, reason=str(exc))
            return None
        if not provisional.preview.allowed:
            self.repo.fail_kernel_plan(plan_id, reason=provisional.preview.reason)
            return None
        action = self.repo.create_kernel_plan_step_action(
            str(plan["id"]), index, title=provisional.operator.title
        )
        prepared = prepare_selected_kernel_action(
            authority.contract,
            authority.snapshot,
            action_id=str(action["id"]),
            actor_id=str(action.get("pc_id") or action["member_id"]),
            player_action=str(action["action_text"]),
            selection=selected_operator_from_semantic(selection),
        )
        preview = prepared.preview
        self.repo.record_kernel_plan_preview(
            str(plan["id"]),
            index,
            action_id=str(action["id"]),
            preview_hash=preview.preview_hash,
        )
        narrative = prepared.deterministic_narrative
        proposal = self._proposal_from_preview(
            action,
            identity,
            preview,
            narrative=narrative,
            source_model="kernel-plan",
        )
        selection = SemanticSelectionResult(
            selection=SemanticSelection(
                kind="operator",
                candidate_id=prepared.operator.operator_id,
                requested_skill_key=preview.selected_skill_key,
                confidence="high",
            ),
            offered_candidates=(SemanticCandidate(
                candidate_id=prepared.operator.operator_id,
                kind="operator",
                title=prepared.operator.title,
                allowed_skill_keys=tuple(
                    item.skill_key for item in prepared.operator.skill_choices
                ),
                available=True,
            ),),
            allowed_skill_keys=tuple(
                item.skill_key for item in prepared.operator.skill_choices
            ),
            attempt_count=0,
        )
        self._attach_selection(
            proposal,
            selection,
            preview=preview,
            run_id=str(plan["run_id"]),
            narrative=narrative,
            authority=authority,
        )
        return action, self.repo.get_turn_proposal(str(proposal["id"])), preview

    def commit(
        self,
        proposal: dict,
        *,
        outcome: str,
        identity: AuthenticatedMember,
    ) -> dict | None:
        payload = self.kernel_payload(proposal)
        if payload is None or payload.get("preview") is None:
            return None
        if (
            payload.get("schema_version") != "kernel-resolution.v3"
            or not isinstance(payload.get("authority_basis"), dict)
        ):
            raise ValueError(
                "Kernel proposal lacks a current authority basis; recompute the ruling"
            )
        preview = ResolutionPreview.model_validate(payload["preview"])
        authority_basis = KernelAuthorityBasis.model_validate(
            payload["authority_basis"]
        )
        batch = ResolutionTransactionService(self.repo).commit(
            proposal=proposal,
            preview=preview,
            authority_basis=authority_basis,
            run_id=str(payload["run_id"]),
            outcome=outcome,
            identity=identity,
        )
        return batch

    @staticmethod
    def kernel_payload(proposal: dict) -> dict | None:
        matches = [
            item.get("payload")
            for item in proposal.get("actions") or ()
            if item.get("action_type") == "kernel_resolution"
        ]
        return dict(matches[-1]) if matches else None

    @staticmethod
    def narrative_for_outcome(proposal: dict, outcome: str) -> str | None:
        payload = KernelActionService.kernel_payload(proposal)
        narrative = payload.get("narrative") if payload else None
        if not isinstance(narrative, dict):
            return None
        try:
            return KernelNarrativeBundle.model_validate(narrative).narration_for(outcome)
        except ValueError:
            return None

    def _proposal_from_preview(
        self,
        action: dict,
        identity: AuthenticatedMember,
        preview: ResolutionPreview,
        *,
        narrative: KernelNarrativeBundle,
        source_model: str,
    ) -> dict:
        if not preview.allowed:
            return self._clarification_proposal(
                action, identity, preview.reason, source_model=source_model
            )
        ordered_choices = sorted(
            preview.skill_choices,
            key=lambda item: item.skill_key != preview.selected_skill_key,
        )
        checks = [
            {
                "skill": item.skill_key,
                "difficulty": item.difficulty,
                "reason": item.reason,
                "pc_id": action.get("pc_id"),
                "hidden": item.hidden,
                "bonus_dice": item.bonus_dice,
                "allow_push": item.allow_push,
                "scope": item.scope,
                "supporting_factors": item.supporting_factors,
                "automatic_information": tuple(
                    dict.fromkeys(
                        (
                            *preview.automatic_information,
                            *item.automatic_information,
                        )
                    )
                ),
                "failure_stakes": item.failure_stakes,
                "pushed_failure_stakes": item.pushed_failure_stakes,
            }
            for item in ordered_choices
        ]
        return self.turns.create_manual_proposal(
            str(action["campaign_id"]),
            identity,
            ManualProposalCommand(
                player_action=str(action["action_text"]),
                player_action_id=str(action["id"]),
                pc_id=action.get("pc_id"),
                public_narration=narrative.preview_narration,
                kp_notes=f"operator={preview.operator_id}; hash={preview.preview_hash}",
                action_ruling={
                    "goal": str(action["action_text"])[:500],
                    "method": preview.operator_id,
                    "target": "当前契约世界",
                    "feasibility": "possible",
                    "resolution": "check" if checks else "automatic",
                    "reason": preview.reason,
                    "maximum_effect": preview.maximum_effect or "仅限契约命令声明的效果。",
                    "alternative": "可修改技能或补充行动后重新提交。",
                },
                proposed_checks=checks,
                source_model=f"kernel:{source_model}",
            ),
        )

    def _clarification_proposal(
        self,
        action: dict,
        identity: AuthenticatedMember,
        prompt: str,
        *,
        source_model: str,
    ) -> dict:
        return self.turns.create_manual_proposal(
            str(action["campaign_id"]),
            identity,
            ManualProposalCommand(
                player_action=str(action["action_text"]),
                player_action_id=str(action["id"]),
                pc_id=action.get("pc_id"),
                public_narration="行动尚未执行；规则内核需要更具体的做法。",
                kp_notes=prompt,
                action_ruling={
                    "goal": str(action["action_text"])[:500],
                    "method": "clarification",
                    "target": "当前场景",
                    "feasibility": "impossible",
                    "resolution": "no_roll",
                    "reason": prompt,
                    "maximum_effect": "不写入任何世界状态。",
                    "alternative": prompt,
                },
                source_model=f"kernel:{source_model}",
            ),
        )

    def _conversation_proposal(
        self,
        action: dict,
        identity: AuthenticatedMember,
        interpretation: TabletopTurnInterpretation,
        response: TabletopConversationResponse,
        *,
        source_model: str,
    ) -> dict:
        labels = {
            "conversation": "桌外交流",
            "information": "当前可感知信息",
            "roleplay": "角色扮演对话",
        }
        route_label = labels.get(interpretation.route, "桌面对话")
        frame = interpretation.frame
        return self.turns.create_manual_proposal(
            str(action["campaign_id"]),
            identity,
            ManualProposalCommand(
                player_action=str(action["action_text"]),
                player_action_id=str(action["id"]),
                pc_id=action.get("pc_id"),
                public_narration=response.public_narration,
                kp_notes=(
                    f"tabletop_route={interpretation.route}; "
                    "non-mechanical response; no world commands"
                ),
                action_ruling={
                    "goal": frame.goal or str(action["action_text"])[:500],
                    "method": route_label,
                    "target": (
                        "、".join(frame.target_entity_ids) or "当前桌面与可见场景"
                    ),
                    "feasibility": "possible",
                    "resolution": "automatic",
                    "reason": f"这句话被识别为{route_label}，不触发规则检定或世界状态变更。",
                    "maximum_effect": "只产生本轮公开回应，不提交规则、资源或世界事实。",
                    "alternative": "如果你希望达成机械效果，请补充目标与角色采用的具体做法。",
                },
                source_model=f"kernel:{source_model}",
            ),
        )

    def _attach_tabletop_turn(
        self,
        proposal: dict,
        interpretation: TabletopTurnInterpretation,
        *,
        response: TabletopConversationResponse | None = None,
    ) -> None:
        self.repo.add_proposal_action(
            str(proposal["id"]),
            "tabletop_turn",
            actor="system",
            note="ruleset-neutral conversation framing before mechanics",
            payload={
                "schema_version": "tabletop-turn.v1",
                "frame": interpretation.frame.model_dump(mode="json"),
                "route": interpretation.route,
                "attempt_count": interpretation.attempt_count,
                "audit_count": interpretation.audit_count,
                "audit_reason": interpretation.audit_reason,
                "validation_errors": interpretation.validation_errors,
                "response": response.model_dump(mode="json") if response else None,
            },
        )

    def _attach_selection(
        self,
        proposal,
        selection,
        *,
        preview,
        run_id: str,
        narrative=None,
        action_status: str = "executable",
        intent_plan: ActionIntentPlan | None = None,
        tabletop_turn: TabletopTurnInterpretation | None = None,
        authority: ScenarioAuthorityContext | None = None,
    ) -> None:
        authority_basis = None
        if preview is not None:
            if authority is None:
                raise ValueError("Kernel preview requires a captured authority basis")
            if (
                authority.run_id != run_id
                or authority.state_version != preview.run_version
            ):
                raise ValueError("Kernel preview authority changed before persistence")
            authority_basis = authority.kernel_authority_basis(preview.preview_hash)
        self.repo.add_proposal_action(
            str(proposal["id"]),
            "kernel_resolution",
            actor="system",
            note="contract-first semantic selection and deterministic preview",
            payload={
                "schema_version": "kernel-resolution.v3",
                "run_id": run_id,
                "authority_basis": (
                    authority_basis.model_dump(mode="json")
                    if authority_basis is not None
                    else None
                ),
                "selection": selection.model_dump(mode="json"),
                "action_status": action_status,
                "intent_plan": (
                    intent_plan.model_dump(mode="json") if intent_plan else None
                ),
                "tabletop_turn": (
                    tabletop_turn.model_dump(mode="json")
                    if tabletop_turn is not None
                    else None
                ),
                "preview": preview.model_dump(mode="json") if preview else None,
                "narrative": narrative.model_dump(mode="json") if narrative else None,
            },
        )


__all__ = ["KernelActionService"]

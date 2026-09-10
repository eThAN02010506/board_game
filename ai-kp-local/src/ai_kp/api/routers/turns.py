from hashlib import sha256
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request

from ai_kp.api.authz import (
    campaign_for_proposal,
    require_approved_pc_binding,
    require_campaign_role,
)
from ai_kp.api.auto_kp import player_auto_kp_job
from ai_kp.api.dependencies import get_app_settings, get_identity, get_repo
from ai_kp.api.llm import create_kp_orchestrator
from ai_kp.api.schemas import (
    ActionAdjudicationConfirm,
    ActionAdjudicationRevise,
    KpTurnRequest,
    ManualKernelSelection,
    PlayerActionCreate,
    ProposalDecision,
    TurnProposalCreate,
)
from ai_kp.api.world_expansion_schemas import WorldExpansionEncounterRequest
from ai_kp.application.action_adjudication_service import ActionAdjudicationService
from ai_kp.application.agent_trace_projection import (
    project_actor_execution_traces,
    with_player_safe_proposal_tabletop,
    with_player_safe_tabletop,
)
from ai_kp.application.auto_kp_queue_service import AutoKpQueueService
from ai_kp.application.auto_turn_service import AutoTurnService
from ai_kp.application.check_consequence_service import (
    CheckConsequenceService,
    GenerateCheckConsequenceCommand,
)
from ai_kp.application.errors import KpSessionEndedError
from ai_kp.application.kernel_action_service import KernelActionService
from ai_kp.application.parallel_action_player_projection import (
    ParallelActionPlayerProjectionService,
)
from ai_kp.application.parallel_action_regather_service import (
    ParallelActionRegatherCoordinator,
)
from ai_kp.application.parallel_action_workflow_service import (
    ParallelActionWorkflowService,
)
from ai_kp.application.session_continuity_service import SessionContinuityService
from ai_kp.application.turn_service import KpTurnCommand, ManualProposalCommand, TurnService
from ai_kp.application.world_expansion_materialization_service import (
    EncounterEntityRealization,
    EncounterFact,
    EncounterMapPlacement,
    EncounterNpc,
    MaterializeWorldExpansionCommand,
    WorldExpansionMaterializationService,
)
from ai_kp.bootstrap.settings import Settings
from ai_kp.director.turn_output import StructuredOutputError
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter()


@router.post("/campaigns/{campaign_id}/actions")
async def submit_player_action(
    campaign_id: str,
    payload: PlayerActionCreate,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    require_campaign_role(identity, campaign_id, ("player",))
    SessionContinuityService(repo).require_actions_allowed(identity)
    campaign = repo.get_campaign(campaign_id)
    if campaign.get("session_zero_required"):
        session_zero = repo.session_zero_projection(campaign_id, identity.member_id)
        if not session_zero["ready"]:
            raise HTTPException(
                status_code=409,
                detail="Complete and confirm the current Session 0 before submitting play actions.",
            )
    require_approved_pc_binding(
        repo,
        campaign_id,
        identity.pc_id,
        owner_profile_id=identity.player_profile_id,
    )
    active_parallel = repo.get_active_parallel_action_batch_for_member(
        campaign_id,
        identity.session_id,
        identity.member_id,
    )
    if active_parallel is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Your simultaneous action is still active; confirm, roll, or wait "
                "for the batch to settle before submitting another action."
            ),
        )
    regather = repo.get_active_parallel_action_regather_for_member(
        campaign_id,
        identity.session_id,
        identity.member_id,
    )
    if regather is not None and (not payload.auto_advance or not payload.background):
        raise HTTPException(
            status_code=409,
            detail=(
                "Your group is durably regrouping after a revision. Submit this "
                "action to Auto KP or wait for the regrouping workflow to finish."
            ),
        )
    action = TurnService(repo).submit_player_action(
        identity,
        action_text=payload.action_text,
        map_id=payload.map_id,
        token_id=payload.token_id,
        client_action_id=payload.client_action_id,
    )
    if regather is not None:
        coordinator = ParallelActionRegatherCoordinator(repo)
        regather = coordinator.register(
            str(regather["id"]),
            str(action["id"]),
            expected_version=int(regather["version"]),
            identity=identity,
        )
        job = AutoKpQueueService(repo).enqueue_parallel_regather(str(regather["id"]))
        if job is not None:
            _wake_auto_kp_worker(request)
            regather = repo.get_parallel_action_regather(str(regather["id"]))
        return {
            "status": "awaiting_group_resubmission",
            "player_action": action,
            "proposal": None,
            "checks": [],
            "adjudication": None,
            "parallel_regather": coordinator.project(regather, identity).as_dict(),
            "job": player_auto_kp_job(job) if job is not None else None,
            "message": (
                "All participants resubmitted; the existing parallel planner is queued."
                if job is not None
                else "Your replacement action is saved; waiting for the other participants."
            ),
        }
    if not payload.auto_advance:
        return action
    if payload.background:
        job = AutoKpQueueService(repo).enqueue_player_action(str(action["id"]))
        worker = getattr(request.app.state, "auto_kp_worker", None)
        if worker is not None:
            worker.wake()
        return {
            "status": "queued",
            "player_action": action,
            "proposal": None,
            "checks": [],
            "job": player_auto_kp_job(job),
            "message": "行动已进入后台 Auto KP 队列；可以安全离开或刷新页面。",
        }
    result = await AutoTurnService(repo).advance_player_action(
        str(action["id"]),
        director=create_kp_orchestrator(repo, settings, request),
        source_model=settings.llm_model,
    )
    result_payload = result.as_dict()
    if result_payload.get("adjudication") is not None:
        result_payload["adjudication"] = with_player_safe_tabletop(
            result_payload["adjudication"]
        )
    if result_payload.get("proposal") is not None:
        result_payload["proposal"] = with_player_safe_proposal_tabletop(
            result_payload["proposal"]
        )
    return result_payload


@router.get("/campaigns/{campaign_id}/actions")
def list_player_actions(
    campaign_id: str,
    status: Literal["submitted", "reviewed", "resolved", "rejected"] | None = None,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.list_player_actions(campaign_id, identity.session_id, status=status)


@router.get("/player-actions/{action_id}")
def get_player_action(
    action_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    action = repo.get_player_action(action_id)
    require_campaign_role(identity, action["campaign_id"])
    if identity.role == "player" and action["member_id"] != identity.member_id:
        raise HTTPException(status_code=403, detail="Players can only inspect their own actions")
    return action


@router.get("/player-actions/{action_id}/kernel-candidates")
def list_manual_kernel_candidates(
    action_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    action = repo.get_player_action(action_id)
    require_campaign_role(identity, str(action["campaign_id"]), ("kp",))
    if str(action["session_id"]) != identity.session_id:
        raise HTTPException(status_code=404, detail="Player action not found")
    if action["status"] != "submitted":
        raise HTTPException(status_code=409, detail="Player action is no longer pending")
    try:
        candidates = KernelActionService(repo).manual_catalog(action)
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "action_id": action_id,
        "action_text": action["action_text"],
        "candidates": candidates,
    }


@router.post("/player-actions/{action_id}/kernel-selection")
def prepare_manual_kernel_selection(
    action_id: str,
    payload: ManualKernelSelection,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    action = repo.get_player_action(action_id)
    require_campaign_role(identity, str(action["campaign_id"]), ("kp",))
    if str(action["session_id"]) != identity.session_id:
        raise HTTPException(status_code=404, detail="Player action not found")
    if action["status"] != "submitted":
        raise HTTPException(status_code=409, detail="Player action is no longer pending")
    try:
        proposal, preview = KernelActionService(repo).prepare_manual(
            action,
            identity,
            operator_id=payload.operator_id,
            requested_skill_key=payload.requested_skill_key,
        )
        adjudication = ActionAdjudicationService(repo).create(
            action,
            proposal,
            source_model="kernel:human-kp",
            enforce_precheck=False,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "proposal": proposal,
        "adjudication": adjudication,
        "preview_hash": preview.preview_hash,
        "message": "人类 KP 已通过规则内核给出裁定；等待玩家确认或改选合理技能。",
    }


@router.get("/player-actions/{action_id}/adjudication")
def get_action_adjudication(
    action_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    action = repo.get_player_action(action_id)
    require_campaign_role(identity, str(action["campaign_id"]))
    if identity.role == "player":
        if action["member_id"] != identity.member_id:
            raise HTTPException(
                status_code=403,
                detail="Players can only inspect their own rulings",
            )
        active_parallel = repo.get_active_parallel_action_batch_for_action(action_id)
        if active_parallel is not None:
            projection = ParallelActionPlayerProjectionService(repo).get(
                str(active_parallel["id"]), identity
            )
            return dict(projection.own_item.adjudication)
        return with_player_safe_tabletop(repo.get_action_adjudication(action_id))
    return repo.get_action_adjudication(action_id)


@router.get("/campaigns/{campaign_id}/action-adjudications/pending")
def list_pending_action_adjudications(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("player",))
    pending = repo.list_member_pending_adjudications(campaign_id, identity.member_id)
    return [
        with_player_safe_tabletop(adjudication)
        for adjudication in pending
        if repo.get_active_parallel_action_batch_for_action(
            str(adjudication["action_id"])
        )
        is None
    ]


@router.post("/player-actions/{action_id}/adjudication/confirm")
def confirm_action_adjudication(
    action_id: str,
    payload: ActionAdjudicationConfirm,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    action = repo.get_player_action(action_id)
    parallel_batch = repo.get_parallel_action_batch_for_action(action_id)
    if parallel_batch is not None:
        if payload.expected_batch_version is None:
            raise HTTPException(
                status_code=409,
                detail="Refresh the simultaneous action before confirming it.",
            )
        selected_skill_key = _parallel_selected_skill_key(
            parallel_batch,
            action_id,
            payload.selected_skill,
        )
        result = ParallelActionWorkflowService(repo).confirm_item(
            str(parallel_batch["id"]),
            action_id,
            expected_batch_version=payload.expected_batch_version,
            expected_adjudication_version=payload.expected_version,
            selected_skill_key=selected_skill_key,
            identity=identity,
        )
        job = AutoKpQueueService(repo).enqueue_parallel_followup(
            str(parallel_batch["id"])
        )
        if job is not None:
            _wake_auto_kp_worker(request)
        projection = ParallelActionPlayerProjectionService(repo).get(
            str(parallel_batch["id"]), identity
        ).as_dict()
        return {
            "status": result.status,
            "player_action": repo.get_player_action(action_id),
            "proposal": None,
            "checks": projection["own_item"]["checks"],
            "adjudication": projection["own_item"]["adjudication"],
            "parallel_batch": projection,
            "job": player_auto_kp_job(job) if job is not None else None,
            "message": projection["public_message"],
        }
    adjudication, proposal, checks, applied = ActionAdjudicationService(repo).confirm(
        action_id,
        expected_version=payload.expected_version,
        selected_skill=payload.selected_skill,
        identity=identity,
    )
    pending = repo.list_member_pending_adjudications(
        str(action["campaign_id"]), identity.member_id
    )
    next_adjudication = next(
        (item for item in pending if item["id"] != adjudication["id"]), None
    )
    return {
        "status": (
            "awaiting_roll" if checks else
            "awaiting_confirmation" if next_adjudication is not None else
            "completed" if applied else "awaiting_confirmation"
        ),
        "player_action": repo.get_player_action(action_id),
        "proposal": with_player_safe_proposal_tabletop(proposal),
        "checks": checks,
        "adjudication": with_player_safe_tabletop(
            next_adjudication or adjudication
        ),
        "message": (
            "裁定已确认，请完成检定。"
            if checks
            else "计划当前步骤已完成；下一步骤等待确认。"
            if next_adjudication is not None
            else "裁定已确认并执行。"
            if applied
            else "你的裁定已确认，等待其他并行动作玩家确认。"
        ),
    }


@router.post("/player-actions/{action_id}/adjudication/revise")
def revise_action_adjudication(
    action_id: str,
    payload: ActionAdjudicationRevise,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    old_action = repo.get_player_action(action_id)
    parallel_batch = repo.get_parallel_action_batch_for_action(action_id)
    if parallel_batch is not None:
        if payload.expected_batch_version is None:
            raise HTTPException(
                status_code=409,
                detail="Refresh the simultaneous action before revising it.",
            )
        adjudication = repo.get_action_adjudication(action_id)
        if int(adjudication["version"]) != payload.expected_version:
            raise ValueError("Action ruling changed; refresh before revising")
        normalized = payload.action_text.strip()
        if not normalized:
            raise ValueError("Revised action text cannot be blank")
        result = ParallelActionWorkflowService(repo).request_revision(
            str(parallel_batch["id"]),
            action_id,
            expected_version=payload.expected_batch_version,
            identity=identity,
            reason="Player revised an action before the confirmation barrier.",
        )
        coordinator = ParallelActionRegatherCoordinator(repo)
        regather = (
            coordinator.start(
                str(parallel_batch["id"]),
                identity=identity,
                reason="A participant revised the simultaneous group plan.",
            )
            if payload.background
            else None
        )
        revision_hash = sha256(normalized.encode("utf-8")).hexdigest()[:16]
        revised = TurnService(repo).submit_player_action(
            identity,
            action_text=normalized,
            map_id=old_action.get("map_id"),
            token_id=old_action.get("token_id"),
            client_action_id=(
                f"parallel-revision:{action_id}:{payload.expected_version}:"
                f"{revision_hash}"
            ),
        )
        job = None
        regather_projection = None
        if regather is not None:
            regather = coordinator.register(
                str(regather["id"]),
                str(revised["id"]),
                expected_version=int(regather["version"]),
                identity=identity,
            )
            job = AutoKpQueueService(repo).enqueue_parallel_regather(
                str(regather["id"])
            )
            regather_projection = coordinator.project(regather, identity).as_dict()
            if job is not None:
                _wake_auto_kp_worker(request)
        return {
            "status": "awaiting_group_resubmission",
            "player_action": revised,
            "proposal": None,
            "checks": [],
            "adjudication": None,
            "parallel_batch": None,
            "parallel_regather": regather_projection,
            "job": player_auto_kp_job(job) if job is not None else None,
            "message": (
                "The old batch was withdrawn safely. Your revised action is "
                "saved in a durable group barrier; other participants may "
                "resubmit whenever they are ready."
                if regather is not None
                else "The old simultaneous batch was withdrawn safely. Other "
                "players must submit fresh actions before manual replanning."
            ),
            "workflow_status": result.status,
        }
    action = ActionAdjudicationService(repo).revise(
        action_id,
        expected_version=payload.expected_version,
        action_text=payload.action_text,
        identity=identity,
    )
    if not payload.background:
        return action
    job = AutoKpQueueService(repo).enqueue_player_action(str(action["id"]))
    worker = getattr(request.app.state, "auto_kp_worker", None)
    if worker is not None:
        worker.wake()
    return {
        "status": "queued",
        "player_action": action,
        "proposal": None,
        "checks": [],
        "adjudication": None,
        "job": player_auto_kp_job(job),
        "message": "修改后的行动已提交 AI KP 重新裁定。",
    }


@router.get("/campaigns/{campaign_id}/proposals")
def list_proposals(
    campaign_id: str,
    status: str | None = None,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.list_turn_proposals(campaign_id, status=status)


@router.get("/campaigns/{campaign_id}/public-turns")
def list_public_turns(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    """Return the shared table narration without exposing KP-only proposal data."""
    require_campaign_role(identity, campaign_id, ("kp", "player", "observer"))
    proposals = repo.list_turn_proposals(campaign_id, status="approved")
    return [
        {
            "id": proposal["id"],
            "player_action_id": proposal.get("player_action_id"),
            "player_action": proposal.get("player_action", ""),
            "public_narration": proposal.get("public_narration", ""),
            "created_at": proposal.get("created_at"),
            "decided_at": proposal.get("decided_at"),
            "actor_traces": project_actor_execution_traces(
                (
                    (proposal.get("tabletop_turn") or {}).get("response") or {}
                ).get("actor_traces")
            ),
        }
        for proposal in proposals[:50]
        if proposal.get("public_narration")
    ]


@router.post("/campaigns/{campaign_id}/proposals")
def create_manual_proposal(
    campaign_id: str,
    payload: TurnProposalCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    if payload.pc_id:
        require_approved_pc_binding(repo, campaign_id, payload.pc_id)
    return TurnService(repo).create_manual_proposal(
        campaign_id,
        identity,
        ManualProposalCommand(
            player_action=payload.player_action,
            public_narration=payload.public_narration,
            player_action_id=payload.player_action_id,
            pc_id=payload.pc_id,
            kp_notes=payload.kp_notes,
            action_ruling=payload.action_ruling,
            proposed_events=payload.proposed_events,
            proposed_memories=payload.proposed_memories,
            proposed_checks=payload.proposed_checks,
            proposed_npc_updates=payload.proposed_npc_updates,
            proposed_map_moves=payload.proposed_map_moves,
            proposed_facts=payload.proposed_facts,
            source_model=payload.source_model,
        ),
    )


@router.get("/kp/proposals/{proposal_id}")
def get_proposal(
    proposal_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_for_proposal(repo, proposal_id), ("kp",))
    return repo.get_turn_proposal(proposal_id)


@router.get("/kp/proposals/{proposal_id}/context")
def get_proposal_context(
    proposal_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict | None:
    require_campaign_role(identity, campaign_for_proposal(repo, proposal_id), ("kp",))
    return repo.get_context_assembly(proposal_id)


@router.post("/kp/proposals/{proposal_id}/approve")
def approve_proposal(
    proposal_id: str,
    payload: ProposalDecision,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    campaign_id = campaign_for_proposal(repo, proposal_id)
    require_campaign_role(identity, campaign_id, ("kp",))
    return TurnService(repo).approve(
        proposal_id,
        campaign_id,
        identity,
        note=payload.note,
        override_public_narration=payload.override_public_narration,
    )


@router.post("/kp/proposals/{proposal_id}/reject")
def reject_proposal(
    proposal_id: str,
    payload: ProposalDecision,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    campaign_id = campaign_for_proposal(repo, proposal_id)
    require_campaign_role(identity, campaign_id, ("kp",))
    return TurnService(repo).reject(
        proposal_id,
        campaign_id,
        identity,
        note=payload.note,
    )


@router.post("/kp/proposals/{proposal_id}/world-expansion-encounters")
def materialize_world_expansion_encounter(
    proposal_id: str,
    payload: WorldExpansionEncounterRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    campaign_id = campaign_for_proposal(repo, proposal_id)
    require_campaign_role(identity, campaign_id, ("kp",))
    npc = (
        EncounterNpc(**payload.npc.model_dump())
        if payload.npc is not None
        else None
    )
    map_placement = (
        EncounterMapPlacement(**payload.map_placement.model_dump())
        if payload.map_placement is not None
        else None
    )
    return WorldExpansionMaterializationService(repo).materialize(
        proposal_id,
        identity,
        MaterializeWorldExpansionCommand(
            idempotency_key=payload.idempotency_key,
            summary=payload.summary,
            happened_at=payload.happened_at,
            facts=tuple(EncounterFact(**item.model_dump()) for item in payload.facts),
            entities=tuple(
                EncounterEntityRealization(**item.model_dump())
                for item in payload.entities
            ),
            npc=npc,
            map_placement=map_placement,
            participant_investigator_ids=tuple(
                payload.participant_investigator_ids
            ),
            interaction_summary=payload.interaction_summary,
            profession_context=payload.profession_context,
        ),
    )


@router.post("/kp/turn")
async def kp_turn(
    payload: KpTurnRequest,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    require_campaign_role(identity, payload.campaign_id, ("kp",))
    if payload.pc_id:
        require_approved_pc_binding(repo, payload.campaign_id, payload.pc_id)
    try:
        return await TurnService(repo).create_ai_proposal(
            KpTurnCommand(
                campaign_id=payload.campaign_id,
                player_action=payload.player_action,
                player_action_id=payload.player_action_id,
                pc_id=payload.pc_id,
                location=payload.location,
                map_id=payload.map_id,
                profession_hint=payload.profession_hint,
                active_spoiler_tags=tuple(payload.active_spoiler_tags),
            ),
            identity,
            create_kp_orchestrator(repo, settings, request),
            source_model=settings.llm_model,
        )
    except StructuredOutputError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Local model returned invalid structured output: {exc}",
        ) from exc
    except KpSessionEndedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/checks/{check_id}/consequence-proposal")
async def create_check_consequence_proposal(
    check_id: str,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    check = repo.get_skill_check(check_id)
    require_campaign_role(identity, str(check["campaign_id"]), ("kp",))
    action_id = check.get("player_action_id")
    if (
        action_id
        and repo.get_parallel_action_batch_for_action(str(action_id)) is not None
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Parallel checks are finalized by their deterministic batch; "
                "a per-action consequence proposal is not allowed."
            ),
        )
    try:
        return await CheckConsequenceService(repo).generate(
            GenerateCheckConsequenceCommand(check_id=check_id),
            identity,
            create_kp_orchestrator(repo, settings, request),
            source_model=settings.llm_model,
        )
    except StructuredOutputError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Local model returned invalid consequence output: {exc}",
        ) from exc
    except KpSessionEndedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _parallel_selected_skill_key(
    batch: dict,
    action_id: str,
    requested: str | None,
) -> str | None:
    item = next(
        (candidate for candidate in batch["items"] if candidate["action_id"] == action_id),
        None,
    )
    if item is None:
        raise KeyError(f"Parallel batch action not found: {action_id}")
    adjudication = item.get("adjudication") or {}
    if adjudication.get("mode") == "direct_resolution":
        return requested
    if requested is None:
        value = item.get("selected_skill_key")
        return str(value) if value else None
    matches = [
        option
        for option in adjudication.get("skill_options") or ()
        if requested in {option.get("skill_key"), option.get("skill_name")}
    ]
    if len(matches) != 1 or not matches[0].get("skill_key"):
        raise ValueError("Selected skill is not an allowed parallel action choice")
    return str(matches[0]["skill_key"])


def _wake_auto_kp_worker(request: Request) -> None:
    worker = getattr(request.app.state, "auto_kp_worker", None)
    if worker is not None:
        worker.wake()

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request

from ai_kp.api.authz import (
    campaign_for_proposal,
    require_approved_pc_binding,
    require_campaign_role,
)
from ai_kp.api.dependencies import get_app_settings, get_identity, get_repo
from ai_kp.api.llm import create_kp_orchestrator
from ai_kp.api.schemas import (
    KpTurnRequest,
    PlayerActionCreate,
    ProposalDecision,
    TurnProposalCreate,
)
from ai_kp.api.world_expansion_schemas import WorldExpansionEncounterRequest
from ai_kp.application.check_consequence_service import (
    CheckConsequenceService,
    GenerateCheckConsequenceCommand,
)
from ai_kp.application.errors import KpSessionEndedError
from ai_kp.application.turn_service import KpTurnCommand, ManualProposalCommand, TurnService
from ai_kp.application.world_expansion_materialization_service import (
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
def submit_player_action(
    campaign_id: str,
    payload: PlayerActionCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("player",))
    require_approved_pc_binding(
        repo,
        campaign_id,
        identity.pc_id,
        owner_profile_id=identity.player_profile_id,
    )
    return TurnService(repo).submit_player_action(
        identity,
        action_text=payload.action_text,
        map_id=payload.map_id,
        token_id=payload.token_id,
        client_action_id=payload.client_action_id,
    )


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


@router.get("/campaigns/{campaign_id}/proposals")
def list_proposals(
    campaign_id: str,
    status: str | None = None,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.list_turn_proposals(campaign_id, status=status)


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

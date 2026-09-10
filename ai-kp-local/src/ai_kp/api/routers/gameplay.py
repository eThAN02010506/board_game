"""Persistent CoC7 encounter, health, sanity, chase, and growth endpoints."""

from fastapi import APIRouter, Depends, Request

from ai_kp.api.authz import require_campaign_role
from ai_kp.api.dependencies import get_app_settings, get_identity, get_repo
from ai_kp.api.llm import create_kp_orchestrator
from ai_kp.api.schemas import (
    Coc7EncounterCreate,
    Coc7GameplayCommand,
    EncounterActionAgentProposalInput,
    EncounterActionCancelInput,
    EncounterActionConfirmInput,
    EncounterActionPreviewInput,
)
from ai_kp.application.auto_kp_queue_service import AutoKpQueueService
from ai_kp.application.encounter_action_service import (
    EncounterActionPreviewCommand,
    EncounterActionService,
)
from ai_kp.application.errors import UpstreamServiceError
from ai_kp.application.gameplay_service import (
    EncounterCreateCommand,
    GameplayCommand,
    GameplayService,
)
from ai_kp.bootstrap.settings import Settings
from ai_kp.director.errors import CampaignAiCallCancelledError
from ai_kp.director.turn_output import StructuredOutputError
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter(tags=["coc7-gameplay"])


def _command(payload: Coc7GameplayCommand) -> GameplayCommand:
    return GameplayCommand(
        command_id=payload.command_id,
        expected_version=payload.expected_version,
        command_type=payload.command_type,
        payload=payload.payload,
        visibility=payload.visibility,
    )


@router.post("/campaigns/{campaign_id}/coc7/encounters")
def create_encounter(
    campaign_id: str,
    payload: Coc7EncounterCreate,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    created = GameplayService(repo).create_encounter(
        campaign_id,
        identity,
        EncounterCreateCommand(
            kind=payload.kind,
            title=payload.title,
            participants=tuple(
                item.model_dump(exclude_none=True) for item in payload.participants
            ),
            locations=tuple(item.model_dump() for item in payload.locations),
        ),
    )
    job = AutoKpQueueService(repo).enqueue_encounter_turn(str(created["id"]))
    if job is not None:
        request.app.state.auto_kp_worker.wake()
    return created


@router.get("/campaigns/{campaign_id}/coc7/encounters")
def list_encounters(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id)
    return GameplayService(repo).list_encounters(campaign_id, identity)


@router.get("/coc7/encounters/{encounter_id}")
def get_encounter(
    encounter_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return GameplayService(repo).get_encounter(encounter_id, identity)


@router.get("/coc7/encounters/{encounter_id}/events")
def list_encounter_events(
    encounter_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    return GameplayService(repo).list_encounter_events(encounter_id, identity)


@router.post("/coc7/encounters/{encounter_id}/commands")
def command_encounter(
    encounter_id: str,
    payload: Coc7GameplayCommand,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    result = GameplayService(repo).command_encounter(
        encounter_id, identity, _command(payload)
    )
    job = AutoKpQueueService(repo).enqueue_encounter_turn(encounter_id)
    if job is not None:
        request.app.state.auto_kp_worker.wake()
    return result


@router.get("/coc7/encounters/{encounter_id}/action-options")
def get_encounter_action_options(
    encounter_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return EncounterActionService(repo).options(encounter_id, identity)


@router.post("/coc7/encounters/{encounter_id}/action-previews")
def create_encounter_action_preview(
    encounter_id: str,
    payload: EncounterActionPreviewInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return EncounterActionService(repo).create_preview(
        encounter_id,
        identity,
        EncounterActionPreviewCommand(**payload.model_dump()),
    )


@router.post("/encounter-action-requests/{request_id}/confirm")
def confirm_encounter_action(
    request_id: str,
    payload: EncounterActionConfirmInput,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    result = EncounterActionService(repo).confirm(
        request_id, identity, expected_version=payload.expected_version
    )
    job = AutoKpQueueService(repo).enqueue_encounter_turn(
        str(result["encounter_id"])
    )
    if job is not None:
        request.app.state.auto_kp_worker.wake()
    return result


@router.post("/encounter-action-requests/{request_id}/agent-proposal")
async def propose_improvised_encounter_action(
    request_id: str,
    payload: EncounterActionAgentProposalInput,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    service = EncounterActionService(repo)
    try:
        return await service.propose_improvised(
            request_id,
            identity,
            expected_version=payload.expected_version,
            director=create_kp_orchestrator(repo, settings, request),
            source_model=settings.llm_model,
        )
    except CampaignAiCallCancelledError:
        raise
    except (RuntimeError, StructuredOutputError, UpstreamServiceError):
        return service.record_agent_clarification(
            request_id,
            identity,
            expected_version=payload.expected_version,
            public_message=(
                "遭遇意图 Agent 暂时无法形成安全方案。请说明具体目标、使用的手段，"
                "以及成功时希望获得的有限效果；也可以改选已列出的规则动作。"
            ),
            source_model=settings.llm_model,
        )


@router.post("/encounter-action-requests/{request_id}/cancel")
def cancel_encounter_action(
    request_id: str,
    payload: EncounterActionCancelInput,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return EncounterActionService(repo).cancel(
        request_id, identity, expected_version=payload.expected_version
    )


@router.get(
    "/campaigns/{campaign_id}/investigators/{investigator_id}/coc7/state"
)
def get_character_state(
    campaign_id: str,
    investigator_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id)
    return GameplayService(repo).get_character_state(
        campaign_id, investigator_id, identity
    )


@router.post(
    "/campaigns/{campaign_id}/investigators/{investigator_id}/coc7/commands"
)
def command_character(
    campaign_id: str,
    investigator_id: str,
    payload: Coc7GameplayCommand,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return GameplayService(repo).command_character(
        campaign_id, investigator_id, identity, _command(payload)
    )


__all__ = ["router"]

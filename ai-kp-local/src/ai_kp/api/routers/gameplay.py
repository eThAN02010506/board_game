"""Persistent CoC7 encounter, health, sanity, chase, and growth endpoints."""

from fastapi import APIRouter, Depends

from ai_kp.api.authz import require_campaign_role
from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.schemas import Coc7EncounterCreate, Coc7GameplayCommand
from ai_kp.application.gameplay_service import (
    EncounterCreateCommand,
    GameplayCommand,
    GameplayService,
)
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
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return GameplayService(repo).create_encounter(
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
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    return GameplayService(repo).command_encounter(
        encounter_id, identity, _command(payload)
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

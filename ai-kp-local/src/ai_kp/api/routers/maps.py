from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from ai_kp.application.map_service import (
    GenerateMapCommand,
    MapService,
    MoveTokenCommand,
    PlaceTokenCommand,
)
from ai_kp.api.authz import campaign_for_map, campaign_for_token, require_campaign_role
from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.schemas import MapGenerateRequest, MapTokenCreate, MapTokenMove
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember


router = APIRouter()


@router.get("/campaigns/{campaign_id}/maps")
def list_maps(
    campaign_id: str,
    view: Literal["player", "kp"] = "kp",
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id)
    if identity.role == "player" and view != "player":
        raise HTTPException(status_code=403, detail="Players can only use player map view")
    return repo.list_maps(
        campaign_id,
        include_prompt=identity.role == "kp" and view == "kp",
        published_only=identity.role == "player",
    )


@router.post("/campaigns/{campaign_id}/maps/generate")
def generate_and_save_map(
    campaign_id: str,
    payload: MapGenerateRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return MapService(repo).generate_and_save(
        campaign_id,
        identity.session_id,
        GenerateMapCommand(
            title=payload.title,
            prompt=payload.prompt,
            locations=tuple(payload.locations),
            routes=tuple(payload.routes),
            style=payload.style,
            width=payload.width,
            height=payload.height,
        ),
    )


@router.get("/maps/{map_id}")
def get_map(
    map_id: str,
    view: Literal["player", "kp"] = "kp",
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    campaign_id = campaign_for_map(repo, map_id)
    require_campaign_role(identity, campaign_id)
    if identity.role == "player" and view != "player":
        raise HTTPException(status_code=403, detail="Players can only use player map view")
    if identity.role == "player" and not repo.is_map_published(map_id):
        raise HTTPException(status_code=404, detail="Map not found")
    allowed_visibility = (
        ("player", "table")
        if identity.role == "player" or view == "player"
        else ("player", "table", "kp")
    )
    return repo.get_map(map_id, allowed_visibility=allowed_visibility)


@router.post("/maps/{map_id}/publish")
def publish_map(
    map_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    campaign_id = campaign_for_map(repo, map_id)
    require_campaign_role(identity, campaign_id, ("kp",))
    return MapService(repo).publish(map_id, campaign_id, identity.session_id)


@router.post("/maps/{map_id}/unpublish")
def unpublish_map(
    map_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    campaign_id = campaign_for_map(repo, map_id)
    require_campaign_role(identity, campaign_id, ("kp",))
    return MapService(repo).unpublish(map_id, campaign_id, identity.session_id)


@router.post("/maps/{map_id}/tokens")
def place_map_token(
    map_id: str,
    payload: MapTokenCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    campaign_id = campaign_for_map(repo, map_id)
    require_campaign_role(identity, campaign_id, ("kp",))
    return MapService(repo).place_token(
        map_id,
        campaign_id,
        identity.session_id,
        PlaceTokenCommand(
            label=payload.label,
            location_name=payload.location_name,
            actor_type=payload.actor_type,
            actor_id=payload.actor_id,
            visibility=payload.visibility,
            color=payload.color,
        ),
    )


@router.post("/map-tokens/{token_id}/move")
def move_map_token(
    token_id: str,
    payload: MapTokenMove,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    campaign_id = campaign_for_token(repo, token_id)
    require_campaign_role(identity, campaign_id)
    token = repo.get_map_token(token_id)
    if identity.role == "player":
        if not repo.is_map_published(token["map_id"]):
            raise HTTPException(status_code=404, detail="Map token not found")
        if not identity.pc_id or token["actor_type"] != "pc" or token["actor_id"] != identity.pc_id:
            raise HTTPException(status_code=403, detail="Players can only move their own PC token")
    return MapService(repo).move_token(
        token_id,
        campaign_id,
        identity.session_id,
        MoveTokenCommand(
            to_location_name=payload.to_location_name,
            moved_by=f"{identity.role}:{identity.member_id}",
            note=payload.note,
            require_route=True if identity.role == "player" else payload.require_route,
            allowed_visibility=("player", "table") if identity.role == "player" else None,
            expected_version=payload.expected_version,
        ),
    )


@router.get("/map-tokens/{token_id}/moves")
def list_map_token_moves(
    token_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_for_token(repo, token_id))
    token = repo.get_map_token(token_id)
    if identity.role == "player":
        if not repo.is_map_published(token["map_id"]):
            raise HTTPException(status_code=404, detail="Map token not found")
        if not identity.pc_id or token["actor_type"] != "pc" or token["actor_id"] != identity.pc_id:
            raise HTTPException(status_code=403, detail="Players can only inspect their own token")
    moves = repo.list_map_token_moves(
        token_id,
        allowed_visibility=("player", "table") if identity.role == "player" else None,
    )
    if identity.role == "player":
        return [
            {
                "id": move["id"],
                "from_location_name": move["from_location_name"],
                "to_location_name": move["to_location_name"],
                "created_at": move["created_at"],
            }
            for move in moves
        ]
    return moves

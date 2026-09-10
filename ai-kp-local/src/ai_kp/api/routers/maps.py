from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from ai_kp.api.authz import (
    campaign_for_map,
    campaign_for_token,
    require_approved_pc_binding,
    require_campaign_role,
)
from ai_kp.api.dependencies import get_app_settings, get_identity, get_repo
from ai_kp.api.schemas import (
    MapFogCreate,
    MapFogReveal,
    MapFogUpdate,
    MapGenerateRequest,
    MapImageGenerateRequest,
    MapPublishRequest,
    MapRevisionCreate,
    MapRoutePlanCreate,
    MapRoutePlanStatusUpdate,
    MapTokenCreate,
    MapTokenMove,
)
from ai_kp.application.errors import KpSessionEndedError
from ai_kp.application.map_image_service import MapImageService
from ai_kp.application.map_route_plan_service import (
    CreateRoutePlanCommand,
    MapRoutePlanService,
    TokenRoute,
)
from ai_kp.application.map_service import (
    GenerateMapCommand,
    MapService,
    MoveTokenCommand,
    PlaceTokenCommand,
)
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.images.openai_compatible import OpenAICompatibleImageProvider
from ai_kp.infrastructure.images.storage import MapAssetFileStore
from ai_kp.infrastructure.llm.model_configuration import normalize_openai_base_url
from ai_kp.platform.scenes.map_spec import build_image_prompt
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter()


@router.post("/maps/{map_id}/revisions")
def create_map_revision(
    map_id: str,
    payload: MapRevisionCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    campaign_id = campaign_for_map(repo, map_id)
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.create_map_revision_from_spec(
        map_id,
        expected_revision_id=payload.expected_revision_id,
        map_spec=payload.map_spec,
        member_id=identity.member_id,
    )


@router.post("/maps/{map_id}/fog-regions")
def create_map_fog_region(
    map_id: str,
    payload: MapFogCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    campaign_id = campaign_for_map(repo, map_id)
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.create_map_fog_region(
        map_id,
        label=payload.label,
        polygon=payload.polygon,
        member_id=identity.member_id,
    )


@router.patch("/map-fog-regions/{fog_id}")
def update_map_fog_region(
    fog_id: str,
    payload: MapFogUpdate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    fog = repo.get_map_fog_region(fog_id)
    campaign_id = campaign_for_map(repo, str(fog["map_id"]))
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.update_map_fog_region(
        fog_id,
        expected_version=payload.expected_version,
        label=payload.label,
        polygon=payload.polygon,
        member_id=identity.member_id,
    )


@router.delete("/map-fog-regions/{fog_id}")
def delete_map_fog_region(
    fog_id: str,
    expected_version: int,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    fog = repo.get_map_fog_region(fog_id)
    campaign_id = campaign_for_map(repo, str(fog["map_id"]))
    require_campaign_role(identity, campaign_id, ("kp",))
    repo.delete_map_fog_region(fog_id, expected_version=expected_version)
    return {"deleted": True, "id": fog_id}


@router.post("/map-fog-regions/{fog_id}/reveal")
def reveal_map_fog_region(
    fog_id: str,
    payload: MapFogReveal,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    fog = repo.get_map_fog_region(fog_id)
    campaign_id = campaign_for_map(repo, str(fog["map_id"]))
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.reveal_map_fog_region(
        fog_id, expected_version=payload.expected_version
    )


@router.get("/maps/{map_id}/route-plans")
def list_map_route_plans(
    map_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    campaign_id = campaign_for_map(repo, map_id)
    require_campaign_role(identity, campaign_id)
    if identity.role == "player" and not repo.is_map_published(map_id):
        raise HTTPException(status_code=404, detail="Map not found")
    member_token_id = None
    if identity.role == "player" and identity.pc_id:
        token = repo.find_map_token_for_actor(map_id, "pc", identity.pc_id)
        member_token_id = str(token["id"]) if token else None
    return repo.list_map_route_plans(
        campaign_id,
        map_id=map_id,
        member_token_id=member_token_id,
        include_kp=identity.role == "kp",
    )


@router.post("/maps/{map_id}/route-plans")
def create_map_route_plan(
    map_id: str,
    payload: MapRoutePlanCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    campaign_id = campaign_for_map(repo, map_id)
    require_campaign_role(identity, campaign_id)
    if identity.role == "player":
        if not repo.is_map_published(map_id):
            raise HTTPException(status_code=404, detail="Map not found")
        require_approved_pc_binding(
            repo,
            campaign_id,
            identity.pc_id,
            owner_profile_id=identity.player_profile_id,
        )
        own_token = (
            repo.find_map_token_for_actor(map_id, "pc", identity.pc_id)
            if identity.pc_id
            else None
        )
        if own_token is None or any(
            route.token_id != own_token["id"] for route in payload.token_routes
        ):
            raise HTTPException(
                status_code=403,
                detail="Players can only plan a route for their own token",
            )
    created = MapRoutePlanService(repo).create(
        campaign_id=campaign_id,
        map_id=map_id,
        member_id=identity.member_id,
        command=CreateRoutePlanCommand(
            title=payload.title,
            note=payload.note,
            token_routes=tuple(
                TokenRoute(
                    token_id=route.token_id,
                    waypoints=tuple(route.waypoints),
                )
                for route in payload.token_routes
            ),
            player_submission=identity.role == "player",
        ),
    )
    # ai_kp 自动化模式下自动批准玩家提交的移动计划。
    run = repo.get_active_campaign_module_run(campaign_id)
    if (
        identity.role == "player"
        and run is not None
        and str(run.get("automation_level") or "conservative") == "ai_kp"
        and created.get("status") == "proposed"
    ):
        try:
            created = repo.update_map_route_plan_status(
                str(created["id"]),
                expected_version=int(created.get("version", 0)),
                status="approved",
            )
        except (KeyError, ValueError):
            pass
    return created


@router.patch("/map-route-plans/{plan_id}")
def update_map_route_plan(
    plan_id: str,
    payload: MapRoutePlanStatusUpdate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    plan = repo.get_map_route_plan(plan_id)
    require_campaign_role(identity, str(plan["campaign_id"]), ("kp",))
    return repo.update_map_route_plan_status(
        plan_id,
        expected_version=payload.expected_version,
        status=payload.status,
    )


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
            map_kind=payload.map_kind,
            era_year=payload.era_year,
            locale=payload.locale,
            season=payload.season,
            time_of_day=payload.time_of_day,
            weather=payload.weather,
            public_architecture=tuple(payload.public_architecture),
            features=tuple(payload.features),
            required_elements=tuple(payload.required_elements),
            forbidden_elements=tuple(payload.forbidden_elements),
            visual_style=payload.visual_style,
        ),
    )


@router.get("/maps/{map_id}")
def get_map(
    map_id: str,
    view: Literal["player", "kp"] = "kp",
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
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
    known_location_ids = None
    if identity.role == "player":
        states, _current_location_id = _player_map_awareness(
            repo,
            map_id=map_id,
            campaign_id=campaign_id,
            identity=identity,
        )
        known_location_ids = frozenset(
            str(item["location_id"])
            for item in states
            if item["state"] in {"current", "seen"}
        )
        public_location_ids = frozenset(
            repo.list_map_location_ids(
                map_id,
                allowed_visibility=("player", "table"),
            )
        )
        if public_location_ids.issubset(known_location_ids):
            known_location_ids = None
    result = repo.get_map(
        map_id,
        allowed_visibility=allowed_visibility,
        known_location_ids=known_location_ids,
    )
    if identity.role == "kp":
        provider_configured = bool(settings.image_base_url and settings.image_model)
        revision_ready = bool(result.get("revision_id"))
        result["image_generation"] = {
            "available": provider_configured and revision_ready,
            "model": settings.image_model,
            "safety": "player_safe_projection",
            "unavailable_reason": (
                None
                if provider_configured and revision_ready
                else (
                    "legacy_map_requires_revision"
                    if provider_configured
                    else "provider_not_configured"
                )
            ),
        }
    return result


def _asset_response(asset: dict, *, cache_hit: bool | None = None) -> dict:
    result = {
        key: value
        for key, value in asset.items()
        if key not in {"storage_path"}
    }
    if cache_hit is not None:
        result["cache_hit"] = cache_hit
    return result


def _image_provider(request: Request, settings: Settings):
    overridden = getattr(request.app.state, "map_image_provider", None)
    if overridden is not None:
        return overridden
    if not settings.image_base_url or not settings.image_model:
        raise ValueError(
            "尚未配置图片模型；请设置 AI_KP_IMAGE_BASE_URL 与 AI_KP_IMAGE_MODEL"
        )
    return OpenAICompatibleImageProvider(
        normalize_openai_base_url(settings.image_base_url),
        settings.image_api_key,
        settings.image_model,
        timeout_seconds=settings.image_timeout_seconds,
        client=getattr(request.app.state, "http_client", None),
    )


@router.get("/maps/{map_id}/image-prompt")
def preview_map_image_prompt(
    map_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    campaign_id = campaign_for_map(repo, map_id)
    require_campaign_role(identity, campaign_id, ("kp",))
    revision = repo.get_current_map_revision(map_id)
    if revision is None:
        raise ValueError("旧地图尚未建立 MapSpec revision")
    return {
        "map_id": map_id,
        "revision_id": revision["id"],
        "prompt": build_image_prompt(revision["spec"], "table"),
        "provider_configured": bool(settings.image_base_url and settings.image_model),
        "model": settings.image_model,
        "projection": "player_safe",
    }


@router.post("/maps/{map_id}/image-assets/generate")
async def generate_map_image_asset(
    map_id: str,
    payload: MapImageGenerateRequest,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    campaign_id = campaign_for_map(repo, map_id)
    require_campaign_role(identity, campaign_id, ("kp",))
    provider = _image_provider(request, settings)
    try:
        asset, cache_hit = await MapImageService(
            repo,
            provider,
            MapAssetFileStore(settings.map_asset_root),
        ).generate_public_background(
            map_id,
            width=payload.width,
            height=payload.height,
            seed=payload.seed,
            member_id=identity.member_id,
            session_id=identity.session_id,
        )
    except KpSessionEndedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    repo.append_realtime_event(
        session_id=identity.session_id,
        campaign_id=campaign_id,
        audience="kp",
        event_type="map.asset_ready",
        resource_type="map_asset",
        resource_id=asset["id"],
        payload={"map_id": map_id, "cache_hit": cache_hit},
    )
    return _asset_response(asset, cache_hit=cache_hit)


@router.post("/maps/{map_id}/assets/{asset_id}/select")
def select_map_image_asset(
    map_id: str,
    asset_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    campaign_id = campaign_for_map(repo, map_id)
    require_campaign_role(identity, campaign_id, ("kp",))
    asset = repo.select_public_map_asset(map_id, asset_id)
    audience = "session" if repo.is_map_published(map_id) else "kp"
    repo.append_realtime_event(
        session_id=identity.session_id,
        campaign_id=campaign_id,
        audience=audience,
        event_type="map.changed",
        resource_type="map",
        resource_id=map_id,
        payload={"map_id": map_id, "background_changed": True},
    )
    return _asset_response(asset)


@router.get("/map-assets/{asset_id}/content")
def get_map_asset_content(
    asset_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> FileResponse:
    asset = repo.get_map_asset(asset_id)
    campaign_id = campaign_for_map(repo, asset["map_id"])
    require_campaign_role(identity, campaign_id)
    if identity.role == "player" and not repo.is_map_asset_player_visible(asset_id):
        raise HTTPException(status_code=404, detail="Map asset not found")
    if identity.role == "player":
        states, _current_location_id = _player_map_awareness(
            repo,
            map_id=str(asset["map_id"]),
            campaign_id=campaign_id,
            identity=identity,
        )
        known_location_ids = {
            str(item["location_id"])
            for item in states
            if item["state"] in {"current", "seen"}
        }
        public_location_ids = set(
            repo.list_map_location_ids(
                str(asset["map_id"]),
                allowed_visibility=("player", "table"),
            )
        )
        if not public_location_ids.issubset(known_location_ids):
            raise HTTPException(status_code=404, detail="Map asset not found")
    path = MapAssetFileStore(settings.map_asset_root).resolve(str(asset["storage_path"]))
    return FileResponse(
        path,
        media_type=asset["mime_type"],
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "ETag": f'"{asset["content_hash"]}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/maps/{map_id}/publish")
def publish_map(
    map_id: str,
    payload: MapPublishRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    campaign_id = campaign_for_map(repo, map_id)
    require_campaign_role(identity, campaign_id, ("kp",))
    return MapService(repo).publish(
        map_id,
        campaign_id,
        identity.session_id,
        expected_revision_id=payload.expected_revision_id,
        expected_selected_asset_id=payload.expected_selected_asset_id,
    )


@router.post("/maps/{map_id}/publish-diff")
def publish_diff(
    map_id: str,
    payload: MapPublishRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    campaign_id = campaign_for_map(repo, map_id)
    require_campaign_role(identity, campaign_id, ("kp",))
    return MapService(repo).publish_diff(
        map_id,
        campaign_id,
        expected_revision_id=payload.expected_revision_id,
        expected_selected_asset_id=payload.expected_selected_asset_id,
    )


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
    if payload.actor_type == "pc":
        require_approved_pc_binding(repo, campaign_id, payload.actor_id)
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
        require_approved_pc_binding(
            repo,
            campaign_id,
            identity.pc_id,
            owner_profile_id=identity.player_profile_id,
        )
        if not repo.is_map_published(token["map_id"]):
            raise HTTPException(status_code=404, detail="Map token not found")
        if not identity.pc_id or token["actor_type"] != "pc" or token["actor_id"] != identity.pc_id:
            raise HTTPException(status_code=403, detail="Players can only move their own PC token")
    moved = MapService(repo).move_token(
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
    # 玩家移动后刷新该玩家的位置感知（当前位置 + 相邻位置标记为已见）。
    if identity.role == "player" and identity.player_profile_id:
        map_id = moved.get("map_id") or token["map_id"]
        dest_location_id = repo.find_map_location_id(map_id, payload.to_location_name)
        repo.refresh_player_location_awareness(
            map_id,
            campaign_id,
            identity.player_profile_id,
            dest_location_id,
        )
    return moved


@router.get("/map-tokens/{token_id}/moves")
def list_map_token_moves(
    token_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    campaign_id = campaign_for_token(repo, token_id)
    require_campaign_role(identity, campaign_id)
    token = repo.get_map_token(token_id)
    if identity.role == "player":
        require_approved_pc_binding(
            repo,
            campaign_id,
            identity.pc_id,
            owner_profile_id=identity.player_profile_id,
        )
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


@router.get("/maps/{map_id}/awareness")
def get_map_awareness(
    map_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    """Return the current player's location-awareness states for a map.

    States: current (where the player is), seen (visited), unknown (unexplored),
    destroyed (revealed to no longer exist). Players see only their own view.
    """
    campaign_id = campaign_for_map(repo, map_id)
    require_campaign_role(identity, campaign_id)
    if identity.role == "player":
        require_approved_pc_binding(
            repo,
            campaign_id,
            identity.pc_id,
            owner_profile_id=identity.player_profile_id,
        )
        if not repo.is_map_published(map_id):
            raise HTTPException(status_code=404, detail="Map not found")
    states, current_location_id = _player_map_awareness(
        repo,
        map_id=map_id,
        campaign_id=campaign_id,
        identity=identity,
    )
    return {
        # Unknown location ids are omitted rather than sent to the browser.
        "states": [item for item in states if item["state"] != "unknown"],
        "current_location_id": current_location_id,
    }


def _player_map_awareness(
    repo: Repository,
    *,
    map_id: str,
    campaign_id: str,
    identity: AuthenticatedMember,
) -> tuple[list[dict], str | None]:
    player_profile_id = identity.player_profile_id
    if player_profile_id is None:
        return [], None
    current_location_id = None
    if identity.role == "player" and identity.pc_id:
        token = repo.find_map_token_for_actor(map_id, "pc", identity.pc_id)
        if token is not None:
            current_location_id = token.get("location_id")
    states = repo.get_map_location_awareness(
        map_id,
        campaign_id,
        player_profile_id,
    )
    if current_location_id is not None and not any(
        item["state"] == "current"
        and item["location_id"] == current_location_id
        for item in states
    ):
        states = repo.refresh_player_location_awareness(
            map_id,
            campaign_id,
            player_profile_id,
            current_location_id,
        )
    return states, current_location_id

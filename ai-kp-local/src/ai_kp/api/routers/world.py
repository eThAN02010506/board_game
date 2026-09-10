from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from ai_kp.api.authz import (
    require_approved_pc_binding,
    require_campaign_role,
    require_local_admin,
)
from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.schemas import (
    CampaignNpcLink,
    CampaignNpcReappearancePolicyUpdate,
    EventCreate,
    HiddenAppearanceRequest,
    MemoryCreate,
    ModuleImport,
    NpcAvailabilityProfileUpdate,
    NpcCreate,
    PcCreate,
    TravelLocationCreate,
    TravelRouteCreate,
    TravelRoutePreviewRequest,
    WorldEntityStateUpdateRequest,
)
from ai_kp.application.npc_reappearance_service import NpcReappearanceService
from ai_kp.application.private_random_resolution_service import (
    HiddenAppearanceCommand,
    HiddenAppearanceDestination,
    PrivateRandomResolutionService,
)
from ai_kp.application.travel_graph_service import (
    TravelGraphService,
    TravelLocationCommand,
    TravelRouteCommand,
)
from ai_kp.application.world_entity_state_service import (
    SetWorldEntityStateCommand,
    WorldEntityStateService,
)
from ai_kp.application.world_service import (
    AddMemoryCommand,
    AppendEventCommand,
    CreateNpcCommand,
    ImportModuleCommand,
    LinkNpcCommand,
    WorldService,
)
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter()


def _public_pc_summary(pc: dict) -> dict:
    sheet = pc.get("sheet") if isinstance(pc.get("sheet"), dict) else {}
    declared = sheet.get("public_summary") if isinstance(sheet.get("public_summary"), dict) else {}
    return {
        key: declared[key]
        for key in ("occupation", "cash", "attributes", "status")
        if key in declared
    }


@router.post("/campaigns/{campaign_id}/pcs", deprecated=True)
def create_pc(
    campaign_id: str,
    payload: PcCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    raise HTTPException(
        status_code=410,
        detail=(
            "Direct PC creation was removed. A player must create or import an "
            "investigator and the KP must approve its submitted revision."
        ),
    )


@router.get("/campaigns/{campaign_id}/pcs")
def list_pcs(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id)
    pcs = WorldService(repo).list_pcs(campaign_id)
    if identity.role == "kp":
        return pcs
    return [
        pc
        if pc["id"] == identity.pc_id
        else {
            "id": pc["id"],
            "campaign_id": pc["campaign_id"],
            "name": pc["name"],
            "public_summary": _public_pc_summary(pc),
        }
        for pc in pcs
    ]


@router.get("/campaigns/{campaign_id}/world-entities")
def list_world_entities(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id)
    return WorldService(repo).list_world_entities(
        campaign_id,
        view="kp" if identity.role == "kp" else "player",
    )


@router.post("/campaigns/{campaign_id}/world-entities/{entity_id}/states")
def update_world_entity_state(
    campaign_id: str,
    entity_id: str,
    payload: WorldEntityStateUpdateRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return WorldEntityStateService(repo).set_state(
        campaign_id,
        entity_id,
        identity,
        SetWorldEntityStateCommand(
            **payload.model_dump(),
            source_kind="human_kp",
        ),
    )


@router.post("/campaigns/{campaign_id}/events")
def append_event(
    campaign_id: str,
    payload: EventCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return WorldService(repo).append_event(
        campaign_id,
        AppendEventCommand(**payload.model_dump()),
    )


@router.post("/campaigns/{campaign_id}/memories")
def add_memory(
    campaign_id: str,
    payload: MemoryCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return WorldService(repo).add_memory(
        campaign_id,
        AddMemoryCommand(**payload.model_dump()),
    )


@router.get("/campaigns/{campaign_id}/modules")
def list_modules(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return WorldService(repo).list_modules(campaign_id)


@router.post("/campaigns/{campaign_id}/modules")
def import_module(
    campaign_id: str,
    payload: ModuleImport,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return WorldService(repo).import_module(
        campaign_id,
        ImportModuleCommand(**payload.model_dump()),
    )


@router.get("/modules/{module_id}/chunks")
def list_module_chunks(
    module_id: str,
    view: Literal["player", "kp"] = "kp",
    spoiler: str | None = None,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    service = WorldService(repo)
    module = service.get_module(module_id)
    module_campaign_id = module["campaign_id"]
    if module_campaign_id is None:
        if identity.role != "kp":
            raise HTTPException(status_code=403, detail="KP access required")
    else:
        required_roles = ("kp",) if view == "kp" else ("kp", "player")
        require_campaign_role(identity, module_campaign_id, required_roles)
    return service.list_module_chunks(
        module_id,
        view=view,
        spoiler=spoiler,
    )


@router.get("/campaigns/{campaign_id}/memory/search")
def search_memory(
    campaign_id: str,
    q: str,
    pc_id: str | None = None,
    view: Literal["player", "kp"] = "kp",
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id)
    if identity.role == "player":
        if view != "player":
            raise HTTPException(status_code=403, detail="Players can only use player memory view")
        if not identity.pc_id:
            raise HTTPException(
                status_code=403,
                detail="A PC must be assigned before searching character memory",
            )
        if pc_id and pc_id != identity.pc_id:
            raise HTTPException(
                status_code=403, detail="Players can only search their own PC memory"
            )
        pc_id = identity.pc_id
        require_approved_pc_binding(
            repo,
            campaign_id,
            pc_id,
            owner_profile_id=identity.player_profile_id,
        )
    return WorldService(repo).search_memory(
        campaign_id,
        q,
        pc_id=pc_id,
        view=view,
    )


@router.post("/npcs")
def create_npc(
    payload: NpcCreate,
    _admin: None = Depends(require_local_admin),
    repo: Repository = Depends(get_repo),
) -> dict:
    return WorldService(repo).create_npc(CreateNpcCommand(**payload.model_dump()))


@router.post("/campaigns/{campaign_id}/npcs")
def create_campaign_npc(
    campaign_id: str,
    payload: NpcCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return WorldService(repo).create_campaign_npc(
        campaign_id,
        CreateNpcCommand(**payload.model_dump()),
    )


@router.post("/campaigns/{campaign_id}/npcs/{npc_id}")
def link_npc_to_campaign(
    campaign_id: str,
    npc_id: str,
    payload: CampaignNpcLink,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return WorldService(repo).link_npc(
        campaign_id,
        npc_id,
        LinkNpcCommand(
            **payload.model_dump(exclude={"participant_investigator_ids"}),
            participant_investigator_ids=tuple(
                item.strip() for item in payload.participant_investigator_ids
            ),
        ),
    )


@router.get("/campaigns/{campaign_id}/npc-candidates")
def npc_candidates(
    campaign_id: str,
    action: str,
    location: str | None = None,
    profession_hint: str | None = None,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return WorldService(repo).npc_candidates(
        campaign_id,
        action,
        location=location,
        profession_hint=profession_hint,
    )


@router.get("/campaigns/{campaign_id}/npc-reappearance-candidates")
def npc_reappearance_candidates(
    campaign_id: str,
    query: str | None = Query(default=None, max_length=200),
    context_location: str | None = Query(default=None, max_length=300),
    profession_hint: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return NpcReappearanceService(repo).list_candidates(
        campaign_id,
        query=query,
        context_location=context_location,
        profession_hint=profession_hint,
        limit=limit,
    )


@router.get("/campaigns/{campaign_id}/npcs")
def list_campaign_npcs(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return NpcReappearanceService(repo).list_campaign_npcs(campaign_id)


@router.put("/campaigns/{campaign_id}/npcs/{npc_id}/availability")
def update_npc_availability(
    campaign_id: str,
    npc_id: str,
    payload: NpcAvailabilityProfileUpdate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return NpcReappearanceService(repo).save_availability_profile(
        campaign_id,
        npc_id,
        payload.model_dump(),
    )


@router.put("/campaigns/{campaign_id}/npc-reappearance-policy")
def update_npc_reappearance_policy(
    campaign_id: str,
    payload: CampaignNpcReappearancePolicyUpdate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return NpcReappearanceService(repo).save_campaign_policy(
        campaign_id,
        payload.model_dump(),
    )


@router.get("/campaigns/{campaign_id}/npc-reappearance-policy")
def get_npc_reappearance_policy(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return NpcReappearanceService(repo).get_campaign_policy(campaign_id)


@router.get("/campaigns/{campaign_id}/npc-hidden-appearances")
def list_npc_hidden_appearances(
    campaign_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return PrivateRandomResolutionService(repo).list_hidden_appearances(
        campaign_id, limit
    )


@router.post("/campaigns/{campaign_id}/npc-hidden-appearances")
def resolve_npc_hidden_appearance(
    campaign_id: str,
    payload: HiddenAppearanceRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return PrivateRandomResolutionService(repo).resolve_hidden_appearance(
        campaign_id,
        HiddenAppearanceCommand(
            idempotency_key=payload.idempotency_key,
            npc_id=payload.npc_id,
            trigger_text=payload.trigger_text,
            appearance_chance=payload.appearance_chance,
            destinations=tuple(
                HiddenAppearanceDestination(item.location_name, item.weight)
                for item in payload.destinations
            ),
            profession_hint=payload.profession_hint,
        ),
        created_by_member_id=identity.member_id,
    )


@router.get("/campaigns/{campaign_id}/travel-graph")
def get_travel_graph(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return TravelGraphService(repo).list_graph(campaign_id)


@router.post("/campaigns/{campaign_id}/travel-locations")
def create_travel_location(
    campaign_id: str,
    payload: TravelLocationCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return TravelGraphService(repo).create_location(
        campaign_id,
        TravelLocationCommand(
            name=payload.name,
            aliases=tuple(payload.aliases),
            source_kind=payload.source_kind,
            source_ref=payload.source_ref,
            kp_notes=payload.kp_notes,
        ),
    )


@router.delete("/campaigns/{campaign_id}/travel-locations/{location_id}")
def delete_travel_location(
    campaign_id: str,
    location_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    TravelGraphService(repo).delete_location(campaign_id, location_id)
    return {"ok": True}


@router.post("/campaigns/{campaign_id}/travel-routes")
def create_travel_route(
    campaign_id: str,
    payload: TravelRouteCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return TravelGraphService(repo).create_route(
        campaign_id,
        TravelRouteCommand(**payload.model_dump()),
    )


@router.delete("/campaigns/{campaign_id}/travel-routes/{route_id}")
def delete_travel_route(
    campaign_id: str,
    route_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    TravelGraphService(repo).delete_route(campaign_id, route_id)
    return {"ok": True}


@router.post("/campaigns/{campaign_id}/travel-route-preview")
def preview_travel_route(
    campaign_id: str,
    payload: TravelRoutePreviewRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return TravelGraphService(repo).preview(
        campaign_id,
        origins=tuple(payload.origins),
        destination=payload.destination,
        max_minutes=payload.max_minutes,
    )

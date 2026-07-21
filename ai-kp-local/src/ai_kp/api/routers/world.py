from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from ai_kp.api.authz import require_campaign_role, require_local_admin
from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.schemas import (
    CampaignNpcLink,
    EventCreate,
    MemoryCreate,
    ModuleImport,
    NpcCreate,
    PcCreate,
)
from ai_kp.application.world_service import (
    AddMemoryCommand,
    AppendEventCommand,
    CreateNpcCommand,
    CreatePcCommand,
    ImportModuleCommand,
    LinkNpcCommand,
    WorldService,
)
from ai_kp.core.repository import Repository
from ai_kp.security.repository import AuthenticatedMember


router = APIRouter()


def _public_pc_summary(pc: dict) -> dict:
    sheet = pc.get("sheet") if isinstance(pc.get("sheet"), dict) else {}
    declared = sheet.get("public_summary") if isinstance(sheet.get("public_summary"), dict) else {}
    return {
        key: declared[key]
        for key in ("occupation", "cash", "attributes", "status")
        if key in declared
    }


@router.post("/campaigns/{campaign_id}/pcs")
def create_pc(
    campaign_id: str,
    payload: PcCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return WorldService(repo).create_pc(
        campaign_id,
        identity.session_id,
        CreatePcCommand(**payload.model_dump()),
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
        LinkNpcCommand(**payload.model_dump()),
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

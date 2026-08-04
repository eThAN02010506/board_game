"""KP-only module entity graph authoring and deterministic diagnostics."""

from fastapi import APIRouter, Depends

from ai_kp.api.authz import require_campaign_role
from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.schemas import (
    ModuleImportConfirm,
    ModuleReachabilityCheck,
)
from ai_kp.application.module_campaign_import_service import (
    ImportLocationChoice,
    ImportNpcChoice,
    ImportRouteChoice,
    ModuleCampaignImportService,
    ModuleImportConfirmCommand,
)
from ai_kp.application.module_graph_service import ModuleGraphService
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.modules.graph import ModuleEntityCreate, ModuleRelationCreate
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter(tags=["module graph"])


def _require_module_kp(
    repo: Repository,
    identity: AuthenticatedMember,
    module_id: str,
) -> None:
    module = repo.get_module(module_id)
    require_campaign_role(identity, module["campaign_id"], ("kp",))


@router.get("/modules/{module_id}/entities")
def list_module_entities(
    module_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    _require_module_kp(repo, identity, module_id)
    return repo.list_module_entities(module_id)


@router.post("/modules/{module_id}/entities")
def create_module_entity(
    module_id: str,
    payload: ModuleEntityCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_module_kp(repo, identity, module_id)
    return ModuleGraphService(repo).create_entity(
        module_id,
        payload,
        member_id=identity.member_id,
    )


@router.get("/modules/{module_id}/relations")
def list_module_relations(
    module_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    _require_module_kp(repo, identity, module_id)
    return repo.list_module_relations(module_id)


@router.post("/modules/{module_id}/relations")
def create_module_relation(
    module_id: str,
    payload: ModuleRelationCreate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_module_kp(repo, identity, module_id)
    return ModuleGraphService(repo).create_relation(
        module_id,
        payload,
        member_id=identity.member_id,
    )


@router.post("/modules/{module_id}/graph/reachability")
def check_module_reachability(
    module_id: str,
    payload: ModuleReachabilityCheck,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_module_kp(repo, identity, module_id)
    return ModuleGraphService(repo).check_reachability(
        module_id,
        tuple(dict.fromkeys(payload.entry_entity_ids)),
    )


@router.get("/campaigns/{campaign_id}/modules/{module_id}/import-preview")
def preview_module_campaign_import(
    campaign_id: str,
    module_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return ModuleCampaignImportService(repo).preview(campaign_id, module_id)


@router.post("/campaigns/{campaign_id}/modules/{module_id}/import")
def confirm_module_campaign_import(
    campaign_id: str,
    module_id: str,
    payload: ModuleImportConfirm,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return ModuleCampaignImportService(repo).confirm(
        campaign_id,
        module_id,
        ModuleImportConfirmCommand(
            locations=tuple(
                ImportLocationChoice(
                    module_entity_id=choice.module_entity_id,
                    include=choice.include,
                    aliases=tuple(choice.aliases),
                )
                for choice in payload.locations
            ),
            routes=tuple(
                ImportRouteChoice(
                    module_relation_id=choice.module_relation_id,
                    include=choice.include,
                    travel_minutes=choice.travel_minutes,
                    travel_mode=choice.travel_mode,
                    bidirectional=choice.bidirectional,
                )
                for choice in payload.routes
            ),
            npcs=tuple(
                ImportNpcChoice(
                    module_entity_id=choice.module_entity_id,
                    include=choice.include,
                    born_year=choice.born_year,
                    died_year=choice.died_year,
                    active_from_year=choice.active_from_year,
                    active_until_year=choice.active_until_year,
                )
                for choice in payload.npcs
            ),
            apply_time_constraints=payload.apply_time_constraints,
        ),
        member_id=identity.member_id,
    )

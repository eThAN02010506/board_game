"""KP-only module entity graph authoring and deterministic diagnostics."""

from fastapi import APIRouter, Depends, Query

from ai_kp.api.authz import require_campaign_role
from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.schemas import (
    ModuleImportConfirm,
    ModuleReachabilityCheck,
    RunSettingSelectionRequest,
    SettingProfileCreateRequest,
    SettingProfileUpdateRequest,
)
from ai_kp.application.module_campaign_import_service import (
    ImportLocationChoice,
    ImportNpcChoice,
    ImportRouteChoice,
    ModuleCampaignImportService,
    ModuleImportConfirmCommand,
)
from ai_kp.application.module_graph_service import ModuleGraphService
from ai_kp.application.module_setting_analysis_service import (
    CreateSettingProfileCommand,
    ModuleSettingAnalysisService,
    SelectRunSettingCommand,
    UpdateSettingProfileCommand,
)
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


def _require_setting_profile_kp(
    repo: Repository,
    identity: AuthenticatedMember,
    profile_id: str,
) -> dict:
    profile = repo.get_module_setting_profile(profile_id)
    _require_module_kp(repo, identity, str(profile["module_id"]))
    return profile


def _require_run_kp(
    repo: Repository,
    identity: AuthenticatedMember,
    run_id: str,
) -> dict:
    run = repo.get_campaign_module_run(run_id)
    require_campaign_role(identity, str(run["campaign_id"]), ("kp",))
    return run


@router.get("/modules/{module_id}/entities")
def list_module_entities(
    module_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    _require_module_kp(repo, identity, module_id)
    return repo.list_module_entities(module_id)


@router.get("/modules/{module_id}/setting-profiles")
def list_module_setting_profiles(
    module_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    _require_module_kp(repo, identity, module_id)
    return repo.list_module_setting_profiles(module_id)


@router.post("/modules/{module_id}/setting-profiles")
def create_module_setting_profile(
    module_id: str,
    payload: SettingProfileCreateRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_module_kp(repo, identity, module_id)
    return ModuleSettingAnalysisService(repo).create_profile(
        CreateSettingProfileCommand(
            module_id=module_id,
            title=payload.title,
            setting_pack_id=payload.setting_pack_id,
            regions=tuple(payload.regions),
        ),
        member_id=identity.member_id,
    )


@router.get("/setting-profiles/{profile_id}")
def get_module_setting_profile(
    profile_id: str,
    version: int | None = Query(default=None, ge=1),
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    current = _require_setting_profile_kp(repo, identity, profile_id)
    return (
        current
        if version is None
        else repo.get_module_setting_profile(profile_id, version=version)
    )


@router.put("/setting-profiles/{profile_id}")
def update_module_setting_profile(
    profile_id: str,
    payload: SettingProfileUpdateRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_setting_profile_kp(repo, identity, profile_id)
    return ModuleSettingAnalysisService(repo).update_profile(
        UpdateSettingProfileCommand(
            profile_id=profile_id,
            expected_version=payload.expected_version,
            title=payload.title,
            document=payload.document,
        ),
        member_id=identity.member_id,
    )


@router.get("/setting-profiles/{profile_id}/settlements/{settlement_id}/analysis")
def analyze_profile_settlement(
    profile_id: str,
    settlement_id: str,
    version: int | None = Query(default=None, ge=1),
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_setting_profile_kp(repo, identity, profile_id)
    return ModuleSettingAnalysisService(repo).analyze_profile_settlement(
        profile_id=profile_id,
        profile_version=version,
        settlement_id=settlement_id,
    )


@router.get("/module-runs/{run_id}/setting-selection")
def get_run_setting_selection(
    run_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict | None:
    _require_run_kp(repo, identity, run_id)
    return repo.get_module_run_setting_selection(run_id)


@router.put("/module-runs/{run_id}/setting-selection")
def set_run_setting_selection(
    run_id: str,
    payload: RunSettingSelectionRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_run_kp(repo, identity, run_id)
    return ModuleSettingAnalysisService(repo).select_for_run(
        SelectRunSettingCommand(
            run_id=run_id,
            expected_run_version=payload.expected_run_version,
            profile_id=payload.profile_id,
            profile_version=payload.profile_version,
            settlement_id=payload.settlement_id,
            reason=payload.reason,
        ),
        member_id=identity.member_id,
    )


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

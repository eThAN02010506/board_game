"""KP-only selection and progression of the module currently being played."""

from fastapi import APIRouter, Depends, Query

from ai_kp.api.authz import require_campaign_role
from ai_kp.api.dependencies import get_identity, get_repo
from ai_kp.api.schemas import (
    DirectorAnalysisRequest,
    ModuleRunEntityStateUpdate,
    ModuleRunStart,
    ModuleRunUpdate,
    ModuleSceneTransition,
)
from ai_kp.application.module_run_service import (
    EntityStateCommand,
    ModuleRunService,
    SceneTransitionCommand,
    StartModuleRunCommand,
)
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter(tags=["module runs"])


def _require_run_kp(
    run_id: str,
    identity: AuthenticatedMember,
    repo: Repository,
) -> dict:
    run = repo.get_campaign_module_run(run_id)
    require_campaign_role(identity, run["campaign_id"], ("kp",))
    return run


@router.get("/campaigns/{campaign_id}/module-runs")
def list_campaign_module_runs(
    campaign_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.list_campaign_module_runs(
        campaign_id,
        limit=limit,
        offset=offset,
    )


@router.get("/campaigns/{campaign_id}/module-runs/current")
def get_active_campaign_module_run(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict | None:
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.get_active_campaign_module_run(campaign_id)


@router.post("/campaigns/{campaign_id}/module-runs")
def start_campaign_module_run(
    campaign_id: str,
    payload: ModuleRunStart,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    return ModuleRunService(repo).start(
        campaign_id,
        StartModuleRunCommand(
            module_id=payload.module_id,
            current_scene_key=payload.current_scene_key,
            active_spoiler_tags=payload.active_spoiler_tags,
            state=payload.state,
        ),
        member_id=identity.member_id,
    )


@router.patch("/module-runs/{run_id}")
def update_campaign_module_run(
    run_id: str,
    payload: ModuleRunUpdate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_run_kp(run_id, identity, repo)
    return ModuleRunService(repo).update(
        run_id,
        payload.model_dump(exclude_unset=True),
    )


@router.get("/module-runs/{run_id}/director-state")
def get_module_run_director_state(
    run_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    run = _require_run_kp(run_id, identity, repo)
    return {
        "run": run,
        "entity_states": repo.list_module_run_entity_states(run_id),
        "scene_events": repo.list_module_run_scene_events(run_id),
        "entity_state_events": repo.list_module_run_entity_state_events(run_id),
    }


@router.post("/module-runs/{run_id}/scene-transitions")
def transition_module_run_scene(
    run_id: str,
    payload: ModuleSceneTransition,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_run_kp(run_id, identity, repo)
    return ModuleRunService(repo).transition_scene(
        run_id,
        SceneTransitionCommand(
            expected_version=payload.expected_version,
            scene_key=payload.scene_key,
            scene_title=payload.scene_title,
            play_pace=payload.play_pace,
            location_entity_id=payload.location_entity_id,
            world_time=payload.world_time,
            note=payload.note,
        ),
        member_id=identity.member_id,
    )


@router.patch("/module-runs/{run_id}/entities/{entity_id}/state")
def update_module_run_entity_state(
    run_id: str,
    entity_id: str,
    payload: ModuleRunEntityStateUpdate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_run_kp(run_id, identity, repo)
    return ModuleRunService(repo).set_entity_state(
        run_id,
        entity_id,
        EntityStateCommand(
            expected_version=payload.expected_version,
            status=payload.status,
            note=payload.note,
        ),
        member_id=identity.member_id,
    )


@router.post("/module-runs/{run_id}/director/analyze")
def analyze_module_run_intent(
    run_id: str,
    payload: DirectorAnalysisRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_run_kp(run_id, identity, repo)
    return ModuleRunService(repo).analyze(run_id, payload.player_intent)

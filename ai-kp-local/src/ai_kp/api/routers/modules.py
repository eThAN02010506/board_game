"""KP-only PDF/DOC/DOCX module import and private asset routes."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import FileResponse

from ai_kp.api.authz import require_campaign_role
from ai_kp.api.dependencies import get_app_settings, get_identity, get_repo
from ai_kp.api.schemas import (
    ModuleAssetAnalyzeRequest,
    ModuleKnowledgeReview,
    ModuleSectionScopeUpdate,
    ScenarioContractBindRequest,
    ScenarioContractCompileRequest,
    ScenarioContractGenerateRequest,
    ScenarioContractPublishRequest,
)
from ai_kp.api.uploads import read_limited_body, safe_upload_filename
from ai_kp.application.module_entity_materialization_service import (
    ModuleEntityMaterializationService,
)
from ai_kp.application.module_knowledge_service import ModuleKnowledgeService
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.llm.model_execution import (
    ModelExecutionSnapshot,
    ModelExecutionSuperseded,
)
from ai_kp.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from ai_kp.infrastructure.modules import ModuleDocumentStorage
from ai_kp.infrastructure.modules.analysis import (
    TESSERACT_PROMPT_VERSION,
    VISION_PROMPT_VERSION,
    OpenAICompatibleVisionAnalyzer,
    TesseractAnalyzer,
)
from ai_kp.infrastructure.modules.import_worker import (
    enqueue_module_import,
    queue_module_import_retry,
)
from ai_kp.platform.modules.documents import MAX_MODULE_BYTES
from ai_kp.platform.modules.knowledge import ModuleKnowledgeCandidate
from ai_kp.platform.resolution.scenario_authoring import (
    ConstrainedScenarioContractAuthoringAdapter,
)
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter(tags=["module documents"])


def _require_module_kp(
    repo: Repository,
    identity: AuthenticatedMember,
    module_id: str,
) -> dict:
    module = repo.get_module(module_id)
    require_campaign_role(identity, module["campaign_id"], ("kp",))
    return module


@router.get("/modules/{module_id}/scenario-contracts")
def list_scenario_contracts(
    module_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    _require_module_kp(repo, identity, module_id)
    return repo.list_module_scenario_contract_versions(module_id)


@router.get("/modules/{module_id}/scenario-source-scopes")
def list_scenario_source_scopes(
    module_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    _require_module_kp(repo, identity, module_id)
    return [
        scope.as_dict()
        for scope in ScenarioContractService(repo).source_scopes(module_id)
    ]


@router.post(
    "/modules/{module_id}/scenario-contracts/generate",
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_scenario_contract(
    module_id: str,
    payload: ScenarioContractGenerateRequest,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    module = _require_module_kp(repo, identity, module_id)
    service = ScenarioContractService(repo)
    prepared = service.prepare_generation(
        module_id,
        source_scope_key=payload.source_scope_key,
        ruleset_id=payload.ruleset_id,
    )
    run = prepared.run
    automation_level = (
        str(run.get("automation_level") or "conservative")
        if run is not None and str(run.get("module_id")) == module_id
        else "conservative"
    )
    partitions = ConstrainedScenarioContractAuthoringAdapter.partitions(
        prepared.evidence
    )
    job = repo.resume_rejected_scenario_contract_review(
        module_id, prepared.source_fingerprint
    ) or repo.create_scenario_contract_job(
        campaign_id=str(module["campaign_id"]),
        module_id=module_id,
        run_id=(str(run["id"]) if run is not None and str(run["module_id"]) == module_id else None),
        ruleset_id=payload.ruleset_id,
        automation_level=automation_level,
        created_by_member_id=identity.member_id,
        source_fingerprint=prepared.source_fingerprint,
        contract_key=f"module-{prepared.source_fingerprint[:24]}",
        title=prepared.source_scope.title,
        evidence_partitions=partitions,
        corpus_total_block_count=prepared.corpus_total_block_count,
        corpus_truncated=prepared.corpus_truncated,
    )
    request.app.state.scenario_contract_worker.wake()
    return job


@router.get("/modules/{module_id}/scenario-contract-jobs")
def list_scenario_contract_jobs(
    module_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    _require_module_kp(repo, identity, module_id)
    return repo.list_scenario_contract_jobs(module_id)


@router.get("/scenario-contract-jobs/{job_id}")
def get_scenario_contract_job(
    job_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    job = repo.get_scenario_contract_job(job_id)
    _require_module_kp(repo, identity, str(job["module_id"]))
    return job


@router.post("/scenario-contract-jobs/{job_id}/retry")
def retry_scenario_contract_job(
    job_id: str,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    job = repo.get_scenario_contract_job(job_id)
    _require_module_kp(repo, identity, str(job["module_id"]))
    retried = repo.retry_scenario_contract_job(job_id)
    request.app.state.scenario_contract_worker.wake()
    return retried


@router.post("/modules/{module_id}/scenario-contracts/compile")
def compile_scenario_contract(
    module_id: str,
    payload: ScenarioContractCompileRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_module_kp(repo, identity, module_id)
    result, saved = ScenarioContractService(repo).compile_draft(
        module_id,
        payload.contract,
        created_by_member_id=identity.member_id,
    )
    return {
        "compilation": result.model_dump(mode="json"),
        "version": saved,
    }


@router.post("/scenario-contracts/{version_id}/publish")
def publish_scenario_contract(
    version_id: str,
    payload: ScenarioContractPublishRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    version = repo.get_scenario_contract_version(version_id)
    _require_module_kp(repo, identity, str(version["module_id"]))
    return ScenarioContractService(repo).publish(
        version_id,
        expected_row_version=payload.expected_row_version,
        published_by_member_id=identity.member_id,
    )


@router.post("/module-runs/{run_id}/scenario-contract-binding")
def bind_scenario_contract(
    run_id: str,
    payload: ScenarioContractBindRequest,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    run = repo.get_campaign_module_run(run_id)
    require_campaign_role(identity, str(run["campaign_id"]), ("kp",))
    return ScenarioContractService(repo).bind_run(
        run_id, payload.contract_version_id
    )


@router.get("/module-runs/{run_id}/scenario-contract-binding")
def get_scenario_contract_binding(
    run_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict | None:
    run = repo.get_campaign_module_run(run_id)
    require_campaign_role(identity, str(run["campaign_id"]), ("kp",))
    try:
        return repo.get_module_run_contract_binding(run_id)
    except KeyError:
        return None


@router.post(
    "/campaigns/{campaign_id}/module-imports",
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_module_document(
    campaign_id: str,
    request: Request,
    title: str = "",
    x_file_name: str | None = Header(default=None),
    identity: AuthenticatedMember = Depends(get_identity),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    filename = safe_upload_filename(x_file_name, default="module.pdf")
    data = await read_limited_body(
        request,
        max_bytes=MAX_MODULE_BYTES,
        label="KP 本",
    )
    job = await asyncio.to_thread(
        enqueue_module_import,
        settings.db_path,
        settings.module_asset_root,
        campaign_id=campaign_id,
        title=title,
        source_filename=filename,
        data=data,
        synchronous=settings.sqlite_synchronous,
    )
    request.app.state.module_import_worker.wake()
    return job


@router.get("/campaigns/{campaign_id}/module-imports")
def list_module_imports(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    require_campaign_role(identity, campaign_id, ("kp",))
    return repo.list_module_import_jobs(campaign_id)


@router.get("/module-imports/{job_id}")
def get_module_import(
    job_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    job = repo.get_module_import_job(job_id)
    require_campaign_role(identity, job["campaign_id"], ("kp",))
    return job


@router.post(
    "/module-imports/{job_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_module_import(
    job_id: str,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    job = repo.get_module_import_job(job_id)
    require_campaign_role(identity, job["campaign_id"], ("kp",))
    queued = queue_module_import_retry(
        settings.db_path,
        job_id,
        synchronous=settings.sqlite_synchronous,
    )
    request.app.state.module_import_worker.wake()
    return queued


@router.get("/modules/{module_id}/assets")
def list_module_assets(
    module_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    module = repo.get_module(module_id)
    require_campaign_role(identity, module["campaign_id"], ("kp",))
    return repo.list_module_assets(module_id)


@router.get("/module-assets/{asset_id}/content")
def get_module_asset_content(
    asset_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> FileResponse:
    asset = repo.get_module_asset(asset_id)
    require_campaign_role(identity, asset["campaign_id"], ("kp",))
    path = ModuleDocumentStorage(settings.module_asset_root).resolve(asset["storage_path"])
    return FileResponse(
        path,
        media_type=asset["mime_type"],
        filename=asset["download_name"],
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": (
                "inline; filename*=UTF-8''" + quote(asset["download_name"])
            ),
        },
    )


@router.get("/module-analysis/capabilities")
def module_analysis_capabilities(
    campaign_id: str,
    identity: AuthenticatedMember = Depends(get_identity),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    require_campaign_role(identity, campaign_id, ("kp",))
    capability = TesseractAnalyzer(settings.tesseract_command).capabilities()
    return {
        "tesseract": {
            "available": capability.available,
            "version": capability.version,
            "languages": list(capability.languages),
        },
        "vision": {
            "configured": bool(settings.llm_base_url and settings.llm_model),
            "model": settings.llm_model,
        },
    }


@router.patch("/modules/{module_id}/sections")
def update_module_section_scope(
    module_id: str,
    payload: ModuleSectionScopeUpdate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_module_kp(repo, identity, module_id)
    return repo.update_module_section_scope(
        module_id,
        title=payload.title,
        visibility=payload.visibility,
        spoiler_tag=(payload.spoiler_tag or "").strip() or None,
    )


@router.post("/module-assets/{asset_id}/analyze")
async def analyze_module_asset(
    asset_id: str,
    payload: ModuleAssetAnalyzeRequest,
    request: Request,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    asset = repo.get_module_asset(asset_id)
    require_campaign_role(identity, asset["campaign_id"], ("kp",))
    path = ModuleDocumentStorage(settings.module_asset_root).resolve(asset["storage_path"])
    data = path.read_bytes()
    if payload.mode == "tesseract":
        analyzer = TesseractAnalyzer(
            settings.tesseract_command,
            language=payload.language,
            timeout_seconds=settings.tesseract_timeout_seconds,
        )
        model = f"tesseract:{payload.language}"
        prompt_version = TESSERACT_PROMPT_VERSION
    else:
        analyzer = OpenAICompatibleVisionAnalyzer(
            settings.llm_base_url,
            settings.llm_api_key,
            settings.llm_model,
            client=getattr(request.app.state, "http_client", None),
        )
        model = settings.llm_model
        prompt_version = VISION_PROMPT_VERSION
    try:
        analysis = await analyzer.analyze(
            data,
            mime_type=asset["mime_type"],
            source_locator=asset["source_locator"],
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return repo.update_module_asset_analysis(
            asset_id,
            status="failed",
            model=model,
            prompt_version=prompt_version,
            error_text=str(exc),
        )
    return repo.update_module_asset_analysis(
        asset_id,
        status="completed",
        model=analysis.model,
        prompt_version=analysis.prompt_version,
        ocr_text=analysis.ocr_text,
        visual_summary=analysis.visual_summary,
        error_text=None,
    )


@router.get("/modules/{module_id}/search")
def search_module(
    module_id: str,
    q: str = Query(min_length=1, max_length=500),
    spoiler_tag: list[str] | None = None,
    limit: int = Query(default=12, ge=1, le=50),
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    module = repo.get_module(module_id)
    require_campaign_role(identity, module["campaign_id"])
    is_kp = identity.role == "kp"
    return repo.search_module(
        module_id,
        q,
        allowed_visibility=("player", "table", "kp", "secret")
        if is_kp
        else ("player", "table"),
        spoiler_tags=(
            None
            if is_kp and spoiler_tag is None
            else tuple(spoiler_tag or ())
            if is_kp
            else ()
        ),
        limit=limit,
    )


@router.post("/modules/{module_id}/knowledge/candidates")
def create_module_knowledge_candidate(
    module_id: str,
    payload: ModuleKnowledgeCandidate,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    _require_module_kp(repo, identity, module_id)
    return ModuleKnowledgeService(repo).create_manual_candidate(
        module_id,
        payload.model_dump(mode="json"),
    )


@router.post("/modules/{module_id}/knowledge/extract")
async def extract_module_knowledge(
    module_id: str,
    request: Request,
    limit: int = Query(default=5, ge=1, le=25),
    retry_failed: bool = Query(default=False),
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    _require_module_kp(repo, identity, module_id)
    model_execution = ModelExecutionSnapshot.capture(repo, settings)
    execution_settings = model_execution.settings
    llm = OpenAICompatibleClient(
        execution_settings.llm_base_url,
        execution_settings.llm_api_key,
        execution_settings.llm_model,
        client=getattr(request.app.state, "http_client", None),
    )
    result = await ModuleKnowledgeService(repo).extract(
        module_id,
        llm,
        model_name=execution_settings.llm_model,
        limit=limit,
        retry_failed=retry_failed,
    )
    module = repo.get_module(module_id)
    run = repo.get_active_campaign_module_run(str(module["campaign_id"]))
    if (
        run is not None
        and str(run.get("module_id")) == module_id
        and str(run.get("automation_level") or "conservative") == "ai_kp"
    ):
        ModuleEntityMaterializationService(repo).review_and_materialize(
            module_id,
            tuple(str(item) for item in result.get("accepted_candidate_ids") or ()),
            member_id=identity.member_id,
            note="auto-reviewed after extraction in ai_kp mode",
        )
    try:
        model_execution.revalidate(repo)
    except ModelExecutionSuperseded as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result


@router.get("/modules/{module_id}/knowledge/candidates")
def list_module_knowledge_candidates(
    module_id: str,
    candidate_status: str | None = Query(default=None, alias="status"),
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> list[dict]:
    _require_module_kp(repo, identity, module_id)
    if candidate_status not in {None, "pending", "approved", "rejected"}:
        raise HTTPException(status_code=422, detail="Invalid candidate status")
    return repo.list_module_knowledge_candidates(module_id, status=candidate_status)


@router.post("/module-knowledge/{candidate_id}/review")
def review_module_knowledge_candidate(
    candidate_id: str,
    payload: ModuleKnowledgeReview,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
) -> dict:
    candidate = repo.get_module_knowledge_candidate(candidate_id)
    _require_module_kp(repo, identity, candidate["module_id"])
    return ModuleKnowledgeService(repo).review_candidate(
        candidate_id,
        decision=payload.decision,
        member_id=identity.member_id,
        note=(payload.note or "").strip() or None,
    )

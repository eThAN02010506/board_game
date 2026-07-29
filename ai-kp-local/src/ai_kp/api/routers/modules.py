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
)
from ai_kp.api.uploads import read_limited_body, safe_upload_filename
from ai_kp.application.module_knowledge_service import ModuleKnowledgeService
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
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
    llm = OpenAICompatibleClient(
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        client=getattr(request.app.state, "http_client", None),
    )
    return await ModuleKnowledgeService(repo).extract(
        module_id,
        llm,
        model_name=settings.llm_model,
        limit=limit,
        retry_failed=retry_failed,
    )


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

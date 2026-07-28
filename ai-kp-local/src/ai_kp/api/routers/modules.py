"""KP-only PDF/DOCX module import and private asset routes."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request, status
from fastapi.responses import FileResponse

from ai_kp.api.authz import require_campaign_role
from ai_kp.api.dependencies import get_app_settings, get_identity, get_repo
from ai_kp.api.uploads import read_limited_body, safe_upload_filename
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.modules import ModuleDocumentStorage
from ai_kp.infrastructure.modules.import_worker import (
    enqueue_module_import,
    queue_module_import_retry,
    run_module_import,
)
from ai_kp.platform.modules.documents import MAX_MODULE_BYTES
from ai_kp.platform.sessions.models import AuthenticatedMember

router = APIRouter(tags=["module documents"])


@router.post(
    "/campaigns/{campaign_id}/module-imports",
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_module_document(
    campaign_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
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
    job = enqueue_module_import(
        settings.db_path,
        settings.module_asset_root,
        campaign_id=campaign_id,
        title=title,
        source_filename=filename,
        data=data,
    )
    background_tasks.add_task(
        run_module_import,
        settings.db_path,
        settings.module_asset_root,
        job["id"],
    )
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
    background_tasks: BackgroundTasks,
    identity: AuthenticatedMember = Depends(get_identity),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    job = repo.get_module_import_job(job_id)
    require_campaign_role(identity, job["campaign_id"], ("kp",))
    queued = queue_module_import_retry(settings.db_path, job_id)
    background_tasks.add_task(
        run_module_import,
        settings.db_path,
        settings.module_asset_root,
        job_id,
    )
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

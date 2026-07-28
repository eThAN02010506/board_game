"""Local-administrator endpoints for online snapshots and verification."""

from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from ai_kp.api.authz import require_local_admin
from ai_kp.api.dependencies import get_app_settings
from ai_kp.api.schemas import (
    BackupCreateResponse,
    BackupSummaryResponse,
    BackupVerifyResponse,
)
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.backups import BackupLimits, BackupService

router = APIRouter(prefix="/admin/backups", tags=["backups"])


def _app_version() -> str:
    try:
        return version("ai-kp-local")
    except PackageNotFoundError:
        return "0.1.0"


def _service(settings: Settings) -> BackupService:
    return BackupService(
        db_path=settings.db_path,
        map_asset_root=settings.map_asset_root,
        module_asset_root=settings.module_asset_root,
        rulebook_index_root=settings.rulebook_index_root,
        backup_root=settings.backup_root,
        app_version=_app_version(),
        limits=BackupLimits(
            max_files=settings.backup_max_files,
            max_uncompressed_bytes=settings.backup_max_uncompressed_bytes,
        ),
    )


@router.get("", response_model=list[BackupSummaryResponse])
def list_backups(
    _admin: None = Depends(require_local_admin),
    settings: Settings = Depends(get_app_settings),
) -> list[dict]:
    return _service(settings).list()


@router.post("", response_model=BackupCreateResponse)
def create_backup(
    _admin: None = Depends(require_local_admin),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    path, manifest = _service(settings).create()
    return {
        "filename": path.name,
        "size": path.stat().st_size,
        "manifest": manifest.to_dict(),
    }


@router.post("/{filename}/verify", response_model=BackupVerifyResponse)
def verify_backup(
    filename: str,
    _admin: None = Depends(require_local_admin),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    service = _service(settings)
    manifest = service.verify(service.resolve(filename))
    return {"ok": True, "filename": filename, "manifest": manifest.to_dict()}


@router.get("/{filename}/content")
def download_backup(
    filename: str,
    _admin: None = Depends(require_local_admin),
    settings: Settings = Depends(get_app_settings),
) -> FileResponse:
    path = _service(settings).resolve(filename)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=path.name,
        headers={"Cache-Control": "no-store"},
    )

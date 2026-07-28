"""Local durable worker for private PDF/DOCX module imports."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path, PurePosixPath

from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.infrastructure.modules import ModuleDocumentStorage
from ai_kp.platform.modules.documents import (
    PARSER_VERSION,
    detect_document_type,
    extract_module_document,
)


def enqueue_module_import(
    db_path: Path,
    module_asset_root: Path,
    *,
    campaign_id: str,
    title: str,
    source_filename: str,
    data: bytes,
) -> dict:
    source_type = detect_document_type(data, source_filename)
    source_hash = hashlib.sha256(data).hexdigest()
    storage = ModuleDocumentStorage(module_asset_root)
    storage_path = storage.store_source(
        data,
        source_hash,
        PurePosixPath(source_filename).suffix,
    )
    connection = connect(db_path)
    try:
        init_db(connection)
        repo = Repository(connection)
        repo.get_campaign(campaign_id)
        job = repo.create_module_import_job(
            campaign_id=campaign_id,
            title=title.strip() or PurePosixPath(source_filename).stem,
            source_filename=source_filename,
            source_type=source_type,
            source_hash=source_hash,
            source_storage_path=storage_path,
            parser_version=PARSER_VERSION,
        )
        connection.commit()
        return job
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def queue_module_import_retry(db_path: Path, job_id: str) -> dict:
    connection = connect(db_path)
    try:
        init_db(connection)
        job = Repository(connection).retry_module_import_job(job_id)
        connection.commit()
        return job
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def run_module_import(
    db_path: Path,
    module_asset_root: Path,
    job_id: str,
) -> None:
    connection = connect(db_path)
    storage = ModuleDocumentStorage(module_asset_root)
    try:
        init_db(connection)
        repo = Repository(connection)
        job = repo.claim_module_import_job(job_id)
        connection.commit()
        if job is None:
            return
        source_path = storage.resolve(job["source_storage_path"])
        data = source_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != job["source_hash"]:
            raise ValueError("KP 本源文件哈希校验失败")

        repo.update_module_import_progress(job_id, stage="extracting")
        connection.commit()
        extracted = extract_module_document(
            data,
            job["source_filename"],
            title=job["title"],
        )
        if extracted.source_hash != job["source_hash"]:
            raise ValueError("解析结果与源文件哈希不一致")

        total = len(extracted.chunks) + len(extracted.assets)
        repo.update_module_import_progress(
            job_id,
            stage="storing",
            current=len(extracted.chunks),
            total=total,
        )
        connection.commit()
        stored_assets = []
        for index, asset in enumerate(extracted.assets, start=1):
            suffix = _canonical_asset_suffix(asset.mime_type)
            content_hash, storage_path = storage.store_asset(asset.data, suffix)
            stored_assets.append((asset, content_hash, storage_path))
            repo.update_module_import_progress(
                job_id,
                stage="storing",
                current=len(extracted.chunks) + index,
                total=total,
            )
        repo.complete_module_document_import(
            job_id,
            chunks=extracted.chunks,
            assets=tuple(stored_assets),
        )
        connection.commit()
    except Exception as exc:  # noqa: BLE001 - persistent job boundary records parser failures
        connection.rollback()
        try:
            Repository(connection).fail_module_import_job(job_id, str(exc))
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
    finally:
        connection.close()


def _canonical_asset_suffix(mime_type: str) -> str:
    return {
        "image/bmp": ".bmp",
        "image/gif": ".gif",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/tiff": ".tiff",
        "image/webp": ".webp",
    }.get(mime_type, ".bin")

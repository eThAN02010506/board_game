"""Persistence for durable KP module document ingestion."""

from __future__ import annotations

import json
from pathlib import PurePosixPath

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.modules.documents import DocumentAsset, DocumentChunk


class ModuleImportRepository(SQLiteRepository):
    def create_module_import_job(
        self,
        *,
        campaign_id: str,
        title: str,
        source_filename: str,
        source_type: str,
        source_hash: str,
        source_storage_path: str,
        parser_version: str,
    ) -> dict:
        job_id = new_id("modjob")
        self.connection.execute(
            """
            INSERT INTO module_import_jobs
              (id, campaign_id, title, source_filename, source_type, source_hash,
               source_storage_path, parser_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                campaign_id,
                title,
                source_filename,
                source_type,
                source_hash,
                source_storage_path,
                parser_version,
            ),
        )
        return self.get_module_import_job(job_id)

    def get_module_import_job(self, job_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM module_import_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Module import job not found: {job_id}")
        return row_to_dict(row)

    def list_module_import_jobs(self, campaign_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM module_import_jobs
            WHERE campaign_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (campaign_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def claim_module_import_job(self, job_id: str) -> dict | None:
        cursor = self.connection.execute(
            """
            UPDATE module_import_jobs
            SET status = 'processing',
                stage = 'validating',
                progress_current = 0,
                progress_total = 0,
                attempt_count = attempt_count + 1,
                error_text = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('queued', 'failed')
            """,
            (job_id,),
        )
        if cursor.rowcount != 1:
            return None
        return self.get_module_import_job(job_id)

    def retry_module_import_job(self, job_id: str) -> dict:
        cursor = self.connection.execute(
            """
            UPDATE module_import_jobs
            SET status = 'queued',
                stage = 'queued',
                progress_current = 0,
                progress_total = 0,
                error_text = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'failed'
            """,
            (job_id,),
        )
        if cursor.rowcount != 1:
            job = self.get_module_import_job(job_id)
            raise ValueError(f"Only failed imports can be retried; current status is {job['status']}")
        return self.get_module_import_job(job_id)

    def update_module_import_progress(
        self,
        job_id: str,
        *,
        stage: str,
        current: int = 0,
        total: int = 0,
    ) -> None:
        self.connection.execute(
            """
            UPDATE module_import_jobs
            SET stage = ?, progress_current = ?, progress_total = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'processing'
            """,
            (stage, current, total, job_id),
        )

    def complete_module_document_import(
        self,
        job_id: str,
        *,
        chunks: tuple[DocumentChunk, ...],
        assets: tuple[tuple[DocumentAsset, str, str], ...],
    ) -> dict:
        job = self.get_module_import_job(job_id)
        if job["status"] != "processing":
            raise ValueError("Module import job is not processing")
        module_id = new_id("mod")
        self.connection.execute(
            """
            INSERT INTO modules
              (id, campaign_id, title, source_type, source_filename, source_hash,
               source_storage_path, parser_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                module_id,
                job["campaign_id"],
                job["title"],
                job["source_type"],
                job["source_filename"],
                job["source_hash"],
                job["source_storage_path"],
                job["parser_version"],
            ),
        )
        for chunk in chunks:
            self.connection.execute(
                """
                INSERT INTO module_chunks
                  (id, module_id, title, text, visibility, order_index, content_kind,
                   page_start, page_end, paragraph_start, paragraph_end, source_locator,
                   semantic_kind, classification_confidence, style_annotations_json,
                   review_flags_json)
                VALUES (?, ?, ?, ?, 'kp', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("chunk"),
                    module_id,
                    chunk.title,
                    chunk.text,
                    chunk.order_index,
                    chunk.content_kind,
                    chunk.page_start,
                    chunk.page_end,
                    chunk.paragraph_start,
                    chunk.paragraph_end,
                    chunk.source_locator,
                    chunk.semantic_kind,
                    chunk.classification_confidence,
                    json.dumps(chunk.style_annotations, ensure_ascii=False),
                    json.dumps(chunk.review_flags, ensure_ascii=False),
                ),
            )
        for asset, content_hash, storage_path in assets:
            self.connection.execute(
                """
                INSERT INTO module_assets
                  (id, module_id, content_hash, storage_path, mime_type, width, height,
                   source_locator, nearby_heading, asset_role, classification_confidence,
                   review_flags_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("modasset"),
                    module_id,
                    content_hash,
                    storage_path,
                    asset.mime_type,
                    asset.width,
                    asset.height,
                    asset.source_locator,
                    asset.nearby_heading,
                    asset.asset_role,
                    asset.classification_confidence,
                    json.dumps(asset.review_flags, ensure_ascii=False),
                ),
            )
        total = len(chunks) + len(assets)
        self.connection.execute(
            """
            UPDATE module_import_jobs
            SET status = 'completed', stage = 'completed',
                progress_current = ?, progress_total = ?, module_id = ?,
                error_text = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'processing'
            """,
            (total, total, module_id, job_id),
        )
        return self.get_module_import_job(job_id)

    def fail_module_import_job(self, job_id: str, error_text: str) -> dict:
        self.connection.execute(
            """
            UPDATE module_import_jobs
            SET status = 'failed', stage = 'failed', error_text = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'processing'
            """,
            (error_text[:4000], job_id),
        )
        return self.get_module_import_job(job_id)

    def recover_interrupted_module_imports(self) -> int:
        cursor = self.connection.execute(
            """
            UPDATE module_import_jobs
            SET status = 'failed', stage = 'failed',
                error_text = '服务在解析完成前中断；请重试此任务',
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'processing'
            """
        )
        return cursor.rowcount

    def list_module_assets(self, module_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM module_assets
            WHERE module_id = ?
            ORDER BY created_at, id
            """,
            (module_id,),
        ).fetchall()
        result = [row_to_dict(row) for row in rows]
        for item in result:
            item["review_flags"] = decode_json_field(
                item.pop("review_flags_json", "[]"),
                [],
            )
        return result

    def get_module_asset(self, asset_id: str) -> dict:
        row = self.connection.execute(
            """
            SELECT a.*, m.campaign_id
            FROM module_assets a
            JOIN modules m ON m.id = a.module_id
            WHERE a.id = ?
            """,
            (asset_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Module asset not found: {asset_id}")
        result = row_to_dict(row)
        result["review_flags"] = decode_json_field(
            result.pop("review_flags_json", "[]"),
            [],
        )
        result["download_name"] = (
            f"{result['id']}{PurePosixPath(result['storage_path']).suffix or '.bin'}"
        )
        return result

"""Durable Auto KP background job queue."""

from __future__ import annotations

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository

AUTO_KP_JOB_STATUSES = {
    "queued",
    "running",
    "retry_wait",
    "succeeded",
    "failed",
    "needs_attention",
    "cancelled",
}
AUTO_KP_JOB_TYPES = {
    "player_action",
    "parallel_actions",
    "world_expansion",
    "check_consequence",
}


class AutoKpJobRepository(SQLiteRepository):
    def enqueue_auto_kp_job(
        self,
        *,
        campaign_id: str,
        job_type: str,
        resource_id: str,
        idempotency_key: str,
        run_id: str | None = None,
        payload: dict[str, Any] | None = None,
        max_attempts: int = 3,
    ) -> dict:
        self._validate_job_type(job_type)
        if not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        job_id = new_id("autojob")
        self.connection.execute(
            """
            INSERT INTO auto_kp_jobs
              (id, campaign_id, run_id, job_type, resource_id, idempotency_key,
               max_attempts, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, idempotency_key) DO NOTHING
            """,
            (
                job_id,
                campaign_id,
                run_id,
                job_type,
                resource_id,
                idempotency_key,
                max_attempts,
                json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
            ),
        )
        return self.get_auto_kp_job_by_key(campaign_id, idempotency_key)

    def get_auto_kp_job(self, job_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM auto_kp_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Auto KP job not found: {job_id}")
        return self._decode_auto_kp_job(row)

    def get_auto_kp_job_by_key(self, campaign_id: str, idempotency_key: str) -> dict:
        row = self.connection.execute(
            """
            SELECT * FROM auto_kp_jobs
            WHERE campaign_id = ? AND idempotency_key = ?
            """,
            (campaign_id, idempotency_key),
        ).fetchone()
        if row is None:
            raise KeyError("Auto KP job was not persisted")
        return self._decode_auto_kp_job(row)

    def list_auto_kp_jobs(
        self,
        campaign_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        if status is not None and status not in AUTO_KP_JOB_STATUSES:
            raise ValueError("Unsupported Auto KP job status")
        if not 1 <= limit <= 100:
            raise ValueError("Invalid Auto KP job limit")
        filters = ["campaign_id = ?"]
        params: list[Any] = [campaign_id]
        if status is not None:
            filters.append("status = ?")
            params.append(status)
        params.append(limit)
        rows = self.connection.execute(
            f"""
            SELECT * FROM auto_kp_jobs
            WHERE {" AND ".join(filters)}
            ORDER BY updated_at DESC, created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._decode_auto_kp_job(row) for row in rows]

    def claim_next_auto_kp_job(self, *, worker_id: str) -> dict | None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if not self.connection.in_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        row = self.connection.execute(
            """
            SELECT id FROM auto_kp_jobs
            WHERE status IN ('queued', 'retry_wait')
              AND (next_run_at IS NULL OR next_run_at <= CURRENT_TIMESTAMP)
            ORDER BY created_at, id
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        cursor = self.connection.execute(
            """
            UPDATE auto_kp_jobs
            SET status = 'running',
                stage = 'running',
                attempt_count = attempt_count + 1,
                locked_by = ?,
                locked_at = CURRENT_TIMESTAMP,
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('queued', 'retry_wait')
            """,
            (worker_id, row["id"]),
        )
        if cursor.rowcount != 1:
            return None
        return self.get_auto_kp_job(str(row["id"]))

    def complete_auto_kp_job(
        self,
        job_id: str,
        *,
        expected_attempt: int,
        status: str = "succeeded",
        stage: str = "completed",
        result: dict[str, Any] | None = None,
    ) -> dict:
        if status not in {"succeeded", "needs_attention"}:
            raise ValueError("Completion status must be succeeded or needs_attention")
        cursor = self.connection.execute(
            """
            UPDATE auto_kp_jobs
            SET status = ?, stage = ?, result_json = ?, locked_by = NULL,
                locked_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running' AND attempt_count = ?
            """,
            (
                status,
                stage,
                json.dumps(result or {}, ensure_ascii=False, sort_keys=True),
                job_id,
                expected_attempt,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("Auto KP job claim is no longer current")
        return self.get_auto_kp_job(job_id)

    def fail_auto_kp_job(
        self,
        job_id: str,
        *,
        expected_attempt: int,
        error: str,
    ) -> dict:
        job = self.get_auto_kp_job(job_id)
        next_status = (
            "retry_wait"
            if int(job["attempt_count"]) < int(job["max_attempts"])
            else "failed"
        )
        cursor = self.connection.execute(
            """
            UPDATE auto_kp_jobs
            SET status = ?, stage = ?, last_error = ?, locked_by = NULL,
                locked_at = NULL, next_run_at = datetime(CURRENT_TIMESTAMP, '+30 seconds'),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running' AND attempt_count = ?
            """,
            (
                next_status,
                "waiting_retry" if next_status == "retry_wait" else "failed",
                error[:2000],
                job_id,
                expected_attempt,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("Auto KP job claim is no longer current")
        return self.get_auto_kp_job(job_id)

    def retry_auto_kp_job(self, job_id: str) -> dict:
        job = self.get_auto_kp_job(job_id)
        if job["status"] not in {"failed", "needs_attention", "retry_wait"}:
            raise ValueError(
                "Only failed, waiting-retry, or attention jobs can be retried; "
                f"current status is {job['status']}"
            )
        self.connection.execute(
            """
            UPDATE auto_kp_jobs
            SET status = 'queued', stage = 'queued', next_run_at = NULL,
                locked_by = NULL, locked_at = NULL, last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (job_id,),
        )
        return self.get_auto_kp_job(job_id)

    def recover_stale_auto_kp_jobs(self) -> int:
        cursor = self.connection.execute(
            """
            UPDATE auto_kp_jobs
            SET status = 'retry_wait',
                stage = 'recovered',
                locked_by = NULL,
                locked_at = NULL,
                next_run_at = CURRENT_TIMESTAMP,
                last_error = 'Recovered stale running Auto KP job',
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'running'
              AND locked_at <= datetime(CURRENT_TIMESTAMP, '-10 minutes')
              AND attempt_count < max_attempts
            """
        )
        return int(cursor.rowcount)

    def _decode_auto_kp_job(self, row: Any) -> dict:
        result = row_to_dict(row)
        result["payload"] = decode_json_field(result.pop("payload_json"), {})
        result["result"] = decode_json_field(result.pop("result_json"), {})
        return result

    def _validate_job_type(self, job_type: str) -> None:
        if job_type not in AUTO_KP_JOB_TYPES:
            raise ValueError("Unsupported Auto KP job type")


__all__ = ["AUTO_KP_JOB_STATUSES", "AUTO_KP_JOB_TYPES", "AutoKpJobRepository"]

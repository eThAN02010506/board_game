"""Durable Auto KP background job queue."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.resolution.automation_state_machine import (
    AutomationJobState,
    AutomationJobStateMachine,
)

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
    "encounter_turn",
}


class AutoKpJobRepository(SQLiteRepository):
    _state_machine = AutomationJobStateMachine()

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
        delay_seconds: int = 0,
    ) -> dict:
        self._validate_job_type(job_type)
        if not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if not 0 <= delay_seconds <= 3600:
            raise ValueError("delay_seconds must be between 0 and 3600")
        job_id = new_id("autojob")
        self.connection.execute(
            """
            INSERT INTO auto_kp_jobs
              (id, campaign_id, run_id, job_type, resource_id, idempotency_key,
               max_attempts, payload_json, next_run_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime(CURRENT_TIMESTAMP, ?))
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
                f"+{delay_seconds} seconds",
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

    def list_player_auto_kp_jobs(
        self,
        campaign_id: str,
        member_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return only action/check jobs belonging to one authenticated player."""

        if status is not None and status not in AUTO_KP_JOB_STATUSES:
            raise ValueError("Unsupported Auto KP job status")
        if not 1 <= limit <= 100:
            raise ValueError("Invalid Auto KP job limit")
        filters = [
            "j.campaign_id = ?",
            (
                "(pa.member_id = ? OR cpa.member_id = ? OR ppa.member_id = ? "
                "OR committed_pa.member_id = ?)"
            ),
        ]
        params: list[Any] = [
            campaign_id,
            member_id,
            member_id,
            member_id,
            member_id,
        ]
        if status is not None:
            filters.append("j.status = ?")
            params.append(status)
        params.append(limit)
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT j.*
            FROM auto_kp_jobs j
            LEFT JOIN player_actions pa
              ON j.job_type = 'player_action' AND pa.id = j.resource_id
            LEFT JOIN skill_checks sc
              ON j.job_type = 'check_consequence' AND sc.id = j.resource_id
            LEFT JOIN player_actions cpa ON cpa.id = sc.player_action_id
            LEFT JOIN json_each(j.payload_json, '$.action_ids') batch_action
              ON j.job_type = 'parallel_actions'
            LEFT JOIN player_actions ppa ON ppa.id = batch_action.value
            LEFT JOIN parallel_action_batches committed_batch
              ON j.job_type = 'parallel_actions'
             AND json_extract(j.payload_json, '$.phase') = 'commit'
             AND committed_batch.id = json_extract(j.payload_json, '$.batch_id')
            LEFT JOIN parallel_action_batch_items committed_item
              ON committed_item.batch_id = committed_batch.id
            LEFT JOIN player_actions committed_pa
              ON committed_pa.id = committed_item.action_id
            WHERE {" AND ".join(filters)}
            ORDER BY j.updated_at DESC, j.created_at DESC, j.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._decode_auto_kp_job(row) for row in rows]

    def get_player_auto_kp_job(self, job_id: str, member_id: str) -> dict:
        row = self.connection.execute(
            """
            SELECT DISTINCT j.*
            FROM auto_kp_jobs j
            LEFT JOIN player_actions pa
              ON j.job_type = 'player_action' AND pa.id = j.resource_id
            LEFT JOIN skill_checks sc
              ON j.job_type = 'check_consequence' AND sc.id = j.resource_id
            LEFT JOIN player_actions cpa ON cpa.id = sc.player_action_id
            LEFT JOIN json_each(j.payload_json, '$.action_ids') batch_action
              ON j.job_type = 'parallel_actions'
            LEFT JOIN player_actions ppa ON ppa.id = batch_action.value
            LEFT JOIN parallel_action_batches committed_batch
              ON j.job_type = 'parallel_actions'
             AND json_extract(j.payload_json, '$.phase') = 'commit'
             AND committed_batch.id = json_extract(j.payload_json, '$.batch_id')
            LEFT JOIN parallel_action_batch_items committed_item
              ON committed_item.batch_id = committed_batch.id
            LEFT JOIN player_actions committed_pa
              ON committed_pa.id = committed_item.action_id
            WHERE j.id = ?
              AND (pa.member_id = ? OR cpa.member_id = ? OR ppa.member_id = ?
                   OR committed_pa.member_id = ?)
            """,
            (job_id, member_id, member_id, member_id, member_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"Player Auto KP job not found: {job_id}")
        return self._decode_auto_kp_job(row)

    def cancel_auto_kp_job(self, job_id: str) -> dict:
        job = self.get_auto_kp_job(job_id)
        transition = self._state_machine.transition(self._job_state(job), "cancel")
        self.connection.execute(
            """
            UPDATE auto_kp_jobs
            SET status = ?, stage = ?, next_run_at = NULL,
                locked_by = NULL, locked_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('queued', 'retry_wait')
            """,
            (transition.current.status, transition.current.stage, job_id),
        )
        return self.get_auto_kp_job(job_id)

    def expand_queued_parallel_prepare_job(
        self,
        job_id: str,
        *,
        expected_payload: Mapping[str, object],
        expanded_payload: Mapping[str, object],
    ) -> dict | None:
        """CAS-expand an unclaimed prepare reservation without moving its deadline."""

        expected_ids = expected_payload.get("action_ids")
        expanded_ids = expanded_payload.get("action_ids")
        if not isinstance(expected_ids, list) or not isinstance(expanded_ids, list):
            raise TypeError("Parallel prepare action_ids must be arrays")
        expected_set = {str(item) for item in expected_ids}
        expanded_set = {str(item) for item in expanded_ids}
        if (
            expected_payload.get("phase", "prepare") != "prepare"
            or expanded_payload.get("phase", "prepare") != "prepare"
            or not 2 <= len(expected_ids) < len(expanded_ids) <= 12
            or len(expected_set) != len(expected_ids)
            or len(expanded_set) != len(expanded_ids)
            or not expected_set < expanded_set
        ):
            raise ValueError(
                "Parallel prepare expansion requires a strict 2-12 action superset"
            )
        expected_static = dict(expected_payload)
        expanded_static = dict(expanded_payload)
        for mutable_key in (
            "action_ids",
            "collection_anchor_action_id",
            "collection_anchor_at",
        ):
            expected_static.pop(mutable_key, None)
            expanded_static.pop(mutable_key, None)
        if expected_static != expanded_static:
            raise ValueError("Parallel prepare expansion cannot change job authority")

        job = self.get_auto_kp_job(job_id)
        if (
            job.get("job_type") != "parallel_actions"
            or job.get("status") != "queued"
            or int(job.get("attempt_count") or 0) != 0
            or job.get("locked_by") is not None
            or job.get("locked_at") is not None
            or str(job.get("resource_id") or "") not in expanded_set
            or job.get("payload") != dict(expected_payload)
        ):
            return None
        expected_json = json.dumps(
            dict(expected_payload), ensure_ascii=False, sort_keys=True
        )
        expanded_json = json.dumps(
            dict(expanded_payload), ensure_ascii=False, sort_keys=True
        )
        cursor = self.connection.execute(
            """
            UPDATE auto_kp_jobs
            SET payload_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND job_type = 'parallel_actions'
              AND status = 'queued'
              AND attempt_count = 0
              AND locked_by IS NULL
              AND locked_at IS NULL
              AND payload_json = ?
            """,
            (expanded_json, job_id, expected_json),
        )
        if cursor.rowcount != 1:
            return None
        return self.get_auto_kp_job(job_id)

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
        job = self.get_auto_kp_job(str(row["id"]))
        transition = self._state_machine.transition(self._job_state(job), "claim")
        cursor = self.connection.execute(
            """
            UPDATE auto_kp_jobs
            SET status = ?,
                stage = ?,
                attempt_count = ?,
                locked_by = ?,
                locked_at = CURRENT_TIMESTAMP,
                last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('queued', 'retry_wait')
            """,
            (
                transition.current.status,
                transition.current.stage,
                transition.current.attempt_count,
                worker_id,
                row["id"],
            ),
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
        job = self.get_auto_kp_job(job_id)
        event = "succeed" if status == "succeeded" else "request_attention"
        transition = self._state_machine.transition(self._job_state(job), event)
        cursor = self.connection.execute(
            """
            UPDATE auto_kp_jobs
            SET status = ?, stage = ?, result_json = ?, locked_by = NULL,
                locked_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running' AND attempt_count = ?
            """,
            (
                transition.current.status,
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
        transition = self._state_machine.transition(self._job_state(job), "fail")
        delay = transition.retry_after_seconds
        next_run_modifier = f"+{delay} seconds" if delay is not None else None
        cursor = self.connection.execute(
            """
            UPDATE auto_kp_jobs
            SET status = ?, stage = ?, last_error = ?, locked_by = NULL,
                locked_at = NULL,
                next_run_at = CASE
                  WHEN ? IS NULL THEN NULL
                  ELSE datetime(CURRENT_TIMESTAMP, ?)
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'running' AND attempt_count = ?
            """,
            (
                transition.current.status,
                transition.current.stage,
                error[:2000],
                next_run_modifier,
                next_run_modifier,
                job_id,
                expected_attempt,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("Auto KP job claim is no longer current")
        return self.get_auto_kp_job(job_id)

    def retry_auto_kp_job(self, job_id: str) -> dict:
        job = self.get_auto_kp_job(job_id)
        transition = self._state_machine.transition(self._job_state(job), "retry")
        self.connection.execute(
            """
            UPDATE auto_kp_jobs
            SET status = ?, stage = ?, next_run_at = NULL,
                locked_by = NULL, locked_at = NULL, last_error = NULL,
                attempt_count = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                transition.current.status,
                transition.current.stage,
                transition.current.attempt_count,
                job_id,
            ),
        )
        return self.get_auto_kp_job(job_id)

    def recover_stale_auto_kp_jobs(self, *, campaign_id: str | None) -> int:
        """Recover one campaign's claims, or all claims at worker startup."""

        if campaign_id is None:
            rows = self.connection.execute(
                """
                SELECT * FROM auto_kp_jobs
                WHERE status = 'running'
                  AND locked_at <= datetime(CURRENT_TIMESTAMP, '-10 minutes')
                ORDER BY id
                """
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT * FROM auto_kp_jobs
                WHERE campaign_id = ? AND status = 'running'
                  AND locked_at <= datetime(CURRENT_TIMESTAMP, '-10 minutes')
                ORDER BY id
                """,
                (campaign_id,),
            ).fetchall()
        recovered = 0
        for row in rows:
            job = self._decode_auto_kp_job(row)
            transition = self._state_machine.transition(
                self._job_state(job), "recover_stale"
            )
            delay = transition.retry_after_seconds
            modifier = f"+{delay} seconds" if delay is not None else None
            error = (
                "Recovered stale running Auto KP job"
                if transition.current.status == "retry_wait"
                else "Recovered stale Auto KP job after retry limit"
            )
            cursor = self.connection.execute(
                """
                UPDATE auto_kp_jobs
                SET status = ?, stage = ?, locked_by = NULL, locked_at = NULL,
                    next_run_at = CASE
                      WHEN ? IS NULL THEN NULL
                      ELSE datetime(CURRENT_TIMESTAMP, ?)
                    END,
                    last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'running' AND attempt_count = ?
                """,
                (
                    transition.current.status,
                    transition.current.stage,
                    modifier,
                    modifier,
                    error,
                    job["id"],
                    job["attempt_count"],
                ),
            )
            recovered += int(cursor.rowcount)
        return recovered

    @staticmethod
    def _job_state(job: dict[str, Any]) -> AutomationJobState:
        return AutomationJobState(
            status=job["status"],
            stage=job["stage"],
            attempt_count=int(job["attempt_count"]),
            max_attempts=int(job["max_attempts"]),
        )

    def _decode_auto_kp_job(self, row: Any) -> dict:
        result = row_to_dict(row)
        result["payload"] = decode_json_field(result.pop("payload_json"), {})
        result["result"] = decode_json_field(result.pop("result_json"), {})
        return result

    def _validate_job_type(self, job_type: str) -> None:
        if job_type not in AUTO_KP_JOB_TYPES:
            raise ValueError("Unsupported Auto KP job type")


__all__ = ["AUTO_KP_JOB_STATUSES", "AUTO_KP_JOB_TYPES", "AutoKpJobRepository"]

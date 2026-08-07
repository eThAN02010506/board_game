"""Durable scheduling for player-facing Auto KP progression."""

from __future__ import annotations

from hashlib import sha256
from typing import Protocol

from ai_kp.platform.resolution import build_check_consequence_snapshot

ACTION_COLLECTION_SECONDS = 2


class AutoKpQueueStore(Protocol):
    def get_player_action(self, action_id: str) -> dict: ...

    def get_skill_check(self, check_id: str) -> dict: ...

    def list_skill_checks_for_action(self, player_action_id: str) -> list[dict]: ...

    def list_opposed_checks_for_action(self, player_action_id: str) -> list[dict]: ...

    def get_active_campaign_module_run(self, campaign_id: str) -> dict | None: ...

    def list_player_actions(
        self, campaign_id: str, session_id: str, *, status: str | None = None
    ) -> list[dict]: ...

    def list_auto_kp_jobs(
        self, campaign_id: str, *, status: str | None = None, limit: int = 50
    ) -> list[dict]: ...

    def cancel_auto_kp_job(self, job_id: str) -> dict: ...

    def enqueue_auto_kp_job(
        self,
        *,
        campaign_id: str,
        job_type: str,
        resource_id: str,
        idempotency_key: str,
        run_id: str | None = None,
        payload: dict | None = None,
        max_attempts: int = 3,
        delay_seconds: int = 0,
    ) -> dict: ...


class AutoKpQueueService:
    """Create idempotent jobs at player action and resolved-check boundaries."""

    def __init__(self, repo: AutoKpQueueStore):
        self.repo = repo

    def enqueue_player_action(self, action_id: str) -> dict:
        action = self.repo.get_player_action(action_id)
        campaign_id = str(action["campaign_id"])
        session_id = str(action["session_id"])
        run = self.repo.get_active_campaign_module_run(campaign_id)
        submitted = self.repo.list_player_actions(
            campaign_id,
            session_id,
            status="submitted",
        )
        action_ids = sorted(str(item["id"]) for item in submitted)
        if len(action_ids) > 1:
            self._cancel_superseded_jobs(campaign_id, session_id, set(action_ids))
            fingerprint = sha256("\n".join(action_ids).encode("utf-8")).hexdigest()
            return self.repo.enqueue_auto_kp_job(
                campaign_id=campaign_id,
                run_id=str(run["id"]) if run is not None else None,
                job_type="parallel_actions",
                resource_id=action_id,
                idempotency_key=f"parallel-actions:{fingerprint}",
                payload={"action_ids": action_ids, "session_id": session_id},
                delay_seconds=ACTION_COLLECTION_SECONDS,
            )
        return self.repo.enqueue_auto_kp_job(
            campaign_id=campaign_id,
            run_id=str(run["id"]) if run is not None else None,
            job_type="player_action",
            resource_id=action_id,
            idempotency_key=f"player-action:{action_id}",
            payload={"action_id": action_id, "session_id": session_id},
            delay_seconds=ACTION_COLLECTION_SECONDS,
        )

    def _cancel_superseded_jobs(
        self,
        campaign_id: str,
        session_id: str,
        action_ids: set[str],
    ) -> None:
        for job in self.repo.list_auto_kp_jobs(campaign_id, limit=100):
            if job["status"] not in {"queued", "retry_wait"}:
                continue
            superseded_action = (
                job["job_type"] == "player_action"
                and job["resource_id"] in action_ids
            )
            superseded_batch = (
                job["job_type"] == "parallel_actions"
                and job.get("payload", {}).get("session_id") == session_id
            )
            if superseded_action or superseded_batch:
                self.repo.cancel_auto_kp_job(str(job["id"]))

    def enqueue_check_consequence(self, check_id: str) -> dict | None:
        check = self.repo.get_skill_check(check_id)
        action_id = check.get("player_action_id")
        if not action_id:
            return None
        action_id = str(action_id)
        action = self.repo.get_player_action(action_id)
        checks = self.repo.list_skill_checks_for_action(action_id)
        opposed = self.repo.list_opposed_checks_for_action(action_id)
        if not checks or any(item["status"] == "requested" for item in checks):
            return None
        if any(
            item["status"] in {"pending", "reroll_required"}
            for item in opposed
        ):
            # 平局（reroll_required）必须先重掷出裁决者，避免后果基于未定结果生成。
            return None
        snapshot = build_check_consequence_snapshot(checks, opposed)
        campaign_id = str(action["campaign_id"])
        run = self.repo.get_active_campaign_module_run(campaign_id)
        return self.repo.enqueue_auto_kp_job(
            campaign_id=campaign_id,
            run_id=str(run["id"]) if run is not None else None,
            job_type="check_consequence",
            resource_id=check_id,
            idempotency_key=(
                f"check-consequence:{action_id}:{snapshot['result_fingerprint']}"
            ),
            payload={
                "action_id": action_id,
                "result_fingerprint": snapshot["result_fingerprint"],
            },
        )


__all__ = ["AutoKpQueueService"]

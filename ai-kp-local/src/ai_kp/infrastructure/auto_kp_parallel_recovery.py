"""Fail-closed, no-KP recovery for stale parallel workflow authority."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ai_kp.application.parallel_action_workflow_service import (
    ParallelActionWorkflowService,
)
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.sessions.models import AuthenticatedMember

KpIdentityResolver = Callable[
    [Repository, Sequence[str]], AuthenticatedMember
]


def start_full_ai_reconfirmation(
    repo: Repository,
    *,
    batch_id: str,
    result: dict[str, Any],
    identity_resolver: KpIdentityResolver,
) -> dict[str, Any] | None:
    """Retire a safe pre-result conflict and ask every player again."""

    batch = repo.get_parallel_action_batch(batch_id)
    if batch["status"] != "needs_attention":
        return None
    abandonment = repo.get_parallel_action_abandonment_decision(batch_id)
    if not abandonment["abandon_allowed"]:
        return None
    action_ids = tuple(str(item["action_id"]) for item in batch["items"])
    identity = identity_resolver(repo, action_ids)
    ParallelActionWorkflowService(repo).abandon_needs_attention(
        batch_id,
        expected_version=int(batch["version"]),
        identity=identity,
        reason=(
            "Full AI KP retired a stale pre-result batch; every player must "
            "reconfirm a fresh action against the current authoritative state."
        ),
    )
    regather = repo.create_parallel_action_regather(
        batch_id,
        actor_member_id=None,
        reason="Automatic no-KP recovery after a pre-result authority conflict.",
    )
    return {
        "status": "succeeded",
        "stage": "parallel_reconfirmation",
        "workflow_status": "awaiting_group_resubmission",
        "regather_id": str(regather["id"]),
        "participant_count": int(regather["participant_count"]),
        "message": (
            "The stale batch was retired without reusing player consent. Every "
            "participant can now resubmit against the current scene."
        ),
        "previous_attention": str(result.get("message") or ""),
    }


def recover_stale_parallel_job(
    repo: Repository,
    job: dict[str, Any],
    *,
    reason: str,
    identity_resolver: KpIdentityResolver,
) -> dict[str, Any] | None:
    """Recover only an exact Full-AI prepare or commit after authority drift."""

    if job.get("job_type") != "parallel_actions":
        return None
    payload = dict(job.get("payload") or {})
    phase = str(payload.get("phase") or "prepare")
    campaign_id = str(job.get("campaign_id") or "")
    run = repo.get_active_campaign_module_run(campaign_id)
    if run is None or str(run.get("automation_level")) != "ai_kp":
        return None
    if phase == "prepare":
        return _recover_stale_regather(repo, job, payload=payload, reason=reason)
    if phase != "commit":
        return None
    return _recover_stale_commit(
        repo,
        job,
        payload=payload,
        reason=reason,
        identity_resolver=identity_resolver,
    )


def _recover_stale_regather(
    repo: Repository,
    job: dict[str, Any],
    *,
    payload: dict[str, Any],
    reason: str,
) -> dict[str, Any] | None:
    regather_id = str(payload.get("regather_id") or "")
    if not regather_id:
        return None
    regather = repo.get_parallel_action_regather(regather_id)
    action_ids = tuple(str(item) for item in payload.get("action_ids") or ())
    expected_ids = tuple(
        str(item["replacement_action_id"]) for item in regather["members"]
    )
    if (
        str(regather["campaign_id"]) != str(job.get("campaign_id") or "")
        or str(regather["session_id"]) != str(payload.get("session_id") or "")
        or str(regather.get("prepare_job_id") or "") != str(job.get("id") or "")
        or str(job.get("resource_id") or "") not in action_ids
        or action_ids != expected_ids
    ):
        return None
    reopened = repo.reopen_parallel_action_regather(
        regather_id,
        expected_prepare_job_id=str(job["id"]),
        reason=(
            "The scenario authority changed before planning. Every player "
            "must resubmit under the current scene."
        ),
    )
    return {
        "status": "succeeded",
        "stage": "parallel_reconfirmation",
        "workflow_status": "awaiting_group_resubmission",
        "regather_id": regather_id,
        "participant_count": int(reopened["participant_count"]),
        "message": "Scenario authority changed; fresh player consent is required.",
        "previous_attention": reason[:1000],
    }


def _recover_stale_commit(
    repo: Repository,
    job: dict[str, Any],
    *,
    payload: dict[str, Any],
    reason: str,
    identity_resolver: KpIdentityResolver,
) -> dict[str, Any] | None:
    batch_id = str(payload.get("batch_id") or "")
    if not batch_id or str(job.get("resource_id") or "") != batch_id:
        return None
    batch = repo.get_parallel_action_batch(batch_id)
    if (
        str(batch["campaign_id"]) != str(job.get("campaign_id") or "")
        or str(batch["session_id"]) != str(payload.get("session_id") or "")
        or batch["status"] != "ready"
        or payload.get("expected_version") != int(batch["version"])
    ):
        return None
    action_ids = tuple(str(item["action_id"]) for item in batch["items"])
    identity = identity_resolver(repo, action_ids)
    batch = repo.transition_parallel_action_batch(
        batch_id,
        "request_attention",
        expected_version=int(batch["version"]),
        actor_member_id=identity.member_id,
        reason="Full AI KP detected stale run authority before atomic commit.",
    )
    return start_full_ai_reconfirmation(
        repo,
        batch_id=batch_id,
        result={"message": reason, "version": batch["version"]},
        identity_resolver=identity_resolver,
    )


__all__ = ["recover_stale_parallel_job", "start_full_ai_reconfirmation"]

"""Restart-safe phase execution for durable parallel Auto KP jobs.

The queue worker owns job claiming, top-level authority validation, retry state,
and the final transaction boundary.  This module owns only the prepare, blind
check, and atomic commit phases after that validation has succeeded.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ai_kp.application.auto_kp_queue_service import (
    PARALLEL_FALLBACK_ROUTES,
    AutoKpQueueService,
)
from ai_kp.application.check_service import CheckService, ResolveCheckCommand
from ai_kp.application.parallel_action_planning_service import (
    ParallelPlanningModelError,
)
from ai_kp.application.parallel_action_workflow_service import (
    ParallelActionWorkflowService,
)
from ai_kp.bootstrap.settings import Settings
from ai_kp.director.orchestrator import KpOrchestrator
from ai_kp.infrastructure.auto_kp_parallel_recovery import (
    start_full_ai_reconfirmation,
)
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.llm.call_registry import CampaignAiCallRegistry
from ai_kp.platform.sessions.models import AuthenticatedMember

ParallelDirectorFactory = Callable[
    [Repository, Settings, CampaignAiCallRegistry | None],
    KpOrchestrator,
]


async def execute_parallel_action_job(
    repo: Repository,
    job: dict,
    *,
    payload: dict[str, Any],
    settings: Settings,
    call_registry: CampaignAiCallRegistry | None,
    director_factory: ParallelDirectorFactory,
) -> dict[str, Any]:
    """Run one restart-safe phase of the authoritative parallel workflow."""

    phase = str(payload.get("phase") or "prepare")
    campaign_id = str(job["campaign_id"])
    workflow = ParallelActionWorkflowService(repo)
    if phase == "prepare":
        action_ids = tuple(str(item) for item in payload.get("action_ids") or ())
        if not 2 <= len(action_ids) <= 12 or len(set(action_ids)) != len(action_ids):
            raise ValueError(
                "parallel prepare jobs require 2-12 distinct action_ids"
            )
        actions = tuple(repo.get_player_action(action_id) for action_id in action_ids)
        if any(str(action["campaign_id"]) != campaign_id for action in actions):
            raise PermissionError("Parallel prepare job crosses campaign boundaries")
        run = repo.get_active_campaign_module_run(campaign_id)
        if run is None:
            return {
                "status": "needs_attention",
                "stage": "parallel_prepare",
                "message": "Parallel planning requires an active scenario run.",
            }
        expected_run_version = payload.get("run_version")
        if (
            type(expected_run_version) is int
            and (
                str(job.get("run_id") or "") != str(run["id"])
                or expected_run_version != int(run["version"])
            )
        ):
            return {
                "status": "needs_attention",
                "stage": "parallel_prepare",
                "message": (
                    "The scenario run changed during the collection window; "
                    "players must review their actions in the current scene."
                ),
            }
        if str(run.get("automation_level") or "conservative") == "conservative":
            return {
                "status": "needs_attention",
                "stage": "parallel_prepare",
                "message": (
                    "Conservative automation keeps simultaneous actions for explicit "
                    "KP review."
                ),
            }
        identity = kp_identity_for_parallel_actions(repo, action_ids)
        try:
            result = await workflow.prepare(
                actions,
                identity,
                director_factory(repo, settings, call_registry),
                idempotency_key=f"workflow:{job['idempotency_key']}",
                source_model=settings.llm_model,
                profile=_semantic_profile(repo),
            )
        except ParallelPlanningModelError:
            fallback_jobs = AutoKpQueueService(
                repo
            ).enqueue_parallel_action_fallbacks(
                job,
                fallback_routes={
                    action_id: "clarification" for action_id in action_ids
                },
            )
            if fallback_jobs:
                _complete_regather(repo, payload, resulting_batch_id=None)
                return {
                    "status": "succeeded",
                    "stage": "parallel_fallback",
                    "workflow_status": "needs_attention",
                    "fallback_job_ids": [
                        str(candidate["id"]) for candidate in fallback_jobs
                    ],
                    "fallback_count": len(fallback_jobs),
                    "unsupported": [],
                    "message": (
                        "Parallel model planning failed safely; every action was "
                        "delegated to the existing single-action adjudication workflow."
                    ),
                }
            return {
                "status": "needs_attention",
                "stage": "parallel_prepare",
                "message": (
                    "Parallel model planning failed and the frozen run authority "
                    "changed before safe single-action fallback could be queued."
                ),
            }
        if (
            result.status == "needs_attention"
            and result.batch is None
            and result.unsupported
            and all(
                item.route in PARALLEL_FALLBACK_ROUTES
                for item in result.unsupported
            )
        ):
            fallback_jobs = AutoKpQueueService(
                repo
            ).enqueue_parallel_action_fallbacks(
                job,
                fallback_routes={
                    item.action_id: item.route for item in result.unsupported
                },
            )
            if fallback_jobs:
                _complete_regather(repo, payload, resulting_batch_id=None)
                return {
                    "status": "succeeded",
                    "stage": "parallel_fallback",
                    "workflow_status": result.status,
                    "fallback_job_ids": [
                        str(candidate["id"]) for candidate in fallback_jobs
                    ],
                    "fallback_count": len(fallback_jobs),
                    "unsupported": [
                        {
                            "action_id": item.action_id,
                            "route": item.route,
                            "reason": item.reason,
                        }
                        for item in result.unsupported
                    ],
                    "message": (
                        "Parallel planning safely delegated every action to the "
                        "existing single-action adjudication workflow."
                    ),
                }
        if result.batch is not None:
            _complete_regather(
                repo,
                payload,
                resulting_batch_id=str(result.batch["id"]),
            )
        return {
            "status": result.status,
            "stage": "parallel_prepare",
            **result.as_dict(),
        }

    if phase == "resolve_blind_checks":
        return _resolve_parallel_blind_checks(repo, job, payload=payload)

    if phase == "commit":
        batch_id = str(payload.get("batch_id") or "")
        if not batch_id:
            raise ValueError("parallel commit jobs require batch_id")
        batch = repo.get_parallel_action_batch(batch_id)
        if str(batch["campaign_id"]) != campaign_id:
            raise PermissionError("Parallel commit job crosses campaign boundaries")
        if batch["status"] not in {"ready", "settled"}:
            return {
                "status": "needs_attention",
                "stage": "parallel_commit",
                "batch_id": batch_id,
                "message": (
                    "Parallel commit job found a batch that is not ready for "
                    f"atomic settlement ({batch['status']})."
                ),
            }
        run = repo.get_active_campaign_module_run(campaign_id)
        if (
            run is None
            or str(run["id"]) != str(batch["run_id"])
            or str(run.get("automation_level") or "conservative") != "ai_kp"
        ):
            return {
                "status": "needs_attention",
                "stage": "parallel_commit",
                "batch_id": batch_id,
                "message": (
                    "Automatic parallel commit requires the same active run in "
                    "Full AI KP mode."
                ),
            }
        expected_version = payload.get("expected_version")
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("parallel commit jobs require expected_version")
        result = workflow.commit(batch_id, expected_version=expected_version)
        if result.status != "settled":
            recovery = start_full_ai_reconfirmation(
                repo,
                batch_id=batch_id,
                result=result.as_dict(),
                identity_resolver=kp_identity_for_parallel_actions,
            )
            if recovery is not None:
                return recovery
            return {
                **result.as_dict(),
                "status": "needs_attention",
                "stage": "parallel_commit",
            }
        return {
            "status": "settled",
            "stage": "parallel_commit",
            **result.as_dict(),
        }

    raise ValueError(f"Unsupported parallel_actions phase: {phase}")


def _complete_regather(
    repo: Repository,
    payload: dict[str, Any],
    *,
    resulting_batch_id: str | None,
) -> None:
    regather_id = str(payload.get("regather_id") or "")
    if not regather_id:
        return
    repo.complete_parallel_action_regather(
        regather_id,
        resulting_batch_id=resulting_batch_id,
    )


def _resolve_parallel_blind_checks(
    repo: Repository,
    job: dict,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Digitally resolve one exact, restart-safe set of Full-AI blind checks."""

    batch_id = str(payload.get("batch_id") or "")
    check_ids = tuple(str(item) for item in payload.get("check_ids") or ())
    action_ids = tuple(str(item) for item in payload.get("action_ids") or ())
    if (
        not batch_id
        or not check_ids
        or len(set(check_ids)) != len(check_ids)
        or not action_ids
        or len(set(action_ids)) != len(action_ids)
    ):
        raise ValueError(
            "parallel blind-check jobs require exact batch, action, and check IDs"
        )
    batch = repo.get_parallel_action_batch(batch_id)
    campaign_id = str(job["campaign_id"])
    if (
        str(job.get("resource_id") or "") != batch_id
        or str(job.get("run_id") or "") != str(batch["run_id"])
        or str(batch["campaign_id"]) != campaign_id
        or str(payload.get("session_id") or "") != str(batch["session_id"])
        or payload.get("run_version") != int(batch["module_run_version"])
        or str(payload.get("preparation_hash") or "")
        != str(batch["preparation_hash"])
        or action_ids != tuple(str(item["action_id"]) for item in batch["items"])
    ):
        raise PermissionError(
            "Parallel blind-check job is outside its durable batch authority"
        )
    exact_checks = _exact_parallel_blind_checks(batch)
    if check_ids != tuple(str(check["id"]) for check in exact_checks):
        raise ValueError("Parallel blind-check job check IDs no longer match the batch")
    if batch["status"] == "settled":
        repo.validate_parallel_action_batch_settlement(batch_id)
        return {
            "status": "succeeded",
            "stage": "parallel_blind_checks",
            "batch_id": batch_id,
            "batch_status": "settled",
            "resolved_check_ids": [],
            "replayed_check_ids": list(check_ids),
            "commit_job_id": None,
            "message": "Parallel batch was already settled.",
        }
    if batch["status"] not in {"awaiting_checks", "ready"}:
        return {
            "status": "needs_attention",
            "stage": "parallel_blind_checks",
            "batch_id": batch_id,
            "message": (
                "Blind checks cannot resolve while the parallel batch is "
                f"{batch['status']}."
            ),
        }
    run = repo.get_active_campaign_module_run(campaign_id)
    if (
        run is None
        or str(run["id"]) != str(batch["run_id"])
        or int(run["version"]) != int(batch["module_run_version"])
        or run.get("director_control_mode") != "ai_assist"
        or str(run.get("automation_level") or "conservative") != "ai_kp"
    ):
        return {
            "status": "needs_attention",
            "stage": "parallel_blind_checks",
            "batch_id": batch_id,
            "message": (
                "Automatic blind checks require the same active run in Full AI KP mode."
            ),
        }

    workflow = ParallelActionWorkflowService(repo)
    identity = kp_identity_for_parallel_actions(repo, action_ids)
    resolved_check_ids: list[str] = []
    replayed_check_ids: list[str] = []
    last_result = None
    for check_id in check_ids:
        check = repo.get_skill_check(check_id)
        if check["status"] == "requested":
            CheckService(repo).resolve(
                check_id,
                identity,
                ResolveCheckCommand(input_method="digital"),
            )
            # The random evidence is the durable authority for a blind roll.
            # Checkpoint it before observing the batch barrier so a worker
            # crash or a later check failure cannot silently roll again.
            repo.connection.commit()
            resolved_check_ids.append(check_id)
        elif check["status"] in {"resolved", "overridden"}:
            replayed_check_ids.append(check_id)
        else:
            return {
                "status": "needs_attention",
                "stage": "parallel_blind_checks",
                "batch_id": batch_id,
                "message": f"Blind check {check_id} is {check['status']}.",
            }
        last_result = workflow.observe_terminal_check(check_id)
        # Observation is restart-safe and may advance the batch to ``ready``.
        # Persist each completed rendezvous independently; the job record is
        # intentionally left running until the outer worker boundary finishes.
        repo.connection.commit()
        if last_result is not None and last_result.status == "needs_attention":
            return {
                "status": "needs_attention",
                "stage": "parallel_blind_checks",
                "batch_id": batch_id,
                "message": last_result.message,
            }

    current = repo.get_parallel_action_batch(batch_id)
    commit_job = (
        AutoKpQueueService(repo).enqueue_parallel_commit(batch_id)
        if current["status"] == "ready"
        else None
    )
    return {
        "status": "succeeded",
        "stage": "parallel_blind_checks",
        "batch_id": batch_id,
        "batch_status": current["status"],
        "resolved_check_ids": resolved_check_ids,
        "replayed_check_ids": replayed_check_ids,
        "commit_job_id": str(commit_job["id"]) if commit_job is not None else None,
        "message": (
            "Every blind check was resolved; the parallel batch is ready."
            if current["status"] == "ready"
            else "Blind checks are resolved; visible player rolls remain pending."
        ),
    }


def _exact_parallel_blind_checks(batch: dict[str, Any]) -> tuple[dict, ...]:
    checks: list[dict] = []
    for item in batch["items"]:
        for check in item.get("checks") or ():
            visibility = str(
                check.get("visibility")
                or ("blind" if check.get("hidden") else "public")
            )
            if visibility != "blind":
                continue
            if (
                check.get("allow_push") is not False
                or str(check.get("campaign_id") or "") != str(batch["campaign_id"])
                or str(check.get("session_id") or "") != str(batch["session_id"])
                or str(check.get("player_action_id") or "")
                != str(item["action_id"])
                or str(check.get("proposal_id") or "") != str(item["proposal_id"])
            ):
                raise ValueError(
                    "Parallel blind check is outside its item authority"
                )
            checks.append(check)
    if not checks or len({str(check["id"]) for check in checks}) != len(checks):
        raise ValueError("Parallel blind-check authority requires distinct checks")
    return tuple(checks)


def kp_identity_for_parallel_actions(
    repo: Repository,
    action_ids: tuple[str, ...],
) -> AuthenticatedMember:
    """Resolve the system KP identity shared by one authoritative action cohort."""

    if not action_ids:
        raise ValueError("parallel_actions jobs require action_ids")
    action = repo.get_player_action(action_ids[0])
    row = repo.connection.execute(
        """
        SELECT id, session_id, campaign_id, display_name
        FROM session_members
        WHERE campaign_id = ? AND session_id = ? AND role = 'kp'
          AND revoked_at IS NULL
        ORDER BY joined_at, id
        LIMIT 1
        """,
        (str(action["campaign_id"]), str(action["session_id"])),
    ).fetchone()
    if row is None:
        raise ValueError("No active KP member exists for Auto KP job session")
    return AuthenticatedMember(
        member_id=str(row["id"]),
        session_id=str(row["session_id"]),
        campaign_id=str(row["campaign_id"]),
        role="kp",
        display_name=f"{row['display_name']} · Auto KP Worker",
        pc_id=None,
    )


def _semantic_profile(repo: Repository) -> str:
    configuration = repo.get_model_configuration()
    return (
        "large"
        if configuration and configuration.get("semantic_profile") == "large"
        else "small"
    )


__all__ = [
    "execute_parallel_action_job",
    "kp_identity_for_parallel_actions",
]

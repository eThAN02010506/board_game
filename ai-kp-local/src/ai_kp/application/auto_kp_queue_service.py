"""Durable scheduling for player-facing Auto KP progression."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Protocol

from ai_kp.application.parallel_action_collection import (
    ACTION_COLLECTION_SECONDS,
    ParallelActionCollectionExpansion,
    ParallelActionCollectionPlanner,
)
from ai_kp.platform.resolution import build_check_consequence_snapshot

PARALLEL_FALLBACK_ROUTES = frozenset(
    {
        "conversation",
        "information",
        "roleplay",
        "clarification",
        "world_expansion",
        "task_method",
        "unavailable_operator",
    }
)


class AutoKpQueueStore(Protocol):
    def begin_immediate(self) -> None: ...

    def get_player_action(self, action_id: str) -> dict: ...

    def get_skill_check(self, check_id: str) -> dict: ...

    def get_coc7_encounter(self, encounter_id: str) -> dict: ...

    def get_current_session_zero_revision(self, campaign_id: str) -> dict | None: ...

    def get_active_session_safety_event(self, session_id: str) -> dict | None: ...

    def get_latest_session_safety_event(self, session_id: str) -> dict | None: ...

    def get_current_campaign_episode(self, session_id: str) -> dict | None: ...

    def list_skill_checks_for_action(self, player_action_id: str) -> list[dict]: ...

    def list_opposed_checks_for_action(self, player_action_id: str) -> list[dict]: ...

    def get_active_campaign_module_run(self, campaign_id: str) -> dict | None: ...

    def get_parallel_action_batch(self, batch_id: str) -> dict: ...

    def get_parallel_action_regather(self, regather_id: str) -> dict: ...

    def mark_parallel_action_regather_queued(
        self,
        regather_id: str,
        *,
        expected_version: int,
        prepare_job_id: str,
    ) -> dict: ...

    def get_parallel_action_batch_for_action(
        self, action_id: str
    ) -> dict | None: ...

    def list_player_actions(
        self, campaign_id: str, session_id: str, *, status: str | None = None
    ) -> list[dict]: ...

    def list_auto_kp_jobs(
        self, campaign_id: str, *, status: str | None = None, limit: int = 50
    ) -> list[dict]: ...

    def get_auto_kp_job(self, job_id: str) -> dict: ...

    def cancel_auto_kp_job(self, job_id: str) -> dict: ...

    def expand_queued_parallel_prepare_job(
        self,
        job_id: str,
        *,
        expected_payload: Mapping[str, object],
        expanded_payload: Mapping[str, object],
    ) -> dict | None: ...

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
        self._collection_planner = ParallelActionCollectionPlanner()

    def enqueue_player_action(self, action_id: str) -> dict:
        # Collection membership and worker claim are one write-serialized
        # decision.  A prepare job may be expanded only while it is still an
        # unclaimed queue reservation; once a worker claims it, its action set
        # is immutable.
        self.repo.begin_immediate()
        action = self.repo.get_player_action(action_id)
        campaign_id = str(action["campaign_id"])
        session_id = str(action["session_id"])
        run = self.repo.get_active_campaign_module_run(campaign_id)
        jobs = self.repo.list_auto_kp_jobs(campaign_id, limit=100)
        reserved = self._collection_planner.reservations(
            jobs, session_id=session_id
        )
        existing = reserved.get(action_id)
        if existing is not None:
            return existing
        eligible = self._collection_planner.eligible_action_ids(
            jobs, session_id=session_id
        )
        submitted = self.repo.list_player_actions(
            campaign_id,
            session_id,
            status="submitted",
        )
        expansion = self._collection_planner.expansion_plan(
            action,
            submitted,
            run=run,
            jobs=jobs,
            reservations=reserved,
            eligible_action_ids=eligible,
        )
        if expansion is not None:
            expanded = self._apply_collection_expansion(
                expansion,
                current=action,
                jobs=jobs,
            )
            if expanded is not None:
                return expanded
        collection = self._collection_planner.initial_plan(
            action,
            submitted,
            reserved_action_ids=frozenset(reserved),
            eligible_action_ids=eligible,
        )
        collected = collection.actions if collection is not None else (action,)
        action_ids = [str(item["id"]) for item in collected]
        by_member = {str(item["member_id"]): item for item in collected}
        if len(by_member) > 1:
            assert collection is not None
            self._cancel_superseded_jobs(
                campaign_id,
                session_id,
                set(action_ids),
                jobs=jobs,
            )
            run_id = str(run["id"]) if run is not None else "no-run"
            run_version = int(run["version"]) if run is not None else 0
            fingerprint = self._fingerprint(
                "prepare",
                campaign_id,
                session_id,
                run_id,
                str(run_version),
                *action_ids,
            )
            return self.repo.enqueue_auto_kp_job(
                campaign_id=campaign_id,
                run_id=str(run["id"]) if run is not None else None,
                job_type="parallel_actions",
                resource_id=action_id,
                idempotency_key=f"parallel-prepare:{fingerprint}",
                payload={
                    "phase": "prepare",
                    "action_ids": action_ids,
                    "session_id": session_id,
                    "run_version": run_version,
                    "collection_anchor_action_id": collection.anchor_action_id,
                    "collection_anchor_at": collection.anchor_at.isoformat(),
                },
                delay_seconds=self._collection_planner.remaining_delay_seconds(
                    collection.anchor_at
                ),
            )
        # Repeated submissions from one seat are sequential player revisions,
        # never multiplayer concurrency. Queue this exact action on its own;
        # older same-seat jobs retain their normal cancellation/supersede path.
        return self.repo.enqueue_auto_kp_job(
            campaign_id=campaign_id,
            run_id=str(run["id"]) if run is not None else None,
            job_type="player_action",
            resource_id=str(action["id"]),
            idempotency_key=f"player-action:{action['id']}",
            payload={"action_id": str(action["id"]), "session_id": session_id},
            delay_seconds=ACTION_COLLECTION_SECONDS,
        )

    def enqueue_encounter_turn(self, encounter_id: str) -> dict | None:
        """Schedule exactly the current AI-KP encounter turn, if automation owns it."""

        self.repo.begin_immediate()
        encounter = self.repo.get_coc7_encounter(encounter_id)
        if encounter["status"] != "active":
            return None
        episode = self.repo.get_current_campaign_episode(str(encounter["session_id"]))
        if episode is None or episode.get("status") != "in_progress":
            return None
        revision = self.repo.get_current_session_zero_revision(
            str(encounter["campaign_id"])
        )
        if revision is None or revision.get("status") != "active":
            return None
        if self.repo.get_active_session_safety_event(str(encounter["session_id"])):
            return None
        config = dict((revision or {}).get("config") or {})
        if config.get("hosting_mode", "ai_kp") != "ai_kp":
            return None
        active_id = str(
            encounter["state"]["turn_order"][int(encounter["turn_index"])]
        )
        participant = next(
            item
            for item in encounter["state"]["participants"]
            if str(item["participant_id"]) == active_id
        )
        phase = "idle_player" if participant.get("investigator_id") else "enemy"
        policy = str(config.get("idle_policy") or "wait") if phase == "idle_player" else "agent"
        if phase == "idle_player" and policy == "wait":
            return None
        delay = int(config.get("idle_timeout_seconds") or 300) if phase == "idle_player" else 0
        if not 30 <= delay <= 3600 and phase == "idle_player":
            raise ValueError("Session idle timeout is outside the supported range")
        run = self.repo.get_active_campaign_module_run(str(encounter["campaign_id"]))
        run_id = str(run["id"]) if run is not None else None
        run_version = int(run["version"]) if run is not None else 0
        version = int(encounter["version"])
        latest_safety = self.repo.get_latest_session_safety_event(
            str(encounter["session_id"])
        )
        safety_epoch = str((latest_safety or {}).get("id") or "initial")
        episode_epoch = f"{episode['id']}:{episode['version']}"
        return self.repo.enqueue_auto_kp_job(
            campaign_id=str(encounter["campaign_id"]),
            run_id=run_id,
            job_type="encounter_turn",
            resource_id=encounter_id,
            idempotency_key=(
                f"encounter-turn:{encounter_id}:{version}:{active_id}:{phase}:"
                f"{safety_epoch}:{episode_epoch}"
            ),
            payload={
                "encounter_version": version,
                "participant_id": active_id,
                "phase": phase,
                "policy": policy,
                "session_id": str(encounter["session_id"]),
                "run_version": run_version,
                "episode_id": str(episode["id"]),
                "episode_version": int(episode["version"]),
            },
            delay_seconds=delay,
        )

    def enqueue_parallel_regather(self, regather_id: str) -> dict | None:
        """Queue an exact durable cohort without reopening the timing window."""

        self.repo.begin_immediate()
        regather = self.repo.get_parallel_action_regather(regather_id)
        if regather["status"] == "queued":
            prepare_job_id = regather.get("prepare_job_id")
            return (
                self.repo.get_auto_kp_job(str(prepare_job_id))
                if prepare_job_id
                else None
            )
        if regather["status"] != "gathering" or not regather["ready"]:
            return None
        action_ids = tuple(
            str(item["replacement_action_id"])
            for item in regather["members"]
        )
        if not 2 <= len(action_ids) <= 12 or len(set(action_ids)) != len(action_ids):
            raise ValueError("Regather requires 2-12 distinct replacement actions")
        actions = tuple(self.repo.get_player_action(action_id) for action_id in action_ids)
        if any(
            str(action["campaign_id"]) != str(regather["campaign_id"])
            or str(action["session_id"]) != str(regather["session_id"])
            or str(action["status"]) != "submitted"
            for action in actions
        ):
            raise PermissionError("Regather replacement actions lost queue authority")
        run = self.repo.get_active_campaign_module_run(str(regather["campaign_id"]))
        run_id = str(run["id"]) if run is not None else None
        run_version = int(run["version"]) if run is not None else 0
        fingerprint = self._fingerprint(
            "regather",
            regather_id,
            run_id or "no-run",
            str(run_version),
            *action_ids,
        )
        job = self.repo.enqueue_auto_kp_job(
            campaign_id=str(regather["campaign_id"]),
            run_id=run_id,
            job_type="parallel_actions",
            resource_id=action_ids[0],
            idempotency_key=f"parallel-regather:{fingerprint}",
            payload={
                "phase": "prepare",
                "action_ids": list(action_ids),
                "session_id": str(regather["session_id"]),
                "run_version": run_version,
                "regather_id": regather_id,
            },
        )
        self.repo.mark_parallel_action_regather_queued(
            regather_id,
            expected_version=int(regather["version"]),
            prepare_job_id=str(job["id"]),
        )
        return job

    def _cancel_superseded_jobs(
        self,
        campaign_id: str,
        session_id: str,
        action_ids: set[str],
        *,
        jobs: Sequence[Mapping[str, object]] | None = None,
        keep_job_id: str | None = None,
    ) -> None:
        candidates = jobs or self.repo.list_auto_kp_jobs(campaign_id, limit=100)
        for job in candidates:
            if str(job.get("id") or "") == keep_job_id:
                continue
            if job["status"] not in {"queued", "retry_wait"}:
                continue
            payload = job.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            collected_ids = payload.get("action_ids")
            collected_ids = (
                {str(item) for item in collected_ids}
                if isinstance(collected_ids, list)
                else set()
            )
            superseded_action = (
                job["job_type"] == "player_action"
                and job["resource_id"] in action_ids
            )
            superseded_batch = (
                job["job_type"] == "parallel_actions"
                and payload.get("session_id") == session_id
                and payload.get("phase", "prepare") == "prepare"
                and bool(collected_ids & action_ids)
            )
            if superseded_action or superseded_batch:
                self.repo.cancel_auto_kp_job(str(job["id"]))

    def _apply_collection_expansion(
        self,
        expansion: ParallelActionCollectionExpansion,
        *,
        current: Mapping[str, object],
        jobs: Sequence[Mapping[str, object]],
    ) -> dict | None:
        """Persist one pure expansion plan and cancel superseded single jobs."""

        job_id = str(expansion.job["id"])
        expanded_job = self.repo.expand_queued_parallel_prepare_job(
            job_id,
            expected_payload=expansion.expected_payload,
            expanded_payload=expansion.expanded_payload,
        )
        if expanded_job is None:
            return None
        self._cancel_superseded_jobs(
            str(current["campaign_id"]),
            str(current["session_id"]),
            set(expansion.newly_collected_action_ids),
            jobs=jobs,
            keep_job_id=job_id,
        )
        return expanded_job

    def enqueue_check_consequence(self, check_id: str) -> dict | None:
        check = self.repo.get_skill_check(check_id)
        action_id = check.get("player_action_id")
        if not action_id:
            return None
        action_id = str(action_id)
        if self.repo.get_parallel_action_batch_for_action(action_id) is not None:
            # Parallel checks rendezvous in the deterministic batch workflow;
            # a per-action consequence would commit one player ahead of the table.
            return None
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

    def enqueue_parallel_commit(self, batch_id: str) -> dict | None:
        """Queue one Full-AI atomic commit after every player-owned decision."""

        batch = self.repo.get_parallel_action_batch(batch_id)
        if batch["status"] != "ready":
            return None
        campaign_id = str(batch["campaign_id"])
        run = self.repo.get_active_campaign_module_run(campaign_id)
        if (
            run is None
            or str(run["id"]) != str(batch["run_id"])
            or str(run.get("automation_level") or "conservative") != "ai_kp"
        ):
            return None
        fingerprint = self._fingerprint(
            "commit",
            batch_id,
            str(batch["preparation_hash"]),
            str(batch["version"]),
        )
        return self.repo.enqueue_auto_kp_job(
            campaign_id=campaign_id,
            run_id=str(batch["run_id"]),
            job_type="parallel_actions",
            resource_id=batch_id,
            idempotency_key=f"parallel-commit:{fingerprint}",
            payload={
                "phase": "commit",
                "batch_id": batch_id,
                "expected_version": int(batch["version"]),
            },
        )

    def enqueue_parallel_blind_check_resolution(
        self,
        batch_id: str,
    ) -> dict | None:
        """Queue Full-AI digital resolution for an exact set of blind checks."""

        batch = self.repo.get_parallel_action_batch(batch_id)
        if batch["status"] != "awaiting_checks":
            return None
        campaign_id = str(batch["campaign_id"])
        run = self.repo.get_active_campaign_module_run(campaign_id)
        if (
            run is None
            or str(run["id"]) != str(batch["run_id"])
            or int(run["version"]) != int(batch["module_run_version"])
            or run.get("director_control_mode") != "ai_assist"
            or str(run.get("automation_level") or "conservative") != "ai_kp"
        ):
            return None
        blind_check_ids = self._blind_check_ids(batch)
        if not blind_check_ids:
            return None
        action_ids = [str(item["action_id"]) for item in batch["items"]]
        fingerprint = self._fingerprint(
            "resolve-blind-checks",
            batch_id,
            str(batch["preparation_hash"]),
            str(batch["version"]),
            *blind_check_ids,
        )
        return self.repo.enqueue_auto_kp_job(
            campaign_id=campaign_id,
            run_id=str(batch["run_id"]),
            job_type="parallel_actions",
            resource_id=batch_id,
            idempotency_key=f"parallel-blind-checks:{fingerprint}",
            payload={
                "phase": "resolve_blind_checks",
                "batch_id": batch_id,
                "session_id": str(batch["session_id"]),
                "run_version": int(batch["module_run_version"]),
                "preparation_hash": str(batch["preparation_hash"]),
                "action_ids": action_ids,
                "check_ids": blind_check_ids,
            },
            delay_seconds=0,
        )

    def enqueue_parallel_followup(self, batch_id: str) -> dict | None:
        """Queue the next restart-safe Full-AI phase for one durable batch.

        Callers should not need to know whether the remaining barrier is a
        hidden system roll or the final atomic commit.  Keeping that decision
        here also makes mixed visible/blind check batches converge after any
        player-owned check changes state.
        """

        batch = self.repo.get_parallel_action_batch(batch_id)
        if batch["status"] == "awaiting_checks":
            return self.enqueue_parallel_blind_check_resolution(batch_id)
        if batch["status"] == "ready":
            return self.enqueue_parallel_commit(batch_id)
        return None

    def enqueue_parallel_action_fallbacks(
        self,
        parent_job: Mapping[str, object],
        *,
        fallback_routes: Mapping[str, str],
    ) -> tuple[dict, ...]:
        """Atomically fan a non-mechanical parallel plan into single-action jobs.

        This intentionally calls the repository primitive instead of
        :meth:`enqueue_player_action`: a fallback action must never re-enter the
        multiplayer collection window that just rejected it.
        """

        payload = parent_job.get("payload")
        if not isinstance(payload, dict):
            raise TypeError("Parallel fallback parent payload is malformed")
        action_ids = tuple(str(item) for item in payload.get("action_ids") or ())
        if (
            parent_job.get("job_type") != "parallel_actions"
            or parent_job.get("status") != "running"
            or str(payload.get("phase") or "prepare") != "prepare"
            or not 2 <= len(action_ids) <= 12
            or len(set(action_ids)) != len(action_ids)
            or str(parent_job.get("resource_id") or "") not in action_ids
        ):
            raise ValueError("Parallel fallback requires one claimed prepare job")
        normalized_routes = {
            str(action_id): str(route)
            for action_id, route in fallback_routes.items()
        }
        if (
            not normalized_routes
            or not set(normalized_routes).issubset(action_ids)
            or any(
                route not in PARALLEL_FALLBACK_ROUTES
                for route in normalized_routes.values()
            )
        ):
            raise ValueError(
                "Parallel fallback requires only supported non-mechanical routes"
            )

        campaign_id = str(parent_job.get("campaign_id") or "")
        session_id = str(payload.get("session_id") or "")
        parent_run_id = str(parent_job.get("run_id") or "")
        parent_run_version = payload.get("run_version")
        parent_job_id = str(parent_job.get("id") or "")
        parent_key = str(parent_job.get("idempotency_key") or "")
        if (
            not campaign_id
            or not session_id
            or not parent_run_id
            or type(parent_run_version) is not int
            or not parent_job_id
            or not parent_key
        ):
            raise ValueError("Parallel fallback parent authority is incomplete")

        # Planning has finished before this short write boundary.  Revalidate the
        # run and every submitted action while holding the lock, then create all
        # children in the parent's completion transaction.
        self.repo.begin_immediate()
        durable_parent = self.repo.get_auto_kp_job(parent_job_id)
        if (
            durable_parent.get("status") != "running"
            or durable_parent.get("job_type") != "parallel_actions"
            or str(durable_parent.get("campaign_id") or "") != campaign_id
            or str(durable_parent.get("run_id") or "") != parent_run_id
            or str(durable_parent.get("resource_id") or "")
            != str(parent_job.get("resource_id") or "")
            or str(durable_parent.get("idempotency_key") or "") != parent_key
            or durable_parent.get("payload") != payload
        ):
            raise ValueError("Parallel fallback parent job authority changed")
        run = self.repo.get_active_campaign_module_run(campaign_id)
        if (
            run is None
            or str(run["id"]) != parent_run_id
            or int(run["version"]) != parent_run_version
            or run.get("director_control_mode") != "ai_assist"
            or str(run.get("automation_level") or "conservative")
            not in {"balanced", "ai_kp"}
        ):
            return ()

        actions = tuple(self.repo.get_player_action(action_id) for action_id in action_ids)
        if any(
            str(action.get("campaign_id") or "") != campaign_id
            or str(action.get("session_id") or "") != session_id
            or action.get("status") != "submitted"
            or action.get("proposal_id") is not None
            for action in actions
        ):
            return ()

        children: list[dict] = []
        for index, action in enumerate(actions):
            action_id = str(action["id"])
            expected_payload = {
                "action_id": action_id,
                "session_id": session_id,
                "run_id": parent_run_id,
                "run_version": parent_run_version,
                "parent_job_id": parent_job_id,
                "parent_idempotency_key": parent_key,
                "fallback_kind": "parallel_nonmechanical",
                "fallback_index": index,
                "fallback_route": normalized_routes.get(
                    action_id, "mechanical_peer"
                ),
            }
            child = self.repo.enqueue_auto_kp_job(
                campaign_id=campaign_id,
                run_id=parent_run_id,
                job_type="player_action",
                resource_id=action_id,
                idempotency_key=f"parallel-fallback:{parent_job_id}:{action_id}",
                payload=expected_payload,
                delay_seconds=0,
            )
            if (
                child.get("job_type") != "player_action"
                or str(child.get("campaign_id") or "") != campaign_id
                or str(child.get("run_id") or "") != parent_run_id
                or str(child.get("resource_id") or "") != action_id
                or child.get("payload") != expected_payload
            ):
                raise ValueError(
                    "Parallel fallback idempotency key has conflicting authority"
                )
            children.append(child)
        return tuple(children)

    @staticmethod
    def _fingerprint(*parts: str) -> str:
        return sha256("\n".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _blind_check_ids(batch: Mapping[str, object]) -> list[str]:
        items = batch.get("items")
        if not isinstance(items, list):
            raise TypeError("Parallel batch items are malformed")
        check_ids: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                raise TypeError("Parallel batch item is malformed")
            for check in item.get("checks") or ():
                if not isinstance(check, dict):
                    raise TypeError("Parallel batch check is malformed")
                visibility = str(
                    check.get("visibility")
                    or ("blind" if check.get("hidden") else "public")
                )
                if visibility != "blind":
                    continue
                if check.get("allow_push") is not False:
                    raise ValueError("Blind parallel checks must disable pushing")
                check_id = str(check.get("id") or "")
                if not check_id or check_id in check_ids:
                    raise ValueError("Parallel blind check IDs must be distinct")
                check_ids.append(check_id)
        return check_ids


__all__ = [
    "ACTION_COLLECTION_SECONDS",
    "PARALLEL_FALLBACK_ROUTES",
    "AutoKpQueueService",
]

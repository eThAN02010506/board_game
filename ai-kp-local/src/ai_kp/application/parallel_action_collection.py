"""Pure cohort planning for simultaneous Auto KP player actions.

This module decides collection membership from immutable action, job, and run
snapshots.  It deliberately performs no repository reads or writes; the queue
service owns the transaction, CAS update, cancellation, and enqueue boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil

ACTION_COLLECTION_SECONDS = 2
MAX_PARALLEL_ACTIONS = 12


@dataclass(frozen=True)
class ParallelActionCollectionPlan:
    """One fixed-anchor action cohort selected from queue snapshots."""

    actions: tuple[Mapping[str, object], ...]
    anchor_action_id: str
    anchor_at: datetime

    @property
    def action_ids(self) -> tuple[str, ...]:
        return tuple(str(action["id"]) for action in self.actions)


@dataclass(frozen=True)
class ParallelActionCollectionExpansion:
    """A compare-and-swap payload update for one unclaimed prepare job."""

    job: Mapping[str, object]
    expected_payload: Mapping[str, object]
    expanded_payload: Mapping[str, object]
    newly_collected_action_ids: frozenset[str]


class ParallelActionCollectionPlanner:
    """Plan fixed-window cohorts without mutating queue or action state."""

    @staticmethod
    def reservations(
        jobs: Sequence[Mapping[str, object]],
        *,
        session_id: str,
    ) -> dict[str, Mapping[str, object]]:
        """Map every action already owned by an in-flight collection job."""

        reservations: dict[str, Mapping[str, object]] = {}
        for job in jobs:
            if job.get("status") not in {"queued", "running", "retry_wait"}:
                continue
            payload = job.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            if payload.get("session_id") != session_id:
                continue
            if (
                job.get("job_type") == "parallel_actions"
                and payload.get("phase", "prepare") == "prepare"
            ):
                action_ids = payload.get("action_ids")
                if not isinstance(action_ids, list):
                    continue
                for action_id in action_ids:
                    reservations[str(action_id)] = job
                continue
            if job.get("job_type") != "player_action":
                continue
            # Queued ordinary actions can still join a cohort. Claimed actions
            # and explicit fallback children have already crossed that boundary.
            if job.get("status") == "running" or payload.get("fallback_kind"):
                action_id = str(job.get("resource_id") or "")
                if action_id:
                    reservations[action_id] = job
        return reservations

    @staticmethod
    def eligible_action_ids(
        jobs: Sequence[Mapping[str, object]],
        *,
        session_id: str,
    ) -> frozenset[str]:
        """Return actions whose owners explicitly requested queued Auto KP."""

        return frozenset(
            str(job.get("resource_id"))
            for job in jobs
            if job.get("job_type") == "player_action"
            and job.get("status") in {"queued", "retry_wait"}
            and isinstance(job.get("payload"), dict)
            and job["payload"].get("session_id") == session_id
            and not job["payload"].get("fallback_kind")
            and job.get("resource_id")
        )

    def initial_plan(
        self,
        current: Mapping[str, object],
        submitted: Sequence[Mapping[str, object]],
        *,
        reserved_action_ids: frozenset[str],
        eligible_action_ids: frozenset[str],
    ) -> ParallelActionCollectionPlan | None:
        """Plan a new cohort around the earliest eligible fixed anchor."""

        actions = self._collection_window(
            current,
            submitted,
            reserved_action_ids=reserved_action_ids,
            eligible_action_ids=eligible_action_ids,
        )
        if not actions:
            return None
        anchor_at = self._created_at(actions[0])
        if anchor_at is None:
            return None
        return ParallelActionCollectionPlan(
            actions=tuple(actions),
            anchor_action_id=str(actions[0]["id"]),
            anchor_at=anchor_at,
        )

    def expansion_plan(
        self,
        current: Mapping[str, object],
        submitted: Sequence[Mapping[str, object]],
        *,
        run: Mapping[str, object] | None,
        jobs: Sequence[Mapping[str, object]],
        reservations: Mapping[str, Mapping[str, object]],
        eligible_action_ids: frozenset[str],
    ) -> ParallelActionCollectionExpansion | None:
        """Plan an in-place expansion of one exact unclaimed prepare job."""

        current_time = self._created_at(current)
        if current_time is None:
            return None
        submitted_by_id = {
            str(candidate.get("id") or ""): candidate for candidate in submitted
        }
        expandable: list[
            tuple[
                datetime,
                str,
                Mapping[str, object],
                list[Mapping[str, object]],
            ]
        ] = []
        for job in jobs:
            if not self._is_unclaimed_prepare_job(job):
                continue
            payload = job.get("payload")
            assert isinstance(payload, dict)  # narrowed by the predicate above
            if not self._matches_authority(current, run, job, payload):
                continue
            action_ids = payload.get("action_ids")
            assert isinstance(action_ids, list)
            if str(job.get("resource_id") or "") not in {
                str(item) for item in action_ids
            }:
                continue
            base = [submitted_by_id.get(str(item)) for item in action_ids]
            if any(candidate is None for candidate in base):
                continue
            base_actions = [candidate for candidate in base if candidate is not None]
            anchor = self._collection_anchor(payload, base_actions)
            if anchor is None:
                continue
            anchor_at, anchor_action_id = anchor
            delta = (current_time - anchor_at).total_seconds()
            member_ids = {
                str(candidate.get("member_id") or "") for candidate in base_actions
            }
            if (
                not 0 <= delta <= ACTION_COLLECTION_SECONDS
                or not member_ids
                or "" in member_ids
                or len(member_ids) != len(base_actions)
                or str(current.get("member_id") or "") in member_ids
                or len(base_actions) >= MAX_PARALLEL_ACTIONS
            ):
                continue
            expandable.append((anchor_at, anchor_action_id, job, base_actions))

        if not expandable:
            return None
        anchor_at, anchor_action_id, job, base_actions = min(
            expandable,
            key=lambda candidate: (candidate[0], candidate[1], str(candidate[2]["id"])),
        )
        payload = job.get("payload")
        assert isinstance(payload, dict)
        collected = self._collection_window(
            current,
            submitted,
            reserved_action_ids=frozenset(
                action_id
                for action_id, owner in reservations.items()
                if str(owner.get("id") or "") != str(job["id"])
            ),
            eligible_action_ids=eligible_action_ids,
            anchor_time=anchor_at,
            required_actions=base_actions,
        )
        collected_ids = tuple(str(candidate["id"]) for candidate in collected)
        base_ids = frozenset(str(candidate["id"]) for candidate in base_actions)
        if (
            str(current["id"]) not in collected_ids
            or len(collected_ids) <= len(base_ids)
        ):
            return None
        return ParallelActionCollectionExpansion(
            job=job,
            expected_payload=dict(payload),
            expanded_payload={
                **payload,
                "action_ids": list(collected_ids),
                "collection_anchor_action_id": anchor_action_id,
                "collection_anchor_at": anchor_at.isoformat(),
            },
            newly_collected_action_ids=frozenset(collected_ids) - base_ids,
        )

    @staticmethod
    def remaining_delay_seconds(anchor_at: datetime) -> int:
        """Keep the prepare deadline tied to the first action, not the last join."""

        deadline = anchor_at + timedelta(seconds=ACTION_COLLECTION_SECONDS)
        remaining = ceil((deadline - datetime.now(UTC)).total_seconds())
        return max(0, min(ACTION_COLLECTION_SECONDS, remaining))

    @staticmethod
    def _matches_authority(
        current: Mapping[str, object],
        run: Mapping[str, object] | None,
        job: Mapping[str, object],
        payload: Mapping[str, object],
    ) -> bool:
        expected_run_id = str(run["id"]) if run is not None else None
        expected_run_version = int(run["version"]) if run is not None else 0
        return (
            str(job.get("campaign_id") or "")
            == str(current.get("campaign_id") or "")
            and job.get("run_id") == expected_run_id
            and payload.get("session_id") == current.get("session_id")
            and payload.get("run_version") == expected_run_version
        )

    @staticmethod
    def _is_unclaimed_prepare_job(job: Mapping[str, object]) -> bool:
        payload = job.get("payload")
        return (
            job.get("job_type") == "parallel_actions"
            and job.get("status") == "queued"
            and int(job.get("attempt_count") or 0) == 0
            and not job.get("locked_by")
            and isinstance(payload, dict)
            and payload.get("phase", "prepare") == "prepare"
            and isinstance(payload.get("action_ids"), list)
            and 2 <= len(payload["action_ids"]) < MAX_PARALLEL_ACTIONS
        )

    @classmethod
    def _collection_anchor(
        cls,
        payload: Mapping[str, object],
        actions: Sequence[Mapping[str, object]],
    ) -> tuple[datetime, str] | None:
        ordered = sorted(
            (
                (created_at, str(action.get("id") or ""))
                for action in actions
                if (created_at := cls._created_at(action)) is not None
                and str(action.get("id") or "")
            ),
            key=lambda candidate: (candidate[0], candidate[1]),
        )
        if len(ordered) != len(actions) or not ordered:
            return None
        actual_time, actual_id = ordered[0]
        stored_id = payload.get("collection_anchor_action_id")
        stored_at = payload.get("collection_anchor_at")
        if stored_id is None and stored_at is None:
            return actual_time, actual_id
        if not isinstance(stored_id, str) or not isinstance(stored_at, str):
            return None
        parsed = cls._created_at({"created_at": stored_at})
        anchor_action = next(
            (action for action in actions if str(action.get("id") or "") == stored_id),
            None,
        )
        if (
            parsed is None
            or anchor_action is None
            or cls._created_at(anchor_action) != parsed
            or any(created_at < parsed for created_at, _ in ordered)
        ):
            return None
        return parsed, stored_id

    @classmethod
    def _collection_window(
        cls,
        current: Mapping[str, object],
        submitted: Sequence[Mapping[str, object]],
        *,
        reserved_action_ids: frozenset[str],
        eligible_action_ids: frozenset[str],
        anchor_time: datetime | None = None,
        required_actions: Sequence[Mapping[str, object]] = (),
    ) -> list[Mapping[str, object]]:
        current_time = cls._created_at(current)
        if current_time is None:
            return [current]
        current_id = str(current["id"])
        current_member = str(current.get("member_id") or "")
        candidates: list[tuple[datetime, str, Mapping[str, object]]] = []
        for candidate in submitted:
            candidate_id = str(candidate.get("id") or "")
            member_id = str(candidate.get("member_id") or "")
            candidate_time = cls._created_at(candidate)
            if (
                candidate_id == current_id
                or candidate_id not in eligible_action_ids
                or candidate_id in reserved_action_ids
                or not member_id
                or member_id == current_member
                or candidate_time is None
            ):
                continue
            candidates.append((candidate_time, candidate_id, candidate))

        if anchor_time is None:
            neighboring = [
                item
                for item in candidates
                if abs((item[0] - current_time).total_seconds())
                <= ACTION_COLLECTION_SECONDS
            ]
            anchor_time = min(
                [
                    (current_time, current_id),
                    *[(item[0], item[1]) for item in neighboring],
                ],
                key=lambda item: (item[0], item[1]),
            )[0]

        deadline = anchor_time.timestamp() + ACTION_COLLECTION_SECONDS
        collected: list[Mapping[str, object]] = []
        seen_ids: set[str] = set()
        seen_members: set[str] = set()
        for candidate in [*required_actions, current]:
            candidate_id = str(candidate.get("id") or "")
            member_id = str(candidate.get("member_id") or "")
            candidate_time = cls._created_at(candidate)
            if (
                not candidate_id
                or candidate_id in seen_ids
                or not member_id
                or member_id in seen_members
                or candidate_time is None
                or candidate_time < anchor_time
                or candidate_time.timestamp() > deadline
                or len(collected) >= MAX_PARALLEL_ACTIONS
            ):
                continue
            collected.append(candidate)
            seen_ids.add(candidate_id)
            seen_members.add(member_id)

        for candidate_time, candidate_id, candidate in sorted(
            candidates, key=lambda item: (item[0], item[1])
        ):
            member_id = str(candidate.get("member_id") or "")
            if (
                candidate_id in seen_ids
                or member_id in seen_members
                or candidate_time < anchor_time
                or candidate_time.timestamp() > deadline
                or len(collected) >= MAX_PARALLEL_ACTIONS
            ):
                continue
            collected.append(candidate)
            seen_ids.add(candidate_id)
            seen_members.add(member_id)
        return sorted(
            collected,
            key=lambda item: (cls._created_at(item) or current_time, str(item["id"])),
        )

    @staticmethod
    def _created_at(action: Mapping[str, object]) -> datetime | None:
        value = action.get("created_at")
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


__all__ = [
    "ACTION_COLLECTION_SECONDS",
    "ParallelActionCollectionExpansion",
    "ParallelActionCollectionPlan",
    "ParallelActionCollectionPlanner",
]

"""Authority binding for phased parallel Auto KP jobs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class AutoKpJobScopeAuthority(Protocol):
    """Common campaign/run/session primitives owned by the central validator."""

    def _validate_active_session(
        self,
        campaign_id: str,
        session_id: str,
        *,
        expected_session_id: str | None,
    ) -> None: ...

    def _validate_current_run(
        self,
        campaign_id: str,
        run_id: str | None,
        *,
        required: bool,
        allow_terminal: bool = False,
    ) -> dict[str, Any] | None: ...

    def _validate_payload_run(
        self,
        payload: Mapping[str, Any],
        run_id: str | None,
        run: Mapping[str, Any] | None,
    ) -> None: ...

    def _match_optional_payload_text(
        self,
        payload: Mapping[str, Any],
        key: str,
        expected: str,
    ) -> None: ...

    def _require_same_campaign(
        self,
        expected: str,
        actual: Any,
        label: str,
    ) -> None: ...

    def _required_text(self, value: Any, field_name: str) -> str: ...

    def _optional_text(self, value: Any, field_name: str) -> str | None: ...


@dataclass(frozen=True)
class ParallelAutoKpJobScope:
    session_id: str
    run_id: str | None


class ParallelAutoKpJobAuthorityValidator:
    """Validate prepare, blind-check, and commit phase-specific authority."""

    def __init__(self, repo: Any, scope: AutoKpJobScopeAuthority):
        self.repo = repo
        self.scope = scope

    def validate(
        self,
        *,
        campaign_id: str,
        run_id: str | None,
        resource_id: str,
        payload: Mapping[str, Any],
        expected_session_id: str | None,
    ) -> ParallelAutoKpJobScope:
        phase = self.scope._optional_text(payload.get("phase"), "payload.phase") or "prepare"
        if phase == "prepare":
            return self._validate_prepare(
                campaign_id,
                run_id,
                resource_id,
                payload,
                expected_session_id=expected_session_id,
            )
        if phase in {"commit", "resolve_blind_checks"}:
            return self._validate_batch_phase(
                campaign_id,
                run_id,
                resource_id,
                payload,
                phase=phase,
                expected_session_id=expected_session_id,
            )
        raise ValueError(f"Unsupported parallel Auto KP phase: {phase}")

    def _validate_prepare(
        self,
        campaign_id: str,
        run_id: str | None,
        resource_id: str,
        payload: Mapping[str, Any],
        *,
        expected_session_id: str | None,
    ) -> ParallelAutoKpJobScope:
        action_ids = self._distinct_text_sequence(
            payload.get("action_ids"),
            "payload.action_ids",
            minimum=2,
            maximum=12,
        )
        if resource_id not in action_ids:
            raise ValueError("Parallel prepare resource must be one collected action")
        actions = tuple(self.repo.get_player_action(action_id) for action_id in action_ids)
        for action in actions:
            self.scope._require_same_campaign(
                campaign_id,
                action.get("campaign_id"),
                "Parallel prepare action",
            )
        session_ids = {str(action.get("session_id") or "") for action in actions}
        member_ids = {str(action.get("member_id") or "") for action in actions}
        if "" in session_ids or len(session_ids) != 1:
            raise PermissionError("Parallel prepare actions must share one session")
        if "" in member_ids or len(member_ids) != len(actions):
            raise ValueError("Parallel prepare actions require distinct session members")
        session_id = next(iter(session_ids))
        self.scope._validate_active_session(
            campaign_id,
            session_id,
            expected_session_id=expected_session_id,
        )

        lifecycle: str | None = None
        replay_batch_id: str | None = None
        for action in actions:
            status = str(action.get("status") or "")
            proposal_id = action.get("proposal_id")
            action_lifecycle = (
                "fresh"
                if status == "submitted" and proposal_id is None
                else "prepared"
                if status == "reviewed" and proposal_id is not None
                else "invalid"
            )
            if action_lifecycle == "invalid":
                raise ValueError(
                    "Parallel prepare requires fresh actions or one durable replay"
                )
            if lifecycle is None:
                lifecycle = action_lifecycle
            elif lifecycle != action_lifecycle:
                raise ValueError("Parallel prepare cannot mix fresh and prepared actions")
            if not self.repo.is_session_member_active(
                str(action["member_id"]), session_id
            ):
                raise PermissionError(
                    "Every parallel action owner must remain active in the session"
                )
            existing_batch = self.repo.get_parallel_action_batch_for_action(
                str(action["id"])
            )
            if action_lifecycle == "fresh" and existing_batch is not None:
                raise ValueError("A fresh player action already belongs to a parallel batch")
            if action_lifecycle == "prepared":
                if existing_batch is None:
                    raise ValueError("Prepared parallel actions lost their durable batch")
                candidate_id = str(existing_batch["id"])
                if replay_batch_id is None:
                    replay_batch_id = candidate_id
                elif replay_batch_id != candidate_id:
                    raise ValueError("Prepared actions belong to different parallel batches")

        if replay_batch_id is not None:
            replay = self.repo.get_parallel_action_batch(replay_batch_id)
            replay_action_ids = tuple(str(item["action_id"]) for item in replay["items"])
            if set(replay_action_ids) != set(action_ids):
                raise ValueError("Parallel replay action set does not match its durable batch")

        regather_id = self.scope._optional_text(
            payload.get("regather_id"), "payload.regather_id"
        )
        if regather_id is not None:
            regather = self.repo.get_parallel_action_regather(regather_id)
            if (
                regather.get("status") != "queued"
                or str(regather.get("campaign_id")) != campaign_id
                or str(regather.get("session_id")) != session_id
                or str(regather.get("prepare_job_id") or "") == ""
            ):
                raise PermissionError("Parallel regather is outside queued authority")
            expected_ids = tuple(
                str(item["replacement_action_id"])
                for item in regather["members"]
            )
            if expected_ids != action_ids:
                raise PermissionError("Parallel prepare actions do not match regather")

        self.scope._match_optional_payload_text(payload, "session_id", session_id)
        run = self.scope._validate_current_run(campaign_id, run_id, required=False)
        self.scope._validate_payload_run(payload, run_id, run)
        return ParallelAutoKpJobScope(session_id=session_id, run_id=run_id)

    def _validate_batch_phase(
        self,
        campaign_id: str,
        run_id: str | None,
        resource_id: str,
        payload: Mapping[str, Any],
        *,
        phase: str,
        expected_session_id: str | None,
    ) -> ParallelAutoKpJobScope:
        batch_id = self.scope._required_text(payload.get("batch_id"), "payload.batch_id")
        if resource_id != batch_id:
            raise PermissionError("Parallel batch job resource does not match its payload")
        batch = self.repo.get_parallel_action_batch(batch_id)
        self.scope._require_same_campaign(
            campaign_id,
            batch.get("campaign_id"),
            "Parallel batch job",
        )
        session_id = self.scope._required_text(
            batch.get("session_id"), "batch.session_id"
        )
        self.scope._validate_active_session(
            campaign_id,
            session_id,
            expected_session_id=expected_session_id,
        )
        self.scope._match_optional_payload_text(payload, "session_id", session_id)
        batch_run_id = self.scope._required_text(batch.get("run_id"), "batch.run_id")
        if run_id != batch_run_id:
            raise PermissionError("Parallel batch job run does not match its batch")

        terminal = batch.get("status") == "settled"
        run = self.scope._validate_current_run(
            campaign_id,
            run_id,
            required=True,
            allow_terminal=terminal,
        )
        if phase == "commit":
            if batch.get("status") not in {"ready", "settled"}:
                raise ValueError("Parallel commit jobs require a ready or settled batch")
            expected_version = payload.get("expected_version")
            if type(expected_version) is not int or expected_version < 1:
                raise ValueError("Parallel commit jobs require a positive expected_version")
        else:
            self._validate_blind_payload(batch, payload)

        self.scope._validate_payload_run(payload, run_id, run)
        return ParallelAutoKpJobScope(session_id=session_id, run_id=run_id)

    def _validate_blind_payload(
        self,
        batch: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> None:
        if batch.get("status") not in {"awaiting_checks", "ready", "settled"}:
            raise ValueError(
                "Parallel blind-check jobs require an awaiting, ready, or settled batch"
            )
        if payload.get("run_version") != int(batch["module_run_version"]):
            raise ValueError("Parallel blind-check run version is stale")
        if payload.get("preparation_hash") != batch.get("preparation_hash"):
            raise ValueError("Parallel blind-check preparation hash is stale")
        expected_action_ids = tuple(str(item["action_id"]) for item in batch["items"])
        action_ids = self._distinct_text_sequence(
            payload.get("action_ids"),
            "payload.action_ids",
            minimum=2,
            maximum=12,
        )
        if action_ids != expected_action_ids:
            raise PermissionError("Parallel blind-check actions do not match the batch")

        expected_check_ids: list[str] = []
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
                    or check.get("campaign_id") != batch.get("campaign_id")
                    or check.get("session_id") != batch.get("session_id")
                    or check.get("player_action_id") != item.get("action_id")
                    or check.get("proposal_id") != item.get("proposal_id")
                ):
                    raise PermissionError(
                        "Parallel blind check is outside its item authority"
                    )
                expected_check_ids.append(str(check["id"]))
        check_ids = self._distinct_text_sequence(
            payload.get("check_ids"),
            "payload.check_ids",
            minimum=1,
            maximum=12,
        )
        if check_ids != tuple(expected_check_ids):
            raise PermissionError(
                "Parallel blind-check job check IDs no longer match the batch"
            )

    def _distinct_text_sequence(
        self,
        value: Any,
        field_name: str,
        *,
        minimum: int,
        maximum: int,
    ) -> tuple[str, ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise TypeError(f"{field_name} must be an array")
        items = tuple(self.scope._required_text(item, field_name) for item in value)
        if not minimum <= len(items) <= maximum or len(set(items)) != len(items):
            raise ValueError(
                f"{field_name} requires {minimum}-{maximum} distinct identifiers"
            )
        return items


__all__ = [
    "AutoKpJobScopeAuthority",
    "ParallelAutoKpJobAuthorityValidator",
    "ParallelAutoKpJobScope",
]

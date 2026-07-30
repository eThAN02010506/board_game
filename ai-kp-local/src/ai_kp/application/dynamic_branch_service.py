"""Lifecycle service for approved, causally constrained dynamic branches."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from ai_kp.application.errors import ConflictError, InvalidInputError
from ai_kp.application.ports.dynamic_branches import DynamicBranchStore
from ai_kp.director.world_expansion import (
    DynamicBranchPlan,
    branch_condition_failures,
)
from ai_kp.platform.sessions.models import AuthenticatedMember

BranchOutcome = Literal["succeeded", "failed", "skipped"]
_COMMAND_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")


@dataclass(frozen=True)
class ResolveBranchBeatCommand:
    expected_version: int
    command_id: str
    outcome: BranchOutcome
    note: str = ""
    observed_effects: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResumeDynamicBranchCommand:
    expected_version: int
    command_id: str
    note: str


@dataclass(frozen=True)
class AbandonDynamicBranchCommand:
    expected_version: int
    command_id: str
    note: str


class DynamicBranchService:
    """Advance branch plans while keeping all world changes as candidates."""

    def __init__(self, repo: DynamicBranchStore):
        self.repo = repo

    def list_for_campaign(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        self._require_kp(identity, campaign_id)
        return self.repo.list_dynamic_branch_runs(campaign_id, status=status)

    def get(
        self,
        branch_id: str,
        identity: AuthenticatedMember,
    ) -> dict[str, Any]:
        branch = self.repo.get_dynamic_branch_run(branch_id)
        self._require_kp(identity, str(branch["campaign_id"]))
        return branch

    def activate_after_contact(
        self,
        proposal_id: str,
        identity: AuthenticatedMember,
        *,
        command_id: str,
    ) -> dict[str, Any] | None:
        branch = self.repo.get_dynamic_branch_for_proposal(proposal_id)
        if branch is None:
            return None
        self._require_kp(identity, str(branch["campaign_id"]))
        if branch["status"] != "approved":
            return branch
        plan = DynamicBranchPlan.model_validate(branch["plan"])
        snapshot = self._runtime_snapshot(branch)
        failures = [
            *branch_condition_failures(plan.entry_conditions, snapshot),
            *self._anchor_failures(plan, snapshot),
        ]
        status = "paused" if failures else "active"
        event_type = "paused" if failures else "activated"
        result = self.repo.transition_dynamic_branch(
            str(branch["id"]),
            expected_version=int(branch["version"]),
            status=status,
            current_beat_index=0,
            event_type=event_type,
            beat_id=plan.beats[0].beat_id,
            outcome=None,
            note=(
                "branch activation paused: " + "; ".join(failures)
                if failures
                else "actual contact activated approved branch"
            ),
            payload={
                "proposal_id": proposal_id,
                "condition_failures": failures,
            },
            member_id=identity.member_id,
            command_id=command_id,
        )
        return result["branch"]

    def resolve_beat(
        self,
        branch_id: str,
        identity: AuthenticatedMember,
        command: ResolveBranchBeatCommand,
    ) -> dict[str, Any]:
        self._validate_command_id(command.command_id)
        self.repo.begin_immediate()
        branch = self.repo.get_dynamic_branch_run(branch_id)
        self._require_kp(identity, str(branch["campaign_id"]))
        replay = self._idempotent_replay(branch, command.command_id)
        if replay is not None:
            return replay
        if branch["status"] != "active":
            raise ConflictError("Only an active dynamic branch can resolve a beat")
        plan = DynamicBranchPlan.model_validate(branch["plan"])
        beat_index = int(branch["current_beat_index"])
        if beat_index >= len(plan.beats):
            raise ConflictError("Dynamic branch has no unresolved beat")
        beat = plan.beats[beat_index]
        snapshot = self._runtime_snapshot(branch)
        failures = [
            *branch_condition_failures(beat.preconditions, snapshot),
            *self._anchor_failures(plan, snapshot),
        ]
        if failures:
            return self._pause_for_preconditions(
                branch,
                identity,
                command,
                beat_id=beat.beat_id,
                failures=failures,
            )
        note = self._text(command.note, "note", 2000, allow_blank=True)
        observed = [
            self._text(value, "observed_effect", 1000)
            for value in command.observed_effects
        ]
        if len(observed) > 8:
            raise InvalidInputError("A beat can record at most eight observed effects")
        next_index, status, event_type = self._next_transition(
            beat_index,
            len(plan.beats),
            command.outcome,
            beat.failure_policy,
        )
        completion_failures = (
            branch_condition_failures(plan.completion_conditions, snapshot)
            if status == "completed"
            else []
        )
        if completion_failures:
            next_index = beat_index
            status = "paused"
            event_type = "paused"
        result = self.repo.transition_dynamic_branch(
            branch_id,
            expected_version=command.expected_version,
            status=status,
            current_beat_index=next_index,
            event_type=event_type,
            beat_id=beat.beat_id,
            outcome=command.outcome,
            note=note,
            payload={
                "observed_effects": observed,
                "candidate_effects": [
                    effect.model_dump(mode="json")
                    for effect in beat.expected_effects
                ],
                "world_writes_performed": False,
                "completion_condition_failures": completion_failures,
            },
            member_id=identity.member_id,
            command_id=command.command_id,
        )
        return result

    def resume(
        self,
        branch_id: str,
        identity: AuthenticatedMember,
        command: ResumeDynamicBranchCommand,
    ) -> dict[str, Any]:
        self._validate_command_id(command.command_id)
        self.repo.begin_immediate()
        branch = self.repo.get_dynamic_branch_run(branch_id)
        self._require_kp(identity, str(branch["campaign_id"]))
        replay = self._idempotent_replay(branch, command.command_id)
        if replay is not None:
            return replay
        if branch["status"] != "paused":
            raise ConflictError("Only a paused dynamic branch can resume")
        return self.repo.transition_dynamic_branch(
            branch_id,
            expected_version=command.expected_version,
            status="active",
            current_beat_index=int(branch["current_beat_index"]),
            event_type="resumed",
            beat_id=self._current_beat_id(branch),
            outcome=None,
            note=self._text(command.note, "note", 2000),
            payload={"kp_override": True},
            member_id=identity.member_id,
            command_id=command.command_id,
        )

    def abandon(
        self,
        branch_id: str,
        identity: AuthenticatedMember,
        command: AbandonDynamicBranchCommand,
    ) -> dict[str, Any]:
        self._validate_command_id(command.command_id)
        self.repo.begin_immediate()
        branch = self.repo.get_dynamic_branch_run(branch_id)
        self._require_kp(identity, str(branch["campaign_id"]))
        replay = self._idempotent_replay(branch, command.command_id)
        if replay is not None:
            return replay
        if branch["status"] in {"completed", "abandoned"}:
            raise ConflictError("Dynamic branch is already terminal")
        return self.repo.transition_dynamic_branch(
            branch_id,
            expected_version=command.expected_version,
            status="abandoned",
            current_beat_index=int(branch["current_beat_index"]),
            event_type="abandoned",
            beat_id=self._current_beat_id(branch),
            outcome=None,
            note=self._text(command.note, "note", 2000),
            payload={},
            member_id=identity.member_id,
            command_id=command.command_id,
        )

    def _pause_for_preconditions(
        self,
        branch: dict[str, Any],
        identity: AuthenticatedMember,
        command: ResolveBranchBeatCommand,
        *,
        beat_id: str,
        failures: list[str],
    ) -> dict[str, Any]:
        return self.repo.transition_dynamic_branch(
            str(branch["id"]),
            expected_version=command.expected_version,
            status="paused",
            current_beat_index=int(branch["current_beat_index"]),
            event_type="paused",
            beat_id=beat_id,
            outcome=None,
            note="beat preconditions no longer hold",
            payload={"condition_failures": failures},
            member_id=identity.member_id,
            command_id=command.command_id,
        )

    def _runtime_snapshot(self, branch: dict[str, Any]) -> dict[str, Any]:
        run = self.repo.get_campaign_module_run(str(branch["module_run_id"]))
        fact_heads = self.repo.list_fact_heads(str(branch["campaign_id"]))
        entity_states = self.repo.list_module_run_entity_states(
            str(branch["module_run_id"])
        )
        return {
            "scene": {
                "current_scene_key": run.get("current_scene_key"),
                "current_scene_title": run.get("current_scene_title"),
            },
            "time": {"current_time": run.get("scene_started_world_time")},
            "entity_states": entity_states,
            "world_fact_heads": [
                {
                    "fact_key": item.get("fact_key"),
                    "object_text": (item.get("fact") or {}).get("object_text"),
                }
                for item in (
                    head.as_dict() if hasattr(head, "as_dict") else dict(head)
                    for head in fact_heads
                )
            ],
            "reachability": self._reachability(run, entity_states),
            "run": run,
        }

    def _reachability(
        self,
        run: dict[str, Any],
        entity_states: list[dict[str, Any]],
    ) -> dict[str, Any]:
        active_entries = tuple(
            str(item["entity_id"])
            for item in entity_states
            if item.get("status") in {"available", "discovered", "resolved"}
        )
        if not active_entries:
            return {"reached_entity_ids": [], "has_conflicts": False}
        return self.repo.module_graph_reachability(
            str(run["module_id"]),
            active_entries,
        )

    @staticmethod
    def _anchor_failures(
        plan: DynamicBranchPlan,
        snapshot: dict[str, Any],
    ) -> list[str]:
        reachable_ids = {
            str(item)
            for item in snapshot["reachability"].get("reached_entity_ids", [])
        }
        return [
            f"anchor:{guard.anchor_entity_id} is not currently reachable"
            for guard in plan.anchor_guards
            if guard.anchor_entity_id not in reachable_ids
        ]

    @staticmethod
    def _next_transition(
        beat_index: int,
        beat_count: int,
        outcome: BranchOutcome,
        failure_policy: str,
    ) -> tuple[int, str, str]:
        if outcome == "failed" and failure_policy in {"pause_for_kp", "divert"}:
            return beat_index, "paused", "paused"
        next_index = beat_index + 1
        if next_index >= beat_count:
            return next_index, "completed", "completed"
        return next_index, "active", "beat_resolved"

    @staticmethod
    def _current_beat_id(branch: dict[str, Any]) -> str | None:
        beats = branch["plan"].get("beats") or []
        index = int(branch["current_beat_index"])
        return str(beats[index]["beat_id"]) if index < len(beats) else None

    def _idempotent_replay(
        self,
        branch: dict[str, Any],
        command_id: str,
    ) -> dict[str, Any] | None:
        event = self.repo.find_dynamic_branch_event(
            str(branch["id"]),
            command_id,
        )
        if event is None:
            return None
        return {
            "branch": branch,
            "event": event,
            "idempotent_replay": True,
        }

    @staticmethod
    def _validate_command_id(value: str) -> None:
        if not _COMMAND_ID_PATTERN.fullmatch(value):
            raise InvalidInputError(
                "command_id must contain 8-200 letters, digits, '.', '_', ':', or '-'"
            )

    @staticmethod
    def _require_kp(
        identity: AuthenticatedMember,
        campaign_id: str,
    ) -> None:
        if identity.campaign_id != campaign_id:
            raise KeyError(f"Campaign not found: {campaign_id}")
        if identity.role != "kp":
            raise PermissionError("KP access required")

    @staticmethod
    def _text(
        value: str,
        field_name: str,
        max_length: int,
        *,
        allow_blank: bool = False,
    ) -> str:
        normalized = " ".join(unicodedata.normalize("NFKC", value).split())
        if not normalized and not allow_blank:
            raise InvalidInputError(f"{field_name} cannot be blank")
        if len(normalized) > max_length:
            raise InvalidInputError(
                f"{field_name} cannot exceed {max_length} characters"
            )
        return normalized


__all__ = [
    "AbandonDynamicBranchCommand",
    "DynamicBranchService",
    "ResolveBranchBeatCommand",
    "ResumeDynamicBranchCommand",
]

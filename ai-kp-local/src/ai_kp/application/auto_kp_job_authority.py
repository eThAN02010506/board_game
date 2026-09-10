"""Fail-closed authority checks for durable Auto KP jobs.

The HTTP queue endpoint and the background worker are separate trust boundaries.
Both must bind a job's denormalized campaign/run/session fields back to the
authoritative resource before the job can be persisted or executed.  Keeping the
rules here prevents one job type from becoming a cross-campaign confused deputy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from ai_kp.application.auto_kp_parallel_job_authority import (
    ParallelAutoKpJobAuthorityValidator,
)
from ai_kp.platform.resolution import build_check_consequence_snapshot

AutoKpJobType = Literal[
    "player_action",
    "parallel_actions",
    "world_expansion",
    "check_consequence",
    "encounter_turn",
]

_JOB_TYPES = {
    "player_action",
    "parallel_actions",
    "world_expansion",
    "check_consequence",
    "encounter_turn",
}


@dataclass(frozen=True)
class AutoKpJobAuthority:
    """The server-derived scope a validated job is allowed to operate in."""

    campaign_id: str
    session_id: str
    run_id: str | None
    job_type: AutoKpJobType
    resource_id: str


class AutoKpJobAuthorityValidator:
    """Bind every job type to its authoritative campaign, run, and session."""

    def __init__(self, repo: Any):
        self.repo = repo

    def validate_job(
        self,
        job: Mapping[str, Any],
        *,
        expected_session_id: str | None = None,
    ) -> AutoKpJobAuthority:
        payload = job.get("payload") or {}
        if not isinstance(payload, Mapping):
            raise TypeError("Auto KP job payload must be an object")
        return self.validate(
            campaign_id=self._required_text(job.get("campaign_id"), "campaign_id"),
            run_id=self._optional_text(job.get("run_id"), "run_id"),
            job_type=self._job_type(job.get("job_type")),
            resource_id=self._required_text(job.get("resource_id"), "resource_id"),
            payload=payload,
            expected_session_id=expected_session_id,
        )

    def validate(
        self,
        *,
        campaign_id: str,
        run_id: str | None,
        job_type: AutoKpJobType,
        resource_id: str,
        payload: Mapping[str, Any],
        expected_session_id: str | None = None,
    ) -> AutoKpJobAuthority:
        """Validate a not-yet-persisted or claimed job without changing state."""

        self.repo.get_campaign(campaign_id)
        if job_type == "player_action":
            return self._validate_player_action(
                campaign_id,
                run_id,
                resource_id,
                payload,
                expected_session_id=expected_session_id,
            )
        if job_type == "check_consequence":
            return self._validate_check_consequence(
                campaign_id,
                run_id,
                resource_id,
                payload,
                expected_session_id=expected_session_id,
            )
        if job_type == "parallel_actions":
            return self._validate_parallel_actions(
                campaign_id,
                run_id,
                resource_id,
                payload,
                expected_session_id=expected_session_id,
            )
        if job_type == "encounter_turn":
            return self._validate_encounter_turn(
                campaign_id,
                run_id,
                resource_id,
                payload,
                expected_session_id=expected_session_id,
            )
        return self._validate_world_expansion(
            campaign_id,
            run_id,
            resource_id,
            payload,
            expected_session_id=expected_session_id,
        )

    def _validate_player_action(
        self,
        campaign_id: str,
        run_id: str | None,
        resource_id: str,
        payload: Mapping[str, Any],
        *,
        expected_session_id: str | None,
    ) -> AutoKpJobAuthority:
        action = self.repo.get_player_action(resource_id)
        self._require_same_campaign(
            campaign_id,
            action.get("campaign_id"),
            "Player-action job",
        )
        session_id = self._required_text(action.get("session_id"), "action.session_id")
        self._validate_active_session(
            campaign_id,
            session_id,
            expected_session_id=expected_session_id,
        )
        if action.get("status") != "submitted" or action.get("proposal_id") is not None:
            raise ValueError("Player-action jobs require one unbound submitted action")
        if not self.repo.is_session_member_active(str(action["member_id"]), session_id):
            raise PermissionError("Player-action owner is no longer active in the session")
        if self.repo.get_parallel_action_batch_for_action(resource_id) is not None:
            raise ValueError("A parallel action cannot re-enter single-action automation")

        self._match_optional_payload_text(payload, "action_id", resource_id)
        self._match_optional_payload_text(payload, "session_id", session_id)
        run = self._validate_current_run(campaign_id, run_id, required=False)
        self._validate_payload_run(payload, run_id, run)
        return self._authority(
            campaign_id, session_id, run_id, "player_action", resource_id
        )

    def _validate_check_consequence(
        self,
        campaign_id: str,
        run_id: str | None,
        resource_id: str,
        payload: Mapping[str, Any],
        *,
        expected_session_id: str | None,
    ) -> AutoKpJobAuthority:
        check = self.repo.get_skill_check(resource_id)
        self._require_same_campaign(
            campaign_id,
            check.get("campaign_id"),
            "Check-consequence job",
        )
        action_id = self._required_text(
            check.get("player_action_id"), "check.player_action_id"
        )
        action = self.repo.get_player_action(action_id)
        self._require_same_campaign(campaign_id, action.get("campaign_id"), "Check action")
        session_id = self._required_text(check.get("session_id"), "check.session_id")
        if action.get("session_id") != session_id:
            raise PermissionError("Check and player action belong to different sessions")
        self._validate_active_session(
            campaign_id,
            session_id,
            expected_session_id=expected_session_id,
        )
        if action.get("status") != "reviewed" or not action.get("proposal_id"):
            raise ValueError("Check-consequence jobs require one reviewed player action")
        if self.repo.get_parallel_action_batch_for_action(action_id) is not None:
            raise ValueError(
                "Parallel checks must rendezvous through their atomic batch workflow"
            )

        checks = self.repo.list_skill_checks_for_action(action_id)
        if not checks or any(item.get("status") == "requested" for item in checks):
            raise ValueError("Check-consequence jobs require all requested rolls to finish")
        opposed = self.repo.list_opposed_checks_for_action(action_id)
        if any(
            item.get("status") in {"pending", "reroll_required"} for item in opposed
        ):
            raise ValueError("Check-consequence jobs require terminal opposed checks")

        self._match_optional_payload_text(payload, "action_id", action_id)
        self._match_optional_payload_text(payload, "session_id", session_id)
        fingerprint = payload.get("result_fingerprint")
        if fingerprint is not None:
            expected_fingerprint = build_check_consequence_snapshot(
                checks, opposed
            )["result_fingerprint"]
            if fingerprint != expected_fingerprint:
                raise ValueError("Check-consequence result fingerprint is stale")
        run = self._validate_current_run(campaign_id, run_id, required=False)
        self._validate_payload_run(payload, run_id, run)
        return self._authority(
            campaign_id, session_id, run_id, "check_consequence", resource_id
        )

    def _validate_parallel_actions(
        self,
        campaign_id: str,
        run_id: str | None,
        resource_id: str,
        payload: Mapping[str, Any],
        *,
        expected_session_id: str | None,
    ) -> AutoKpJobAuthority:
        scope = ParallelAutoKpJobAuthorityValidator(self.repo, self).validate(
            campaign_id=campaign_id,
            run_id=run_id,
            resource_id=resource_id,
            payload=payload,
            expected_session_id=expected_session_id,
        )
        return self._authority(
            campaign_id,
            scope.session_id,
            scope.run_id,
            "parallel_actions",
            resource_id,
        )

    def _validate_world_expansion(
        self,
        campaign_id: str,
        run_id: str | None,
        resource_id: str,
        payload: Mapping[str, Any],
        *,
        expected_session_id: str | None,
    ) -> AutoKpJobAuthority:
        payload_run_id = self._required_text(payload.get("run_id"), "payload.run_id")
        if run_id != payload_run_id or resource_id != payload_run_id:
            raise PermissionError(
                "World-expansion job run and resource must identify the same run"
            )
        run = self._validate_current_run(campaign_id, run_id, required=True)
        session_id = self._required_text(payload.get("session_id"), "payload.session_id")
        self._validate_active_session(
            campaign_id,
            session_id,
            expected_session_id=expected_session_id,
        )
        intent = self._required_text(payload.get("player_intent"), "payload.player_intent")
        if len(intent) > 20_000:
            raise ValueError("World-expansion player intent is too long")
        pc_id = payload.get("pc_id")
        if pc_id is not None:
            pc = self.repo.get_pc(self._required_text(pc_id, "payload.pc_id"))
            self._require_same_campaign(
                campaign_id,
                pc.get("campaign_id"),
                "World-expansion PC",
            )
        map_id = payload.get("map_id")
        if map_id is not None:
            map_record = self.repo.get_map(self._required_text(map_id, "payload.map_id"))
            self._require_same_campaign(
                campaign_id,
                map_record.get("campaign_id"),
                "World-expansion map",
            )
        return self._authority(
            campaign_id,
            session_id,
            self._required_text(run["id"], "run.id"),
            "world_expansion",
            resource_id,
        )

    def _validate_encounter_turn(
        self,
        campaign_id: str,
        run_id: str | None,
        resource_id: str,
        payload: Mapping[str, Any],
        *,
        expected_session_id: str | None,
    ) -> AutoKpJobAuthority:
        encounter = self.repo.get_coc7_encounter(resource_id)
        self._require_same_campaign(
            campaign_id, encounter.get("campaign_id"), "Encounter-turn job"
        )
        session_id = self._required_text(
            encounter.get("session_id"), "encounter.session_id"
        )
        self._validate_active_session(
            campaign_id,
            session_id,
            expected_session_id=expected_session_id,
        )
        if encounter.get("status") != "active":
            raise ValueError("Encounter-turn jobs require an active encounter")
        episode = self.repo.get_current_campaign_episode(session_id)
        if episode is None or episode.get("status") != "in_progress":
            raise ValueError("Encounter automation requires an in-progress episode")
        if (
            payload.get("episode_id") != episode.get("id")
            or type(payload.get("episode_version")) is not int
            or payload.get("episode_version") != int(episode["version"])
        ):
            raise ValueError("Encounter-turn episode authority is stale")
        revision = self.repo.get_current_session_zero_revision(campaign_id)
        if revision is None or revision.get("status") != "active":
            raise ValueError("Encounter automation requires an active Session 0 agreement")
        if dict(revision.get("config") or {}).get("hosting_mode") != "ai_kp":
            raise ValueError("Encounter automation requires Full AI KP hosting mode")
        version = payload.get("encounter_version")
        if type(version) is not int or int(encounter["version"]) != version:
            raise ValueError("Encounter-turn job version is stale")
        active_id = encounter["state"]["turn_order"][int(encounter["turn_index"])]
        if payload.get("participant_id") != active_id:
            raise ValueError("Encounter-turn participant is no longer active")
        phase = payload.get("phase")
        if phase not in {"enemy", "idle_player"}:
            raise ValueError("Encounter-turn phase is invalid")
        participant = next(
            item
            for item in encounter["state"]["participants"]
            if item["participant_id"] == active_id
        )
        if (phase == "enemy") == bool(participant.get("investigator_id")):
            raise PermissionError("Encounter-turn phase does not match participant control")
        configured_policy = str(dict(revision.get("config") or {}).get("idle_policy") or "wait")
        expected_policy = "agent" if phase == "enemy" else configured_policy
        if payload.get("policy") != expected_policy:
            raise PermissionError("Encounter-turn policy differs from Session 0 authority")
        if phase == "idle_player" and expected_policy == "wait":
            raise ValueError("Wait policy does not authorize an automated player turn")
        self._match_optional_payload_text(payload, "session_id", session_id)
        run = self._validate_current_run(campaign_id, run_id, required=False)
        self._validate_payload_run(payload, run_id, run)
        return self._authority(
            campaign_id, session_id, run_id, "encounter_turn", resource_id
        )

    def _validate_active_session(
        self,
        campaign_id: str,
        session_id: str,
        *,
        expected_session_id: str | None,
    ) -> None:
        session = self.repo.get_campaign_session(session_id)
        self._require_same_campaign(campaign_id, session.get("campaign_id"), "Job session")
        if session.get("status") != "active":
            raise ValueError("Auto KP jobs require an active campaign session")
        if expected_session_id is not None and session_id != expected_session_id:
            raise PermissionError("Auto KP job belongs to another campaign session")

    def _validate_current_run(
        self,
        campaign_id: str,
        run_id: str | None,
        *,
        required: bool,
        allow_terminal: bool = False,
    ) -> dict[str, Any] | None:
        active = self.repo.get_active_campaign_module_run(campaign_id)
        if run_id is None:
            if required:
                raise ValueError("Auto KP job requires a scenario run")
            if active is not None:
                raise ValueError("Auto KP job omitted the campaign's active run")
            return None
        run = self.repo.get_campaign_module_run(run_id)
        self._require_same_campaign(campaign_id, run.get("campaign_id"), "Auto KP run")
        if allow_terminal:
            return run
        if (
            run.get("status") != "active"
            or active is None
            or str(active.get("id")) != run_id
        ):
            raise ValueError("Auto KP job run is no longer the active campaign run")
        return run

    @staticmethod
    def _validate_payload_run(
        payload: Mapping[str, Any],
        run_id: str | None,
        run: Mapping[str, Any] | None,
    ) -> None:
        if "run_id" in payload:
            payload_run_id = AutoKpJobAuthorityValidator._optional_text(
                payload.get("run_id"), "payload.run_id"
            )
            if payload_run_id != run_id:
                raise PermissionError("Auto KP payload run does not match the job run")
        if "run_version" not in payload:
            return
        version = payload.get("run_version")
        if type(version) is not int or version < 0:
            raise ValueError("payload.run_version must be a non-negative integer")
        expected = int(run["version"]) if run is not None else 0
        if version != expected:
            raise ValueError("Auto KP payload run version is stale")

    @staticmethod
    def _match_optional_payload_text(
        payload: Mapping[str, Any], key: str, expected: str
    ) -> None:
        if key not in payload:
            return
        value = AutoKpJobAuthorityValidator._required_text(
            payload.get(key), f"payload.{key}"
        )
        if value != expected:
            raise PermissionError(f"Auto KP payload {key} is outside resource authority")

    @staticmethod
    def _require_same_campaign(
        expected: str,
        actual: Any,
        label: str,
    ) -> None:
        if actual != expected:
            raise PermissionError(f"{label} crosses campaign boundaries")

    @staticmethod
    def _required_text(value: Any, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} is required")
        return value.strip()

    @staticmethod
    def _optional_text(value: Any, field_name: str) -> str | None:
        if value is None:
            return None
        return AutoKpJobAuthorityValidator._required_text(value, field_name)

    @staticmethod
    def _job_type(value: Any) -> AutoKpJobType:
        if value not in _JOB_TYPES:
            raise ValueError("Unsupported Auto KP job type")
        return value

    @staticmethod
    def _authority(
        campaign_id: str,
        session_id: str,
        run_id: str | None,
        job_type: AutoKpJobType,
        resource_id: str,
    ) -> AutoKpJobAuthority:
        return AutoKpJobAuthority(
            campaign_id=campaign_id,
            session_id=session_id,
            run_id=run_id,
            job_type=job_type,
            resource_id=resource_id,
        )


__all__ = [
    "AutoKpJobAuthority",
    "AutoKpJobAuthorityValidator",
    "AutoKpJobType",
]

"""Typed authority boundary for long-running campaign objectives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ai_kp.application.errors import InvalidInputError
from ai_kp.platform.sessions.models import AuthenticatedMember

ObjectiveStatus = Literal["open", "blocked", "completed", "failed", "abandoned"]


class CampaignObjectiveStore(Protocol):
    def begin_immediate(self) -> None: ...
    def create_campaign_objective(self, **values: Any) -> dict[str, Any]: ...
    def update_campaign_objective(self, objective_id: str, **values: Any) -> dict[str, Any]: ...
    def get_campaign_objective(self, objective_id: str) -> dict[str, Any]: ...
    def list_campaign_objectives(self, campaign_id: str) -> list[dict[str, Any]]: ...
    def append_realtime_event(self, **values: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CreateObjectiveCommand:
    command_id: str
    title: str
    public_description: str = ""
    kp_notes: str = ""
    visibility: Literal["table", "kp"] = "table"
    source_refs: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class UpdateObjectiveCommand:
    command_id: str
    expected_version: int
    status: ObjectiveStatus
    public_progress: str = ""
    kp_notes: str = ""
    source_refs: tuple[dict[str, Any], ...] = ()


class CampaignObjectiveService:
    """AI may call this tool, but only typed commands can mutate objective state."""

    def __init__(self, repo: CampaignObjectiveStore):
        self.repo = repo

    def list(self, campaign_id: str, identity: AuthenticatedMember) -> list[dict[str, Any]]:
        self._scope(campaign_id, identity)
        return [
            projected
            for objective in self.repo.list_campaign_objectives(campaign_id)
            if (projected := self._project(objective, identity.role)) is not None
        ]

    def create(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: CreateObjectiveCommand,
    ) -> dict[str, Any]:
        self._kp(campaign_id, identity)
        title = self._text(command.title, "Objective title", 200)
        self.repo.begin_immediate()
        objective = self.repo.create_campaign_objective(
            campaign_id=campaign_id,
            command_id=self._command_id(command.command_id),
            title=title,
            public_description=self._text(command.public_description, "Public description", 2000),
            kp_notes=self._text(command.kp_notes, "KP notes", 4000),
            visibility=command.visibility,
            source_refs=list(command.source_refs),
            actor_member_id=identity.member_id,
        )
        self._notify(identity, objective, "campaign.objective_created")
        return self._project(objective, "kp") or {}

    def update(
        self,
        objective_id: str,
        identity: AuthenticatedMember,
        command: UpdateObjectiveCommand,
    ) -> dict[str, Any]:
        objective = self.repo.get_campaign_objective(objective_id)
        self._kp(str(objective["campaign_id"]), identity)
        if command.expected_version < 1:
            raise InvalidInputError("Objective version must be positive")
        self.repo.begin_immediate()
        objective = self.repo.update_campaign_objective(
            objective_id,
            command_id=self._command_id(command.command_id),
            expected_version=command.expected_version,
            status=command.status,
            public_progress=self._text(command.public_progress, "Public progress", 2000),
            kp_notes=self._text(command.kp_notes, "KP notes", 4000),
            source_refs=list(command.source_refs),
            actor_member_id=identity.member_id,
        )
        self._notify(identity, objective, "campaign.objective_updated")
        return self._project(objective, "kp") or {}

    def _notify(self, identity: AuthenticatedMember, objective: dict[str, Any], event_type: str) -> None:
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=identity.campaign_id,
            audience="session" if objective["visibility"] == "table" else "kp",
            event_type=event_type,
            resource_type="campaign_objective",
            resource_id=str(objective["id"]),
            payload={"status": objective["status"]},
        )

    @staticmethod
    def _project(objective: dict[str, Any], role: str) -> dict[str, Any] | None:
        if role != "kp" and objective["visibility"] != "table":
            return None
        public_events = [
            {
                key: event.get(key)
                for key in ("id", "from_status", "to_status", "public_progress", "created_at")
            }
            for event in objective["events"]
            if event.get("public_progress") or event.get("from_status") is not None
        ]
        result = {
            key: objective[key]
            for key in (
                "id", "campaign_id", "title", "public_description", "status",
                "visibility", "version", "created_at", "updated_at",
            )
        }
        result["events"] = public_events
        if role == "kp":
            result["kp_notes"] = objective["kp_notes"]
            result["source_refs"] = objective["source_refs"]
            result["events"] = objective["events"]
        return result

    @staticmethod
    def _scope(campaign_id: str, identity: AuthenticatedMember) -> None:
        if identity.campaign_id != campaign_id or identity.role not in {"kp", "player", "observer"}:
            raise PermissionError("Campaign objective access denied")

    @classmethod
    def _kp(cls, campaign_id: str, identity: AuthenticatedMember) -> None:
        cls._scope(campaign_id, identity)
        if identity.role != "kp":
            raise PermissionError("KP access required")

    @staticmethod
    def _command_id(value: str) -> str:
        value = value.strip()
        if not 8 <= len(value) <= 200:
            raise InvalidInputError("Objective command id must contain 8-200 characters")
        return value

    @staticmethod
    def _text(value: str, label: str, limit: int) -> str:
        value = value.strip()
        if len(value) > limit:
            raise InvalidInputError(f"{label} exceeds {limit} characters")
        return value


__all__ = [
    "CampaignObjectiveService",
    "CreateObjectiveCommand",
    "ObjectiveStatus",
    "UpdateObjectiveCommand",
]

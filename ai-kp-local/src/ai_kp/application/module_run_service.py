"""Use cases for selecting the authoritative module and spoiler scope of a campaign."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ai_kp.application.errors import ConflictError, InvalidInputError
from ai_kp.application.ports.repositories import ModuleRunStore


@dataclass(frozen=True)
class StartModuleRunCommand:
    module_id: str
    current_scene_key: str | None = None
    active_spoiler_tags: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)


class ModuleRunService:
    def __init__(self, repo: ModuleRunStore):
        self.repo = repo

    def start(
        self,
        campaign_id: str,
        command: StartModuleRunCommand,
        *,
        member_id: str,
    ) -> dict:
        try:
            return self.repo.start_campaign_module_run(
                campaign_id=campaign_id,
                module_id=command.module_id,
                current_scene_key=command.current_scene_key,
                active_spoiler_tags=command.active_spoiler_tags,
                state=command.state,
                started_by_member_id=member_id,
            )
        except (TypeError, ValueError) as exc:
            raise InvalidInputError(str(exc)) from exc

    def update(self, run_id: str, changes: dict[str, Any]) -> dict:
        try:
            return self.repo.update_campaign_module_run(run_id, changes)
        except ValueError as exc:
            if str(exc).startswith("Module run changed;"):
                raise ConflictError(str(exc)) from exc
            raise InvalidInputError(str(exc)) from exc
        except TypeError as exc:
            raise InvalidInputError(str(exc)) from exc

"""Fail-closed authorization snapshots for every game-director model call."""

from dataclasses import dataclass

from ai_kp.application.errors import ConflictError
from ai_kp.application.ports.ai_control import AiControlStore


@dataclass(frozen=True)
class AiControlSnapshot:
    campaign_id: str
    operation: str
    run_id: str | None
    run_version: int | None
    model_configuration_version: int
    allowed_modes: tuple[str, ...] = ("ai_assist",)


class AiControlService:
    def __init__(self, repo: AiControlStore):
        self.repo = repo

    def authorize(self, campaign_id: str, operation: str) -> AiControlSnapshot:
        return self._authorize(
            campaign_id,
            operation,
            allowed_modes=("ai_assist",),
        )

    def authorize_kp_help(
        self, campaign_id: str, operation: str = "human KP help"
    ) -> AiControlSnapshot:
        """Authorize an explicit, read-only KP request without enabling automation."""

        return self._authorize(
            campaign_id,
            operation,
            allowed_modes=("ai_assist", "human_kp"),
        )

    def _authorize(
        self,
        campaign_id: str,
        operation: str,
        *,
        allowed_modes: tuple[str, ...],
    ) -> AiControlSnapshot:
        run = self.repo.get_active_campaign_module_run(campaign_id)
        model_configuration = self.repo.get_model_configuration()
        if run is not None and run.get("director_control_mode") not in allowed_modes:
            raise ConflictError(self._blocked_message(run, operation))
        return AiControlSnapshot(
            campaign_id=campaign_id,
            operation=operation,
            run_id=str(run["id"]) if run else None,
            run_version=int(run["version"]) if run else None,
            model_configuration_version=self._model_version(model_configuration),
            allowed_modes=allowed_modes,
        )

    def revalidate(self, snapshot: AiControlSnapshot) -> None:
        run = self.repo.get_active_campaign_module_run(snapshot.campaign_id)
        if self._model_version(self.repo.get_model_configuration()) != (
            snapshot.model_configuration_version
        ):
            raise ConflictError(
                f"Model configuration changed while {snapshot.operation} was running"
            )
        if snapshot.run_id is None:
            if run is None:
                return
            raise ConflictError(
                f"Campaign AI control changed while {snapshot.operation} was running"
            )
        if (
            run is None
            or str(run["id"]) != snapshot.run_id
            or int(run["version"]) != snapshot.run_version
            or run.get("director_control_mode") not in snapshot.allowed_modes
        ):
            raise ConflictError(
                f"Campaign AI control changed while {snapshot.operation} was running"
            )

    @staticmethod
    def _model_version(configuration: dict | None) -> int:
        return int(configuration["version"]) if configuration is not None else 0

    @staticmethod
    def _blocked_message(run: dict, operation: str) -> str:
        mode = str(run.get("director_control_mode") or "safety_paused")
        reason = str(run.get("director_control_reason") or "").strip()
        suffix = f": {reason}" if reason else ""
        return f"{operation} is blocked while campaign AI mode is {mode}{suffix}"


__all__ = ["AiControlService", "AiControlSnapshot"]

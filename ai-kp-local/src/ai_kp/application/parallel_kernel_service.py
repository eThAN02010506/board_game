"""Atomic application boundary for authoritative parallel kernel settlement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_kp.application.scenario_authority import load_scenario_authority_context
from ai_kp.application.scenario_effect_service import ScenarioEffectService
from ai_kp.platform.resolution.parallel import (
    ParallelActionKernel,
    ParallelSettlementPreview,
    ParallelSettlementRequest,
)
from ai_kp.platform.sessions.models import AuthenticatedMember


@dataclass(frozen=True)
class ParallelKernelCommitResult:
    preview: ParallelSettlementPreview
    batch: dict[str, Any] | None


class ParallelKernelService:
    def __init__(self, repo: Any):
        self.repo = repo

    def initialize_run(
        self, run_id: str, *, actor_locations: dict[str, str] | None = None
    ) -> dict[str, Any]:
        return self.repo.initialize_scenario_run_state(
            run_id, actor_locations=actor_locations
        )

    def settle(
        self,
        run_id: str,
        request: ParallelSettlementRequest,
        *,
        idempotency_key: str,
        identity: AuthenticatedMember | None = None,
    ) -> ParallelKernelCommitResult:
        replay = self._idempotent_replay(
            run_id,
            request,
            idempotency_key=idempotency_key,
            identity=identity,
        )
        if replay is not None:
            return replay

        self.repo.begin_immediate()
        # The initial read can race another connection that commits the same key
        # while this request waits for SQLite's write lock.  Re-read after the
        # lock so a legitimate concurrent retry replays instead of recomputing
        # against the already-advanced scenario state.
        replay = self._idempotent_replay(
            run_id,
            request,
            idempotency_key=idempotency_key,
            identity=identity,
        )
        if replay is not None:
            return replay
        self.repo.begin_scenario_command_batch()
        try:
            authority = load_scenario_authority_context(
                self.repo,
                run_id,
                state_policy="require",
            )
            preview = ParallelActionKernel(authority.contract).preview(
                authority.snapshot,
                request,
            )
            batch = None
            if preview.status == "ready":
                batch = self.repo.commit_parallel_scenario_batch(
                    run_id=run_id,
                    idempotency_key=idempotency_key,
                    preview=preview,
                    authority_basis=authority.kernel_authority_basis(
                        preview.settlement_hash
                    ),
                )
                self._apply_ruleset_effects(run_id, batch, identity)
            self.repo.finish_scenario_command_batch()
            return ParallelKernelCommitResult(preview=preview, batch=batch)
        except Exception:
            self.repo.rollback_scenario_command_batch()
            raise

    def _idempotent_replay(
        self,
        run_id: str,
        request: ParallelSettlementRequest,
        *,
        idempotency_key: str,
        identity: AuthenticatedMember | None,
    ) -> ParallelKernelCommitResult | None:
        repeated = self.repo.get_scenario_command_batch_by_key(
            run_id, idempotency_key
        )
        if repeated is None:
            return None
        if repeated["batch_kind"] != "parallel":
            raise ValueError("Idempotency key belongs to a different settlement kind")
        expected_request_hash = ParallelActionKernel.request_hash(request)
        recorded_request_hash = repeated["preview"].request_hash
        if not recorded_request_hash:
            raise ValueError(
                "Persisted parallel settlement predates request-bound "
                "idempotency and cannot be replayed automatically"
            )
        if recorded_request_hash != expected_request_hash:
            raise ValueError(
                "Idempotency key belongs to a different parallel settlement"
            )
        self._apply_ruleset_effects(run_id, repeated, identity)
        return ParallelKernelCommitResult(
            preview=repeated["preview"], batch=repeated
        )

    def _apply_ruleset_effects(
        self,
        run_id: str,
        batch: dict[str, Any],
        identity: AuthenticatedMember | None,
    ) -> None:
        if not any(
            item.get("kind") in {"apply_ruleset_effect", "set_world_entity_state"}
            for item in batch["commands"]
        ):
            return
        if identity is None:
            raise PermissionError(
                "Parallel ruleset effects require campaign KP authority"
            )
        run = self.repo.get_campaign_module_run(run_id)
        batch.update(ScenarioEffectService(self.repo).apply_batch(
            batch,
            identity,
            campaign_id=str(run["campaign_id"]),
        ))


__all__ = ["ParallelKernelCommitResult", "ParallelKernelService"]

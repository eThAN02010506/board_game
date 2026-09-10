"""Durable orchestration for independently planned simultaneous actions.

The language model may interpret and present each action, but it never owns the
confirmation barrier, dice, exact outcome, or atomic world commit.  This service
coordinates those deterministic boundaries over restart-safe repository state.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ai_kp.application.action_adjudication_service import (
    ActionAdjudicationService,
)
from ai_kp.application.kernel_action_service import KernelActionService
from ai_kp.application.parallel_action_attention_service import (
    ParallelActionAttentionService,
)
from ai_kp.application.parallel_action_planning_service import (
    ParallelActionPlanningService,
    ParallelSkillRepreview,
    UnsupportedParallelAction,
)
from ai_kp.application.parallel_action_workflow_authority import (
    ParallelActionWorkflowAuthority,
)
from ai_kp.application.parallel_action_workflow_finalizer import (
    ParallelActionWorkflowFinalizer,
)
from ai_kp.application.parallel_action_workflow_models import (
    ParallelActionPlanner,
    ParallelWorkflowResult,
    ParallelWorkflowStatus,
)
from ai_kp.application.parallel_kernel_service import ParallelKernelService
from ai_kp.application.ports.director import KernelDirector
from ai_kp.application.turn_service import TurnService
from ai_kp.platform.resolution import (
    build_check_consequence_snapshot,
    exact_kernel_outcome,
)
from ai_kp.platform.sessions.models import AuthenticatedMember


@dataclass(frozen=True)
class _AttentionRequired(Exception):
    reason: str
    unsupported: tuple[UnsupportedParallelAction, ...] = ()


class ParallelActionWorkflowService:
    """Coordinate planning, consent, checks, and one atomic kernel commit."""

    def __init__(
        self,
        repo: Any,
        *,
        planner: ParallelActionPlanner | None = None,
    ):
        self.repo = repo
        self.planner = planner or ParallelActionPlanningService(repo)
        self.turns = TurnService(repo)
        self.finalizer = ParallelActionWorkflowFinalizer(repo)
        self.authority = ParallelActionWorkflowAuthority(repo)
        self.attention = ParallelActionAttentionService(
            repo,
            authority=self.authority,
        )

    async def prepare(
        self,
        actions: Sequence[dict[str, Any]],
        identity: AuthenticatedMember,
        director: KernelDirector,
        *,
        idempotency_key: str,
        source_model: str,
        profile: str = "small",
    ) -> ParallelWorkflowResult:
        """Plan without a write lock, then persist every item or none of them."""

        self.authority.require_kp(identity)
        replay = self.authority.prepared_replay(
            actions,
            identity=identity,
            idempotency_key=idempotency_key,
        )
        if replay is not None:
            if (
                replay.status == "needs_attention"
                and replay.batch is not None
                and replay.batch["status"]
                in {
                    "awaiting_confirmation",
                    "awaiting_checks",
                    "ready",
                    "committing",
                }
            ):
                return self._request_attention_after_revalidation(
                    str(replay.batch["id"]),
                    reason=replay.message,
                    actor_member_id=identity.member_id,
                    allowed_statuses=(
                        "awaiting_confirmation",
                        "awaiting_checks",
                        "ready",
                        "committing",
                    ),
                )
            return replay
        authoritative_actions = self.authority.authorize_prepare_scope(
            actions, identity
        )
        planning = await self.planner.plan(
            authoritative_actions,
            director,
            source_model=source_model,
            profile=profile,
        )
        if (
            planning.campaign_id != identity.campaign_id
            or planning.session_id != identity.session_id
        ):
            raise PermissionError(
                "Parallel planning result belongs to a different KP session"
            )
        if not planning.ready:
            return ParallelWorkflowResult(
                status="needs_attention",
                batch=None,
                actions=authoritative_actions,
                unsupported=planning.unsupported,
                message=(
                    "At least one action needs an independent conversation, "
                    "clarification, or world-expansion workflow; nothing was written."
                ),
            )

        self.repo.begin_parallel_action_workflow()
        try:
            locked_replay = self.authority.prepared_replay(
                authoritative_actions,
                identity=identity,
                idempotency_key=idempotency_key,
            )
            if locked_replay is not None:
                self.repo.finish_parallel_action_workflow()
                return locked_replay
            kernel_actions = KernelActionService(self.repo)
            adjudications = ActionAdjudicationService(self.repo)
            durable_items: list[dict[str, Any]] = []
            for priority, prepared in enumerate(planning.prepared):
                action = self.repo.require_submitted_player_action(
                    prepared.action_id,
                    planning.campaign_id,
                    planning.session_id,
                )
                proposal, preview = kernel_actions.persist_prepared_parallel(
                    action,
                    identity,
                    prepared,
                )
                adjudication = adjudications.create(
                    action,
                    proposal,
                    source_model=f"kernel:{source_model}",
                    enforce_precheck=False,
                )
                if adjudication["mode"] == "roleplay_or_clarification":
                    raise _AttentionRequired(
                        "A planned action could not be mapped to a valid "
                        "player-owned confirmation choice."
                    )
                selected_skill_key = self.authority.selected_skill_key(
                    adjudication,
                    expected=preview.selected_skill_key,
                )
                durable_items.append(
                    {
                        "action_id": prepared.action_id,
                        "proposal_id": str(proposal["id"]),
                        "adjudication_id": str(adjudication["id"]),
                        "actor_id": prepared.actor_id,
                        "operator_id": prepared.operator_id,
                        "preview_hash": preview.preview_hash,
                        "selected_skill_key": selected_skill_key,
                        "priority": -priority,
                    }
                )
            batch = self.repo.create_parallel_action_batch(
                campaign_id=planning.campaign_id,
                session_id=planning.session_id,
                run_id=planning.run_id,
                module_run_version=planning.module_run_version,
                contract_version_id=planning.contract_version_id,
                base_state_version=planning.base_state_version,
                idempotency_key=idempotency_key,
                items=durable_items,
                created_by_member_id=identity.member_id,
            )
            self.repo.finish_parallel_action_workflow()
        except _AttentionRequired as exc:
            self.repo.rollback_parallel_action_workflow()
            return ParallelWorkflowResult(
                status="needs_attention",
                batch=None,
                actions=authoritative_actions,
                unsupported=exc.unsupported,
                message=exc.reason,
            )
        except Exception:
            self.repo.rollback_parallel_action_workflow()
            raise
        return self.authority.result(
            "awaiting_confirmation",
            batch,
            "Every player has an independent ruling and must confirm their own choice.",
        )

    def confirm_item(
        self,
        batch_id: str,
        action_id: str,
        *,
        expected_batch_version: int,
        expected_adjudication_version: int,
        selected_skill_key: str | None,
        identity: AuthenticatedMember,
    ) -> ParallelWorkflowResult:
        """Confirm one owned item; the last confirmation creates all checks."""

        self.authority.require_player(identity)
        initial = self.repo.get_parallel_action_batch(batch_id)
        item = self.authority.owned_item(
            initial,
            action_id,
            identity,
            allowed_action_statuses=("reviewed", "resolved"),
        )
        if initial["status"] == "settled":
            initial = self.repo.validate_parallel_action_batch_settlement(batch_id)
        active_statuses = {
            "awaiting_confirmation",
            "awaiting_checks",
            "ready",
            "committing",
        }
        if initial["status"] not in active_statuses:
            if self.authority.confirmation_is_replay(
                item,
                selected_skill_key=selected_skill_key,
            ):
                return self.authority.result(
                    self.authority.workflow_status(initial),
                    initial,
                    "Replayed the player's existing canonical confirmation.",
                )
            raise ValueError("Parallel confirmation is already closed")
        item = self.authority.owned_item(initial, action_id, identity)
        adjudication = item.get("adjudication") or {}
        mode = str(adjudication.get("mode") or "")
        repreview: ParallelSkillRepreview | None = None
        if mode == "skill_check":
            chosen = selected_skill_key or item.get("selected_skill_key")
            if not chosen:
                raise ValueError("A skill-check action requires a canonical skill key")
            if chosen != item.get("selected_skill_key"):
                # No model call and no write.  This can be reconstructed after a
                # process restart solely from durable authority.
                repreview = self.planner.repreview_persisted_skill(
                    batch_id,
                    action_id,
                    requested_skill_key=str(chosen),
                    actor_member_id=identity.member_id,
                )
        elif selected_skill_key is not None:
            raise ValueError("A direct-resolution action cannot select a skill")
        elif mode != "direct_resolution":
            raise ValueError(
                "Clarification actions cannot enter deterministic settlement"
            )

        self.repo.begin_parallel_action_workflow()
        try:
            batch = self.repo.get_parallel_action_batch(batch_id)
            if batch["status"] == "settled":
                batch = self.repo.validate_parallel_action_batch_settlement(batch_id)
            elif batch["status"] not in {
                "awaiting_confirmation",
                "awaiting_checks",
                "ready",
                "committing",
            }:
                locked_item = self.authority.owned_item(
                    batch,
                    action_id,
                    identity,
                    allowed_action_statuses=("reviewed", "resolved"),
                )
                if not self.authority.confirmation_is_replay(
                    locked_item,
                    selected_skill_key=selected_skill_key,
                ):
                    raise ValueError("Parallel confirmation is already closed")
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    self.authority.workflow_status(batch),
                    batch,
                    "Replayed the player's existing canonical confirmation.",
                )
            else:
                try:
                    batch = self.repo.validate_parallel_action_batch_authority(
                        batch_id
                    )
                except (KeyError, PermissionError, TypeError, ValueError) as exc:
                    reason = (
                        "Parallel batch authority changed during confirmation: "
                        f"{exc}"
                    )
                    batch = self.repo.transition_parallel_action_batch(
                        batch_id,
                        "request_attention",
                        expected_version=int(batch["version"]),
                        actor_member_id=identity.member_id,
                        reason=reason[:2000],
                    )
                    self.repo.finish_parallel_action_workflow()
                    return self.authority.result(
                        "needs_attention", batch, reason
                    )
            locked_item = self.authority.owned_item(
                batch,
                action_id,
                identity,
                allowed_action_statuses=("reviewed", "resolved"),
            )
            if self.authority.confirmation_is_replay(
                locked_item,
                selected_skill_key=selected_skill_key,
            ):
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    self.authority.workflow_status(batch),
                    batch,
                    "Replayed the player's existing canonical confirmation.",
                )
            if batch["status"] != "awaiting_confirmation":
                raise ValueError("Parallel confirmation is already closed")
            if batch["version"] != expected_batch_version:
                raise ValueError(
                    "Parallel action batch changed; refresh before confirming"
                )
            item = self.authority.owned_item(batch, action_id, identity)
            if repreview is not None:
                rebound = self.repo.rebind_parallel_action_item_skill(
                    batch_id,
                    action_id,
                    expected_batch_version=expected_batch_version,
                    expected_adjudication_version=expected_adjudication_version,
                    selected_skill_key=repreview.selected_skill_key,
                    preview=repreview.preview.model_dump(mode="json"),
                    narrative=repreview.narrative.model_dump(mode="json"),
                    actor_member_id=identity.member_id,
                )
                batch = rebound["batch"]
                adjudication = rebound["adjudication"]
                expected_adjudication_version = int(adjudication["version"])
            else:
                adjudication = self.repo.get_action_adjudication(action_id)
                if adjudication["id"] != item["adjudication_id"]:
                    raise ValueError("Parallel adjudication binding changed")

            self.repo.confirm_action_adjudication(
                action_id,
                expected_version=expected_adjudication_version,
                actor_member_id=identity.member_id,
            )
            batch = self.repo.get_parallel_action_batch(batch_id)
            if any(
                (candidate.get("adjudication") or {}).get("status") == "pending"
                for candidate in batch["items"]
            ):
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    "awaiting_confirmation",
                    batch,
                    "This choice is confirmed; the other players still need to confirm.",
                )

            coordinator = self.authority.system_kp_identity(batch)
            checked = [
                candidate
                for candidate in batch["items"]
                if candidate["adjudication"]["mode"] == "skill_check"
            ]
            for candidate in batch["items"]:
                if candidate["adjudication"]["mode"] == "direct_resolution":
                    if candidate.get("outcome_key") is None:
                        batch = self.repo.record_parallel_action_item_decision(
                            batch_id,
                            str(candidate["action_id"]),
                            expected_version=int(batch["version"]),
                            outcome_key="success",
                            actor_member_id=None,
                        )
                    continue
                proposal = self.repo.get_turn_proposal(
                    str(candidate["proposal_id"])
                )
                if proposal["status"] != "draft":
                    raise ValueError(
                        "Parallel check proposal changed before the confirmation barrier"
                    )
                self.turns.approve_parallel_proposal(
                    str(candidate["proposal_id"]),
                    str(batch["campaign_id"]),
                    coordinator,
                    batch_id=batch_id,
                    phase="check_request",
                    note="all parallel players confirmed their independent rulings",
                )
            batch = self.repo.get_parallel_action_batch(batch_id)
            event = (
                "confirmations_completed_with_checks"
                if checked
                else "confirmations_completed_without_checks"
            )
            batch = self.repo.transition_parallel_action_batch(
                batch_id,
                event,
                expected_version=int(batch["version"]),
                actor_member_id=None,
            )
            self.repo.finish_parallel_action_workflow()
        except Exception:
            self.repo.rollback_parallel_action_workflow()
            raise
        status: ParallelWorkflowStatus = (
            "awaiting_checks" if checked else "ready"
        )
        return self.authority.result(
            status,
            batch,
            (
                "All choices are confirmed; resolve each player's assigned check."
                if checked
                else "All automatic actions are confirmed and ready for atomic commit."
            ),
        )

    def observe_terminal_check(self, check_id: str) -> ParallelWorkflowResult | None:
        """Record exact terminal outcomes and release the all-checks barrier."""

        check = self.repo.get_skill_check(check_id)
        action_id = check.get("player_action_id")
        if not action_id:
            return None
        batch = self.repo.get_active_parallel_action_batch_for_action(str(action_id))
        if batch is None:
            return None
        if batch["status"] not in {"awaiting_checks", "ready"}:
            return self.authority.result(
                "needs_attention",
                batch,
                f"Parallel batch cannot observe checks while {batch['status']}.",
            )

        self.repo.begin_parallel_action_workflow()
        try:
            batch = self.repo.get_parallel_action_batch(str(batch["id"]))
            if batch["status"] == "settled":
                batch = self.repo.validate_parallel_action_batch_settlement(
                    str(batch["id"])
                )
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    "settled", batch, "Parallel batch is already settled."
                )
            if batch["status"] not in {"awaiting_checks", "ready"}:
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    self.authority.workflow_status(batch),
                    batch,
                    f"Parallel batch cannot observe checks while {batch['status']}.",
                )
            authority_error = self.authority.execution_authority_error(batch)
            if authority_error:
                batch = self.repo.transition_parallel_action_batch(
                    str(batch["id"]),
                    "request_attention",
                    expected_version=int(batch["version"]),
                    actor_member_id=None,
                    reason=authority_error[:2000],
                )
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    "needs_attention", batch, authority_error
                )
            if batch["status"] == "ready":
                drift = self.authority.check_result_drift_reason(batch)
                if drift:
                    batch = self.repo.transition_parallel_action_batch(
                        str(batch["id"]),
                        "request_attention",
                        expected_version=int(batch["version"]),
                        actor_member_id=None,
                        reason=drift[:2000],
                    )
                    self.repo.finish_parallel_action_workflow()
                    return self.authority.result(
                        "needs_attention", batch, drift
                    )
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    "ready", batch, "Parallel check results are still authoritative."
                )
            attention = self.authority.check_attention_reason(batch)
            if attention:
                batch = self.repo.transition_parallel_action_batch(
                    str(batch["id"]),
                    "request_attention",
                    expected_version=int(batch["version"]),
                    actor_member_id=None,
                    reason=attention,
                )
                self.repo.finish_parallel_action_workflow()
                return self.authority.result("needs_attention", batch, attention)
            if self.authority.checks_are_waiting(batch):
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    "awaiting_checks",
                    batch,
                    "At least one roll or push decision is still pending.",
                )

            for item in batch["items"]:
                if item["adjudication"]["mode"] != "skill_check":
                    continue
                snapshot = build_check_consequence_snapshot(item["checks"])
                fingerprint = str(snapshot["result_fingerprint"])
                outcome = exact_kernel_outcome(item["preview"], item["checks"])
                if (
                    item.get("check_result_fingerprint") == fingerprint
                    and item.get("outcome_key") == outcome
                ):
                    continue
                batch = self.repo.record_parallel_action_item_decision(
                    str(batch["id"]),
                    str(item["action_id"]),
                    expected_version=int(batch["version"]),
                    outcome_key=outcome,
                    check_result_fingerprint=fingerprint,
                    actor_member_id=None,
                )
            batch = self.repo.transition_parallel_action_batch(
                str(batch["id"]),
                "checks_completed",
                expected_version=int(batch["version"]),
                actor_member_id=None,
            )
            self.repo.finish_parallel_action_workflow()
        except (TypeError, ValueError) as exc:
            self.repo.rollback_parallel_action_workflow()
            return self._request_attention_after_revalidation(
                str(batch["id"]),
                reason=f"Check result needs deterministic review: {exc}",
                actor_member_id=None,
                allowed_statuses=("awaiting_checks", "ready"),
            )
        except Exception:
            self.repo.rollback_parallel_action_workflow()
            raise
        return self.authority.result(
            "ready", batch, "Every exact check outcome is ready for atomic commit."
        )

    def commit(
        self,
        batch_id: str,
        *,
        expected_version: int,
    ) -> ParallelWorkflowResult:
        """Commit kernel state and finalize every proposal in one savepoint."""

        initial = self.repo.get_parallel_action_batch(batch_id)
        if initial["status"] == "settled":
            initial = self.repo.validate_parallel_action_batch_settlement(batch_id)
            return self.authority.result(
                "settled", initial, "Parallel batch is already settled."
            )
        if initial["status"] != "ready":
            raise ValueError("Only a ready parallel batch can be committed")

        self.repo.begin_parallel_action_workflow()
        try:
            batch = self.repo.get_parallel_action_batch(batch_id)
            if batch["status"] == "settled":
                batch = self.repo.validate_parallel_action_batch_settlement(batch_id)
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    "settled", batch, "Parallel batch is already settled."
                )
            if batch["status"] != "ready":
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    self.authority.workflow_status(batch),
                    batch,
                    f"Parallel batch cannot commit while {batch['status']}.",
                )
            batch = self.repo.validate_parallel_action_batch_authority(batch_id)
            if batch["status"] == "ready" and batch["version"] != expected_version:
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    "ready",
                    batch,
                    "Parallel batch changed; refresh before committing.",
                )
            coordinator = self.authority.system_kp_identity(batch)
            batch = self.repo.transition_parallel_action_batch(
                batch_id,
                "begin_commit",
                expected_version=expected_version,
                actor_member_id=None,
            )
            request = self.authority.settlement_request(batch)
            result = ParallelKernelService(self.repo).settle(
                str(batch["run_id"]),
                request,
                idempotency_key=f"parallel-batch:{batch_id}",
                identity=coordinator,
            )
            if result.preview.status != "ready" or result.batch is None:
                detail = ", ".join(result.preview.conflicts) or result.preview.status
                raise _AttentionRequired(
                    f"Atomic parallel preflight did not become ready: {detail}"
                )
            for item in batch["items"]:
                self.finalizer.finalize_item(
                    item,
                    batch=batch,
                    coordinator=coordinator,
                )
            batch = self.repo.transition_parallel_action_batch(
                batch_id,
                "commit_succeeded",
                expected_version=int(batch["version"]),
                actor_member_id=None,
                settlement_hash=result.preview.settlement_hash,
                scenario_command_batch_id=str(result.batch["id"]),
            )
            self.repo.finish_parallel_action_workflow()
        except (
            _AttentionRequired,
            KeyError,
            PermissionError,
            RuntimeError,
            sqlite3.DatabaseError,
            TypeError,
            ValueError,
        ) as exc:
            self.repo.rollback_parallel_action_workflow()
            return self._request_attention_after_revalidation(
                batch_id,
                reason=f"Atomic parallel commit rolled back: {exc}",
                actor_member_id=None,
                allowed_statuses=("ready", "committing"),
            )
        except Exception:
            self.repo.rollback_parallel_action_workflow()
            raise
        return self.authority.result(
            "settled", batch, "All simultaneous actions committed atomically."
        )

    def request_revision(
        self,
        batch_id: str,
        action_id: str,
        *,
        expected_version: int,
        identity: AuthenticatedMember,
        reason: str = "Player requested a revised parallel action.",
    ) -> ParallelWorkflowResult:
        """Withdraw consent by permanently superseding the whole old batch."""

        self.authority.require_player(identity)
        batch = self.repo.get_parallel_action_batch(batch_id)
        self.authority.owned_item(
            batch,
            action_id,
            identity,
            allowed_action_statuses=("reviewed", "rejected"),
        )
        if batch["status"] == "superseded":
            self.authority.require_complete_superseded_disposition(batch)
            return self.authority.result(
                "needs_attention",
                batch,
                "Replayed the completed whole-batch withdrawal.",
            )
        if batch["status"] != "awaiting_confirmation":
            raise ValueError(
                "Parallel actions can be revised only before every player confirms"
            )
        self.repo.begin_parallel_action_workflow()
        try:
            batch = self.repo.get_parallel_action_batch(batch_id)
            self.authority.owned_item(
                batch,
                action_id,
                identity,
                allowed_action_statuses=("reviewed", "rejected"),
            )
            if batch["status"] == "superseded":
                self.authority.require_complete_superseded_disposition(batch)
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    "needs_attention",
                    batch,
                    "Replayed the completed whole-batch withdrawal.",
                )
            if batch["status"] != "awaiting_confirmation":
                raise ValueError(
                    "Parallel actions can be revised only before every player confirms"
                )
            batch = self.repo.transition_parallel_action_batch(
                batch_id,
                "supersede",
                expected_version=expected_version,
                actor_member_id=identity.member_id,
                reason=reason,
            )
            batch = self.repo.dispose_superseded_parallel_actions(
                batch_id,
                actor_member_id=identity.member_id,
                reason=f"{reason} Requested by member {identity.member_id}.",
            )
            self.repo.finish_parallel_action_workflow()
        except Exception:
            self.repo.rollback_parallel_action_workflow()
            raise
        return self.authority.result(
            "needs_attention",
            batch,
            "The old batch was safely withdrawn; players must submit a fresh action batch.",
        )

    def resume_needs_attention(
        self,
        batch_id: str,
        *,
        expected_version: int,
        identity: AuthenticatedMember,
        reason: str = "KP validated the durable parallel-action barrier.",
    ) -> ParallelWorkflowResult:
        """Delegate trusted recovery to the narrow attention coordinator."""

        return self.attention.resume(
            batch_id,
            expected_version=expected_version,
            identity=identity,
            reason=reason,
        )

    def abandon_needs_attention(
        self,
        batch_id: str,
        *,
        expected_version: int,
        identity: AuthenticatedMember,
        reason: str = "KP abandoned an unrecoverable parallel-action batch.",
    ) -> ParallelWorkflowResult:
        """Delegate terminal disposal to the narrow attention coordinator."""

        return self.attention.abandon(
            batch_id,
            expected_version=expected_version,
            identity=identity,
            reason=reason,
        )

    def _request_attention_after_revalidation(
        self,
        batch_id: str,
        *,
        reason: str,
        actor_member_id: str | None,
        allowed_statuses: tuple[str, ...],
    ) -> ParallelWorkflowResult:
        """Persist attention only after a fresh read under the workflow write lock."""

        self.repo.begin_parallel_action_workflow()
        try:
            current = self.repo.get_parallel_action_batch(batch_id)
            if current["status"] == "settled":
                current = self.repo.validate_parallel_action_batch_settlement(batch_id)
                self.repo.finish_parallel_action_workflow()
                return self.authority.result(
                    "settled", current, "Parallel batch is already settled."
                )
            if current["status"] in allowed_statuses:
                current = self.repo.transition_parallel_action_batch(
                    batch_id,
                    "request_attention",
                    expected_version=int(current["version"]),
                    actor_member_id=actor_member_id,
                    reason=reason[:2000],
                )
            self.repo.finish_parallel_action_workflow()
        except Exception:
            self.repo.rollback_parallel_action_workflow()
            raise
        return self.authority.result(
            self.authority.workflow_status(current),
            current,
            str(current.get("attention_reason") or reason),
        )


__all__ = [
    "ParallelActionPlanner",
    "ParallelActionWorkflowService",
    "ParallelWorkflowResult",
    "ParallelWorkflowStatus",
]

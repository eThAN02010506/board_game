"""Durable scope, ownership, and request reconstruction for parallel actions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ai_kp.application.parallel_action_workflow_models import (
    ParallelWorkflowResult,
    ParallelWorkflowStatus,
)
from ai_kp.platform.resolution import (
    build_check_consequence_snapshot,
    exact_kernel_outcome,
    has_pending_push_decision,
)
from ai_kp.platform.resolution.parallel import (
    ParallelIntent,
    ParallelSettlementRequest,
)
from ai_kp.platform.resolution.parallel_batch_state import ParallelBatchEvent
from ai_kp.platform.sessions.models import AuthenticatedMember


class ParallelActionWorkflowAuthority:
    """Read and validate only authority that survives process restarts."""

    def __init__(self, repo: Any):
        self.repo = repo

    @staticmethod
    def selected_skill_key(
        adjudication: dict[str, Any],
        *,
        expected: str | None,
    ) -> str | None:
        if adjudication["mode"] == "direct_resolution":
            if expected is not None:
                raise ValueError("Direct resolution unexpectedly selected a skill")
            return None
        matches = [
            item
            for item in adjudication["skill_options"]
            if item.get("skill_name") == adjudication.get("selected_skill")
        ]
        if (
            len(matches) != 1
            or matches[0].get("skill_key") != expected
            or expected is None
        ):
            raise ValueError(
                "Player adjudication cannot be bound to the kernel's canonical skill"
            )
        return expected

    def owned_item(
        self,
        batch: dict[str, Any],
        action_id: str,
        identity: AuthenticatedMember,
        *,
        allowed_action_statuses: tuple[str, ...] = ("reviewed",),
    ) -> dict[str, Any]:
        if (
            identity.campaign_id != batch["campaign_id"]
            or identity.session_id != batch["session_id"]
        ):
            raise PermissionError("Parallel batch belongs to another session")
        item = next(
            (
                candidate
                for candidate in batch["items"]
                if candidate["action_id"] == action_id
            ),
            None,
        )
        if item is None:
            raise KeyError(f"Parallel batch action not found: {action_id}")
        member = self.repo.get_session_member(identity.member_id)
        action = self.repo.get_player_action(action_id)
        if (
            member.get("campaign_id") != batch["campaign_id"]
            or member.get("session_id") != batch["session_id"]
            or member.get("role") != "player"
            or member.get("revoked_at") is not None
            or action.get("campaign_id") != batch["campaign_id"]
            or action.get("session_id") != batch["session_id"]
            or action.get("member_id") != identity.member_id
            or action.get("proposal_id") != item["proposal_id"]
            or action.get("status") not in allowed_action_statuses
            or str(action.get("pc_id") or action["member_id"]) != item["actor_id"]
        ):
            raise PermissionError(
                "Players can confirm or revise only their own parallel action"
            )
        return item

    def confirmation_is_replay(
        self,
        item: dict[str, Any],
        *,
        selected_skill_key: str | None,
    ) -> bool:
        """Recognize only a confirmation that matches durable player consent."""

        adjudication = item.get("adjudication") or {}
        if adjudication.get("status") != "confirmed":
            return False
        mode = str(adjudication.get("mode") or "")
        if mode == "direct_resolution":
            if selected_skill_key is not None:
                raise ValueError("A direct-resolution action cannot select a skill")
            return True
        if mode != "skill_check":
            raise ValueError(
                "Clarification actions cannot enter deterministic settlement"
            )
        durable_skill = item.get("selected_skill_key")
        chosen = selected_skill_key or durable_skill
        if not durable_skill or chosen != durable_skill:
            raise ValueError(
                "Confirmed parallel skill differs from the requested replay"
            )
        options = [
            option
            for option in adjudication.get("skill_options") or ()
            if isinstance(option, dict)
            and option.get("skill_name") == adjudication.get("selected_skill")
            and option.get("skill_key") == durable_skill
        ]
        if len(options) != 1:
            raise ValueError("Confirmed parallel skill lost its canonical binding")
        return True

    def execution_authority_error(self, batch: dict[str, Any]) -> str:
        try:
            self.repo.validate_parallel_action_batch_authority(str(batch["id"]))
        except (KeyError, PermissionError, TypeError, ValueError) as exc:
            return f"Parallel batch authority changed: {exc}"
        return ""

    @staticmethod
    def workflow_status(batch: dict[str, Any]) -> ParallelWorkflowStatus:
        status_map: dict[str, ParallelWorkflowStatus] = {
            "awaiting_confirmation": "awaiting_confirmation",
            "awaiting_checks": "awaiting_checks",
            "ready": "ready",
            "settled": "settled",
            "committing": "needs_attention",
            "needs_attention": "needs_attention",
            "superseded": "needs_attention",
        }
        return status_map[str(batch["status"])]

    def system_kp_identity(self, batch: dict[str, Any]) -> AuthenticatedMember:
        row = self.repo.connection.execute(
            """
            SELECT id, session_id, campaign_id, display_name
            FROM session_members
            WHERE session_id = ? AND campaign_id = ? AND role = 'kp'
              AND revoked_at IS NULL
            ORDER BY joined_at, id LIMIT 1
            """,
            (batch["session_id"], batch["campaign_id"]),
        ).fetchone()
        if row is None:
            raise ValueError("No active KP member exists for parallel coordination")
        return AuthenticatedMember(
            member_id=str(row["id"]),
            session_id=str(row["session_id"]),
            campaign_id=str(row["campaign_id"]),
            role="kp",
            display_name=f"{row['display_name']} · Auto KP",
            pc_id=None,
        )

    def require_active_kp_for_batch(
        self,
        identity: AuthenticatedMember,
        batch: dict[str, Any],
    ) -> None:
        """Authorize a human KP or the existing active-KP coordinator identity."""

        member = self.repo.get_session_member(identity.member_id)
        if (
            identity.role != "kp"
            or identity.campaign_id != batch["campaign_id"]
            or identity.session_id != batch["session_id"]
            or member.get("campaign_id") != batch["campaign_id"]
            or member.get("session_id") != batch["session_id"]
            or member.get("role") != "kp"
            or member.get("revoked_at") is not None
        ):
            raise PermissionError(
                "Parallel recovery requires the active KP for this session"
            )

    def check_attention_reason(self, batch: dict[str, Any]) -> str:
        for item in batch["items"]:
            if item["adjudication"]["mode"] != "skill_check":
                continue
            if any(check["status"] == "cancelled" for check in item["checks"]):
                return "A parallel check was cancelled and cannot authorize an outcome."
            if self.repo.list_opposed_checks_for_action(str(item["action_id"])):
                return (
                    "Opposed parallel checks need explicit contest-side binding "
                    "before deterministic settlement."
                )
        return ""

    @staticmethod
    def checks_are_waiting(batch: dict[str, Any]) -> bool:
        for item in batch["items"]:
            if item["adjudication"]["mode"] != "skill_check":
                continue
            checks = item["checks"]
            if not checks or any(check["status"] == "requested" for check in checks):
                return True
            if has_pending_push_decision(checks):
                return True
        return False

    def recovery_event(
        self,
        batch: dict[str, Any],
    ) -> tuple[ParallelBatchEvent, ParallelWorkflowStatus, str]:
        """Infer the only safe resume target from durable consent/check barriers."""

        if batch["status"] != "needs_attention":
            raise ValueError("Only a needs-attention parallel batch can be resumed")
        adjudications = [item.get("adjudication") or {} for item in batch["items"]]
        statuses = {str(item.get("status") or "") for item in adjudications}
        if not statuses.issubset({"pending", "confirmed"}):
            raise ValueError(
                "Parallel adjudications are not recoverable from their durable state"
            )
        if "pending" in statuses:
            if any(item.get("checks") for item in batch["items"]) or any(
                item.get("outcome_key") is not None
                or item.get("check_result_fingerprint") is not None
                for item in batch["items"]
            ):
                raise ValueError(
                    "Pending consent conflicts with already-bound parallel results"
                )
            return (
                "resume_confirmations",
                "awaiting_confirmation",
                "Durable player confirmations are incomplete.",
            )
        if statuses != {"confirmed"}:
            raise ValueError("Parallel batch lost its durable adjudications")

        checked = [
            item
            for item in batch["items"]
            if (item.get("adjudication") or {}).get("mode") == "skill_check"
        ]
        if not checked:
            return (
                "resume_ready",
                "ready",
                "All confirmed direct outcomes remain authoritative.",
            )
        if any(not item.get("checks") for item in checked):
            raise ValueError(
                "A confirmed skill action lost its authoritative check request"
            )
        if any(
            (item.get("adjudication") or {}).get("mode") == "direct_resolution"
            and item.get("outcome_key") is None
            for item in batch["items"]
        ):
            raise ValueError("A confirmed direct action lost its exact outcome")
        attention = self.check_attention_reason(batch)
        if attention:
            raise ValueError(attention)
        if self.checks_are_waiting(batch) or self.check_result_drift_reason(batch):
            return (
                "resume_checks",
                "awaiting_checks",
                "Checks, push decisions, or exact result bindings remain incomplete.",
            )
        return (
            "resume_ready",
            "ready",
            "Every exact check outcome and fingerprint remains authoritative.",
        )

    def check_result_drift_reason(self, batch: dict[str, Any]) -> str:
        """Detect a check override/cancellation after the result barrier opened."""

        attention = self.check_attention_reason(batch)
        if attention:
            return attention
        if self.checks_are_waiting(batch):
            return "A parallel check or push decision is no longer terminal."
        try:
            for item in batch["items"]:
                if item["adjudication"]["mode"] != "skill_check":
                    continue
                snapshot = build_check_consequence_snapshot(item["checks"])
                fingerprint = str(snapshot["result_fingerprint"])
                outcome = exact_kernel_outcome(item["preview"], item["checks"])
                if (
                    item.get("check_result_fingerprint") != fingerprint
                    or item.get("outcome_key") != outcome
                ):
                    return (
                        "A parallel check result changed after the result barrier; "
                        "the batch must be reviewed before settlement."
                    )
        except (KeyError, TypeError, ValueError) as exc:
            return f"Parallel check authority changed: {exc}"
        return ""

    def require_complete_superseded_disposition(
        self, batch: dict[str, Any]
    ) -> None:
        if batch["status"] != "superseded":
            raise ValueError("Parallel batch is not superseded")
        for item in batch["items"]:
            action = self.repo.get_player_action(str(item["action_id"]))
            proposal = self.repo.get_turn_proposal(str(item["proposal_id"]))
            if (
                action["status"] != "rejected"
                or item["adjudication"]["status"] != "superseded"
                or proposal["status"] == "draft"
                or any(check["status"] == "requested" for check in item["checks"])
            ):
                raise ValueError(
                    "Superseded parallel batch has an incomplete terminal disposition"
                )

    def settlement_request(
        self, batch: dict[str, Any]
    ) -> ParallelSettlementRequest:
        intents: list[ParallelIntent] = []
        for item in batch["items"]:
            action = self.repo.get_player_action(str(item["action_id"]))
            if (
                action.get("campaign_id") != batch["campaign_id"]
                or action.get("session_id") != batch["session_id"]
                or action.get("proposal_id") != item["proposal_id"]
                or action.get("status") != "reviewed"
                or str(action.get("pc_id") or action["member_id"])
                != item["actor_id"]
            ):
                raise ValueError(
                    "Player action changed before parallel settlement request"
                )
            intents.append(
                ParallelIntent(
                    action_id=str(item["action_id"]),
                    actor_id=str(item["actor_id"]),
                    operator_id=str(item["operator_id"]),
                    goal=str(action["action_text"]),
                    requested_skill_key=item.get("selected_skill_key"),
                    outcome=str(item["outcome_key"]),
                    priority=int(item["priority"]),
                )
            )
        return ParallelSettlementRequest(
            batch_id=str(batch["id"]),
            intents=tuple(intents),
        )

    def prepared_replay(
        self,
        actions: Sequence[dict[str, Any]],
        *,
        identity: AuthenticatedMember,
        idempotency_key: str,
    ) -> ParallelWorkflowResult | None:
        member = self.repo.get_session_member(identity.member_id)
        if (
            member.get("campaign_id") != identity.campaign_id
            or member.get("session_id") != identity.session_id
            or member.get("role") != "kp"
            or member.get("revoked_at") is not None
        ):
            raise PermissionError("Parallel replay requires the active session KP")
        try:
            batch = self.repo.get_parallel_action_batch_by_key(
                identity.campaign_id,
                idempotency_key,
            )
        except KeyError:
            return None
        if (
            batch["campaign_id"] != identity.campaign_id
            or batch["session_id"] != identity.session_id
        ):
            raise PermissionError("Parallel replay belongs to another KP session")
        requested_ids = [str(action.get("id") or "") for action in actions]
        durable_ids = [str(item["action_id"]) for item in batch["items"]]
        if (
            len(requested_ids) != len(set(requested_ids))
            or requested_ids != durable_ids
        ):
            raise ValueError(
                "Idempotency key belongs to a different parallel action set"
            )
        if batch["status"] == "settled":
            batch = self.repo.validate_parallel_action_batch_settlement(
                str(batch["id"])
            )
        authority_error = (
            self.execution_authority_error(batch)
            if batch["status"] in {
                "awaiting_confirmation",
                "awaiting_checks",
                "ready",
                "committing",
            }
            else ""
        )
        return self.result(
            (
                "needs_attention"
                if authority_error
                else self.workflow_status(batch)
            ),
            batch,
            authority_error
            or "Replayed the existing durable parallel workflow without another model call.",
        )

    def authorize_prepare_scope(
        self,
        actions: Sequence[dict[str, Any]],
        identity: AuthenticatedMember,
    ) -> tuple[dict[str, Any], ...]:
        member = self.repo.get_session_member(identity.member_id)
        if (
            member.get("campaign_id") != identity.campaign_id
            or member.get("session_id") != identity.session_id
            or member.get("role") != "kp"
            or member.get("revoked_at") is not None
        ):
            raise PermissionError("Parallel preparation requires the active session KP")
        if not 2 <= len(actions) <= 12:
            raise ValueError("Parallel preparation requires between 2 and 12 actions")
        action_ids = [str(action.get("id") or "") for action in actions]
        if any(not action_id for action_id in action_ids):
            raise ValueError("Every parallel action requires an ID")
        authoritative = tuple(
            self.repo.get_player_action(action_id) for action_id in action_ids
        )
        if any(
            action.get("campaign_id") != identity.campaign_id
            or action.get("session_id") != identity.session_id
            for action in authoritative
        ):
            raise PermissionError(
                "Parallel actions must belong to the authenticated KP session"
            )
        return authoritative

    def result(
        self,
        status: ParallelWorkflowStatus,
        batch: dict[str, Any],
        message: str,
    ) -> ParallelWorkflowResult:
        actions = tuple(
            self.repo.get_player_action(str(item["action_id"]))
            for item in batch["items"]
        )
        checks = tuple(
            check
            for item in batch["items"]
            for check in item.get("checks") or ()
        )
        return ParallelWorkflowResult(
            status=status,
            batch=batch,
            actions=actions,
            checks=checks,
            message=message,
        )

    @staticmethod
    def require_kp(identity: AuthenticatedMember) -> None:
        if identity.role != "kp":
            raise PermissionError("Parallel preparation requires KP authority")

    @staticmethod
    def require_player(identity: AuthenticatedMember) -> None:
        if identity.role != "player":
            raise PermissionError("Parallel confirmation belongs to a player")


__all__ = ["ParallelActionWorkflowAuthority"]

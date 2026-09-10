"""Append-only audit lifecycle for human-KP Need Help requests."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from ai_kp.application.ports.repositories import DirectorHelpAuditStore
from ai_kp.platform.resolution.json_projection import canonical_json_bytes

DirectorHelpTerminalOutcome = Literal[
    "completed",
    "failed",
    "cancelled_client",
    "cancelled_control",
    "rejected_busy",
]

_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,159}$")
_ERROR_CODES_BY_FAILURE_OUTCOME = {
    "failed": frozenset(
        {
            "conflict",
            "internal_error",
            "invalid_input",
            "kp_session_ended",
            "request_abandoned",
            "request_cancelled",
            "upstream_invalid_response",
            "upstream_service_error",
        }
    ),
    "cancelled_client": frozenset({"client_disconnected"}),
    "cancelled_control": frozenset({"campaign_ai_call_cancelled"}),
    "rejected_busy": frozenset(
        {"director_help_capacity_exceeded", "director_help_in_progress"}
    ),
}


@dataclass(frozen=True)
class DirectorHelpAuditAttempt:
    attempt_id: str
    campaign_id: str
    run_id: str
    requested_by_member_id: str
    request_id: str
    question: str


class DirectorHelpAuditService:
    """Record one immutable request event and at most one terminal event."""

    def __init__(self, repo: DirectorHelpAuditStore):
        self.repo = repo

    def begin(
        self,
        *,
        campaign_id: str,
        run_id: str,
        requested_by_member_id: str,
        request_id: str,
        question: str,
    ) -> DirectorHelpAuditAttempt:
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("Need Help audit question is required")
        attempt = DirectorHelpAuditAttempt(
            attempt_id=f"dhelp_{uuid.uuid4().hex}",
            campaign_id=campaign_id,
            run_id=run_id,
            requested_by_member_id=requested_by_member_id,
            request_id=request_id,
            question=normalized_question,
        )
        self.repo.append_director_help_audit_event(
            attempt_id=attempt.attempt_id,
            campaign_id=attempt.campaign_id,
            run_id=attempt.run_id,
            requested_by_member_id=attempt.requested_by_member_id,
            request_id=attempt.request_id,
            event_type="requested",
            question=attempt.question,
        )
        return attempt

    def complete(
        self,
        attempt: DirectorHelpAuditAttempt,
        advice: dict[str, Any],
        *,
        duration_ms: int,
    ) -> str:
        response_hash = hashlib.sha256(canonical_json_bytes(advice)).hexdigest()
        self.repo.append_director_help_audit_event(
            attempt_id=attempt.attempt_id,
            campaign_id=attempt.campaign_id,
            run_id=attempt.run_id,
            requested_by_member_id=None,
            request_id=attempt.request_id,
            event_type="completed",
            advice=advice,
            response_hash=response_hash,
            duration_ms=self._duration(duration_ms),
        )
        return response_hash

    def terminate(
        self,
        attempt: DirectorHelpAuditAttempt,
        *,
        outcome: DirectorHelpTerminalOutcome,
        error_code: str,
        duration_ms: int,
    ) -> None:
        allowed_error_codes = _ERROR_CODES_BY_FAILURE_OUTCOME.get(outcome)
        if allowed_error_codes is None:
            raise ValueError("A non-completed Need Help audit needs a failure outcome")
        if (
            not _ERROR_CODE_RE.fullmatch(error_code)
            or error_code not in allowed_error_codes
        ):
            raise ValueError("Need Help audit error code is invalid")
        self.repo.append_director_help_audit_event(
            attempt_id=attempt.attempt_id,
            campaign_id=attempt.campaign_id,
            run_id=attempt.run_id,
            requested_by_member_id=None,
            request_id=attempt.request_id,
            event_type=outcome,
            error_code=error_code,
            duration_ms=self._duration(duration_ms),
        )

    def recover_abandoned(self) -> int:
        """Close requests left open by a previous process interruption."""

        return self.repo.append_abandoned_director_help_terminals(
            error_code="request_abandoned"
        )

    def page(
        self,
        campaign_id: str,
        *,
        limit: int,
        before_id: str | None,
    ) -> dict[str, Any]:
        if isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ValueError("Need Help audit page size must be from 1 to 50")
        rows = self.repo.list_director_help_audits(
            campaign_id,
            limit=limit + 1,
            before_id=before_id,
        )
        items = rows[:limit]
        return {
            "items": items,
            "next_before_id": items[-1]["id"] if len(rows) > limit else None,
        }

    @staticmethod
    def _duration(duration_ms: int) -> int:
        if isinstance(duration_ms, bool) or duration_ms < 0:
            raise ValueError("Need Help audit duration must be non-negative")
        return duration_ms


__all__ = [
    "DirectorHelpAuditAttempt",
    "DirectorHelpAuditService",
    "DirectorHelpTerminalOutcome",
]

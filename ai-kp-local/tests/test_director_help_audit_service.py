from __future__ import annotations

import hashlib
from typing import Any

import pytest

from ai_kp.application.director_help_audit_service import (
    DirectorHelpAuditService,
    DirectorHelpTerminalOutcome,
)
from ai_kp.platform.resolution.json_projection import canonical_json_bytes


class FakeAuditStore:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.events: list[dict[str, Any]] = []
        self.rows = rows or []
        self.list_calls: list[dict[str, Any]] = []

    def append_director_help_audit_event(self, **event: Any) -> dict[str, Any]:
        self.events.append(event)
        return event

    def list_director_help_audits(
        self,
        campaign_id: str,
        *,
        limit: int,
        before_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self.list_calls.append(
            {"campaign_id": campaign_id, "limit": limit, "before_id": before_id}
        )
        return self.rows[:limit]

    def append_abandoned_director_help_terminals(self, *, error_code: str) -> int:
        self.events.append({"event_type": "recovered", "error_code": error_code})
        return 2


def test_audit_service_appends_requested_and_hashed_completion() -> None:
    store = FakeAuditStore()
    service = DirectorHelpAuditService(store)

    attempt = service.begin(
        campaign_id="campaign-1",
        run_id="run-1",
        requested_by_member_id="member-1",
        request_id="request-1234",
        question="  What now?  ",
    )
    advice = {"question": "What now?", "writes_performed": False}
    response_hash = service.complete(attempt, advice, duration_ms=12)

    assert attempt.question == "What now?"
    assert [event["event_type"] for event in store.events] == [
        "requested",
        "completed",
    ]
    assert store.events[0]["question"] == "What now?"
    assert store.events[0]["requested_by_member_id"] == "member-1"
    assert "question" not in store.events[1]
    assert store.events[1]["advice"] == advice
    assert store.events[1]["requested_by_member_id"] is None
    assert response_hash == hashlib.sha256(canonical_json_bytes(advice)).hexdigest()


@pytest.mark.parametrize(
    ("outcome", "error_code"),
    [
        ("failed", "upstream_service_error"),
        ("cancelled_client", "client_disconnected"),
        ("cancelled_control", "campaign_ai_call_cancelled"),
        ("rejected_busy", "director_help_in_progress"),
    ],
)
def test_audit_service_appends_only_safe_failure_metadata(
    outcome: DirectorHelpTerminalOutcome,
    error_code: str,
) -> None:
    store = FakeAuditStore()
    service = DirectorHelpAuditService(store)
    attempt = service.begin(
        campaign_id="campaign-1",
        run_id="run-1",
        requested_by_member_id="member-1",
        request_id="request-1234",
        question="What now?",
    )

    service.terminate(
        attempt,
        outcome=outcome,
        error_code=error_code,
        duration_ms=8,
    )

    terminal = store.events[-1]
    assert terminal["event_type"] == outcome
    assert terminal["error_code"] == error_code
    assert terminal["requested_by_member_id"] is None
    assert "advice" not in terminal
    assert "question" not in terminal


def test_audit_service_rejects_unsafe_terminal_metadata() -> None:
    store = FakeAuditStore()
    service = DirectorHelpAuditService(store)
    attempt = service.begin(
        campaign_id="campaign-1",
        run_id="run-1",
        requested_by_member_id="member-1",
        request_id="request-1234",
        question="What now?",
    )

    with pytest.raises(ValueError, match="failure outcome"):
        service.terminate(
            attempt,
            outcome="completed",
            error_code="should_not_exist",
            duration_ms=1,
        )
    with pytest.raises(ValueError, match="error code"):
        service.terminate(
            attempt,
            outcome="failed",
            error_code="provider at http://127.0.0.1:8001 failed",
            duration_ms=1,
        )
    with pytest.raises(ValueError, match="error code"):
        service.terminate(
            attempt,
            outcome="cancelled_control",
            error_code="conflict",
            duration_ms=1,
        )
    assert len(store.events) == 1


def test_audit_service_recovers_abandoned_requests_with_stable_reason() -> None:
    store = FakeAuditStore()

    recovered = DirectorHelpAuditService(store).recover_abandoned()

    assert recovered == 2
    assert store.events == [
        {"event_type": "recovered", "error_code": "request_abandoned"}
    ]


def test_audit_service_uses_stable_before_cursor_and_limit_plus_one() -> None:
    store = FakeAuditStore(
        [{"id": f"attempt-{index}"} for index in range(1, 4)]
    )
    page = DirectorHelpAuditService(store).page(
        "campaign-1",
        limit=2,
        before_id="attempt-9",
    )

    assert page == {
        "items": [{"id": "attempt-1"}, {"id": "attempt-2"}],
        "next_before_id": "attempt-2",
    }
    assert store.list_calls == [
        {"campaign_id": "campaign-1", "limit": 3, "before_id": "attempt-9"}
    ]

import sqlite3
from pathlib import Path

import pytest

from ai_kp.application.director_help_audit_service import DirectorHelpAuditService
from ai_kp.application.ports.repositories import DirectorHelpAuditStore, DirectorHelpStore
from ai_kp.bootstrap.composition import create_app
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.migrations import v0085_director_help_audit_events
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db


def _seed_campaign(repo: Repository, suffix: str) -> dict[str, str]:
    campaign_id = f"campaign-{suffix}"
    session_id = f"session-{suffix}"
    member_id = f"member-{suffix}"
    module_id = f"module-{suffix}"
    run_id = f"run-{suffix}"
    repo.connection.execute(
        "INSERT INTO campaigns (id, title) VALUES (?, ?)",
        (campaign_id, f"Campaign {suffix}"),
    )
    repo.connection.execute(
        """
        INSERT INTO campaign_sessions (id, campaign_id, title, join_code_hash)
        VALUES (?, ?, ?, ?)
        """,
        (session_id, campaign_id, f"Session {suffix}", f"join-hash-{suffix}"),
    )
    repo.connection.execute(
        """
        INSERT INTO session_members
          (id, session_id, campaign_id, role, display_name, token_hash)
        VALUES (?, ?, ?, 'kp', ?, ?)
        """,
        (member_id, session_id, campaign_id, f"KP {suffix}", f"token-hash-{suffix}"),
    )
    repo.connection.execute(
        "INSERT INTO modules (id, campaign_id, title) VALUES (?, ?, ?)",
        (module_id, campaign_id, f"Module {suffix}"),
    )
    repo.connection.execute(
        """
        INSERT INTO campaign_module_runs (id, campaign_id, module_id, started_by_member_id)
        VALUES (?, ?, ?, ?)
        """,
        (run_id, campaign_id, module_id, member_id),
    )
    return {
        "campaign_id": campaign_id,
        "run_id": run_id,
        "member_id": member_id,
    }


def _append_requested(
    repo: Repository,
    seeded: dict[str, str],
    attempt_id: str,
    *,
    request_id: str | None = None,
) -> dict:
    return repo.append_director_help_audit_event(
        attempt_id=attempt_id,
        campaign_id=seeded["campaign_id"],
        run_id=seeded["run_id"],
        requested_by_member_id=seeded["member_id"],
        request_id=request_id or f"request-{attempt_id}",
        event_type="requested",
        question=f"Question for {attempt_id}?",
    )


def test_v85_migration_is_idempotent_and_creates_partial_indexes() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        v0085_director_help_audit_events.migrate(connection)
        v0085_director_help_audit_events.migrate(connection)

        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(director_help_audit_events)"
            ).fetchall()
        }
        indexes = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'index' AND tbl_name = 'director_help_audit_events'
                """
            ).fetchall()
        }
        assert {
            "sequence",
            "event_id",
            "attempt_id",
            "campaign_id",
            "run_id",
            "requested_by_member_id",
            "request_id",
            "event_type",
            "question",
            "advice_json",
            "response_hash",
            "error_code",
            "duration_ms",
            "created_at",
        } == columns
        assert {
            "ux_director_help_audit_requested_attempt",
            "ux_director_help_audit_terminal_attempt",
            "idx_director_help_audit_campaign_requested_sequence",
        } <= indexes
    finally:
        connection.close()


def test_append_constraints_and_projection_are_append_only() -> None:
    connection = connect(":memory:")
    try:
        init_db(connection)
        repo = Repository(connection)
        seeded = _seed_campaign(repo, "one")
        requested = _append_requested(repo, seeded, "attempt-open")

        assert requested["event_type"] == "requested"
        assert requested["advice"] is None
        assert repo.list_director_help_audits(seeded["campaign_id"], 10) == [
            {
                "id": "attempt-open",
                "run_id": seeded["run_id"],
                "requested_by_member_id": seeded["member_id"],
                "question": "Question for attempt-open?",
                "error_code": None,
                "duration_ms": None,
                "created_at": requested["created_at"],
                "completed_at": None,
                "response_hash": None,
                "outcome": "requested",
                "advice": None,
            }
        ]

        with pytest.raises(sqlite3.IntegrityError):
            _append_requested(repo, seeded, "attempt-open")
        with pytest.raises(ValueError, match="requires a requested event"):
            repo.append_director_help_audit_event(
                attempt_id="attempt-orphan",
                campaign_id=seeded["campaign_id"],
                run_id=seeded["run_id"],
                requested_by_member_id=seeded["member_id"],
                request_id="request-orphan",
                event_type="failed",
                error_code="provider_failed",
                duration_ms=5,
            )

        completed_request = _append_requested(repo, seeded, "attempt-complete")
        completed = repo.append_director_help_audit_event(
            attempt_id="attempt-complete",
            campaign_id=seeded["campaign_id"],
            run_id=seeded["run_id"],
            requested_by_member_id=seeded["member_id"],
            request_id="request-attempt-complete",
            event_type="completed",
            advice={"answer": "Review the sealed door clue."},
            response_hash="a" * 64,
            duration_ms=42,
        )
        assert completed["advice"] == {"answer": "Review the sealed door clue."}
        with pytest.raises(sqlite3.IntegrityError):
            repo.append_director_help_audit_event(
                attempt_id="attempt-complete",
                campaign_id=seeded["campaign_id"],
                run_id=seeded["run_id"],
                requested_by_member_id=seeded["member_id"],
                request_id="request-attempt-complete",
                event_type="failed",
                error_code="duplicate_terminal",
                duration_ms=43,
            )

        audits = repo.list_director_help_audits(seeded["campaign_id"], 10)
        completed_projection = next(item for item in audits if item["id"] == "attempt-complete")
        assert completed_projection == {
            "id": "attempt-complete",
            "run_id": seeded["run_id"],
            "requested_by_member_id": seeded["member_id"],
            "question": "Question for attempt-complete?",
            "error_code": None,
            "duration_ms": 42,
            "created_at": completed_request["created_at"],
            "completed_at": completed["created_at"],
            "response_hash": "a" * 64,
            "outcome": "completed",
            "advice": {"answer": "Review the sealed door clue."},
        }
    finally:
        connection.close()


def test_terminal_validation_and_member_deletion_are_compatible() -> None:
    connection = connect(":memory:")
    try:
        init_db(connection)
        repo = Repository(connection)
        seeded = _seed_campaign(repo, "member-delete")
        _append_requested(repo, seeded, "attempt-member-delete")

        connection.execute(
            "DELETE FROM session_members WHERE id = ?", (seeded["member_id"],)
        )
        failed = repo.append_director_help_audit_event(
            attempt_id="attempt-member-delete",
            campaign_id=seeded["campaign_id"],
            run_id=seeded["run_id"],
            requested_by_member_id=None,
            request_id="request-attempt-member-delete",
            event_type="failed",
            error_code="provider_failed",
            duration_ms=9,
        )
        assert failed["requested_by_member_id"] is None
        audit = repo.list_director_help_audits(seeded["campaign_id"], 1)[0]
        assert audit["requested_by_member_id"] is None
        assert audit["outcome"] == "failed"

        _append_requested(repo, seeded | {"member_id": None}, "attempt-invalid-code")
        with pytest.raises(sqlite3.IntegrityError):
            repo.append_director_help_audit_event(
                attempt_id="attempt-invalid-code",
                campaign_id=seeded["campaign_id"],
                run_id=seeded["run_id"],
                requested_by_member_id=None,
                request_id="request-attempt-invalid-code",
                event_type="failed",
                error_code="Provider-Failed",
                duration_ms=3,
            )
    finally:
        connection.close()


def test_abandoned_request_recovery_is_append_only_and_idempotent() -> None:
    connection = connect(":memory:")
    try:
        init_db(connection)
        repo = Repository(connection)
        seeded = _seed_campaign(repo, "recovery")
        _append_requested(repo, seeded, "attempt-abandoned")
        _append_requested(repo, seeded, "attempt-completed")
        repo.append_director_help_audit_event(
            attempt_id="attempt-completed",
            campaign_id=seeded["campaign_id"],
            run_id=seeded["run_id"],
            requested_by_member_id=None,
            request_id="request-attempt-completed",
            event_type="completed",
            advice={"answer": "Already settled."},
            response_hash="b" * 64,
            duration_ms=4,
        )

        service = DirectorHelpAuditService(repo)
        assert service.recover_abandoned() == 1
        assert service.recover_abandoned() == 0

        audits = {item["id"]: item for item in repo.list_director_help_audits(
            seeded["campaign_id"], 10
        )}
        assert audits["attempt-abandoned"]["outcome"] == "failed"
        assert audits["attempt-abandoned"]["error_code"] == "request_abandoned"
        assert audits["attempt-abandoned"]["duration_ms"] == 0
        assert audits["attempt-completed"]["outcome"] == "completed"
        assert connection.execute(
            "SELECT COUNT(*) FROM director_help_audit_events WHERE attempt_id = ?",
            ("attempt-abandoned",),
        ).fetchone()[0] == 2
    finally:
        connection.close()


def test_app_startup_recovers_and_commits_abandoned_requests(tmp_path: Path) -> None:
    db_path = tmp_path / "director-help-restart.sqlite3"
    connection = connect(db_path)
    try:
        init_db(connection)
        repo = Repository(connection)
        seeded = _seed_campaign(repo, "restart")
        _append_requested(repo, seeded, "attempt-restart")
        connection.commit()
    finally:
        connection.close()

    create_app(Settings(db_path=db_path))

    verification_connection = connect(db_path)
    try:
        audit = Repository(verification_connection).list_director_help_audits(
            seeded["campaign_id"], 1
        )[0]
        assert audit["outcome"] == "failed"
        assert audit["error_code"] == "request_abandoned"
    finally:
        verification_connection.close()


def test_sequence_pagination_is_stable_and_cursor_is_campaign_scoped() -> None:
    connection = connect(":memory:")
    try:
        init_db(connection)
        repo = Repository(connection)
        first_campaign = _seed_campaign(repo, "page-one")
        second_campaign = _seed_campaign(repo, "page-two")
        for attempt_id in ("attempt-1", "attempt-2", "attempt-3"):
            _append_requested(repo, first_campaign, attempt_id)
        _append_requested(repo, second_campaign, "attempt-other")
        connection.execute(
            "UPDATE director_help_audit_events SET created_at = '2026-01-01 00:00:00'"
        )

        first_page = repo.list_director_help_audits(first_campaign["campaign_id"], 2)
        assert [item["id"] for item in first_page] == ["attempt-3", "attempt-2"]
        second_page = repo.list_director_help_audits(
            first_campaign["campaign_id"], 2, before_id=first_page[-1]["id"]
        )
        assert [item["id"] for item in second_page] == ["attempt-1"]
        assert not ({item["id"] for item in first_page} & {item["id"] for item in second_page})

        with pytest.raises(KeyError, match="cursor not found"):
            repo.list_director_help_audits(
                second_campaign["campaign_id"], 2, before_id="attempt-3"
            )
    finally:
        connection.close()


def test_director_help_read_and_audit_ports_remain_separate() -> None:
    assert DirectorHelpAuditStore not in DirectorHelpStore.__mro__
    assert "append_director_help_audit_event" not in DirectorHelpStore.__dict__

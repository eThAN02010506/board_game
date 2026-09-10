import json

import pytest

from ai_kp.application.session_continuity_service import SessionContinuityService
from ai_kp.application.session_service import SessionService
from ai_kp.evaluation.continuity_checkpoint import reconstruct_continuity_checkpoint
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import db_session


def _ended_checkpoint(tmp_path):
    database = tmp_path / "checkpoint.sqlite3"
    with db_session(database) as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Checkpoint campaign")
        bundle = SessionService(repo).create(str(campaign["id"]))
        identity = repo.authenticate_access_token(str(bundle["access_token"]))
        assert identity is not None
        repo.activate_prepared_episode(identity.session_id)
        event = repo.append_event(
            identity.campaign_id,
            "kp",
            "checkpoint_public",
            "The table learned one public fact.",
            visibility="table",
        )
        ended = SessionContinuityService(repo).end(
            identity, client_end_id="checkpoint-end-1"
        )
        episode_id = str(ended["current_episode"]["id"])
        connection.commit()
    return database, episode_id, str(event["id"])


def test_reconstructs_safe_immutable_continuity_checkpoint(tmp_path) -> None:
    database, episode_id, event_id = _ended_checkpoint(tmp_path)
    with db_session(database) as connection:
        evidence = reconstruct_continuity_checkpoint(connection, episode_id)

    payload = evidence.as_dict()
    assert payload["episode_id"] == episode_id
    assert payload["event_ids"] == [event_id]
    assert len(payload["checkpoint_fingerprint"]) == 64
    assert len(payload["public_projection_hash"]) == 64
    assert len(payload["observer_projection_hash"]) == 64
    assert len(payload["kp_projection_hash"]) == 64
    serialized = json.dumps(payload)
    assert "The table learned one public fact" not in serialized
    assert "projection" not in serialized.replace("projection_hash", "")


def test_reconstruction_rejects_tampered_event_window(tmp_path) -> None:
    database, episode_id, event_id = _ended_checkpoint(tmp_path)
    with db_session(database) as connection:
        connection.execute(
            "UPDATE events SET summary = 'tampered' WHERE id = ?", (event_id,)
        )
        with pytest.raises(ValueError, match="event window hash"):
            reconstruct_continuity_checkpoint(connection, episode_id)


def test_projection_drift_changes_checkpoint_fingerprint(tmp_path) -> None:
    database, episode_id, _ = _ended_checkpoint(tmp_path)
    with db_session(database) as connection:
        before = reconstruct_continuity_checkpoint(connection, episode_id)
        connection.execute(
            """
            UPDATE session_continuity_snapshots
            SET observer_projection_json = json_set(observer_projection_json, '$.drift', 1)
            WHERE episode_id = ?
            """,
            (episode_id,),
        )
        after = reconstruct_continuity_checkpoint(connection, episode_id)

    assert after.observer_projection_hash != before.observer_projection_hash
    assert after.checkpoint_fingerprint != before.checkpoint_fingerprint


def test_reconstruction_rejects_foreign_or_reordered_event_ids(tmp_path) -> None:
    database, episode_id, event_id = _ended_checkpoint(tmp_path)
    with db_session(database) as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Foreign campaign")
        foreign = repo.append_event(
            str(campaign["id"]),
            "kp",
            "foreign",
            "Foreign event",
            visibility="table",
        )
        connection.execute(
            """
            UPDATE session_continuity_snapshots
            SET event_ids_json = ? WHERE episode_id = ?
            """,
            (json.dumps([event_id, foreign["id"]]), episode_id),
        )
        with pytest.raises(ValueError, match="event IDs"):
            reconstruct_continuity_checkpoint(connection, episode_id)

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from ai_kp.evaluation.continuity_checkpoint import reconstruct_continuity_checkpoint
from ai_kp.evaluation.long_ui_journey_reconstruction import (
    reconstruct_long_ui_journey_evidence,
)


def _canonical_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _long_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE campaigns (
          id TEXT PRIMARY KEY, ruleset_id TEXT, ruleset_version TEXT
        );
        CREATE TABLE campaign_sessions (
          id TEXT PRIMARY KEY, campaign_id TEXT
        );
        CREATE TABLE session_members (
          id TEXT PRIMARY KEY, session_id TEXT, campaign_id TEXT, role TEXT,
          pc_id TEXT, revoked_at TEXT
        );
        CREATE TABLE campaign_investigators (
          campaign_id TEXT, investigator_id TEXT, legacy_pc_id TEXT, status TEXT
        );
        CREATE TABLE modules (
          id TEXT PRIMARY KEY, source_hash TEXT
        );
        CREATE TABLE campaign_module_runs (
          id TEXT PRIMARY KEY, campaign_id TEXT, module_id TEXT,
          module_source_hash TEXT, status TEXT, automation_level TEXT,
          started_at TEXT, completed_at TEXT
        );
        CREATE TABLE campaign_episodes (
          id TEXT PRIMARY KEY, campaign_id TEXT, session_id TEXT,
          sequence_no INTEGER, status TEXT, client_continue_id TEXT,
          event_start_rowid INTEGER, started_at TEXT, ended_at TEXT, version INTEGER
        );
        CREATE TABLE session_continuity_snapshots (
          id TEXT PRIMARY KEY, episode_id TEXT, campaign_id TEXT, session_id TEXT,
          client_end_id TEXT, event_window_hash TEXT, event_ids_json TEXT,
          generation_cutoff TEXT, public_projection_json TEXT,
          observer_projection_json TEXT, kp_projection_json TEXT, created_at TEXT
        );
        CREATE TABLE events (
          id TEXT PRIMARY KEY, campaign_id TEXT, actor_type TEXT, actor_id TEXT,
          visibility TEXT, event_type TEXT, happened_at TEXT, summary TEXT,
          created_at TEXT
        );
        CREATE TABLE persistent_store_identity (
          singleton INTEGER PRIMARY KEY, persistent_store_id TEXT
        );

        INSERT INTO campaigns VALUES ('campaign-1', 'coc7', '7e');
        INSERT INTO campaign_sessions VALUES ('session-1', 'campaign-1');
        INSERT INTO modules VALUES (
          'module-1', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        );
        INSERT INTO campaign_module_runs VALUES (
          'run-1', 'campaign-1', 'module-1',
          'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
          'completed', 'ai_kp', '2026-08-01T00:00:00+00:00',
          '2026-08-11T04:00:00+00:00'
        );
        INSERT INTO persistent_store_identity VALUES (1, 'store-long-1');
        """
    )
    for index in range(1, 5):
        connection.execute(
            "INSERT INTO session_members VALUES (?, 'session-1', 'campaign-1', "
            "'player', ?, NULL)",
            (f"member-{index}", f"pc-{index}"),
        )
        connection.execute(
            "INSERT INTO campaign_investigators VALUES "
            "('campaign-1', ?, ?, 'approved')",
            (f"investigator-{index}", f"pc-{index}"),
        )

    started = datetime(2026, 8, 1, tzinfo=UTC)
    for index in range(1, 11):
        episode_started = started + timedelta(days=index - 1)
        episode_ended = episode_started + timedelta(hours=2)
        event_id = f"event-{index}"
        connection.execute(
            """
            INSERT INTO events
              (id, campaign_id, actor_type, actor_id, visibility, event_type,
               happened_at, summary, created_at)
            VALUES (?, 'campaign-1', 'kp', 'member-kp', 'table', 'episode_marker',
                    ?, ?, ?)
            """,
            (
                event_id,
                episode_started.isoformat(),
                f"Episode {index} completed.",
                episode_ended.isoformat(),
            ),
        )
        rowid = int(
            connection.execute("SELECT rowid FROM events WHERE id = ?", (event_id,)).fetchone()[
                0
            ]
        )
        event = dict(
            connection.execute(
                """
                SELECT id, actor_type, actor_id, visibility, event_type,
                       happened_at, summary, created_at
                FROM events WHERE id = ?
                """,
                (event_id,),
            ).fetchone()
        )
        episode_id = f"episode-{index}"
        snapshot_id = f"snapshot-{index}"
        connection.execute(
            "INSERT INTO campaign_episodes VALUES (?, 'campaign-1', 'session-1', ?, "
            "'ended', ?, ?, ?, ?, 1)",
            (
                episode_id,
                index,
                None if index == 1 else f"continue-{index - 1}",
                rowid,
                episode_started.isoformat(),
                episode_ended.isoformat(),
            ),
        )
        projections = {
            "public": {"episode": index, "scope": "public"},
            "observer": {"episode": index, "scope": "observer"},
            "kp": {"episode": index, "scope": "kp"},
        }
        connection.execute(
            """
            INSERT INTO session_continuity_snapshots VALUES
              (?, ?, 'campaign-1', 'session-1', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                episode_id,
                f"end-{index}",
                _canonical_hash([event]),
                json.dumps([event_id]),
                episode_ended.isoformat(),
                json.dumps(projections["public"]),
                json.dumps(projections["observer"]),
                json.dumps(projections["kp"]),
                episode_ended.isoformat(),
            ),
        )
    connection.commit()
    connection.close()


def _fake_anchor() -> dict:
    return {
        "session_id": "session-1",
        "automation_mode": "full_ai",
        "source_commit": "abcdef1234567",
        "ruleset": {"id": "coc7", "version": "7e"},
        "players": [
            {"member_id": f"member-{index}", "investigator_id": f"investigator-{index}"}
            for index in range(1, 5)
        ],
        "module": {"sha256": "a" * 64},
    }


def _playwright_evidence(database: Path) -> dict:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    checkpoint = reconstruct_continuity_checkpoint(connection, "episode-5")
    connection.close()
    started = datetime(2026, 8, 1, tzinfo=UTC)
    ui_events = []
    for index in range(1, 11):
        episode_started = started + timedelta(days=index - 1)
        if index == 3:
            ui_events.append(
                {
                    "kind": "authoritative_ending_visible",
                    "observed_at": (episode_started + timedelta(hours=1)).isoformat(),
                    "session_index": index,
                }
            )
        ui_events.append(
            {
                "kind": "session_end_visible",
                "observed_at": (episode_started + timedelta(hours=2, minutes=1)).isoformat(),
                "session_index": index,
            }
        )
        if index < 10:
            ui_events.append(
                {
                    "kind": "continue_campaign_visible",
                    "observed_at": (episode_started + timedelta(days=1, minutes=1)).isoformat(),
                    "session_index": index,
                }
            )
    return {
        "completed_at": "2026-08-11T05:00:00+00:00",
        "source_commit": "abcdef1234567",
        "long_ui_passed": True,
        "authority_refs": {
            "campaign_ids": ["campaign-1"],
            "session_ids": ["session-1"],
            "episode_ids": [f"episode-{index}" for index in range(1, 11)],
            "continuity_snapshot_ids": [f"snapshot-{index}" for index in range(1, 11)],
            "member_ids": [f"member-{index}" for index in range(1, 5)],
            "investigator_ids": [f"investigator-{index}" for index in range(1, 5)],
            "module_import_job_ids": [],
            "module_ids": ["module-1"],
            "module_run_ids": ["run-1"],
            "scenario_contract_job_ids": [],
            "scenario_contract_version_ids": [],
            "action_ids": [],
            "adjudication_ids": [],
            "check_ids": [],
            "parallel_batch_ids": [],
            "public_turn_ids": [],
            "scenario_command_batch_ids": [],
        },
        "process_restart": {
            "before": {
                "observed_at": "2026-08-05T03:00:00+00:00",
                "process_instance_id": "process-old",
                "os_pid": 4001,
                "persistent_store_id": "store-long-1",
                "checkpoint_snapshot_id": checkpoint.snapshot_id,
            },
            "stopped": {
                "observed_at": "2026-08-05T03:01:00+00:00",
                "process_instance_id": "process-old",
                "os_pid": 4001,
                "persistent_store_id": "store-long-1",
                "clean_shutdown": True,
            },
            "started": {
                "observed_at": "2026-08-05T03:02:00+00:00",
                "process_instance_id": "process-new",
                "os_pid": 4002,
                "persistent_store_id": "store-long-1",
                "health_status": 200,
            },
            "after": {
                "observed_at": "2026-08-06T00:01:00+00:00",
                "process_instance_id": "process-new",
                "os_pid": 4002,
                "persistent_store_id": "store-long-1",
                "checkpoint_snapshot_id": checkpoint.snapshot_id,
            },
        },
        "ui_corroboration_events": ui_events,
        "single_session_anchor_evidence": {"scoped": True},
    }


def _accept_anchor(monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_kp.evaluation.long_ui_journey_reconstruction.reconstruct_ui_journey_anchor_evidence",
        lambda *_args, **_kwargs: SimpleNamespace(
            strict_evidence=_fake_anchor(), gaps=()
        ),
    )
    monkeypatch.setattr(
        "ai_kp.evaluation.long_ui_journey_evidence.verify_real_ui_journey_anchor",
        lambda _value: SimpleNamespace(accepted=True, blockers=()),
    )


def test_reconstructs_complete_longitudinal_authority_chain(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "long.sqlite3"
    _long_database(database)
    _accept_anchor(monkeypatch)

    result = reconstruct_long_ui_journey_evidence(
        _playwright_evidence(database), database
    )

    assert result.gaps == ()
    assert result.accepted is True
    assert result.verification.accepted is True
    assert result.ignored_producer_claims == ("long_ui_passed",)
    assert [item["sequence_no"] for item in result.strict_evidence["episodes"]] == list(
        range(1, 11)
    )
    assert result.strict_evidence["episodes"][4]["next_episode"] == {
        "id": "episode-6",
        "sequence_no": 6,
        "client_continue_id": "continue-5",
    }
    serialized = json.dumps(result.as_dict())
    assert "Episode 5 completed" not in serialized


def test_reconstructs_final_four_after_observed_join_and_departure(
    tmp_path: Path, monkeypatch
) -> None:
    database = tmp_path / "membership-changes.sqlite3"
    _long_database(database)
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO session_members VALUES "
        "('member-5', 'session-1', 'campaign-1', 'observer', 'pc-5', NULL)"
    )
    connection.execute(
        "INSERT INTO campaign_investigators VALUES "
        "('campaign-1', 'investigator-5', 'pc-5', 'approved')"
    )
    connection.commit()
    connection.close()
    _accept_anchor(monkeypatch)
    evidence = _playwright_evidence(database)
    evidence["authority_refs"]["member_ids"].append("member-5")
    evidence["authority_refs"]["investigator_ids"].append("investigator-5")

    result = reconstruct_long_ui_journey_evidence(evidence, database)

    assert result.gaps == ()
    assert result.accepted is True
    assert len(result.strict_evidence["players"]) == 4


def test_rejects_incomplete_episode_refs(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "tampered.sqlite3"
    _long_database(database)
    _accept_anchor(monkeypatch)
    evidence = _playwright_evidence(database)
    evidence["authority_refs"]["episode_ids"].pop()
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE events SET summary = 'tampered' WHERE id = 'event-4'"
    )
    connection.commit()
    connection.close()

    result = reconstruct_long_ui_journey_evidence(evidence, database)

    assert result.verification.accepted is False
    assert result.accepted is False
    assert any("complete episode ledger" in gap for gap in result.gaps)


def test_rejects_tampered_authoritative_event_window(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "event-tamper.sqlite3"
    _long_database(database)
    _accept_anchor(monkeypatch)
    evidence = _playwright_evidence(database)
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE events SET summary = 'tampered' WHERE id = 'event-4'"
    )
    connection.commit()
    connection.close()

    result = reconstruct_long_ui_journey_evidence(evidence, database)

    assert result.verification.accepted is False
    assert result.accepted is False
    assert any("event window hash" in gap for gap in result.gaps)


def test_rejects_restart_store_drift_and_missing_anchor(tmp_path: Path) -> None:
    database = tmp_path / "store-drift.sqlite3"
    _long_database(database)
    evidence = _playwright_evidence(database)
    evidence["process_restart"]["after"]["persistent_store_id"] = "store-copy"
    evidence.pop("single_session_anchor_evidence")

    result = reconstruct_long_ui_journey_evidence(evidence, database)

    assert result.verification.accepted is False
    assert result.accepted is False
    assert any("store identity" in gap for gap in result.gaps)
    assert any("single_session_anchor_evidence" in gap for gap in result.gaps)

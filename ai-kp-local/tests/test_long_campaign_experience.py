import sqlite3
from pathlib import Path

from ai_kp.evaluation.long_campaign_experience import (
    build_gm_persona_evidence,
    build_journey_coverage_evidence,
    build_player_persona_evidence,
    build_rps07_fault_evidence,
    build_rps_evidence,
)


def _evidence() -> dict:
    action = {
        "persona": "roleplayer",
        "driver_source": "model",
        "result": "resolved",
        "observation_fingerprint": "pv1-deadbeef",
        "clarification_count": 1,
        "confirmation_count": 1,
        "digital_roll_count": 1,
        "push_count": 0,
        "accepted_failure_count": 0,
    }
    return {
        "source_commit": "abcdef1234567",
        "completed_at": "2026-09-01T02:00:00+00:00",
        "sessions": [
            {
                "index": 1,
                "actions": [
                    action,
                    {**action, "persona": "optimizer", "driver_source": "deterministic_fallback"},
                    {"persona": "trpg_newcomer", "parallel": True, "result": "resolved"},
                ],
            }
        ],
    }


def test_builds_only_terminal_model_driven_player_persona_evidence() -> None:
    result = build_player_persona_evidence(
        _evidence(), artifact_ref="evidence/long-ui.json", artifact_sha256="a" * 64
    )

    assert result == [
        {
            "persona_id": "roleplayer",
            "actor_mode": "player_ui",
            "source_commit": "abcdef1234567",
            "observed_at": "2026-09-01T02:00:00+00:00",
            "artifact_ref": "evidence/long-ui.json",
            "artifact_sha256": "a" * 64,
            "observation_ids": ["session-1:action-1:pv1-deadbeef"],
            "interaction_count": 4,
        }
    ]


def test_rejects_invalid_counts_and_provenance() -> None:
    evidence = _evidence()
    evidence["sessions"][0]["actions"][0]["confirmation_count"] = True

    assert build_player_persona_evidence(
        evidence, artifact_ref="evidence/long-ui.json", artifact_sha256="a" * 64
    ) == []
    assert build_player_persona_evidence(
        _evidence(), artifact_ref="evidence/long-ui.json", artifact_sha256="bad"
    ) == []


def _coverage_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE skill_checks (
          id TEXT, campaign_id TEXT, session_id TEXT, status TEXT, passed INTEGER
        );
        CREATE TABLE coc7_encounters (
          id TEXT, campaign_id TEXT, session_id TEXT, kind TEXT, status TEXT
        );
        CREATE TABLE encounter_action_requests (
          id TEXT, encounter_id TEXT, campaign_id TEXT, session_id TEXT,
          action_key TEXT, status TEXT
        );
        CREATE TABLE inventory_ledger_events (
          id TEXT, campaign_id TEXT, item_id TEXT, command_type TEXT
        );
        CREATE TABLE character_lifecycle_events (
          id TEXT, campaign_id TEXT, session_id TEXT, action TEXT, to_state TEXT
        );
        CREATE TABLE session_members (
          id TEXT, campaign_id TEXT, session_id TEXT, role TEXT, joined_at TEXT
        );
        CREATE TABLE campaign_module_runs (id TEXT, campaign_id TEXT, status TEXT);
        CREATE TABLE coc7_gameplay_events (
          id TEXT, campaign_id TEXT, session_id TEXT, event_type TEXT
        );
        CREATE TABLE investigator_permanent_change_proposals (
          id TEXT, campaign_id TEXT, kind TEXT, status TEXT
        );
        CREATE TABLE campaign_setup_revisions (
          id TEXT, campaign_id TEXT, status TEXT
        );
        CREATE TABLE session_zero_confirmations (revision_id TEXT, member_id TEXT);
        CREATE TABLE module_run_control_events (
          id TEXT, run_id TEXT, from_mode TEXT, to_mode TEXT, created_at TEXT
        );

        INSERT INTO skill_checks VALUES
          ('check-ok', 'campaign-1', 'session-1', 'resolved', 1),
          ('check-fail', 'campaign-1', 'session-1', 'resolved', 0);
        INSERT INTO coc7_encounters VALUES
          ('combat-1', 'campaign-1', 'session-1', 'combat', 'completed');
        INSERT INTO encounter_action_requests VALUES
          ('action-1', 'combat-1', 'campaign-1', 'session-1', 'brawl', 'committed'),
          ('action-2', 'combat-1', 'campaign-1', 'session-1', 'improvised', 'committed');
        INSERT INTO inventory_ledger_events VALUES
          ('inventory-create', 'campaign-1', 'item-1', 'create'),
          ('inventory-pickup', 'campaign-1', 'item-1', 'pickup');
        INSERT INTO character_lifecycle_events VALUES
          ('life-death', 'campaign-1', 'session-1', 'ruleset_state_sync', 'dead'),
          ('life-replace', 'campaign-1', 'session-1', 'replace', 'active'),
          ('life-leave', 'campaign-1', 'session-1', 'observe', 'departed');
        INSERT INTO session_members VALUES
          ('member-1', 'campaign-1', 'session-1', 'player', '2026-09-01T01:00:00'),
          ('member-2', 'campaign-1', 'session-1', 'player', '2026-09-01T01:00:01'),
          ('member-3', 'campaign-1', 'session-1', 'player', '2026-09-01T01:00:02'),
          ('member-4', 'campaign-1', 'session-1', 'observer', '2026-09-01T01:00:03'),
          ('member-5', 'campaign-1', 'session-1', 'player', '2026-09-08T01:00:00');
        INSERT INTO campaign_module_runs VALUES ('run-1', 'campaign-1', 'completed');
        INSERT INTO coc7_gameplay_events VALUES
          ('growth-1', 'campaign-1', 'session-1', 'character.development');
        INSERT INTO investigator_permanent_change_proposals VALUES
          ('permanent-growth-1', 'campaign-1', 'skill', 'accepted');
        INSERT INTO campaign_setup_revisions VALUES
          ('setup-1', 'campaign-1', 'active');
        INSERT INTO session_zero_confirmations VALUES
          ('setup-1', 'member-kp'),
          ('setup-1', 'member-1'), ('setup-1', 'member-2'),
          ('setup-1', 'member-3'), ('setup-1', 'member-4');
        INSERT INTO module_run_control_events VALUES
          ('control-1', 'run-1', 'ai_assist', 'human_kp', '2026-09-03T01:00:00'),
          ('control-2', 'run-1', 'human_kp', 'ai_assist', '2026-09-03T01:01:00');
        """
    )
    connection.commit()
    connection.close()


def test_builds_all_journey_coverage_from_ui_and_authority(tmp_path: Path) -> None:
    database = tmp_path / "coverage.sqlite3"
    _coverage_database(database)
    evidence = {
        "source_commit": "abcdef1234567",
        "completed_at": "2026-09-10T02:00:00+00:00",
        "authority_refs": {
            "campaign_ids": ["campaign-1"],
            "session_ids": ["session-1"],
            "check_ids": ["check-ok", "check-fail"],
        },
        "sessions": [{
            "index": 2,
            "actions": [
                {
                    "driver_source": "model",
                    "driver_approach": approach,
                    "result": "resolved",
                    "observation_fingerprint": f"pv1-deadbee{index}",
                    "persona": (
                        "trpg_newcomer" if index == 0 else
                        "rules_veteran" if index == 1 else "roleplayer"
                    ),
                    "clarification_count": 0,
                    "confirmation_count": 1,
                    "digital_roll_count": 1,
                    "push_count": 0,
                    "accepted_failure_count": 1 if index == 2 else 0,
                    "selected_skills": ["侦查"] if index == 1 else [],
                    "observed_routes": ["skill_check"] if index == 1 else [],
                }
                for index, approach in enumerate(("travel", "dialogue", "investigate"))
            ],
        }],
        "combat_ui_events": [{"kind": "ruleset_combat_completed"}],
        "inventory_ui_events": [{"kind": "loot_registered_and_picked_up"}],
        "improvised_ui_events": [{"kind": "improvised_agent_action_confirmed"}],
        "growth_ui_events": [{"kind": "ruleset_growth_player_confirmed"}],
        "lifecycle_ui_events": [
            {"kind": "death_and_replacement"},
            {"kind": "late_player_joined"},
            {"kind": "player_departed_to_observer"},
        ],
        "ui_corroboration_events": [
            {"kind": "authoritative_ending_visible"},
            {"kind": "session_end_visible"},
            {"kind": "continue_campaign_visible"},
        ],
        "process_restart": {
            "after": {"checkpoint_snapshot_id": "snapshot-1"}
        },
        "player_standard_ui_events": [
            {"kind": "current_situation_visible"},
            {"kind": "action_space_visible"},
            {"kind": "next_step_visible"},
            {"kind": "character_state_visible"},
        ],
        "gm_ui_events": [
            {"kind": "new_gm_guided_setup"},
            {"kind": "veteran_gm_authority_tools"},
            {"kind": "ai_assisted_gm_handoff"},
        ],
    }

    result = build_journey_coverage_evidence(
        evidence,
        database,
        artifact_ref="evidence/long-ui.json",
        artifact_sha256="d" * 64,
    )

    assert {item["coverage_id"] for item in result} == {
        "exploration",
        "npc_dialogue",
        "investigation",
        "successful_check",
        "failed_check",
        "combat",
        "inventory_loot",
        "improvised_action",
        "major_choice",
        "growth",
        "death",
        "replacement",
        "late_join",
        "player_leave",
    }
    assert next(item for item in result if item["coverage_id"] == "combat")[
        "surface"
    ] == "kp_player_ui"

    gm_result = build_gm_persona_evidence(
        evidence,
        database,
        artifact_ref="evidence/long-ui.json",
        artifact_sha256="d" * 64,
    )
    assert {item["persona_id"] for item in gm_result} == {
        "new_gm",
        "veteran_gm",
        "ai_assisted_gm",
    }

    rps_result = build_rps_evidence(
        evidence,
        database,
        artifact_ref="evidence/long-ui.json",
        artifact_sha256="d" * 64,
    )
    assert {item["requirement_id"] for item in rps_result} == {
        "RPS-01",
        "RPS-02",
        "RPS-03",
        "RPS-04",
        "RPS-05",
        "RPS-06",
        "RPS-08",
        "RPS-09",
        "RPS-10",
        "RPS-11",
        "RPS-12",
    }

    fault_result = build_rps07_fault_evidence(
        {
            "evidence_kind": "rps_ai_failure_ui",
            "source_commit": "abcdef1",
            "observed_at": "2026-08-31T12:00:00+00:00",
            "requirement_id": "RPS-07",
            "surface": "player_ui",
            "ui_observations": [
                "auto_kp_terminal_fallback_visible",
                "unexecuted_ruling_visible",
                "confirm_action_absent",
                "revise_action_visible",
            ],
            "authority_observations": {
                "player_action_id": "action-failed-safe",
                "action_status": "reviewed",
                "adjudication_status": "pending",
                "adjudication_mode": "roleplay_or_clarification",
                "source_model": "auto-kp-fallback",
                "source_error_present": True,
                "public_turn_count_before": 0,
                "public_turn_count_after": 0,
            },
        },
        artifact_ref="evidence/rps-fault-ui.json",
        artifact_sha256="e" * 64,
    )
    assert [item["requirement_id"] for item in fault_result] == ["RPS-07"]

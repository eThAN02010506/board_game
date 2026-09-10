import json
import sqlite3
from pathlib import Path

import pytest

from ai_kp.evaluation.ui_journey_reconstruction import (
    reconstruct_ui_journey_anchor_evidence,
    reconstruct_ui_journey_evidence,
)
from ai_kp.platform.resolution.contracts import (
    EndingRule,
    ScenarioContract,
    StateCondition,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler


def _authority_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE campaigns (
          id TEXT PRIMARY KEY, title TEXT, system TEXT, ruleset_id TEXT,
          ruleset_version TEXT, created_at TEXT
        );
        CREATE TABLE campaign_sessions (
          id TEXT PRIMARY KEY, campaign_id TEXT, status TEXT, created_at TEXT, ended_at TEXT
        );
        CREATE TABLE session_members (
          id TEXT PRIMARY KEY, session_id TEXT, campaign_id TEXT, role TEXT, pc_id TEXT,
          revoked_at TEXT
        );
        CREATE TABLE campaign_investigators (
          campaign_id TEXT, investigator_id TEXT, legacy_pc_id TEXT, status TEXT
        );
        CREATE TABLE modules (
          id TEXT PRIMARY KEY, title TEXT, source_filename TEXT, source_hash TEXT
        );
        CREATE TABLE module_import_jobs (
          id TEXT PRIMARY KEY, campaign_id TEXT, module_id TEXT, status TEXT,
          updated_at TEXT
        );
        CREATE TABLE campaign_module_runs (
          id TEXT PRIMARY KEY, campaign_id TEXT, module_id TEXT, status TEXT,
          automation_level TEXT, state_json TEXT, started_at TEXT, completed_at TEXT
        );
        CREATE TABLE scenario_contract_jobs (
          id TEXT PRIMARY KEY, campaign_id TEXT, module_id TEXT, contract_key TEXT,
          status TEXT, stage TEXT, automation_level TEXT,
          model_configuration_version INTEGER, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE scenario_contract_versions (
          id TEXT PRIMARY KEY, module_id TEXT, contract_key TEXT, version INTEGER, status TEXT,
          contract_hash TEXT, contract_json TEXT, published_at TEXT
        );
        CREATE TABLE module_run_contract_bindings (
          contract_version_id TEXT, run_id TEXT, bound_at TEXT
        );
        CREATE TABLE model_configuration (
          provider_type TEXT, model TEXT, version INTEGER, api_key TEXT
        );
        CREATE TABLE player_actions (
          id TEXT PRIMARY KEY, campaign_id TEXT, session_id TEXT, status TEXT
        );
        CREATE TABLE player_action_adjudications (
          id TEXT PRIMARY KEY, action_id TEXT, confirmed_at TEXT
        );
        CREATE TABLE skill_checks (
          id TEXT PRIMARY KEY, campaign_id TEXT, session_id TEXT,
          player_action_id TEXT, passed INTEGER, pushed_from_check_id TEXT
        );
        CREATE TABLE skill_check_push_decisions (id TEXT PRIMARY KEY, check_id TEXT);
        CREATE TABLE parallel_action_batches (
          id TEXT PRIMARY KEY, campaign_id TEXT, session_id TEXT, run_id TEXT,
          contract_version_id TEXT, scenario_command_batch_id TEXT, status TEXT
        );
        CREATE TABLE turn_proposals (id TEXT PRIMARY KEY, campaign_id TEXT);
        CREATE TABLE scenario_contract_overlays (
          id TEXT PRIMARY KEY, run_id TEXT, status TEXT, sequence_no INTEGER,
          merged_contract_hash TEXT, merged_contract_json TEXT
        );
        CREATE TABLE scenario_run_states (
          run_id TEXT PRIMARY KEY, contract_version_id TEXT, state_version INTEGER,
          snapshot_json TEXT
        );
        CREATE TABLE scenario_command_batches (
          id TEXT PRIMARY KEY, run_id TEXT, expected_version INTEGER, result_version INTEGER,
          commands_json TEXT, snapshot_json TEXT, created_at TEXT
        );

        INSERT INTO campaigns VALUES (
          'campaign-1', 'AC-LONG Full AI test', 'coc7', 'coc7', '7e',
          '2026-08-24 02:00:00'
        );
        INSERT INTO campaign_sessions VALUES (
          'session-1', 'campaign-1', 'active', '2026-08-24 02:00:00', NULL
        );
        INSERT INTO modules VALUES (
          'module-1', 'Generic House', 'generic-house.docx',
          'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        );
        INSERT INTO module_import_jobs VALUES (
          'import-job-1', 'campaign-1', 'module-1', 'completed',
          '2026-08-24 02:00:04'
        );
        INSERT INTO campaign_module_runs VALUES (
          'run-1', 'campaign-1', 'module-1', 'completed', 'ai_kp',
          '{"ending_id":"ending-1"}', '2026-08-24 02:00:05', '2026-08-24 03:00:00'
        );
        INSERT INTO scenario_contract_jobs VALUES (
          'job-1', 'campaign-1', 'module-1', 'contract-key', 'succeeded', 'completed',
          'ai_kp', 3, '2026-08-24 02:00:06', '2026-08-24 02:00:08'
        );
        INSERT INTO module_run_contract_bindings VALUES (
          'contract-version-1', 'run-1', '2026-08-24 02:00:10'
        );
        INSERT INTO model_configuration VALUES (
          'openai-compatible', 'small-model', 3, 'sk-database-secret-must-never-leak'
        );
        INSERT INTO player_actions VALUES ('action-1', 'campaign-1', 'session-1', 'resolved');
        INSERT INTO player_action_adjudications VALUES (
          'adjudication-1', 'action-1', '2026-08-24 02:10:00'
        );
        INSERT INTO skill_checks VALUES (
          'check-1', 'campaign-1', 'session-1', 'action-1', 0, NULL
        );
        INSERT INTO skill_check_push_decisions VALUES ('decision-1', 'check-1');
        INSERT INTO parallel_action_batches VALUES (
          'batch-1', 'campaign-1', 'session-1', 'run-1',
          'contract-version-1', NULL, 'settled'
        );
        INSERT INTO turn_proposals VALUES ('proposal-1', 'campaign-1');
        """
    )
    contract = ScenarioContract(
        contract_id="contract-key",
        source_version=1,
        ruleset_id="coc7",
        title="Generic contract",
        endings=(
            EndingRule(
                ending_id="ending-1",
                title="Terminal state",
                all_conditions=(StateCondition(path="facts.done", operator="eq", value=True),),
            ),
        ),
    )
    connection.execute(
        "INSERT INTO scenario_contract_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "contract-version-1",
            "module-1",
            "contract-key",
            1,
            "published",
            ScenarioContractCompiler.contract_hash(contract),
            json.dumps(contract.model_dump(mode="json")),
            "2026-08-24 02:00:09",
        ),
    )
    for index in range(1, 5):
        connection.execute(
            "INSERT INTO session_members VALUES (?, 'session-1', 'campaign-1', 'player', ?, NULL)",
            (f"member-{index}", f"pc-{index}"),
        )
        connection.execute(
            "INSERT INTO campaign_investigators VALUES ('campaign-1', ?, ?, 'approved')",
            (f"investigator-{index}", f"pc-{index}"),
        )
    connection.commit()
    connection.close()


def _playwright_evidence() -> dict:
    return {
        "started_at": "2026-08-24T02:00:00+00:00",
        "completed_at": "2026-08-24T04:00:00+00:00",
        "campaign_id": "campaign-1",
        "authority_refs": {
            "campaign_ids": ["campaign-1"],
            "session_ids": ["session-1"],
            "episode_ids": [],
            "continuity_snapshot_ids": [],
            "member_ids": [f"member-{index}" for index in range(1, 5)],
            "investigator_ids": [f"investigator-{index}" for index in range(1, 5)],
            "module_import_job_ids": ["import-job-1"],
            "module_ids": ["module-1"],
            "module_run_ids": ["run-1"],
            "scenario_contract_job_ids": ["job-1"],
            "scenario_contract_version_ids": ["contract-version-1"],
            "action_ids": ["action-1"],
            "adjudication_ids": ["adjudication-1"],
            "check_ids": ["check-1"],
            "parallel_batch_ids": ["batch-1"],
            "public_turn_ids": ["proposal-1"],
            "scenario_command_batch_ids": [],
        },
        "mode": "ai_kp",
        "semantic_profile": "small",
        "player_count": 4,
        "sessions": [{"index": 1, "actions": []}],
        "source_commit": "abcdef1234567",
        "ui_journey_passed": True,
        "four_player_full_ai_passed": True,
    }


def test_reconstruction_uses_safe_authority_projection_and_ignores_pass_claims(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.sqlite3"
    _authority_database(database)

    result = reconstruct_ui_journey_evidence(_playwright_evidence(), database)

    assert result.strict_evidence["session_id"] == "session-1"
    assert result.strict_evidence["automation_mode"] == "full_ai"
    assert len(result.strict_evidence["players"]) == 4
    assert result.strict_evidence["module"]["sha256"] == "a" * 64
    assert result.strict_evidence["model_generation"] == {
        "provider": "openai-compatible",
        "model": "small-model",
        "generation_id": "model-configuration-version:3",
    }
    assert result.ignored_producer_claims == (
        "four_player_full_ai_passed",
        "ui_journey_passed",
    )
    assert result.verification.accepted is False
    assert "ui_journey_passed" not in result.strict_evidence
    assert "sk-database-secret" not in json.dumps(result.as_dict())
    assert any(fact["kind"] == "scenario_contract_lifecycle" for fact in result.authority_facts)
    assert any(fact["kind"] == "gameplay_counts" for fact in result.authority_facts)
    assert [
        item["kind"] for item in result.strict_evidence["observations"][:4]
    ] == [
        "ui_module_imported",
        "ui_scenario_reviewed",
        "ui_scenario_published",
        "ui_scenario_bound",
    ]
    assert [
        item["sequence"] for item in result.strict_evidence["observations"]
    ] == list(range(1, len(result.strict_evidence["observations"]) + 1))


def test_anchor_reconstruction_leaves_restart_to_the_long_checkpoint_gate(
    tmp_path: Path,
) -> None:
    database = tmp_path / "anchor.sqlite3"
    _authority_database(database)

    result = reconstruct_ui_journey_anchor_evidence(
        _playwright_evidence(), database
    )

    assert not any("backend stop/start" in gap for gap in result.gaps)
    assert not any("Continue before/after" in gap for gap in result.gaps)
    assert result.verification.accepted is False
    assert any("NPC actor identity" in gap for gap in result.gaps)


def test_setup_reconstruction_fails_closed_for_invalid_authority_timestamp(
    tmp_path: Path,
) -> None:
    database = tmp_path / "invalid-timestamp.sqlite3"
    _authority_database(database)
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE module_run_contract_bindings SET bound_at = 'not-a-timestamp'"
    )
    connection.commit()
    connection.close()

    result = reconstruct_ui_journey_anchor_evidence(
        _playwright_evidence(), database
    )

    assert "authority setup chain contains an invalid timestamp" in result.gaps
    assert not any(
        item["kind"].startswith("ui_")
        for item in result.strict_evidence["observations"]
    )


def test_reconstruction_reports_current_e2e_correlation_and_observation_gaps(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.sqlite3"
    _authority_database(database)
    evidence = _playwright_evidence()
    evidence["authority_refs"]["campaign_ids"] = []
    evidence.pop("completed_at")
    evidence.pop("source_commit")
    evidence["sessions"] = []

    result = reconstruct_ui_journey_evidence(evidence, database)

    assert result.verification.accepted is False
    assert "authority_refs.campaign_ids must identify exactly one UI-observed campaign" in result.gaps
    assert "playwright.completed_at is missing; the UI journey is not terminal" in result.gaps
    assert "Playwright evidence lacks the tested source_commit" in result.gaps
    assert "Playwright evidence contains no completed Session action traces" in result.gaps
    assert any("backend stop/start" in gap for gap in result.gaps)
    assert any("NPC actor identity" in gap for gap in result.gaps)


def test_reconstruction_emits_ending_only_after_deterministic_batch_replay(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.sqlite3"
    _authority_database(database)
    connection = sqlite3.connect(database)
    contract = ScenarioContract.model_validate(
        json.loads(
            connection.execute(
                "SELECT contract_json FROM scenario_contract_versions"
            ).fetchone()[0]
        )
    )
    kernel = ActionResolutionKernel.from_contract(contract)
    first_command = WorldCommand(kind="set_fact", path="begun", value=True)
    first = kernel.preflight(contract.initial_snapshot("run-1"), (first_command,))
    ending_command = WorldCommand(kind="set_fact", path="done", value=True)
    final = kernel.preflight(first, (ending_command,))
    connection.execute(
        "INSERT INTO scenario_run_states VALUES (?, ?, ?, ?)",
        (
            "run-1",
            "contract-version-1",
            final.run_version,
            json.dumps(final.model_dump(mode="json")),
        ),
    )
    connection.execute(
        "INSERT INTO scenario_command_batches VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "batch-1",
            "run-1",
            0,
            1,
            json.dumps([first_command.model_dump(mode="json")]),
            json.dumps(first.model_dump(mode="json")),
            "2026-08-24 02:30:00",
        ),
    )
    connection.execute(
        "INSERT INTO scenario_command_batches VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "batch-ending",
            "run-1",
            1,
            2,
            json.dumps([ending_command.model_dump(mode="json")]),
            json.dumps(final.model_dump(mode="json")),
            "2026-08-24 03:00:00",
        ),
    )
    connection.commit()
    connection.close()

    evidence = _playwright_evidence()
    evidence["authority_refs"]["scenario_command_batch_ids"] = ["batch-ending"]

    result = reconstruct_ui_journey_evidence(evidence, database)

    ending = next(
        item
        for item in result.strict_evidence["observations"]
        if item["kind"] == "authoritative_ending"
    )
    assert ending["ending_id"] == "ending-1"
    assert ending["authority_event_id"] == "batch-ending"
    assert len(ending["authority_fingerprint"]) == 64
    replay_fact = next(
        item for item in result.authority_facts if item["kind"] == "authoritative_ending_replay"
    )
    assert replay_fact["expected_version"] == 1
    assert replay_fact["result_version"] == 2

    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE scenario_run_states SET snapshot_json = ? WHERE run_id = 'run-1'",
        (json.dumps(first.model_dump(mode="json")),),
    )
    connection.commit()
    connection.close()

    tampered = reconstruct_ui_journey_evidence(evidence, database)

    assert not any(
        item["kind"] == "authoritative_ending"
        for item in tampered.strict_evidence["observations"]
    )
    assert "deterministic ending replay differs from persisted authoritative state" in tampered.gaps


@pytest.mark.parametrize(
    "secret_mutation",
    [
        {"api_key": "redacted"},
        {"join_code": "must-not-be-recorded"},
        {"headers": {"x-test": "not-an-evidence-field"}},
        {"storage": {"campaign": "not-an-evidence-field"}},
        {"semantic_profile": "Bearer credential-that-must-not-be-read"},
    ],
)
def test_reconstruction_rejects_secret_bearing_playwright_input(
    tmp_path: Path, secret_mutation: dict
) -> None:
    database = tmp_path / "authority.sqlite3"
    _authority_database(database)
    evidence = _playwright_evidence()
    evidence.update(secret_mutation)

    with pytest.raises(ValueError, match="forbidden secret-bearing material"):
        reconstruct_ui_journey_evidence(evidence, database)


def test_reconstruction_rejects_campaign_locator_mismatch(tmp_path: Path) -> None:
    database = tmp_path / "authority.sqlite3"
    _authority_database(database)

    result = reconstruct_ui_journey_evidence(
        _playwright_evidence(), database, campaign_id="another-campaign"
    )

    assert result.verification.accepted is False
    assert result.strict_evidence["observations"] == []
    assert "requested campaign_id differs from the Playwright-recorded campaign_id" in result.gaps


@pytest.mark.parametrize(
    ("key", "foreign_id", "expected_gap"),
    [
        (
            "session_ids",
            "session-foreign",
            "authority_refs.session_ids must identify exactly one Session for this vertical journey",
        ),
        (
            "module_run_ids",
            "run-foreign",
            "authority_refs.module_run_ids must identify exactly one run for this vertical journey",
        ),
    ],
)
def test_reconstruction_fails_closed_for_multiple_session_or_run_refs(
    tmp_path: Path, key: str, foreign_id: str, expected_gap: str
) -> None:
    database = tmp_path / "authority.sqlite3"
    _authority_database(database)
    evidence = _playwright_evidence()
    evidence["authority_refs"][key].append(foreign_id)

    result = reconstruct_ui_journey_evidence(evidence, database)

    assert result.verification.accepted is False
    assert expected_gap in result.gaps


def test_reconstruction_fails_closed_for_foreign_action_id(tmp_path: Path) -> None:
    database = tmp_path / "authority.sqlite3"
    _authority_database(database)
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO player_actions VALUES ('action-foreign', 'campaign-else', 'session-else', 'resolved')"
    )
    connection.commit()
    connection.close()
    evidence = _playwright_evidence()
    evidence["authority_refs"]["action_ids"].append("action-foreign")

    result = reconstruct_ui_journey_evidence(evidence, database)

    assert result.verification.accepted is False
    assert "a UI-observed action is missing or belongs to a foreign campaign/Session" in result.gaps


def test_reconstruction_selects_exact_refs_when_campaign_has_other_sessions_and_runs(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.sqlite3"
    _authority_database(database)
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO campaign_sessions VALUES "
        "('session-2', 'campaign-1', 'active', '2026-08-24 05:00:00', NULL)"
    )
    connection.execute(
        "INSERT INTO campaign_module_runs VALUES "
        "('run-2', 'campaign-1', 'module-1', 'active', 'ai_kp', '{}', "
        "'2026-08-24 05:00:00', NULL)"
    )
    connection.commit()
    connection.close()

    result = reconstruct_ui_journey_evidence(_playwright_evidence(), database)

    assert result.strict_evidence["session_id"] == "session-1"
    run_facts = [fact for fact in result.authority_facts if fact["kind"] == "module_run"]
    assert [fact["run_id"] for fact in run_facts] == ["run-1"]


def test_reconstruction_never_promotes_producer_pass_claims(tmp_path: Path) -> None:
    database = tmp_path / "authority.sqlite3"
    _authority_database(database)
    evidence = _playwright_evidence()
    evidence["authority_chain_passed"] = True

    result = reconstruct_ui_journey_evidence(evidence, database)

    assert "authority_chain_passed" in result.ignored_producer_claims
    assert "authority_chain_passed" not in json.dumps(result.strict_evidence)


def test_long_ui_corroboration_is_reconstructable_but_never_authoritative(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.sqlite3"
    _authority_database(database)
    evidence = _playwright_evidence()
    evidence["evidence_kind"] = "long_campaign_ui_journey"
    evidence["sessions"] = [{"index": 1, "actions": []}, {"index": 2, "actions": []}]
    evidence["ui_corroboration_events"] = [
        {
            "kind": "authoritative_ending_visible",
            "observed_at": "2026-08-24T03:00:00+00:00",
            "session_index": 1,
        },
        {
            "kind": "session_end_visible",
            "observed_at": "2026-08-24T03:01:00+00:00",
            "session_index": 1,
        },
        {
            "kind": "continue_campaign_visible",
            "observed_at": "2026-08-24T03:02:00+00:00",
            "session_index": 1,
        },
        {
            "kind": "session_end_visible",
            "observed_at": "2026-08-24T03:03:00+00:00",
            "session_index": 2,
        },
    ]
    evidence["long_campaign_passed"] = True
    evidence["ui_journey_passed"] = False

    result = reconstruct_ui_journey_evidence(evidence, database)

    assert len(result.ui_corroboration) == 4
    assert not any("typed UI evidence" in gap for gap in result.gaps)
    assert result.ignored_producer_claims == (
        "four_player_full_ai_passed",
        "long_campaign_passed",
        "ui_journey_passed",
    )
    assert "ui_corroboration_events" not in result.strict_evidence
    assert "long_campaign_passed" not in json.dumps(result.strict_evidence)
    # UI milestones corroborate the journey but cannot satisfy the strict
    # restart/gameplay authority invariants on their own.
    assert result.verification.accepted is False


@pytest.mark.parametrize(
    "mutation",
    [
        {"kind": "claimed_pass", "observed_at": "2026-08-24T03:00:00+00:00", "session_index": 1},
        {
            "kind": "session_end_visible",
            "observed_at": "2026-08-24T03:00:00+00:00",
            "session_index": 1,
            "passed": True,
        },
    ],
)
def test_long_ui_corroboration_rejects_unknown_kinds_and_fields(
    tmp_path: Path, mutation: dict
) -> None:
    database = tmp_path / "authority.sqlite3"
    _authority_database(database)
    evidence = _playwright_evidence()
    evidence["ui_corroboration_events"] = [mutation]

    result = reconstruct_ui_journey_evidence(evidence, database)

    assert result.ui_corroboration == ()
    assert any(
        "not an allowed UI event" in gap or "contains unknown fields: passed" in gap
        for gap in result.gaps
    )
    assert result.verification.accepted is False

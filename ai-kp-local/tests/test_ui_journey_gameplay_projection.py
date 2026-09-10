from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta

from ai_kp.evaluation.ui_journey_evidence import verify_real_ui_journey_anchor
from ai_kp.evaluation.ui_journey_gameplay_projection import (
    project_gameplay_observations,
)
from ai_kp.platform.resolution.contracts import (
    ResolutionPreview,
    ScenarioContract,
    ScenarioSnapshot,
    WorldCommand,
)
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE scenario_contract_versions (
          id TEXT, status TEXT, contract_hash TEXT, contract_json TEXT
        );
        CREATE TABLE module_run_contract_bindings (run_id TEXT, contract_version_id TEXT);
        CREATE TABLE scenario_contract_overlays (
          run_id TEXT, status TEXT, sequence_no INTEGER,
          merged_contract_hash TEXT, merged_contract_json TEXT
        );
        CREATE TABLE player_actions (
          id TEXT, campaign_id TEXT, session_id TEXT, member_id TEXT,
          status TEXT, resolved_at TEXT, proposal_id TEXT
        );
        CREATE TABLE turn_proposals (
          id TEXT, campaign_id TEXT, status TEXT, public_narration TEXT,
          applied_at TEXT
        );
        CREATE TABLE proposal_actions (
          id TEXT, proposal_id TEXT, action_type TEXT, payload_json TEXT
        );
        CREATE TABLE player_action_adjudications (
          id TEXT, action_id TEXT, proposal_id TEXT, mode TEXT, status TEXT,
          selected_skill TEXT
        );
        CREATE TABLE player_action_adjudication_events (
          id TEXT, adjudication_id TEXT, event_type TEXT,
          actor_member_id TEXT, created_at TEXT
        );
        CREATE TABLE skill_checks (
          id TEXT, campaign_id TEXT, session_id TEXT, player_action_id TEXT,
          skill_key TEXT, skill_name TEXT, difficulty TEXT, passed INTEGER,
          success_level TEXT, status TEXT, resolved_at TEXT,
          pushed_from_check_id TEXT, created_at TEXT
        );
        CREATE TABLE skill_check_push_decisions (
          id TEXT, check_id TEXT, actor_member_id TEXT, decision TEXT
        );
        CREATE TABLE scenario_command_batches (
          id TEXT, run_id TEXT, batch_kind TEXT, idempotency_key TEXT,
          expected_version INTEGER, result_version INTEGER,
          preview_hash TEXT, preview_json TEXT, commands_json TEXT,
          snapshot_json TEXT, created_at TEXT
        );
        CREATE TABLE parallel_action_batches (
          id TEXT, campaign_id TEXT, session_id TEXT, run_id TEXT,
          status TEXT, scenario_command_batch_id TEXT, settled_at TEXT
        );
        CREATE TABLE parallel_action_batch_items (
          batch_id TEXT, action_id TEXT, adjudication_id TEXT,
          priority INTEGER
        );
        """
    )
    contract = ScenarioContract.model_validate(
        {
            "contract_id": "contract-1",
            "source_version": 1,
            "ruleset_id": "coc7",
            "title": "Projection contract",
            "initial_scene_id": "scene-hall",
            "locations": [{"location_id": "scene-hall", "title": "Hall"}],
            "entities": [
                {"entity_id": "npc-witness", "entity_type": "npc", "title": "Witness"}
            ],
            "response_obligations": [
                {
                    "obligation_id": "obligation-warning",
                    "entity_id": "npc-witness",
                    "facts_to_convey": ["The north door is sealed."],
                }
            ],
        }
    )
    payload = contract.model_dump(mode="json")
    connection.execute(
        "INSERT INTO scenario_contract_versions VALUES (?, ?, ?, ?)",
        (
            "contract-version-1",
            "published",
            ScenarioContractCompiler.contract_hash(contract),
            json.dumps(payload),
        ),
    )
    connection.execute(
        "INSERT INTO module_run_contract_bindings VALUES (?, ?)",
        ("run-1", "contract-version-1"),
    )
    return connection


def _snapshot(version: int) -> dict:
    actions = ("action-direct", "action-skill", "action-failure")[:version]
    return ScenarioSnapshot(
        run_id="run-1",
        contract_id="contract-1",
        scenario_version=1,
        run_version=version,
        scene_id="scene-hall",
        facts={"done": {action_id: True for action_id in actions}},
        entities={"npc-witness": "active"},
        entity_runtime={"npc-witness": {"status": "active"}},
        events=tuple(
            {"type": "action_resolved", "payload": {"action_id": action_id}}
            for action_id in actions
        ),
    ).model_dump(mode="json")


def _insert_action(
    connection: sqlite3.Connection,
    *,
    action_id: str,
    proposal_id: str,
    member_id: str,
    narration: str,
    mode: str,
    selected_skill: str | None = None,
    kernel: dict | None = None,
) -> None:
    connection.execute(
        "INSERT INTO player_actions VALUES (?, 'campaign-1', 'session-1', ?, "
        "'resolved', '2026-08-24 03:00:09', ?)",
        (action_id, member_id, proposal_id),
    )
    connection.execute(
        "INSERT INTO turn_proposals VALUES (?, 'campaign-1', 'approved', ?, "
        "'2026-08-24 03:00:08')",
        (proposal_id, narration),
    )
    adjudication_id = f"adjudication-{action_id}"
    connection.execute(
        "INSERT INTO player_action_adjudications VALUES (?, ?, ?, ?, 'confirmed', ?)",
        (adjudication_id, action_id, proposal_id, mode, selected_skill),
    )
    connection.execute(
        "INSERT INTO player_action_adjudication_events VALUES (?, ?, 'confirmed', ?, "
        "'2026-08-24 03:00:02')",
        (f"event-{action_id}", adjudication_id, member_id),
    )
    if kernel is not None:
        connection.execute(
            "INSERT INTO proposal_actions VALUES (?, ?, 'kernel_resolution', ?)",
            (f"kernel-{action_id}", proposal_id, json.dumps(kernel)),
        )


def _insert_action_batch(
    connection: sqlite3.Connection,
    *,
    batch_id: str,
    action_id: str,
    expected_version: int,
    outcome: str = "success",
) -> None:
    effect = WorldCommand(kind="set_fact", path=f"done.{action_id}", value=True)
    draft = ResolutionPreview(
        action_id=action_id,
        run_id="run-1",
        contract_id="contract-1",
        scenario_version=1,
        run_version=expected_version,
        operator_id=f"operator-{action_id}",
        policy="automatic",
        allowed=True,
        reason="The contract allows this bounded action.",
        always_commands=(effect,),
    )
    preview_hash = hashlib.sha256(
        json.dumps(
            draft.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    preview = draft.model_copy(update={"preview_hash": preview_hash})
    connection.execute(
        "INSERT INTO scenario_command_batches VALUES (?, 'run-1', 'action', ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            batch_id,
            f"action:{action_id}:v{expected_version}:{outcome}",
            expected_version,
            expected_version + 1,
            preview_hash,
            json.dumps(preview.model_dump(mode="json")),
            json.dumps(
                [
                    {
                        "kind": "emit_event",
                        "event_type": "action_resolved",
                        "payload": {"action_id": action_id},
                    },
                    effect.model_dump(mode="json"),
                ]
            ),
            json.dumps(_snapshot(expected_version + 1)),
            f"2026-08-24 03:00:0{expected_version + 3}",
        ),
    )


def test_projects_grounded_npc_direct_check_deviation_and_parallel_branches() -> None:
    connection = _connection()
    _insert_action(
        connection,
        action_id="action-direct",
        proposal_id="proposal-direct",
        member_id="member-1",
        narration="The investigator secures the ledger and records its chain of custody.",
        mode="direct_resolution",
        kernel={
            "intent_plan": {
                "goal": "secure and compare the ledger",
                "steps": [
                    {
                        "step_id": "secure",
                        "operator_id": "operator-secure",
                        "goal": "secure ledger",
                        "method": "bag the ledger",
                        "target": "ledger",
                        "requirements": [
                            {
                                "requirement_id": "constraint-access",
                                "category": "location",
                                "description": "must remain in the hall",
                                "source": "state",
                                "state_path": "scene_id",
                                "comparison": "eq",
                                "expected_value": "scene-hall",
                            }
                        ],
                        "effects": [
                            {
                                "effect_id": "effect-secure",
                                "role": "progress",
                                "applies_on": "success",
                                "command_kind": "set_fact",
                                "target_ref": "secured",
                                "value": True,
                                "description": "ledger is secured",
                            }
                        ],
                    },
                    {
                        "step_id": "compare",
                        "operator_id": "operator-compare",
                        "goal": "compare ledger",
                        "method": "compare dates",
                        "target": "manifest",
                        "depends_on": ["secure"],
                        "requirements": [
                            {
                                "requirement_id": "constraint-time",
                                "category": "dependency",
                                "description": "ledger must be secured first",
                                "source": "prior_step",
                                "producer_step_id": "secure",
                                "produced_ref": "secured",
                            }
                        ],
                        "effects": [
                            {
                                "effect_id": "effect-compare",
                                "role": "progress",
                                "applies_on": "success",
                                "command_kind": "set_fact",
                                "target_ref": "compared",
                                "value": True,
                                "description": "manifest is compared",
                            }
                        ],
                    },
                ],
            }
        },
    )
    _insert_action_batch(
        connection,
        batch_id="batch-direct",
        action_id="action-direct",
        expected_version=0,
    )
    _insert_action(
        connection,
        action_id="action-skill",
        proposal_id="proposal-skill",
        member_id="member-2",
        narration="The investigator searches the damaged shipping records.",
        mode="skill_check",
        selected_skill="Library Use",
    )
    _insert_action_batch(
        connection,
        batch_id="batch-skill",
        action_id="action-skill",
        expected_version=1,
    )
    connection.execute(
        "INSERT INTO skill_checks VALUES ("
        "'check-success', 'campaign-1', 'session-1', 'action-skill', "
        "'library_use', 'Library Use', 'hard', 1, 'hard', 'resolved', "
        "'2026-08-24 03:00:05', NULL, '2026-08-24 03:00:03')"
    )
    connection.execute(
        "INSERT INTO turn_proposals VALUES ("
        "'proposal-consequence', 'campaign-1', 'approved', "
        "'The dated manifest reveals the exact vessel and unloading berth.', "
        "'2026-08-24 03:00:07')"
    )
    connection.execute(
        "INSERT INTO proposal_actions VALUES ("
        "'basis-success', 'proposal-consequence', 'check_consequence_basis', ?) ",
        (
            json.dumps(
                {
                    "player_action_id": "action-skill",
                    "check_ids": ["check-success"],
                    "result_fingerprint": "c" * 64,
                }
            ),
        ),
    )
    connection.execute(
        "INSERT INTO player_actions VALUES ("
        "'action-npc', 'campaign-1', 'session-1', 'member-3', 'resolved', "
        "'2026-08-24 03:00:06', 'proposal-npc')"
    )
    connection.execute(
        "INSERT INTO turn_proposals VALUES ("
        "'proposal-npc', 'campaign-1', 'approved', 'NPC response', "
        "'2026-08-24 03:00:06')"
    )
    connection.execute(
        "INSERT INTO proposal_actions VALUES ("
        "'tabletop-npc', 'proposal-npc', 'tabletop_turn', ?)",
        (
            json.dumps(
                {
                    "route": "roleplay",
                    "response": {
                        "public_narration": (
                            "The witness lowers his voice. The north door is sealed."
                        ),
                        "speaker_entity_ids": ["npc-witness"],
                        "actor_traces": [{"entity_id": "npc-witness"}],
                    },
                }
            ),
        ),
    )
    _insert_action(
        connection,
        action_id="action-failure",
        proposal_id="proposal-failure",
        member_id="member-4",
        narration="The investigator attempts to persuade the guarded archivist.",
        mode="skill_check",
        selected_skill="Persuade",
    )
    connection.execute(
        "UPDATE player_actions SET resolved_at = '2026-08-24 03:00:14' "
        "WHERE id = 'action-failure'"
    )
    _insert_action_batch(
        connection,
        batch_id="batch-failure",
        action_id="action-failure",
        expected_version=2,
        outcome="failure",
    )
    connection.execute(
        "INSERT INTO skill_checks VALUES ("
        "'check-failure', 'campaign-1', 'session-1', 'action-failure', "
        "'persuade', 'Persuade', 'regular', 0, 'failure', 'resolved', "
        "'2026-08-24 03:00:11', NULL, '2026-08-24 03:00:10')"
    )
    connection.execute(
        "INSERT INTO skill_check_push_decisions VALUES ("
        "'decision-accept', 'check-failure', 'member-4', 'accept_failure')"
    )
    connection.execute(
        "INSERT INTO turn_proposals VALUES ("
        "'proposal-failure-consequence', 'campaign-1', 'approved', "
        "'The archivist closes the records room and bars access for the day.', "
        "'2026-08-24 03:00:12')"
    )
    connection.execute(
        "INSERT INTO proposal_actions VALUES ("
        "'basis-failure', 'proposal-failure-consequence', 'check_consequence_basis', ?)",
        (
            json.dumps(
                {
                    "player_action_id": "action-failure",
                    "check_ids": ["check-failure"],
                    "result_fingerprint": "d" * 64,
                }
            ),
        ),
    )
    connection.execute(
        "INSERT INTO scenario_command_batches VALUES ("
        "'batch-parallel-command', 'run-1', 'parallel', 'parallel:key', 3, 4, ?, ?, ?, ?, "
        "'2026-08-24 03:00:10')",
        (
            "f" * 64,
            json.dumps({"batch_id": "parallel-1"}),
            json.dumps([]),
            json.dumps(_snapshot(4)),
        ),
    )
    connection.execute(
        "INSERT INTO parallel_action_batches VALUES ("
        "'parallel-1', 'campaign-1', 'session-1', 'run-1', 'settled', "
        "'batch-parallel-command', '2026-08-24 03:00:11')"
    )
    connection.executemany(
        "INSERT INTO parallel_action_batch_items VALUES ('parallel-1', ?, ?, ?)",
        [
            ("action-direct", "adjudication-action-direct", 2),
            ("action-skill", "adjudication-action-skill", 1),
        ],
    )

    result = project_gameplay_observations(
        connection,
        campaign_id="campaign-1",
        session_id="session-1",
        run_id="run-1",
        authority_refs={
            "action_ids": (
                "action-direct",
                "action-skill",
                "action-npc",
                "action-failure",
            ),
            "scenario_command_batch_ids": (
                "batch-direct",
                "batch-skill",
                "batch-failure",
                "batch-parallel-command",
            ),
            "parallel_batch_ids": ("parallel-1",),
        },
    )

    by_kind = {item["kind"]: item for item in result.observations}
    assert {
        "npc_response",
        "direct_resolution",
        "skill_confirmed",
        "check_success",
        "check_failure_cost",
        "failure_followup",
        "bounded_deviation",
        "parallel_settlement",
    }.issubset(by_kind)
    assert by_kind["npc_response"]["npc_entity_id"] == "npc-witness"
    assert by_kind["npc_response"]["knowledge_fact_ids"] == [
        "obligation-warning:fact:1"
    ]
    assert by_kind["skill_confirmed"]["confirmed_by_member_id"] == "member-2"
    assert by_kind["parallel_settlement"]["regroup_location_id"] == "scene-hall"
    assert all("secret" not in json.dumps(item) for item in result.observations)

    setup = [
        {
            "kind": "ui_module_imported",
            "document_id": "import-1",
            "module_sha256": "a" * 64,
        },
        {
            "kind": "ui_scenario_reviewed",
            "review_id": "review-1",
            "decision": "approved",
            "finding_ids": [],
        },
        {
            "kind": "ui_scenario_published",
            "contract_id": "contract-version-1",
            "contract_version": "1",
        },
        {
            "kind": "ui_scenario_bound",
            "contract_id": "contract-version-1",
            "contract_version": "1",
        },
    ]
    ending = {
        "kind": "authoritative_ending",
        "ending_id": "ending-1",
        "authority_event_id": "batch-ending",
        "authority_fingerprint": "e" * 64,
    }
    ordered = [*setup, *result.observations, ending]
    start = datetime(2026, 8, 24, tzinfo=UTC)
    observations = [
        {
            **item,
            "sequence": index,
            "session_id": "session-1",
            "observed_at": (start + timedelta(seconds=index)).isoformat(),
        }
        for index, item in enumerate(ordered, start=1)
    ]
    verified = verify_real_ui_journey_anchor(
        {
            "schema_version": 1,
            "journey_id": "journey-1",
            "session_id": "session-1",
            "automation_mode": "full_ai",
            "produced_at": (start + timedelta(minutes=1)).isoformat(),
            "module": {"sha256": "a" * 64, "artifact_name": "module.pdf"},
            "source_commit": "abc1234",
            "model_generation": {
                "provider": "openai-compatible",
                "model": "local-small-model",
                "generation_id": "configuration-1",
            },
            "ruleset": {"id": "coc7", "version": "1"},
            "players": [
                {"member_id": f"member-{index}", "investigator_id": f"pc-{index}"}
                for index in range(1, 5)
            ],
            "observations": observations,
        }
    )
    assert verified.accepted, verified.blockers


def test_rejects_uncorrelated_command_batch() -> None:
    connection = _connection()
    _insert_action(
        connection,
        action_id="action-direct",
        proposal_id="proposal-direct",
        member_id="member-1",
        narration="The investigator secures a durable piece of evidence.",
        mode="direct_resolution",
    )
    _insert_action_batch(
        connection,
        batch_id="batch-direct",
        action_id="action-direct",
        expected_version=0,
    )
    connection.execute(
        "UPDATE scenario_command_batches SET idempotency_key = 'action:foreign:v0:success'"
    )

    result = project_gameplay_observations(
        connection,
        campaign_id="campaign-1",
        session_id="session-1",
        run_id="run-1",
        authority_refs={
            "action_ids": ("action-direct",),
            "scenario_command_batch_ids": ("batch-direct",),
        },
    )

    assert not result.observations
    assert any("does not match an observed action preview" in gap for gap in result.gaps)


def test_malformed_authority_timestamp_becomes_a_gap() -> None:
    connection = _connection()
    _insert_action(
        connection,
        action_id="action-direct",
        proposal_id="proposal-direct",
        member_id="member-1",
        narration="The investigator secures a durable piece of evidence.",
        mode="direct_resolution",
    )
    _insert_action_batch(
        connection,
        batch_id="batch-direct",
        action_id="action-direct",
        expected_version=0,
    )
    connection.execute(
        "UPDATE turn_proposals SET applied_at = 'invalid' WHERE id = 'proposal-direct'"
    )

    result = project_gameplay_observations(
        connection,
        campaign_id="campaign-1",
        session_id="session-1",
        run_id="run-1",
        authority_refs={
            "action_ids": ("action-direct",),
            "scenario_command_batch_ids": ("batch-direct",),
        },
    )

    assert not result.observations
    assert "resolved-action gameplay authority could not be validated" in result.gaps

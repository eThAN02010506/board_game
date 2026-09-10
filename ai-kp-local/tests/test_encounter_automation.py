from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_kp.application.auto_kp_queue_service import AutoKpQueueService
from ai_kp.application.encounter_automation_service import EncounterAutomationService
from ai_kp.application.gameplay_service import (
    EncounterCreateCommand,
    GameplayCommand,
    GameplayService,
)
from ai_kp.application.session_continuity_service import SessionContinuityService
from ai_kp.application.session_service import SessionService
from ai_kp.application.session_zero_service import SessionZeroService
from ai_kp.bootstrap.settings import Settings
from ai_kp.director.enemy_turn import (
    ConstrainedEnemyTurnAdapter,
    EnemyTurnOutput,
    parse_enemy_turn_output,
)
from ai_kp.director.turn_output import StructuredOutputError
from ai_kp.infrastructure.auto_kp_worker import process_claimed_auto_kp_job
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.sessions.session_zero import CampaignSetupConfig


class EnemyDirector:
    def __init__(self, output: EnemyTurnOutput | Exception):
        self.output = output
        self.calls = 0
        self.snapshots: list[dict] = []

    async def select_enemy_turn(self, **values) -> EnemyTurnOutput:
        self.calls += 1
        self.snapshots.append(values["snapshot"])
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


class SequenceLlm:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0

    async def complete(self, _messages, temperature: float = 0.7) -> str:
        response = self.responses[self.calls]
        self.calls += 1
        return response


def _fixture(tmp_path: Path) -> tuple[Repository, dict, object, dict]:
    connection = connect(tmp_path / "encounter-automation.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    campaign = repo.create_campaign("Restart-safe enemy turns")
    session = SessionService(repo).create(str(campaign["id"]))
    kp = repo.authenticate_access_token(str(session["access_token"]))
    assert kp is not None
    SessionZeroService(repo).save_config(
        kp,
        expected_version=0,
        config=CampaignSetupConfig(
            ruleset_id=str(campaign["ruleset_id"]),
            ruleset_version=str(campaign["ruleset_version"]),
            worldview="A generic conflict with no scenario-specific names",
            hosting_mode="ai_kp",
            expected_player_count=1,
            style={"combat": 3, "roleplay": 3},
            idle_policy="defend",
            idle_timeout_seconds=30,
        ),
    )
    first_npc = repo.create_npc(
        "Guard A", secret_notes="Protect the sealed order at any cost."
    )
    second_npc = repo.create_npc("Guard B")
    repo.link_npc_to_campaign(str(campaign["id"]), str(first_npc["id"]))
    repo.link_npc_to_campaign(str(campaign["id"]), str(second_npc["id"]))
    encounter = GameplayService(repo).create_encounter(
        str(campaign["id"]),
        kp,
        EncounterCreateCommand(
            kind="combat",
            title="Generic opposing sides",
            participants=(
                {
                    "participant_id": "side-a",
                    "name": "Guard A",
                    "npc_id": first_npc["id"],
                    "side": "a",
                    "dex": 80,
                    "max_hp": 10,
                    "current_hp": 10,
                    "secret_notes": "must never be copied from participant input",
                    "action_profiles": [
                        {
                            "action_key": "strike",
                            "label": "Strike",
                            "kind": "melee",
                            "skill_target": 70,
                            "damage_expression": "1d3",
                        }
                    ],
                },
                {
                    "participant_id": "side-b",
                    "name": "Guard B",
                    "npc_id": second_npc["id"],
                    "side": "b",
                    "dex": 20,
                    "max_hp": 10,
                    "current_hp": 10,
                    "dodge_target": 25,
                    "action_profiles": [
                        {
                            "action_key": "counter",
                            "label": "Counter",
                            "kind": "melee",
                            "skill_target": 40,
                            "damage_expression": "1d3",
                        }
                    ],
                },
            ),
        ),
    )
    repo.connection.commit()
    return repo, campaign, kp, encounter


def test_enemy_turn_adapter_repairs_once_and_rejects_unstructured_output() -> None:
    llm = SequenceLlm(
        [
            "not json",
            json.dumps(
                {
                    "action_key": "defend",
                    "target_id": None,
                    "public_intent": "It raises its guard.",
                    "reason": "No safe target.",
                }
            ),
        ]
    )
    result = asyncio.run(ConstrainedEnemyTurnAdapter(llm).select(snapshot={}))
    assert result.action_key == "defend"
    assert llm.calls == 2
    with pytest.raises(StructuredOutputError):
        parse_enemy_turn_output("not json")


def test_enemy_agent_selection_is_bounded_and_durable(tmp_path: Path) -> None:
    repo, _campaign, kp, encounter = _fixture(tmp_path)
    try:
        job = AutoKpQueueService(repo).enqueue_encounter_turn(str(encounter["id"]))
        assert job is not None
        assert job["job_type"] == "encounter_turn"
        director = EnemyDirector(
            EnemyTurnOutput(
                action_key="strike",
                target_id="side-b",
                public_intent="Guard A lunges at the opposing guard.",
                reason="The target belongs to the opposing side.",
            )
        )
        result = asyncio.run(
            EncounterAutomationService(repo).advance(
                str(encounter["id"]),
                phase="enemy",
                policy="agent",
                identity=kp,
                director=director,
                source_model="small-test-model",
            )
        )
        assert result["status"] == "succeeded"
        assert result["turn"]["status"] == "committed"
        assert result["turn"]["selection"]["agent"]["prompt_version"] == "enemy-turn.v1"
        assert "Guard A lunges" in result["public_message"]
        assert "规则结算完成" in result["public_message"] or any(
            marker in result["public_message"]
            for marker in ("命中", "没有命中", "反击")
        )
        assert director.calls == 1
        assert {item["action_key"] for item in director.snapshots[0]["allowed_actions"]} >= {
            "strike",
            "defend",
            "end_turn",
        }
        assert director.snapshots[0]["targets"] == [
            {"id": "side-b", "name": "Guard B", "conditions": []}
        ]
        current = repo.get_coc7_encounter(str(encounter["id"]))
        assert current["version"] == 1
        assert current["state"]["turn_order"][current["turn_index"]] == "side-b"
        public_payloads = [
            str(row["payload_json"])
            for row in repo.connection.execute(
                "SELECT payload_json FROM realtime_events WHERE resource_id = ?",
                (encounter["id"],),
            ).fetchall()
        ]
        assert any("lunges" in payload for payload in public_payloads)
        assert all("private_motivation" not in payload for payload in public_payloads)
        messages = repo.list_visible_table_messages(kp, limit=20)
        assert messages[-1]["content"] == result["public_message"]
        assert "Protect the sealed order" not in messages[-1]["content"]
    finally:
        repo.connection.close()


def test_enemy_turn_retry_reuses_checkpointed_random_evidence(
    tmp_path: Path,
) -> None:
    repo, _campaign, kp, encounter = _fixture(tmp_path)
    database_path = tmp_path / "encounter-automation.sqlite3"
    director = EnemyDirector(
        EnemyTurnOutput(
            action_key="strike",
            target_id="side-b",
            public_intent="Guard A attacks.",
            reason="Bounded test action.",
        )
    )
    original = GameplayService.command_encounter
    captured: list[dict] = []

    def crash_after_checkpoint(self, encounter_id, identity, command):
        captured.append(command.payload)
        raise RuntimeError("crash after durable random checkpoint")

    try:
        with patch.object(GameplayService, "command_encounter", crash_after_checkpoint):
            with pytest.raises(RuntimeError, match="durable random checkpoint"):
                asyncio.run(
                    EncounterAutomationService(repo).advance(
                        str(encounter["id"]),
                        phase="enemy",
                        policy="agent",
                        identity=kp,
                        director=director,
                        source_model="small-test-model",
                    )
                )
        checkpoint = repo.get_encounter_automation_turn_for_version(
            str(encounter["id"]), 0, "side-a", "enemy"
        )
        assert checkpoint is not None and checkpoint["status"] == "checkpointed"

        # Reopen the actual database to prove recovery does not depend on an
        # in-memory service, connection, director, or random generator state.
        repo.connection.close()
        connection = connect(database_path)
        init_db(connection)
        repo = Repository(connection)

        def resume(self, encounter_id, identity, command):
            captured.append(command.payload)
            return original(self, encounter_id, identity, command)

        with patch.object(GameplayService, "command_encounter", resume):
            result = asyncio.run(
                EncounterAutomationService(repo).advance(
                    str(encounter["id"]),
                    phase="enemy",
                    policy="agent",
                    identity=kp,
                    director=EnemyDirector(AssertionError("agent must not rerun")),
                    source_model="small-test-model",
                )
            )
        assert result["turn"]["status"] == "committed"
        assert captured[0] == captured[1]
        assert director.calls == 1
    finally:
        repo.connection.close()


def test_enemy_agent_failure_uses_non_attacking_liveness_fallback(tmp_path: Path) -> None:
    repo, _campaign, kp, encounter = _fixture(tmp_path)
    try:
        result = asyncio.run(
            EncounterAutomationService(repo).advance(
                str(encounter["id"]),
                phase="enemy",
                policy="agent",
                identity=kp,
                director=EnemyDirector(RuntimeError("malformed small-model response")),
                source_model="weak-model",
            )
        )
        assert result["turn"]["selection"]["action_key"] == "defend"
        assert result["turn"]["selection"]["agent"]["fallback"] is True
        assert (
            result["turn"]["prepared_command"]["payload"]["inner"]["action_type"]
            == "defend"
        )
    finally:
        repo.connection.close()


def test_idle_defend_policy_is_deterministic_and_safety_pause_resumes(
    tmp_path: Path,
) -> None:
    repo, _campaign, kp, encounter = _fixture(tmp_path)
    try:
        # Production encounter creation derives this link from an approved
        # investigator. The focused automation test changes only that one
        # normalized field so it can exercise the player-idle branch without
        # rebuilding the investigator approval workflow.
        state = encounter["state"]
        state["participants"][0]["investigator_id"] = "investigator-fixture"
        repo.connection.execute(
            "UPDATE coc7_encounters SET state_json = ? WHERE id = ?",
            (json.dumps(state, ensure_ascii=False, sort_keys=True), encounter["id"]),
        )
        repo.connection.commit()

        initial = AutoKpQueueService(repo).enqueue_encounter_turn(str(encounter["id"]))
        assert initial is not None
        assert initial["payload"]["phase"] == "idle_player"
        assert initial["payload"]["policy"] == "defend"
        assert initial["next_run_at"] > initial["created_at"]

        safety = SessionZeroService(repo).trigger_safety(kp, response_kind="pause")
        paused = asyncio.run(
            EncounterAutomationService(repo).advance(
                str(encounter["id"]),
                phase="idle_player",
                policy="defend",
                identity=kp,
                director=EnemyDirector(AssertionError("safety pause must not call AI")),
                source_model="small-test-model",
            )
        )
        assert paused["status"] == "needs_attention"
        assert repo.get_coc7_encounter(str(encounter["id"]))["version"] == 0

        SessionZeroService(repo).resolve_safety(
            kp,
            event_id=str(safety["event"]["id"]),
            resolution_kind="resume",
        )
        resumed_job = AutoKpQueueService(repo).enqueue_encounter_turn(
            str(encounter["id"])
        )
        assert resumed_job is not None
        assert resumed_job["id"] != initial["id"]
        director = EnemyDirector(AssertionError("defend policy must not call AI"))
        resumed = asyncio.run(
            EncounterAutomationService(repo).advance(
                str(encounter["id"]),
                phase="idle_player",
                policy="defend",
                identity=kp,
                director=director,
                source_model="small-test-model",
            )
        )
        assert resumed["turn"]["selection"]["agent"]["used"] is False
        assert resumed["turn"]["selection"]["action_key"] == "defend"
        assert director.calls == 0
    finally:
        repo.connection.close()


def test_worker_executes_current_turn_and_safely_supersedes_stale_job(
    tmp_path: Path,
) -> None:
    repo, _campaign, kp, encounter = _fixture(tmp_path)
    try:
        queue = AutoKpQueueService(repo)
        job = queue.enqueue_encounter_turn(str(encounter["id"]))
        assert job is not None
        repo.connection.execute(
            "UPDATE auto_kp_jobs SET next_run_at = CURRENT_TIMESTAMP WHERE id = ?",
            (job["id"],),
        )
        repo.connection.commit()
        claimed = repo.claim_next_auto_kp_job(worker_id="encounter-test-worker")
        assert claimed is not None
        director = EnemyDirector(
            EnemyTurnOutput(
                action_key="defend",
                target_id=None,
                public_intent="Guard A braces for the next exchange.",
                reason="A bounded non-target action.",
            )
        )
        with patch(
            "ai_kp.infrastructure.auto_kp_worker._director", return_value=director
        ):
            process_claimed_auto_kp_job(
                repo.connection,
                claimed,
                settings=Settings(
                    db_path=tmp_path / "encounter-automation.sqlite3",
                    admin_token="test-admin",
                    local_admin_enabled=False,
                ),
            )
        completed = repo.get_auto_kp_job(str(job["id"]))
        assert completed["status"] == "succeeded"
        assert completed["stage"] == "encounter_turn"
        assert director.calls == 1

        stale = queue.enqueue_encounter_turn(str(encounter["id"]))
        assert stale is not None
        current = repo.get_coc7_encounter(str(encounter["id"]))
        GameplayService(repo).command_encounter(
            str(encounter["id"]),
            kp,
            GameplayCommand(
                command_id="advance-before-idle-job",
                expected_version=int(current["version"]),
                command_type="advance_turn",
                payload={},
            ),
        )
        repo.connection.execute(
            "UPDATE auto_kp_jobs SET next_run_at = CURRENT_TIMESTAMP WHERE id = ?",
            (stale["id"],),
        )
        repo.connection.commit()
        claimed_stale = repo.claim_next_auto_kp_job(worker_id="encounter-test-worker")
        assert claimed_stale is not None
        process_claimed_auto_kp_job(
            repo.connection,
            claimed_stale,
            settings=Settings(
                db_path=tmp_path / "encounter-automation.sqlite3",
                admin_token="test-admin",
                local_admin_enabled=False,
            ),
        )
        superseded = repo.get_auto_kp_job(str(stale["id"]))
        assert superseded["status"] == "succeeded"
        assert superseded["stage"] == "encounter_turn_superseded"
    finally:
        repo.connection.close()


def test_rules_engine_completes_combat_when_only_one_declared_side_can_act(
    tmp_path: Path,
) -> None:
    repo, _campaign, kp, encounter = _fixture(tmp_path)
    try:
        defeated = next(
            item
            for item in encounter["state"]["participants"]
            if item["participant_id"] == "side-b"
        )
        item = repo.create_inventory_item(
            campaign_id=encounter["campaign_id"],
            item_type="gear",
            public_name="Dropped lantern",
            public_description="An ordinary storm lantern.",
            publicly_listed=True,
            quantity=1,
            is_unique=True,
            holder_kind="npc",
            holder_id=defeated["npc_id"],
            weight_units=1,
            source_refs=[{"kind": "test", "id": "generic-loot"}],
        )
        result = GameplayService(repo).command_encounter(
            str(encounter["id"]),
            kp,
            GameplayCommand(
                command_id="terminal-side-damage",
                expected_version=0,
                command_type="damage",
                payload={"target_id": "side-b", "damage": 10},
            ),
        )
        assert result["encounter"]["status"] == "completed"
        assert result["event"]["result"]["encounter_outcome"] == {
            "reason": "only_one_declared_side_can_act",
            "remaining_sides": ["a"],
        }
        assert AutoKpQueueService(repo).enqueue_encounter_turn(str(encounter["id"])) is None
        loot = repo.get_inventory_item(str(item["id"]))
        assert (loot["holder_kind"], loot["holder_id"]) == (
            "loot",
            encounter["id"],
        )
        assert any(
            event["command_type"] == "encounter_loot"
            for event in repo.list_inventory_ledger_events(encounter["campaign_id"])
        )
    finally:
        repo.connection.close()


def test_idle_pause_policy_requeues_same_turn_after_campaign_resume(
    tmp_path: Path,
) -> None:
    repo, campaign, kp, encounter = _fixture(tmp_path)
    try:
        current_revision = repo.get_current_session_zero_revision(str(campaign["id"]))
        assert current_revision is not None
        config = dict(current_revision["config"])
        config["idle_policy"] = "pause"
        SessionZeroService(repo).save_config(
            kp,
            expected_version=int(current_revision["version"]),
            config=CampaignSetupConfig.model_validate(config),
        )
        state = encounter["state"]
        state["participants"][0]["investigator_id"] = "investigator-fixture"
        repo.connection.execute(
            "UPDATE coc7_encounters SET state_json = ? WHERE id = ?",
            (json.dumps(state, ensure_ascii=False, sort_keys=True), encounter["id"]),
        )
        repo.connection.commit()
        before = AutoKpQueueService(repo).enqueue_encounter_turn(str(encounter["id"]))
        assert before is not None and before["payload"]["policy"] == "pause"
        paused = asyncio.run(
            EncounterAutomationService(repo).advance(
                str(encounter["id"]),
                phase="idle_player",
                policy="pause",
                identity=kp,
                director=EnemyDirector(AssertionError("pause must not call AI")),
                source_model="none",
                expected_episode_id=str(before["payload"]["episode_id"]),
                expected_episode_version=int(before["payload"]["episode_version"]),
            )
        )
        assert paused["stage"] == "encounter_idle_pause"
        episode = repo.get_current_campaign_episode(kp.session_id)
        assert episode is not None and episode["status"] == "paused"
        assert AutoKpQueueService(repo).enqueue_encounter_turn(str(encounter["id"])) is None

        SessionContinuityService(repo).transition(
            kp, target="in_progress", expected_version=int(episode["version"])
        )
        after = AutoKpQueueService(repo).enqueue_encounter_turn(str(encounter["id"]))
        assert after is not None
        assert after["id"] != before["id"]
        assert after["payload"]["episode_version"] > before["payload"]["episode_version"]
    finally:
        repo.connection.close()

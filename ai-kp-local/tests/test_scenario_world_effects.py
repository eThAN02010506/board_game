from pathlib import Path

import pytest

from ai_kp.application.errors import InvalidInputError
from ai_kp.application.kernel_action_service import KernelActionService
from ai_kp.application.parallel_kernel_service import ParallelKernelService
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.application.scenario_effect_service import ScenarioEffectService
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import TurnService
from ai_kp.application.world_entity_state_service import (
    SetWorldEntityStateCommand,
    WorldEntityStateService,
)
from ai_kp.application.world_service import WorldService
from ai_kp.platform.resolution.contracts import ScenarioContract, WorldCommand
from ai_kp.platform.resolution.parallel import (
    ParallelActionKernel,
    ParallelIntent,
    ParallelSettlementRequest,
)
from tests.test_scenario_entity_identity import setup_identity


def world_command(dimension="condition", value="read", visibility="kp"):
    return {
        "kind": "set_world_entity_state",
        "entity_id": "ship-log",
        "path": dimension,
        "value": value,
        "payload": {"visibility": visibility},
    }


def setup_world_effect(tmp_path: Path, *, invalid=False):
    repo, module_id, _, graph, raw = setup_identity(tmp_path)
    raw["operators"][1]["success_commands"].append(world_command())
    if invalid:
        raw["operators"][1]["success_commands"].append(world_command("undeclared"))
    service = ScenarioContractService(repo)
    result, draft = service.compile_draft(module_id, raw, created_by_member_id=None)
    assert result.report.release_ready, result.report
    assert draft is not None
    published = service.publish(draft["id"], expected_row_version=1, published_by_member_id=None)
    campaign_id = repo.get_module(module_id)["campaign_id"]
    sessions = SessionService(repo)
    host = sessions.create(campaign_id)
    player = sessions.join(host["join_code"], display_name="Player")
    kp = repo.authenticate_access_token(host["access_token"])
    player_identity = repo.authenticate_access_token(player["access_token"])
    run = repo.start_campaign_module_run(
        campaign_id=campaign_id,
        module_id=module_id,
        current_scene_key=None,
        active_spoiler_tags=[],
        state={},
        started_by_member_id=kp.member_id,
    )
    service.bind_run(run["id"], published["id"])
    event = repo.append_event(
        campaign_id=campaign_id, actor_type="system", event_type="encounter", summary="发现日志"
    )
    entity = repo.create_campaign_world_entity(
        campaign_id=campaign_id,
        entity_kind="item",
        name="航海日志",
        visibility="kp",
        origin_kind="module_source",
        origin_ref=graph["id"],
        created_from_event_id=event["id"],
        data={"state_dimensions": ["condition"]},
    )
    repo.commit()
    return repo, campaign_id, run, kp, player_identity, entity


def settle(repo, run, kp, player, mode):
    if mode == "parallel":
        service = ParallelKernelService(repo)
        service.initialize_run(run["id"])
        return service.settle(
            run["id"],
            ParallelSettlementRequest(
                batch_id="world-batch",
                intents=(
                    ParallelIntent(
                        action_id="world-action",
                        actor_id=player.member_id,
                        operator_id="interview-witness",
                        outcome="success",
                    ),
                    ParallelIntent(
                        action_id="world-action-2",
                        actor_id="other-player",
                        operator_id="interview-witness",
                        outcome="success",
                    ),
                ),
            ),
            idempotency_key="world-effect-test",
            identity=kp,
        ).batch
    action = TurnService(repo).submit_player_action(
        player, action_text="询问日志的内容", client_action_id="world-single"
    )
    service = KernelActionService(repo)
    proposal, _ = service.prepare_manual(
        action, kp, operator_id="interview-witness", requested_skill_key=None
    )
    service.commit(proposal, outcome="success", identity=kp)
    return repo.list_scenario_command_batches(run["id"])[0]


@pytest.mark.parametrize("mode", ["single", "parallel"])
def test_world_effect_noop_receipt_and_replay_preserve_later_human_change(tmp_path, mode):
    repo, campaign, run, kp, player, entity = setup_world_effect(tmp_path)
    try:
        service = WorldEntityStateService(repo)
        service.set_state(
            campaign,
            entity["id"],
            kp,
            SetWorldEntityStateCommand(
                expected_version=0,
                dimension="condition",
                value="read",
                visibility="kp",
                idempotency_key="human-world-initial",
            ),
        )
        batch = settle(repo, run, kp, player, mode)
        assert repo.get_campaign_world_entity(entity["id"])["state_version"] == 2
        effect = ScenarioEffectService(repo).apply_batch(batch, kp, campaign_id=campaign)
        receipt = effect["world_entity_effects"][0]["change"]
        assert receipt["source_kind"] == "rules_kernel"
        assert receipt["from_value"] == receipt["to_value"] == "read"
        service.set_state(
            campaign,
            entity["id"],
            kp,
            SetWorldEntityStateCommand(
                expected_version=2,
                dimension="condition",
                value="destroyed",
                visibility="secret",
                idempotency_key="human-world-later",
            ),
        )
        repo.commit()
        replay = ScenarioEffectService(repo).apply_batch(batch, kp, campaign_id=campaign)
        assert replay["world_entity_effects"][0]["change"]["id"] == receipt["id"]
        assert repo.get_campaign_world_entity(entity["id"])["state_version"] == 3
        assert (
            repo.get_campaign_world_entity_state(entity["id"], "condition")["value"] == "destroyed"
        )
        tampered = {**batch, "commands": [world_command(value="stolen")]}
        with pytest.raises(ValueError, match="persisted batch"):
            ScenarioEffectService(repo).apply_batch(tampered, kp, campaign_id=campaign)
    finally:
        repo.connection.close()


@pytest.mark.parametrize("mode", ["single", "parallel"])
def test_invalid_world_effect_rolls_back_scenario_and_prior_world_write(tmp_path, mode):
    repo, _campaign, run, kp, player, entity = setup_world_effect(tmp_path, invalid=True)
    try:
        baseline = repo.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        with pytest.raises(InvalidInputError, match="declare state dimension"):
            settle(repo, run, kp, player, mode)
        repo.commit()  # Mimic a worker catching the error then committing its job record.
        assert repo.get_campaign_world_entity(entity["id"])["state_version"] == 0
        assert repo.get_campaign_world_entity_state(entity["id"], "condition") is None
        assert repo.list_scenario_command_batches(run["id"]) == []
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 0
        assert (
            repo.connection.execute(
                "SELECT count(*) FROM events WHERE event_type='world_entity.state_changed'"
            ).fetchone()[0]
            == 0
        )
        assert repo.connection.execute("SELECT count(*) FROM events").fetchone()[0] >= baseline
    finally:
        repo.connection.close()


def test_world_effect_shape_and_explicit_identity_are_required(tmp_path):
    for changes in ({"path": "bad.path"}, {"value": []}, {"payload": {}}, {"value": 1.5}):
        with pytest.raises(ValueError):
            WorldCommand.model_validate({**world_command(), **changes})
    repo, _, _, _, raw = setup_identity(tmp_path)
    try:
        raw["operators"][1]["success_commands"].append(world_command())
        raw["entities"][0].pop("module_entity_id")
        with pytest.raises(ValueError, match="explicit module entity identity"):
            ScenarioContract.model_validate(raw)
    finally:
        repo.connection.close()


def test_world_effect_writes_shared_ledger_and_keeps_secret_out_of_player_graph(tmp_path):
    repo, campaign, run, kp, player, entity = setup_world_effect(tmp_path)
    try:
        settle(repo, run, kp, player, "parallel")
        state = repo.get_campaign_world_entity_state(entity["id"], "condition")
        assert state["value"] == "read"
        assert state["visibility"] == "kp"
        assert repo.get_campaign_world_entity(entity["id"])["state_version"] == 1
        assert WorldService(repo).list_world_entities(campaign, view="player")["entities"] == []
    finally:
        repo.connection.close()


def test_rules_source_upgrade_preserves_existing_receipts_and_foreign_keys(tmp_path):
    from ai_kp.infrastructure.database.migrations import v0089_campaign_world_entity_states as old
    from ai_kp.infrastructure.database.migrations import v0091_world_state_rules_source as upgrade

    repo, campaign, _, kp, _, entity = setup_world_effect(tmp_path)
    try:
        WorldEntityStateService(repo).set_state(
            campaign,
            entity["id"],
            kp,
            SetWorldEntityStateCommand(
                expected_version=0,
                dimension="condition",
                value="read",
                visibility="kp",
                idempotency_key="migration-existing-receipt",
            ),
        )
        table = "campaign_world_entity_state_changes"
        before = repo.connection.execute(f"SELECT * FROM {table}").fetchall()
        # Reconstruct precisely the v89 leaf ledger with its existing data.
        repo.connection.execute(old._STATEMENTS[1].replace(table, table + "_old"))
        repo.connection.execute(f"INSERT INTO {table}_old SELECT * FROM {table}")
        repo.connection.execute(f"DROP TABLE {table}")
        repo.connection.execute(f"ALTER TABLE {table}_old RENAME TO {table}")
        repo.connection.execute(old._STATEMENTS[2])
        upgrade.migrate(repo.connection)
        upgrade.migrate(repo.connection)
        assert repo.connection.execute(f"SELECT * FROM {table}").fetchall() == before
        assert repo.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        WorldEntityStateService(repo).set_state(
            campaign,
            entity["id"],
            kp,
            SetWorldEntityStateCommand(
                expected_version=1,
                dimension="condition",
                value="read",
                visibility="kp",
                idempotency_key="migration-rules-receipt",
                source_kind="rules_kernel",
            ),
        )
        assert repo.get_campaign_world_entity(entity["id"])["state_version"] == 2
    finally:
        repo.connection.close()


@pytest.mark.parametrize("visibility,value", [("kp", "destroyed"), ("secret", "read")])
def test_parallel_world_dimension_value_or_visibility_conflicts(tmp_path, visibility, value):
    repo, _, run, _, _, _ = setup_world_effect(tmp_path)
    try:
        contract = repo.get_module_run_contract_binding(run["id"])["contract"]
        first = next(op for op in contract.operators if op.operator_id == "interview-witness")
        second = first.model_copy(
            update={
                "operator_id": "other-world-write",
                "success_commands": (
                    WorldCommand.model_validate(world_command(value=value, visibility=visibility)),
                ),
            }
        )
        contract = contract.model_copy(update={"operators": (first, second)})
        snapshot = contract.initial_snapshot(run["id"])
        result = ParallelActionKernel(contract).preview(
            snapshot,
            ParallelSettlementRequest(
                batch_id="conflicting-world-writes",
                intents=(
                    ParallelIntent(
                        action_id="a",
                        actor_id="one",
                        operator_id=first.operator_id,
                        outcome="success",
                    ),
                    ParallelIntent(
                        action_id="b",
                        actor_id="two",
                        operator_id=second.operator_id,
                        outcome="success",
                    ),
                ),
            ),
        )
        assert result.status == "conflict"
        assert result.commands == ()
        assert result.resulting_snapshot == snapshot
    finally:
        repo.connection.close()

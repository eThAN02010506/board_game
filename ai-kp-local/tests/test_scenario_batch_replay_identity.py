"""Retries must preserve the exact committed action, actor and outcome."""

from pathlib import Path

import pytest

from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.platform.resolution.contracts import ActionIntent
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.parallel import ParallelActionKernel
from tests.scenario_contract_testkit import kernel_authority_basis
from tests.test_parallel_action_kernel import parallel_contract
from tests.test_parallel_kernel_service import compatible_request, setup_bound_run


def test_parallel_repository_replays_committed_version_without_reapplying(tmp_path: Path):
    with db_session(tmp_path / "parallel-replay.sqlite3") as connection:
        repo = Repository(connection)
        run = setup_bound_run(repo)
        initial = repo.initialize_scenario_run_state(run["id"])
        preview = ParallelActionKernel(parallel_contract()).preview(
            initial["snapshot"], compatible_request()
        )
        basis = kernel_authority_basis(repo, run["id"], preview.actions[0].preview)
        basis = basis.model_copy(update={"preview_hash": preview.settlement_hash})
        args = {
            "run_id": run["id"],
            "idempotency_key": "parallel-retry",
            "preview": preview,
            "authority_basis": basis,
        }
        first = repo.commit_parallel_scenario_batch(**args)
        next_run = repo.start_campaign_module_run(
            campaign_id=run["campaign_id"],
            module_id=run["module_id"],
            current_scene_key=None,
            active_spoiler_tags=[],
            state={},
            started_by_member_id=None,
        )
        session = repo.create_campaign_session(run["campaign_id"])
        joined = repo.join_campaign_session(session["join_code"], display_name="Player")
        identity = repo.authenticate_access_token(joined["access_token"])
        assert identity is not None
        pending = repo.create_player_action(identity, action_text="下一场景的行动")
        connection.commit()
        with db_session(tmp_path / "parallel-replay.sqlite3") as retry_connection:
            second = Repository(retry_connection).commit_parallel_scenario_batch(**args)
        assert second["id"] == first["id"]
        assert repo.get_player_action(pending["id"])["status"] == "submitted"
        assert repo.get_campaign_module_run(next_run["id"])["status"] == "active"
        with pytest.raises(ValueError, match="different settlement"):
            repo.commit_parallel_scenario_batch(
                **{
                    **args,
                    "preview": preview.model_copy(update={"batch_id": "changed-batch"}),
                }
            )
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 1
        assert len(repo.list_scenario_command_batches(run["id"])) == 1


@pytest.mark.parametrize("changed", ["actor", "outcome", "preview"])
def test_action_receipt_rejects_reused_key_with_different_semantics(tmp_path: Path, changed: str):
    with db_session(tmp_path / f"action-{changed}.sqlite3") as connection:
        repo = Repository(connection)
        run = setup_bound_run(repo)
        initial = repo.initialize_scenario_run_state(run["id"])
        contract = repo.get_module_run_contract_binding(run["id"])["contract"]
        preview = ActionResolutionKernel.from_contract(contract).preview(
            initial["snapshot"],
            ActionIntent(
                action_id="retry-action",
                actor_id="pc-a",
                operator_id="observe-quietly",
                goal="Observe quietly",
            ),
        )
        args = {
            "run_id": run["id"],
            "idempotency_key": "same-key",
            "preview": preview,
            "authority_basis": kernel_authority_basis(repo, run["id"], preview),
            "outcome": "success",
            "actor_id": "pc-a",
        }
        first = repo.commit_action_scenario_batch(**args)
        connection.commit()
        assert repo.commit_action_scenario_batch(**args)["id"] == first["id"]
        altered = dict(args)
        if changed == "actor":
            altered["actor_id"] = "pc-b"
        elif changed == "outcome":
            altered["outcome"] = "failure"
        else:
            altered["preview"] = preview.model_copy(update={"action_id": "different-action"})
        with pytest.raises(ValueError):
            repo.commit_action_scenario_batch(**altered)
        connection.commit()
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 1
        assert len(repo.list_scenario_command_batches(run["id"])) == 1

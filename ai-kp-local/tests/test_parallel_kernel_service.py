from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_kp.application.errors import ConflictError
from ai_kp.application.parallel_kernel_service import ParallelKernelService
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.platform.resolution.contracts import ActionIntent
from ai_kp.platform.resolution.kernel import (
    ActionResolutionKernel,
    operator_outcome_path,
)
from ai_kp.platform.resolution.parallel import (
    ParallelActionKernel,
    ParallelIntent,
    ParallelSettlementRequest,
)
from tests.scenario_contract_testkit import (
    bind_payload_to_module,
    kernel_authority_basis,
)
from tests.test_parallel_action_kernel import parallel_contract
from tests.test_scenario_contract_service import create_module


def setup_bound_run(repo: Repository):
    campaign = repo.create_campaign("Parallel kernel campaign")
    module = create_module(repo, campaign["id"])
    contract = parallel_contract()
    service = ScenarioContractService(repo)
    _, draft = service.compile_draft(
        module["id"],
        bind_payload_to_module(
            repo, module["id"], contract.model_dump(mode="json")
        ),
        created_by_member_id=None,
    )
    assert draft is not None
    published = service.publish(
        draft["id"], expected_row_version=1, published_by_member_id=None
    )
    run = repo.start_campaign_module_run(
        campaign_id=campaign["id"],
        module_id=module["id"],
        current_scene_key=None,
        active_spoiler_tags=[],
        state={},
        started_by_member_id=None,
    )
    service.bind_run(run["id"], published["id"])
    return run


def compatible_request() -> ParallelSettlementRequest:
    return ParallelSettlementRequest(
        batch_id="batch-1",
        intents=(
            ParallelIntent(
                action_id="a",
                actor_id="pc-a",
                operator_id="find-mark",
                outcome="success",
            ),
            ParallelIntent(
                action_id="b", actor_id="pc-b", operator_id="open-panel"
            ),
        ),
    )


def test_parallel_service_commits_one_evented_batch_and_is_idempotent(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-kernel.sqlite3") as connection:
        repo = Repository(connection)
        run = setup_bound_run(repo)
        service = ParallelKernelService(repo)
        initial = service.initialize_run(run["id"])

        result = service.settle(
            run["id"], compatible_request(), idempotency_key="parallel:batch-1"
        )
        repeated = service.settle(
            run["id"], compatible_request(), idempotency_key="parallel:batch-1"
        )

        assert initial["state_version"] == 0
        assert result.preview.status == "ready"
        assert result.batch is not None
        assert repeated.batch is not None
        assert repeated.batch["id"] == result.batch["id"]
        assert len(repo.list_scenario_command_batches(run["id"])) == 1
        current = repo.get_scenario_run_state(run["id"])
        assert current["state_version"] == 1
        assert current["snapshot"].status == "completed"
        assert result.batch["commands"] == [
            item.model_dump(mode="json") for item in result.preview.commands
        ]
        assert result.batch["authority_basis"] == {
            "schema_version": "kernel-authority-basis.v1",
            "run_id": run["id"],
            "contract_version_id": initial["contract_version_id"],
            "contract_hash": repo.get_module_run_contract_binding(run["id"])[
                "contract_hash"
            ],
            "state_version": 0,
            "preview_hash": result.preview.settlement_hash,
        }


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("run_id", "run-tampered"),
        ("state_version", 7),
        ("preview_hash", "0" * 64),
        ("contract_hash", "0" * 64),
    ),
)
def test_parallel_repository_rejects_tampered_shared_authority_without_writes(
    tmp_path: Path,
    field: str,
    value: str | int,
) -> None:
    with db_session(tmp_path / f"parallel-authority-{field}.sqlite3") as connection:
        repo = Repository(connection)
        run = setup_bound_run(repo)
        state = repo.initialize_scenario_run_state(run["id"])
        preview = ParallelActionKernel(parallel_contract()).preview(
            state["snapshot"], compatible_request()
        )
        basis = kernel_authority_basis(
            repo,
            run["id"],
            preview.actions[0].preview,
        ).model_copy(
            update={"preview_hash": preview.settlement_hash, field: value}
        )

        repo.begin_scenario_command_batch()
        with pytest.raises(ValueError, match="authority|different run"):
            repo.commit_parallel_scenario_batch(
                run_id=run["id"],
                idempotency_key=f"parallel:tampered-{field}",
                preview=preview,
                authority_basis=basis,
            )
        repo.rollback_scenario_command_batch()

        assert repo.get_scenario_run_state(run["id"])["state_version"] == 0
        assert repo.list_scenario_command_batches(run["id"]) == []


def test_parallel_service_rejects_reusing_a_key_for_a_different_request(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-key-collision.sqlite3") as connection:
        repo = Repository(connection)
        run = setup_bound_run(repo)
        service = ParallelKernelService(repo)
        service.initialize_run(run["id"])
        service.settle(
            run["id"], compatible_request(), idempotency_key="parallel:same-key"
        )
        changed = compatible_request().model_copy(
            update={"batch_id": "a-different-logical-batch"}
        )

        with pytest.raises(ValueError, match="different parallel settlement"):
            service.settle(
                run["id"], changed, idempotency_key="parallel:same-key"
            )

        batches = repo.list_scenario_command_batches(run["id"])
        assert len(batches) == 1
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 1


def test_parallel_service_rechecks_idempotency_after_acquiring_the_write_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with db_session(tmp_path / "parallel-key-race.sqlite3") as connection:
        repo = Repository(connection)
        run = setup_bound_run(repo)
        service = ParallelKernelService(repo)
        service.initialize_run(run["id"])
        original = service.settle(
            run["id"],
            compatible_request(),
            idempotency_key="parallel:raced-retry",
        )
        connection.commit()
        get_by_key = repo.get_scenario_command_batch_by_key
        calls = 0

        def commit_appears_while_waiting(
            run_id: str, idempotency_key: str
        ) -> dict | None:
            nonlocal calls
            calls += 1
            if calls == 1:
                return None
            return get_by_key(run_id, idempotency_key)

        monkeypatch.setattr(
            repo,
            "get_scenario_command_batch_by_key",
            commit_appears_while_waiting,
        )

        replay = service.settle(
            run["id"],
            compatible_request(),
            idempotency_key="parallel:raced-retry",
        )

        assert calls == 2
        assert replay.batch is not None and original.batch is not None
        assert replay.batch["id"] == original.batch["id"]
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 1


def test_conflict_and_stale_preview_do_not_write_partial_state(tmp_path: Path) -> None:
    with db_session(tmp_path / "parallel-conflict.sqlite3") as connection:
        repo = Repository(connection)
        run = setup_bound_run(repo)
        service = ParallelKernelService(repo)
        initial = service.initialize_run(run["id"])
        conflict = ParallelSettlementRequest(
            batch_id="conflict",
            intents=(
                ParallelIntent(
                    action_id="a", actor_id="pc-a", operator_id="claim-key-a"
                ),
                ParallelIntent(
                    action_id="b", actor_id="pc-b", operator_id="claim-key-b"
                ),
            ),
        )

        result = service.settle(
            run["id"], conflict, idempotency_key="parallel:conflict"
        )

        assert result.preview.status == "conflict"
        assert result.batch is None
        assert repo.get_scenario_run_state(run["id"])["snapshot"] == initial["snapshot"]
        assert repo.list_scenario_command_batches(run["id"]) == []

        stale_preview = ParallelActionKernel(parallel_contract()).preview(
            initial["snapshot"], compatible_request()
        )
        stale_basis = kernel_authority_basis(
            repo,
            run["id"],
            stale_preview.actions[0].preview,
        ).model_copy(update={"preview_hash": stale_preview.settlement_hash})
        service.settle(
            run["id"], compatible_request(), idempotency_key="parallel:fresh"
        )
        repo.begin_scenario_command_batch()
        with pytest.raises(ValueError, match="state changed"):
            repo.commit_parallel_scenario_batch(
                run_id=run["id"],
                idempotency_key="parallel:stale",
                preview=stale_preview,
                authority_basis=stale_basis,
            )
        repo.rollback_scenario_command_batch()


@pytest.mark.parametrize(
    ("snapshot_field", "tampered_value"),
    (("run_id", "run-tampered"), ("run_version", 7)),
)
def test_parallel_service_rejects_incoherent_snapshot_without_partial_writes(
    tmp_path: Path,
    snapshot_field: str,
    tampered_value: str | int,
) -> None:
    with db_session(tmp_path / f"parallel-invalid-{snapshot_field}.sqlite3") as connection:
        repo = Repository(connection)
        run = setup_bound_run(repo)
        service = ParallelKernelService(repo)
        initialized = service.initialize_run(run["id"])
        snapshot_payload = initialized["snapshot"].model_dump(mode="json")
        snapshot_payload[snapshot_field] = tampered_value
        connection.execute(
            "UPDATE scenario_run_states SET snapshot_json = ? WHERE run_id = ?",
            (json.dumps(snapshot_payload), run["id"]),
        )
        connection.commit()
        tampered_state = repo.get_scenario_run_state(run["id"])

        with pytest.raises(
            ConflictError,
            match="Scenario state metadata does not match its snapshot",
        ):
            service.settle(
                run["id"],
                compatible_request(),
                idempotency_key=f"parallel:invalid-{snapshot_field}",
            )

        assert repo.list_scenario_command_batches(run["id"]) == []
        persisted = repo.get_scenario_run_state(run["id"])
        assert persisted["state_version"] == tampered_state["state_version"] == 0
        assert persisted["snapshot"] == tampered_state["snapshot"]


def test_single_noop_action_commits_a_queryable_resolution_event(tmp_path: Path) -> None:
    with db_session(tmp_path / "single-noop-event.sqlite3") as connection:
        repo = Repository(connection)
        run = setup_bound_run(repo)
        state = repo.initialize_scenario_run_state(run["id"])
        contract = repo.get_module_run_contract_binding(run["id"])["contract"]
        kernel = ActionResolutionKernel.from_contract(contract)
        preview = kernel.preview(
            state["snapshot"],
            ActionIntent(
                action_id="quiet-1",
                actor_id="pc-a",
                operator_id="observe-quietly",
                goal="Observe quietly",
            ),
        )

        batch = repo.commit_action_scenario_batch(
            run_id=run["id"],
            idempotency_key="action:quiet-1",
            preview=preview,
            authority_basis=kernel_authority_basis(repo, run["id"], preview),
            outcome="success",
        )

        assert [item["kind"] for item in batch["commands"]] == ["emit_event"]
        snapshot = repo.get_scenario_run_state(run["id"])["snapshot"]
        assert kernel.state_value(
            snapshot, operator_outcome_path("observe-quietly")
        ) == (True, "success")

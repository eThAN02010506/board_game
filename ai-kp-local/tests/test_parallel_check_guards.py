from __future__ import annotations

from pathlib import Path

import pytest

from ai_kp.application.check_service import (
    CheckService,
    CreateCheckCommand,
    CreateOpposedCheckCommand,
    OpposedSideCommand,
    ResolveCheckCommand,
)
from ai_kp.application.parallel_action_workflow_service import (
    ParallelActionWorkflowService,
)
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from tests import test_parallel_kernel_service as parallel_kernel_fixtures
from tests.test_parallel_action_kernel import parallel_contract
from tests.test_parallel_action_workflow_service import (
    _prepare,
    _workflow_fixture,
)


def _awaiting_skill_batch(
    repo: Repository,
    fixture: dict,
    *,
    idempotency_key: str,
):
    workflow = ParallelActionWorkflowService(repo)
    prepared = _prepare(repo, fixture, idempotency_key)
    assert prepared.batch is not None
    first_item, second_item = prepared.batch["items"]
    first = workflow.confirm_item(
        str(prepared.batch["id"]),
        str(first_item["action_id"]),
        expected_batch_version=int(prepared.batch["version"]),
        expected_adjudication_version=int(first_item["adjudication"]["version"]),
        selected_skill_key="spot_hidden",
        identity=fixture["first"],
    )
    assert first.batch is not None
    awaiting = workflow.confirm_item(
        str(prepared.batch["id"]),
        str(second_item["action_id"]),
        expected_batch_version=int(first.batch["version"]),
        expected_adjudication_version=int(second_item["adjudication"]["version"]),
        selected_skill_key=None,
        identity=fixture["second"],
    )
    assert awaiting.batch is not None
    assert awaiting.status == "awaiting_checks"
    assert len(awaiting.checks) == 1
    return workflow, awaiting


def _resolve(
    repo: Repository,
    fixture: dict,
    check_id: str,
    *,
    roll: int,
) -> dict:
    return CheckService(repo).resolve(
        check_id,
        fixture["first"],
        ResolveCheckCommand(
            input_method="physical",
            ones_digit=roll % 10,
            tens_digits=(roll // 10,),
        ),
    )


def test_unbatched_check_mutations_keep_the_normal_service_path(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "normal-check-mutation.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        check = CheckService(repo).create(
            str(fixture["run"]["campaign_id"]),
            fixture["kp"],
            CreateCheckCommand(
                skill_name="Spot Hidden",
                roller_member_id=fixture["first"].member_id,
                pc_id=fixture["first"].pc_id,
                target=60,
            ),
        )

        resolved = _resolve(
            repo,
            fixture,
            str(check["id"]),
            roll=1,
        )
        overridden = CheckService(repo).override(
            str(resolved["id"]),
            fixture["kp"],
            success_level="failure",
            passed=False,
            reason="A normal KP correction remains legal outside a batch.",
        )

        assert overridden["status"] == "overridden"
        assert overridden["passed"] is False


def test_parallel_batch_rejects_generic_additional_check_creation(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-extra-check-guard.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        _workflow, awaiting = _awaiting_skill_batch(
            repo,
            fixture,
            idempotency_key="parallel:extra-check-guard",
        )
        existing = awaiting.checks[0]

        with pytest.raises(ValueError, match="consent-bound parallel workflow"):
            CheckService(repo).create(
                str(existing["campaign_id"]),
                fixture["kp"],
                CreateCheckCommand(
                    skill_name="Listen",
                    roller_member_id=fixture["first"].member_id,
                    pc_id=fixture["first"].pc_id,
                    target=50,
                    proposal_id=str(existing["proposal_id"]),
                    player_action_id=str(existing["player_action_id"]),
                ),
            )

        linked = repo.list_skill_checks_for_action(
            str(existing["player_action_id"])
        )
        assert [check["id"] for check in linked] == [existing["id"]]


def test_parallel_batch_rejects_generic_opposed_check_without_partial_rows(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-opposed-check-guard.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        _workflow, awaiting = _awaiting_skill_batch(
            repo,
            fixture,
            idempotency_key="parallel:opposed-check-guard",
        )
        existing = awaiting.checks[0]
        before_check_count = connection.execute(
            "SELECT COUNT(*) FROM skill_checks"
        ).fetchone()[0]
        before_opposed_count = connection.execute(
            "SELECT COUNT(*) FROM opposed_checks"
        ).fetchone()[0]

        with pytest.raises(ValueError, match="consent-bound parallel workflow"):
            CheckService(repo).create_opposed(
                str(existing["campaign_id"]),
                fixture["kp"],
                CreateOpposedCheckCommand(
                    left=OpposedSideCommand(
                        skill_name="Spot Hidden",
                        target=60,
                        roller_member_id=fixture["first"].member_id,
                        pc_id=fixture["first"].pc_id,
                    ),
                    right=OpposedSideCommand(
                        skill_name="Stealth",
                        target=50,
                        roller_member_id=fixture["second"].member_id,
                        pc_id=fixture["second"].pc_id,
                    ),
                    proposal_id=str(existing["proposal_id"]),
                    player_action_id=str(existing["player_action_id"]),
                ),
            )

        assert connection.execute(
            "SELECT COUNT(*) FROM skill_checks"
        ).fetchone()[0] == before_check_count
        assert connection.execute(
            "SELECT COUNT(*) FROM opposed_checks"
        ).fetchone()[0] == before_opposed_count


def test_awaiting_parallel_batch_allows_push_and_notifies_only_after_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = parallel_contract()
    payload = contract.model_dump(mode="json")
    operator = payload["operators"][0]
    operator["skill_choices"][0]["allow_push"] = True
    operator["skill_choices"][0]["pushed_failure_stakes"] = (
        "The riskier attempt applies a lasting consequence."
    )
    operator["outcome_branches"] = [
        {
            "outcome_key": "pushed_failure",
            "commands": [
                {
                    "kind": "set_fact",
                    "path": "batch.push_cost_applied",
                    "value": True,
                }
            ],
        }
    ]
    pushable_contract = type(contract).model_validate(payload)
    monkeypatch.setattr(
        parallel_kernel_fixtures,
        "parallel_contract",
        lambda: pushable_contract,
    )
    with db_session(tmp_path / "parallel-push-guard.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        workflow, awaiting = _awaiting_skill_batch(
            repo,
            fixture,
            idempotency_key="parallel:push-guard",
        )
        root = _resolve(
            repo,
            fixture,
            str(awaiting.checks[0]["id"]),
            roll=99,
        )
        assert root["passed"] is False
        action_id = str(root["player_action_id"])
        assert connection.execute(
            """
            SELECT COUNT(*) FROM realtime_events
            WHERE event_type = 'check.consequence_ready' AND resource_id = ?
            """,
            (action_id,),
        ).fetchone()[0] == 0

        child = CheckService(repo).push(
            str(root["id"]),
            fixture["first"],
            reason="Try a materially riskier approach.",
        )
        pushed = _resolve(
            repo,
            fixture,
            str(child["id"]),
            roll=99,
        )

        assert pushed["pushed_from_check_id"] == root["id"]
        assert connection.execute(
            """
            SELECT COUNT(*) FROM realtime_events
            WHERE event_type = 'check.consequence_ready' AND resource_id = ?
            """,
            (action_id,),
        ).fetchone()[0] == 1
        ready = workflow.observe_terminal_check(str(pushed["id"]))
        assert ready is not None and ready.status == "ready"


def test_ready_parallel_batch_rejects_late_check_override(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-ready-guard.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        workflow, awaiting = _awaiting_skill_batch(
            repo,
            fixture,
            idempotency_key="parallel:ready-guard",
        )
        resolved = _resolve(
            repo,
            fixture,
            str(awaiting.checks[0]["id"]),
            roll=1,
        )
        ready = workflow.observe_terminal_check(str(resolved["id"]))
        assert ready is not None and ready.batch is not None
        assert ready.status == "ready"
        assert resolved["passed"] is True

        with pytest.raises(ValueError, match="current status: ready"):
            CheckService(repo).override(
                str(resolved["id"]),
                fixture["kp"],
                success_level="failure",
                passed=False,
                reason="This result is already bound to a ready batch.",
            )

        assert repo.get_skill_check(str(resolved["id"]))["passed"] is True
        assert repo.get_parallel_action_batch(str(ready.batch["id"]))[
            "status"
        ] == "ready"


def test_settled_parallel_batch_remains_visible_to_check_write_fence(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-settled-guard.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        workflow, awaiting = _awaiting_skill_batch(
            repo,
            fixture,
            idempotency_key="parallel:settled-guard",
        )
        resolved = _resolve(
            repo,
            fixture,
            str(awaiting.checks[0]["id"]),
            roll=1,
        )
        ready = workflow.observe_terminal_check(str(resolved["id"]))
        assert ready is not None and ready.batch is not None
        settled = workflow.commit(
            str(ready.batch["id"]),
            expected_version=int(ready.batch["version"]),
        )
        assert settled.status == "settled"
        assert resolved["passed"] is True
        assert repo.get_active_parallel_action_batch_for_action(
            str(resolved["player_action_id"])
        ) is None
        historical = repo.get_parallel_action_batch_for_action(
            str(resolved["player_action_id"])
        )
        assert historical is not None and historical["status"] == "settled"

        with pytest.raises(ValueError, match="current status: settled"):
            CheckService(repo).override(
                str(resolved["id"]),
                fixture["kp"],
                success_level="failure",
                passed=False,
                reason="A settled receipt must remain immutable.",
            )

        assert repo.get_skill_check(str(resolved["id"]))["passed"] is True

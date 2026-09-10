from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.application.parallel_action_workflow_service import (
    ParallelActionWorkflowService,
)
from ai_kp.application.turn_service import ManualProposalCommand, TurnService
from ai_kp.bootstrap.settings import Settings
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.infrastructure.database.schema import connect
from tests.test_parallel_action_workflow_service import _prepare, _workflow_fixture


@pytest.mark.parametrize("decision", ("approve", "reject"))
def test_legacy_proposal_route_cannot_decide_one_active_parallel_item(
    tmp_path: Path,
    decision: str,
) -> None:
    db_path = tmp_path / f"parallel-legacy-{decision}.sqlite3"
    with db_session(db_path) as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        prepared = _prepare(repo, fixture, f"parallel:legacy-{decision}")
        assert prepared.batch is not None
        batch_id = str(prepared.batch["id"])
        item = prepared.batch["items"][0]
        proposal_id = str(item["proposal_id"])
        action_id = str(item["action_id"])
        kp_token = str(fixture["session"]["access_token"])

    app = create_app(
        Settings(
            db_path=db_path,
            llm_base_url="http://must-not-be-called.local/v1",
            llm_model="parallel-proposal-guard",
        )
    )
    with TestClient(app) as client:
        response = client.post(
            f"/kp/proposals/{proposal_id}/{decision}",
            headers={"Authorization": f"Bearer {kp_token}"},
            json={"note": "attempted legacy single-item decision"},
        )
    assert response.status_code == 409, response.text
    assert "atomic parallel workflow" in response.json()["detail"]

    connection = connect(db_path)
    try:
        repo = Repository(connection)
        assert repo.get_parallel_action_batch(batch_id)["status"] == "awaiting_confirmation"
        assert repo.get_player_action(action_id)["status"] == "reviewed"
        assert repo.get_turn_proposal(proposal_id)["status"] == "draft"
    finally:
        connection.close()


def test_repository_legacy_approval_cannot_bypass_parallel_guard(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-repository-guard.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        prepared = _prepare(repo, fixture, "parallel:repository-guard")
        assert prepared.batch is not None
        proposal_id = str(prepared.batch["items"][0]["proposal_id"])

        with pytest.raises(ValueError, match="atomic parallel workflow"):
            repo.approve_turn_proposal(
                proposal_id,
                actor="direct-repository-caller",
            )
        with pytest.raises(ValueError, match="atomic parallel workflow"):
            repo.reject_turn_proposal(
                proposal_id,
                actor="direct-repository-caller",
            )

        assert repo.get_turn_proposal(proposal_id)["status"] == "draft"


def test_parallel_internal_approval_seam_still_settles_the_whole_batch(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-internal-seam.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        workflow = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:internal-approval-seam")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]

        first = workflow.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["first"],
        )
        assert first.batch is not None
        ready = workflow.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert ready.status == "ready" and ready.batch is not None

        settled = workflow.commit(
            str(ready.batch["id"]),
            expected_version=int(ready.batch["version"]),
        )

        assert settled.status == "settled"
        assert all(
            repo.get_player_action(str(item["action_id"]))["status"] == "resolved"
            for item in settled.batch["items"]
        )
        assert all(
            repo.get_turn_proposal(str(item["proposal_id"]))["status"] == "approved"
            for item in settled.batch["items"]
        )


def test_normal_unbatched_proposal_decisions_remain_compatible(tmp_path: Path) -> None:
    with db_session(tmp_path / "normal-proposal-decisions.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        action = fixture["actions"][0]
        # The fixture actions are not batched until `_prepare` is called.
        proposal = TurnService(repo).create_manual_proposal(
            str(fixture["run"]["campaign_id"]),
            fixture["kp"],
            ManualProposalCommand(
                player_action=str(action["action_text"]),
                player_action_id=str(action["id"]),
                public_narration="The ordinary action resolves normally.",
            ),
        )
        approved = TurnService(repo).approve(
            str(proposal["id"]),
            str(fixture["run"]["campaign_id"]),
            fixture["kp"],
        )
        assert approved["status"] == "approved"

from __future__ import annotations

from pathlib import Path

import pytest

from ai_kp.application.errors import ConflictError
from ai_kp.application.scenario_authority import (
    load_active_scenario_authority_context,
    load_scenario_authority_context,
    revalidate_scenario_authority_context,
)
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from tests.test_parallel_kernel_service import setup_bound_run


def test_ephemeral_and_initialized_contexts_make_state_policy_explicit(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "scenario-authority-policy.sqlite3") as connection:
        repo = Repository(connection)
        run = setup_bound_run(repo)

        ephemeral = load_scenario_authority_context(
            repo, run["id"], state_policy="ephemeral"
        )

        assert ephemeral.state_persisted is False
        assert ephemeral.state_version == 0
        with pytest.raises(KeyError):
            repo.get_scenario_run_state(run["id"])
        assert revalidate_scenario_authority_context(repo, ephemeral) == ephemeral

        initialized = load_scenario_authority_context(
            repo, run["id"], state_policy="initialize"
        )

        assert initialized.state_persisted is True
        assert initialized.snapshot == ephemeral.snapshot
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 0
        with pytest.raises(ConflictError, match="Scenario authority changed"):
            revalidate_scenario_authority_context(repo, ephemeral)


def test_active_loader_and_revalidation_cover_run_and_state_drift(tmp_path: Path) -> None:
    with db_session(tmp_path / "scenario-authority-drift.sqlite3") as connection:
        repo = Repository(connection)
        run = setup_bound_run(repo)
        expected = load_active_scenario_authority_context(
            repo, run["campaign_id"], state_policy="initialize"
        )
        assert expected is not None
        assert expected.run_id == run["id"]
        assert expected.contract_hash

        connection.execute(
            "UPDATE campaign_module_runs SET version = version + 1 WHERE id = ?",
            (run["id"],),
        )

        with pytest.raises(ConflictError, match="Scenario authority changed"):
            revalidate_scenario_authority_context(repo, expected)


def test_required_context_does_not_create_missing_state(tmp_path: Path) -> None:
    with db_session(tmp_path / "scenario-authority-required.sqlite3") as connection:
        repo = Repository(connection)
        run = setup_bound_run(repo)

        with pytest.raises(ConflictError, match="requires an initialized"):
            load_scenario_authority_context(repo, run["id"], state_policy="require")
        with pytest.raises(KeyError):
            repo.get_scenario_run_state(run["id"])


def test_revalidation_rejects_spoiler_scope_drift_without_a_version_increment(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "scenario-authority-spoilers.sqlite3") as connection:
        repo = Repository(connection)
        run = setup_bound_run(repo)
        expected = load_scenario_authority_context(
            repo,
            run["id"],
            state_policy="initialize",
        )

        connection.execute(
            "UPDATE campaign_module_runs SET active_spoiler_tags_json = ? WHERE id = ?",
            ('["chapter-two"]', run["id"]),
        )

        with pytest.raises(ConflictError, match="Scenario authority changed"):
            revalidate_scenario_authority_context(repo, expected)

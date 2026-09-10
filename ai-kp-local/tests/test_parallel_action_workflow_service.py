from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from ai_kp.application.action_adjudication_service import ActionAdjudicationService
from ai_kp.application.check_service import CheckService, ResolveCheckCommand
from ai_kp.application.parallel_action_planning_service import (
    ParallelActionPlanningService,
)
from ai_kp.application.parallel_action_workflow_finalizer import (
    ParallelActionWorkflowFinalizer,
)
from ai_kp.application.parallel_action_workflow_service import (
    ParallelActionWorkflowService,
)
from ai_kp.application.parallel_kernel_service import ParallelKernelService
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import TurnService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from tests.support_investigators import coc7_sheet
from tests.test_parallel_action_planning_service import (
    ForbiddenExplicitSelectionDirector,
    MechanicalParallelDirector,
)
from tests.test_parallel_kernel_service import setup_bound_run


def _workflow_fixture(
    repo: Repository,
    *,
    with_skill_character: bool,
    first_action_text: str | None = None,
) -> dict[str, Any]:
    run = setup_bound_run(repo)
    ParallelKernelService(repo).initialize_run(str(run["id"]))
    sessions = SessionService(repo)
    session = sessions.create(str(run["campaign_id"]))
    first_bundle = sessions.join(str(session["join_code"]), display_name="Ada")
    second_bundle = sessions.join(str(session["join_code"]), display_name="Bert")
    if with_skill_character:
        profile = repo.create_player_profile("Ada")
        sheet = coc7_sheet(
            "Ada Investigator",
            skills={"Spot Hidden": 60, "Art": 50},
        )
        sheet["skills"][0]["skill_key"] = "spot_hidden"
        sheet["skills"][1]["skill_key"] = "art"
        investigator = repo.create_investigator(
            str(profile["profile"]["id"]),
            sheet,
            source_type="manual",
        )
        repo.submit_investigator_to_campaign(
            campaign_id=str(run["campaign_id"]),
            investigator_id=str(investigator["id"]),
            revision_id=str(investigator["current_revision_id"]),
            owner_profile_id=str(profile["profile"]["id"]),
            member_id=str(first_bundle["member"]["id"]),
            session_id=str(session["session"]["id"]),
        )
        repo.review_campaign_investigator(
            campaign_id=str(run["campaign_id"]),
            investigator_id=str(investigator["id"]),
            action="approved",
            comment="parallel workflow fixture",
            kp_member_id=str(session["member"]["id"]),
            session_id=str(session["session"]["id"]),
        )
        repo.assign_approved_investigator(
            session_id=str(session["session"]["id"]),
            member_id=str(first_bundle["member"]["id"]),
            investigator_id=str(investigator["id"]),
        )
    kp = repo.authenticate_access_token(str(session["access_token"]))
    first = repo.authenticate_access_token(str(first_bundle["access_token"]))
    second = repo.authenticate_access_token(str(second_bundle["access_token"]))
    assert kp is not None and first is not None and second is not None
    first_text = first_action_text or (
        "inspect the hidden mark" if with_skill_character else "open the panel"
    )
    second_text = "open the panel" if with_skill_character else "observe quietly"
    actions = (
        TurnService(repo).submit_player_action(
            first,
            action_text=first_text,
            client_action_id=f"parallel-workflow-a-{int(with_skill_character)}",
        ),
        TurnService(repo).submit_player_action(
            second,
            action_text=second_text,
            client_action_id=f"parallel-workflow-b-{int(with_skill_character)}",
        ),
    )
    selections = (
        {
            "inspect the hidden mark": ("find-mark", "spot_hidden"),
            first_text: ("find-mark", "spot_hidden"),
            "open the panel": ("open-panel", None),
        }
        if with_skill_character
        else {
            "open the panel": ("open-panel", None),
            "observe quietly": ("observe-quietly", None),
        }
    )
    repo.connection.commit()
    return {
        "run": run,
        "session": session,
        "kp": kp,
        "first": first,
        "second": second,
        "actions": actions,
        "director": MechanicalParallelDirector(repo.connection, selections),
    }


def _prepare(repo: Repository, fixture: dict[str, Any], key: str):
    return asyncio.run(
        ParallelActionWorkflowService(repo).prepare(
            fixture["actions"],
            fixture["kp"],
            fixture["director"],
            idempotency_key=key,
            source_model="parallel-workflow-test",
        )
    )


def _pause_parallel_batch(
    repo: Repository,
    batch: dict[str, Any],
    fixture: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    return repo.transition_parallel_action_batch(
        str(batch["id"]),
        "request_attention",
        expected_version=int(batch["version"]),
        actor_member_id=fixture["kp"].member_id,
        reason=reason,
    )


def test_direct_workflow_is_idempotent_owner_confirmed_and_atomically_settled(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-direct-workflow.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        service = ParallelActionWorkflowService(repo)

        prepared = _prepare(repo, fixture, "parallel:workflow-direct")

        assert prepared.status == "awaiting_confirmation"
        assert prepared.batch is not None
        assert len({item["proposal_id"] for item in prepared.batch["items"]}) == 2
        assert len({item["adjudication_id"] for item in prepared.batch["items"]}) == 2
        assert all(
            repo.get_player_action(str(action["id"]))["status"] == "reviewed"
            for action in fixture["actions"]
        )
        connection.commit()

        model_calls = len(fixture["director"].transaction_states)
        replayed = _prepare(repo, fixture, "parallel:workflow-direct")
        assert replayed.batch is not None
        assert replayed.batch["id"] == prepared.batch["id"]
        assert len(fixture["director"].transaction_states) == model_calls
        with pytest.raises(ValueError, match="different parallel action set"):
            asyncio.run(
                service.prepare(
                    fixture["actions"][:1],
                    fixture["kp"],
                    fixture["director"],
                    idempotency_key="parallel:workflow-direct",
                    source_model="parallel-workflow-test",
                )
            )
        with pytest.raises(ValueError, match="different parallel action set"):
            asyncio.run(
                service.prepare(
                    tuple(reversed(fixture["actions"])),
                    fixture["kp"],
                    fixture["director"],
                    idempotency_key="parallel:workflow-direct",
                    source_model="parallel-workflow-test",
                )
            )

        first_item, second_item = prepared.batch["items"]
        with pytest.raises(PermissionError, match="only their own"):
            service.confirm_item(
                str(prepared.batch["id"]),
                str(first_item["action_id"]),
                expected_batch_version=1,
                expected_adjudication_version=int(
                    first_item["adjudication"]["version"]
                ),
                selected_skill_key=None,
                identity=fixture["second"],
            )
        first_confirmed = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=1,
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["first"],
        )
        assert first_confirmed.status == "awaiting_confirmation"
        first_replay = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=1,
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["first"],
        )
        assert first_replay.status == "awaiting_confirmation"
        assert first_replay.batch is not None
        assert first_replay.batch["version"] == first_confirmed.batch["version"]
        ready = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first_confirmed.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert ready.status == "ready"
        assert ready.batch is not None
        assert all(item["outcome_key"] == "success" for item in ready.batch["items"])
        assert all(
            repo.get_turn_proposal(str(item["proposal_id"]))["status"] == "draft"
            for item in ready.batch["items"]
        )
        last_replay = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first_confirmed.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert last_replay.status == "ready"
        assert last_replay.batch is not None
        assert last_replay.batch["version"] == ready.batch["version"]

        settled = service.commit(
            str(ready.batch["id"]),
            expected_version=int(ready.batch["version"]),
        )

        assert settled.status == "settled"
        assert settled.batch is not None
        assert repo.get_scenario_run_state(str(fixture["run"]["id"]))[
            "state_version"
        ] == 1
        assert all(action["status"] == "resolved" for action in settled.actions)
        assert len(repo.list_scenario_command_batches(str(fixture["run"]["id"]))) == 1
        settled_replay = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first_confirmed.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert settled_replay.status == "settled"


def test_four_player_explicit_mixed_operators_persist_canonical_skill_authority(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-explicit-four-player.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        sessions = SessionService(repo)
        third_bundle = sessions.join(
            str(fixture["session"]["join_code"]), display_name="Cora"
        )
        fourth_bundle = sessions.join(
            str(fixture["session"]["join_code"]), display_name="Dorian"
        )
        third = repo.authenticate_access_token(str(third_bundle["access_token"]))
        fourth = repo.authenticate_access_token(str(fourth_bundle["access_token"]))
        assert third is not None and fourth is not None

        explicit_texts = (
            '选择已发布行动“Find a hidden mark”',
            '选择已发布行动“Open the panel”',
        )
        for action, action_text in zip(
            fixture["actions"], explicit_texts, strict=True
        ):
            connection.execute(
                "UPDATE player_actions SET action_text = ? WHERE id = ?",
                (action_text, action["id"]),
            )
        third_action = TurnService(repo).submit_player_action(
            third,
            action_text=(
                '选择已发布行动“Observe without changing the world”'
            ),
            client_action_id="parallel-explicit-c",
        )
        fourth_action = TurnService(repo).submit_player_action(
            fourth,
            action_text='选择已发布行动“Spend a shared token”',
            client_action_id="parallel-explicit-d",
        )
        connection.commit()
        actions = tuple(
            repo.get_player_action(str(action["id"]))
            for action in (*fixture["actions"], third_action, fourth_action)
        )

        prepared = asyncio.run(
            ParallelActionWorkflowService(repo).prepare(
                actions,
                fixture["kp"],
                ForbiddenExplicitSelectionDirector(),
                idempotency_key="parallel:explicit-four-player",
                source_model="weak-model",
            )
        )

        assert prepared.status == "awaiting_confirmation"
        assert prepared.batch is not None
        assert [item["operator_id"] for item in prepared.batch["items"]] == [
            "find-mark",
            "open-panel",
            "observe-quietly",
            "spend-token",
        ]
        assert [item["selected_skill_key"] for item in prepared.batch["items"]] == [
            "spot_hidden",
            None,
            None,
            None,
        ]
        first_adjudication = prepared.batch["items"][0]["adjudication"]
        assert first_adjudication["mode"] == "skill_check"
        assert {item["skill_key"] for item in first_adjudication["skill_options"]} == {
            "spot_hidden",
            "art",
        }
        assert repo.get_scenario_run_state(str(fixture["run"]["id"]))[
            "state_version"
        ] == 0
        assert all(
            repo.get_player_action(str(action["id"]))["status"] == "reviewed"
            for action in actions
        )


def test_prepare_rechecks_idempotency_under_lock_before_persisting(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-prepare-race.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        real_planner = ParallelActionPlanningService(repo)

        class StaticPlanner:
            def __init__(self, planning):
                self.planning = planning

            async def plan(self, *args, **kwargs):
                return self.planning

        class RacingPlanner:
            async def plan(self, actions, director, **kwargs):
                planning = await real_planner.plan(actions, director, **kwargs)
                competing = ParallelActionWorkflowService(
                    repo,
                    planner=StaticPlanner(planning),
                )
                won = await competing.prepare(
                    actions,
                    fixture["kp"],
                    director,
                    idempotency_key="parallel:prepare-race",
                    source_model="parallel-workflow-test",
                )
                assert won.batch is not None
                return planning

        replay = asyncio.run(
            ParallelActionWorkflowService(repo, planner=RacingPlanner()).prepare(
                fixture["actions"],
                fixture["kp"],
                fixture["director"],
                idempotency_key="parallel:prepare-race",
                source_model="parallel-workflow-test",
            )
        )

        assert replay.status == "awaiting_confirmation"
        assert replay.batch is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM parallel_action_batches"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM turn_proposals"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM player_action_adjudications"
        ).fetchone()[0] == 2


def test_confirm_rechecks_same_player_consent_under_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with db_session(tmp_path / "parallel-confirm-race.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:confirm-race")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        original_begin = repo.begin_parallel_action_workflow

        def race_first_confirmation() -> None:
            monkeypatch.setattr(
                repo, "begin_parallel_action_workflow", original_begin
            )
            service.confirm_item(
                str(prepared.batch["id"]),
                str(first_item["action_id"]),
                expected_batch_version=int(prepared.batch["version"]),
                expected_adjudication_version=int(
                    first_item["adjudication"]["version"]
                ),
                selected_skill_key=None,
                identity=fixture["first"],
            )
            original_begin()

        monkeypatch.setattr(
            repo, "begin_parallel_action_workflow", race_first_confirmation
        )
        first_replay = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["first"],
        )
        assert first_replay.status == "awaiting_confirmation"

        first_basis = first_replay.batch
        assert first_basis is not None

        def race_last_confirmation() -> None:
            monkeypatch.setattr(
                repo, "begin_parallel_action_workflow", original_begin
            )
            service.confirm_item(
                str(prepared.batch["id"]),
                str(second_item["action_id"]),
                expected_batch_version=int(first_basis["version"]),
                expected_adjudication_version=int(
                    second_item["adjudication"]["version"]
                ),
                selected_skill_key=None,
                identity=fixture["second"],
            )
            original_begin()

        monkeypatch.setattr(
            repo, "begin_parallel_action_workflow", race_last_confirmation
        )
        ready_replay = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first_basis["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )

        assert ready_replay.status == "ready"
        assert ready_replay.batch is not None
        assert len(ready_replay.batch["events"]) == 4


def test_commit_rechecks_settled_receipt_under_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with db_session(tmp_path / "parallel-commit-race.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:commit-race")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        first = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["first"],
        )
        ready = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert ready.batch is not None
        original_begin = repo.begin_parallel_action_workflow

        def settle_before_lock() -> None:
            monkeypatch.setattr(
                repo, "begin_parallel_action_workflow", original_begin
            )
            winner = service.commit(
                str(ready.batch["id"]),
                expected_version=int(ready.batch["version"]),
            )
            assert winner.status == "settled"
            original_begin()

        monkeypatch.setattr(
            repo, "begin_parallel_action_workflow", settle_before_lock
        )
        replay = service.commit(
            str(ready.batch["id"]),
            expected_version=int(ready.batch["version"]),
        )

        assert replay.status == "settled"
        assert replay.batch is not None
        assert len(repo.list_scenario_command_batches(str(fixture["run"]["id"]))) == 1


def test_check_observer_replays_settlement_that_wins_the_write_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with db_session(tmp_path / "parallel-observer-race.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:observer-race")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        first = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key="spot_hidden",
            identity=fixture["first"],
        )
        awaiting = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        resolved = CheckService(repo).resolve(
            str(awaiting.checks[0]["id"]),
            fixture["first"],
            ResolveCheckCommand(
                input_method="physical",
                ones_digit=1,
                tens_digits=(0,),
            ),
        )
        ready = service.observe_terminal_check(str(resolved["id"]))
        assert ready is not None and ready.batch is not None
        assert ready.status == "ready"
        original_begin = repo.begin_parallel_action_workflow

        def settle_before_observer_lock() -> None:
            monkeypatch.setattr(
                repo, "begin_parallel_action_workflow", original_begin
            )
            winner = service.commit(
                str(ready.batch["id"]),
                expected_version=int(ready.batch["version"]),
            )
            assert winner.status == "settled"
            original_begin()

        monkeypatch.setattr(
            repo, "begin_parallel_action_workflow", settle_before_observer_lock
        )
        replay = service.observe_terminal_check(str(resolved["id"]))

        assert replay is not None and replay.status == "settled"
        assert replay.batch is not None
        assert len(repo.list_scenario_command_batches(str(fixture["run"]["id"]))) == 1


def test_skill_rebind_check_rendezvous_and_consequence_finish_one_batch(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-skill-workflow.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:workflow-skill")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        old_hash = str(first_item["preview_hash"])
        # Confirmation is a later request and re-previews before taking a write lock.
        connection.commit()

        first_confirmed = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key="art",
            identity=fixture["first"],
        )
        assert first_confirmed.batch is not None
        rebound = first_confirmed.batch["items"][0]
        assert rebound["selected_skill_key"] == "art"
        assert rebound["preview_hash"] != old_hash
        assert rebound["preview"]["selected_skill_key"] == "art"
        assert rebound["adjudication"]["selected_skill"] == "art"

        awaiting = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first_confirmed.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert awaiting.status == "awaiting_checks"
        assert awaiting.batch is not None
        assert len(awaiting.checks) == 1
        assert awaiting.checks[0]["skill_name"] == "art"
        assert repo.get_turn_proposal(str(first_item["proposal_id"]))[
            "status"
        ] == "approved"
        assert repo.get_turn_proposal(str(second_item["proposal_id"]))[
            "status"
        ] == "draft"

        resolved = CheckService(repo).resolve(
            str(awaiting.checks[0]["id"]),
            fixture["first"],
            ResolveCheckCommand(
                input_method="physical",
                ones_digit=1,
                tens_digits=(0,),
            ),
        )
        assert resolved["passed"] is True
        ready = service.observe_terminal_check(str(resolved["id"]))
        assert ready is not None and ready.status == "ready"
        assert ready.batch is not None
        assert ready.batch["items"][0]["outcome_key"] == "success"

        settled = service.commit(
            str(ready.batch["id"]),
            expected_version=int(ready.batch["version"]),
        )

        assert settled.status == "settled"
        assert settled.batch is not None
        state = repo.get_scenario_run_state(str(fixture["run"]["id"]))
        assert state["snapshot"].status == "completed"
        assert all(action["status"] == "resolved" for action in settled.actions)
        consequence_rows = connection.execute(
            """
            SELECT COUNT(*)
            FROM proposal_actions
            WHERE action_type = 'check_consequence_basis'
            """
        ).fetchone()
        assert consequence_rows[0] == 1
        assert (
            repo.get_scenario_run_state(str(fixture["run"]["id"]))[
                "snapshot"
            ].facts["batch"]["mark_found"]
            is True
        )
        assert (
            state["snapshot"].facts["batch"]["panel_open"] is True
        )


def test_parallel_kernel_adjudication_does_not_apply_legacy_keyword_precheck(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-no-legacy-precheck.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(
            repo,
            with_skill_character=True,
            first_action_text="I use Art to inspect the hidden mark.",
        )

        prepared = _prepare(repo, fixture, "parallel:no-legacy-precheck")

        assert prepared.status == "awaiting_confirmation"
        assert prepared.batch is not None
        item = prepared.batch["items"][0]
        assert item["preview"]["selected_skill_key"] == "spot_hidden"
        assert item["selected_skill_key"] == "spot_hidden"
        selected = item["adjudication"]["selected_skill"]
        selected_option = next(
            option
            for option in item["adjudication"]["skill_options"]
            if option["skill_name"] == selected
        )
        assert selected_option["skill_key"] == "spot_hidden"


def test_failed_parallel_check_executes_and_narrates_exact_accepted_stakes(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-failure-stakes.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:failure-stakes")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        first = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key="spot_hidden",
            identity=fixture["first"],
        )
        awaiting = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert awaiting.batch is not None and len(awaiting.checks) == 1
        failed = CheckService(repo).resolve(
            str(awaiting.checks[0]["id"]),
            fixture["first"],
            ResolveCheckCommand(
                input_method="physical",
                ones_digit=9,
                tens_digits=(9,),
            ),
        )
        assert failed["passed"] is False
        ready = service.observe_terminal_check(str(failed["id"]))
        assert ready is not None and ready.batch is not None
        settled = service.commit(
            str(ready.batch["id"]),
            expected_version=int(ready.batch["version"]),
        )

        assert settled.status == "settled"
        consequence = connection.execute(
            """
            SELECT public_narration FROM turn_proposals
            WHERE source_model = 'kernel:parallel-consequence'
            """
        ).fetchone()
        assert consequence is not None
        assert "The mark remains hidden for this attempt." in consequence[0]
        assert "既定代价已执行" in consequence[0]
        state = repo.get_scenario_run_state(str(fixture["run"]["id"]))
        assert state["snapshot"].facts["batch"]["mark_missed"] is True


def test_pushed_failure_narration_preserves_approach_and_exact_stakes() -> None:
    narration = ParallelActionWorkflowFinalizer._public_check_narration(
        {
            "has_hidden_checks": False,
            "effective_results": [
                {
                    "outcome": "pushed_failure",
                    "push_approach": "I force the archive door after closing.",
                    "accepted_stakes": "Security expels the investigator.",
                }
            ],
        },
        outcome="pushed_failure",
        narration="The attempt fails.",
    )

    assert "I force the archive door after closing." in narration
    assert "Security expels the investigator." in narration
    assert "推动失败的既定代价已执行" in narration


def test_hidden_success_delivers_contract_public_clue_without_roll_metadata() -> None:
    narration = ParallelActionWorkflowFinalizer._public_check_narration(
        {
            "has_hidden_checks": True,
            "effective_results": [
                {
                    "outcome": "success",
                    "hidden": True,
                    "skill_name": "Spot Hidden",
                    "selected_roll": 4,
                    "threshold": 60,
                    "success_level": "extreme",
                    "automatic_information": [
                        "The seal bears the city archive's public crest."
                    ],
                    "push_approach": "",
                    "accepted_stakes": "",
                }
            ],
        },
        outcome="hard",
        narration="Behind the frame, you find the archivist's signed confession.",
    )

    assert narration.startswith("Behind the frame")
    assert "signed confession" in narration
    assert "The seal bears the city archive's public crest." in narration
    for secret in ("Spot Hidden", "extreme", "04", "60", "hard"):
        assert secret not in narration


def test_hidden_failure_narrates_visible_stakes_without_roll_metadata() -> None:
    narration = ParallelActionWorkflowFinalizer._public_check_narration(
        {
            "has_hidden_checks": True,
            "effective_results": [
                {
                    "outcome": "failure",
                    "hidden": True,
                    "skill_name": "Psychology",
                    "selected_roll": 96,
                    "threshold": 40,
                    "success_level": "fumble",
                    "automatic_information": [],
                    "push_approach": "",
                    "accepted_stakes": "The witness ends the interview.",
                }
            ],
        },
        outcome="failure",
        narration=None,
    )

    assert narration.startswith("局势仍在发展")
    assert "The witness ends the interview." in narration
    assert "既定代价已执行" in narration
    for secret in ("Psychology", "96", "40", "fumble", "outcome: failure"):
        assert secret not in narration


@pytest.mark.parametrize("tamper", ["commands", "hash"])
def test_skill_rebind_rejects_tampered_preview_without_partial_writes(
    tmp_path: Path,
    tamper: str,
) -> None:
    with db_session(tmp_path / f"parallel-rebind-{tamper}.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        prepared = _prepare(repo, fixture, f"parallel:rebind-{tamper}")
        assert prepared.batch is not None
        item = prepared.batch["items"][0]
        connection.commit()
        replacement = ParallelActionPlanningService(repo).repreview_persisted_skill(
            str(prepared.batch["id"]),
            str(item["action_id"]),
            requested_skill_key="art",
            actor_member_id=fixture["first"].member_id,
        )
        preview = replacement.preview.model_dump(mode="json")
        if tamper == "commands":
            preview["success_commands"] = []
        else:
            preview["preview_hash"] = "0" * 64

        with pytest.raises(ValueError, match="outside the durable batch authority"):
            repo.rebind_parallel_action_item_skill(
                str(prepared.batch["id"]),
                str(item["action_id"]),
                expected_batch_version=int(prepared.batch["version"]),
                expected_adjudication_version=int(item["adjudication"]["version"]),
                selected_skill_key="art",
                preview=preview,
                narrative=replacement.narrative.model_dump(mode="json"),
                actor_member_id=fixture["first"].member_id,
            )

        current = repo.get_parallel_action_batch(str(prepared.batch["id"]))
        assert current["version"] == prepared.batch["version"]
        assert current["items"][0]["preview_hash"] == item["preview_hash"]
        assert current["items"][0]["selected_skill_key"] == "spot_hidden"


def test_player_revision_supersedes_batch_with_owned_audit_identity(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-revision-audit.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        prepared = _prepare(repo, fixture, "parallel:revision-audit")
        assert prepared.batch is not None
        first_item = prepared.batch["items"][0]

        paused = ParallelActionWorkflowService(repo).request_revision(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_version=int(prepared.batch["version"]),
            identity=fixture["first"],
            reason="I want to change my approach.",
        )

        assert paused.status == "needs_attention"
        assert paused.batch is not None
        assert paused.batch["status"] == "superseded"
        assert paused.batch["events"][-1]["event_type"] == "supersede"
        assert paused.batch["events"][-1]["actor_member_id"] == fixture[
            "first"
        ].member_id
        assert all(
            item["adjudication"]["status"] == "superseded"
            for item in paused.batch["items"]
        )
        assert all(
            repo.get_turn_proposal(str(item["proposal_id"]))["status"]
            == "rejected"
            for item in paused.batch["items"]
        )
        assert all(
            repo.get_player_action(str(action["id"]))["status"] == "rejected"
            for action in fixture["actions"]
        )
        with pytest.raises(ValueError, match="Cannot resume_confirmations"):
            repo.transition_parallel_action_batch(
                str(paused.batch["id"]),
                "resume_confirmations",
                expected_version=int(paused.batch["version"]),
                actor_member_id=fixture["kp"].member_id,
            )
        replay = ParallelActionWorkflowService(repo).request_revision(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_version=int(prepared.batch["version"]),
            identity=fixture["first"],
            reason="I want to change my approach.",
        )
        assert replay.batch is not None
        assert replay.batch["version"] == paused.batch["version"]


def test_player_can_withdraw_consent_when_no_active_kp_remains(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-revision-without-kp.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        prepared = _prepare(repo, fixture, "parallel:revision-without-kp")
        assert prepared.batch is not None
        first_item = prepared.batch["items"][0]
        connection.execute(
            "UPDATE session_members SET revoked_at = CURRENT_TIMESTAMP WHERE id = ?",
            (fixture["kp"].member_id,),
        )

        withdrawn = ParallelActionWorkflowService(repo).request_revision(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_version=int(prepared.batch["version"]),
            identity=fixture["first"],
            reason="I withdraw consent.",
        )

        assert withdrawn.batch is not None
        assert withdrawn.batch["status"] == "superseded"
        assert all(action["status"] == "rejected" for action in withdrawn.actions)


def test_revision_after_confirmation_is_rejected_to_prevent_outcome_fishing(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-check-revision.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:check-revision")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        first = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key="spot_hidden",
            identity=fixture["first"],
        )
        awaiting = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert awaiting.batch is not None
        check_id = str(awaiting.checks[0]["id"])

        with pytest.raises(ValueError, match="only before every player confirms"):
            service.request_revision(
                str(awaiting.batch["id"]),
                str(first_item["action_id"]),
                expected_version=int(awaiting.batch["version"]),
                identity=fixture["first"],
                reason="I withdraw this approach before rolling.",
            )
        assert repo.get_skill_check(check_id)["status"] == "requested"

        failed = CheckService(repo).resolve(
            check_id,
            fixture["first"],
            ResolveCheckCommand(
                input_method="physical",
                ones_digit=9,
                tens_digits=(9,),
            ),
        )
        ready = service.observe_terminal_check(str(failed["id"]))
        assert ready is not None and ready.batch is not None
        with pytest.raises(ValueError, match="only before every player confirms"):
            service.request_revision(
                str(ready.batch["id"]),
                str(first_item["action_id"]),
                expected_version=int(ready.batch["version"]),
                identity=fixture["first"],
                reason="I dislike the failed result.",
            )
        current = repo.get_parallel_action_batch(str(ready.batch["id"]))
        assert current["status"] == "ready"
        assert next(
            item
            for item in current["items"]
            if item["action_id"] == first_item["action_id"]
        )["outcome_key"] == "failure"


def test_run_authority_drift_before_confirmation_pauses_without_player_consent(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-confirm-authority-drift.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:confirm-authority-drift")
        assert prepared.batch is not None
        item = prepared.batch["items"][0]
        connection.execute(
            "UPDATE campaign_module_runs SET version = version + 1 WHERE id = ?",
            (fixture["run"]["id"],),
        )

        paused = service.confirm_item(
            str(prepared.batch["id"]),
            str(item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["first"],
        )

        assert paused.status == "needs_attention"
        assert paused.batch is not None
        assert paused.batch["status"] == "needs_attention"
        assert repo.get_action_adjudication(str(item["action_id"]))[
            "status"
        ] == "pending"


def test_run_authority_drift_before_observe_does_not_bind_check_result(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-observe-authority-drift.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:observe-authority-drift")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        first = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key="spot_hidden",
            identity=fixture["first"],
        )
        awaiting = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert awaiting.batch is not None
        resolved = CheckService(repo).resolve(
            str(awaiting.checks[0]["id"]),
            fixture["first"],
            ResolveCheckCommand(
                input_method="physical",
                ones_digit=1,
                tens_digits=(0,),
            ),
        )
        connection.execute(
            "UPDATE campaign_module_runs SET version = version + 1 WHERE id = ?",
            (fixture["run"]["id"],),
        )

        paused = service.observe_terminal_check(str(resolved["id"]))

        assert paused is not None and paused.status == "needs_attention"
        assert paused.batch is not None
        checked_item = next(
            item
            for item in paused.batch["items"]
            if item["adjudication"]["mode"] == "skill_check"
        )
        assert checked_item["outcome_key"] is None
        assert checked_item["check_result_fingerprint"] is None


def test_run_authority_drift_before_commit_rolls_back_without_world_effects(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-commit-authority-drift.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:commit-authority-drift")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        first = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["first"],
        )
        ready = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert ready.batch is not None
        connection.execute(
            "UPDATE campaign_module_runs SET version = version + 1 WHERE id = ?",
            (fixture["run"]["id"],),
        )

        paused = service.commit(
            str(ready.batch["id"]),
            expected_version=int(ready.batch["version"]),
        )

        assert paused.status == "needs_attention"
        assert repo.get_scenario_run_state(str(fixture["run"]["id"]))[
            "state_version"
        ] == 0
        assert repo.list_scenario_command_batches(str(fixture["run"]["id"])) == []


def test_stale_commit_version_refreshes_without_poisoning_ready_batch(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-stale-commit.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:stale-commit")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        first = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["first"],
        )
        ready = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert ready.batch is not None

        refreshed = service.commit(
            str(ready.batch["id"]),
            expected_version=int(ready.batch["version"]) - 1,
        )

        assert refreshed.status == "ready"
        assert refreshed.batch is not None
        assert refreshed.batch["status"] == "ready"
        assert refreshed.batch["attention_reason"] == ""
        assert repo.list_scenario_command_batches(str(fixture["run"]["id"])) == []


def test_ready_batch_detects_a_late_check_override_and_fails_closed(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-ready-check-drift.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:ready-check-drift")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        first = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key="spot_hidden",
            identity=fixture["first"],
        )
        awaiting = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        resolved = CheckService(repo).resolve(
            str(awaiting.checks[0]["id"]),
            fixture["first"],
            ResolveCheckCommand(
                input_method="physical",
                ones_digit=1,
                tens_digits=(0,),
            ),
        )
        ready = service.observe_terminal_check(str(resolved["id"]))
        assert ready is not None and ready.status == "ready"
        with pytest.raises(ValueError, match="current status: ready"):
            CheckService(repo).override(
                str(resolved["id"]),
                fixture["kp"],
                success_level="failure",
                passed=False,
                reason="Late KP correction for the audit test.",
            )

        unchanged_check = repo.get_skill_check(str(resolved["id"]))
        unchanged_batch = repo.get_parallel_action_batch(str(ready.batch["id"]))
        assert unchanged_check["passed"] is True
        assert unchanged_batch["status"] == "ready"
        assert unchanged_batch["items"][0]["check_result_fingerprint"] == (
            ready.batch["items"][0]["check_result_fingerprint"]
        )


def test_prepare_failure_rolls_back_every_independent_proposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with db_session(tmp_path / "parallel-prepare-rollback.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        original = ActionAdjudicationService.create
        calls = 0

        def fail_second_adjudication(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected second-item persistence failure")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(
            ActionAdjudicationService,
            "create",
            fail_second_adjudication,
        )

        with pytest.raises(RuntimeError, match="second-item persistence failure"):
            _prepare(repo, fixture, "parallel:prepare-rollback")

        assert connection.execute("SELECT COUNT(*) FROM turn_proposals").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM player_action_adjudications"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM parallel_action_batches"
        ).fetchone()[0] == 0
        assert all(
            repo.get_player_action(str(action["id"]))["status"] == "submitted"
            for action in fixture["actions"]
        )


def test_commit_failure_rolls_back_kernel_and_all_proposal_finalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with db_session(tmp_path / "parallel-commit-rollback.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:commit-rollback")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        first = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["first"],
        )
        ready = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert ready.batch is not None
        original = TurnService.approve_parallel_proposal
        calls = 0

        def fail_second_approval(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValueError("injected second-item approval failure")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(
            TurnService,
            "approve_parallel_proposal",
            fail_second_approval,
        )

        result = service.commit(
            str(ready.batch["id"]),
            expected_version=int(ready.batch["version"]),
        )

        assert result.status == "needs_attention"
        assert result.batch is not None and result.batch["status"] == "needs_attention"
        assert "rolled back" in result.batch["attention_reason"]
        assert repo.get_scenario_run_state(str(fixture["run"]["id"]))[
            "state_version"
        ] == 0
        assert repo.list_scenario_command_batches(str(fixture["run"]["id"])) == []
        assert all(
            repo.get_turn_proposal(str(item["proposal_id"]))["status"] == "draft"
            for item in ready.batch["items"]
        )
        assert all(
            repo.get_player_action(str(action["id"]))["status"] == "reviewed"
            for action in fixture["actions"]
        )


def test_kp_recovers_direct_attention_to_exact_ready_barrier_and_retries(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-recover-direct.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:recover-direct")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        first = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["first"],
        )
        assert first.batch is not None
        ready = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert ready.batch is not None
        paused = _pause_parallel_batch(
            repo,
            ready.batch,
            fixture,
            reason="Injected recoverable coordinator interruption.",
        )

        recovered = service.resume_needs_attention(
            str(paused["id"]),
            expected_version=int(paused["version"]),
            identity=fixture["kp"],
        )

        assert recovered.status == "ready"
        assert recovered.batch is not None
        assert recovered.batch["attention_reason"] == ""
        assert recovered.batch["events"][-1]["event_type"] == "resume_ready"
        assert recovered.batch["events"][-1]["actor_member_id"] == fixture[
            "kp"
        ].member_id
        replay = service.resume_needs_attention(
            str(paused["id"]),
            expected_version=int(paused["version"]),
            identity=fixture["kp"],
        )
        assert replay.status == "ready"
        assert replay.batch is not None
        assert replay.batch["version"] == recovered.batch["version"]

        settled = service.commit(
            str(recovered.batch["id"]),
            expected_version=int(recovered.batch["version"]),
        )
        assert settled.batch is not None
        settled_replay = service.resume_needs_attention(
            str(settled.batch["id"]),
            expected_version=int(recovered.batch["version"]),
            identity=fixture["kp"],
        )
        assert settled_replay.status == "settled"
        with pytest.raises(ValueError, match="settled.*cannot be abandoned"):
            service.abandon_needs_attention(
                str(settled.batch["id"]),
                expected_version=int(settled.batch["version"]),
                identity=fixture["kp"],
            )


def test_kp_recovers_pending_confirmation_barrier_and_revoked_kp_is_denied(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-recover-confirmation.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:recover-confirmation")
        assert prepared.batch is not None
        paused = _pause_parallel_batch(
            repo,
            prepared.batch,
            fixture,
            reason="Confirmation coordinator restarted.",
        )

        recovered = service.resume_needs_attention(
            str(paused["id"]),
            expected_version=int(paused["version"]),
            identity=fixture["kp"],
        )

        assert recovered.status == "awaiting_confirmation"
        assert recovered.batch is not None
        assert recovered.batch["events"][-1]["event_type"] == (
            "resume_confirmations"
        )
        assert all(
            item["adjudication"]["status"] == "pending"
            for item in recovered.batch["items"]
        )
        paused_again = _pause_parallel_batch(
            repo,
            recovered.batch,
            fixture,
            reason="KP membership is about to change.",
        )
        connection.execute(
            "UPDATE session_members SET revoked_at = CURRENT_TIMESTAMP WHERE id = ?",
            (fixture["kp"].member_id,),
        )
        with pytest.raises(PermissionError, match="active KP"):
            service.resume_needs_attention(
                str(paused_again["id"]),
                expected_version=int(paused_again["version"]),
                identity=fixture["kp"],
            )


def test_kp_recovers_confirmed_requested_checks_without_changing_them(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-recover-checks.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:recover-checks")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        first = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key="spot_hidden",
            identity=fixture["first"],
        )
        assert first.batch is not None
        awaiting = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert awaiting.batch is not None
        check_before = repo.get_skill_check(str(awaiting.checks[0]["id"]))
        paused = _pause_parallel_batch(
            repo,
            awaiting.batch,
            fixture,
            reason="Check worker was interrupted before the roll.",
        )

        recovered = service.resume_needs_attention(
            str(paused["id"]),
            expected_version=int(paused["version"]),
            identity=fixture["kp"],
        )

        assert recovered.status == "awaiting_checks"
        assert recovered.batch is not None
        assert recovered.batch["events"][-1]["event_type"] == "resume_checks"
        check_after = repo.get_skill_check(str(check_before["id"]))
        assert check_after == check_before

        resolved = CheckService(repo).resolve(
            str(check_after["id"]),
            fixture["first"],
            ResolveCheckCommand(
                input_method="physical",
                ones_digit=1,
                tens_digits=(0,),
            ),
        )
        paused_unbound = _pause_parallel_batch(
            repo,
            recovered.batch,
            fixture,
            reason="The roll is terminal but its exact batch binding was interrupted.",
        )
        resumed_unbound = service.resume_needs_attention(
            str(paused_unbound["id"]),
            expected_version=int(paused_unbound["version"]),
            identity=fixture["kp"],
        )
        assert resumed_unbound.status == "awaiting_checks"
        unchanged_resolved = repo.get_skill_check(str(resolved["id"]))
        assert unchanged_resolved["status"] == "resolved"
        assert unchanged_resolved["selected_roll"] == resolved["selected_roll"]
        ready = service.observe_terminal_check(str(resolved["id"]))
        assert ready is not None and ready.status == "ready"


def test_kp_recovers_pending_push_but_cannot_abandon_known_results(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-recover-push.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:recover-push")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        first = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key="spot_hidden",
            identity=fixture["first"],
        )
        assert first.batch is not None
        awaiting = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert awaiting.batch is not None
        root_check_id = str(awaiting.checks[0]["id"])
        connection.execute(
            "UPDATE skill_checks SET allow_push = 1 WHERE id = ?",
            (root_check_id,),
        )
        failed = CheckService(repo).resolve(
            root_check_id,
            fixture["first"],
            ResolveCheckCommand(
                input_method="physical",
                ones_digit=9,
                tens_digits=(9,),
            ),
        )
        waiting = service.observe_terminal_check(str(failed["id"]))
        assert waiting is not None and waiting.status == "awaiting_checks"
        assert waiting.batch is not None
        paused = _pause_parallel_batch(
            repo,
            waiting.batch,
            fixture,
            reason="Pending push decision requires coordinator recovery.",
        )
        resumed = service.resume_needs_attention(
            str(paused["id"]),
            expected_version=int(paused["version"]),
            identity=fixture["kp"],
        )
        assert resumed.status == "awaiting_checks"
        root_before_push = repo.get_skill_check(root_check_id)
        assert root_before_push["status"] == "resolved"
        assert root_before_push["push_decision"] is None

        pushed = CheckService(repo).push(
            root_check_id,
            fixture["first"],
            reason="Try the risky approach again.",
        )
        pushed_failure = CheckService(repo).resolve(
            str(pushed["id"]),
            fixture["first"],
            ResolveCheckCommand(
                input_method="physical",
                ones_digit=9,
                tens_digits=(9,),
            ),
        )
        ready = service.observe_terminal_check(str(pushed_failure["id"]))
        assert ready is not None and ready.status == "ready"
        assert ready.batch is not None
        paused_ready = _pause_parallel_batch(
            repo,
            ready.batch,
            fixture,
            reason="KP chooses an audited replan after the resolved push.",
        )
        recovered_ready = service.resume_needs_attention(
            str(paused_ready["id"]),
            expected_version=int(paused_ready["version"]),
            identity=fixture["kp"],
        )
        assert recovered_ready.status == "ready"
        assert recovered_ready.batch is not None
        paused_ready = _pause_parallel_batch(
            repo,
            recovered_ready.batch,
            fixture,
            reason="KP confirms this resolved batch should instead be replanned.",
        )
        dice_before = {
            check_id: repo.get_skill_check(check_id)
            for check_id in (root_check_id, str(pushed_failure["id"]))
        }
        decision = repo.get_parallel_action_abandonment_decision(
            str(paused_ready["id"])
        )
        assert decision["abandon_allowed"] is False
        assert "known result" in decision["abandon_block_reason"]
        with pytest.raises(ValueError, match="known result"):
            service.abandon_needs_attention(
                str(paused_ready["id"]),
                expected_version=int(paused_ready["version"]),
                identity=fixture["kp"],
                reason="Attempt to discard a known pushed-roll result.",
            )

        unchanged_batch = repo.get_parallel_action_batch(str(paused_ready["id"]))
        assert unchanged_batch["status"] == "needs_attention"
        assert unchanged_batch["version"] == paused_ready["version"]
        for check_id, before in dice_before.items():
            after = repo.get_skill_check(check_id)
            assert after["status"] == before["status"] == "resolved"
            assert after["selected_roll"] == before["selected_roll"]
            assert after["raw_dice"] == before["raw_dice"]


def test_abandonment_authority_allows_cancelled_but_blocks_overridden_checks(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-abandon-check-states.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=True)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:abandon-cancelled")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        first = service.confirm_item(
            str(prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key="spot_hidden",
            identity=fixture["first"],
        )
        assert first.batch is not None
        awaiting = service.confirm_item(
            str(prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["second"],
        )
        assert awaiting.batch is not None
        check_id = str(awaiting.checks[0]["id"])
        requested = repo.get_parallel_action_abandonment_decision(
            str(awaiting.batch["id"])
        )
        assert requested == {
            "abandon_allowed": True,
            "abandon_block_reason": "",
        }
        CheckService(repo).cancel(
            check_id,
            fixture["kp"],
            reason="Cancel without learning a result.",
        )
        cancelled = repo.get_parallel_action_abandonment_decision(
            str(awaiting.batch["id"])
        )
        assert cancelled["abandon_allowed"] is True

        override_fixture = _workflow_fixture(repo, with_skill_character=True)
        override_service = ParallelActionWorkflowService(repo)
        override_prepared = _prepare(
            repo, override_fixture, "parallel:abandon-overridden"
        )
        assert override_prepared.batch is not None
        first_item, second_item = override_prepared.batch["items"]
        first = override_service.confirm_item(
            str(override_prepared.batch["id"]),
            str(first_item["action_id"]),
            expected_batch_version=int(override_prepared.batch["version"]),
            expected_adjudication_version=int(first_item["adjudication"]["version"]),
            selected_skill_key="spot_hidden",
            identity=override_fixture["first"],
        )
        assert first.batch is not None
        awaiting_override = override_service.confirm_item(
            str(override_prepared.batch["id"]),
            str(second_item["action_id"]),
            expected_batch_version=int(first.batch["version"]),
            expected_adjudication_version=int(second_item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=override_fixture["second"],
        )
        override_check_id = str(awaiting_override.checks[0]["id"])
        CheckService(repo).resolve(
            override_check_id,
            override_fixture["first"],
            ResolveCheckCommand(
                input_method="physical",
                ones_digit=1,
                tens_digits=(0,),
            ),
        )
        CheckService(repo).override(
            override_check_id,
            override_fixture["kp"],
            success_level="failure",
            passed=False,
            reason="KP records an explicit overridden outcome.",
        )
        overridden = repo.get_parallel_action_abandonment_decision(
            str(override_prepared.batch["id"])
        )
        assert overridden["abandon_allowed"] is False
        assert "known result" in overridden["abandon_block_reason"]


def test_only_active_kp_can_recover_and_stale_authority_can_only_be_abandoned(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "parallel-recover-authority.sqlite3") as connection:
        repo = Repository(connection)
        fixture = _workflow_fixture(repo, with_skill_character=False)
        service = ParallelActionWorkflowService(repo)
        prepared = _prepare(repo, fixture, "parallel:recover-authority")
        assert prepared.batch is not None
        item = prepared.batch["items"][0]
        connection.execute(
            "UPDATE campaign_module_runs SET version = version + 1 WHERE id = ?",
            (fixture["run"]["id"],),
        )
        paused = service.confirm_item(
            str(prepared.batch["id"]),
            str(item["action_id"]),
            expected_batch_version=int(prepared.batch["version"]),
            expected_adjudication_version=int(item["adjudication"]["version"]),
            selected_skill_key=None,
            identity=fixture["first"],
        )
        assert paused.batch is not None

        with pytest.raises(PermissionError, match="KP authority"):
            service.resume_needs_attention(
                str(paused.batch["id"]),
                expected_version=int(paused.batch["version"]),
                identity=fixture["first"],
            )
        with pytest.raises(PermissionError, match="KP authority"):
            service.abandon_needs_attention(
                str(paused.batch["id"]),
                expected_version=int(paused.batch["version"]),
                identity=fixture["first"],
            )
        with pytest.raises(ValueError, match="authority changed"):
            service.resume_needs_attention(
                str(paused.batch["id"]),
                expected_version=int(paused.batch["version"]),
                identity=fixture["kp"],
            )

        abandoned = service.abandon_needs_attention(
            str(paused.batch["id"]),
            expected_version=int(paused.batch["version"]),
            identity=fixture["kp"],
            reason="Frozen run authority is stale; require fresh player actions.",
        )
        assert abandoned.batch is not None
        assert abandoned.batch["status"] == "superseded"
        assert all(action["status"] == "rejected" for action in abandoned.actions)
        replay = service.abandon_needs_attention(
            str(paused.batch["id"]),
            expected_version=int(paused.batch["version"]),
            identity=fixture["kp"],
            reason="Frozen run authority is stale; require fresh player actions.",
        )
        assert replay.batch is not None
        assert replay.batch["version"] == abandoned.batch["version"]

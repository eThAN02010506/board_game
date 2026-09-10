import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_kp.api.auto_kp import player_auto_kp_job
from ai_kp.application.action_adjudication_service import ActionAdjudicationService
from ai_kp.application.auto_kp_queue_service import AutoKpQueueService
from ai_kp.application.errors import UpstreamServiceError
from ai_kp.application.module_run_service import (
    AutomationLevelCommand,
    ModuleRunService,
)
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import TurnService
from ai_kp.bootstrap.settings import Settings
from ai_kp.director.context_builder import ContextAssembly
from ai_kp.director.orchestrator import KpTurnResult
from ai_kp.director.turn_output import KpTurnOutput
from ai_kp.director.world_expansion import WorldExpansionOutput
from ai_kp.infrastructure import auto_kp_parallel_worker, auto_kp_worker
from ai_kp.infrastructure.auto_kp_worker import process_claimed_auto_kp_job
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.modules.ingestion import ModuleChunk
from tests.test_parallel_action_planning_service import MixedRouteDirector
from tests.test_parallel_action_workflow_service import (
    _pause_parallel_batch,
    _prepare,
    _workflow_fixture,
)


class FakeDirector:
    async def handle_player_action(self, **kwargs):
        return KpTurnResult(
            output=KpTurnOutput.model_validate(
                {
                    "public_narration": "自动 KP worker 推进了行动。",
                    "kp_notes": "fake worker",
                    "action_ruling": {
                        "goal": kwargs["player_action"],
                        "method": "worker fake",
                        "target": "当前场景",
                        "feasibility": "possible",
                        "resolution": "automatic",
                        "reason": "测试用稳定输出。",
                        "maximum_effect": "只写公开叙事。",
                        "alternative": "",
                    },
                    "proposed_checks": [],
                    "proposed_events": [],
                    "proposed_memories": [],
                    "proposed_npc_updates": [],
                    "proposed_map_moves": [],
                    "proposed_facts": [],
                }
            ),
            context=ContextAssembly(
                messages=[],
                included_sources=[],
                excluded_sources=[],
                token_estimate=0,
                visibility_scope="kp",
            ),
        )


class FakeWorldGapDirector:
    async def handle_world_expansion(self, **_kwargs):
        return SimpleNamespace(
            output=WorldExpansionOutput.model_validate(
                {
                    "public_narration": "镇中心有一间小型警务办公室。",
                    "kp_notes": "低副作用环境补全。",
                    "candidate": {
                        "expansion_kind": "environment",
                        "subject": "小镇警务办公室",
                        "proposal": "小镇有一间由治安官值守的警务办公室。",
                        "rationale": "符合时代、地点和调查意图。",
                        "confidence": "medium",
                        "assumptions": [],
                        "conflicts": [],
                        "alternatives": [
                            {
                                "title": "邻镇负责",
                                "description": "本镇没有常驻警务办公室。",
                                "tradeoff": "需要旅行查档。",
                            },
                            {
                                "title": "临时驻点",
                                "description": "只有巡警临时驻点。",
                                "tradeoff": "可用档案更少。",
                            },
                        ],
                    },
                }
            ),
            context=ContextAssembly(
                messages=[],
                included_sources=[],
                excluded_sources=[],
                token_estimate=0,
                visibility_scope="kp",
            ),
        )


class UnavailableParallelDirector:
    async def interpret_tabletop_turn(self, **_kwargs):
        raise UpstreamServiceError("weak model returned no usable response")

    async def respond_tabletop_turn(self, **_kwargs):
        raise UpstreamServiceError("weak model returned no usable response")

    async def select_kernel_action(self, **_kwargs):
        raise AssertionError("selection must not run after interpretation failure")

    async def author_kernel_world_expansion(self, **_kwargs):
        raise AssertionError("authoring must not run after interpretation failure")

    async def narrate_kernel_action(self, **_kwargs):
        raise AssertionError("narration must not run after interpretation failure")


def _make_auto_job_runnable(connection, job_id: str) -> None:
    connection.execute(
        "UPDATE auto_kp_jobs SET next_run_at = CURRENT_TIMESTAMP WHERE id = ?",
        (job_id,),
    )


def _full_ai_blind_parallel_batch(
    repo: Repository,
    fixture: dict,
    *,
    key: str,
) -> tuple[dict, dict]:
    run = repo.get_active_campaign_module_run(str(fixture["run"]["campaign_id"]))
    assert run is not None
    ModuleRunService(repo).set_automation_level(
        str(run["id"]),
        command=AutomationLevelCommand(
            expected_version=int(run["version"]),
            level="ai_kp",
            reason="automatic blind parallel check test",
        ),
        member_id=fixture["kp"].member_id,
    )
    repo.connection.commit()
    prepared = _prepare(repo, fixture, key)
    assert prepared.batch is not None
    first_item, second_item = prepared.batch["items"]
    workflow = auto_kp_parallel_worker.ParallelActionWorkflowService(repo)
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
    assert awaiting.status == "awaiting_checks" and awaiting.batch is not None
    check_id = str(awaiting.checks[0]["id"])
    repo.connection.execute(
        """
        UPDATE skill_checks
        SET hidden = 1, visibility = 'blind', allow_push = 0
        WHERE id = ?
        """,
        (check_id,),
    )
    return repo.get_parallel_action_batch(str(awaiting.batch["id"])), repo.get_skill_check(
        check_id
    )


def test_auto_kp_queue_collects_concurrent_player_actions(tmp_path: Path) -> None:
    connection = connect(tmp_path / "auto-kp-parallel-queue.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        campaign = repo.create_campaign("Auto KP parallel queue")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        first_bundle = sessions.join(kp_bundle["join_code"], display_name="Ada")
        second_bundle = sessions.join(kp_bundle["join_code"], display_name="Bert")
        first_identity = repo.authenticate_access_token(first_bundle["access_token"])
        second_identity = repo.authenticate_access_token(second_bundle["access_token"])
        assert first_identity is not None
        assert second_identity is not None
        turns = TurnService(repo)
        first_action = turns.submit_player_action(
            first_identity,
            action_text="我守住巷口。",
            client_action_id="parallel-queue-action-001",
        )
        first_job = AutoKpQueueService(repo).enqueue_player_action(first_action["id"])
        assert first_job["job_type"] == "player_action"

        commit_job = repo.enqueue_auto_kp_job(
            campaign_id=campaign["id"],
            job_type="parallel_actions",
            resource_id="parallelbatch_existing",
            idempotency_key="parallel-commit-existing-batch",
            payload={
                "phase": "commit",
                "batch_id": "parallelbatch_existing",
                "expected_version": 3,
                "session_id": kp_bundle["session"]["id"],
            },
        )

        second_action = turns.submit_player_action(
            second_identity,
            action_text="我检查后门。",
            client_action_id="parallel-queue-action-002",
        )
        batch_job = AutoKpQueueService(repo).enqueue_player_action(second_action["id"])

        assert batch_job["job_type"] == "parallel_actions"
        assert batch_job["payload"]["phase"] == "prepare"
        assert player_auto_kp_job(batch_job)["phase"] == "prepare"
        assert "resource_id" not in player_auto_kp_job(batch_job)
        assert batch_job["payload"]["action_ids"] == sorted(
            [first_action["id"], second_action["id"]]
        )
        assert repo.get_auto_kp_job(first_job["id"])["status"] == "cancelled"
        assert repo.get_auto_kp_job(commit_job["id"])["status"] == "queued"
        assert [
            job["id"]
            for job in repo.list_player_auto_kp_jobs(
                campaign["id"], first_identity.member_id
            )
            if job["status"] == "queued"
        ] == [batch_job["id"]]
        assert [
            job["id"]
            for job in repo.list_player_auto_kp_jobs(
                campaign["id"], second_identity.member_id
            )
            if job["status"] == "queued"
        ] == [batch_job["id"]]
    finally:
        connection.close()


def test_auto_kp_queue_expands_one_unclaimed_cohort_to_four_players(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "auto-kp-four-player-cohort.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        campaign = repo.create_campaign("Expandable Auto KP cohort")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        identities = []
        for name in ("Ada", "Bert", "Cleo", "Dara"):
            bundle = sessions.join(kp_bundle["join_code"], display_name=name)
            identity = repo.authenticate_access_token(bundle["access_token"])
            assert identity is not None
            identities.append(identity)
        turns = TurnService(repo)
        actions = []
        queued = []
        for index, identity in enumerate(identities):
            action = turns.submit_player_action(
                identity,
                action_text=f"{identity.display_name} acts",
                client_action_id=f"four-player-cohort-{index}",
            )
            connection.execute(
                "UPDATE player_actions SET created_at = ? WHERE id = ?",
                ("2026-08-21 12:00:00", action["id"]),
            )
            actions.append(action)
            queued.append(AutoKpQueueService(repo).enqueue_player_action(action["id"]))

        first_single, pair, triple, quartet = queued
        assert first_single["job_type"] == "player_action"
        assert repo.get_auto_kp_job(first_single["id"])["status"] == "cancelled"
        assert pair["id"] == triple["id"] == quartet["id"]
        assert pair["next_run_at"] == triple["next_run_at"] == quartet["next_run_at"]
        assert pair["next_run_at"] <= first_single["next_run_at"]
        assert quartet["job_type"] == "parallel_actions"
        assert quartet["status"] == "queued"
        assert quartet["attempt_count"] == 0
        assert quartet["payload"]["action_ids"] == sorted(
            str(action["id"]) for action in actions
        )
        assert quartet["payload"]["collection_anchor_action_id"] == min(
            str(action["id"]) for action in actions[:2]
        )
        assert AutoKpQueueService(repo).enqueue_player_action(actions[-1]["id"])[
            "id"
        ] == quartet["id"]

        active = [
            job
            for job in repo.list_auto_kp_jobs(campaign["id"], limit=100)
            if job["status"] in {"queued", "running", "retry_wait"}
        ]
        assert [job["id"] for job in active] == [quartet["id"]]
        assert all(
            [
                job["id"]
                for job in repo.list_player_auto_kp_jobs(
                    campaign["id"], identity.member_id
                )
                if job["status"] == "queued"
            ]
            == [quartet["id"]]
            for identity in identities
        )
    finally:
        connection.close()


def test_auto_kp_queue_does_not_merge_repeated_actions_from_one_seat(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "auto-kp-same-seat.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        campaign = repo.create_campaign("Sequential player actions")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        player_bundle = sessions.join(kp_bundle["join_code"], display_name="Ada")
        identity = repo.authenticate_access_token(player_bundle["access_token"])
        assert identity is not None
        turns = TurnService(repo)
        first = turns.submit_player_action(
            identity, action_text="我先观察门口。", client_action_id="same-seat-1"
        )
        AutoKpQueueService(repo).enqueue_player_action(first["id"])
        second = turns.submit_player_action(
            identity, action_text="我再决定离开。", client_action_id="same-seat-2"
        )

        queued = AutoKpQueueService(repo).enqueue_player_action(second["id"])

        assert queued["job_type"] == "player_action"
        assert queued["payload"]["action_id"] == second["id"]
    finally:
        connection.close()


def test_auto_kp_collection_respects_another_players_manual_kp_choice(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "auto-kp-player-opt-out.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        campaign = repo.create_campaign("Per-action Auto KP choice")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        manual_bundle = sessions.join(kp_bundle["join_code"], display_name="Ada")
        auto_bundle = sessions.join(kp_bundle["join_code"], display_name="Bert")
        manual_identity = repo.authenticate_access_token(manual_bundle["access_token"])
        auto_identity = repo.authenticate_access_token(auto_bundle["access_token"])
        assert manual_identity is not None
        assert auto_identity is not None
        turns = TurnService(repo)
        manual_action = turns.submit_player_action(
            manual_identity,
            action_text="交给真人 KP 处理。",
            client_action_id="manual-kp-opt-out",
        )
        auto_action = turns.submit_player_action(
            auto_identity,
            action_text="请 AI KP 处理。",
            client_action_id="auto-kp-opt-in",
        )
        for action in (manual_action, auto_action):
            connection.execute(
                "UPDATE player_actions SET created_at = '2026-08-21 12:00:00' WHERE id = ?",
                (action["id"],),
            )

        queued = AutoKpQueueService(repo).enqueue_player_action(auto_action["id"])

        assert queued["job_type"] == "player_action"
        assert queued["resource_id"] == auto_action["id"]
        assert repo.get_player_action(manual_action["id"])["status"] == "submitted"
        assert not any(
            job["job_type"] == "parallel_actions"
            for job in repo.list_auto_kp_jobs(campaign["id"], limit=100)
        )
    finally:
        connection.close()


def test_disjoint_parallel_collection_windows_do_not_cancel_each_other(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "auto-kp-disjoint-windows.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        campaign = repo.create_campaign("Disjoint parallel windows")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        identities = []
        for name in ("Ada", "Bert", "Cleo", "Dara"):
            bundle = sessions.join(kp_bundle["join_code"], display_name=name)
            identity = repo.authenticate_access_token(bundle["access_token"])
            assert identity is not None
            identities.append(identity)
        turns = TurnService(repo)
        actions = []
        for index, identity in enumerate(identities[:2]):
            action = turns.submit_player_action(
                identity,
                action_text=f"{identity.display_name} acts",
                client_action_id=f"disjoint-window-{index}",
            )
            connection.execute(
                "UPDATE player_actions SET created_at = '2026-08-21 12:00:00' WHERE id = ?",
                (action["id"],),
            )
            actions.append(action)
            if index == 0:
                first_single = AutoKpQueueService(repo).enqueue_player_action(
                    action["id"]
                )
        first_batch = AutoKpQueueService(repo).enqueue_player_action(actions[1]["id"])

        for index, identity in enumerate(identities[2:], start=2):
            action = turns.submit_player_action(
                identity,
                action_text=f"{identity.display_name} acts",
                client_action_id=f"disjoint-window-{index}",
            )
            connection.execute(
                "UPDATE player_actions SET created_at = '2026-08-21 12:00:10' WHERE id = ?",
                (action["id"],),
            )
            actions.append(action)
            if index == 2:
                second_single = AutoKpQueueService(repo).enqueue_player_action(
                    action["id"]
                )
        second_batch = AutoKpQueueService(repo).enqueue_player_action(actions[3]["id"])

        assert repo.get_auto_kp_job(first_single["id"])["status"] == "cancelled"
        assert repo.get_auto_kp_job(second_single["id"])["status"] == "cancelled"
        assert repo.get_auto_kp_job(first_batch["id"])["status"] == "queued"
        assert repo.get_auto_kp_job(second_batch["id"])["status"] == "queued"
        assert set(first_batch["payload"]["action_ids"]) == {
            actions[0]["id"],
            actions[1]["id"],
        }
        assert set(second_batch["payload"]["action_ids"]) == {
            actions[2]["id"],
            actions[3]["id"],
        }
    finally:
        connection.close()


def test_overlapping_collection_windows_do_not_strand_an_earlier_action(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "auto-kp-overlapping-windows.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        campaign = repo.create_campaign("Overlapping parallel windows")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        identities = []
        for name in ("Ada", "Bert", "Cleo"):
            bundle = sessions.join(kp_bundle["join_code"], display_name=name)
            identity = repo.authenticate_access_token(bundle["access_token"])
            assert identity is not None
            identities.append(identity)
        turns = TurnService(repo)
        actions = []
        for index, (identity, created_at) in enumerate(
            zip(
                identities,
                (
                    "2026-08-21 12:00:00.000000",
                    "2026-08-21 12:00:01.900000",
                    "2026-08-21 12:00:03.800000",
                ),
                strict=True,
            )
        ):
            action = turns.submit_player_action(
                identity,
                action_text=f"{identity.display_name} acts",
                client_action_id=f"overlapping-window-{index}",
            )
            connection.execute(
                "UPDATE player_actions SET created_at = ? WHERE id = ?",
                (created_at, action["id"]),
            )
            actions.append(action)
            if index == 0:
                first_single = AutoKpQueueService(repo).enqueue_player_action(
                    action["id"]
                )
            elif index == 1:
                first_batch = AutoKpQueueService(repo).enqueue_player_action(
                    action["id"]
                )
            else:
                third_job = AutoKpQueueService(repo).enqueue_player_action(action["id"])

        assert repo.get_auto_kp_job(first_single["id"])["status"] == "cancelled"
        assert repo.get_auto_kp_job(first_batch["id"])["status"] == "queued"
        assert set(first_batch["payload"]["action_ids"]) == {
            actions[0]["id"],
            actions[1]["id"],
        }
        assert third_job["job_type"] == "player_action"
        assert third_job["resource_id"] == actions[2]["id"]
    finally:
        connection.close()


def test_collection_anchor_does_not_chain_actions_outside_its_fixed_window(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "auto-kp-fixed-anchor.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        campaign = repo.create_campaign("Fixed collection anchor")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        identities = []
        for name in ("Anchor", "Outside", "Middle", "Outside Peer"):
            bundle = sessions.join(kp_bundle["join_code"], display_name=name)
            identity = repo.authenticate_access_token(bundle["access_token"])
            assert identity is not None
            identities.append(identity)
        turns = TurnService(repo)

        def submit(index: int, created_at: str) -> dict:
            action = turns.submit_player_action(
                identities[index],
                action_text=f"fixed-window-{index}",
                client_action_id=f"fixed-window-{index}",
            )
            connection.execute(
                "UPDATE player_actions SET created_at = ? WHERE id = ?",
                (created_at, action["id"]),
            )
            return action

        anchor = submit(0, "2026-08-21 12:00:00.000000")
        anchor_single = AutoKpQueueService(repo).enqueue_player_action(anchor["id"])
        outside = submit(1, "2026-08-21 12:00:03.800000")
        outside_single = AutoKpQueueService(repo).enqueue_player_action(outside["id"])
        middle = submit(2, "2026-08-21 12:00:01.900000")

        anchor_batch = AutoKpQueueService(repo).enqueue_player_action(middle["id"])

        assert anchor_batch["job_type"] == "parallel_actions"
        assert set(anchor_batch["payload"]["action_ids"]) == {
            anchor["id"],
            middle["id"],
        }
        assert outside["id"] not in anchor_batch["payload"]["action_ids"]
        assert anchor_batch["next_run_at"] <= outside_single["next_run_at"]
        assert repo.get_auto_kp_job(anchor_single["id"])["status"] == "cancelled"
        assert repo.get_auto_kp_job(outside_single["id"])["status"] == "queued"

        outside_peer = submit(3, "2026-08-21 12:00:03.900000")
        outside_batch = AutoKpQueueService(repo).enqueue_player_action(
            outside_peer["id"]
        )
        assert outside_batch["job_type"] == "parallel_actions"
        assert set(outside_batch["payload"]["action_ids"]) == {
            outside["id"],
            outside_peer["id"],
        }
        assert repo.get_auto_kp_job(outside_single["id"])["status"] == "cancelled"
        assert {
            job["id"]
            for job in repo.list_auto_kp_jobs(campaign["id"], limit=100)
            if job["status"] == "queued"
        } == {anchor_batch["id"], outside_batch["id"]}
    finally:
        connection.close()


def test_running_parallel_prepare_cohort_is_frozen_for_later_players(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "auto-kp-running-cohort.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        campaign = repo.create_campaign("Running cohort fence")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        identities = []
        for name in ("Ada", "Bert", "Cleo"):
            bundle = sessions.join(kp_bundle["join_code"], display_name=name)
            identity = repo.authenticate_access_token(bundle["access_token"])
            assert identity is not None
            identities.append(identity)
        turns = TurnService(repo)
        actions = []
        for index, identity in enumerate(identities[:2]):
            action = turns.submit_player_action(
                identity,
                action_text=f"running-cohort-{index}",
                client_action_id=f"running-cohort-{index}",
            )
            connection.execute(
                "UPDATE player_actions SET created_at = ? WHERE id = ?",
                ("2026-08-21 12:00:00", action["id"]),
            )
            actions.append(action)
            cohort = AutoKpQueueService(repo).enqueue_player_action(action["id"])
        assert cohort["job_type"] == "parallel_actions"
        original_ids = list(cohort["payload"]["action_ids"])
        _make_auto_job_runnable(connection, str(cohort["id"]))
        claimed = repo.claim_next_auto_kp_job(worker_id="freeze-cohort-worker")
        assert claimed is not None and claimed["id"] == cohort["id"]

        third = turns.submit_player_action(
            identities[2],
            action_text="running-cohort-2",
            client_action_id="running-cohort-2",
        )
        connection.execute(
            "UPDATE player_actions SET created_at = ? WHERE id = ?",
            ("2026-08-21 12:00:00", third["id"]),
        )
        third_job = AutoKpQueueService(repo).enqueue_player_action(third["id"])

        frozen = repo.get_auto_kp_job(str(cohort["id"]))
        assert frozen["status"] == "running"
        assert frozen["payload"]["action_ids"] == original_ids
        assert third_job["job_type"] == "player_action"
        assert third_job["resource_id"] == third["id"]
    finally:
        connection.close()


def test_queued_cohort_does_not_expand_across_changed_run_authority(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "auto-kp-cohort-run-authority.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=False)
        campaign_id = str(fixture["run"]["campaign_id"])
        action_ids = [str(action["id"]) for action in fixture["actions"]]
        for action_id in action_ids:
            connection.execute(
                "UPDATE player_actions SET created_at = ? WHERE id = ?",
                ("2026-08-21 12:00:00", action_id),
            )
        AutoKpQueueService(repo).enqueue_player_action(action_ids[0])
        cohort = AutoKpQueueService(repo).enqueue_player_action(action_ids[1])
        original_ids = list(cohort["payload"]["action_ids"])

        connection.execute(
            "UPDATE campaign_module_runs SET version = version + 1 WHERE id = ?",
            (fixture["run"]["id"],),
        )
        third_bundle = SessionService(repo).join(
            str(fixture["session"]["join_code"]), display_name="Cleo"
        )
        third_identity = repo.authenticate_access_token(
            str(third_bundle["access_token"])
        )
        assert third_identity is not None
        third = TurnService(repo).submit_player_action(
            third_identity,
            action_text="observe quietly",
            client_action_id="changed-run-third-action",
        )
        connection.execute(
            "UPDATE player_actions SET created_at = ? WHERE id = ?",
            ("2026-08-21 12:00:00", third["id"]),
        )

        third_job = AutoKpQueueService(repo).enqueue_player_action(third["id"])

        assert third_job["job_type"] == "player_action"
        assert third_job["run_id"] == fixture["run"]["id"]
        assert repo.get_auto_kp_job(str(cohort["id"]))["payload"][
            "action_ids"
        ] == original_ids
        assert repo.get_auto_kp_job(str(cohort["id"]))["campaign_id"] == campaign_id
    finally:
        connection.close()


def test_running_single_action_is_not_recollected_into_a_parallel_batch(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "auto-kp-running-single.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        campaign = repo.create_campaign("Running action collection fence")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        first_bundle = sessions.join(kp_bundle["join_code"], display_name="Ada")
        second_bundle = sessions.join(kp_bundle["join_code"], display_name="Bert")
        first_identity = repo.authenticate_access_token(first_bundle["access_token"])
        second_identity = repo.authenticate_access_token(second_bundle["access_token"])
        assert first_identity is not None
        assert second_identity is not None
        turns = TurnService(repo)
        first_action = turns.submit_player_action(
            first_identity,
            action_text="Ada acts",
            client_action_id="running-single-0",
        )
        connection.execute(
            "UPDATE player_actions SET created_at = '2026-08-21 12:00:00' WHERE id = ?",
            (first_action["id"],),
        )
        first_job = AutoKpQueueService(repo).enqueue_player_action(first_action["id"])
        connection.execute(
            "UPDATE auto_kp_jobs SET status = 'running' WHERE id = ?",
            (first_job["id"],),
        )
        second_action = turns.submit_player_action(
            second_identity,
            action_text="Bert acts",
            client_action_id="running-single-1",
        )
        connection.execute(
            "UPDATE player_actions SET created_at = '2026-08-21 12:00:00' WHERE id = ?",
            (second_action["id"],),
        )

        second_job = AutoKpQueueService(repo).enqueue_player_action(second_action["id"])

        assert repo.get_auto_kp_job(first_job["id"])["status"] == "running"
        assert second_job["job_type"] == "player_action"
        assert second_job["resource_id"] == second_action["id"]
        assert not any(
            job["job_type"] == "parallel_actions"
            and job.get("payload", {}).get("phase") == "prepare"
            for job in repo.list_auto_kp_jobs(campaign["id"], limit=100)
        )
    finally:
        connection.close()


def test_auto_kp_queue_collects_only_recent_parseable_other_seats(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "auto-kp-collection-window.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        campaign = repo.create_campaign("Bounded collection window")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        bundles = [
            sessions.join(kp_bundle["join_code"], display_name=name)
            for name in ("Stale", "Malformed", "Current")
        ]
        identities = [
            repo.authenticate_access_token(bundle["access_token"])
            for bundle in bundles
        ]
        assert all(identity is not None for identity in identities)
        turns = TurnService(repo)
        actions = [
            turns.submit_player_action(
                identity,
                action_text=f"action-{index}",
                client_action_id=f"collection-window-{index}",
            )
            for index, identity in enumerate(identities)
            if identity is not None
        ]
        connection.execute(
            "UPDATE player_actions SET created_at = datetime(CURRENT_TIMESTAMP, '-1 hour') WHERE id = ?",
            (actions[0]["id"],),
        )
        connection.execute(
            "UPDATE player_actions SET created_at = 'not-a-timestamp' WHERE id = ?",
            (actions[1]["id"],),
        )

        queued = AutoKpQueueService(repo).enqueue_player_action(actions[2]["id"])

        assert queued["job_type"] == "player_action"
        assert queued["resource_id"] == actions[2]["id"]
        assert queued["payload"]["action_id"] == actions[2]["id"]
    finally:
        connection.close()


def test_auto_kp_queue_orders_collection_by_creation_time_then_id(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "auto-kp-collection-order.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        campaign = repo.create_campaign("Fair collection order")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        first_bundle = sessions.join(kp_bundle["join_code"], display_name="First")
        second_bundle = sessions.join(kp_bundle["join_code"], display_name="Second")
        first_identity = repo.authenticate_access_token(first_bundle["access_token"])
        second_identity = repo.authenticate_access_token(second_bundle["access_token"])
        assert first_identity is not None and second_identity is not None
        turns = TurnService(repo)
        first = turns.submit_player_action(
            first_identity,
            action_text="first action",
            client_action_id="collection-order-first",
        )
        second = turns.submit_player_action(
            second_identity,
            action_text="second action",
            client_action_id="collection-order-second",
        )
        connection.execute(
            "UPDATE player_actions SET created_at = '2026-08-21 12:00:00' WHERE id = ?",
            (first["id"],),
        )
        connection.execute(
            "UPDATE player_actions SET created_at = '2026-08-21 12:00:01' WHERE id = ?",
            (second["id"],),
        )

        AutoKpQueueService(repo).enqueue_player_action(first["id"])
        queued = AutoKpQueueService(repo).enqueue_player_action(second["id"])

        assert queued["job_type"] == "parallel_actions"
        assert queued["payload"]["action_ids"] == [first["id"], second["id"]]
    finally:
        connection.close()


def test_auto_kp_worker_processes_claimed_player_action_job(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "auto-kp-worker.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        captured_settings: dict[str, str] = {}

        def configured_director(_repo, resolved_settings, _registry):
            captured_settings.update(
                base_url=resolved_settings.llm_base_url,
                model=resolved_settings.llm_model,
            )
            return FakeDirector()

        monkeypatch.setattr(
            auto_kp_worker,
            "_director",
            configured_director,
        )
        repo.save_model_configuration(
            provider_type="openai_compatible",
            base_url="http://selected-auto-kp.test/v1",
            api_key="selected-key",
            model="selected-auto-kp-model",
            local_model_path=None,
            local_port=8011,
            semantic_profile="small",
        )
        campaign = repo.create_campaign("Auto KP worker")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        player_bundle = sessions.join(kp_bundle["join_code"], display_name="Ada")
        player_identity = repo.authenticate_access_token(player_bundle["access_token"])
        assert player_identity is not None
        action = TurnService(repo).submit_player_action(
            player_identity,
            action_text="我检查窗户。",
            client_action_id="worker-action-0001",
        )
        repo.enqueue_auto_kp_job(
            campaign_id=campaign["id"],
            job_type="player_action",
            resource_id=action["id"],
            idempotency_key="worker-action-job-0001",
        )
        claimed = repo.claim_next_auto_kp_job(worker_id="test-worker")
        assert claimed is not None
        connection.commit()

        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
                llm_base_url="http://unused.invalid/v1",
                llm_model="fake",
            ),
        )

        saved = repo.get_auto_kp_job(claimed["id"])
        assert captured_settings == {
            "base_url": "http://selected-auto-kp.test/v1",
            "model": "selected-auto-kp-model",
        }
        assert saved["status"] == "succeeded"
        assert saved["result"]["status"] == "awaiting_confirmation"
        assert saved["result"]["player_action"]["status"] == "reviewed"
        ruling = saved["result"]["adjudication"]
        _, proposal, checks, applied = ActionAdjudicationService(repo).confirm(
            action["id"],
            expected_version=ruling["version"],
            selected_skill=ruling["selected_skill"],
            identity=player_identity,
        )
        assert applied is True
        assert proposal["status"] == "approved"
        assert checks == []
        assert repo.get_player_action(action["id"])["status"] == "resolved"
        event = connection.execute(
            "SELECT summary FROM events WHERE campaign_id = ?",
            (campaign["id"],),
        ).fetchone()
        assert "worker 推进" in event["summary"]
        assert json.dumps(saved["result"], ensure_ascii=False)
    finally:
        connection.close()


def test_auto_kp_worker_routes_world_gap_and_resolves_player_action(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "auto-kp-world-gap.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        monkeypatch.setattr(
            auto_kp_worker,
            "_director",
            lambda *_args, **_kwargs: FakeWorldGapDirector(),
        )
        campaign = repo.create_campaign("Auto KP world gap")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        kp_identity = repo.authenticate_access_token(kp_bundle["access_token"])
        assert kp_identity is not None
        module = repo.create_module(
            campaign["id"],
            "钟楼片段",
            [
                ModuleChunk(
                    title="钟楼",
                    text="钟楼门前有碎玻璃。",
                    visibility="kp",
                    spoiler_tag="act-1",
                    order_index=0,
                )
            ],
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key="town",
            active_spoiler_tags=["act-1"],
            state={},
            started_by_member_id=kp_identity.member_id,
        )
        ModuleRunService(repo).set_automation_level(
            run["id"],
            command=AutomationLevelCommand(
                expected_version=run["version"],
                level="ai_kp",
                reason="world gap worker test",
            ),
            member_id=kp_identity.member_id,
        )
        player_bundle = sessions.join(kp_bundle["join_code"], display_name="Ada")
        player_identity = repo.authenticate_access_token(player_bundle["access_token"])
        assert player_identity is not None
        action = TurnService(repo).submit_player_action(
            player_identity,
            action_text="我去寻找镇上的警务办公室。",
            client_action_id="world-gap-action-0001",
        )
        job = repo.enqueue_auto_kp_job(
            campaign_id=campaign["id"],
            run_id=run["id"],
            job_type="player_action",
            resource_id=action["id"],
            idempotency_key="world-gap-job-0001",
        )
        claimed = repo.claim_next_auto_kp_job(worker_id="test-worker")
        assert claimed is not None
        connection.commit()

        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
                llm_model="fake-world-gap",
            ),
        )

        saved = repo.get_auto_kp_job(job["id"])
        assert saved["status"] == "succeeded"
        assert saved["stage"] == "world_expansion"
        assert saved["result"]["status"] == "awaiting_confirmation"
        assert repo.get_player_action(action["id"])["status"] == "reviewed"
        ruling = saved["result"]["adjudication"]
        _, proposal, _, applied = ActionAdjudicationService(repo).confirm(
            action["id"],
            expected_version=ruling["version"],
            selected_skill=None,
            identity=player_identity,
        )
        assert applied is True
        assert proposal["status"] == "approved"
        assert repo.get_player_action(action["id"])["status"] == "resolved"
        facts = repo.list_fact_heads(campaign["id"])
        assert [(item.fact.subject, item.fact.predicate) for item in facts] == [
            ("小镇警务办公室", "存在或成立")
        ]
    finally:
        connection.close()


def test_auto_kp_worker_retries_failed_service_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "auto-kp-worker-failure.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        campaign = repo.create_campaign("Auto KP worker failure")
        job = repo.enqueue_auto_kp_job(
            campaign_id=campaign["id"],
            job_type="player_action",
            resource_id="action_missing",
            idempotency_key="worker-failed-result-0001",
        )
        claimed = repo.claim_next_auto_kp_job(worker_id="test-worker")
        assert claimed is not None
        connection.commit()

        async def failed_dispatch(*_args, **_kwargs):
            return {
                "status": "failed",
                "stage": "player_action",
                "message": "approval conflict",
            }

        monkeypatch.setattr(auto_kp_worker, "_dispatch_auto_kp_job", failed_dispatch)
        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
            ),
        )

        saved = repo.get_auto_kp_job(job["id"])
        assert saved["status"] == "retry_wait"
        assert saved["stage"] == "waiting_retry"
        assert saved["last_error"] == "approval conflict"
    finally:
        connection.close()


def test_full_ai_parallel_commit_job_settles_without_calling_a_model(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "auto-kp-parallel-commit.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=False)
        run = repo.get_active_campaign_module_run(str(fixture["run"]["campaign_id"]))
        assert run is not None
        ModuleRunService(repo).set_automation_level(
            str(run["id"]),
            command=AutomationLevelCommand(
                expected_version=int(run["version"]),
                level="ai_kp",
                reason="automatic parallel commit test",
            ),
            member_id=fixture["kp"].member_id,
        )
        connection.commit()
        prepared = _prepare(repo, fixture, "parallel:auto-kp-commit")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        service = auto_kp_parallel_worker.ParallelActionWorkflowService(repo)
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
        assert ready.status == "ready" and ready.batch is not None
        job = AutoKpQueueService(repo).enqueue_parallel_commit(
            str(ready.batch["id"])
        )
        assert job is not None
        assert job["payload"] == {
            "phase": "commit",
            "batch_id": ready.batch["id"],
            "expected_version": ready.batch["version"],
        }
        for member in (fixture["first"], fixture["second"]):
            visible = repo.list_player_auto_kp_jobs(
                str(ready.batch["campaign_id"]), member.member_id
            )
            assert [candidate["id"] for candidate in visible] == [job["id"]]
        projected = player_auto_kp_job(job)
        assert projected["phase"] == "settlement"
        assert projected["stage"] == "parallel_settlement"
        assert "resource_id" not in projected
        claimed = repo.claim_next_auto_kp_job(worker_id="test-worker")
        assert claimed is not None
        connection.commit()

        def unexpected_director(*_args, **_kwargs):
            raise AssertionError("parallel commit must not call a model")

        monkeypatch.setattr(auto_kp_worker, "_director", unexpected_director)
        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
                llm_model="unused",
            ),
        )

        saved = repo.get_auto_kp_job(str(job["id"]))
        assert saved["status"] == "succeeded"
        assert saved["stage"] == "parallel_commit"
        assert saved["result"]["status"] == "settled"
        assert repo.get_parallel_action_batch(str(ready.batch["id"]))[
            "status"
        ] == "settled"
        assert repo.get_scenario_run_state(str(fixture["run"]["id"]))[
            "state_version"
        ] == 1
    finally:
        connection.close()


def test_full_ai_parallel_state_drift_requires_fresh_group_consent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "auto-kp-parallel-state-reconfirm.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=False)
        run = repo.get_active_campaign_module_run(str(fixture["run"]["campaign_id"]))
        assert run is not None
        ModuleRunService(repo).set_automation_level(
            str(run["id"]),
            command=AutomationLevelCommand(
                expected_version=int(run["version"]),
                level="ai_kp",
                reason="state drift recovery test",
            ),
            member_id=fixture["kp"].member_id,
        )
        connection.commit()
        prepared = _prepare(repo, fixture, "parallel:auto-kp-state-reconfirm")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        workflow = auto_kp_parallel_worker.ParallelActionWorkflowService(repo)
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
        assert ready.batch is not None and ready.status == "ready"
        job = AutoKpQueueService(repo).enqueue_parallel_commit(str(ready.batch["id"]))
        assert job is not None
        claimed = repo.claim_next_auto_kp_job(worker_id="state-drift-worker")
        assert claimed is not None
        connection.execute(
            """
            UPDATE scenario_run_states
            SET state_version = state_version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE run_id = ?
            """,
            (run["id"],),
        )
        connection.commit()

        monkeypatch.setattr(
            auto_kp_worker,
            "_director",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("state drift recovery must not call a model")
            ),
        )
        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
                llm_model="unused",
            ),
        )

        saved = repo.get_auto_kp_job(str(job["id"]))
        assert saved["status"] == "succeeded"
        assert saved["stage"] == "parallel_reconfirmation"
        stale = repo.get_parallel_action_batch(str(ready.batch["id"]))
        assert stale["status"] == "superseded"
        for member in (fixture["first"], fixture["second"]):
            regather = repo.get_active_parallel_action_regather_for_member(
                member.campaign_id,
                member.session_id,
                member.member_id,
            )
            assert regather is not None
            assert regather["status"] == "gathering"
            assert regather["submitted_count"] == 0
        assert repo.list_scenario_command_batches(str(run["id"])) == []
    finally:
        connection.close()


def test_full_ai_regather_run_drift_reopens_without_reusing_consent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "auto-kp-regather-run-reconfirm.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=False)
        run = repo.get_active_campaign_module_run(str(fixture["run"]["campaign_id"]))
        assert run is not None
        ModuleRunService(repo).set_automation_level(
            str(run["id"]),
            command=AutomationLevelCommand(
                expected_version=int(run["version"]),
                level="ai_kp",
                reason="regather run drift test",
            ),
            member_id=fixture["kp"].member_id,
        )
        connection.commit()
        prepared = _prepare(repo, fixture, "parallel:auto-kp-regather-run")
        assert prepared.batch is not None
        source = prepared.batch
        first_item = source["items"][0]
        auto_kp_parallel_worker.ParallelActionWorkflowService(repo).request_revision(
            str(source["id"]),
            str(first_item["action_id"]),
            expected_version=int(source["version"]),
            identity=fixture["first"],
        )
        regather = repo.create_parallel_action_regather(
            str(source["id"]),
            actor_member_id=fixture["first"].member_id,
            reason="players revise the shared plan",
        )
        replacements = []
        for index, identity in enumerate((fixture["first"], fixture["second"]), 1):
            action = TurnService(repo).submit_player_action(
                identity,
                action_text=f"fresh replacement action {index}",
                client_action_id=f"regather-run-drift-{index}",
            )
            replacements.append(action)
            regather = repo.register_parallel_action_regather_submission(
                str(regather["id"]),
                str(action["id"]),
                actor_member_id=identity.member_id,
                expected_version=int(regather["version"]),
                auto_kp_requested=True,
            )
        job = AutoKpQueueService(repo).enqueue_parallel_regather(str(regather["id"]))
        assert job is not None
        claimed = repo.claim_next_auto_kp_job(worker_id="regather-run-drift-worker")
        assert claimed is not None
        connection.execute(
            """
            UPDATE campaign_module_runs
            SET version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (run["id"],),
        )
        connection.commit()

        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
                llm_model="must-not-be-called",
            ),
        )

        saved = repo.get_auto_kp_job(str(job["id"]))
        assert saved["status"] == "succeeded"
        assert saved["stage"] == "parallel_reconfirmation"
        reopened = repo.get_parallel_action_regather(str(regather["id"]))
        assert reopened["status"] == "gathering"
        assert reopened["submitted_count"] == 0
        assert reopened["prepare_job_id"] is None
        assert all(
            repo.get_player_action(str(action["id"]))["status"] == "rejected"
            for action in replacements
        )
    finally:
        connection.close()


def test_resumed_parallel_batch_queues_a_new_commit_generation(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "auto-kp-parallel-resume-generation.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=False)
        run = repo.get_active_campaign_module_run(str(fixture["run"]["campaign_id"]))
        assert run is not None
        ModuleRunService(repo).set_automation_level(
            str(run["id"]),
            command=AutomationLevelCommand(
                expected_version=int(run["version"]),
                level="ai_kp",
                reason="commit generation recovery test",
            ),
            member_id=fixture["kp"].member_id,
        )
        connection.commit()
        prepared = _prepare(repo, fixture, "parallel:auto-kp-resume-generation")
        assert prepared.batch is not None
        first_item, second_item = prepared.batch["items"]
        workflow = auto_kp_parallel_worker.ParallelActionWorkflowService(repo)
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
        assert ready.batch is not None and ready.status == "ready"
        first_job = AutoKpQueueService(repo).enqueue_parallel_followup(
            str(ready.batch["id"])
        )
        assert first_job is not None

        paused = _pause_parallel_batch(
            repo,
            ready.batch,
            fixture,
            reason="Injected recoverable commit interruption.",
        )
        recovered = workflow.resume_needs_attention(
            str(paused["id"]),
            expected_version=int(paused["version"]),
            identity=fixture["kp"],
        )
        assert recovered.batch is not None and recovered.status == "ready"
        second_job = AutoKpQueueService(repo).enqueue_parallel_followup(
            str(recovered.batch["id"])
        )

        assert second_job is not None
        assert second_job["id"] != first_job["id"]
        assert second_job["idempotency_key"] != first_job["idempotency_key"]
        assert second_job["payload"]["expected_version"] == recovered.batch["version"]
    finally:
        connection.close()


def test_full_ai_blind_parallel_job_digitally_resolves_observes_and_queues_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "auto-kp-parallel-blind.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=True)
        batch, blind_check = _full_ai_blind_parallel_batch(
            repo,
            fixture,
            key="parallel:auto-kp-blind",
        )
        queue = AutoKpQueueService(repo)
        job = queue.enqueue_parallel_blind_check_resolution(str(batch["id"]))
        replay = queue.enqueue_parallel_blind_check_resolution(str(batch["id"]))
        assert job is not None and replay is not None
        assert replay["id"] == job["id"]
        assert job["payload"] == {
            "phase": "resolve_blind_checks",
            "batch_id": batch["id"],
            "session_id": batch["session_id"],
            "run_version": batch["module_run_version"],
            "preparation_hash": batch["preparation_hash"],
            "action_ids": [item["action_id"] for item in batch["items"]],
            "check_ids": [blind_check["id"]],
        }
        for member in (fixture["first"], fixture["second"]):
            visible = repo.list_player_auto_kp_jobs(
                str(batch["campaign_id"]), member.member_id
            )
            assert job["id"] in {candidate["id"] for candidate in visible}
            projected = player_auto_kp_job(job)
            assert projected["phase"] == "progress"
            assert projected["stage"] == "parallel_progress"
            assert "resource_id" not in projected

        claimed = repo.claim_next_auto_kp_job(worker_id="blind-check-worker")
        assert claimed is not None and claimed["id"] == job["id"]
        connection.commit()
        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
                llm_model="unused-blind-check-model",
            ),
        )

        saved = repo.get_auto_kp_job(str(job["id"]))
        assert saved["status"] == "succeeded"
        assert saved["stage"] == "parallel_blind_checks"
        assert saved["result"]["resolved_check_ids"] == [blind_check["id"]]
        assert saved["result"]["replayed_check_ids"] == []
        resolved = repo.get_skill_check(str(blind_check["id"]))
        assert resolved["status"] == "resolved"
        assert resolved["input_method"] == "digital"
        assert resolved["random_evidence"]
        ready = repo.get_parallel_action_batch(str(batch["id"]))
        assert ready["status"] == "ready"
        commit_job_id = saved["result"]["commit_job_id"]
        assert commit_job_id
        commit_job = repo.get_auto_kp_job(str(commit_job_id))
        assert commit_job["payload"]["phase"] == "commit"

        commit_claim = repo.claim_next_auto_kp_job(worker_id="blind-commit-worker")
        assert commit_claim is not None and commit_claim["id"] == commit_job_id
        connection.commit()

        def unexpected_director(*_args, **_kwargs):
            raise AssertionError("blind resolution and commit must not call a model")

        monkeypatch.setattr(auto_kp_worker, "_director", unexpected_director)
        process_claimed_auto_kp_job(
            connection,
            commit_claim,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
                llm_model="unused",
            ),
        )
        assert repo.get_parallel_action_batch(str(batch["id"]))[
            "status"
        ] == "settled"
    finally:
        connection.close()


def test_resumed_blind_check_batch_queues_a_new_resolution_generation(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "parallel-blind-resume-generation.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=True)
        batch, _blind_check = _full_ai_blind_parallel_batch(
            repo,
            fixture,
            key="parallel:auto-kp-blind-resume-generation",
        )
        first_job = AutoKpQueueService(repo).enqueue_parallel_followup(
            str(batch["id"])
        )
        assert first_job is not None
        paused = _pause_parallel_batch(
            repo,
            batch,
            fixture,
            reason="Injected recoverable blind-check interruption.",
        )
        recovered = auto_kp_parallel_worker.ParallelActionWorkflowService(
            repo
        ).resume_needs_attention(
            str(paused["id"]),
            expected_version=int(paused["version"]),
            identity=fixture["kp"],
        )
        assert recovered.batch is not None
        assert recovered.status == "awaiting_checks"

        second_job = AutoKpQueueService(repo).enqueue_parallel_followup(
            str(recovered.batch["id"])
        )

        assert second_job is not None
        assert second_job["id"] != first_job["id"]
        assert second_job["idempotency_key"] != first_job["idempotency_key"]
        assert second_job["payload"]["check_ids"] == first_job["payload"]["check_ids"]
    finally:
        connection.close()


def test_blind_parallel_worker_replays_terminal_check_without_rerolling(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "auto-kp-parallel-blind-replay.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=True)
        batch, blind_check = _full_ai_blind_parallel_batch(
            repo,
            fixture,
            key="parallel:auto-kp-blind-replay",
        )
        system_kp = auto_kp_parallel_worker.kp_identity_for_parallel_actions(
            repo,
            tuple(str(item["action_id"]) for item in batch["items"]),
        )
        pre_resolved = auto_kp_parallel_worker.CheckService(repo).resolve(
            str(blind_check["id"]),
            system_kp,
            auto_kp_parallel_worker.ResolveCheckCommand(input_method="digital"),
        )
        selected_roll = pre_resolved["selected_roll"]
        random_evidence = pre_resolved["random_evidence"]
        job = AutoKpQueueService(repo).enqueue_parallel_blind_check_resolution(
            str(batch["id"])
        )
        assert job is not None
        claimed = repo.claim_next_auto_kp_job(worker_id="blind-replay-worker")
        assert claimed is not None
        connection.commit()

        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
            ),
        )

        saved = repo.get_auto_kp_job(str(job["id"]))
        assert saved["status"] == "succeeded"
        assert saved["result"]["resolved_check_ids"] == []
        assert saved["result"]["replayed_check_ids"] == [blind_check["id"]]
        unchanged = repo.get_skill_check(str(blind_check["id"]))
        assert unchanged["selected_roll"] == selected_roll
        assert unchanged["random_evidence"] == random_evidence
        assert repo.get_parallel_action_batch(str(batch["id"]))[
            "status"
        ] == "ready"
    finally:
        connection.close()


def test_blind_parallel_worker_checkpoints_roll_before_observation_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "auto-kp-parallel-blind-checkpoint.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=True)
        batch, blind_check = _full_ai_blind_parallel_batch(
            repo,
            fixture,
            key="parallel:auto-kp-blind-checkpoint",
        )
        job = AutoKpQueueService(repo).enqueue_parallel_blind_check_resolution(
            str(batch["id"])
        )
        assert job is not None
        claimed = repo.claim_next_auto_kp_job(worker_id="blind-checkpoint-worker")
        assert claimed is not None
        connection.commit()

        original_observe = (
            auto_kp_parallel_worker.ParallelActionWorkflowService.observe_terminal_check
        )

        def fail_after_roll(_service, _check_id: str):
            raise RuntimeError("fault after durable blind roll")

        monkeypatch.setattr(
            auto_kp_parallel_worker.ParallelActionWorkflowService,
            "observe_terminal_check",
            fail_after_roll,
        )
        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
            ),
        )

        failed = repo.get_auto_kp_job(str(job["id"]))
        assert failed["status"] == "retry_wait"
        checkpointed = repo.get_skill_check(str(blind_check["id"]))
        assert checkpointed["status"] == "resolved"
        assert checkpointed["random_evidence"]
        evidence = checkpointed["random_evidence"]
        selected_roll = checkpointed["selected_roll"]
        assert repo.get_parallel_action_batch(str(batch["id"]))[
            "status"
        ] == "awaiting_checks"

        monkeypatch.setattr(
            auto_kp_parallel_worker.ParallelActionWorkflowService,
            "observe_terminal_check",
            original_observe,
        )
        _make_auto_job_runnable(connection, str(job["id"]))
        retry = repo.claim_next_auto_kp_job(worker_id="blind-checkpoint-retry")
        assert retry is not None and retry["id"] == job["id"]
        connection.commit()
        process_claimed_auto_kp_job(
            connection,
            retry,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
            ),
        )

        succeeded = repo.get_auto_kp_job(str(job["id"]))
        assert succeeded["status"] == "succeeded"
        assert succeeded["result"]["resolved_check_ids"] == []
        assert succeeded["result"]["replayed_check_ids"] == [blind_check["id"]]
        unchanged = repo.get_skill_check(str(blind_check["id"]))
        assert unchanged["random_evidence"] == evidence
        assert unchanged["selected_roll"] == selected_roll
        assert repo.get_parallel_action_batch(str(batch["id"]))[
            "status"
        ] == "ready"
    finally:
        connection.close()


def test_blind_parallel_worker_rejects_tampered_exact_check_ids(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "auto-kp-parallel-blind-tamper.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=True)
        batch, blind_check = _full_ai_blind_parallel_batch(
            repo,
            fixture,
            key="parallel:auto-kp-blind-tamper",
        )
        job = AutoKpQueueService(repo).enqueue_parallel_blind_check_resolution(
            str(batch["id"])
        )
        assert job is not None
        tampered_payload = dict(job["payload"])
        tampered_payload["check_ids"] = ["check_outside_batch"]
        connection.execute(
            "UPDATE auto_kp_jobs SET payload_json = ? WHERE id = ?",
            (json.dumps(tampered_payload), job["id"]),
        )
        claimed = repo.claim_next_auto_kp_job(worker_id="blind-tamper-worker")
        assert claimed is not None
        connection.commit()

        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
            ),
        )

        saved = repo.get_auto_kp_job(str(job["id"]))
        assert saved["status"] == "retry_wait"
        assert "check IDs no longer match" in saved["last_error"]
        assert repo.get_skill_check(str(blind_check["id"]))[
            "status"
        ] == "requested"
        assert repo.get_parallel_action_batch(str(batch["id"]))[
            "status"
        ] == "awaiting_checks"
    finally:
        connection.close()


def test_parallel_prepare_job_uses_independent_authoritative_rulings(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "auto-kp-parallel-prepare.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=False)
        run = repo.get_active_campaign_module_run(str(fixture["run"]["campaign_id"]))
        assert run is not None
        ModuleRunService(repo).set_automation_level(
            str(run["id"]),
            command=AutomationLevelCommand(
                expected_version=int(run["version"]),
                level="balanced",
                reason="parallel prepare worker test",
            ),
            member_id=fixture["kp"].member_id,
        )
        action_ids = sorted(str(action["id"]) for action in fixture["actions"])
        job = repo.enqueue_auto_kp_job(
            campaign_id=str(fixture["run"]["campaign_id"]),
            run_id=str(run["id"]),
            job_type="parallel_actions",
            resource_id=action_ids[0],
            idempotency_key="parallel-legacy-prepare-job",
            # Jobs created before phased dispatch are treated as prepare jobs.
            payload={"action_ids": action_ids},
        )
        claimed = repo.claim_next_auto_kp_job(worker_id="test-worker")
        assert claimed is not None
        connection.commit()
        monkeypatch.setattr(
            auto_kp_worker,
            "_director",
            lambda *_args, **_kwargs: fixture["director"],
        )

        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
                llm_model="parallel-worker-test",
            ),
        )

        saved = repo.get_auto_kp_job(str(job["id"]))
        assert saved["status"] == "succeeded"
        assert saved["stage"] == "parallel_prepare"
        assert saved["result"]["status"] == "awaiting_confirmation"
        batch = saved["result"]["batch"]
        assert len(batch["items"]) == 2
        assert len({item["proposal_id"] for item in batch["items"]}) == 2
        assert len({item["adjudication_id"] for item in batch["items"]}) == 2
        assert all(
            repo.get_player_action(action_id)["status"] == "reviewed"
            for action_id in action_ids
        )
        assert repo.get_scenario_run_state(str(run["id"]))["state_version"] == 0
    finally:
        connection.close()


def test_worker_claims_and_prepares_the_complete_four_player_cohort(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "auto-kp-four-player-worker.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=False)
        run = repo.get_active_campaign_module_run(str(fixture["run"]["campaign_id"]))
        assert run is not None
        ModuleRunService(repo).set_automation_level(
            str(run["id"]),
            command=AutomationLevelCommand(
                expected_version=int(run["version"]),
                level="balanced",
                reason="four-player collection worker test",
            ),
            member_id=fixture["kp"].member_id,
        )
        extra_actions = []
        for index, (name, action_text) in enumerate(
            (("Cleo", "open the panel"), ("Dara", "observe quietly")),
            start=2,
        ):
            bundle = SessionService(repo).join(
                str(fixture["session"]["join_code"]), display_name=name
            )
            identity = repo.authenticate_access_token(str(bundle["access_token"]))
            assert identity is not None
            extra_actions.append(
                TurnService(repo).submit_player_action(
                    identity,
                    action_text=action_text,
                    client_action_id=f"four-player-worker-{index}",
                )
            )
        actions = [*fixture["actions"], *extra_actions]
        for action in actions:
            connection.execute(
                "UPDATE player_actions SET created_at = ? WHERE id = ?",
                ("2026-08-21 12:00:00", action["id"]),
            )

        queued = [
            AutoKpQueueService(repo).enqueue_player_action(str(action["id"]))
            for action in actions
        ]
        parent = queued[-1]
        assert parent["job_type"] == "parallel_actions"
        assert len(parent["payload"]["action_ids"]) == 4
        assert queued[1]["id"] == queued[2]["id"] == queued[3]["id"]
        _make_auto_job_runnable(connection, str(parent["id"]))
        claimed = repo.claim_next_auto_kp_job(worker_id="four-player-worker")
        assert claimed is not None and claimed["id"] == parent["id"]
        assert claimed["payload"]["action_ids"] == parent["payload"]["action_ids"]
        connection.commit()
        monkeypatch.setattr(
            auto_kp_worker,
            "_director",
            lambda *_args, **_kwargs: fixture["director"],
        )

        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
                llm_model="four-player-worker-test",
            ),
        )

        saved = repo.get_auto_kp_job(str(parent["id"]))
        assert saved["status"] == "succeeded"
        assert saved["stage"] == "parallel_prepare"
        assert len(saved["result"]["batch"]["items"]) == 4
        assert all(
            repo.get_player_action(str(action["id"]))["status"] == "reviewed"
            for action in actions
        )
    finally:
        connection.close()


@pytest.mark.parametrize("automation_level", ["balanced", "ai_kp"])
def test_nonmechanical_parallel_prepare_falls_back_to_visible_single_action_jobs(
    tmp_path: Path,
    monkeypatch,
    automation_level: str,
) -> None:
    db_path = tmp_path / f"parallel-fallback-{automation_level}.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=False)
        run = repo.get_active_campaign_module_run(str(fixture["run"]["campaign_id"]))
        assert run is not None
        ModuleRunService(repo).set_automation_level(
            str(run["id"]),
            command=AutomationLevelCommand(
                expected_version=int(run["version"]),
                level=automation_level,
                reason="parallel single-action fallback test",
            ),
            member_id=fixture["kp"].member_id,
        )
        question = "ask an out-of-character question"
        second_id = str(fixture["actions"][1]["id"])
        connection.execute(
            "UPDATE player_actions SET action_text = ? WHERE id = ?",
            (question, second_id),
        )
        AutoKpQueueService(repo).enqueue_player_action(
            str(fixture["actions"][0]["id"])
        )
        parent = AutoKpQueueService(repo).enqueue_player_action(second_id)
        assert parent["job_type"] == "parallel_actions"
        _make_auto_job_runnable(connection, str(parent["id"]))
        claimed = repo.claim_next_auto_kp_job(worker_id="fallback-parent")
        assert claimed is not None and claimed["id"] == parent["id"]
        connection.commit()
        mixed = MixedRouteDirector(
            connection,
            {
                "open the panel": ("open-panel", None),
                question: ("observe-quietly", None),
            },
        )
        monkeypatch.setattr(
            auto_kp_worker,
            "_director",
            lambda *_args, **_kwargs: mixed,
        )

        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
                llm_model="parallel-fallback-test",
            ),
        )

        saved_parent = repo.get_auto_kp_job(str(parent["id"]))
        assert saved_parent["status"] == "succeeded"
        assert saved_parent["stage"] == "parallel_fallback"
        assert saved_parent["result"]["workflow_status"] == "needs_attention"
        assert saved_parent["result"]["fallback_count"] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM parallel_action_batches"
        ).fetchone()[0] == 0
        children = [
            job
            for job in repo.list_auto_kp_jobs(
                str(fixture["run"]["campaign_id"]), limit=20
            )
            if (job.get("payload") or {}).get("fallback_kind")
            == "parallel_nonmechanical"
        ]
        assert len(children) == 2
        assert {child["resource_id"] for child in children} == {
            str(action["id"]) for action in fixture["actions"]
        }
        current_run = repo.get_active_campaign_module_run(
            str(fixture["run"]["campaign_id"])
        )
        assert current_run is not None
        for child in children:
            child_payload = child["payload"]
            assert child["job_type"] == "player_action"
            assert child["status"] == "queued"
            assert child["run_id"] == current_run["id"]
            assert child_payload["run_id"] == current_run["id"]
            assert child_payload["run_version"] == current_run["version"]
            assert child_payload["parent_job_id"] == parent["id"]
            assert child_payload["parent_idempotency_key"] == parent[
                "idempotency_key"
            ]
            assert child_payload["session_id"] == fixture["session"]["session"][
                "id"
            ]
            assert child_payload["action_id"] == child["resource_id"]

        child_by_action = {child["resource_id"]: child for child in children}
        for member, action in zip(
            (fixture["first"], fixture["second"]),
            fixture["actions"],
            strict=True,
        ):
            visible = repo.list_player_auto_kp_jobs(
                str(fixture["run"]["campaign_id"]), member.member_id
            )
            own_child = child_by_action[str(action["id"])]
            other_child_ids = {
                child["id"] for child in children if child["id"] != own_child["id"]
            }
            assert own_child["id"] in {job["id"] for job in visible}
            assert not other_child_ids.intersection(job["id"] for job in visible)
            projected = player_auto_kp_job(own_child)
            assert projected["resource_id"] == action["id"]

        monkeypatch.setattr(
            auto_kp_worker,
            "_director",
            lambda *_args, **_kwargs: FakeDirector(),
        )
        processed_children: list[dict] = []
        for _ in children:
            child_claim = repo.claim_next_auto_kp_job(worker_id="fallback-child")
            assert child_claim is not None
            connection.commit()
            process_claimed_auto_kp_job(
                connection,
                child_claim,
                settings=Settings(
                    db_path=db_path,
                    module_asset_root=tmp_path / "assets",
                    llm_model="single-action-fallback-test",
                ),
            )
            processed_children.append(repo.get_auto_kp_job(str(child_claim["id"])))
        assert all(child["status"] == "succeeded" for child in processed_children)
        assert all(
            child["result"]["status"] == "awaiting_confirmation"
            for child in processed_children
        )
        assert all(
            repo.get_player_action(str(action["id"]))["status"] == "reviewed"
            for action in fixture["actions"]
        )
        assert len(
            [
                job
                for job in repo.list_auto_kp_jobs(
                    str(fixture["run"]["campaign_id"]), limit=20
                )
                if job["job_type"] == "parallel_actions"
            ]
        ) == 1
    finally:
        connection.close()


def test_parallel_model_failure_falls_back_once_without_stranding_actions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "parallel-model-fallback.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=False)
        run = repo.get_active_campaign_module_run(str(fixture["run"]["campaign_id"]))
        assert run is not None
        ModuleRunService(repo).set_automation_level(
            str(run["id"]),
            command=AutomationLevelCommand(
                expected_version=int(run["version"]),
                level="ai_kp",
                reason="weak model fallback test",
            ),
            member_id=fixture["kp"].member_id,
        )
        second_id = str(fixture["actions"][1]["id"])
        AutoKpQueueService(repo).enqueue_player_action(
            str(fixture["actions"][0]["id"])
        )
        parent = AutoKpQueueService(repo).enqueue_player_action(second_id)
        assert parent["job_type"] == "parallel_actions"
        _make_auto_job_runnable(connection, str(parent["id"]))
        claimed = repo.claim_next_auto_kp_job(worker_id="model-fallback-parent")
        assert claimed is not None and claimed["id"] == parent["id"]
        connection.commit()
        monkeypatch.setattr(
            auto_kp_worker,
            "_director",
            lambda *_args, **_kwargs: UnavailableParallelDirector(),
        )

        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
                llm_model="weak-parallel-model",
            ),
        )

        saved_parent = repo.get_auto_kp_job(str(parent["id"]))
        assert saved_parent["status"] == "succeeded"
        assert saved_parent["attempt_count"] == 1
        assert saved_parent["stage"] == "parallel_fallback"
        assert saved_parent["result"]["fallback_count"] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM parallel_action_batches"
        ).fetchone()[0] == 0
        children = [
            job
            for job in repo.list_auto_kp_jobs(
                str(fixture["run"]["campaign_id"]), limit=20
            )
            if (job.get("payload") or {}).get("fallback_kind")
            == "parallel_nonmechanical"
        ]
        assert len(children) == 2
        assert all(child["status"] == "queued" for child in children)
        assert all(
            repo.get_player_action(str(action["id"]))["status"] == "submitted"
            for action in fixture["actions"]
        )

        processed_children = []
        for _ in children:
            claimed_child = repo.claim_next_auto_kp_job(
                worker_id="model-fallback-child"
            )
            assert claimed_child is not None
            connection.commit()
            process_claimed_auto_kp_job(
                connection,
                claimed_child,
                settings=Settings(
                    db_path=db_path,
                    module_asset_root=tmp_path / "assets",
                    llm_model="still-unavailable-weak-model",
                ),
            )
            processed_children.append(
                repo.get_auto_kp_job(str(claimed_child["id"]))
            )
        assert all(child["status"] == "succeeded" for child in processed_children)
        assert all(child["attempt_count"] == 1 for child in processed_children)
        assert all(
            child["result"]["status"] == "awaiting_confirmation"
            for child in processed_children
        )
        assert all(
            repo.get_player_action(str(action["id"]))["status"] == "reviewed"
            for action in fixture["actions"]
        )
    finally:
        connection.close()


def test_parallel_fallback_child_enqueue_is_idempotent_and_zero_delay(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "parallel-fallback-idempotent.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=False)
        run = repo.get_active_campaign_module_run(str(fixture["run"]["campaign_id"]))
        assert run is not None
        ModuleRunService(repo).set_automation_level(
            str(run["id"]),
            command=AutomationLevelCommand(
                expected_version=int(run["version"]),
                level="balanced",
                reason="fallback enqueue idempotency test",
            ),
            member_id=fixture["kp"].member_id,
        )
        second_id = str(fixture["actions"][1]["id"])
        AutoKpQueueService(repo).enqueue_player_action(
            str(fixture["actions"][0]["id"])
        )
        parent = AutoKpQueueService(repo).enqueue_player_action(second_id)
        _make_auto_job_runnable(connection, str(parent["id"]))
        claimed = repo.claim_next_auto_kp_job(worker_id="fallback-idempotency")
        assert claimed is not None and claimed["id"] == parent["id"]
        connection.commit()
        routes = {second_id: "clarification"}

        first = AutoKpQueueService(repo).enqueue_parallel_action_fallbacks(
            claimed,
            fallback_routes=routes,
        )
        replay = AutoKpQueueService(repo).enqueue_parallel_action_fallbacks(
            claimed,
            fallback_routes=routes,
        )

        assert [job["id"] for job in replay] == [job["id"] for job in first]
        assert len(first) == 2
        assert all(job["status"] == "queued" for job in first)
        # delay_seconds=0 makes the first child claimable in this same transaction.
        immediate = repo.claim_next_auto_kp_job(worker_id="fallback-immediate")
        assert immediate is not None
        assert immediate["id"] in {job["id"] for job in first}
    finally:
        connection.close()


def test_conservative_parallel_attention_does_not_enqueue_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "parallel-fallback-conservative.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=False)
        second_id = str(fixture["actions"][1]["id"])
        AutoKpQueueService(repo).enqueue_player_action(
            str(fixture["actions"][0]["id"])
        )
        parent = AutoKpQueueService(repo).enqueue_player_action(second_id)
        _make_auto_job_runnable(connection, str(parent["id"]))
        claimed = repo.claim_next_auto_kp_job(worker_id="conservative-parent")
        assert claimed is not None
        connection.commit()

        def unexpected_director(*_args, **_kwargs):
            raise AssertionError("conservative fallback must not call the model")

        monkeypatch.setattr(auto_kp_worker, "_director", unexpected_director)
        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
            ),
        )

        saved = repo.get_auto_kp_job(str(parent["id"]))
        assert saved["status"] == "needs_attention"
        assert saved["stage"] == "parallel_prepare"
        assert all(
            (job.get("payload") or {}).get("fallback_kind") is None
            for job in repo.list_auto_kp_jobs(
                str(fixture["run"]["campaign_id"]), limit=20
            )
        )
    finally:
        connection.close()


def test_existing_stale_parallel_batch_attention_never_falls_back(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "parallel-fallback-stale-batch.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        fixture = _workflow_fixture(repo, with_skill_character=False)
        run = repo.get_active_campaign_module_run(str(fixture["run"]["campaign_id"]))
        assert run is not None
        ModuleRunService(repo).set_automation_level(
            str(run["id"]),
            command=AutomationLevelCommand(
                expected_version=int(run["version"]),
                level="balanced",
                reason="stale batch must fail closed",
            ),
            member_id=fixture["kp"].member_id,
        )
        connection.commit()
        parent_key = "parallel-existing-stale-parent"
        prepared = asyncio.run(
            auto_kp_parallel_worker.ParallelActionWorkflowService(repo).prepare(
                fixture["actions"],
                fixture["kp"],
                fixture["director"],
                idempotency_key=f"workflow:{parent_key}",
                source_model="stale-existing-batch-test",
            )
        )
        assert prepared.batch is not None
        connection.execute(
            "UPDATE campaign_module_runs SET version = version + 1 WHERE id = ?",
            (fixture["run"]["id"],),
        )
        current_run = repo.get_active_campaign_module_run(
            str(fixture["run"]["campaign_id"])
        )
        assert current_run is not None
        action_ids = [str(action["id"]) for action in fixture["actions"]]
        parent = repo.enqueue_auto_kp_job(
            campaign_id=str(fixture["run"]["campaign_id"]),
            run_id=str(current_run["id"]),
            job_type="parallel_actions",
            resource_id=action_ids[0],
            idempotency_key=parent_key,
            payload={
                "phase": "prepare",
                "action_ids": action_ids,
                "session_id": fixture["session"]["session"]["id"],
                "run_version": int(current_run["version"]),
            },
        )
        claimed = repo.claim_next_auto_kp_job(worker_id="stale-batch-parent")
        assert claimed is not None
        connection.commit()
        monkeypatch.setattr(
            auto_kp_worker,
            "_director",
            lambda *_args, **_kwargs: fixture["director"],
        )

        process_claimed_auto_kp_job(
            connection,
            claimed,
            settings=Settings(
                db_path=db_path,
                module_asset_root=tmp_path / "assets",
                llm_model="stale-existing-batch-test",
            ),
        )

        saved = repo.get_auto_kp_job(str(parent["id"]))
        assert saved["status"] == "needs_attention"
        assert saved["result"]["batch"]["id"] == prepared.batch["id"]
        assert all(
            (job.get("payload") or {}).get("fallback_kind") is None
            for job in repo.list_auto_kp_jobs(
                str(fixture["run"]["campaign_id"]), limit=20
            )
        )
    finally:
        connection.close()

import json
from pathlib import Path
from types import SimpleNamespace

from ai_kp.application.auto_kp_queue_service import AutoKpQueueService
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
from ai_kp.infrastructure import auto_kp_worker
from ai_kp.infrastructure.auto_kp_worker import process_claimed_auto_kp_job
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.modules.ingestion import ModuleChunk


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

        second_action = turns.submit_player_action(
            second_identity,
            action_text="我检查后门。",
            client_action_id="parallel-queue-action-002",
        )
        batch_job = AutoKpQueueService(repo).enqueue_player_action(second_action["id"])

        assert batch_job["job_type"] == "parallel_actions"
        assert batch_job["payload"]["action_ids"] == sorted(
            [first_action["id"], second_action["id"]]
        )
        assert repo.get_auto_kp_job(first_job["id"])["status"] == "cancelled"
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


def test_auto_kp_worker_processes_claimed_player_action_job(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "auto-kp-worker.sqlite3"
    connection = connect(db_path)
    init_db(connection)
    repo = Repository(connection)
    try:
        monkeypatch.setattr(
            auto_kp_worker,
            "_director",
            lambda *_args, **_kwargs: FakeDirector(),
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
        assert saved["status"] == "succeeded"
        assert saved["result"]["player_action"]["status"] == "resolved"
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

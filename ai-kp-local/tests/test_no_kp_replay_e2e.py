import asyncio
from pathlib import Path
from types import SimpleNamespace

from ai_kp.application.auto_kp_queue_service import AutoKpQueueService
from ai_kp.application.auto_world_expansion_service import AutoWorldExpansionService
from ai_kp.application.module_run_service import (
    AutomationLevelCommand,
    ModuleRunService,
)
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import TurnService, WorldExpansionCommand
from ai_kp.director.context_builder import ContextAssembly
from ai_kp.director.world_expansion import WorldExpansionOutput
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.modules.ingestion import ModuleChunk


class EnvironmentGapDirector:
    async def handle_world_expansion(self, **values):
        return SimpleNamespace(
            output=WorldExpansionOutput.model_validate(
                {
                    "public_narration": "镇中心可以找到一间兼作警务办公室的小屋。",
                    "kp_notes": "低副作用环境补全。",
                    "candidate": {
                        "expansion_kind": "environment",
                        "subject": "小镇警务办公室",
                        "proposal": "小镇有一间由治安官和兼职书记员共用的警务办公室。",
                        "rationale": "符合当前时代、地点和玩家调查意图。",
                        "confidence": "medium",
                        "assumptions": [],
                        "conflicts": [],
                        "alternatives": [
                            {
                                "title": "邻镇负责",
                                "description": "本镇没有常驻警务办公室。",
                                "tradeoff": "需要旅行获取档案。",
                            },
                            {
                                "title": "车站驻点",
                                "description": "只有临时巡警驻点。",
                                "tradeoff": "档案更少。",
                            },
                        ],
                    },
                }
            ),
            context=ContextAssembly(
                messages=[{"role": "system", "content": "world gap"}],
                included_sources=[],
                excluded_sources=[],
                token_estimate=10,
                visibility_scope="kp",
            ),
        )


def test_no_kp_replay_e2e_covers_parallel_world_expansion_and_jobs(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "no-kp-replay.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        campaign = repo.create_campaign("No KP replay", current_time="1928-10-03")
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
                    text="钟楼门前有碎玻璃，门内传来微弱钟摆声。",
                    visibility="kp",
                    spoiler_tag="act-1",
                    scene_key="town",
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
        run = ModuleRunService(repo).set_automation_level(
            run["id"],
            AutomationLevelCommand(
                expected_version=run["version"],
                level="ai_kp",
                reason="真人团 no-KP replay 使用 AI KP 强度。",
            ),
            member_id=kp_identity.member_id,
        )

        first = sessions.join(kp_bundle["join_code"], display_name="Ada")
        second = sessions.join(kp_bundle["join_code"], display_name="Bert")
        first_identity = repo.authenticate_access_token(first["access_token"])
        second_identity = repo.authenticate_access_token(second["access_token"])
        assert first_identity is not None
        assert second_identity is not None
        turns = TurnService(repo)
        first_action = turns.submit_player_action(
            first_identity,
            action_text="我守住巷口。",
            client_action_id="replay-action-0001",
        )
        first_job = AutoKpQueueService(repo).enqueue_player_action(
            first_action["id"]
        )
        second_action = turns.submit_player_action(
            second_identity,
            action_text="我检查后门。",
            client_action_id="replay-action-0002",
        )
        parallel_job = AutoKpQueueService(repo).enqueue_player_action(
            second_action["id"]
        )
        assert repo.get_auto_kp_job(first_job["id"])["status"] == "cancelled"
        assert parallel_job["job_type"] == "parallel_actions"
        assert parallel_job["payload"]["phase"] == "prepare"
        assert [
            repo.get_player_action(first_action["id"])["status"],
            repo.get_player_action(second_action["id"])["status"],
        ] == ["submitted", "submitted"]

        world = asyncio.run(
            AutoWorldExpansionService(repo).create_and_maybe_materialize(
                WorldExpansionCommand(
                    run_id=run["id"],
                    player_intent="我去寻找镇上的警务办公室",
                ),
                kp_identity,
                EnvironmentGapDirector(),
                source_model="replay-fake",
            )
        )
        assert world.status == "materialized"
        assert world.materialization is not None
        assert [
            (item.fact.subject, item.fact.predicate)
            for item in repo.list_fact_heads(campaign["id"])
        ] == [("小镇警务办公室", "存在或成立")]

        connection.execute(
            "UPDATE auto_kp_jobs SET next_run_at = CURRENT_TIMESTAMP WHERE id = ?",
            (parallel_job["id"],),
        )
        claimed = repo.claim_next_auto_kp_job(worker_id="replay-worker")
        assert claimed is not None
        assert claimed["id"] == parallel_job["id"]
        completed = repo.complete_auto_kp_job(
            claimed["id"],
            expected_attempt=claimed["attempt_count"],
            result={"phase": "prepare", "action_count": 2},
        )
        assert completed["status"] == "succeeded"
        assert repo.get_auto_kp_job(parallel_job["id"])["result"] == {
            "phase": "prepare",
            "action_count": 2,
        }
    finally:
        connection.close()

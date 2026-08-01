from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.application.errors import ConflictError
from ai_kp.application.module_graph_service import ModuleGraphService
from ai_kp.application.module_knowledge_service import ModuleKnowledgeService
from ai_kp.application.module_run_service import (
    AutomationLevelCommand,
    DirectorControlCommand,
    ModuleRunService,
)
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.modules.graph import ModuleEntityCreate, ModuleRelationCreate
from ai_kp.platform.modules.ingestion import ModuleChunk


def _module(repo: Repository, campaign_id: str, title: str) -> dict:
    return repo.create_module(
        campaign_id,
        title,
        [
            ModuleChunk(
                title="第一幕",
                text=f"{title}的第一幕",
                visibility="kp",
                spoiler_tag="act-1",
                scene_key="scene-1",
                order_index=0,
            )
        ],
    )


def test_campaign_has_one_explicit_active_module_run(tmp_path: Path) -> None:
    connection = connect(tmp_path / "module-runs.sqlite3")
    try:
        init_db(connection)
        repo = Repository(connection)
        campaign = repo.create_campaign("连续模组")
        first_module = _module(repo, campaign["id"], "模组一")
        second_module = _module(repo, campaign["id"], "模组二")

        first_run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=first_module["id"],
            current_scene_key="scene-1",
            active_spoiler_tags=["act-1", "act-1"],
            state={"clock": 1},
            started_by_member_id=None,
        )
        assert first_run["status"] == "active"
        assert first_run["active_spoiler_tags"] == ["act-1"]
        assert first_run["state"] == {"clock": 1}

        same_run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=first_module["id"],
            current_scene_key="scene-2",
            active_spoiler_tags=["act-1", "act-2"],
            state={"clock": 2},
            started_by_member_id=None,
        )
        assert same_run["id"] == first_run["id"]
        assert same_run["current_scene_key"] == "scene-1"
        assert same_run["active_spoiler_tags"] == ["act-1"]
        assert same_run["state"] == {"clock": 1}

        updated = repo.update_campaign_module_run(
            first_run["id"],
            {
                "expected_version": first_run["version"],
                "current_scene_key": "scene-2",
                "active_spoiler_tags": ["act-1", "act-2"],
                "state": {"clock": 2},
            },
        )
        assert updated["current_scene_key"] == "scene-2"
        assert updated["state"] == {"clock": 2}

        second_run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=second_module["id"],
            current_scene_key=None,
            active_spoiler_tags=[],
            state={},
            started_by_member_id=None,
        )
        assert second_run["status"] == "active"
        assert repo.get_campaign_module_run(first_run["id"])["status"] == "paused"
        assert repo.get_active_campaign_module_run(campaign["id"])["id"] == second_run["id"]
        with pytest.raises(ValueError, match="refresh"):
            repo.update_campaign_module_run(
                first_run["id"],
                {
                    "expected_version": updated["version"],
                    "status": "active",
                },
            )

        completed = repo.update_campaign_module_run(
            second_run["id"],
            {
                "expected_version": second_run["version"],
                "status": "completed",
            },
        )
        assert completed["status"] == "completed"
        assert completed["completed_at"] is not None
        assert repo.get_active_campaign_module_run(campaign["id"]) is None
    finally:
        connection.close()


def test_module_run_rejects_cross_campaign_module(tmp_path: Path) -> None:
    connection = connect(tmp_path / "module-run-isolation.sqlite3")
    try:
        init_db(connection)
        repo = Repository(connection)
        first_campaign = repo.create_campaign("团一")
        second_campaign = repo.create_campaign("团二")
        module = _module(repo, second_campaign["id"], "团二模组")

        with pytest.raises(ValueError, match="does not belong"):
            repo.start_campaign_module_run(
                campaign_id=first_campaign["id"],
                module_id=module["id"],
                current_scene_key=None,
                active_spoiler_tags=[],
                state={},
                started_by_member_id=None,
            )
    finally:
        connection.close()


def test_director_control_handoff_is_audited_and_blocks_ai_analysis(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "director-control.sqlite3")
    try:
        init_db(connection)
        repo = Repository(connection)
        campaign = repo.create_campaign("人工接管")
        kp = repo.create_campaign_session(
            campaign["id"], kp_display_name="人工 KP"
        )["member"]
        module = _module(repo, campaign["id"], "接管模组")
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key="scene-1",
            active_spoiler_tags=["act-1"],
            state={},
            started_by_member_id=kp["id"],
        )
        service = ModuleRunService(repo)

        paused = service.set_control(
            run["id"],
            DirectorControlCommand(
                expected_version=run["version"],
                mode="safety_paused",
                reason="模型输出触及未揭示真相，等待人工复核",
            ),
            member_id=kp["id"],
        )

        assert paused["director_control_mode"] == "safety_paused"
        assert paused["version"] == run["version"] + 1
        with pytest.raises(ConflictError, match="human KP control"):
            service.analyze(run["id"], "继续调查")
        with pytest.raises(ConflictError, match="refresh"):
            service.set_control(
                run["id"],
                DirectorControlCommand(
                    expected_version=run["version"],
                    mode="human_kp",
                    reason="过期页面试图接管",
                ),
                member_id=kp["id"],
            )

        resumed = service.set_control(
            run["id"],
            DirectorControlCommand(
                expected_version=paused["version"],
                mode="ai_assist",
                reason="人工确认上下文安全，交还 AI",
            ),
            member_id=kp["id"],
        )
        assert resumed["director_control_mode"] == "ai_assist"
        events = repo.list_module_run_control_events(run["id"])
        assert [
            (item["from_mode"], item["to_mode"]) for item in events
        ] == [
            ("ai_assist", "safety_paused"),
            ("safety_paused", "ai_assist"),
        ]
    finally:
        connection.close()


def test_automation_level_changes_are_versioned_and_audited(tmp_path: Path) -> None:
    connection = connect(tmp_path / "automation-level.sqlite3")
    try:
        init_db(connection)
        repo = Repository(connection)
        campaign = repo.create_campaign("自动化强度")
        kp = repo.create_campaign_session(
            campaign["id"], kp_display_name="人工 KP"
        )["member"]
        module = _module(repo, campaign["id"], "自动化模组")
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key="scene-1",
            active_spoiler_tags=[],
            state={},
            started_by_member_id=kp["id"],
        )
        service = ModuleRunService(repo)

        balanced = service.set_automation_level(
            run["id"],
            AutomationLevelCommand(
                expected_version=run["version"],
                level="balanced",
                reason="普通行动可交给 AI 自动审批",
            ),
            member_id=kp["id"],
        )

        assert balanced["automation_level"] == "balanced"
        assert balanced["version"] == run["version"] + 1
        with pytest.raises(ConflictError, match="refresh"):
            service.set_automation_level(
                run["id"],
                AutomationLevelCommand(
                    expected_version=run["version"],
                    level="ai_kp",
                    reason="过期页面试图切换强度",
                ),
                member_id=kp["id"],
            )
        ai_kp = service.set_automation_level(
            run["id"],
            AutomationLevelCommand(
                expected_version=balanced["version"],
                level="ai_kp",
                reason="进入完整 AI KP 模式",
            ),
            member_id=kp["id"],
        )
        assert ai_kp["automation_level"] == "ai_kp"
        assert [
            (item["from_level"], item["to_level"])
            for item in repo.list_module_run_automation_events(run["id"])
        ] == [("conservative", "balanced"), ("balanced", "ai_kp")]
    finally:
        connection.close()


def test_scene_director_tracks_scene_clues_and_read_only_gap_analysis(
    tmp_path: Path,
) -> None:
    connection = connect(tmp_path / "scene-director.sqlite3")
    try:
        init_db(connection)
        repo = Repository(connection)
        campaign = repo.create_campaign("导演测试")
        module = repo.create_module(
            campaign["id"],
            "雾港疑云",
            [
                ModuleChunk(
                    title="仓库",
                    text="仓库照片指向灯塔航海日志。",
                    visibility="kp",
                    spoiler_tag="act-1",
                    scene_key="warehouse",
                    order_index=0,
                ),
                ModuleChunk(
                    title="终幕",
                    text="地下祭坛藏在灯塔之下。",
                    visibility="secret",
                    spoiler_tag="ending",
                    scene_key="ending",
                    order_index=1,
                ),
            ],
        )
        chunks = repo.list_module_chunks(
            module["id"],
            allowed_visibility=("kp", "secret"),
            spoiler_tags=None,
        )
        warehouse_chunk = next(item for item in chunks if item["scene_key"] == "warehouse")
        candidate = ModuleKnowledgeService(repo).create_manual_candidate(
            module["id"],
            {
                "kind": "module_anchor",
                "title": "仓库调查",
                "statement": "仓库、仓库照片与航海日志形成调查路径。",
                "visibility": "kp",
                "spoiler_tag": "act-1",
                "citations": [
                    {
                        "chunk_id": warehouse_chunk["id"],
                        "evidence_text": "仓库照片指向灯塔航海日志。",
                    }
                ],
            },
        )
        repo.review_module_knowledge_candidate(
            candidate["id"],
            decision="approved",
            member_id=None,
            note=None,
        )
        graph = ModuleGraphService(repo)

        def entity(entity_type: str, name: str) -> dict:
            return graph.create_entity(
                module["id"],
                ModuleEntityCreate(
                    entity_type=entity_type,
                    name=name,
                    visibility="kp",
                    spoiler_tag="act-1",
                    source_candidate_id=candidate["id"],
                ),
                member_id=None,
            )

        warehouse = entity("location", "仓库")
        photo = entity("clue", "仓库照片")
        log = entity("anchor", "航海日志")
        for source, predicate, target in (
            (warehouse, "leads_to", photo),
            (photo, "reveals", log),
        ):
            graph.create_relation(
                module["id"],
                ModuleRelationCreate(
                    source_entity_id=source["id"],
                    predicate=predicate,
                    target_entity_id=target["id"],
                    visibility="kp",
                    spoiler_tag="act-1",
                    source_candidate_id=candidate["id"],
                ),
                member_id=None,
            )
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key=None,
            active_spoiler_tags=["act-1"],
            state={},
            started_by_member_id=None,
        )
        transitioned = repo.transition_module_run_scene(
            run["id"],
            expected_version=run["version"],
            scene_key="warehouse",
            scene_title="旧仓库",
            play_pace="freeform",
            location_entity_id=warehouse["id"],
            world_time="1928-10-03 21:00",
            note="调查员抵达仓库。",
            member_id=None,
        )
        assert transitioned["run"]["current_scene_title"] == "旧仓库"
        assert transitioned["run"]["current_location_entity_id"] == warehouse["id"]
        assert transitioned["event"]["from_scene_key"] is None
        with pytest.raises(ValueError, match="Module run changed"):
            repo.transition_module_run_scene(
                run["id"],
                expected_version=run["version"],
                scene_key="stale-scene",
                scene_title="过期场景",
                play_pace="freeform",
                location_entity_id=None,
                world_time=None,
                note="",
                member_id=None,
            )
        assert len(repo.list_module_run_scene_events(run["id"])) == 1
        available = repo.set_module_run_entity_state(
            run["id"],
            photo["id"],
            expected_version=transitioned["run"]["version"],
            status="available",
            note="进入仓库后可以调查照片。",
            member_id=None,
        )
        discovered = repo.set_module_run_entity_state(
            run["id"],
            photo["id"],
            expected_version=available["run"]["version"],
            status="discovered",
            note="调查员检查了照片。",
            member_id=None,
        )
        assert discovered["entity_state"]["status"] == "discovered"
        assert [
            item["to_status"]
            for item in repo.list_module_run_entity_state_events(run["id"])
        ] == ["available", "discovered"]

        service = ModuleRunService(repo)
        before_version = discovered["run"]["version"]
        canon = service.analyze(run["id"], "我检查仓库照片")
        spoiler = service.analyze(run["id"], "我寻找地下祭坛")
        gap = service.analyze(run["id"], "我去找镇上的警察局")
        assert canon["decision"] == "answer_from_canon"
        assert canon["sources"]
        assert spoiler["decision"] == "blocked_by_spoiler"
        assert spoiler["sources"] == []
        assert spoiler["deferred_source_count"] > 0
        assert gap["decision"] == "world_gap"
        assert gap["recommended_action"] == "propose_world_expansion"
        assert all(
            result["writes_performed"] is False
            for result in (canon, spoiler, gap)
        )
        assert repo.get_campaign_module_run(run["id"])["version"] == before_version
        assert canon["reachability"]["evaluated"] is True
    finally:
        connection.close()


def test_auto_kp_jobs_are_durable_claimable_and_retryable(tmp_path: Path) -> None:
    connection = connect(tmp_path / "auto-kp-jobs.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    try:
        campaign = repo.create_campaign("Auto KP jobs")
        job = repo.enqueue_auto_kp_job(
            campaign_id=campaign["id"],
            job_type="player_action",
            resource_id="action_1",
            idempotency_key="auto-action-0001",
            payload={"action_id": "action_1"},
        )
        repeated = repo.enqueue_auto_kp_job(
            campaign_id=campaign["id"],
            job_type="player_action",
            resource_id="action_1",
            idempotency_key="auto-action-0001",
        )
        assert repeated["id"] == job["id"]
        claimed = repo.claim_next_auto_kp_job(worker_id="worker-1")
        assert claimed is not None
        assert claimed["status"] == "running"
        assert claimed["attempt_count"] == 1
        waiting = repo.fail_auto_kp_job(
            claimed["id"],
            expected_attempt=claimed["attempt_count"],
            error="model timeout",
        )
        assert waiting["status"] == "retry_wait"
        retried = repo.retry_auto_kp_job(waiting["id"])
        assert retried["status"] == "queued"
        assert retried["attempt_count"] == 0
        claimed_again = repo.claim_next_auto_kp_job(worker_id="worker-2")
        assert claimed_again is not None
        done = repo.complete_auto_kp_job(
            claimed_again["id"],
            expected_attempt=claimed_again["attempt_count"],
            result={"status": "completed"},
        )
        assert done["status"] == "succeeded"
        assert done["result"] == {"status": "completed"}

        exhausted = repo.enqueue_auto_kp_job(
            campaign_id=campaign["id"],
            job_type="player_action",
            resource_id="action_2",
            idempotency_key="auto-action-exhausted-0001",
            max_attempts=1,
        )
        exhausted_claim = repo.claim_next_auto_kp_job(worker_id="worker-crashed")
        assert exhausted_claim is not None
        connection.execute(
            "UPDATE auto_kp_jobs SET locked_at = datetime(CURRENT_TIMESTAMP, '-11 minutes') WHERE id = ?",
            (exhausted["id"],),
        )
        assert repo.recover_stale_auto_kp_jobs() == 1
        recovered_exhausted = repo.get_auto_kp_job(exhausted["id"])
        assert recovered_exhausted["status"] == "failed"
        assert recovered_exhausted["stage"] == "failed"
    finally:
        connection.close()


def test_module_run_api_is_kp_only_and_rejects_empty_or_null_updates(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_path=tmp_path / "module-run-api.sqlite3",
        module_asset_root=tmp_path / "module-assets",
        local_admin_enabled=False,
        admin_token="module-run-admin",
    )
    with TestClient(create_app(settings)) as client:
        campaign = client.post(
            "/campaigns",
            headers={"X-AI-KP-Admin-Token": "module-run-admin"},
            json={"title": "模组运行权限"},
        ).json()
        session = client.post(
            f"/campaigns/{campaign['id']}/sessions",
            headers={"X-AI-KP-Admin-Token": "module-run-admin"},
            json={"kp_display_name": "OldOnes"},
        ).json()
        kp_headers = {"Authorization": f"Bearer {session['access_token']}"}
        player = client.post(
            "/sessions/join",
            json={"join_code": session["join_code"], "display_name": "调查员"},
        ).json()
        player_headers = {"Authorization": f"Bearer {player['access_token']}"}
        module = client.post(
            f"/campaigns/{campaign['id']}/modules",
            headers=kp_headers,
            json={
                "title": "雾港疑云",
                "text": "@visibility=kp @spoiler=act-1\n仓库里有一张照片。",
            },
        ).json()

        assert client.get(
            f"/campaigns/{campaign['id']}/module-runs",
            headers=player_headers,
        ).status_code == 403
        assert client.post(
            f"/campaigns/{campaign['id']}/module-runs",
            headers=player_headers,
            json={"module_id": module["id"]},
        ).status_code == 403

        started = client.post(
            f"/campaigns/{campaign['id']}/module-runs",
            headers=kp_headers,
            json={
                "module_id": module["id"],
                "current_scene_key": "仓库",
                "active_spoiler_tags": ["act-1"],
            },
        )
        assert started.status_code == 200
        run = started.json()
        assert run["module_id"] == module["id"]
        assert client.get(
            f"/campaigns/{campaign['id']}/module-runs/current",
            headers=kp_headers,
        ).json()["id"] == run["id"]
        assert client.get(
            f"/module-runs/{run['id']}/director-state",
            headers=player_headers,
        ).status_code == 403
        assert client.post(
            f"/module-runs/{run['id']}/scene-transitions",
            headers=player_headers,
            json={
                "expected_version": run["version"],
                "scene_key": "warehouse",
                "scene_title": "旧仓库",
                "play_pace": "freeform",
            },
        ).status_code == 403
        assert client.post(
            f"/module-runs/{run['id']}/director/analyze",
            headers=player_headers,
            json={"player_intent": "检查仓库"},
        ).status_code == 403
        assert client.patch(
            f"/module-runs/{run['id']}/entities/not-an-entity/state",
            headers=player_headers,
            json={
                "expected_version": run["version"],
                "status": "discovered",
            },
        ).status_code == 403
        scene_transition = client.post(
            f"/module-runs/{run['id']}/scene-transitions",
            headers=kp_headers,
            json={
                "expected_version": run["version"],
                "scene_key": "warehouse",
                "scene_title": "旧仓库",
                "play_pace": "freeform",
                "world_time": "1928-10-03 21:00",
                "note": "调查开始",
            },
        )
        assert scene_transition.status_code == 200
        run = scene_transition.json()["run"]
        assert run["current_scene_title"] == "旧仓库"
        director_state = client.get(
            f"/module-runs/{run['id']}/director-state",
            headers=kp_headers,
        )
        assert director_state.status_code == 200
        assert director_state.json()["scene_events"][0]["to_scene_key"] == "warehouse"
        analysis = client.post(
            f"/module-runs/{run['id']}/director/analyze",
            headers=kp_headers,
            json={"player_intent": "我检查仓库里的照片"},
        )
        assert analysis.status_code == 200
        assert analysis.json()["writes_performed"] is False
        assert analysis.json()["decision"] == "answer_from_canon"
        assert client.patch(
            f"/module-runs/{run['id']}",
            headers=player_headers,
            json={"expected_version": run["version"], "status": "completed"},
        ).status_code == 403
        assert client.patch(
            f"/module-runs/{run['id']}",
            headers=kp_headers,
            json={"expected_version": run["version"]},
        ).status_code == 422
        assert client.patch(
            f"/module-runs/{run['id']}",
            headers=kp_headers,
            json={
                "expected_version": run["version"],
                "active_spoiler_tags": None,
            },
        ).status_code == 422
        completed = client.patch(
            f"/module-runs/{run['id']}",
            headers=kp_headers,
            json={
                "expected_version": run["version"],
                "status": "completed",
            },
        )
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"
        completed_at = completed.json()["completed_at"]
        stale = client.patch(
            f"/module-runs/{run['id']}",
            headers=kp_headers,
            json={
                "expected_version": run["version"],
                "status": "paused",
            },
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "conflict"
        oversized = client.patch(
            f"/module-runs/{run['id']}",
            headers=kp_headers,
            json={
                "expected_version": completed.json()["version"],
                "state": {
                    "one": "字" * 1500,
                    "two": "字" * 1500,
                    "three": "字" * 1500,
                },
            },
        )
        assert oversized.status_code == 422
        assert oversized.json()["code"] == "invalid_input"
        repeated = client.patch(
            f"/module-runs/{run['id']}",
            headers=kp_headers,
            json={
                "expected_version": completed.json()["version"],
                "status": "completed",
            },
        )
        assert repeated.status_code == 200
        assert repeated.json()["completed_at"] == completed_at


def test_module_run_state_has_a_durable_size_limit(tmp_path: Path) -> None:
    connection = connect(tmp_path / "module-run-state-limit.sqlite3")
    try:
        init_db(connection)
        repo = Repository(connection)
        campaign = repo.create_campaign("状态边界")
        module = _module(repo, campaign["id"], "状态边界模组")
        with pytest.raises(ValueError, match="4 KiB"):
            repo.start_campaign_module_run(
                campaign_id=campaign["id"],
                module_id=module["id"],
                current_scene_key=None,
                active_spoiler_tags=[],
                state={
                    "one": "字" * 1500,
                    "two": "字" * 1500,
                    "three": "字" * 1500,
                },
                started_by_member_id=None,
            )
    finally:
        connection.close()


def test_concurrent_start_waits_and_returns_the_same_active_run(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "module-run-concurrency.sqlite3"
    setup_connection = connect(database_path)
    try:
        init_db(setup_connection)
        setup_repo = Repository(setup_connection)
        campaign = setup_repo.create_campaign("并发开始")
        module = _module(setup_repo, campaign["id"], "同一本模组")
        setup_connection.commit()
    finally:
        setup_connection.close()

    first_connection = connect(database_path)
    second_connection = connect(database_path)
    first_written = Event()
    allow_first_commit = Event()
    second_begin_attempted = Event()
    second_connection.set_trace_callback(
        lambda sql: (
            second_begin_attempted.set()
            if sql.strip().upper().startswith("BEGIN IMMEDIATE")
            else None
        )
    )

    def start_first() -> dict:
        repo = Repository(first_connection)
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key="scene-1",
            active_spoiler_tags=["act-1"],
            state={"clock": 1},
            started_by_member_id=None,
        )
        first_written.set()
        assert allow_first_commit.wait(timeout=5)
        first_connection.commit()
        return run

    def start_second() -> dict:
        repo = Repository(second_connection)
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key=None,
            active_spoiler_tags=[],
            state={},
            started_by_member_id=None,
        )
        second_connection.commit()
        return run

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(start_first)
            assert first_written.wait(timeout=5)
            second_future = pool.submit(start_second)
            assert second_begin_attempted.wait(timeout=5)
            assert not second_future.done()
            allow_first_commit.set()
            first_run = first_future.result(timeout=5)
            second_run = second_future.result(timeout=5)
        assert second_run["id"] == first_run["id"]
        assert second_run["state"] == {"clock": 1}
    finally:
        first_connection.close()
        second_connection.close()


def test_stale_module_run_patch_cannot_overwrite_newer_state(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "module-run-stale-patch.sqlite3"
    first_connection = connect(database_path)
    try:
        init_db(first_connection)
        first_repo = Repository(first_connection)
        campaign = first_repo.create_campaign("并发状态")
        module = _module(first_repo, campaign["id"], "并发状态模组")
        run = first_repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key=None,
            active_spoiler_tags=[],
            state={"clock": 1},
            started_by_member_id=None,
        )
        first_connection.commit()

        second_connection = connect(database_path)
        try:
            second_repo = Repository(second_connection)
            stale = second_repo.get_campaign_module_run(run["id"])
            updated = first_repo.update_campaign_module_run(
                run["id"],
                {
                    "expected_version": run["version"],
                    "state": {"clock": 2, "door": "open"},
                },
            )
            first_connection.commit()
            assert updated["version"] == run["version"] + 1

            with pytest.raises(ValueError, match="refresh"):
                second_repo.update_campaign_module_run(
                    run["id"],
                    {
                        "expected_version": stale["version"],
                        "state": {"clock": 1, "cultist": "arrived"},
                    },
                )
            second_connection.rollback()
            current = second_repo.get_campaign_module_run(run["id"])
            assert current["state"] == {"clock": 2, "door": "open"}
        finally:
            second_connection.close()
    finally:
        first_connection.close()

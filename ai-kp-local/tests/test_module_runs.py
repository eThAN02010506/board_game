from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
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

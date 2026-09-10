from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ai_kp.api.main import create_app
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import TurnService
from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.auto_kp_worker import process_claimed_auto_kp_job
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.modules.ingestion import ModuleChunk


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _campaign_with_action(repo: Repository, title: str, *, suffix: str) -> dict:
    campaign = repo.create_campaign(title)
    session = SessionService(repo).create(campaign["id"], kp_display_name=f"KP {suffix}")
    player = SessionService(repo).join(
        session["join_code"],
        display_name=f"Player {suffix}",
    )
    identity = repo.authenticate_access_token(player["access_token"])
    assert identity is not None
    action = TurnService(repo).submit_player_action(
        identity,
        action_text=f"I inspect the room in campaign {suffix}.",
        client_action_id=f"authority-action-{suffix}",
    )
    return {"campaign": campaign, "session": session, "action": action}


class AutoKpJobAuthorityApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "auto-kp-authority.sqlite3"
        self.settings = Settings(
            db_path=self.db_path,
            llm_base_url="http://must-not-be-called.local/v1",
            llm_model="authority-test-model",
        )
        connection = connect(self.db_path)
        try:
            init_db(connection)
            repo = Repository(connection)
            self.first = _campaign_with_action(repo, "Campaign A", suffix="a")
            self.second = _campaign_with_action(repo, "Campaign B", suffix="b")
            connection.commit()
        finally:
            connection.close()

        self.app = create_app(self.settings)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://authority.test",
        )
        self.first_headers = _bearer(str(self.first["session"]["access_token"]))

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.tmpdir.cleanup()

    async def test_generic_enqueue_accepts_own_action_and_rejects_foreign_action(
        self,
    ) -> None:
        campaign_id = str(self.first["campaign"]["id"])
        own_action_id = str(self.first["action"]["id"])
        own_response = await self.client.post(
            f"/campaigns/{campaign_id}/auto-kp/jobs",
            headers=self.first_headers,
            json={
                "job_type": "player_action",
                "resource_id": own_action_id,
                "idempotency_key": "authority-own-action-0001",
                "payload": {"action_id": own_action_id},
            },
        )
        self.assertEqual(own_response.status_code, 200, own_response.text)
        own_job = own_response.json()
        self.assertEqual(
            own_job["payload"]["session_id"],
            self.first["session"]["session"]["id"],
        )

        foreign_action_id = str(self.second["action"]["id"])
        foreign_response = await self.client.post(
            f"/campaigns/{campaign_id}/auto-kp/jobs",
            headers=self.first_headers,
            json={
                "job_type": "player_action",
                "resource_id": foreign_action_id,
                "idempotency_key": "authority-cross-campaign-0001",
                "payload": {"action_id": foreign_action_id},
            },
        )
        self.assertEqual(foreign_response.status_code, 403, foreign_response.text)
        self.assertIn("crosses campaign", foreign_response.json()["detail"])

        connection = connect(self.db_path)
        try:
            repo = Repository(connection)
            jobs = repo.list_auto_kp_jobs(campaign_id)
            self.assertEqual([job["id"] for job in jobs], [own_job["id"]])
            foreign_action = repo.get_player_action(foreign_action_id)
            self.assertEqual(foreign_action["status"], "submitted")
            self.assertIsNone(foreign_action["proposal_id"])
        finally:
            connection.close()

    async def test_generic_enqueue_accepts_session_bound_world_expansion(self) -> None:
        campaign_id = str(self.first["campaign"]["id"])
        connection = connect(self.db_path)
        try:
            repo = Repository(connection)
            module = repo.create_module(
                campaign_id,
                "Authority world fixture",
                [
                    ModuleChunk(
                        title="Town square",
                        text="The investigators are free to explore beyond the source.",
                        visibility="kp",
                        scene_key="town-square",
                        order_index=0,
                    )
                ],
            )
            run = repo.start_campaign_module_run(
                campaign_id=campaign_id,
                module_id=str(module["id"]),
                current_scene_key="town-square",
                active_spoiler_tags=[],
                state={},
                started_by_member_id=str(self.first["session"]["member"]["id"]),
            )
            connection.commit()
        finally:
            connection.close()

        response = await self.client.post(
            f"/campaigns/{campaign_id}/auto-kp/jobs",
            headers=self.first_headers,
            json={
                "job_type": "world_expansion",
                "resource_id": run["id"],
                "run_id": run["id"],
                "idempotency_key": "authority-world-expansion-0001",
                "payload": {
                    "run_id": run["id"],
                    "run_version": run["version"],
                    "player_intent": "I ask whether a nearby public archive exists.",
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        job = response.json()
        self.assertEqual(job["campaign_id"], campaign_id)
        self.assertEqual(job["run_id"], run["id"])
        self.assertEqual(
            job["payload"]["session_id"],
            self.first["session"]["session"]["id"],
        )

    async def test_campaign_recovery_does_not_mutate_another_campaign_job(self) -> None:
        connection = connect(self.db_path)
        try:
            repo = Repository(connection)
            jobs = []
            for index, fixture in enumerate((self.first, self.second), start=1):
                action_id = str(fixture["action"]["id"])
                jobs.append(
                    repo.enqueue_auto_kp_job(
                        campaign_id=str(fixture["campaign"]["id"]),
                        job_type="player_action",
                        resource_id=action_id,
                        idempotency_key=f"stale-recovery-scope-000{index}",
                        payload={
                            "action_id": action_id,
                            "session_id": fixture["session"]["session"]["id"],
                        },
                    )
                )
            claimed = [
                repo.claim_next_auto_kp_job(worker_id="stale-scope-worker"),
                repo.claim_next_auto_kp_job(worker_id="stale-scope-worker"),
            ]
            self.assertTrue(all(job is not None for job in claimed))
            connection.execute(
                """
                UPDATE auto_kp_jobs
                SET locked_at = datetime(CURRENT_TIMESTAMP, '-20 minutes')
                WHERE id IN (?, ?)
                """,
                (jobs[0]["id"], jobs[1]["id"]),
            )
            connection.commit()
        finally:
            connection.close()

        campaign_id = str(self.first["campaign"]["id"])
        response = await self.client.post(
            f"/campaigns/{campaign_id}/auto-kp/jobs/recover",
            headers=self.first_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["recovered"], 1)

        connection = connect(self.db_path)
        try:
            repo = Repository(connection)
            self.assertEqual(repo.get_auto_kp_job(str(jobs[0]["id"]))["status"], "retry_wait")
            self.assertEqual(repo.get_auto_kp_job(str(jobs[1]["id"]))["status"], "running")
        finally:
            connection.close()


def test_worker_rejects_durable_cross_campaign_job_before_director_call(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "auto-kp-worker-authority.sqlite3"
    connection = connect(db_path)
    try:
        init_db(connection)
        repo = Repository(connection)
        first = _campaign_with_action(repo, "Worker campaign A", suffix="worker-a")
        second = _campaign_with_action(repo, "Worker campaign B", suffix="worker-b")
        foreign_action_id = str(second["action"]["id"])
        forged = repo.enqueue_auto_kp_job(
            campaign_id=str(first["campaign"]["id"]),
            job_type="player_action",
            resource_id=foreign_action_id,
            idempotency_key="worker-cross-campaign-0001",
            payload={
                "action_id": foreign_action_id,
                "session_id": second["session"]["session"]["id"],
            },
        )
        claimed = repo.claim_next_auto_kp_job(worker_id="authority-test-worker")
        assert claimed is not None
        connection.commit()

        with patch(
            "ai_kp.infrastructure.auto_kp_worker._director",
            side_effect=AssertionError("authority validation must run before the model"),
        ) as director:
            process_claimed_auto_kp_job(
                connection,
                claimed,
                settings=Settings(
                    db_path=db_path,
                    llm_base_url="http://must-not-be-called.local/v1",
                    llm_model="authority-test-model",
                ),
            )

        director.assert_not_called()
        saved = repo.get_auto_kp_job(str(forged["id"]))
        assert saved["status"] == "retry_wait"
        assert "crosses campaign" in saved["last_error"]
        foreign_action = repo.get_player_action(foreign_action_id)
        assert foreign_action["status"] == "submitted"
        assert foreign_action["proposal_id"] is None
    finally:
        connection.close()

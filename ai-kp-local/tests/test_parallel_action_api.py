from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

from ai_kp.api.main import create_app
from ai_kp.application.parallel_kernel_service import ParallelKernelService
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.core.config import Settings
from ai_kp.core.repository import Repository
from ai_kp.infrastructure.auto_kp_worker import process_claimed_auto_kp_job
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.modules.ingestion import ModuleChunk
from tests.scenario_contract_testkit import bind_payload_to_module
from tests.support_investigators import (
    coc7_sheet,
    confirm_current_session_zero,
    create_approved_player,
)
from tests.test_parallel_action_kernel import parallel_contract
from tests.test_parallel_action_planning_service import MechanicalParallelDirector

PLAYER_BATCH_KEYS = {
    "id",
    "status",
    "version",
    "participant_count",
    "confirmed_count",
    "waiting_count",
    "self_phase",
    "own_item",
    "updated_at",
    "settled_at",
    "public_message",
}

COORDINATOR_SUMMARY_KEYS = {
    "id",
    "status",
    "version",
    "attention_reason",
    "updated_at",
    "participant_count",
    "confirmed_count",
    "pending_check_count",
    "abandon_allowed",
    "abandon_block_reason",
}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _install_parallel_run(
    db_path: Path,
    *,
    campaign_id: str,
    kp_member_id: str,
    automation_level: str,
) -> dict[str, Any]:
    """Install a source-bound generic contract behind the public API fixture."""

    connection = connect(db_path)
    try:
        init_db(connection)
        repo = Repository(connection)
        module = repo.create_module(
            campaign_id,
            "Generic parallel API fixture",
            [
                ModuleChunk(
                    title="Shared scene",
                    text="Two investigators can act at the same time.",
                    visibility="kp",
                    scene_key="shared-scene",
                    order_index=0,
                )
            ],
        )
        service = ScenarioContractService(repo)
        _, draft = service.compile_draft(
            module["id"],
            bind_payload_to_module(
                repo,
                module["id"],
                parallel_contract().model_dump(mode="json"),
            ),
            created_by_member_id=kp_member_id,
        )
        assert draft is not None
        published = service.publish(
            draft["id"],
            expected_row_version=int(draft["row_version"]),
            published_by_member_id=kp_member_id,
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign_id,
            module_id=module["id"],
            current_scene_key="shared-scene",
            active_spoiler_tags=[],
            state={},
            started_by_member_id=kp_member_id,
        )
        service.bind_run(run["id"], published["id"])
        ParallelKernelService(repo).initialize_run(str(run["id"]))
        if automation_level != "conservative":
            current = repo.get_campaign_module_run(str(run["id"]))
            run = repo.set_module_run_automation_level(
                str(run["id"]),
                expected_version=int(current["version"]),
                level=automation_level,
                reason="parallel API contract fixture",
                member_id=kp_member_id,
            )
        connection.commit()
        return run
    finally:
        connection.close()


class ParallelActionApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "parallel-api.sqlite3"
        self.settings = Settings(
            db_path=self.db_path,
            llm_base_url="http://must-not-be-called.local/v1",
            llm_model="parallel-api-fake",
        )
        self.app = create_app(self.settings)
        self.transport = httpx.ASGITransport(app=self.app)
        self.client = httpx.AsyncClient(
            transport=self.transport,
            base_url="http://test.local",
        )
        self.campaign = (
            await self.client.post("/campaigns", json={"title": "Parallel API campaign"})
        ).json()
        self.session = (
            await self.client.post(
                f"/campaigns/{self.campaign['id']}/sessions",
                json={"kp_display_name": "Parallel KP"},
            )
        ).json()
        self.kp_headers = _bearer(str(self.session["access_token"]))

        first_sheet = coc7_sheet(
            "Ada Investigator",
            skills={"Spot Hidden": 60, "Art": 50},
        )
        first_sheet["skills"][0]["skill_key"] = "spot_hidden"
        first_sheet["skills"][1]["skill_key"] = "art"
        self.first = await create_approved_player(
            self.client,
            campaign=self.campaign,
            session=self.session,
            kp_headers=self.kp_headers,
            display_name="Ada",
            sheet=first_sheet,
        )
        self.second = await create_approved_player(
            self.client,
            campaign=self.campaign,
            session=self.session,
            kp_headers=self.kp_headers,
            display_name="Bert",
            sheet=coc7_sheet("Bert Investigator", skills={"Listen": 55}),
        )
        await confirm_current_session_zero(
            self.client,
            campaign_id=str(self.campaign["id"]),
            member_headers=(
                self.kp_headers,
                self.first["headers"],
                self.second["headers"],
            ),
        )
        self.run = _install_parallel_run(
            self.db_path,
            campaign_id=str(self.campaign["id"]),
            kp_member_id=str(self.session["member"]["id"]),
            automation_level="balanced",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.tmpdir.cleanup()

    def _set_automation_level(self, level: str) -> None:
        connection = connect(self.db_path)
        try:
            repo = Repository(connection)
            current = repo.get_campaign_module_run(str(self.run["id"]))
            self.run = repo.set_module_run_automation_level(
                str(self.run["id"]),
                expected_version=int(current["version"]),
                level=level,
                reason=f"exercise {level} parallel API behavior",
                member_id=str(self.session["member"]["id"]),
            )
            connection.commit()
        finally:
            connection.close()

    async def _submit_actions(
        self,
        first_text: str,
        second_text: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        actions = []
        for index, (player, action_text) in enumerate(
            ((self.first, first_text), (self.second, second_text)),
            start=1,
        ):
            response = await self.client.post(
                f"/campaigns/{self.campaign['id']}/actions",
                headers=player["headers"],
                json={
                    "action_text": action_text,
                    "client_action_id": f"parallel-api-{index}-{first_text[:10]}",
                    "auto_advance": False,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            actions.append(response.json())
        return actions[0], actions[1]

    async def _prepare(
        self,
        first_action: dict[str, Any],
        second_action: dict[str, Any],
        selections: dict[str, tuple[str, str | None]],
    ) -> dict[str, Any]:
        def create_director(repo: Repository, *_args: object) -> MechanicalParallelDirector:
            return MechanicalParallelDirector(repo.connection, selections)

        with patch(
            "ai_kp.api.routers.parallel_actions.create_kp_orchestrator",
            side_effect=create_director,
        ):
            response = await self.client.post(
                f"/campaigns/{self.campaign['id']}/actions/settle",
                headers=self.kp_headers,
                json={"action_ids": [first_action["id"], second_action["id"]]},
            )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "awaiting_confirmation")
        self.assertEqual(result["batch"]["status"], "awaiting_confirmation")
        return result

    async def _current(self, player: dict[str, Any]) -> dict[str, Any] | None:
        response = await self.client.get(
            f"/campaigns/{self.campaign['id']}/parallel-action-batches/current",
            headers=player["headers"],
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def _confirm(
        self,
        *,
        player: dict[str, Any],
        action: dict[str, Any],
        batch_version: int,
        adjudication_version: int,
        selected_skill: str | None,
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"/player-actions/{action['id']}/adjudication/confirm",
            headers=player["headers"],
            json={
                "expected_version": adjudication_version,
                "expected_batch_version": batch_version,
                "selected_skill": selected_skill,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(
            set(result),
            {
                "status",
                "player_action",
                "proposal",
                "checks",
                "adjudication",
                "parallel_batch",
                "job",
                "message",
            },
        )
        self.assertIsNone(result["proposal"])
        self.assertEqual(set(result["parallel_batch"]), PLAYER_BATCH_KEYS)
        return result

    async def test_skill_confirmations_use_a_barrier_and_player_safe_views(self) -> None:
        self.assertIsNone(await self._current(self.first))
        first_action, second_action = await self._submit_actions(
            "inspect the hidden mark",
            "open the panel",
        )

        rejected_extra = await self.client.post(
            f"/campaigns/{self.campaign['id']}/actions/settle",
            headers=self.kp_headers,
            json={
                "action_ids": [first_action["id"], second_action["id"]],
                "auto_approve": True,
            },
        )
        self.assertEqual(rejected_extra.status_code, 422, rejected_extra.text)
        player_denied = await self.client.post(
            f"/campaigns/{self.campaign['id']}/actions/settle",
            headers=self.first["headers"],
            json={"action_ids": [first_action["id"], second_action["id"]]},
        )
        self.assertEqual(player_denied.status_code, 403, player_denied.text)

        prepared = await self._prepare(
            first_action,
            second_action,
            {
                "inspect the hidden mark": ("find-mark", "spot_hidden"),
                "open the panel": ("open-panel", None),
            },
        )
        batch_id = str(prepared["batch"]["id"])
        first_view = await self._current(self.first)
        second_view = await self._current(self.second)
        assert first_view is not None and second_view is not None
        self.assertEqual(set(first_view), PLAYER_BATCH_KEYS)
        self.assertEqual(first_view["own_item"]["action_id"], first_action["id"])
        self.assertEqual(second_view["own_item"]["action_id"], second_action["id"])
        self.assertEqual(first_view["participant_count"], 2)
        self.assertEqual(first_view["confirmed_count"], 0)

        first_serialized = json.dumps(first_view, ensure_ascii=False)
        self.assertNotIn(second_action["id"], first_serialized)
        self.assertNotIn(second_action["action_text"], first_serialized)
        self.assertNotIn("preparation_hash", first_serialized)
        self.assertNotIn("operator_id", first_serialized)
        self.assertNotIn("outcome_key", first_serialized)
        self.assertNotIn("attention_reason", first_serialized)

        observer = (
            await self.client.post(
                "/sessions/join",
                json={
                    "join_code": self.session["join_code"],
                    "display_name": "Observer",
                },
            )
        ).json()
        nonparticipant = await self.client.get(
            f"/parallel-action-batches/{batch_id}",
            headers=_bearer(str(observer["access_token"])),
        )
        self.assertIn(nonparticipant.status_code, {403, 404}, nonparticipant.text)

        first_confirmation = await self._confirm(
            player=self.first,
            action=first_action,
            batch_version=int(first_view["version"]),
            adjudication_version=int(first_view["own_item"]["adjudication"]["version"]),
            selected_skill="spot_hidden",
        )
        self.assertEqual(
            first_confirmation["parallel_batch"]["self_phase"],
            "waiting_for_others",
        )
        self.assertEqual(first_confirmation["checks"], [])

        refreshed_second = await self._current(self.second)
        assert refreshed_second is not None
        second_confirmation = await self._confirm(
            player=self.second,
            action=second_action,
            batch_version=int(refreshed_second["version"]),
            adjudication_version=int(refreshed_second["own_item"]["adjudication"]["version"]),
            selected_skill=None,
        )
        self.assertEqual(second_confirmation["parallel_batch"]["status"], "awaiting_checks")
        self.assertEqual(second_confirmation["checks"], [])
        first_roll_view = await self._current(self.first)
        second_wait_view = await self._current(self.second)
        assert first_roll_view is not None and second_wait_view is not None
        self.assertEqual(first_roll_view["self_phase"], "awaiting_check")
        self.assertEqual(second_wait_view["self_phase"], "waiting_for_checks")
        self.assertEqual(len(first_roll_view["own_item"]["checks"]), 1)
        self.assertEqual(second_wait_view["own_item"]["checks"], [])
        check_id = first_roll_view["own_item"]["checks"][0]["id"]
        self.assertNotIn(check_id, json.dumps(second_wait_view, ensure_ascii=False))

        resolved = await self.client.post(
            f"/checks/{check_id}/resolve",
            headers=self.first["headers"],
            json={
                "input_method": "physical",
                "ones_digit": 1,
                "tens_digits": [0],
                "auto_advance": False,
            },
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        ready = await self._current(self.first)
        assert ready is not None
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(ready["self_phase"], "ready")
        self.assertEqual(
            (
                await self.client.get(
                    f"/campaigns/{self.campaign['id']}/auto-kp/jobs",
                    headers=self.first["headers"],
                )
            ).json(),
            [],
        )

    async def test_legacy_adjudication_reads_cannot_bypass_player_projection(
        self,
    ) -> None:
        first_action, second_action = await self._submit_actions(
            "inspect the hidden mark",
            "open the panel",
        )
        await self._prepare(
            first_action,
            second_action,
            {
                "inspect the hidden mark": ("find-mark", "spot_hidden"),
                "open the panel": ("open-panel", None),
            },
        )
        first_view = await self._current(self.first)
        assert first_view is not None

        legacy_read = await self.client.get(
            f"/player-actions/{first_action['id']}/adjudication",
            headers=self.first["headers"],
        )
        self.assertEqual(legacy_read.status_code, 200, legacy_read.text)
        self.assertEqual(
            legacy_read.json(),
            first_view["own_item"]["adjudication"],
        )
        serialized = json.dumps(legacy_read.json(), ensure_ascii=False)
        for forbidden in (
            '"proposal_id"',
            '"source_error"',
            '"events"',
            '"tabletop_turn"',
            '"hidden"',
        ):
            self.assertNotIn(forbidden, serialized)

        for player in (self.first, self.second):
            pending = await self.client.get(
                f"/campaigns/{self.campaign['id']}/action-adjudications/pending",
                headers=player["headers"],
            )
            self.assertEqual(pending.status_code, 200, pending.text)
            self.assertEqual(pending.json(), [])

        kp_read = await self.client.get(
            f"/player-actions/{first_action['id']}/adjudication",
            headers=self.kp_headers,
        )
        self.assertEqual(kp_read.status_code, 200, kp_read.text)
        self.assertIn("proposal_id", kp_read.json())
        self.assertIn("hidden", kp_read.json()["skill_options"][0])

    async def test_full_ai_direct_batch_enqueues_one_safe_idempotent_commit_job(
        self,
    ) -> None:
        self._set_automation_level("ai_kp")
        first_action, second_action = await self._submit_actions(
            "open the panel",
            "observe quietly",
        )
        await self._prepare(
            first_action,
            second_action,
            {
                "open the panel": ("open-panel", None),
                "observe quietly": ("observe-quietly", None),
            },
        )
        first_view = await self._current(self.first)
        assert first_view is not None
        await self._confirm(
            player=self.first,
            action=first_action,
            batch_version=int(first_view["version"]),
            adjudication_version=int(first_view["own_item"]["adjudication"]["version"]),
            selected_skill=None,
        )
        second_view = await self._current(self.second)
        assert second_view is not None
        final = await self._confirm(
            player=self.second,
            action=second_action,
            batch_version=int(second_view["version"]),
            adjudication_version=int(second_view["own_item"]["adjudication"]["version"]),
            selected_skill=None,
        )
        self.assertEqual(final["parallel_batch"]["status"], "ready")
        self.assertEqual(final["job"]["job_type"], "parallel_actions")
        self.assertEqual(final["job"]["phase"], "settlement")
        self.assertNotIn("resource_id", final["job"])

        replay = await self._confirm(
            player=self.second,
            action=second_action,
            batch_version=int(second_view["version"]),
            adjudication_version=int(second_view["own_item"]["adjudication"]["version"]),
            selected_skill=None,
        )
        self.assertEqual(replay["job"]["id"], final["job"]["id"])

        for player in (self.first, self.second):
            jobs_response = await self.client.get(
                f"/campaigns/{self.campaign['id']}/auto-kp/jobs",
                headers=player["headers"],
            )
            self.assertEqual(jobs_response.status_code, 200, jobs_response.text)
            jobs = [
                job
                for job in jobs_response.json()
                if job["job_type"] == "parallel_actions"
                and job.get("phase") == "settlement"
            ]
            self.assertEqual(len(jobs), 1)
            self.assertNotIn("resource_id", jobs[0])
            self.assertNotIn("payload", jobs[0])
            self.assertNotIn("result", jobs[0])
            self.assertNotIn("last_error", jobs[0])

    async def test_full_ai_check_batch_commits_even_when_legacy_auto_advance_is_off(
        self,
    ) -> None:
        """The run automation level, not a legacy UI toggle, owns batch commit."""

        self._set_automation_level("ai_kp")
        first_action, second_action = await self._submit_actions(
            "inspect the hidden mark",
            "open the panel",
        )
        await self._prepare(
            first_action,
            second_action,
            {
                "inspect the hidden mark": ("find-mark", "spot_hidden"),
                "open the panel": ("open-panel", None),
            },
        )
        first_view = await self._current(self.first)
        assert first_view is not None
        await self._confirm(
            player=self.first,
            action=first_action,
            batch_version=int(first_view["version"]),
            adjudication_version=int(
                first_view["own_item"]["adjudication"]["version"]
            ),
            selected_skill="spot_hidden",
        )
        second_view = await self._current(self.second)
        assert second_view is not None
        await self._confirm(
            player=self.second,
            action=second_action,
            batch_version=int(second_view["version"]),
            adjudication_version=int(
                second_view["own_item"]["adjudication"]["version"]
            ),
            selected_skill=None,
        )
        roll_view = await self._current(self.first)
        assert roll_view is not None
        check_id = str(roll_view["own_item"]["checks"][0]["id"])

        resolved = await self.client.post(
            f"/checks/{check_id}/resolve",
            headers=self.first["headers"],
            json={
                "input_method": "physical",
                "ones_digit": 1,
                "tens_digits": [0],
                # This switch belongs to the old single-action flow.  Full AI
                # still has to finish the now-ready atomic multiplayer batch.
                "auto_advance": False,
            },
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        envelope = resolved.json()
        self.assertEqual(envelope["parallel_batch"]["status"], "ready")
        self.assertEqual(envelope["job"]["job_type"], "parallel_actions")
        self.assertEqual(envelope["job"]["phase"], "settlement")
        self.assertNotIn("resource_id", envelope["job"])

    async def test_balanced_mode_requires_kp_commit_and_commit_retry_is_safe(
        self,
    ) -> None:
        first_action, second_action = await self._submit_actions(
            "open the panel",
            "observe quietly",
        )
        await self._prepare(
            first_action,
            second_action,
            {
                "open the panel": ("open-panel", None),
                "observe quietly": ("observe-quietly", None),
            },
        )
        first_view = await self._current(self.first)
        assert first_view is not None
        await self._confirm(
            player=self.first,
            action=first_action,
            batch_version=int(first_view["version"]),
            adjudication_version=int(first_view["own_item"]["adjudication"]["version"]),
            selected_skill=None,
        )
        second_view = await self._current(self.second)
        assert second_view is not None
        ready_envelope = await self._confirm(
            player=self.second,
            action=second_action,
            batch_version=int(second_view["version"]),
            adjudication_version=int(second_view["own_item"]["adjudication"]["version"]),
            selected_skill=None,
        )
        self.assertIsNone(ready_envelope["job"])
        batch = ready_envelope["parallel_batch"]
        self.assertEqual(batch["status"], "ready")

        denied = await self.client.post(
            f"/parallel-action-batches/{batch['id']}/commit",
            headers=self.first["headers"],
            json={"expected_version": batch["version"]},
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        committed = await self.client.post(
            f"/parallel-action-batches/{batch['id']}/commit",
            headers=self.kp_headers,
            json={"expected_version": batch["version"]},
        )
        self.assertEqual(committed.status_code, 200, committed.text)
        self.assertEqual(committed.json()["status"], "settled")
        retried = await self.client.post(
            f"/parallel-action-batches/{batch['id']}/commit",
            headers=self.kp_headers,
            json={"expected_version": batch["version"]},
        )
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["status"], "settled")

        settled_view = await self.client.get(
            f"/parallel-action-batches/{batch['id']}",
            headers=self.first["headers"],
        )
        self.assertEqual(settled_view.status_code, 200, settled_view.text)
        self.assertEqual(settled_view.json()["status"], "settled")
        self.assertIsNone(await self._current(self.first))
        connection = connect(self.db_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM scenario_command_batches WHERE run_id = ?",
                    (self.run["id"],),
                ).fetchone()[0],
                1,
            )
        finally:
            connection.close()

    async def test_revision_supersedes_the_whole_batch_before_any_roll(self) -> None:
        first_action, second_action = await self._submit_actions(
            "open the panel",
            "observe quietly",
        )
        prepared = await self._prepare(
            first_action,
            second_action,
            {
                "open the panel": ("open-panel", None),
                "observe quietly": ("observe-quietly", None),
            },
        )
        batch_id = str(prepared["batch"]["id"])
        first_view = await self._current(self.first)
        second_view = await self._current(self.second)
        assert first_view is not None and second_view is not None

        blank_revision = await self.client.post(
            f"/player-actions/{first_action['id']}/adjudication/revise",
            headers=self.first["headers"],
            json={
                "expected_version": first_view["own_item"]["adjudication"]["version"],
                "expected_batch_version": first_view["version"],
                "action_text": "   ",
                "background": False,
            },
        )
        self.assertEqual(blank_revision.status_code, 409, blank_revision.text)
        still_active = await self._current(self.first)
        assert still_active is not None
        self.assertEqual(still_active["id"], batch_id)

        revised = await self.client.post(
            f"/player-actions/{first_action['id']}/adjudication/revise",
            headers=self.first["headers"],
            json={
                "expected_version": first_view["own_item"]["adjudication"]["version"],
                "expected_batch_version": first_view["version"],
                "action_text": "I wait and reconsider the shared situation.",
                "background": False,
            },
        )
        self.assertEqual(revised.status_code, 200, revised.text)
        revised_payload = revised.json()
        self.assertEqual(
            revised_payload["player_action"]["action_text"],
            "I wait and reconsider the shared situation.",
        )
        self.assertEqual(revised_payload["status"], "awaiting_group_resubmission")
        self.assertEqual(revised_payload["workflow_status"], "needs_attention")
        self.assertIsNone(revised_payload["job"])
        superseded = await self.client.get(
            f"/parallel-action-batches/{batch_id}",
            headers=self.second["headers"],
        )
        self.assertEqual(superseded.status_code, 200, superseded.text)
        self.assertEqual(superseded.json()["status"], "superseded")
        self.assertEqual(superseded.json()["self_phase"], "superseded")
        self.assertIsNone(await self._current(self.first))
        self.assertIsNone(await self._current(self.second))

        stale_confirmation = await self.client.post(
            f"/player-actions/{second_action['id']}/adjudication/confirm",
            headers=self.second["headers"],
            json={
                "expected_version": second_view["own_item"]["adjudication"]["version"],
                "expected_batch_version": second_view["version"],
                "selected_skill": None,
            },
        )
        self.assertIn(
            stale_confirmation.status_code,
            {403, 409},
            stale_confirmation.text,
        )

        peer_resubmission = await self.client.post(
            f"/campaigns/{self.campaign['id']}/actions",
            headers=self.second["headers"],
            json={
                "action_text": "I explicitly ask Auto KP to observe quietly again.",
                "client_action_id": "parallel-manual-revision-peer-resubmit",
                "auto_advance": True,
                "background": True,
            },
        )
        self.assertEqual(peer_resubmission.status_code, 200, peer_resubmission.text)
        peer_payload = peer_resubmission.json()
        self.assertEqual(peer_payload["job"]["job_type"], "player_action")
        self.assertEqual(
            peer_payload["job"]["resource_id"],
            peer_payload["player_action"]["id"],
        )

        connection = connect(self.db_path)
        try:
            repo = Repository(connection)
            for old_action in (first_action, second_action):
                self.assertEqual(
                    repo.get_player_action(str(old_action["id"]))["status"],
                    "rejected",
                )
                self.assertEqual(
                    repo.get_action_adjudication(str(old_action["id"]))["status"],
                    "superseded",
                )
            jobs = repo.list_auto_kp_jobs(str(self.campaign["id"]), limit=20)
            self.assertFalse(
                any(
                    job["resource_id"] == revised_payload["player_action"]["id"]
                    for job in jobs
                )
            )
            self.assertFalse(
                any(job["job_type"] == "parallel_actions" for job in jobs)
            )
        finally:
            connection.close()

    async def test_background_revision_rejoins_only_after_peer_auto_kp_opt_in(
        self,
    ) -> None:
        first_action, second_action = await self._submit_actions(
            "open the panel",
            "observe quietly",
        )
        await self._prepare(
            first_action,
            second_action,
            {
                "open the panel": ("open-panel", None),
                "observe quietly": ("observe-quietly", None),
            },
        )
        first_view = await self._current(self.first)
        assert first_view is not None
        revised = await self.client.post(
            f"/player-actions/{first_action['id']}/adjudication/revise",
            headers=self.first["headers"],
            json={
                "expected_version": first_view["own_item"]["adjudication"][
                    "version"
                ],
                "expected_batch_version": first_view["version"],
                "action_text": "I open the panel after checking its hinges.",
                "background": True,
            },
        )
        self.assertEqual(revised.status_code, 200, revised.text)
        revised_payload = revised.json()
        replacement = revised_payload["player_action"]
        self.assertEqual(revised_payload["status"], "awaiting_group_resubmission")
        self.assertIsNone(revised_payload["job"])
        first_regather = revised_payload["parallel_regather"]
        self.assertEqual(first_regather["self_phase"], "waiting_for_others")
        self.assertEqual(first_regather["submitted_count"], 1)
        self.assertEqual(first_regather["waiting_count"], 1)
        self.assertEqual(first_regather["own_action_id"], replacement["id"])

        peer_regather = await self.client.get(
            f"/campaigns/{self.campaign['id']}/parallel-action-regathers/current",
            headers=self.second["headers"],
        )
        self.assertEqual(peer_regather.status_code, 200, peer_regather.text)
        self.assertEqual(peer_regather.json()["self_phase"], "awaiting_submission")
        self.assertIsNone(peer_regather.json()["own_action_id"])

        # The durable barrier must not depend on the ordinary two-second window.
        connection = connect(self.db_path)
        try:
            connection.execute(
                "UPDATE player_actions SET created_at = datetime('now', '-1 day') "
                "WHERE id = ?",
                (replacement["id"],),
            )
            connection.commit()
        finally:
            connection.close()

        peer = await self.client.post(
            f"/campaigns/{self.campaign['id']}/actions",
            headers=self.second["headers"],
            json={
                "action_text": "I observe quietly after the plan changes.",
                "client_action_id": "parallel-background-revision-peer",
                "auto_advance": True,
                "background": True,
            },
        )
        self.assertEqual(peer.status_code, 200, peer.text)
        peer_payload = peer.json()
        cohort_job_view = peer_payload["job"]
        self.assertEqual(cohort_job_view["job_type"], "parallel_actions")
        self.assertEqual(cohort_job_view["phase"], "prepare")
        self.assertNotIn("resource_id", cohort_job_view)

        connection = connect(self.db_path)
        try:
            repo = Repository(connection)
            cohort_job = repo.get_auto_kp_job(str(cohort_job_view["id"]))
            self.assertEqual(cohort_job["payload"]["phase"], "prepare")
            self.assertEqual(cohort_job["payload"]["regather_id"], first_regather["id"])
            self.assertEqual(
                set(cohort_job["payload"]["action_ids"]),
                {replacement["id"], peer_payload["player_action"]["id"]},
            )
            self.assertNotIn(first_action["id"], cohort_job["payload"]["action_ids"])
            self.assertNotIn(second_action["id"], cohort_job["payload"]["action_ids"])
            connection.execute(
                "UPDATE auto_kp_jobs SET next_run_at = CURRENT_TIMESTAMP WHERE id = ?",
                (cohort_job["id"],),
            )
            claimed = repo.claim_next_auto_kp_job(worker_id="revision-regather-test")
            self.assertIsNotNone(claimed)
            connection.commit()

            def create_director(
                worker_repo: Repository,
                *_args: object,
            ) -> MechanicalParallelDirector:
                return MechanicalParallelDirector(
                    worker_repo.connection,
                    {
                        replacement["action_text"]: ("open-panel", None),
                        peer_payload["player_action"]["action_text"]: (
                            "observe-quietly",
                            None,
                        ),
                    },
                )

            with patch(
                "ai_kp.infrastructure.auto_kp_worker._director",
                side_effect=create_director,
            ):
                await asyncio.to_thread(
                    process_claimed_auto_kp_job,
                    connection,
                    claimed,
                    settings=self.settings,
                )
            saved_job = repo.get_auto_kp_job(str(cohort_job["id"]))
            self.assertEqual(saved_job["status"], "succeeded")
            saved_regather = repo.get_parallel_action_regather(first_regather["id"])
            self.assertEqual(saved_regather["status"], "completed")
            self.assertEqual(saved_regather["completion_kind"], "batch")
        finally:
            connection.close()

        replacement_view = await self._current(self.first)
        peer_view = await self._current(self.second)
        assert replacement_view is not None and peer_view is not None
        self.assertEqual(replacement_view["id"], peer_view["id"])
        self.assertEqual(replacement_view["status"], "awaiting_confirmation")
        self.assertEqual(replacement_view["participant_count"], 2)

    async def test_only_kp_can_resume_or_abandon_a_needs_attention_batch(self) -> None:
        first_action, second_action = await self._submit_actions(
            "open the panel",
            "observe quietly",
        )
        prepared = await self._prepare(
            first_action,
            second_action,
            {
                "open the panel": ("open-panel", None),
                "observe quietly": ("observe-quietly", None),
            },
        )
        batch_id = str(prepared["batch"]["id"])
        connection = connect(self.db_path)
        try:
            repo = Repository(connection)
            paused = repo.transition_parallel_action_batch(
                batch_id,
                "request_attention",
                expected_version=int(prepared["batch"]["version"]),
                actor_member_id=str(self.session["member"]["id"]),
                reason="fixture requires a trusted recovery decision",
            )
            connection.commit()
        finally:
            connection.close()

        for operation in ("resume", "abandon"):
            denied = await self.client.post(
                f"/parallel-action-batches/{batch_id}/{operation}",
                headers=self.first["headers"],
                json={
                    "expected_version": paused["version"],
                    "reason": "a player cannot recover authority",
                },
            )
            self.assertEqual(denied.status_code, 403, denied.text)

        resumed = await self.client.post(
            f"/parallel-action-batches/{batch_id}/resume",
            headers=self.kp_headers,
            json={
                "expected_version": paused["version"],
                "reason": "KP verified the frozen authority",
            },
        )
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(resumed.json()["status"], "awaiting_confirmation")

        connection = connect(self.db_path)
        try:
            repo = Repository(connection)
            current = repo.get_parallel_action_batch(batch_id)
            paused_again = repo.transition_parallel_action_batch(
                batch_id,
                "request_attention",
                expected_version=int(current["version"]),
                actor_member_id=str(self.session["member"]["id"]),
                reason="fixture exercises audited abandonment",
            )
            connection.commit()
        finally:
            connection.close()
        abandoned = await self.client.post(
            f"/parallel-action-batches/{batch_id}/abandon",
            headers=self.kp_headers,
            json={
                "expected_version": paused_again["version"],
                "reason": "KP chooses a clean replan",
            },
        )
        self.assertEqual(abandoned.status_code, 200, abandoned.text)
        self.assertEqual(abandoned.json()["status"], "needs_attention")
        self.assertEqual(abandoned.json()["batch"]["status"], "superseded")
        self.assertIsNone(await self._current(self.first))

    async def test_kp_recovery_index_is_safe_session_scoped_and_refreshable(
        self,
    ) -> None:
        first_action, second_action = await self._submit_actions(
            "inspect the hidden mark",
            "open the panel",
        )
        prepared = await self._prepare(
            first_action,
            second_action,
            {
                "inspect the hidden mark": ("find-mark", "spot_hidden"),
                "open the panel": ("open-panel", None),
            },
        )
        first_view = await self._current(self.first)
        assert first_view is not None
        await self._confirm(
            player=self.first,
            action=first_action,
            batch_version=int(first_view["version"]),
            adjudication_version=int(
                first_view["own_item"]["adjudication"]["version"]
            ),
            selected_skill="spot_hidden",
        )
        second_view = await self._current(self.second)
        assert second_view is not None
        awaiting_checks = await self._confirm(
            player=self.second,
            action=second_action,
            batch_version=int(second_view["version"]),
            adjudication_version=int(
                second_view["own_item"]["adjudication"]["version"]
            ),
            selected_skill=None,
        )

        connection = connect(self.db_path)
        try:
            repo = Repository(connection)
            paused = repo.transition_parallel_action_batch(
                str(prepared["batch"]["id"]),
                "request_attention",
                expected_version=int(awaiting_checks["parallel_batch"]["version"]),
                actor_member_id=str(self.session["member"]["id"]),
                reason="KP must review a stalled shared check",
            )
            connection.commit()
        finally:
            connection.close()

        path = (
            f"/campaigns/{self.campaign['id']}/parallel-action-batches"
            "?status=needs_attention"
        )
        listed = await self.client.get(path, headers=self.kp_headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(len(listed.json()), 1)
        summary = listed.json()[0]
        self.assertEqual(set(summary), COORDINATOR_SUMMARY_KEYS)
        self.assertEqual(summary["id"], paused["id"])
        self.assertEqual(summary["status"], "needs_attention")
        self.assertEqual(summary["version"], paused["version"])
        self.assertEqual(
            summary["attention_reason"],
            "KP must review a stalled shared check",
        )
        self.assertEqual(summary["participant_count"], 2)
        self.assertEqual(summary["confirmed_count"], 2)
        self.assertEqual(summary["pending_check_count"], 1)
        self.assertTrue(summary["abandon_allowed"])
        self.assertEqual(summary["abandon_block_reason"], "")
        serialized = json.dumps(summary, ensure_ascii=False)
        for private_value in (
            first_action["action_text"],
            second_action["action_text"],
            "find-mark",
            "open-panel",
            "preview_hash",
            "commands",
        ):
            self.assertNotIn(private_value, serialized)

        refreshed = await self.client.get(path, headers=self.kp_headers)
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertEqual(refreshed.json(), listed.json())

        player_denied = await self.client.get(path, headers=self.first["headers"])
        self.assertEqual(player_denied.status_code, 403, player_denied.text)
        invalid_status = await self.client.get(
            path.replace("needs_attention", "ready"),
            headers=self.kp_headers,
        )
        self.assertEqual(invalid_status.status_code, 422, invalid_status.text)

        other_campaign = (
            await self.client.post("/campaigns", json={"title": "Other campaign"})
        ).json()
        other_session = (
            await self.client.post(
                f"/campaigns/{other_campaign['id']}/sessions",
                json={"kp_display_name": "Other KP"},
            )
        ).json()
        cross_campaign = await self.client.get(
            path,
            headers=_bearer(str(other_session["access_token"])),
        )
        self.assertEqual(cross_campaign.status_code, 404, cross_campaign.text)

        connection = connect(self.db_path)
        try:
            repo = Repository(connection)
            repo.close_campaign_session(str(self.session["session"]["id"]))
            connection.commit()
        finally:
            connection.close()
        replacement = (
            await self.client.post(
                f"/campaigns/{self.campaign['id']}/sessions",
                json={"kp_display_name": "Replacement KP"},
            )
        ).json()
        cross_session = await self.client.get(
            path,
            headers=_bearer(str(replacement["access_token"])),
        )
        self.assertEqual(cross_session.status_code, 200, cross_session.text)
        self.assertEqual(cross_session.json(), [])

    async def test_known_check_result_blocks_abandon_without_mutating_state(
        self,
    ) -> None:
        first_action, second_action = await self._submit_actions(
            "inspect the hidden mark",
            "open the panel",
        )
        prepared = await self._prepare(
            first_action,
            second_action,
            {
                "inspect the hidden mark": ("find-mark", "spot_hidden"),
                "open the panel": ("open-panel", None),
            },
        )
        first_view = await self._current(self.first)
        assert first_view is not None
        await self._confirm(
            player=self.first,
            action=first_action,
            batch_version=int(first_view["version"]),
            adjudication_version=int(
                first_view["own_item"]["adjudication"]["version"]
            ),
            selected_skill="spot_hidden",
        )
        second_view = await self._current(self.second)
        assert second_view is not None
        await self._confirm(
            player=self.second,
            action=second_action,
            batch_version=int(second_view["version"]),
            adjudication_version=int(
                second_view["own_item"]["adjudication"]["version"]
            ),
            selected_skill=None,
        )
        roll_view = await self._current(self.first)
        assert roll_view is not None
        check_id = str(roll_view["own_item"]["checks"][0]["id"])
        resolved = await self.client.post(
            f"/checks/{check_id}/resolve",
            headers=self.first["headers"],
            json={
                "input_method": "physical",
                "ones_digit": 1,
                "tens_digits": [0],
                "auto_advance": False,
            },
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        ready_batch = resolved.json()["parallel_batch"]
        self.assertEqual(ready_batch["status"], "ready")

        connection = connect(self.db_path)
        try:
            repo = Repository(connection)
            paused = repo.transition_parallel_action_batch(
                str(prepared["batch"]["id"]),
                "request_attention",
                expected_version=int(ready_batch["version"]),
                actor_member_id=str(self.session["member"]["id"]),
                reason="KP recovery must not permit outcome fishing",
            )
            check_before = repo.get_skill_check(check_id)
            connection.commit()
        finally:
            connection.close()

        path = (
            f"/campaigns/{self.campaign['id']}/parallel-action-batches"
            "?status=needs_attention"
        )
        summary_response = await self.client.get(path, headers=self.kp_headers)
        self.assertEqual(summary_response.status_code, 200, summary_response.text)
        summary = summary_response.json()[0]
        self.assertFalse(summary["abandon_allowed"])
        self.assertIn("known result", summary["abandon_block_reason"])
        self.assertNotIn(str(check_before["selected_roll"]), summary["abandon_block_reason"])

        denied = await self.client.post(
            f"/parallel-action-batches/{paused['id']}/abandon",
            headers=self.kp_headers,
            json={
                "expected_version": paused["version"],
                "reason": "Try to discard an unfavorable known result",
            },
        )
        self.assertEqual(denied.status_code, 409, denied.text)
        self.assertIn("known result", denied.json()["detail"])

        connection = connect(self.db_path)
        try:
            repo = Repository(connection)
            batch_after = repo.get_parallel_action_batch(str(paused["id"]))
            check_after = repo.get_skill_check(check_id)
        finally:
            connection.close()
        self.assertEqual(batch_after["status"], "needs_attention")
        self.assertEqual(batch_after["version"], paused["version"])
        self.assertEqual(check_after["status"], check_before["status"])
        self.assertEqual(check_after["selected_roll"], check_before["selected_roll"])
        self.assertEqual(check_after["raw_dice"], check_before["raw_dice"])

    async def test_direct_only_ready_batch_can_be_abandoned(self) -> None:
        first_action, second_action = await self._submit_actions(
            "open the panel",
            "observe quietly",
        )
        prepared = await self._prepare(
            first_action,
            second_action,
            {
                "open the panel": ("open-panel", None),
                "observe quietly": ("observe-quietly", None),
            },
        )
        first_view = await self._current(self.first)
        assert first_view is not None
        await self._confirm(
            player=self.first,
            action=first_action,
            batch_version=int(first_view["version"]),
            adjudication_version=int(
                first_view["own_item"]["adjudication"]["version"]
            ),
            selected_skill=None,
        )
        second_view = await self._current(self.second)
        assert second_view is not None
        ready = await self._confirm(
            player=self.second,
            action=second_action,
            batch_version=int(second_view["version"]),
            adjudication_version=int(
                second_view["own_item"]["adjudication"]["version"]
            ),
            selected_skill=None,
        )

        connection = connect(self.db_path)
        try:
            repo = Repository(connection)
            paused = repo.transition_parallel_action_batch(
                str(prepared["batch"]["id"]),
                "request_attention",
                expected_version=int(ready["parallel_batch"]["version"]),
                actor_member_id=str(self.session["member"]["id"]),
                reason="Direct-only batch needs a clean replan",
            )
            connection.commit()
        finally:
            connection.close()

        summaries = (
            await self.client.get(
                f"/campaigns/{self.campaign['id']}/parallel-action-batches"
                "?status=needs_attention",
                headers=self.kp_headers,
            )
        ).json()
        self.assertEqual(len(summaries), 1)
        self.assertTrue(summaries[0]["abandon_allowed"])
        self.assertEqual(summaries[0]["abandon_block_reason"], "")

        abandoned = await self.client.post(
            f"/parallel-action-batches/{paused['id']}/abandon",
            headers=self.kp_headers,
            json={
                "expected_version": paused["version"],
                "reason": "Replan a direct-only coordinated turn",
            },
        )
        self.assertEqual(abandoned.status_code, 200, abandoned.text)
        self.assertEqual(abandoned.json()["batch"]["status"], "superseded")


if __name__ == "__main__":
    unittest.main()

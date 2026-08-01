import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings
from ai_kp.platform.resolution import HIDDEN_CHECK_PUBLIC_NARRATION
from tests.support_investigators import coc7_sheet, create_approved_player


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeConsequenceLlm:
    def __init__(self) -> None:
        self.calls = 0
        self.messages = []

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls += 1
        self.messages.append(messages)
        return json.dumps(
            {
                "public_narration": "检定结果明确后，你在柜底找到一张撕碎的收据。",
                "kp_notes": "严格依据已验证结果生成。",
                "action_ruling": {
                    "goal": "搜索档案柜",
                    "method": "已经完成的侦查检定",
                    "target": "档案柜",
                    "feasibility": "possible",
                    "resolution": "automatic",
                    "reason": "已验证检定结果支持发现线索。",
                    "maximum_effect": "发现柜底的撕碎收据。",
                    "alternative": "",
                },
                "proposed_checks": [],
                "proposed_events": [
                    {
                        "event_type": "check_consequence_effect",
                        "summary": "调查员在档案柜底发现撕碎的收据。",
                        "actor_type": "pc",
                        "actor_id": None,
                        "visibility": "table",
                        "happened_at": None,
                        "payload": {},
                    }
                ],
                "proposed_memories": [
                    {
                        "text": "档案柜底藏着一张撕碎的收据。",
                        "scope": "clue",
                        "importance": 3,
                        "visibility": "table",
                        "pc_id": None,
                        "npc_id": None,
                        "happened_at": None,
                    }
                ],
                "proposed_npc_updates": [],
                "proposed_map_moves": [],
            },
            ensure_ascii=False,
        )


class HiddenConsequenceLlm(FakeConsequenceLlm):
    def __init__(self, *, effect_visibility: str = "kp") -> None:
        super().__init__()
        self.effect_visibility = effect_visibility

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls += 1
        self.messages.append(messages)
        return json.dumps(
            {
                "public_narration": (
                    "暗骰 D100=04，是极难成功；你立刻发现了密门。"
                ),
                "kp_notes": "暗骰 04，极难成功；密门线索暂不公开。",
                "action_ruling": {
                    "goal": "寻找隐藏入口",
                    "method": "已经完成的KP暗骰侦查检定",
                    "target": "当前房间",
                    "feasibility": "possible",
                    "resolution": "automatic",
                    "reason": "已验证暗骰结果支持发现密门，但结果仍仅KP可见。",
                    "maximum_effect": "在KP侧确认密门存在，不向玩家泄露。",
                    "alternative": "",
                },
                "proposed_checks": [],
                "proposed_events": [
                    {
                        "event_type": "hidden_clue_found",
                        "summary": "暗骰 04 发现密门。",
                        "actor_type": "pc",
                        "actor_id": None,
                        "visibility": self.effect_visibility,
                        "happened_at": None,
                        "payload": {"selected_roll": 4},
                    }
                ],
                "proposed_memories": [
                    {
                        "text": "暗骰 04 发现密门。",
                        "scope": "clue",
                        "importance": 3,
                        "visibility": self.effect_visibility,
                        "pc_id": None,
                        "npc_id": None,
                        "happened_at": None,
                    }
                ],
                "proposed_npc_updates": [],
                "proposed_map_moves": [],
            },
            ensure_ascii=False,
        )


class CheckConsequenceApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.settings = Settings(
            db_path=Path(self.tmpdir.name) / "consequences.sqlite3",
            llm_base_url="http://unused.local/v1",
            llm_api_key="test",
            llm_model="fake-consequence",
        )
        self.app = create_app(self.settings)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 46300)),
            base_url="http://127.0.0.1",
        )
        self.campaign = (
            await self.client.post(
                "/campaigns",
                json={"title": "Check consequence"},
            )
        ).json()
        self.session = (
            await self.client.post(
                f"/campaigns/{self.campaign['id']}/sessions",
                json={"kp_display_name": "Keeper"},
            )
        ).json()
        self.kp_headers = bearer(self.session["access_token"])
        approved_player = await create_approved_player(
            self.client,
            campaign=self.campaign,
            session=self.session,
            kp_headers=self.kp_headers,
            display_name="Player",
            sheet=coc7_sheet("Ada", skills={"侦查": 60}),
        )
        self.pc = approved_player["pc"]
        self.player = approved_player["bundle"]
        self.player_headers = approved_player["headers"]

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.tmpdir.cleanup()

    async def prepare_check(
        self,
        *,
        hidden: bool = False,
    ) -> tuple[dict, dict, dict]:
        action_response = await self.client.post(
            f"/campaigns/{self.campaign['id']}/actions",
            headers=self.player_headers,
            json={
                "action_text": "我仔细检查档案柜。",
                "client_action_id": "consequence-action-001",
            },
        )
        self.assertEqual(action_response.status_code, 200, action_response.text)
        action = action_response.json()
        origin_response = await self.client.post(
            f"/campaigns/{self.campaign['id']}/proposals",
            headers=self.kp_headers,
            json={
                "player_action_id": action["id"],
                "player_action": action["action_text"],
                "public_narration": "请进行侦查检定。",
                "pc_id": self.pc["id"],
                "proposed_checks": [
                    {
                        "skill": "侦查",
                        "difficulty": "regular",
                        "reason": "搜索档案柜",
                        "pc_id": self.pc["id"],
                        "hidden": hidden,
                    }
                ],
            },
        )
        self.assertEqual(origin_response.status_code, 200, origin_response.text)
        origin = origin_response.json()
        approved = await self.client.post(
            f"/kp/proposals/{origin['id']}/approve",
            headers=self.kp_headers,
            json={"note": "request deterministic check"},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        checks = await self.client.get(
            f"/campaigns/{self.campaign['id']}/checks",
            headers=self.kp_headers,
        )
        check = next(
            item
            for item in checks.json()
            if item["player_action_id"] == action["id"]
        )
        return action, origin, check

    async def resolve_check(
        self,
        check_id: str,
        *,
        ones: int,
        tens: int,
        auto_advance: bool = False,
        background: bool = False,
        headers: dict[str, str] | None = None,
    ) -> dict:
        response = await self.client.post(
            f"/checks/{check_id}/resolve",
            headers=headers or self.player_headers,
            json={
                "input_method": "physical",
                "ones_digit": ones,
                "tens_digits": [tens],
                "auto_advance": auto_advance,
                "background": background,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def generate_consequence(
        self,
        check_id: str,
        fake_llm: FakeConsequenceLlm,
    ) -> httpx.Response:
        with patch(
            "ai_kp.api.main.OpenAICompatibleClient",
            return_value=fake_llm,
        ):
            return await self.client.post(
                f"/checks/{check_id}/consequence-proposal",
                headers=self.kp_headers,
            )

    async def test_action_resolves_only_after_idempotent_consequence_approval(self) -> None:
        action, origin, check = await self.prepare_check()
        resolved = await self.resolve_check(check["id"], ones=4, tens=2)
        self.assertTrue(resolved["passed"])

        awaiting = await self.client.get(
            f"/player-actions/{action['id']}",
            headers=self.kp_headers,
        )
        self.assertEqual(awaiting.json()["status"], "reviewed")

        fake_llm = FakeConsequenceLlm()
        generated = await self.generate_consequence(check["id"], fake_llm)
        self.assertEqual(generated.status_code, 200, generated.text)
        proposal = generated.json()
        self.assertEqual(proposal["proposal_kind"], "check_consequence")
        self.assertEqual(
            proposal["check_consequence"]["origin_proposal_id"],
            origin["id"],
        )
        self.assertEqual(proposal["proposed_checks"], [])
        self.assertEqual(proposal["proposed_map_moves"], [])
        self.assertEqual(fake_llm.calls, 1)
        self.assertIn("verified_check_batch", str(fake_llm.messages))

        retry = await self.generate_consequence(check["id"], fake_llm)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()["id"], proposal["id"])
        self.assertEqual(fake_llm.calls, 1)

        context = await self.client.get(
            f"/kp/proposals/{proposal['id']}/context",
            headers=self.kp_headers,
        )
        self.assertEqual(context.status_code, 200)
        included_kinds = {
            source["kind"] for source in context.json()["included_sources"]
        }
        self.assertIn("verified_check_batch", included_kinds)

        approved = await self.client.post(
            f"/kp/proposals/{proposal['id']}/approve",
            headers=self.kp_headers,
            json={"note": "consequence matches the roll"},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        finalized = await self.client.get(
            f"/player-actions/{action['id']}",
            headers=self.player_headers,
        )
        self.assertEqual(finalized.json()["status"], "resolved")

        blocked_override = await self.client.post(
            f"/checks/{check['id']}/override",
            headers=self.kp_headers,
            json={
                "success_level": "failure",
                "passed": False,
                "reason": "too late",
            },
        )
        self.assertEqual(blocked_override.status_code, 409)

        orphan_check = await self.client.post(
            f"/campaigns/{self.campaign['id']}/checks",
            headers=self.kp_headers,
            json={
                "skill_name": "侦查",
                "difficulty": "regular",
                "target": 60,
                "roller_member_id": self.player["member"]["id"],
                "pc_id": self.pc["id"],
                "proposal_id": origin["id"],
                "player_action_id": action["id"],
            },
        )
        self.assertEqual(orphan_check.status_code, 409, orphan_check.text)

    async def test_player_roll_can_auto_generate_and_approve_consequence(self) -> None:
        _action, _origin, check = await self.prepare_check()
        fake_llm = FakeConsequenceLlm()

        with patch(
            "ai_kp.api.main.OpenAICompatibleClient",
            return_value=fake_llm,
        ):
            result = await self.resolve_check(
                check["id"],
                ones=4,
                tens=2,
                auto_advance=True,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["player_action"]["status"], "resolved")
        self.assertEqual(result["proposal"]["status"], "approved")
        self.assertEqual(result["proposal"]["proposal_kind"], "check_consequence")
        self.assertEqual(fake_llm.calls, 1)

    async def test_player_action_and_check_can_use_durable_background_queue(self) -> None:
        action_response = await self.client.post(
            f"/campaigns/{self.campaign['id']}/actions",
            headers=self.player_headers,
            json={
                "action_text": "我先确认走廊是否安全。",
                "client_action_id": "background-action-001",
                "auto_advance": True,
                "background": True,
            },
        )
        self.assertEqual(action_response.status_code, 200, action_response.text)
        queued_action = action_response.json()
        self.assertEqual(queued_action["status"], "queued")
        self.assertEqual(queued_action["job"]["job_type"], "player_action")

        _action, _origin, check = await self.prepare_check()
        queued_check = await self.resolve_check(
            check["id"],
            ones=4,
            tens=2,
            auto_advance=True,
            background=True,
        )
        self.assertEqual(queued_check["status"], "queued")
        self.assertEqual(queued_check["job"]["job_type"], "check_consequence")

        player_jobs_response = await self.client.get(
            f"/campaigns/{self.campaign['id']}/auto-kp/jobs",
            headers=self.player_headers,
        )
        self.assertEqual(player_jobs_response.status_code, 200)
        player_jobs = player_jobs_response.json()
        self.assertEqual(
            {job["job_type"] for job in player_jobs},
            {"player_action", "check_consequence"},
        )
        self.assertTrue(all("payload" not in job for job in player_jobs))
        self.assertTrue(all("result" not in job for job in player_jobs))
        self.assertTrue(all("last_error" not in job for job in player_jobs))

        kp_jobs_response = await self.client.get(
            f"/campaigns/{self.campaign['id']}/auto-kp/jobs",
            headers=self.kp_headers,
        )
        self.assertEqual(kp_jobs_response.status_code, 200)
        self.assertTrue(all("payload" in job for job in kp_jobs_response.json()))

    async def test_override_or_push_makes_an_old_draft_stale_without_side_effects(self) -> None:
        action, _origin, check = await self.prepare_check()
        resolved = await self.resolve_check(check["id"], ones=4, tens=8)
        self.assertFalse(resolved["passed"])
        fake_llm = FakeConsequenceLlm()
        first = await self.generate_consequence(check["id"], fake_llm)
        self.assertEqual(first.status_code, 200, first.text)
        stale_proposal = first.json()

        pushed_response = await self.client.post(
            f"/checks/{check['id']}/push",
            headers=self.kp_headers,
            json={"reason": "失败会惊动管理员。"},
        )
        self.assertEqual(pushed_response.status_code, 200, pushed_response.text)
        pushed = pushed_response.json()
        still_reviewed = await self.client.get(
            f"/player-actions/{action['id']}",
            headers=self.player_headers,
        )
        self.assertEqual(still_reviewed.json()["status"], "reviewed")

        stale_approval = await self.client.post(
            f"/kp/proposals/{stale_proposal['id']}/approve",
            headers=self.kp_headers,
            json={"note": "must be rejected as stale"},
        )
        self.assertEqual(stale_approval.status_code, 409)
        stale_after = await self.client.get(
            f"/kp/proposals/{stale_proposal['id']}",
            headers=self.kp_headers,
        )
        self.assertEqual(stale_after.json()["status"], "draft")

        await self.resolve_check(pushed["id"], ones=2, tens=1)
        current = await self.generate_consequence(pushed["id"], fake_llm)
        self.assertEqual(current.status_code, 200, current.text)
        self.assertNotEqual(current.json()["id"], stale_proposal["id"])
        self.assertEqual(fake_llm.calls, 2)

        approved = await self.client.post(
            f"/kp/proposals/{current.json()['id']}/approve",
            headers=self.kp_headers,
            json={"note": "final push result"},
        )
        self.assertEqual(approved.status_code, 200, approved.text)

        events = await self.client.get(
            f"/campaigns/{self.campaign['id']}/memory/search",
            headers=self.kp_headers,
            params={"q": "撕碎 收据"},
        )
        self.assertEqual(events.status_code, 200)
        self.assertEqual(
            len(
                [
                    item
                    for item in events.json()
                    if "撕碎的收据" in item["text"]
                ]
            ),
            1,
        )

    async def test_hidden_consequence_redacts_public_text_and_keeps_effects_kp_only(
        self,
    ) -> None:
        _action, _origin, check = await self.prepare_check(hidden=True)
        resolved = await self.resolve_check(
            check["id"],
            ones=4,
            tens=0,
            headers=self.kp_headers,
        )
        self.assertTrue(resolved["hidden"])

        fake_llm = HiddenConsequenceLlm()
        response = await self.generate_consequence(check["id"], fake_llm)

        self.assertEqual(response.status_code, 200, response.text)
        proposal = response.json()
        self.assertEqual(
            proposal["public_narration"],
            HIDDEN_CHECK_PUBLIC_NARRATION,
        )
        self.assertNotIn("04", proposal["public_narration"])
        self.assertIn("04", proposal["kp_notes"])
        self.assertEqual(
            {item["visibility"] for item in proposal["proposed_events"]},
            {"kp"},
        )
        self.assertEqual(
            {item["visibility"] for item in proposal["proposed_memories"]},
            {"kp"},
        )
        serialized_messages = str(fake_llm.messages)
        self.assertIn('"has_hidden_checks": true', serialized_messages)
        self.assertIn(HIDDEN_CHECK_PUBLIC_NARRATION, serialized_messages)

    async def test_hidden_consequence_rejects_leaking_model_effects(self) -> None:
        _action, _origin, check = await self.prepare_check(hidden=True)
        await self.resolve_check(
            check["id"],
            ones=4,
            tens=0,
            headers=self.kp_headers,
        )
        fake_llm = HiddenConsequenceLlm(effect_visibility="table")

        response = await self.generate_consequence(check["id"], fake_llm)

        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(fake_llm.calls, 2)
        proposals = await self.client.get(
            f"/campaigns/{self.campaign['id']}/proposals",
            headers=self.kp_headers,
        )
        self.assertEqual(proposals.status_code, 200, proposals.text)
        self.assertFalse(
            any(
                item["proposal_kind"] == "check_consequence"
                for item in proposals.json()
            )
        )


if __name__ == "__main__":
    unittest.main()

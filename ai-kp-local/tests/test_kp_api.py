import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings, get_settings
from tests.support_investigators import coc7_sheet, create_approved_player


class FakeStructuredLlm:
    async def complete(self, messages, temperature: float = 0.7) -> str:
        return json.dumps(
            {
                "public_narration": "仓库门上有一道新鲜刮痕。",
                "kp_notes": "刮痕来自柜子。",
                "action_ruling": {
                    "goal": "检查仓库门",
                    "method": "近距离观察",
                    "target": "仓库门",
                    "feasibility": "possible",
                    "resolution": "automatic",
                    "reason": "刮痕无需专业能力即可看见。",
                    "maximum_effect": "看到门上的明显刮痕。",
                    "alternative": "",
                },
                "proposed_checks": [],
                "proposed_events": [
                    {
                        "event_type": "clue_seen",
                        "summary": "玩家看到仓库门上的刮痕。",
                        "actor_type": "system",
                        "actor_id": None,
                        "visibility": "table",
                        "happened_at": None,
                        "payload": {},
                    }
                ],
                "proposed_memories": [
                    {
                        "text": "仓库门有新鲜刮痕。",
                        "scope": "clue",
                        "importance": 2,
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


class FakeJumpLlm:
    async def complete(self, messages, temperature: float = 0.7) -> str:
        return json.dumps(
            {
                "public_narration": "你靠近车门，准备从行驶中的列车跳下。",
                "kp_notes": "跳车具有固有重伤风险。",
                "action_ruling": {
                    "goal": "从行驶列车跳下",
                    "method": "直接跃出车门",
                    "target": "列车外地面",
                    "feasibility": "partial",
                    "resolution": "automatic",
                    "reason": "列车长相信亲属说法并交出钥匙，玩家可以安全跳车。",
                    "maximum_effect": "获得钥匙并安全落地。",
                    "alternative": "等待列车减速或寻找制动装置。",
                },
                "proposed_checks": [],
                "proposed_events": [
                    {
                        "event_type": "key_handed_over",
                        "summary": "列车长交出万能钥匙，玩家安全跳车。",
                        "actor_type": "npc",
                        "actor_id": None,
                        "visibility": "table",
                        "happened_at": None,
                        "payload": {},
                    }
                ],
                "proposed_memories": [],
                "proposed_npc_updates": [],
                "proposed_map_moves": [],
                "proposed_facts": [],
            },
            ensure_ascii=False,
        )


class KpApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_dangerous_train_jump_requires_player_confirmed_idea_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                db_path=Path(tmpdir) / "jump-api.sqlite3",
                llm_base_url="http://unused.local/v1",
                llm_api_key="test",
                llm_model="fake-jump",
            )
            app = create_app(settings)
            app.dependency_overrides[get_settings] = lambda: settings
            with patch(
                "ai_kp.api.main.OpenAICompatibleClient", return_value=FakeJumpLlm()
            ):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://test.local"
                ) as client:
                    campaign = (await client.post("/campaigns", json={"title": "常暗之厢"})).json()
                    session = (
                        await client.post(
                            f"/campaigns/{campaign['id']}/sessions",
                            json={"kp_display_name": "Auto KP"},
                        )
                    ).json()
                    kp_headers = {"Authorization": f"Bearer {session['access_token']}"}
                    player = await create_approved_player(
                        client,
                        campaign=campaign,
                        session=session,
                        kp_headers=kp_headers,
                        display_name="Bold Player",
                        sheet=coc7_sheet(
                            "Passenger",
                            skills={"跳跃": 40, "话术": 55, "说服": 60},
                            characteristics={"int": 70},
                        ),
                    )
                    result = (
                        await client.post(
                            f"/campaigns/{campaign['id']}/actions",
                            headers=player["headers"],
                            json={
                                "action_text": "我确认列车长是亲属，然后从行驶列车跳下。",
                                "client_action_id": "dangerous-jump-001",
                                "auto_advance": True,
                            },
                        )
                    ).json()

                    ruling = result["adjudication"]
                    self.assertEqual(ruling["mode"], "skill_check")
                    self.assertEqual(ruling["selected_skill"], "INT")
                    self.assertEqual(ruling["skill_options"][0]["target"], 70)
                    self.assertIn("不保证安全", ruling["skill_options"][0]["reason"])
                    self.assertEqual(result["proposal"]["status"], "draft")
                    self.assertEqual(result["proposal"]["proposed_events"], [])
                    self.assertIn("灵感/INT", result["proposal"]["public_narration"])
                    self.assertIn(
                        "不保证安全",
                        result["proposal"]["action_ruling"]["maximum_effect"],
                    )
                    self.assertEqual(
                        ruling["ruling"], result["proposal"]["action_ruling"]
                    )
                    pending = (
                        await client.get(
                            f"/campaigns/{campaign['id']}/action-adjudications/pending",
                            headers=player["headers"],
                        )
                    ).json()
                    self.assertEqual([item["id"] for item in pending], [ruling["id"]])

                    confirmed = (
                        await client.post(
                            f"/player-actions/{result['player_action']['id']}"
                            "/adjudication/confirm",
                            headers=player["headers"],
                            json={
                                "expected_version": ruling["version"],
                                "selected_skill": "INT",
                            },
                        )
                    ).json()
                    self.assertEqual(confirmed["status"], "awaiting_roll")
                    self.assertEqual(confirmed["checks"][0]["skill_name"], "INT")
                    self.assertEqual(confirmed["checks"][0]["target"], 70)
                    pending = (
                        await client.get(
                            f"/campaigns/{campaign['id']}/action-adjudications/pending",
                            headers=player["headers"],
                        )
                    ).json()
                    self.assertEqual(pending, [])

                    anachronism = (
                        await client.post(
                            f"/campaigns/{campaign['id']}/actions",
                            headers=player["headers"],
                            json={
                                "action_text": "我用 1928 年的智能手机 GPS 和微信定位列车长。",
                                "client_action_id": "anachronism-001",
                                "auto_advance": True,
                            },
                        )
                    ).json()
                    self.assertEqual(
                        anachronism["adjudication"]["mode"],
                        "roleplay_or_clarification",
                    )
                    self.assertIn("当前时代", anachronism["adjudication"]["reason"])
                    self.assertEqual(anachronism["proposal"]["status"], "draft")

                    deception = (
                        await client.post(
                            f"/campaigns/{campaign['id']}/actions",
                            headers=player["headers"],
                            json={
                                "action_text": "我声称列车长是失散亲属，请他直接交出万能钥匙。",
                                "client_action_id": "deception-001",
                                "auto_advance": True,
                            },
                        )
                    ).json()
                    deception_ruling = deception["adjudication"]
                    self.assertEqual(deception_ruling["mode"], "skill_check")
                    self.assertEqual(deception_ruling["selected_skill"], "话术")
                    self.assertEqual(deception_ruling["skill_options"][0]["target"], 55)
                    self.assertIn("不能确认亲属关系为真", deception_ruling["reason"])
                    self.assertEqual(deception["proposal"]["proposed_events"], [])
                    self.assertIn(
                        "不保证交出钥匙",
                        deception_ruling["ruling"]["maximum_effect"],
                    )
                    self.assertEqual(deception["proposal"]["status"], "draft")

    async def test_kp_turn_saves_structured_draft_context_and_approved_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                db_path=Path(tmpdir) / "api.sqlite3",
                llm_base_url="http://unused.local/v1",
                llm_api_key="test",
                llm_model="fake-structured",
            )
            app = create_app(settings)
            app.dependency_overrides[get_settings] = lambda: settings
            with patch(
                "ai_kp.api.main.OpenAICompatibleClient",
                return_value=FakeStructuredLlm(),
            ):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://test.local",
                ) as client:
                    campaign = (
                        await client.post(
                            "/campaigns",
                            json={"title": "雾港 1928", "current_time": "1928-10-03 19:30"},
                        )
                    ).json()
                    session_bundle = (
                        await client.post(
                            f"/campaigns/{campaign['id']}/sessions",
                            json={"kp_display_name": "Test KP"},
                        )
                    ).json()
                    auth_headers = {
                        "Authorization": f"Bearer {session_bundle['access_token']}"
                    }
                    draft_response = await client.post(
                        "/kp/turn",
                        json={
                            "campaign_id": campaign["id"],
                            "player_action": "我检查仓库门。",
                        },
                        headers=auth_headers,
                    )

                    self.assertEqual(draft_response.status_code, 200)
                    draft = draft_response.json()
                    self.assertEqual(draft["status"], "draft")
                    self.assertEqual(draft["proposed_checks"], [])
                    self.assertEqual(draft["proposed_memories"][0]["scope"], "clue")

                    context = (
                        await client.get(
                            f"/kp/proposals/{draft['id']}/context",
                            headers=auth_headers,
                        )
                    ).json()
                    self.assertEqual(context["proposal_id"], draft["id"])
                    self.assertTrue(context["included_sources"])

                    approved = (
                        await client.post(
                        f"/kp/proposals/{draft['id']}/approve",
                            json={"actor": "human_kp", "note": "API integration test"},
                            headers=auth_headers,
                        )
                    ).json()
                    self.assertEqual(approved["status"], "approved")

                    memories = (
                        await client.get(
                            f"/campaigns/{campaign['id']}/memory/search",
                            params={"q": "仓库刮痕"},
                            headers=auth_headers,
                        )
                    ).json()
                    self.assertTrue(any(item["scope"] == "clue" for item in memories))

    async def test_player_action_auto_advance_waits_for_player_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                db_path=Path(tmpdir) / "auto-api.sqlite3",
                llm_base_url="http://unused.local/v1",
                llm_api_key="test",
                llm_model="fake-structured",
            )
            app = create_app(settings)
            app.dependency_overrides[get_settings] = lambda: settings
            with patch(
                "ai_kp.api.main.OpenAICompatibleClient",
                return_value=FakeStructuredLlm(),
            ):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://test.local",
                ) as client:
                    campaign = (
                        await client.post(
                            "/campaigns",
                            json={"title": "雾港自动团"},
                        )
                    ).json()
                    session_bundle = (
                        await client.post(
                            f"/campaigns/{campaign['id']}/sessions",
                            json={"kp_display_name": "Test KP"},
                        )
                    ).json()
                    kp_headers = {
                        "Authorization": f"Bearer {session_bundle['access_token']}"
                    }
                    approved_player = await create_approved_player(
                        client,
                        campaign=campaign,
                        session=session_bundle,
                        kp_headers=kp_headers,
                        display_name="Player",
                        sheet=coc7_sheet("Investigator", skills={"侦查": 60}),
                    )

                    response = await client.post(
                        f"/campaigns/{campaign['id']}/actions",
                        headers=approved_player["headers"],
                        json={
                            "action_text": "我检查仓库门。",
                            "client_action_id": "auto-api-action-001",
                            "auto_advance": True,
                        },
                    )

                    self.assertEqual(response.status_code, 200, response.text)
                    result = response.json()
                    self.assertEqual(result["status"], "awaiting_confirmation")
                    self.assertEqual(result["proposal"]["status"], "draft")
                    self.assertEqual(result["player_action"]["status"], "reviewed")
                    self.assertEqual(
                        result["adjudication"]["mode"], "direct_resolution"
                    )

                    confirmed = (
                        await client.post(
                            f"/player-actions/{result['player_action']['id']}"
                            "/adjudication/confirm",
                            headers=approved_player["headers"],
                            json={
                                "expected_version": result["adjudication"]["version"],
                                "selected_skill": None,
                            },
                        )
                    ).json()
                    self.assertEqual(confirmed["status"], "completed")
                    self.assertEqual(confirmed["proposal"]["status"], "approved")
                    self.assertEqual(confirmed["player_action"]["status"], "resolved")


if __name__ == "__main__":
    unittest.main()

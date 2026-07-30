import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings, get_settings


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


class KpApiTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()

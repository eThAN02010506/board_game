import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings
from tests.support_investigators import coc7_sheet, create_approved_player


class FakeRecapLlm:
    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls += 1
        return json.dumps(self.responses.pop(0), ensure_ascii=False)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class SessionRecapApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_recap_generation_review_idempotency_and_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                db_path=Path(tmpdir) / "recaps.sqlite3",
                llm_base_url="http://unused.local/v1",
                llm_model="fake-recap",
            )
            app = create_app(settings)
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 41234))
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1",
            ) as client:
                campaign = (
                    await client.post("/campaigns", json={"title": "雾港团后摘要"})
                ).json()
                session = (
                    await client.post(
                        f"/campaigns/{campaign['id']}/sessions",
                        json={"kp_display_name": "OldOnes"},
                    )
                ).json()
                kp_headers = bearer(session["access_token"])
                approved = await create_approved_player(
                    client,
                    campaign=campaign,
                    session=session,
                    kp_headers=kp_headers,
                    display_name="林默玩家",
                    sheet=coc7_sheet("林默", occupation="记者"),
                )
                pc_id = approved["pc"]["id"]
                visible_event = (
                    await client.post(
                        f"/campaigns/{campaign['id']}/events",
                        headers=kp_headers,
                        json={
                            "actor_type": "pc",
                            "actor_id": pc_id,
                            "event_type": "clue.discovered",
                            "summary": "林默在档案馆找到港口账簿。",
                            "visibility": "table",
                            "happened_at": "1924-10-03T21:10:00",
                        },
                    )
                ).json()
                secret_event = (
                    await client.post(
                        f"/campaigns/{campaign['id']}/events",
                        headers=kp_headers,
                        json={
                            "actor_type": "kp",
                            "event_type": "truth.hidden",
                            "summary": "账簿其实是邪教故意留下的诱饵。",
                            "visibility": "kp",
                        },
                    )
                ).json()
                fake = FakeRecapLlm(
                    [
                        {
                            "candidates": [
                                {
                                    "text": "林默发现港口账簿记录午夜运货。",
                                    "scope": "clue",
                                    "importance": 4,
                                    "visibility": "player",
                                    "pc_id": pc_id,
                                    "npc_id": None,
                                    "happened_at": "1924-10-03T21:10:00",
                                    "source_event_ids": [visible_event["id"]],
                                    "rationale": "这是可继续调查的关键线索。",
                                },
                                {
                                    "text": "账簿是邪教布置的诱饵。",
                                    "scope": "campaign_fact",
                                    "importance": 5,
                                    "visibility": "table",
                                    "pc_id": None,
                                    "npc_id": None,
                                    "happened_at": None,
                                    "source_event_ids": [secret_event["id"]],
                                    "rationale": "必须保密的幕后真相。",
                                },
                            ]
                        },
                        {"candidates": []},
                    ]
                )
                with patch(
                    "ai_kp.api.main.OpenAICompatibleClient",
                    return_value=fake,
                ):
                    generated = await client.post(
                        f"/sessions/{session['session']['id']}/recaps/generate",
                        headers=kp_headers,
                    )
                    self.assertEqual(generated.status_code, 200)
                    run = generated.json()
                    self.assertEqual(len(run["candidates"]), 2)
                    self.assertEqual(run["candidates"][1]["visibility"], "kp")

                    repeated = await client.post(
                        f"/sessions/{session['session']['id']}/recaps/generate",
                        headers=kp_headers,
                    )
                    self.assertEqual(repeated.json()["id"], run["id"])
                    self.assertEqual(fake.calls, 1)

                    player_forbidden = await client.get(
                        f"/sessions/{session['session']['id']}/recaps/latest",
                        headers=approved["headers"],
                    )
                    self.assertEqual(player_forbidden.status_code, 403)

                    first = run["candidates"][0]
                    approved_response = await client.post(
                        f"/session-recap-candidates/{first['id']}/review",
                        headers=kp_headers,
                        json={
                            "action": "approve",
                            "reason": "KP 核对事件来源无误",
                            "text": "林默确认港口账簿记录了午夜运货。",
                            "importance": 5,
                        },
                    )
                    self.assertEqual(approved_response.status_code, 200)
                    approved_candidate = approved_response.json()
                    self.assertEqual(approved_candidate["status"], "approved")
                    self.assertIsNotNone(approved_candidate["memory_id"])

                    retry = await client.post(
                        f"/session-recap-candidates/{first['id']}/review",
                        headers=kp_headers,
                        json={
                            "action": "approve",
                            "reason": "KP 核对事件来源无误",
                            "text": "林默确认港口账簿记录了午夜运货。",
                            "importance": 5,
                        },
                    )
                    self.assertEqual(retry.status_code, 200)
                    self.assertEqual(
                        retry.json()["memory_id"],
                        approved_candidate["memory_id"],
                    )
                    conflicting = await client.post(
                        f"/session-recap-candidates/{first['id']}/review",
                        headers=kp_headers,
                        json={"action": "reject", "reason": "改变主意"},
                    )
                    self.assertEqual(conflicting.status_code, 409)

                    second = run["candidates"][1]
                    rejected = await client.post(
                        f"/session-recap-candidates/{second['id']}/review",
                        headers=kp_headers,
                        json={"action": "reject", "reason": "暂不进入长期记忆"},
                    )
                    self.assertEqual(rejected.status_code, 200)
                    self.assertEqual(rejected.json()["status"], "rejected")
                    self.assertIsNone(rejected.json()["memory_id"])

                    player_timeline = await client.get(
                        f"/campaigns/{campaign['id']}/memory/timeline",
                        headers=approved["headers"],
                    )
                    self.assertEqual(player_timeline.status_code, 200)
                    self.assertTrue(
                        any(
                            item["id"] == approved_candidate["memory_id"]
                            for item in player_timeline.json()
                        )
                    )

                    await client.post(
                        f"/campaigns/{campaign['id']}/events",
                        headers=kp_headers,
                        json={
                            "actor_type": "pc",
                            "actor_id": pc_id,
                            "event_type": "session.wrap",
                            "summary": "调查员离开档案馆。",
                            "visibility": "table",
                        },
                    )
                    next_run = await client.post(
                        f"/sessions/{session['session']['id']}/recaps/generate",
                        headers=kp_headers,
                    )
                    self.assertEqual(next_run.status_code, 200)
                    self.assertNotEqual(next_run.json()["id"], run["id"])
                    self.assertEqual(fake.calls, 2)

                    closed = await client.post(
                        f"/sessions/{session['session']['id']}/close",
                        headers=kp_headers,
                    )
                    self.assertEqual(closed.status_code, 200)
                    self.assertEqual(fake.calls, 2)


if __name__ == "__main__":
    unittest.main()

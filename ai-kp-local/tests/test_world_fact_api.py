import sqlite3
import tempfile
import unittest
from pathlib import Path

import httpx

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings
from tests.support_investigators import coc7_sheet, create_approved_player


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class WorldFactApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.settings = Settings(db_path=Path(self.tmpdir.name) / "facts.sqlite3")
        self.app = create_app(self.settings)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 45200)),
            base_url="http://127.0.0.1",
        )
        self.campaign = (
            await self.client.post("/campaigns", json={"title": "Fact ledger"})
        ).json()
        self.session = (
            await self.client.post(
                f"/campaigns/{self.campaign['id']}/sessions",
                json={"kp_display_name": "Keeper"},
            )
        ).json()
        self.kp_headers = bearer(self.session["access_token"])
        approved_one = await create_approved_player(
            self.client,
            campaign=self.campaign,
            session=self.session,
            kp_headers=self.kp_headers,
            display_name="Player one",
            sheet=coc7_sheet("Ada"),
        )
        approved_two = await create_approved_player(
            self.client,
            campaign=self.campaign,
            session=self.session,
            kp_headers=self.kp_headers,
            display_name="Player two",
            sheet=coc7_sheet("Basil"),
        )
        self.pc_one = approved_one["pc"]
        self.pc_two = approved_two["pc"]
        self.player_one = approved_one["bundle"]
        self.player_two = approved_two["bundle"]
        self.player_one_headers = approved_one["headers"]
        self.player_two_headers = approved_two["headers"]

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.tmpdir.cleanup()

    async def assert_fact(self, **overrides) -> httpx.Response:
        payload = {
            "fact_type": "canonical_fact",
            "subject": "archive door",
            "predicate": "state",
            "object_text": "locked",
        }
        payload.update(overrides)
        return await self.client.post(
            f"/campaigns/{self.campaign['id']}/facts",
            headers=self.kp_headers,
            json=payload,
        )

    async def test_typed_fact_projection_enforces_kp_player_and_pc_visibility(self) -> None:
        evidence = await self.client.post(
            f"/campaigns/{self.campaign['id']}/events",
            headers=self.kp_headers,
            json={
                "actor_type": "environment",
                "event_type": "door_examined",
                "summary": "Ada examined the archive door.",
            },
        )
        self.assertEqual(evidence.status_code, 200, evidence.text)
        canonical = await self.assert_fact(
            evidence_event_ids=[evidence.json()["id"]],
            source_reference={"kind": "kp_observation"},
        )
        secret = await self.assert_fact(
            fact_type="kp_secret",
            subject="archive door",
            predicate="hidden mechanism",
            object_text="opens when the clock strikes three",
        )
        belief = await self.assert_fact(
            fact_type="character_belief",
            subject="Ada",
            predicate="believes",
            object_text="the archivist has the key",
            pc_id=self.pc_one["id"],
        )
        self.assertEqual(canonical.status_code, 200, canonical.text)
        self.assertEqual(secret.status_code, 200, secret.text)
        self.assertEqual(belief.status_code, 200, belief.text)

        kp_view = await self.client.get(
            f"/campaigns/{self.campaign['id']}/facts",
            headers=self.kp_headers,
        )
        player_one_view = await self.client.get(
            f"/campaigns/{self.campaign['id']}/facts",
            headers=self.player_one_headers,
        )
        player_two_view = await self.client.get(
            f"/campaigns/{self.campaign['id']}/facts",
            headers=self.player_two_headers,
        )

        self.assertEqual(kp_view.status_code, 200)
        self.assertEqual(len(kp_view.json()), 3)
        self.assertEqual(
            {item["fact"]["category"] for item in player_one_view.json()},
            {"canonical_fact", "character_belief"},
        )
        self.assertEqual(
            {item["fact"]["category"] for item in player_two_view.json()},
            {"canonical_fact"},
        )
        player_canonical = next(
            item
            for item in player_one_view.json()
            if item["fact"]["category"] == "canonical_fact"
        )
        self.assertNotIn("source_reference", player_canonical)
        self.assertNotIn("evidence_event_ids", player_canonical)
        self.assertNotIn("asserted_by", player_canonical)
        denied_secret = await self.client.get(
            f"/campaigns/{self.campaign['id']}/facts/{secret.json()['fact_key']}",
            headers=self.player_one_headers,
        )
        self.assertEqual(denied_secret.status_code, 404)

        forged_reserved_event = await self.client.post(
            f"/campaigns/{self.campaign['id']}/events",
            headers=self.kp_headers,
            json={
                "actor_type": "kp",
                "event_type": "world_fact.asserted",
                "summary": "Forged fact",
                "payload": {"anything": "goes"},
            },
        )
        self.assertEqual(forged_reserved_event.status_code, 409)

    async def test_retcon_is_append_only_stale_safe_and_restart_stable(self) -> None:
        created_response = await self.assert_fact()
        self.assertEqual(created_response.status_code, 200, created_response.text)
        created = created_response.json()

        duplicate = await self.assert_fact(object_text="open")
        self.assertEqual(duplicate.status_code, 409)

        stale = await self.client.post(
            (
                f"/campaigns/{self.campaign['id']}/facts/"
                f"{created['fact_key']}/retcon"
            ),
            headers=self.kp_headers,
            json={
                "expected_head_event_id": "evt_stale",
                "reason": "stale correction",
            },
        )
        self.assertEqual(stale.status_code, 409)

        corrected = await self.client.post(
            (
                f"/campaigns/{self.campaign['id']}/facts/"
                f"{created['fact_key']}/retcon"
            ),
            headers=self.kp_headers,
            json={
                "expected_head_event_id": created["event_id"],
                "reason": "The Keeper retracts the premature assertion.",
            },
        )
        self.assertEqual(corrected.status_code, 200, corrected.text)
        correction = corrected.json()
        self.assertTrue(correction["append_only"])
        self.assertEqual(
            correction["retained_revision"]["event_id"],
            created["event_id"],
        )
        self.assertEqual(
            correction["appended_revision"]["fact"]["category"],
            "retconned",
        )

        active = await self.client.get(
            f"/campaigns/{self.campaign['id']}/facts",
            headers=self.kp_headers,
        )
        history = await self.client.get(
            f"/campaigns/{self.campaign['id']}/facts",
            headers=self.kp_headers,
            params={"include_history": True},
        )
        player_history_attempt = await self.client.get(
            f"/campaigns/{self.campaign['id']}/facts",
            headers=self.player_one_headers,
            params={"include_history": True},
        )
        self.assertEqual(active.json(), [])
        self.assertEqual(len(history.json()), 2)
        self.assertEqual(player_history_attempt.json(), [])

        repeated = await self.client.post(
            (
                f"/campaigns/{self.campaign['id']}/facts/"
                f"{created['fact_key']}/retcon"
            ),
            headers=self.kp_headers,
            json={
                "expected_head_event_id": created["event_id"],
                "reason": "duplicate correction",
            },
        )
        self.assertEqual(repeated.status_code, 409)

        replacement = await self.assert_fact(object_text="open")
        self.assertEqual(replacement.status_code, 200, replacement.text)

        await self.client.aclose()
        restarted = create_app(self.settings)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted, client=("127.0.0.1", 45201)),
            base_url="http://127.0.0.1",
        )
        restarted_history = await self.client.get(
            f"/campaigns/{self.campaign['id']}/facts",
            headers=self.kp_headers,
            params={"include_history": True},
        )
        self.assertEqual(restarted_history.status_code, 200)
        self.assertEqual(len(restarted_history.json()), 3)

    async def test_approved_proposed_facts_apply_atomically_and_keep_provenance(self) -> None:
        proposal_response = await self.client.post(
            f"/campaigns/{self.campaign['id']}/proposals",
            headers=self.kp_headers,
            json={
                "player_action": "检查档案室门锁。",
                "public_narration": "门锁留下最近使用过的痕迹。",
                "proposed_facts": [
                    {
                        "fact_type": "canonical_fact",
                        "subject": "archive door",
                        "predicate": "recent use",
                        "object_text": "the lock was used tonight",
                    },
                    {
                        "fact_type": "character_belief",
                        "subject": "Ada",
                        "predicate": "suspects",
                        "object_text": "the archivist returned after closing",
                        "pc_id": self.pc_one["id"],
                    },
                ],
            },
        )
        self.assertEqual(proposal_response.status_code, 200, proposal_response.text)
        proposal = proposal_response.json()
        self.assertEqual(len(proposal["proposed_facts"]), 2)

        approved = await self.client.post(
            f"/kp/proposals/{proposal['id']}/approve",
            headers=self.kp_headers,
            json={"note": "事实类别和角色范围已人工核对"},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(len(approved.json()["applied_facts"]), 2)

        facts = (
            await self.client.get(
                f"/campaigns/{self.campaign['id']}/facts",
                headers=self.kp_headers,
            )
        ).json()
        self.assertEqual(len(facts), 2)
        self.assertTrue(
            all(
                item["source_reference"]["kind"] == "approved_turn_proposal"
                and item["source_reference"]["proposal_id"] == proposal["id"]
                for item in facts
            )
        )

        player_one_facts = (
            await self.client.get(
                f"/campaigns/{self.campaign['id']}/facts",
                headers=self.player_one_headers,
            )
        ).json()
        self.assertEqual(
            {item["fact"]["category"] for item in player_one_facts},
            {"canonical_fact", "character_belief"},
        )
        player_two_facts = (
            await self.client.get(
                f"/campaigns/{self.campaign['id']}/facts",
                headers=self.player_two_headers,
            )
        ).json()
        self.assertEqual(
            {item["fact"]["category"] for item in player_two_facts},
            {"canonical_fact"},
        )

    async def test_fact_conflict_rolls_back_entire_proposal_approval(self) -> None:
        existing = await self.assert_fact(
            subject="archive door",
            predicate="state",
            object_text="locked",
        )
        self.assertEqual(existing.status_code, 200, existing.text)
        proposal = (
            await self.client.post(
                f"/campaigns/{self.campaign['id']}/proposals",
                headers=self.kp_headers,
                json={
                    "player_action": "再次检查门。",
                    "public_narration": "门看起来已经打开。",
                    "proposed_facts": [
                        {
                            "fact_type": "canonical_fact",
                            "subject": "archive door",
                            "predicate": "state",
                            "object_text": "open",
                        }
                    ],
                },
            )
        ).json()

        denied = await self.client.post(
            f"/kp/proposals/{proposal['id']}/approve",
            headers=self.kp_headers,
            json={"note": "attempt conflicting approval"},
        )
        self.assertEqual(denied.status_code, 409, denied.text)
        refreshed = await self.client.get(
            f"/kp/proposals/{proposal['id']}",
            headers=self.kp_headers,
        )
        self.assertEqual(refreshed.json()["status"], "draft")
        with sqlite3.connect(self.settings.db_path) as observer:
            narration_count = observer.execute(
                """
                SELECT COUNT(*) FROM events
                WHERE campaign_id = ? AND event_type = 'kp_turn'
                  AND json_extract(payload_json, '$.proposal_id') = ?
                """,
                (self.campaign["id"], proposal["id"]),
            ).fetchone()[0]
        self.assertEqual(narration_count, 0)

    async def test_unresolved_check_cannot_precommit_proposed_fact(self) -> None:
        response = await self.client.post(
            f"/campaigns/{self.campaign['id']}/proposals",
            headers=self.kp_headers,
            json={
                "player_action": "寻找暗门。",
                "public_narration": "需要进行侦查检定。",
                "proposed_checks": [
                    {
                        "skill": "侦查",
                        "difficulty": "regular",
                        "reason": "寻找隐藏机关",
                    }
                ],
                "proposed_facts": [
                    {
                        "fact_type": "canonical_fact",
                        "subject": "archive",
                        "predicate": "hidden door",
                        "object_text": "exists",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 409, response.text)


if __name__ == "__main__":
    unittest.main()

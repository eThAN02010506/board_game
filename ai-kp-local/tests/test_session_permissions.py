import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

import httpx

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings
from ai_kp.core.db import connect, init_db
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.security.tokens import hash_access_token, hash_join_code
from tests.support_investigators import coc7_sheet, create_approved_player


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class SessionPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "permissions.sqlite3"
        self.settings = Settings(db_path=self.db_path)
        self.app = create_app(self.settings)
        self.transport = httpx.ASGITransport(
            app=self.app,
            client=("127.0.0.1", 41234),
        )
        self.client = httpx.AsyncClient(
            transport=self.transport,
            base_url="http://127.0.0.1",
        )

        self.campaign_a = (
            await self.client.post("/campaigns", json={"title": "Campaign A"})
        ).json()
        self.session_a = (
            await self.client.post(
                f"/campaigns/{self.campaign_a['id']}/sessions",
                json={"kp_display_name": "Keeper A"},
            )
        ).json()
        self.kp_a_headers = bearer(self.session_a["access_token"])
        approved_a = await create_approved_player(
            self.client,
            campaign=self.campaign_a,
            session=self.session_a,
            kp_headers=self.kp_a_headers,
            display_name="Player A",
            sheet=coc7_sheet("Investigator A", skills={"侦查": 60}),
        )
        approved_other = await create_approved_player(
            self.client,
            campaign=self.campaign_a,
            session=self.session_a,
            kp_headers=self.kp_a_headers,
            display_name="Player B",
            sheet=coc7_sheet(
                "Investigator B",
                skills={"图书馆使用": 70},
                characteristics={"dex": 55, "app": 60},
                assets={"cash": 18},
            ),
            assign=False,
        )
        self.pc_a = approved_a["pc"]
        self.pc_other = approved_other["pc"]
        self.other_investigator = approved_other["investigator"]
        self.player_a = approved_a["bundle"]
        self.player_a_headers = approved_a["headers"]
        self.map_a = (
            await self.client.post(
                f"/campaigns/{self.campaign_a['id']}/maps/generate",
                headers=self.kp_a_headers,
                json={
                    "title": "A Map",
                    "prompt": "Start, Hall, Hidden Room",
                    "locations": ["Start", "Hall", "Hidden Room"],
                    "routes": [["Start", "Hall"], ["Start", "Hidden Room"]],
                },
            )
        ).json()
        self.token_a = (
            await self.client.post(
                f"/maps/{self.map_a['id']}/tokens",
                headers=self.kp_a_headers,
                json={
                    "label": "A Token",
                    "location_name": "Start",
                    "actor_type": "pc",
                    "actor_id": self.pc_a["id"],
                },
            )
        ).json()
        self.token_other = (
            await self.client.post(
                f"/maps/{self.map_a['id']}/tokens",
                headers=self.kp_a_headers,
                json={
                    "label": "B Token",
                    "location_name": "Start",
                    "actor_type": "pc",
                    "actor_id": self.pc_other["id"],
                },
            )
        ).json()
        publish_map = await self.client.post(
            f"/maps/{self.map_a['id']}/publish",
            headers=self.kp_a_headers,
            json={
                "expected_revision_id": self.map_a["revision_id"],
                "expected_selected_asset_id": None,
            },
        )
        self.assertEqual(publish_map.status_code, 200)
        self.campaign_b = (
            await self.client.post("/campaigns", json={"title": "Campaign B"})
        ).json()
        self.session_b = (
            await self.client.post(
                f"/campaigns/{self.campaign_b['id']}/sessions",
                json={"kp_display_name": "Keeper B"},
            )
        ).json()
        self.kp_b_headers = bearer(self.session_b["access_token"])
        self.map_b = (
            await self.client.post(
                f"/campaigns/{self.campaign_b['id']}/maps/generate",
                headers=self.kp_b_headers,
                json={
                    "title": "B Secret Map",
                    "prompt": "B-only secret",
                    "locations": ["B Start", "B End"],
                },
            )
        ).json()

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.tmpdir.cleanup()

    async def test_secrets_are_hashed_and_not_interchangeable(self) -> None:
        connection = connect(self.db_path)
        try:
            member = connection.execute(
                "SELECT token_hash FROM session_members WHERE id = ?",
                (self.player_a["member"]["id"],),
            ).fetchone()
            session = connection.execute(
                "SELECT join_code_hash FROM campaign_sessions WHERE id = ?",
                (self.session_a["session"]["id"],),
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(member["token_hash"], hash_access_token(self.player_a["access_token"]))
        self.assertNotEqual(member["token_hash"], self.player_a["access_token"])
        self.assertEqual(session["join_code_hash"], hash_join_code(self.session_a["join_code"]))
        self.assertNotEqual(session["join_code_hash"], self.session_a["join_code"])

        join_code_as_bearer = await self.client.get(
            "/auth/me",
            headers=bearer(self.session_a["join_code"]),
        )
        token_as_join_code = await self.client.post(
            "/sessions/join",
            json={
                "join_code": self.player_a["access_token"],
                "display_name": "Invalid",
            },
        )
        self.assertEqual(join_code_as_bearer.status_code, 401)
        self.assertEqual(token_as_join_code.status_code, 422)

    async def test_memory_timeline_is_evidence_backed_curated_and_player_scoped(
        self,
    ) -> None:
        event = await self.client.post(
            f"/campaigns/{self.campaign_a['id']}/events",
            headers=self.kp_a_headers,
            json={
                "actor_type": "pc",
                "actor_id": self.pc_a["id"],
                "event_type": "clue.discovered",
                "summary": "在旧档案馆找到港口账簿",
                "visibility": "table",
                "happened_at": "1924-10-03T21:10:00",
            },
        )
        self.assertEqual(event.status_code, 200)
        memory = await self.client.post(
            f"/campaigns/{self.campaign_a['id']}/memories",
            headers=self.kp_a_headers,
            json={
                "text": "账簿记录了午夜运货",
                "scope": "pc_side",
                "pc_id": self.pc_a["id"],
                "visibility": "player",
                "importance": 2,
                "happened_at": "1924-10-03T21:10:00",
                "source_event_id": event.json()["id"],
            },
        )
        self.assertEqual(memory.status_code, 200)
        kp_event = await self.client.post(
            f"/campaigns/{self.campaign_a['id']}/events",
            headers=self.kp_a_headers,
            json={
                "actor_type": "kp",
                "event_type": "foreshadowing.secret",
                "summary": "幕后真相不得展示",
                "visibility": "kp",
            },
        )
        secret_sourced_memory = await self.client.post(
            f"/campaigns/{self.campaign_a['id']}/memories",
            headers=self.kp_a_headers,
            json={
                "text": "调查员记得门后传来钟声",
                "scope": "pc_side",
                "pc_id": self.pc_a["id"],
                "visibility": "player",
                "source_event_id": kp_event.json()["id"],
            },
        )
        self.assertEqual(secret_sourced_memory.status_code, 200)

        player_timeline = await self.client.get(
            f"/campaigns/{self.campaign_a['id']}/memory/timeline",
            headers=self.player_a_headers,
        )
        self.assertEqual(player_timeline.status_code, 200)
        projected = next(
            item for item in player_timeline.json() if item["id"] == memory.json()["id"]
        )
        self.assertEqual(projected["classification"], "side")
        self.assertEqual(projected["source_event_summary"], "在旧档案馆找到港口账簿")
        self.assertNotIn("curation_reason", projected)
        redacted = next(
            item
            for item in player_timeline.json()
            if item["id"] == secret_sourced_memory.json()["id"]
        )
        self.assertEqual(redacted["text"], "调查员记得门后传来钟声")
        self.assertIsNone(redacted["source_event_summary"])
        self.assertIsNone(redacted["source_event_type"])

        curated = await self.client.post(
            f"/campaigns/{self.campaign_a['id']}/memories/{memory.json()['id']}/curation",
            headers=self.kp_a_headers,
            json={
                "classification": "clue",
                "importance": 5,
                "hidden": False,
                "reason": "确认是主线证据",
                "expected_head_action_id": None,
            },
        )
        self.assertEqual(curated.status_code, 200)
        stale = await self.client.post(
            f"/campaigns/{self.campaign_a['id']}/memories/{memory.json()['id']}/curation",
            headers=self.kp_a_headers,
            json={
                "classification": "side",
                "importance": 1,
                "hidden": False,
                "reason": "陈旧窗口",
                "expected_head_action_id": None,
            },
        )
        self.assertEqual(stale.status_code, 409)

        hidden = await self.client.post(
            f"/campaigns/{self.campaign_a['id']}/memories/{memory.json()['id']}/curation",
            headers=self.kp_a_headers,
            json={
                "classification": "clue",
                "importance": 5,
                "hidden": True,
                "reason": "暂时隐藏误导线索",
                "expected_head_action_id": curated.json()["id"],
            },
        )
        self.assertEqual(hidden.status_code, 200)
        hidden_from_player = await self.client.get(
            f"/campaigns/{self.campaign_a['id']}/memory/timeline",
            headers=self.player_a_headers,
        )
        self.assertNotIn(memory.json()["id"], {item["id"] for item in hidden_from_player.json()})
        kp_projection = await self.client.get(
            f"/campaigns/{self.campaign_a['id']}/memory/timeline",
            params={"include_hidden": "true"},
            headers=self.kp_a_headers,
        )
        kp_item = next(item for item in kp_projection.json() if item["id"] == memory.json()["id"])
        self.assertTrue(kp_item["hidden"])
        self.assertEqual(kp_item["curation_reason"], "暂时隐藏误导线索")

        restored = await self.client.post(
            f"/campaigns/{self.campaign_a['id']}/memories/{memory.json()['id']}/curation",
            headers=self.kp_a_headers,
            json={
                "classification": "clue",
                "importance": 5,
                "hidden": False,
                "reason": "KP 已确认可以公开",
                "expected_head_action_id": hidden.json()["id"],
            },
        )
        self.assertEqual(restored.status_code, 200)
        restored_for_player = await self.client.get(
            f"/campaigns/{self.campaign_a['id']}/memory/timeline",
            headers=self.player_a_headers,
        )
        restored_item = next(
            item
            for item in restored_for_player.json()
            if item["id"] == memory.json()["id"]
        )
        self.assertEqual(restored_item["classification"], "clue")
        self.assertEqual(restored_item["importance"], 5)
        self.assertNotIn("curation_head_id", restored_item)
        self.assertNotIn("curated_at", restored_item)

        connection = connect(self.db_path)
        try:
            unchanged = connection.execute(
                """
                SELECT scope, importance, text, source_event_id
                FROM memories WHERE id = ?
                """,
                (memory.json()["id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(dict(unchanged), {
            "scope": "pc_side",
            "importance": 2,
            "text": "账簿记录了午夜运货",
            "source_event_id": event.json()["id"],
        })

        cross_campaign = await self.client.get(
            f"/campaigns/{self.campaign_a['id']}/memory/timeline",
            headers=self.kp_b_headers,
        )
        self.assertEqual(cross_campaign.status_code, 404)

    async def test_local_admin_can_reissue_existing_kp_without_replacing_session(self) -> None:
        recovered = await self.client.post(
            f"/campaigns/{self.campaign_a['id']}/sessions/recover-kp",
            json={"kp_display_name": "Keeper A"},
        )
        self.assertEqual(recovered.status_code, 200)
        bundle = recovered.json()
        self.assertEqual(bundle["session"]["id"], self.session_a["session"]["id"])
        self.assertEqual(bundle["member"]["id"], self.session_a["member"]["id"])
        self.assertEqual(bundle["member"]["role"], "kp")

        old_auth = await self.client.get("/auth/me", headers=self.kp_a_headers)
        new_headers = bearer(bundle["access_token"])
        new_auth = await self.client.get("/auth/me", headers=new_headers)
        persisted_map = await self.client.get(
            f"/maps/{self.map_a['id']}",
            params={"view": "kp"},
            headers=new_headers,
        )
        self.assertEqual(old_auth.status_code, 401)
        self.assertEqual(new_auth.status_code, 200)
        self.assertEqual(new_auth.json()["display_name"], "Keeper A")
        self.assertEqual(persisted_map.status_code, 200)

    async def test_player_cannot_read_kp_views_or_cross_campaign_resources(self) -> None:
        proposal = (
            await self.client.post(
                f"/campaigns/{self.campaign_a['id']}/proposals",
                headers=self.kp_a_headers,
                json={
                    "player_action": "Inspect",
                    "public_narration": "Public result",
                    "kp_notes": "KP-only truth",
                },
            )
        ).json()
        module = (
            await self.client.post(
                f"/campaigns/{self.campaign_a['id']}/modules",
                headers=self.kp_a_headers,
                json={
                    "title": "Test Module",
                    "text": (
                        "@visibility=player\nPublic clue.\n\n"
                        "@visibility=player @spoiler=ending\nFuture clue.\n\n"
                        "@visibility=kp\nKP truth."
                    ),
                },
            )
        ).json()

        player_map = await self.client.get(
            f"/maps/{self.map_a['id']}",
            params={"view": "player"},
            headers=self.player_a_headers,
        )
        kp_map_attempt = await self.client.get(
            f"/maps/{self.map_a['id']}",
            params={"view": "kp"},
            headers=self.player_a_headers,
        )
        proposal_attempt = await self.client.get(
            f"/kp/proposals/{proposal['id']}",
            headers=self.player_a_headers,
        )
        context_attempt = await self.client.get(
            f"/kp/proposals/{proposal['id']}/context",
            headers=self.player_a_headers,
        )
        memory_attempt = await self.client.get(
            f"/campaigns/{self.campaign_a['id']}/memory/search",
            params={"q": "truth", "view": "kp"},
            headers=self.player_a_headers,
        )
        cross_campaign_map = await self.client.get(
            f"/maps/{self.map_b['id']}",
            params={"view": "player"},
            headers=self.player_a_headers,
        )
        chunks = await self.client.get(
            f"/modules/{module['id']}/chunks",
            params={"view": "player", "spoiler": "ending"},
            headers=self.player_a_headers,
        )

        self.assertEqual(player_map.status_code, 200)
        self.assertEqual(player_map.json()["prompt"], "")
        self.assertEqual(kp_map_attempt.status_code, 403)
        self.assertEqual(proposal_attempt.status_code, 403)
        self.assertEqual(context_attempt.status_code, 403)
        self.assertEqual(memory_attempt.status_code, 403)
        self.assertEqual(cross_campaign_map.status_code, 404)
        self.assertEqual([chunk["text"] for chunk in chunks.json()], ["Public clue."])

    async def test_player_only_moves_own_token_over_visible_routes(self) -> None:
        own_move = await self.client.post(
            f"/map-tokens/{self.token_a['id']}/move",
            headers=self.player_a_headers,
            json={
                "to_location_name": "Hall",
                "require_route": False,
                "moved_by": "kp:forged-identity",
            },
        )
        other_move = await self.client.post(
            f"/map-tokens/{self.token_other['id']}/move",
            headers=self.player_a_headers,
            json={"to_location_name": "Hall"},
        )

        connection = connect(self.db_path)
        try:
            hidden_location = connection.execute(
                "SELECT id FROM map_locations WHERE map_id = ? AND name = 'Hidden Room'",
                (self.map_a["id"],),
            ).fetchone()
            connection.execute(
                "UPDATE map_locations SET visibility = 'kp' WHERE id = ?",
                (hidden_location["id"],),
            )
            connection.execute(
                """
                UPDATE map_routes SET visibility = 'kp'
                WHERE map_id = ? AND (
                  start_location_id = ? OR end_location_id = ?
                )
                """,
                (self.map_a["id"], hidden_location["id"], hidden_location["id"]),
            )
            connection.commit()
            recorded_move = connection.execute(
                """
                SELECT moved_by FROM map_token_moves
                WHERE token_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1
                """,
                (self.token_a["id"],),
            ).fetchone()
        finally:
            connection.close()

        hidden_move = await self.client.post(
            f"/map-tokens/{self.token_a['id']}/move",
            headers=self.player_a_headers,
            json={"to_location_name": "Hidden Room", "require_route": False},
        )
        current_map = (
            await self.client.get(
                f"/maps/{self.map_a['id']}",
                params={"view": "player"},
                headers=self.player_a_headers,
            )
        ).json()
        own_token = next(token for token in current_map["tokens"] if token["id"] == self.token_a["id"])

        self.assertEqual(own_move.status_code, 200)
        self.assertEqual(own_move.json()["location_name"], "Hall")
        self.assertEqual(other_move.status_code, 403)
        self.assertEqual(hidden_move.status_code, 409)
        self.assertEqual(own_token["location_name"], "Hall")
        self.assertEqual(
            recorded_move["moved_by"],
            f"player:{self.player_a['member']['id']}",
        )

    async def test_player_action_is_idempotent_and_contains_no_kp_draft(self) -> None:
        payload = {
            "action_text": "I inspect the hall.",
            "location": "Hall",
            "map_id": self.map_a["id"],
            "client_action_id": "client-action-0001",
        }
        first = await self.client.post(
            f"/campaigns/{self.campaign_a['id']}/actions",
            headers=self.player_a_headers,
            json=payload,
        )
        second = await self.client.post(
            f"/campaigns/{self.campaign_a['id']}/actions",
            headers=self.player_a_headers,
            json=payload,
        )
        kp_actions = await self.client.get(
            f"/campaigns/{self.campaign_a['id']}/actions",
            headers=self.kp_a_headers,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.json()["id"], first.json()["id"])
        self.assertNotIn("kp_notes", first.json())
        self.assertIsNone(first.json()["proposal_id"])
        self.assertEqual(first.json()["location"], "Start")
        self.assertEqual(len(kp_actions.json()), 1)

    async def test_player_cannot_use_kp_write_endpoints(self) -> None:
        attempts = [
            ("post", f"/campaigns/{self.campaign_a['id']}/pcs", {"name": "Forged"}),
            (
                "post",
                f"/campaigns/{self.campaign_a['id']}/events",
                {"actor_type": "pc", "event_type": "forged", "summary": "forged"},
            ),
            (
                "post",
                f"/campaigns/{self.campaign_a['id']}/memories",
                {"text": "forged", "scope": "campaign_fact"},
            ),
            (
                "post",
                f"/campaigns/{self.campaign_a['id']}/modules",
                {"title": "forged", "text": "secret"},
            ),
            (
                "post",
                f"/campaigns/{self.campaign_a['id']}/maps/generate",
                {"title": "forged", "prompt": "secret"},
            ),
            (
                "post",
                f"/maps/{self.map_a['id']}/tokens",
                {"label": "forged", "location_name": "Start"},
            ),
            (
                "post",
                f"/campaigns/{self.campaign_a['id']}/npcs",
                {"name": "forged"},
            ),
            (
                "post",
                f"/campaigns/{self.campaign_a['id']}/proposals",
                {"player_action": "forged", "public_narration": "forged"},
            ),
            (
                "post",
                "/kp/turn",
                {"campaign_id": self.campaign_a["id"], "player_action": "forged"},
            ),
            ("post", f"/maps/{self.map_a['id']}/unpublish", None),
        ]
        for method, path, payload in attempts:
            with self.subTest(path=path):
                response = await self.client.request(
                    method,
                    path,
                    headers=self.player_a_headers,
                    json=payload,
                )
                self.assertEqual(response.status_code, 403, response.text)

    async def test_player_action_tracks_proposal_review_and_resolution(self) -> None:
        submitted = (
            await self.client.post(
                f"/campaigns/{self.campaign_a['id']}/actions",
                headers=self.player_a_headers,
                json={
                    "action_text": "I listen at the hall door.",
                    "token_id": self.token_a["id"],
                    "map_id": self.map_a["id"],
                    "client_action_id": "lifecycle-action-0001",
                },
            )
        ).json()
        proposal_response = await self.client.post(
            f"/campaigns/{self.campaign_a['id']}/proposals",
            headers=self.kp_a_headers,
            json={
                "player_action_id": submitted["id"],
                "player_action": "client text must not replace the queued action",
                "public_narration": "You hear footsteps beyond the door.",
            },
        )
        self.assertEqual(proposal_response.status_code, 200, proposal_response.text)
        proposal = proposal_response.json()
        reviewed = (
            await self.client.get(
                f"/player-actions/{submitted['id']}",
                headers=self.player_a_headers,
            )
        ).json()
        duplicate_proposal = await self.client.post(
            f"/campaigns/{self.campaign_a['id']}/proposals",
            headers=self.kp_a_headers,
            json={
                "player_action_id": submitted["id"],
                "player_action": "duplicate",
                "public_narration": "duplicate",
            },
        )
        approved = await self.client.post(
            f"/kp/proposals/{proposal['id']}/approve",
            headers=self.kp_a_headers,
            json={"note": "resolved in lifecycle test"},
        )
        resolved = (
            await self.client.get(
                f"/player-actions/{submitted['id']}",
                headers=self.player_a_headers,
            )
        ).json()

        self.assertEqual(proposal["player_action"], submitted["action_text"])
        self.assertEqual(reviewed["status"], "reviewed")
        self.assertEqual(reviewed["proposal_id"], proposal["id"])
        self.assertEqual(duplicate_proposal.status_code, 409)
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(resolved["status"], "resolved")
        self.assertIsNotNone(resolved["resolved_at"])

        second = (
            await self.client.post(
                f"/campaigns/{self.campaign_a['id']}/actions",
                headers=self.player_a_headers,
                json={
                    "action_text": "I wait.",
                    "client_action_id": "lifecycle-action-0002",
                },
            )
        ).json()
        second_proposal = (
            await self.client.post(
                f"/campaigns/{self.campaign_a['id']}/proposals",
                headers=self.kp_a_headers,
                json={
                    "player_action_id": second["id"],
                    "player_action": "I wait.",
                    "public_narration": "The moment passes.",
                },
            )
        ).json()
        rejected = await self.client.post(
            f"/kp/proposals/{second_proposal['id']}/reject",
            headers=self.kp_a_headers,
            json={"note": "not used"},
        )
        rejected_action = (
            await self.client.get(
                f"/player-actions/{second['id']}",
                headers=self.player_a_headers,
            )
        ).json()
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected_action["status"], "rejected")

    async def test_unassigned_player_cannot_bypass_investigator_approval_and_ownership(
        self,
    ) -> None:
        memory = await self.client.post(
            f"/campaigns/{self.campaign_a['id']}/memories",
            headers=self.kp_a_headers,
            json={
                "text": "Private lighthouse recollection",
                "scope": "pc_arc",
                "pc_id": self.pc_a["id"],
                "visibility": "player",
            },
        )
        self.assertEqual(memory.status_code, 200)

        legacy_pc_claim = await self.client.post(
            "/sessions/join",
            json={
                "join_code": self.session_a["join_code"],
                "display_name": "Legacy claimant",
                "pc_id": self.pc_a["id"],
            },
        )
        unassigned = await self.client.post(
            "/sessions/join",
            json={
                "join_code": self.session_a["join_code"],
                "display_name": "Unassigned player",
            },
        )
        unassigned_headers = bearer(unassigned.json()["access_token"])
        memory_attempt = await self.client.get(
            f"/campaigns/{self.campaign_a['id']}/memory/search",
            params={"q": "lighthouse", "view": "player"},
            headers=unassigned_headers,
        )
        legacy_assign = await self.client.post(
            f"/sessions/{self.session_a['session']['id']}/members/"
            f"{unassigned.json()['member']['id']}/assign-pc",
            headers=self.kp_a_headers,
            json={"pc_id": self.pc_other["id"]},
        )
        wrong_owner_assign = await self.client.post(
            f"/sessions/{self.session_a['session']['id']}/members/"
            f"{unassigned.json()['member']['id']}/assign-investigator",
            headers=self.kp_a_headers,
            json={"investigator_id": self.other_investigator["id"]},
        )
        refreshed_identity = await self.client.get("/auth/me", headers=unassigned_headers)
        player_pc_list = await self.client.get(
            f"/campaigns/{self.campaign_a['id']}/pcs",
            headers=self.player_a_headers,
        )

        self.assertEqual(legacy_pc_claim.status_code, 422)
        self.assertEqual(unassigned.status_code, 200)
        self.assertEqual(memory_attempt.status_code, 403)
        self.assertEqual(legacy_assign.status_code, 410)
        self.assertEqual(wrong_owner_assign.status_code, 409)
        self.assertIsNone(refreshed_identity.json()["pc_id"])
        pcs_by_id = {pc["id"]: pc for pc in player_pc_list.json()}
        self.assertEqual(
            next(
                skill["current_value"]
                for skill in pcs_by_id[self.pc_a["id"]]["sheet"]["skills"]
                if skill["display_name"] == "侦查"
            ),
            60,
        )
        self.assertNotIn("sheet", pcs_by_id[self.pc_other["id"]])
        self.assertEqual(pcs_by_id[self.pc_other["id"]]["public_summary"]["cash"], 18)
        self.assertEqual(
            pcs_by_id[self.pc_other["id"]]["public_summary"]["attributes"]["dex"],
            55,
        )
        self.assertEqual(
            pcs_by_id[self.pc_other["id"]]["public_summary"]["attributes"]["app"],
            60,
        )

    async def test_draft_maps_are_invisible_until_published_and_move_history_is_sanitized(self) -> None:
        draft_map = (
            await self.client.post(
                f"/campaigns/{self.campaign_a['id']}/maps/generate",
                headers=self.kp_a_headers,
                json={
                    "title": "Future Chapter",
                    "prompt": "Spoiler Place, Ending",
                    "locations": ["Spoiler Place", "Ending"],
                },
            )
        ).json()
        player_maps_before = await self.client.get(
            f"/campaigns/{self.campaign_a['id']}/maps",
            params={"view": "player"},
            headers=self.player_a_headers,
        )
        draft_direct = await self.client.get(
            f"/maps/{draft_map['id']}",
            params={"view": "player"},
            headers=self.player_a_headers,
        )
        publish = await self.client.post(
            f"/maps/{draft_map['id']}/publish",
            headers=self.kp_a_headers,
            json={
                "expected_revision_id": draft_map["revision_id"],
                "expected_selected_asset_id": None,
            },
        )
        player_maps_after = await self.client.get(
            f"/campaigns/{self.campaign_a['id']}/maps",
            params={"view": "player"},
            headers=self.player_a_headers,
        )
        await self.client.post(
            f"/map-tokens/{self.token_a['id']}/move",
            headers=self.player_a_headers,
            json={"to_location_name": "Hall", "note": "private client note"},
        )
        move_history = await self.client.get(
            f"/map-tokens/{self.token_a['id']}/moves",
            headers=self.player_a_headers,
        )

        self.assertNotIn(draft_map["id"], {item["id"] for item in player_maps_before.json()})
        self.assertEqual(draft_direct.status_code, 404)
        self.assertEqual(publish.status_code, 200)
        self.assertIn(draft_map["id"], {item["id"] for item in player_maps_after.json()})
        self.assertTrue(move_history.json())
        for move in move_history.json():
            self.assertNotIn("moved_by", move)
            self.assertNotIn("note", move)
            self.assertNotIn("token_id", move)

    async def test_rotate_revoke_and_close_invalidate_old_credentials(self) -> None:
        rotated = await self.client.post(
            f"/sessions/{self.session_a['session']['id']}/rotate-join-code",
            headers=self.kp_a_headers,
        )
        old_join = await self.client.post(
            "/sessions/join",
            json={"join_code": self.session_a["join_code"], "display_name": "Old code"},
        )
        new_join = await self.client.post(
            "/sessions/join",
            json={"join_code": rotated.json()["join_code"], "display_name": "New code"},
        )
        revoke = await self.client.post(
            f"/sessions/{self.session_a['session']['id']}/members/"
            f"{self.player_a['member']['id']}/revoke",
            headers=self.kp_a_headers,
        )
        revoked_auth = await self.client.get("/auth/me", headers=self.player_a_headers)
        revoked_code_join = await self.client.post(
            "/sessions/join",
            json={"join_code": rotated.json()["join_code"], "display_name": "Rejoin attempt"},
        )
        close = await self.client.post(
            f"/sessions/{self.session_a['session']['id']}/close",
            headers=self.kp_a_headers,
        )
        closed_kp_auth = await self.client.get("/auth/me", headers=self.kp_a_headers)
        new_player_auth = await self.client.get(
            "/auth/me",
            headers=bearer(new_join.json()["access_token"]),
        )

        self.assertEqual(rotated.status_code, 200)
        self.assertEqual(old_join.status_code, 404)
        self.assertEqual(new_join.status_code, 200)
        self.assertEqual(revoke.status_code, 200)
        self.assertEqual(revoked_auth.status_code, 401)
        self.assertEqual(revoked_code_join.status_code, 404)
        self.assertEqual(close.status_code, 200)
        self.assertEqual(closed_kp_auth.status_code, 401)
        self.assertEqual(new_player_auth.status_code, 401)

    async def test_remote_bootstrap_cannot_be_spoofed_with_forwarded_header(self) -> None:
        remote_transport = httpx.ASGITransport(
            app=self.app,
            client=("203.0.113.15", 49000),
        )
        async with httpx.AsyncClient(
            transport=remote_transport,
            base_url="http://example.test",
        ) as remote_client:
            response = await remote_client.post(
                "/campaigns",
                headers={"X-Forwarded-For": "127.0.0.1"},
                json={"title": "Spoofed campaign"},
            )
            recover = await remote_client.post(
                f"/campaigns/{self.campaign_a['id']}/sessions/recover-kp",
                headers={"X-Forwarded-For": "127.0.0.1"},
                json={"kp_display_name": "Keeper A"},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(recover.status_code, 403)

    async def test_local_bootstrap_can_be_disabled_and_admin_token_is_required_remotely(self) -> None:
        secure_db_path = Path(self.tmpdir.name) / "secure-admin.sqlite3"
        secure_app = create_app(
            Settings(
                db_path=secure_db_path,
                local_admin_enabled=False,
                admin_token="test-admin-secret",
            )
        )
        transport = httpx.ASGITransport(app=secure_app, client=("127.0.0.1", 40123))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            denied = await client.post("/campaigns", json={"title": "Denied"})
            allowed = await client.post(
                "/campaigns",
                headers={"X-AI-KP-Admin-Token": "test-admin-secret"},
                json={"title": "Allowed"},
            )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)


class SessionJoinConcurrencyTests(unittest.TestCase):
    def test_join_code_rotation_cannot_overtake_an_authenticated_join(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "join-rotation.sqlite3"
            join_connection = connect(db_path)
            rotate_connection = connect(db_path)
            try:
                init_db(join_connection)
                init_db(rotate_connection)
                setup_repo = Repository(join_connection)
                campaign = setup_repo.create_campaign("Join race")
                session_bundle = setup_repo.create_campaign_session(campaign["id"])
                join_connection.commit()
                rotate_connection.commit()

                join_read = threading.Event()
                release_join = threading.Event()
                rotation_attempted = threading.Event()
                rotation_lock_acquired = threading.Event()
                join_result: dict = {}
                rotate_result: dict = {}
                errors: list[BaseException] = []

                def join_row_factory(
                    cursor: sqlite3.Cursor,
                    row: tuple[object, ...],
                ) -> sqlite3.Row:
                    result = sqlite3.Row(cursor, row)
                    column_names = result.keys()
                    if "join_code_hash" in column_names and not join_read.is_set():
                        join_read.set()
                        if not release_join.wait(timeout=15):
                            raise TimeoutError("join concurrency test was not released")
                    return result

                def rotate_row_factory(
                    cursor: sqlite3.Cursor,
                    row: tuple[object, ...],
                ) -> sqlite3.Row:
                    result = sqlite3.Row(cursor, row)
                    column_names = result.keys()
                    if "join_code_hash" in column_names:
                        rotation_lock_acquired.set()
                    return result

                def trace_rotation(sql: str) -> None:
                    if sql.strip().upper().startswith("BEGIN IMMEDIATE"):
                        rotation_attempted.set()

                join_connection.row_factory = join_row_factory
                rotate_connection.row_factory = rotate_row_factory
                rotate_connection.set_trace_callback(trace_rotation)

                def join() -> None:
                    try:
                        join_result.update(
                            Repository(join_connection).join_campaign_session(
                                session_bundle["join_code"],
                                display_name="Linearized player",
                            )
                        )
                        join_connection.commit()
                    except BaseException as exc:  # noqa: BLE001 - thread handoff
                        errors.append(exc)
                        join_connection.rollback()

                def rotate() -> None:
                    try:
                        rotate_result.update(
                            Repository(rotate_connection).rotate_session_join_code(
                                session_bundle["session"]["id"]
                            )
                        )
                        rotate_connection.commit()
                    except BaseException as exc:  # noqa: BLE001 - thread handoff
                        errors.append(exc)
                        rotate_connection.rollback()

                join_thread = threading.Thread(target=join)
                rotate_thread = threading.Thread(target=rotate)
                join_thread.start()
                self.assertTrue(join_read.wait(timeout=10))
                rotate_thread.start()
                try:
                    self.assertTrue(rotation_attempted.wait(timeout=10))
                    self.assertFalse(rotation_lock_acquired.wait(timeout=0.25))
                finally:
                    release_join.set()
                join_thread.join(timeout=10)
                rotate_thread.join(timeout=10)

                self.assertFalse(join_thread.is_alive())
                self.assertFalse(rotate_thread.is_alive())
                self.assertEqual(errors, [])
                self.assertTrue(join_result["access_token"])
                self.assertTrue(rotate_result["join_code"])
                self.assertIsNotNone(
                    Repository(join_connection).authenticate_access_token(
                        join_result["access_token"]
                    )
                )

                join_connection.rollback()
                with self.assertRaises(KeyError):
                    Repository(join_connection).join_campaign_session(
                        session_bundle["join_code"],
                        display_name="Retired code",
                    )
            finally:
                join_connection.close()
                rotate_connection.close()


class DatabaseMigrationTests(unittest.TestCase):
    def test_init_db_upgrades_old_map_and_proposal_tables_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.sqlite3"
            connection = connect(db_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE maps (
                      id TEXT PRIMARY KEY,
                      campaign_id TEXT NOT NULL,
                      title TEXT NOT NULL,
                      prompt TEXT NOT NULL DEFAULT '',
                      style TEXT NOT NULL DEFAULT 'investigation',
                      width INTEGER NOT NULL DEFAULT 960,
                      height INTEGER NOT NULL DEFAULT 640,
                      svg_text TEXT NOT NULL,
                      created_by TEXT NOT NULL DEFAULT 'ai',
                      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE turn_proposals (
                      id TEXT PRIMARY KEY,
                      campaign_id TEXT NOT NULL,
                      pc_id TEXT,
                      status TEXT NOT NULL DEFAULT 'draft',
                      player_action TEXT NOT NULL,
                      public_narration TEXT NOT NULL,
                      kp_notes TEXT NOT NULL DEFAULT '',
                      proposed_events_json TEXT NOT NULL DEFAULT '[]',
                      proposed_memories_json TEXT NOT NULL DEFAULT '[]',
                      proposed_map_moves_json TEXT NOT NULL DEFAULT '[]',
                      source_model TEXT NOT NULL DEFAULT 'unknown',
                      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                      applied_at TEXT
                    );
                    """
                )
                init_db(connection)
                init_db(connection)
                map_columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(maps)")
                }
                proposal_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(turn_proposals)")
                }
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            finally:
                connection.close()

        self.assertIn("status", map_columns)
        self.assertIn("proposed_checks_json", proposal_columns)
        self.assertIn("proposed_npc_updates_json", proposal_columns)
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()

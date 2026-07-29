import asyncio
import tempfile
import unittest
from pathlib import Path

import httpx

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings
from ai_kp.core.db import connect, init_db
from ai_kp.core.repository import Repository


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class SessionSeatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "seats.sqlite3"
        self.app = create_app(Settings(db_path=self.db_path))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 43100)),
            base_url="http://127.0.0.1",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.tmpdir.cleanup()

    async def create_campaign_session(self, title: str) -> tuple[dict, dict]:
        campaign_response = await self.client.post("/campaigns", json={"title": title})
        self.assertEqual(campaign_response.status_code, 200)
        campaign = campaign_response.json()
        session_response = await self.client.post(
            f"/campaigns/{campaign['id']}/sessions",
            json={"kp_display_name": f"{title} KP"},
        )
        self.assertEqual(session_response.status_code, 200)
        return campaign, session_response.json()

    async def create_seat(self, session: dict, label: str) -> dict:
        response = await self.client.post(
            f"/sessions/{session['session']['id']}/seats",
            headers=bearer(session["access_token"]),
            json={"label": label},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_legacy_direct_pc_assignment_contract_is_rejected(self) -> None:
        campaign, session = await self.create_campaign_session("Approval boundary")
        session_id = session["session"]["id"]
        kp_headers = bearer(session["access_token"])

        create_with_pc = await self.client.post(
            f"/sessions/{session_id}/seats",
            headers=kp_headers,
            json={"label": "Legacy seat", "pc_id": "pc-legacy"},
        )
        self.assertEqual(create_with_pc.status_code, 422, create_with_pc.text)
        self.assertTrue(
            any(
                error.get("type") == "extra_forbidden"
                and error.get("loc") == ["body", "pc_id"]
                for error in create_with_pc.json()["detail"]
            )
        )

        seat = await self.create_seat(session, "Approved investigator required")
        connection = connect(self.db_path)
        try:
            init_db(connection)
            legacy_pc = Repository(connection).create_pc(
                campaign["id"],
                "未经审核的旧角色",
                {},
            )
            connection.execute(
                "UPDATE session_seats SET assigned_pc_id = ? WHERE id = ?",
                (legacy_pc["id"], seat["seat"]["id"]),
            )
            connection.commit()
        finally:
            connection.close()
        legacy_claim = await self.client.post(
            "/session-seats/claim",
            json={
                "invitation_code": seat["invitation_code"],
                "display_name": "Legacy claimant",
            },
        )
        self.assertEqual(legacy_claim.status_code, 409, legacy_claim.text)
        self.assertIn("KP-approved investigator", legacy_claim.json()["detail"])

        direct_assign = await self.client.patch(
            f"/sessions/{session_id}/seats/{seat['seat']['id']}/pc",
            headers=kp_headers,
            json={"pc_id": "pc-legacy"},
        )
        self.assertEqual(direct_assign.status_code, 410, direct_assign.text)
        self.assertIn("approved investigator", direct_assign.json()["detail"])

    async def test_single_use_invitations_stable_recovery_and_isolated_revocation(self) -> None:
        _campaign_a, session_a = await self.create_campaign_session("Mist Harbour")
        seat_a = await self.create_seat(session_a, "Alice")
        seat_b = await self.create_seat(session_a, "Bob")

        claim_a_response = await self.client.post(
            "/session-seats/claim",
            json={
                "invitation_code": seat_a["invitation_code"],
                "display_name": "Alice Player",
            },
        )
        self.assertEqual(claim_a_response.status_code, 200, claim_a_response.text)
        claim_a = claim_a_response.json()
        self.assertEqual(claim_a["seat"]["status"], "claimed")
        self.assertEqual(claim_a["member"]["display_name"], "Alice Player")
        self.assertIn("player_token", claim_a)

        replay = await self.client.post(
            "/session-seats/claim",
            json={
                "invitation_code": seat_a["invitation_code"],
                "display_name": "Intruder",
            },
        )
        self.assertEqual(replay.status_code, 404)

        identity = await self.client.get(
            "/auth/me", headers=bearer(claim_a["access_token"])
        )
        self.assertEqual(identity.status_code, 200)
        self.assertEqual(identity.json()["player_profile_id"], claim_a["profile"]["id"])
        self.assertEqual(identity.json()["seat_id"], seat_a["seat"]["id"])

        player_a_headers = {"X-AI-KP-Player-Token": claim_a["player_token"]}
        recovered_response = await self.client.post(
            f"/session-seats/{seat_a['seat']['id']}/recover",
            headers=player_a_headers,
        )
        self.assertEqual(recovered_response.status_code, 200, recovered_response.text)
        recovered = recovered_response.json()
        self.assertNotEqual(recovered["access_token"], claim_a["access_token"])
        old_access = await self.client.get(
            "/auth/me", headers=bearer(claim_a["access_token"])
        )
        self.assertEqual(old_access.status_code, 401)
        self.assertEqual(
            (await self.client.get("/auth/me", headers=bearer(recovered["access_token"]))).status_code,
            200,
        )

        claim_b_response = await self.client.post(
            "/session-seats/claim",
            json={
                "invitation_code": seat_b["invitation_code"],
                "display_name": "Bob Player",
            },
        )
        self.assertEqual(claim_b_response.status_code, 200, claim_b_response.text)
        claim_b = claim_b_response.json()

        revoked = await self.client.post(
            f"/sessions/{session_a['session']['id']}/seats/{seat_a['seat']['id']}/revoke",
            headers=bearer(session_a["access_token"]),
        )
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(revoked.json()["status"], "revoked")
        self.assertEqual(
            (await self.client.get("/auth/me", headers=bearer(recovered["access_token"]))).status_code,
            401,
        )
        self.assertEqual(
            (await self.client.get("/auth/me", headers=bearer(claim_b["access_token"]))).status_code,
            200,
        )
        still_has_profile = await self.client.get(
            "/player-profile", headers=player_a_headers
        )
        self.assertEqual(still_has_profile.status_code, 200)

        _campaign_b, session_b = await self.create_campaign_session("Second Story")
        seat_c = await self.create_seat(session_b, "Returning Alice")
        returning_response = await self.client.post(
            "/session-seats/claim",
            headers=player_a_headers,
            json={
                "invitation_code": seat_c["invitation_code"],
                "display_name": "Ignored Alias",
            },
        )
        self.assertEqual(returning_response.status_code, 200, returning_response.text)
        returning = returning_response.json()
        self.assertNotIn("player_token", returning)
        self.assertEqual(returning["profile"]["id"], claim_a["profile"]["id"])
        self.assertEqual(returning["member"]["display_name"], "Alice Player")

        owned_seats_response = await self.client.get(
            "/player-profile/session-seats", headers=player_a_headers
        )
        self.assertEqual(owned_seats_response.status_code, 200)
        owned_seats = owned_seats_response.json()
        self.assertEqual({seat["id"] for seat in owned_seats}, {
            seat_a["seat"]["id"],
            seat_c["seat"]["id"],
        })
        self.assertEqual(
            {seat["status"] for seat in owned_seats}, {"revoked", "claimed"}
        )

    async def test_reissue_invalidates_only_the_previous_open_invitation(self) -> None:
        _campaign, session = await self.create_campaign_session("Invitation Rotation")
        original = await self.create_seat(session, "Reserved seat")
        reissue_response = await self.client.post(
            f"/sessions/{session['session']['id']}/seats/{original['seat']['id']}/reissue",
            headers=bearer(session["access_token"]),
        )
        self.assertEqual(reissue_response.status_code, 200, reissue_response.text)
        replacement = reissue_response.json()
        self.assertNotEqual(replacement["invitation_code"], original["invitation_code"])

        expired = await self.client.post(
            "/session-seats/claim",
            json={
                "invitation_code": original["invitation_code"],
                "display_name": "Old Link",
            },
        )
        self.assertEqual(expired.status_code, 404)
        accepted = await self.client.post(
            "/session-seats/claim",
            json={
                "invitation_code": replacement["invitation_code"],
                "display_name": "New Link",
            },
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        accepted_bundle = accepted.json()

        legacy_revoke = await self.client.post(
            f"/sessions/{session['session']['id']}/members/{accepted_bundle['member']['id']}/revoke",
            headers=bearer(session["access_token"]),
        )
        self.assertEqual(legacy_revoke.status_code, 200, legacy_revoke.text)

        listed = await self.client.get(
            f"/sessions/{session['session']['id']}/seats",
            headers=bearer(session["access_token"]),
        )
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn("invitation_code", listed.json()[0])
        self.assertEqual(listed.json()[0]["status"], "revoked")

    async def test_one_profile_cannot_concurrently_claim_two_session_seats(self) -> None:
        _campaign, session = await self.create_campaign_session("Concurrent Seats")
        first = await self.create_seat(session, "First")
        second = await self.create_seat(session, "Second")
        profile_bundle = (
            await self.client.post(
                "/player-profiles",
                json={"display_name": "Stable Player"},
            )
        ).json()
        profile_headers = {
            "X-AI-KP-Player-Token": profile_bundle["player_token"]
        }

        async def claim(invitation_code: str) -> httpx.Response:
            return await self.client.post(
                "/session-seats/claim",
                headers=profile_headers,
                json={
                    "invitation_code": invitation_code,
                    "display_name": "Ignored Alias",
                },
            )

        responses = await asyncio.gather(
            claim(first["invitation_code"]),
            claim(second["invitation_code"]),
        )
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])

        listed = await self.client.get(
            f"/sessions/{session['session']['id']}/seats",
            headers=bearer(session["access_token"]),
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            sorted(seat["status"] for seat in listed.json()),
            ["claimed", "open"],
        )
        claimed = next(seat for seat in listed.json() if seat["status"] == "claimed")
        self.assertEqual(
            claimed["player_profile_id"],
            profile_bundle["profile"]["id"],
        )

        failed_index = next(
            index for index, response in enumerate(responses) if response.status_code == 409
        )
        still_active_code = (
            first["invitation_code"]
            if failed_index == 0
            else second["invitation_code"]
        )
        recovered_claim = await self.client.post(
            "/session-seats/claim",
            json={
                "invitation_code": still_active_code,
                "display_name": "Another Player",
            },
        )
        self.assertEqual(recovered_claim.status_code, 200, recovered_claim.text)


if __name__ == "__main__":
    unittest.main()

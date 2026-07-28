import tempfile
import unittest
from pathlib import Path

import httpx

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings
from ai_kp.rules.dice import resolve_d100, success_level


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class DiceRuleTests(unittest.TestCase):
    def test_success_levels_and_fumble_boundary_match_rulebook(self) -> None:
        self.assertEqual(success_level(60, 1), "critical")
        self.assertEqual(success_level(60, 12), "extreme")
        self.assertEqual(success_level(60, 30), "hard")
        self.assertEqual(success_level(60, 60), "regular")
        self.assertEqual(success_level(60, 96), "failure")
        self.assertEqual(success_level(60, 100), "fumble")
        self.assertEqual(success_level(40, 96), "fumble")
        self.assertEqual(success_level(0, 1), "critical")

    def test_bonus_and_penalty_dice_replay_from_shared_ones_digit(self) -> None:
        bonus = resolve_d100(
            target=60,
            difficulty="hard",
            bonus_dice=1,
            ones_digit=4,
            tens_digits=(4, 2),
        )
        penalty = resolve_d100(
            target=60,
            difficulty="hard",
            bonus_dice=-1,
            ones_digit=4,
            tens_digits=(4, 2),
        )

        self.assertEqual(bonus.candidates, (44, 24))
        self.assertEqual(bonus.selected_roll, 24)
        self.assertTrue(bonus.passed)
        self.assertEqual(penalty.selected_roll, 44)
        self.assertFalse(penalty.passed)


class SkillCheckApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "checks.sqlite3"
        self.settings = Settings(db_path=self.db_path)
        self.app = create_app(self.settings)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=("127.0.0.1", 44100)),
            base_url="http://127.0.0.1",
        )
        self.campaign = (await self.client.post(
            "/campaigns", json={"title": "Check real case"}
        )).json()
        self.session = (await self.client.post(
            f"/campaigns/{self.campaign['id']}/sessions",
            json={"kp_display_name": "Keeper"},
        )).json()
        self.kp_headers = bearer(self.session["access_token"])
        self.pc = (await self.client.post(
            f"/campaigns/{self.campaign['id']}/pcs",
            headers=self.kp_headers,
            json={"name": "Investigator", "sheet": {"侦查": 60}},
        )).json()
        self.player = (await self.client.post(
            "/sessions/join",
            json={
                "join_code": self.session["join_code"],
                "display_name": "Player",
                "pc_id": self.pc["id"],
            },
        )).json()
        self.player_headers = bearer(self.player["access_token"])

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.tmpdir.cleanup()

    async def create_check(self, **overrides) -> dict:
        payload = {
            "skill_name": "侦查",
            "difficulty": "hard",
            "bonus_dice": 1,
            "roller_member_id": self.player["member"]["id"],
            "pc_id": self.pc["id"],
            "target": None,
            "hidden": False,
            "allow_push": True,
        }
        payload.update(overrides)
        response = await self.client.post(
            f"/campaigns/{self.campaign['id']}/checks",
            headers=self.kp_headers,
            json=payload,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_physical_roll_visibility_override_and_restart_replay(self) -> None:
        check = await self.create_check()
        self.assertEqual(check["target"], 60)
        self.assertEqual(check["target_source"], "legacy_pc_sheet")
        self.assertEqual(check["source_reference"]["page_start"], 77)

        player_list = await self.client.get(
            f"/campaigns/{self.campaign['id']}/checks", headers=self.player_headers
        )
        self.assertEqual(player_list.status_code, 200)
        self.assertEqual([item["id"] for item in player_list.json()], [check["id"]])

        resolved_response = await self.client.post(
            f"/checks/{check['id']}/resolve",
            headers=self.player_headers,
            json={"input_method": "physical", "ones_digit": 4, "tens_digits": [4, 2]},
        )
        self.assertEqual(resolved_response.status_code, 200, resolved_response.text)
        resolved = resolved_response.json()
        self.assertEqual(resolved["raw_dice"]["candidates"], [44, 24])
        self.assertEqual(resolved["selected_roll"], 24)
        self.assertEqual(resolved["success_level"], "hard")
        self.assertTrue(resolved["passed"])

        replay = await self.client.post(
            f"/checks/{check['id']}/replay", headers=self.player_headers
        )
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["matches_recorded_result"])

        contradictory_override = await self.client.post(
            f"/checks/{check['id']}/override",
            headers=self.kp_headers,
            json={
                "success_level": "regular",
                "passed": True,
                "reason": "A regular success does not pass this hard check.",
            },
        )
        self.assertEqual(
            contradictory_override.status_code,
            409,
            contradictory_override.text,
        )

        override = await self.client.post(
            f"/checks/{check['id']}/override",
            headers=self.kp_headers,
            json={
                "success_level": "failure",
                "passed": False,
                "reason": "The lens was already broken before the roll.",
            },
        )
        self.assertEqual(override.status_code, 200, override.text)
        self.assertEqual(override.json()["status"], "overridden")
        self.assertEqual(override.json()["original_result"]["success_level"], "hard")

        await self.client.aclose()
        restarted = create_app(self.settings)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted, client=("127.0.0.1", 44101)),
            base_url="http://127.0.0.1",
        )
        restarted_replay = await self.client.post(
            f"/checks/{check['id']}/replay", headers=self.kp_headers
        )
        self.assertEqual(restarted_replay.status_code, 200)
        self.assertTrue(restarted_replay.json()["matches_recorded_result"])
        self.assertEqual(restarted_replay.json()["recorded_status"], "overridden")

    async def test_hidden_checks_and_state_transitions_are_server_authorized(self) -> None:
        hidden = await self.create_check(hidden=True, bonus_dice=0, target=40)
        player_list = await self.client.get(
            f"/campaigns/{self.campaign['id']}/checks", headers=self.player_headers
        )
        self.assertNotIn(hidden["id"], {item["id"] for item in player_list.json()})
        denied = await self.client.post(
            f"/checks/{hidden['id']}/resolve",
            headers=self.player_headers,
            json={"input_method": "digital"},
        )
        self.assertEqual(denied.status_code, 403)
        kp_roll = await self.client.post(
            f"/checks/{hidden['id']}/resolve",
            headers=self.kp_headers,
            json={"input_method": "digital"},
        )
        self.assertEqual(kp_roll.status_code, 200, kp_roll.text)

        failed = await self.create_check(bonus_dice=0, difficulty="regular", target=20)
        failed_response = await self.client.post(
            f"/checks/{failed['id']}/resolve",
            headers=self.player_headers,
            json={"input_method": "physical", "ones_digit": 0, "tens_digits": [8]},
        )
        self.assertEqual(failed_response.status_code, 200)
        self.assertFalse(failed_response.json()["passed"])
        pushed = await self.client.post(
            f"/checks/{failed['id']}/push",
            headers=self.kp_headers,
            json={"reason": "Failure will alert the guard."},
        )
        self.assertEqual(pushed.status_code, 200, pushed.text)
        self.assertEqual(pushed.json()["pushed_from_check_id"], failed["id"])
        self.assertFalse(pushed.json()["allow_push"])

        parent_override = await self.client.post(
            f"/checks/{failed['id']}/override",
            headers=self.kp_headers,
            json={
                "success_level": "failure",
                "passed": False,
                "reason": "The pushed result must remain authoritative.",
            },
        )
        self.assertEqual(parent_override.status_code, 409, parent_override.text)

        pending = await self.create_check(target=50)
        cancelled = await self.client.post(
            f"/checks/{pending['id']}/cancel",
            headers=self.kp_headers,
            json={"reason": "The action became automatic."},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")

    async def test_unresolved_check_proposal_cannot_precommit_world_effects(self) -> None:
        response = await self.client.post(
            f"/campaigns/{self.campaign['id']}/proposals",
            headers=self.kp_headers,
            json={
                "player_action": "I search the room.",
                "public_narration": "Make a Spot Hidden check.",
                "pc_id": self.pc["id"],
                "proposed_checks": [
                    {
                        "skill": "侦查",
                        "difficulty": "regular",
                        "reason": "Find the clue.",
                    }
                ],
                "proposed_events": [
                    {
                        "event_type": "clue_found",
                        "summary": "The clue was found before rolling.",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 409)

        safe_proposal_response = await self.client.post(
            f"/campaigns/{self.campaign['id']}/proposals",
            headers=self.kp_headers,
            json={
                "player_action": "I search the room.",
                "public_narration": "Make a Spot Hidden check.",
                "pc_id": self.pc["id"],
                "proposed_checks": [
                    {
                        "skill": "侦查",
                        "difficulty": "regular",
                        "reason": "Find the clue.",
                    }
                ],
            },
        )
        self.assertEqual(safe_proposal_response.status_code, 200)
        safe_proposal = safe_proposal_response.json()
        approved = await self.client.post(
            f"/kp/proposals/{safe_proposal['id']}/approve",
            headers=self.kp_headers,
            json={"note": "Request the roll without precommitting its result."},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        checks = await self.client.get(
            f"/campaigns/{self.campaign['id']}/checks", headers=self.player_headers
        )
        linked = [
            item for item in checks.json() if item["proposal_id"] == safe_proposal["id"]
        ]
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["status"], "requested")

    async def test_check_origin_cannot_cross_campaign_boundaries(self) -> None:
        foreign_campaign = (
            await self.client.post(
                "/campaigns",
                json={"title": "Foreign check origin"},
            )
        ).json()
        foreign_session = (
            await self.client.post(
                f"/campaigns/{foreign_campaign['id']}/sessions",
                json={"kp_display_name": "Foreign Keeper"},
            )
        ).json()
        foreign_headers = bearer(foreign_session["access_token"])
        foreign_proposal_response = await self.client.post(
            f"/campaigns/{foreign_campaign['id']}/proposals",
            headers=foreign_headers,
            json={
                "player_action": "A different campaign action.",
                "public_narration": "This proposal belongs elsewhere.",
            },
        )
        self.assertEqual(
            foreign_proposal_response.status_code,
            200,
            foreign_proposal_response.text,
        )
        foreign_proposal = foreign_proposal_response.json()
        approved = await self.client.post(
            f"/kp/proposals/{foreign_proposal['id']}/approve",
            headers=foreign_headers,
            json={"note": "Make the foreign origin approved."},
        )
        self.assertEqual(approved.status_code, 200, approved.text)

        crossed = await self.client.post(
            f"/campaigns/{self.campaign['id']}/checks",
            headers=self.kp_headers,
            json={
                "skill_name": "侦查",
                "difficulty": "regular",
                "target": 60,
                "roller_member_id": self.player["member"]["id"],
                "pc_id": self.pc["id"],
                "proposal_id": foreign_proposal["id"],
            },
        )
        self.assertEqual(crossed.status_code, 409, crossed.text)

    async def test_new_campaign_session_cannot_mutate_an_old_session_check(self) -> None:
        old_check = await self.create_check(target=60)
        closed = await self.client.post(
            f"/sessions/{self.session['session']['id']}/close",
            headers=self.kp_headers,
        )
        self.assertEqual(closed.status_code, 200, closed.text)
        replacement_session = (
            await self.client.post(
                f"/campaigns/{self.campaign['id']}/sessions",
                json={"kp_display_name": "Replacement Keeper"},
            )
        ).json()

        crossed = await self.client.post(
            f"/checks/{old_check['id']}/cancel",
            headers=bearer(replacement_session["access_token"]),
            json={"reason": "This check belongs to the previous session."},
        )
        self.assertEqual(crossed.status_code, 403, crossed.text)


if __name__ == "__main__":
    unittest.main()

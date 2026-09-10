import tempfile
import unittest
from pathlib import Path

import httpx

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings
from ai_kp.rules.dice import resolve_d100, success_level
from tests.support_investigators import coc7_sheet, create_approved_player


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
        approved_player = await create_approved_player(
            self.client,
            campaign=self.campaign,
            session=self.session,
            kp_headers=self.kp_headers,
            display_name="Player",
            sheet=coc7_sheet("Investigator", skills={"侦查": 60}),
        )
        self.pc = approved_player["pc"]
        self.player = approved_player["bundle"]
        self.player_headers = approved_player["headers"]

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
        self.assertEqual(check["target_source"], "approved_investigator_revision")
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
        self.assertEqual(resolved["random_evidence"]["schema_version"], "dice-roll.v1")
        self.assertEqual(
            resolved["random_evidence"]["rolls"],
            {"ones_digit": [4], "tens_digits": [4, 2]},
        )
        self.assertEqual(
            len(resolved["random_evidence"]["evidence_fingerprint"]),
            64,
        )
        self.assertEqual(resolved["raw_dice"]["candidates"], [44, 24])
        self.assertEqual(resolved["selected_roll"], 24)
        self.assertEqual(resolved["success_level"], "hard")
        self.assertTrue(resolved["passed"])
        resolved_action = next(
            action for action in resolved["actions"]
            if action["action_type"] == "resolved"
        )
        audit = resolved_action["payload"]
        self.assertEqual(audit["input_method"], "physical")
        self.assertEqual(
            audit["raw_dice"],
            {"ones_digit": 4, "tens_digits": [4, 2], "candidates": [44, 24]},
        )
        self.assertEqual(
            audit["random_evidence"]["evidence_fingerprint"],
            resolved["random_evidence"]["evidence_fingerprint"],
        )
        self.assertEqual(audit["selected_roll"], 24)
        self.assertEqual(audit["target"], 60)
        self.assertEqual(audit["threshold"], 30)
        self.assertEqual(audit["difficulty"], "hard")
        self.assertEqual(audit["bonus_dice"], 1)
        self.assertEqual(audit["success_level"], "hard")
        self.assertTrue(audit["passed"])
        self.assertFalse(audit["hidden"])
        self.assertEqual(len(audit["result_fingerprint"]), 64)

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
        persisted = await self.client.get(
            f"/checks/{check['id']}", headers=self.kp_headers
        )
        persisted_audit = next(
            action["payload"] for action in persisted.json()["actions"]
            if action["action_type"] == "resolved"
        )
        self.assertEqual(persisted_audit["raw_dice"]["candidates"], [44, 24])
        self.assertEqual(
            persisted_audit["result_fingerprint"],
            audit["result_fingerprint"],
        )

    async def test_every_player_sees_public_roll_but_only_assignee_can_roll(
        self,
    ) -> None:
        observer = await create_approved_player(
            self.client,
            campaign=self.campaign,
            session=self.session,
            kp_headers=self.kp_headers,
            display_name="Observer",
            sheet=coc7_sheet("Observer Investigator", skills={"侦查": 45}),
        )
        public_check = await self.create_check(target=60)

        observer_list = await self.client.get(
            f"/campaigns/{self.campaign['id']}/checks",
            headers=observer["headers"],
        )
        self.assertEqual(observer_list.status_code, 200, observer_list.text)
        self.assertIn(
            public_check["id"],
            {item["id"] for item in observer_list.json()},
        )

        denied_roll = await self.client.post(
            f"/checks/{public_check['id']}/resolve",
            headers=observer["headers"],
            json={"input_method": "digital"},
        )
        self.assertEqual(denied_roll.status_code, 403, denied_roll.text)

        resolved = await self.client.post(
            f"/checks/{public_check['id']}/resolve",
            headers=self.player_headers,
            json={"input_method": "physical", "ones_digit": 8, "tens_digits": [3, 6]},
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)

        observer_result = await self.client.get(
            f"/checks/{public_check['id']}",
            headers=observer["headers"],
        )
        self.assertEqual(observer_result.status_code, 200, observer_result.text)
        self.assertEqual(observer_result.json()["selected_roll"], 38)
        self.assertEqual(
            observer_result.json()["raw_dice"]["candidates"],
            [38, 68],
        )

    async def test_check_visibility_matrix_is_enforced_by_the_server(self) -> None:
        observer = await create_approved_player(
            self.client,
            campaign=self.campaign,
            session=self.session,
            kp_headers=self.kp_headers,
            display_name="Visibility Observer",
            sheet=coc7_sheet("Visibility Observer", skills={"侦查": 45}),
        )
        private_check = await self.create_check(
            target=60,
            visibility="private",
        )
        blind_check = await self.create_check(
            target=60,
            visibility="blind",
        )
        async def visible_ids(headers: dict[str, str]) -> set[str]:
            response = await self.client.get(
                f"/campaigns/{self.campaign['id']}/checks",
                headers=headers,
            )
            self.assertEqual(response.status_code, 200, response.text)
            return {item["id"] for item in response.json()}

        kp_ids = await visible_ids(self.kp_headers)
        roller_ids = await visible_ids(self.player_headers)
        observer_ids = await visible_ids(observer["headers"])

        self.assertIn(private_check["id"], kp_ids)
        self.assertIn(private_check["id"], roller_ids)
        self.assertNotIn(private_check["id"], observer_ids)

        self.assertIn(blind_check["id"], kp_ids)
        self.assertNotIn(blind_check["id"], roller_ids)
        self.assertNotIn(blind_check["id"], observer_ids)

        roller_private_read = await self.client.get(
            f"/checks/{private_check['id']}",
            headers=self.player_headers,
        )
        self.assertEqual(roller_private_read.status_code, 200)
        observer_private_read = await self.client.get(
            f"/checks/{private_check['id']}",
            headers=observer["headers"],
        )
        self.assertEqual(observer_private_read.status_code, 403)

        unsupported_player_only = await self.client.post(
            f"/campaigns/{self.campaign['id']}/checks",
            headers=self.kp_headers,
            json={
                "skill_name": "侦查",
                "difficulty": "regular",
                "target": 60,
                "roller_member_id": self.player["member"]["id"],
                "pc_id": self.pc["id"],
                "visibility": "self",
            },
        )
        self.assertEqual(unsupported_player_only.status_code, 422)

    async def test_hidden_checks_and_state_transitions_are_server_authorized(self) -> None:
        hidden = await self.create_check(hidden=True, bonus_dice=0, target=40)
        self.assertFalse(hidden["allow_push"])
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
            headers=self.player_headers,
            json={"reason": "Failure will alert the guard."},
        )
        self.assertEqual(pushed.status_code, 200, pushed.text)
        self.assertEqual(pushed.json()["pushed_from_check_id"], failed["id"])
        self.assertFalse(pushed.json()["allow_push"])

        accepted_failure = await self.create_check(
            bonus_dice=0, difficulty="regular", target=20
        )
        await self.client.post(
            f"/checks/{accepted_failure['id']}/resolve",
            headers=self.player_headers,
            json={"input_method": "physical", "ones_digit": 0, "tens_digits": [8]},
        )
        declined = await self.client.post(
            f"/checks/{accepted_failure['id']}/decline-push",
            headers=self.player_headers,
            json={"reason": "I accept the ordinary failure consequence."},
        )
        self.assertEqual(declined.status_code, 200, declined.text)
        self.assertFalse(declined.json()["allow_push"])
        self.assertEqual(
            declined.json()["push_decision"]["decision"], "accept_failure"
        )
        denied_late_push = await self.client.post(
            f"/checks/{accepted_failure['id']}/push",
            headers=self.player_headers,
            json={"reason": "Changed my mind."},
        )
        self.assertEqual(denied_late_push.status_code, 409)

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

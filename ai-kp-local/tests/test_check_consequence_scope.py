import unittest

from ai_kp.application.check_consequence_service import (
    CheckConsequenceService,
    GenerateCheckConsequenceCommand,
)
from ai_kp.platform.sessions.models import AuthenticatedMember


class ScopeRepo:
    def __init__(self, check: dict, action: dict, checks: list[dict]) -> None:
        self.check = check
        self.action = action
        self.checks = checks

    def get_skill_check(self, check_id: str) -> dict:
        return self.check

    def get_player_action(self, action_id: str) -> dict:
        return self.action

    def list_skill_checks_for_action(self, action_id: str) -> list[dict]:
        return self.checks


class NeverCalledDirector:
    def __init__(self) -> None:
        self.calls = 0

    async def handle_check_consequence(self, **kwargs) -> None:
        self.calls += 1
        raise AssertionError("scope validation must run before the model")


class CheckConsequenceScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_cross_scope_links_before_calling_the_model(self) -> None:
        identity = AuthenticatedMember(
            member_id="member-kp",
            session_id="session-a",
            campaign_id="campaign-a",
            role="kp",
            display_name="Keeper",
            pc_id=None,
        )
        base_check = {
            "id": "check-a",
            "campaign_id": "campaign-a",
            "session_id": "session-a",
            "player_action_id": "action-a",
            "proposal_id": "proposal-a",
        }
        base_action = {
            "id": "action-a",
            "campaign_id": "campaign-a",
            "session_id": "session-a",
            "proposal_id": "proposal-a",
            "status": "reviewed",
        }
        cases = (
            (
                "cross campaign",
                base_check,
                {**base_action, "campaign_id": "campaign-b"},
                [base_check],
            ),
            (
                "cross session",
                base_check,
                {**base_action, "session_id": "session-b"},
                [base_check],
            ),
            (
                "mismatched origin",
                {**base_check, "proposal_id": "proposal-other"},
                base_action,
                [{**base_check, "proposal_id": "proposal-other"}],
            ),
        )

        for label, check, action, checks in cases:
            with self.subTest(label=label):
                director = NeverCalledDirector()
                with self.assertRaisesRegex(
                    ValueError,
                    "campaign session|campaign, session",
                ):
                    await CheckConsequenceService(
                        ScopeRepo(check, action, checks)  # type: ignore[arg-type]
                    ).generate(
                        GenerateCheckConsequenceCommand(check_id="check-a"),
                        identity,
                        director,
                        source_model="must-not-run",
                    )
                self.assertEqual(director.calls, 0)


if __name__ == "__main__":
    unittest.main()

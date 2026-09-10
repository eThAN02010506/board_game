from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_kp.application.auto_turn_service import AutoTurnService
from ai_kp.application.errors import UpstreamServiceError
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import TurnService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository


class UnavailableKernelDirector:
    """Advertise the kernel port while failing at its first model boundary."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def interpret_tabletop_turn(self, **_kwargs):
        self.calls.append("interpret_tabletop_turn")
        raise UpstreamServiceError("model endpoint unavailable")

    async def respond_tabletop_turn(self, **_kwargs):
        raise AssertionError("response must not run after interpretation fails")

    async def select_kernel_action(self, **_kwargs):
        raise AssertionError("selection must not run after interpretation fails")

    async def author_kernel_world_expansion(self, **_kwargs):
        raise AssertionError("expansion must not run after interpretation fails")

    async def narrate_kernel_action(self, **_kwargs):
        raise AssertionError("narration must not run after interpretation fails")

    async def handle_player_action(self, **_kwargs):
        raise AssertionError("one failed model boundary must immediately degrade safely")


def submit_action(repo: Repository, *, client_action_id: str) -> dict:
    campaign = repo.create_campaign("Unavailable model fallback")
    session = SessionService(repo).create(campaign["id"])
    joined = SessionService(repo).join(
        session["join_code"],
        display_name="Player",
    )
    identity = repo.authenticate_access_token(joined["access_token"])
    assert identity is not None
    return TurnService(repo).submit_player_action(
        identity,
        action_text="I inspect the entrance for danger.",
        client_action_id=client_action_id,
    )


class UnboundAutoTurnFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_unbound_preflight_degrades_to_player_review_when_model_is_down(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "unbound-preflight.sqlite3") as connection:
                repo = Repository(connection)
                action = submit_action(repo, client_action_id="unbound-preflight")
                director = UnavailableKernelDirector()

                result = await AutoTurnService(repo).advance_unbound_tabletop(
                    action["id"],
                    director=director,
                    source_model="offline-model",
                )

                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.status, "awaiting_confirmation")
                self.assertEqual(result.proposal["source_model"], "auto-kp-fallback")
                self.assertEqual(
                    result.adjudication["mode"],
                    "roleplay_or_clarification",
                )
                self.assertIn("model endpoint unavailable", result.adjudication["source_error"])
                self.assertEqual(
                    repo.get_player_action(action["id"])["status"],
                    "reviewed",
                )
                self.assertEqual(director.calls, ["interpret_tabletop_turn"])

    async def test_direct_auto_turn_uses_the_same_unbound_safe_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "direct-auto-turn.sqlite3") as connection:
                repo = Repository(connection)
                action = submit_action(repo, client_action_id="direct-auto-turn")
                director = UnavailableKernelDirector()

                result = await AutoTurnService(repo).advance_player_action(
                    action["id"],
                    director=director,
                    source_model="offline-model",
                )

                self.assertEqual(result.status, "awaiting_confirmation")
                self.assertEqual(result.proposal["source_model"], "auto-kp-fallback")
                self.assertEqual(
                    result.adjudication["mode"],
                    "roleplay_or_clarification",
                )
                self.assertEqual(director.calls, ["interpret_tabletop_turn"])


if __name__ == "__main__":
    unittest.main()

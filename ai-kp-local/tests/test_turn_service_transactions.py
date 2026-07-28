import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import ManualProposalCommand, TurnService
from ai_kp.core.db import connect, init_db
from ai_kp.core.repository import Repository


class TurnServiceTransactionTests(unittest.TestCase):
    def _setup_proposal(
        self,
        db_path: Path,
        *,
        proposed_checks: list[dict] | None = None,
    ) -> tuple:
        connection = connect(db_path)
        init_db(connection)
        repo = Repository(connection)
        campaign = repo.create_campaign("Atomic turn service")
        pc = repo.create_pc(
            campaign["id"],
            "Investigator",
            {"侦查": 60, "聆听": 55},
        )
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        player_bundle = sessions.join(
            kp_bundle["join_code"],
            display_name="Player",
            pc_id=pc["id"],
        )
        kp_identity = repo.authenticate_access_token(kp_bundle["access_token"])
        player_identity = repo.authenticate_access_token(player_bundle["access_token"])
        assert kp_identity is not None
        assert player_identity is not None

        service = TurnService(repo)
        action = service.submit_player_action(
            player_identity,
            action_text="I search the archive.",
            client_action_id="atomic-action-001",
        )
        proposal = service.create_manual_proposal(
            campaign["id"],
            kp_identity,
            ManualProposalCommand(
                player_action="This fallback must not win.",
                player_action_id=action["id"],
                public_narration="The archive waits for the Keeper's decision.",
                proposed_checks=proposed_checks or (),
            ),
        )
        connection.commit()
        return (
            connection,
            repo,
            service,
            campaign,
            kp_identity,
            action,
            proposal,
        )

    @staticmethod
    def _state(db_path: Path, *, proposal_id: str, action_id: str) -> dict:
        observer = connect(db_path)
        try:
            proposal = observer.execute(
                "SELECT status FROM turn_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            action = observer.execute(
                "SELECT status FROM player_actions WHERE id = ?",
                (action_id,),
            ).fetchone()
            proposal_actions = observer.execute(
                """
                SELECT action_type FROM proposal_actions
                WHERE proposal_id = ? ORDER BY created_at, id
                """,
                (proposal_id,),
            ).fetchall()
            return {
                "proposal_status": proposal["status"],
                "action_status": action["status"],
                "proposal_actions": tuple(
                    row["action_type"] for row in proposal_actions
                ),
                "world_events": observer.execute(
                    "SELECT COUNT(*) AS total FROM events"
                ).fetchone()["total"],
                "checks": observer.execute(
                    "SELECT COUNT(*) AS total FROM skill_checks"
                ).fetchone()["total"],
                "outbox": observer.execute(
                    "SELECT COUNT(*) AS total FROM realtime_events"
                ).fetchone()["total"],
            }
        finally:
            observer.close()

    def test_approve_rolls_back_when_concrete_check_creation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "approve-check-failure.sqlite3"
            checks = [
                {
                    "skill": "侦查",
                    "difficulty": "regular",
                    "reason": "Find the hidden ledger.",
                },
                {
                    "skill": "聆听",
                    "difficulty": "hard",
                    "reason": "Hear the approaching guard.",
                },
            ]
            (
                connection,
                repo,
                service,
                campaign,
                identity,
                action,
                proposal,
            ) = self._setup_proposal(db_path, proposed_checks=checks)
            baseline = self._state(
                db_path,
                proposal_id=proposal["id"],
                action_id=action["id"],
            )
            create_check = repo.create_skill_check
            create_calls = 0

            def fail_second_check(**values):
                nonlocal create_calls
                create_calls += 1
                if create_calls == 2:
                    raise RuntimeError("injected concrete check failure")
                return create_check(**values)

            try:
                with patch.object(
                    repo,
                    "create_skill_check",
                    side_effect=fail_second_check,
                ), self.assertRaisesRegex(
                    RuntimeError,
                    "injected concrete check failure",
                ):
                    service.approve(
                        proposal["id"],
                        campaign["id"],
                        identity,
                    )
                connection.rollback()
                self.assertEqual(
                    self._state(
                        db_path,
                        proposal_id=proposal["id"],
                        action_id=action["id"],
                    ),
                    baseline,
                )
            finally:
                connection.close()

    def test_approve_rolls_back_when_post_approval_outbox_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "approve-outbox-failure.sqlite3"
            (
                connection,
                repo,
                service,
                campaign,
                identity,
                action,
                proposal,
            ) = self._setup_proposal(db_path)
            baseline = self._state(
                db_path,
                proposal_id=proposal["id"],
                action_id=action["id"],
            )
            append_event = repo.append_realtime_event

            def fail_world_updated(**values):
                if values["event_type"] == "world.updated":
                    raise RuntimeError("injected approval outbox failure")
                return append_event(**values)

            try:
                with patch.object(
                    repo,
                    "append_realtime_event",
                    side_effect=fail_world_updated,
                ), self.assertRaisesRegex(
                    RuntimeError,
                    "injected approval outbox failure",
                ):
                    service.approve(
                        proposal["id"],
                        campaign["id"],
                        identity,
                    )
                connection.rollback()
                self.assertEqual(
                    self._state(
                        db_path,
                        proposal_id=proposal["id"],
                        action_id=action["id"],
                    ),
                    baseline,
                )
            finally:
                connection.close()

    def test_reject_rolls_back_when_outbox_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "reject-outbox-failure.sqlite3"
            (
                connection,
                repo,
                service,
                campaign,
                identity,
                action,
                proposal,
            ) = self._setup_proposal(db_path)
            baseline = self._state(
                db_path,
                proposal_id=proposal["id"],
                action_id=action["id"],
            )
            append_event = repo.append_realtime_event

            def fail_proposal_rejected(**values):
                if values["event_type"] == "proposal.rejected":
                    raise RuntimeError("injected rejection outbox failure")
                return append_event(**values)

            try:
                with patch.object(
                    repo,
                    "append_realtime_event",
                    side_effect=fail_proposal_rejected,
                ), self.assertRaisesRegex(
                    RuntimeError,
                    "injected rejection outbox failure",
                ):
                    service.reject(
                        proposal["id"],
                        campaign["id"],
                        identity,
                    )
                connection.rollback()
                self.assertEqual(
                    self._state(
                        db_path,
                        proposal_id=proposal["id"],
                        action_id=action["id"],
                    ),
                    baseline,
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()

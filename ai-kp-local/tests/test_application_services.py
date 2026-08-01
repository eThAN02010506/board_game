import json
import tempfile
import unittest
from pathlib import Path

from ai_kp.application.auto_turn_service import AutoTurnService
from ai_kp.application.errors import KpSessionEndedError
from ai_kp.application.map_service import (
    GenerateMapCommand,
    MapService,
    MoveTokenCommand,
    PlaceTokenCommand,
)
from ai_kp.application.parallel_action_settlement_service import (
    ParallelActionSettlementCommand,
    ParallelActionSettlementService,
)
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import KpTurnCommand, ManualProposalCommand, TurnService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.director.context_builder import ContextAssembly
from ai_kp.director.orchestrator import KpOrchestrator, KpTurnResult
from ai_kp.director.turn_output import KpTurnOutput
from ai_kp.platform.modules.ingestion import ModuleChunk


class ApplicationServiceTests(unittest.TestCase):
    def test_session_revoke_rotates_code_in_the_same_use_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "services.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("Session service")
                service = SessionService(repo)
                kp_bundle = service.create(campaign["id"])
                player_bundle = service.join(
                    kp_bundle["join_code"],
                    display_name="Player",
                )

                result = service.revoke_member_and_rotate_code(
                    kp_bundle["session"]["id"],
                    player_bundle["member"]["id"],
                )

                self.assertIsNotNone(result["member"]["revoked_at"])
                self.assertNotEqual(result["join_code"], kp_bundle["join_code"])
                with self.assertRaises(KeyError):
                    service.join(kp_bundle["join_code"], display_name="Old code")
                replacement = service.join(result["join_code"], display_name="New code")
                self.assertEqual(replacement["session"]["id"], kp_bundle["session"]["id"])

    def test_map_service_writes_visibility_aware_outbox_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "services.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("Map service")
                session = SessionService(repo).create(campaign["id"])
                session_id = session["session"]["id"]
                service = MapService(repo)
                saved_map = service.generate_and_save(
                    campaign["id"],
                    session_id,
                    GenerateMapCommand(
                        title="Harbour",
                        prompt="Pier, Warehouse",
                        locations=("Pier", "Warehouse"),
                        routes=(("Pier", "Warehouse"),),
                    ),
                )
                token = service.place_token(
                    saved_map["id"],
                    campaign["id"],
                    session_id,
                    PlaceTokenCommand(label="Investigator", location_name="Pier"),
                )
                service.publish(
                    saved_map["id"],
                    campaign["id"],
                    session_id,
                    expected_revision_id=saved_map["revision_id"],
                )
                moved = service.move_token(
                    token["id"],
                    campaign["id"],
                    session_id,
                    MoveTokenCommand(
                        to_location_name="Warehouse",
                        moved_by="kp:test",
                        expected_version=token["version"],
                    ),
                )

                token_events = connection.execute(
                    """
                    SELECT event_type, audience FROM realtime_events
                    WHERE resource_id = ? ORDER BY id
                    """,
                    (token["id"],),
                ).fetchall()
                self.assertEqual(moved["version"], 1)
                self.assertEqual(
                    [(row["event_type"], row["audience"]) for row in token_events],
                    [
                        ("map.token_placed", "kp"),
                        ("map.token_moved", "session"),
                    ],
                )

    def test_map_service_refuses_to_publish_an_invalid_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "invalid-map.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("Invalid map")
                session = SessionService(repo).create(campaign["id"])
                saved_map = MapService(repo).generate_and_save(
                    campaign["id"],
                    session["session"]["id"],
                    GenerateMapCommand(
                        title="Review required",
                        prompt="Entrance, Archive",
                        locations=("Entrance", "Archive"),
                    ),
                )
                connection.execute(
                    "UPDATE map_revisions SET validation_json = ? WHERE id = ?",
                    (
                        json.dumps(
                            {
                                "schema_version": "map-spec.v1",
                                "valid": False,
                                "coverage": {
                                    "required": 2,
                                    "covered": 1,
                                    "percent": 50,
                                },
                                "issues": [],
                            }
                        ),
                        saved_map["revision_id"],
                    ),
                )

                with self.assertRaisesRegex(ValueError, "未通过校验"):
                    MapService(repo).publish(
                        saved_map["id"],
                        campaign["id"],
                        session["session"]["id"],
                        expected_revision_id=saved_map["revision_id"],
                    )

                self.assertEqual(repo.get_map(saved_map["id"])["status"], "draft")

    def test_turn_service_links_and_resolves_queued_player_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "services.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("Turn service")
                sessions = SessionService(repo)
                kp_bundle = sessions.create(campaign["id"])
                player_bundle = sessions.join(
                    kp_bundle["join_code"],
                    display_name="Player",
                )
                kp_identity = repo.authenticate_access_token(kp_bundle["access_token"])
                player_identity = repo.authenticate_access_token(player_bundle["access_token"])
                assert kp_identity is not None
                assert player_identity is not None
                service = TurnService(repo)
                action = service.submit_player_action(
                    player_identity,
                    action_text="I inspect the warehouse.",
                    client_action_id="service-action-001",
                )
                proposal = service.create_manual_proposal(
                    campaign["id"],
                    kp_identity,
                    ManualProposalCommand(
                        player_action="This fallback must not win.",
                        player_action_id=action["id"],
                        public_narration="You find wet footprints.",
                    ),
                )
                approved = service.approve(
                    proposal["id"],
                    campaign["id"],
                    kp_identity,
                    note="service test",
                )

                resolved = repo.get_player_action(action["id"])
                visible_events = connection.execute(
                    """
                    SELECT event_type, audience FROM realtime_events
                    WHERE resource_id IN (?, ?) ORDER BY id
                    """,
                    (action["id"], proposal["id"]),
                ).fetchall()
                self.assertEqual(proposal["player_action"], action["action_text"])
                self.assertEqual(approved["status"], "approved")
                self.assertEqual(resolved["status"], "resolved")
                self.assertIn(("proposal.created", "kp"), map(tuple, visible_events))
                self.assertIn(("proposal.approved", "kp"), map(tuple, visible_events))


class AiTurnServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_ai_turn_revalidates_session_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "services.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("AI turn service")
                session = SessionService(repo).create(campaign["id"])
                identity = repo.authenticate_access_token(session["access_token"])
                assert identity is not None

                class ClosingLlm:
                    async def complete(self, messages, temperature: float = 0.7) -> str:
                        repo.close_campaign_session(identity.session_id)
                        return json.dumps(
                            {
                                "public_narration": "The door opens.",
                                "kp_notes": "No proposal should persist.",
                                "action_ruling": {
                                    "goal": "Open the door",
                                    "method": "Use the handle",
                                    "target": "The door",
                                    "feasibility": "possible",
                                    "resolution": "automatic",
                                    "reason": "The door is unlocked.",
                                    "maximum_effect": "The door opens.",
                                    "alternative": "",
                                },
                                "proposed_checks": [],
                                "proposed_events": [],
                                "proposed_memories": [],
                                "proposed_npc_updates": [],
                                "proposed_map_moves": [],
                            }
                        )

                with self.assertRaises(KpSessionEndedError):
                    await TurnService(repo).create_ai_proposal(
                        KpTurnCommand(
                            campaign_id=campaign["id"],
                            player_action="I open the door.",
                        ),
                        identity,
                        KpOrchestrator(repo.connection, ClosingLlm()),
                        source_model="closing-fake",
                    )
                proposal_count = connection.execute(
                    "SELECT COUNT(*) AS total FROM turn_proposals"
                ).fetchone()
                self.assertEqual(proposal_count["total"], 0)


class ParallelActionSettlementServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_actions_are_linked_and_resolved_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "parallel-actions.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("Parallel actions")
                sessions = SessionService(repo)
                kp_bundle = sessions.create(campaign["id"])
                first = sessions.join(kp_bundle["join_code"], display_name="Ada")
                second = sessions.join(kp_bundle["join_code"], display_name="Bert")
                kp_identity = repo.authenticate_access_token(kp_bundle["access_token"])
                first_identity = repo.authenticate_access_token(first["access_token"])
                second_identity = repo.authenticate_access_token(second["access_token"])
                assert kp_identity is not None
                assert first_identity is not None
                assert second_identity is not None
                turns = TurnService(repo)
                first_action = turns.submit_player_action(
                    first_identity,
                    action_text="I watch the alley.",
                    client_action_id="parallel-action-001",
                )
                second_action = turns.submit_player_action(
                    second_identity,
                    action_text="I check the back door.",
                    client_action_id="parallel-action-002",
                )

                result = await ParallelActionSettlementService(repo).settle(
                    campaign["id"],
                    kp_identity,
                    ParallelActionSettlementCommand(
                        action_ids=(first_action["id"], second_action["id"]),
                    ),
                    FakeTurnDirector("The alley stays quiet while the back door gives way."),
                    source_model="fake-parallel",
                )

                self.assertEqual(result.status, "approved")
                self.assertIsNotNone(result.proposal)
                self.assertIn("多人并行动作统一结算", result.proposal["player_action"])
                self.assertEqual(
                    [repo.get_player_action(item["id"])["status"] for item in result.actions],
                    ["resolved", "resolved"],
                )
                actions = repo.list_proposal_actions(result.proposal["id"])
                self.assertIn(
                    "parallel_action_batch",
                    [item["action_type"] for item in actions],
                )


class AutoTurnServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_turn_conservative_mode_waits_for_human_kp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "auto-turn-conservative.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("Auto turn conservative")
                session = SessionService(repo).create(campaign["id"])
                module = repo.create_module(
                    campaign["id"],
                    "保守模式模组",
                    [
                        ModuleChunk(
                            title="入口",
                            text="门厅里很安静。",
                            visibility="kp",
                            order_index=0,
                        )
                    ],
                    source_type="plaintext",
                )
                repo.start_campaign_module_run(
                    campaign_id=campaign["id"],
                    module_id=module["id"],
                    current_scene_key="entry",
                    active_spoiler_tags=[],
                    state={},
                    started_by_member_id=session["member"]["id"],
                )
                joined = SessionService(repo).join(
                    session["join_code"],
                    display_name="Player",
                )
                player_identity = repo.authenticate_access_token(joined["access_token"])
                assert player_identity is not None
                action = TurnService(repo).submit_player_action(
                    player_identity,
                    action_text="I inspect the entry hall.",
                    client_action_id="auto-action-000",
                )

                result = await AutoTurnService(repo).advance_player_action(
                    action["id"],
                    director=FailingTurnDirector(),
                    source_model="should-not-call",
                )

                self.assertEqual(result.status, "needs_attention")
                self.assertIsNone(result.proposal)
                self.assertEqual(repo.get_player_action(action["id"])["status"], "submitted")

    async def test_auto_turn_approves_standard_ai_proposal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "auto-turn.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("Auto turn")
                session = SessionService(repo).create(campaign["id"])
                joined = SessionService(repo).join(
                    session["join_code"],
                    display_name="Player",
                )
                player_identity = repo.authenticate_access_token(joined["access_token"])
                assert player_identity is not None
                action = TurnService(repo).submit_player_action(
                    player_identity,
                    action_text="I listen at the study door.",
                    client_action_id="auto-action-001",
                )

                result = await AutoTurnService(repo).advance_player_action(
                    action["id"],
                    director=FakeTurnDirector("You hear papers rustle inside."),
                    source_model="fake-auto",
                )

                self.assertEqual(result.status, "completed")
                self.assertEqual(result.proposal["status"], "approved")
                self.assertEqual(repo.get_player_action(action["id"])["status"], "resolved")
                event = connection.execute(
                    "SELECT summary FROM events WHERE campaign_id = ?",
                    (campaign["id"],),
                ).fetchone()
                self.assertIn("papers rustle", event["summary"])

    async def test_auto_turn_falls_back_when_model_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "auto-turn-fallback.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("Auto turn fallback")
                session = SessionService(repo).create(campaign["id"])
                joined = SessionService(repo).join(
                    session["join_code"],
                    display_name="Player",
                )
                player_identity = repo.authenticate_access_token(joined["access_token"])
                assert player_identity is not None
                action = TurnService(repo).submit_player_action(
                    player_identity,
                    action_text="I check whether the window is latched.",
                    client_action_id="auto-action-002",
                )

                result = await AutoTurnService(repo).advance_player_action(
                    action["id"],
                    director=FailingTurnDirector(),
                    source_model="weak-fake",
                )

                self.assertEqual(result.status, "completed")
                self.assertEqual(result.proposal["source_model"], "auto-kp-fallback")
                self.assertEqual(repo.get_player_action(action["id"])["status"], "resolved")


class FakeTurnDirector:
    def __init__(self, narration: str) -> None:
        self.narration = narration

    async def handle_player_action(self, **kwargs):
        output = KpTurnOutput.model_validate(
            {
                "public_narration": self.narration,
                "kp_notes": "fake auto turn",
                "action_ruling": {
                    "goal": kwargs["player_action"],
                    "method": "observe carefully",
                    "target": "current scene",
                    "feasibility": "possible",
                    "resolution": "automatic",
                    "reason": "The action has no immediate obstacle.",
                    "maximum_effect": "Only public narration is applied.",
                    "alternative": "",
                },
                "proposed_checks": [],
                "proposed_events": [],
                "proposed_memories": [],
                "proposed_npc_updates": [],
                "proposed_map_moves": [],
                "proposed_facts": [],
            }
        )
        return KpTurnResult(
            output=output,
            context=ContextAssembly(
                messages=[],
                included_sources=[],
                excluded_sources=[],
                token_estimate=0,
                visibility_scope="kp",
            ),
        )


class FailingTurnDirector:
    async def handle_player_action(self, **kwargs):
        raise RuntimeError("model too weak")


if __name__ == "__main__":
    unittest.main()

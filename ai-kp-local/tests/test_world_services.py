import tempfile
import unittest
from pathlib import Path

from ai_kp.application.campaign_service import CampaignService, CreateCampaignCommand
from ai_kp.application.world_service import (
    AddMemoryCommand,
    AppendEventCommand,
    CreateNpcCommand,
    CreatePcCommand,
    ImportModuleCommand,
    LinkNpcCommand,
    WorldService,
)
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository


class WorldServiceTests(unittest.TestCase):
    def test_campaign_pc_creation_and_realtime_outbox_share_the_use_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "world.sqlite3") as connection:
                repo = Repository(connection)
                campaigns = CampaignService(repo)
                campaign = campaigns.create(
                    CreateCampaignCommand(
                        title="雾港 1928",
                        current_time="1928-10-03 19:30",
                    )
                )
                session = repo.create_campaign_session(campaign["id"])

                pc = WorldService(repo).create_pc(
                    campaign["id"],
                    session["session"]["id"],
                    CreatePcCommand(name="林若川", sheet={"侦查": 65}),
                )

                self.assertEqual(campaigns.list_accessible(campaign["id"]), [campaign])
                self.assertEqual(WorldService(repo).list_pcs(campaign["id"])[0]["sheet"], {"侦查": 65})
                event = connection.execute(
                    "SELECT * FROM realtime_events WHERE resource_id = ?",
                    (pc["id"],),
                ).fetchone()
                self.assertEqual(event["event_type"], "pc.created")
                self.assertEqual(event["audience"], "session")

    def test_module_import_and_chunk_reads_apply_view_and_spoiler_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "modules.sqlite3") as connection:
                repo = Repository(connection)
                campaign = CampaignService(repo).create(CreateCampaignCommand("雾港"))
                service = WorldService(repo)
                module = service.import_module(
                    campaign["id"],
                    ImportModuleCommand(
                        title="第一幕",
                        text=(
                            "@visibility=player\n已公开线索。\n\n"
                            "@visibility=player @spoiler=ending\n未公开线索。\n\n"
                            "@visibility=kp\nKP 真相。"
                        ),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO modules (id, campaign_id, title, source_type)
                    VALUES ('legacy_global_module', NULL, '旧版全局模组', 'plaintext')
                    """
                )

                player_chunks = service.list_module_chunks(
                    module["id"],
                    view="player",
                    spoiler="ending",
                )
                kp_chunks = service.list_module_chunks(
                    module["id"],
                    view="kp",
                    spoiler="ending",
                )

                self.assertEqual(service.get_module(module["id"])["campaign_id"], campaign["id"])
                self.assertEqual(
                    [item["id"] for item in service.list_modules(campaign["id"])],
                    [module["id"]],
                )
                self.assertEqual([item["text"] for item in player_chunks], ["已公开线索。"])
                self.assertEqual(len(kp_chunks), 3)

    def test_event_memory_and_npc_candidate_queries_are_orchestrated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "memory.sqlite3") as connection:
                repo = Repository(connection)
                campaign = CampaignService(repo).create(CreateCampaignCommand("雾港"))
                pc = repo.create_pc(campaign["id"], "林若川")
                service = WorldService(repo)
                event = service.append_event(
                    campaign["id"],
                    AppendEventCommand(
                        actor_type="pc",
                        actor_id=pc["id"],
                        event_type="contact_met",
                        summary="在旧码头见到了报社线人。",
                    ),
                )
                service.add_memory(
                    campaign["id"],
                    AddMemoryCommand(
                        text="林若川认识旧码头的报社线人。",
                        scope="pc_major",
                        pc_id=pc["id"],
                        importance=5,
                        visibility="player",
                        source_event_id=event["id"],
                    ),
                )
                npc = service.create_campaign_npc(
                    campaign["id"],
                    CreateNpcCommand(
                        name="周怀民",
                        home_location="雾港旧码头",
                        profession="报社线人",
                    ),
                )
                linked = service.link_npc(
                    campaign["id"],
                    npc["id"],
                    LinkNpcCommand(
                        first_seen_time="1928-10-03 21:15",
                        relationship_score=2,
                        notes="曾提供走私线索。",
                    ),
                )
                campaign_npc = repo.connection.execute(
                    """
                    SELECT first_seen_time FROM campaign_npcs
                    WHERE campaign_id = ? AND npc_id = ?
                    """,
                    (campaign["id"], npc["id"]),
                ).fetchone()

                memories = service.search_memory(
                    campaign["id"],
                    "旧码头线人",
                    pc_id=pc["id"],
                    view="player",
                )
                candidates = service.npc_candidates(
                    campaign["id"],
                    "寻找行业内打过交道的人",
                    location="旧码头",
                    profession_hint="线人",
                )

                self.assertTrue(linked["ok"])
                self.assertEqual(campaign_npc["first_seen_time"], "1928-10-03 21:15")
                self.assertEqual(memories[0]["scope"], "pc_major")
                self.assertEqual(candidates[0]["npc_id"], npc["id"])
                self.assertGreaterEqual(candidates[0]["score"], 7)


if __name__ == "__main__":
    unittest.main()

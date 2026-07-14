from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository


def seed_demo(db_path: str = "data/ai_kp.sqlite3") -> None:
    with db_session(db_path) as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("雾港 1928", system="coc7", current_time="1928-10-03 19:30")
        pc = repo.create_pc(campaign["id"], "林若川", {"occupation": "记者", "credit_rating": 45})
        npc = repo.create_npc("周怀民", home_location="雾港旧码头", profession="报社线人")
        repo.link_npc_to_campaign(
            campaign_id=campaign["id"],
            npc_id=npc["id"],
            first_seen_time="1928-10-03",
            last_seen_time="1928-10-03",
            relationship_score=3,
            notes="曾把码头走私传闻告诉林若川。",
        )
        event = repo.append_event(
            campaign_id=campaign["id"],
            actor_type="pc",
            actor_id=pc["id"],
            event_type="investigation",
            happened_at="1928-10-03 20:10",
            summary="林若川在旧码头向周怀民打听失踪船员。",
        )
        repo.add_memory(
            campaign_id=campaign["id"],
            pc_id=pc["id"],
            npc_id=npc["id"],
            scope="pc_major",
            importance=4,
            happened_at="1928-10-03 20:10",
            text="林若川曾在旧码头向报社线人周怀民打听失踪船员，两人建立了可再次联系的关系。",
            source_event_id=event["id"],
        )


if __name__ == "__main__":
    seed_demo()


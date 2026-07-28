import tempfile
import unittest
from pathlib import Path

from ai_kp.core.db import db_session
from ai_kp.core.ids import new_id
from ai_kp.core.repository import Repository
from ai_kp.director.context_builder import ContextBuilder
from ai_kp.platform.facts import FactLedgerEntry, WorldFact


def append_fact(
    repo: Repository,
    campaign_id: str,
    *,
    fact_key: str,
    category: str,
    visibility: str,
    object_text: str,
    pc_id: str | None = None,
    revision: int = 1,
    supersedes_event_id: str | None = None,
) -> FactLedgerEntry:
    event_id = new_id("evt")
    return repo.append_fact_entry(
        FactLedgerEntry(
            campaign_id=campaign_id,
            fact_key=fact_key,
            event_id=event_id,
            revision=revision,
            supersedes_event_id=supersedes_event_id,
            asserted_by="kp:test",
            fact=WorldFact(
                fact_id=event_id,
                category=category,
                visibility=visibility,
                subject=fact_key,
                predicate="state",
                object_text=object_text,
                pc_id=pc_id,
                supersedes_fact_id=(
                    supersedes_event_id if category == "retconned" else None
                ),
            ),
        )
    )


class WorldFactContextTests(unittest.TestCase):
    def test_context_uses_current_scoped_heads_and_preserves_epistemic_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "context.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("Fact context")
                pc = repo.create_pc(campaign["id"], "Ada")
                public = append_fact(
                    repo,
                    campaign["id"],
                    fact_key="public-door",
                    category="canonical_fact",
                    visibility="table",
                    object_text="The door is locked.",
                )
                append_fact(
                    repo,
                    campaign["id"],
                    fact_key="public-door",
                    category="retconned",
                    visibility="table",
                    object_text="The door state was recorded too early.",
                    revision=2,
                    supersedes_event_id=public.event_id,
                )
                append_fact(
                    repo,
                    campaign["id"],
                    fact_key="keeper-secret",
                    category="kp_secret",
                    visibility="kp",
                    object_text="The archivist controls the mechanism.",
                )
                append_fact(
                    repo,
                    campaign["id"],
                    fact_key="ada-belief",
                    category="character_belief",
                    visibility="player",
                    object_text="Ada believes the key is in the desk.",
                    pc_id=pc["id"],
                )
                append_fact(
                    repo,
                    campaign["id"],
                    fact_key="ai-guess",
                    category="ai_hypothesis",
                    visibility="kp",
                    object_text="The clock may trigger the lock.",
                )

                kp_context = ContextBuilder(connection).build(
                    campaign_id=campaign["id"],
                    pc_id=pc["id"],
                    player_action="I inspect the archive.",
                    visibility_scope="kp",
                )
                player_context = ContextBuilder(connection).build(
                    campaign_id=campaign["id"],
                    pc_id=pc["id"],
                    player_action="I inspect the archive.",
                    visibility_scope="player",
                )

                kp_facts = [
                    source
                    for source in kp_context.included_sources
                    if source["kind"] == "world_fact"
                ]
                player_facts = [
                    source
                    for source in player_context.included_sources
                    if source["kind"] == "world_fact"
                ]
                self.assertEqual(
                    {source["label"] for source in kp_facts},
                    {
                        "kp_secret:keeper-secret",
                        "character_belief:ada-belief",
                        "ai_hypothesis:ai-guess",
                    },
                )
                self.assertEqual(
                    {source["label"] for source in player_facts},
                    {"character_belief:ada-belief"},
                )
                self.assertNotIn("The door is locked.", str(kp_context.messages))
                self.assertIn("ai_hypothesis", str(kp_context.messages))
                self.assertIn("只是待验证推测", kp_context.messages[0]["content"])


if __name__ == "__main__":
    unittest.main()

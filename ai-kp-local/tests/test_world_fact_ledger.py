import unittest

from ai_kp.platform.facts import (
    FACT_ASSERTED_EVENT,
    FACT_RETCONNED_EVENT,
    FACT_SCHEMA_VERSION,
    FactLedgerEntry,
    WorldFact,
    project_fact_heads,
    visible_fact_heads,
)


def _entry(
    *,
    fact_key: str = "fact-key-1",
    event_id: str = "evt-1",
    revision: int = 1,
    category: str = "canonical_fact",
    visibility: str = "table",
    pc_id: str | None = None,
    supersedes_event_id: str | None = None,
    object_text: str = "The archive door is locked.",
    created_at: str = "2026-01-01 00:00:00",
) -> FactLedgerEntry:
    return FactLedgerEntry(
        campaign_id="camp-1",
        fact_key=fact_key,
        event_id=event_id,
        revision=revision,
        supersedes_event_id=supersedes_event_id,
        asserted_by="kp:member-1",
        fact=WorldFact(
            fact_id=event_id,
            category=category,
            visibility=visibility,
            subject="archive door",
            predicate="state",
            object_text=object_text,
            pc_id=pc_id,
            supersedes_fact_id=(
                supersedes_event_id if category == "retconned" else None
            ),
        ),
        created_at=created_at,
    )


class FactLedgerProjectionTests(unittest.TestCase):
    def test_entry_round_trips_through_authoritative_event_payload(self) -> None:
        entry = _entry()
        event = {
            "id": entry.event_id,
            "campaign_id": entry.campaign_id,
            "event_type": entry.event_type,
            "visibility": entry.storage_visibility,
            "happened_at": None,
            "created_at": entry.created_at,
            "payload": entry.event_payload(),
        }

        restored = FactLedgerEntry.from_event(event)

        self.assertEqual(restored, entry)
        self.assertEqual(restored.event_type, FACT_ASSERTED_EVENT)
        self.assertEqual(restored.event_payload()["schema_version"], FACT_SCHEMA_VERSION)

    def test_character_beliefs_are_stored_kp_only_but_projected_to_the_owner(self) -> None:
        belief = _entry(
            event_id="evt-belief",
            category="character_belief",
            visibility="player",
            pc_id="pc-1",
        )

        self.assertEqual(belief.storage_visibility, "kp")
        self.assertEqual(
            visible_fact_heads([belief], role="player", pc_id="pc-1"),
            [belief],
        )
        self.assertEqual(
            visible_fact_heads([belief], role="player", pc_id="pc-2"),
            [],
        )
        self.assertEqual(visible_fact_heads([belief], role="kp", pc_id=None), [belief])

    def test_retcon_is_the_new_head_without_mutating_the_retained_revision(self) -> None:
        original = _entry()
        correction = _entry(
            event_id="evt-2",
            revision=2,
            category="retconned",
            supersedes_event_id=original.event_id,
            object_text="KP correction: the lock was never engaged.",
            created_at="2026-01-01 00:01:00",
        )

        heads = project_fact_heads([correction, original])

        self.assertEqual(heads, [correction])
        self.assertEqual(correction.event_type, FACT_RETCONNED_EVENT)
        self.assertFalse(correction.active)
        self.assertTrue(original.active)
        self.assertEqual(
            visible_fact_heads(
                [original, correction],
                role="kp",
                pc_id=None,
            ),
            [],
        )
        self.assertEqual(
            visible_fact_heads(
                [original, correction],
                role="kp",
                pc_id=None,
                include_retconned=True,
            ),
            [correction],
        )

    def test_stale_or_forked_revisions_fail_closed(self) -> None:
        original = _entry()
        stale = _entry(
            event_id="evt-3",
            revision=3,
            category="retconned",
            supersedes_event_id=original.event_id,
        )
        with self.assertRaisesRegex(ValueError, "revision gap or fork"):
            project_fact_heads([original, stale])

        branch_a = _entry(
            event_id="evt-2a",
            revision=2,
            category="retconned",
            supersedes_event_id=original.event_id,
        )
        branch_b = _entry(
            event_id="evt-2b",
            revision=2,
            category="retconned",
            supersedes_event_id=original.event_id,
        )
        with self.assertRaisesRegex(ValueError, "revision gap or fork"):
            project_fact_heads([original, branch_a, branch_b])

    def test_parser_rejects_event_type_visibility_and_schema_mismatch(self) -> None:
        entry = _entry()
        base_event = {
            "id": entry.event_id,
            "campaign_id": entry.campaign_id,
            "event_type": FACT_ASSERTED_EVENT,
            "visibility": "table",
            "payload": entry.event_payload(),
        }
        with self.assertRaisesRegex(ValueError, "schema version"):
            FactLedgerEntry.from_event(
                {
                    **base_event,
                    "payload": {
                        **entry.event_payload(),
                        "schema_version": "world-fact.v999",
                    },
                }
            )
        with self.assertRaisesRegex(ValueError, "storage visibility"):
            FactLedgerEntry.from_event({**base_event, "visibility": "kp"})
        with self.assertRaisesRegex(ValueError, "does not match"):
            FactLedgerEntry.from_event(
                {**base_event, "event_type": FACT_RETCONNED_EVENT}
            )


if __name__ == "__main__":
    unittest.main()

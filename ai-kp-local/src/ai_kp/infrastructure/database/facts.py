"""SQLite adapter projecting typed world facts from the authoritative event stream."""

from __future__ import annotations

import json

from ai_kp.infrastructure.database.rows import row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.facts import FactLedgerEntry, project_fact_heads


class FactRepository(SQLiteRepository):
    """Append and project strict fact revisions without creating a second truth table."""

    def list_fact_entries(self, campaign_id: str) -> list[FactLedgerEntry]:
        rows = self.connection.execute(
            """
            SELECT * FROM events
            WHERE campaign_id = ?
              AND event_type IN ('world_fact.asserted', 'world_fact.retconned')
            ORDER BY created_at, id
            """,
            (campaign_id,),
        ).fetchall()
        entries = [FactLedgerEntry.from_event(row_to_dict(row)) for row in rows]
        # Validate every chain even when the caller asks for history.
        project_fact_heads(entries)
        return entries

    def list_fact_heads(self, campaign_id: str) -> list[FactLedgerEntry]:
        return project_fact_heads(self.list_fact_entries(campaign_id))

    def get_fact_head(self, campaign_id: str, fact_key: str) -> FactLedgerEntry:
        matches = [
            entry
            for entry in self.list_fact_heads(campaign_id)
            if entry.fact_key == fact_key
        ]
        if not matches:
            raise KeyError(f"World fact not found: {fact_key}")
        return matches[0]

    def find_active_fact(
        self,
        campaign_id: str,
        *,
        category: str,
        subject: str,
        predicate: str,
    ) -> FactLedgerEntry | None:
        for entry in self.list_fact_heads(campaign_id):
            if (
                entry.active
                and entry.fact.category == category
                and entry.fact.subject == subject
                and entry.fact.predicate == predicate
            ):
                return entry
        return None

    def append_fact_entry(self, entry: FactLedgerEntry) -> FactLedgerEntry:
        """Append one revision after validating campaign, evidence, and current head."""

        self.begin_immediate()
        campaign = self.connection.execute(
            "SELECT id FROM campaigns WHERE id = ?",
            (entry.campaign_id,),
        ).fetchone()
        if campaign is None:
            raise KeyError(f"Campaign not found: {entry.campaign_id}")
        if entry.fact.pc_id is not None:
            pc = self.connection.execute(
                """
                SELECT id FROM player_characters
                WHERE id = ? AND campaign_id = ?
                """,
                (entry.fact.pc_id, entry.campaign_id),
            ).fetchone()
            if pc is None:
                raise ValueError(
                    f"PC {entry.fact.pc_id} does not belong to campaign "
                    f"{entry.campaign_id}"
                )
        self._validate_evidence(entry)
        existing_entries = self.list_fact_entries(entry.campaign_id)
        same_chain = [
            existing for existing in existing_entries if existing.fact_key == entry.fact_key
        ]
        if not same_chain:
            if entry.revision != 1 or entry.supersedes_event_id is not None:
                raise ValueError("A new fact chain must start at revision 1")
        else:
            current = project_fact_heads(same_chain)[0]
            if entry.revision != current.revision + 1:
                raise ValueError("World fact revision is stale")
            if entry.supersedes_event_id != current.event_id:
                raise ValueError("World fact head changed; refresh before correcting it")
            # Project before writing so an invalid revision leaves no partial event.
            project_fact_heads([*same_chain, entry])

        self.connection.execute(
            """
            INSERT INTO events
              (id, campaign_id, actor_type, actor_id, visibility, event_type,
               happened_at, summary, payload_json)
            VALUES (?, ?, 'kp', ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.event_id,
                entry.campaign_id,
                entry.asserted_by,
                entry.storage_visibility,
                entry.event_type,
                entry.happened_at,
                self._event_summary(entry),
                json.dumps(entry.event_payload(), ensure_ascii=False, sort_keys=True),
            ),
        )
        row = self.connection.execute(
            "SELECT * FROM events WHERE id = ?",
            (entry.event_id,),
        ).fetchone()
        return FactLedgerEntry.from_event(row_to_dict(row))

    def _validate_evidence(self, entry: FactLedgerEntry) -> None:
        if not entry.evidence_event_ids:
            return
        placeholders = ",".join("?" for _ in entry.evidence_event_ids)
        rows = self.connection.execute(
            f"""
            SELECT id FROM events
            WHERE campaign_id = ? AND id IN ({placeholders})
            """,
            (entry.campaign_id, *entry.evidence_event_ids),
        ).fetchall()
        found = {str(row["id"]) for row in rows}
        missing = sorted(set(entry.evidence_event_ids) - found)
        if missing:
            raise ValueError(
                "Evidence events do not belong to this campaign: "
                + ", ".join(missing)
            )

    @staticmethod
    def _event_summary(entry: FactLedgerEntry) -> str:
        if entry.fact.category == "retconned":
            return (
                f"更正事实：{entry.fact.subject} / {entry.fact.predicate}；"
                f"原因：{entry.fact.object_text}"
            )
        return (
            f"[{entry.fact.category}] {entry.fact.subject} / "
            f"{entry.fact.predicate}：{entry.fact.object_text}"
        )


__all__ = ["FactRepository"]

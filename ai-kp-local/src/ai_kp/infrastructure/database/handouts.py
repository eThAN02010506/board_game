"""Campaign-scoped handout persistence with server-side visibility projection."""

from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.facts import FactLedgerEntry, project_fact_heads


class HandoutRepository(SQLiteRepository):
    def create_handout(
        self,
        *,
        campaign_id: str,
        member_id: str,
        title: str,
        body: str,
        kind: str,
        link_type: str | None,
        link_id: str | None,
    ) -> dict[str, Any]:
        title, body = title.strip(), body.strip()
        if not title or not body:
            raise ValueError("Handout title and body are required")
        if (link_type is None) != (link_id is None):
            raise ValueError("Handout link type and ID must be supplied together")
        self._require_valid_link(campaign_id, link_type, link_id)
        handout_id = new_id("handout")
        self.connection.execute(
            """
            INSERT INTO campaign_handouts
              (id, campaign_id, title, body, kind, link_type, link_id,
               created_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (handout_id, campaign_id, title, body, kind, link_type, link_id, member_id),
        )
        return self.get_handout(handout_id)

    def get_handout(self, handout_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM campaign_handouts WHERE id = ?", (handout_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Handout not found: {handout_id}")
        result = row_to_dict(row)
        result["pinned"] = bool(result["pinned"])
        receipts = self.connection.execute(
            """
            SELECT member_id, first_read_at, last_read_at
            FROM handout_read_receipts WHERE handout_id = ?
            ORDER BY first_read_at, member_id
            """,
            (handout_id,),
        ).fetchall()
        result["read_receipts"] = [row_to_dict(item) for item in receipts]
        return result

    def list_handouts(self, campaign_id: str, *, include_drafts: bool) -> list[dict]:
        where = "" if include_drafts else "AND status = 'revealed'"
        rows = self.connection.execute(
            f"""
            SELECT id FROM campaign_handouts
            WHERE campaign_id = ? {where}
            ORDER BY pinned DESC, COALESCE(revealed_at, created_at) DESC, id DESC
            """,
            (campaign_id,),
        ).fetchall()
        return [self.get_handout(str(row["id"])) for row in rows]

    def update_handout(
        self,
        handout_id: str,
        *,
        expected_version: int,
        member_id: str,
        status: str,
        pinned: bool,
        link_type: str | None,
        link_id: str | None,
    ) -> dict:
        handout = self.get_handout(handout_id)
        if (link_type is None) != (link_id is None):
            raise ValueError("Handout link type and ID must be supplied together")
        self._require_valid_link(str(handout["campaign_id"]), link_type, link_id)
        if handout["version"] != expected_version:
            raise ValueError("Handout changed; refresh before updating")
        cursor = self.connection.execute(
            """
            UPDATE campaign_handouts
            SET status = ?, pinned = ?, link_type = ?, link_id = ?,
                revealed_by_member_id = CASE WHEN ? = 'revealed'
                  THEN ? ELSE revealed_by_member_id END,
                revealed_at = CASE WHEN ? = 'revealed'
                  THEN COALESCE(revealed_at, CURRENT_TIMESTAMP) ELSE revealed_at END,
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ?
            """,
            (
                status, int(pinned), link_type, link_id, status, member_id,
                status, handout_id, expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("Handout changed; refresh before updating")
        return self.get_handout(handout_id)

    def list_handout_link_options(self, campaign_id: str) -> list[dict[str, str]]:
        options: list[dict[str, str]] = []
        event_rows = self.connection.execute(
            """
            SELECT * FROM events
            WHERE campaign_id = ?
              AND event_type IN ('world_fact.asserted', 'world_fact.retconned')
            ORDER BY created_at, id
            """,
            (campaign_id,),
        ).fetchall()
        facts = project_fact_heads(
            [FactLedgerEntry.from_event(row_to_dict(row)) for row in event_rows]
        )
        options.extend(
            {
                "type": "fact",
                "id": fact.fact_key,
                "label": (
                    f"{fact.fact.subject} · {fact.fact.predicate}: "
                    f"{fact.fact.object_text}"
                ),
            }
            for fact in facts
            if fact.active
        )
        queries = (
            (
                "npc",
                """
                SELECT n.id, n.name AS label
                FROM campaign_npcs cn JOIN npcs n ON n.id = cn.npc_id
                WHERE cn.campaign_id = ? ORDER BY n.name, n.id
                """,
            ),
            (
                "map",
                (
                    "SELECT id, title AS label FROM maps WHERE campaign_id = ? "
                    "ORDER BY title, id"
                ),
            ),
            (
                "location",
                """
                SELECT l.id, m.title || ' · ' || l.name AS label
                FROM map_locations l JOIN maps m ON m.id = l.map_id
                WHERE m.campaign_id = ? ORDER BY m.title, l.name, l.id
                """,
            ),
            (
                "module_entity",
                """
                SELECT DISTINCT e.id, mo.title || ' · ' || e.name AS label
                FROM module_entities e
                JOIN modules mo ON mo.id = e.module_id
                LEFT JOIN campaign_module_runs r ON r.module_id = mo.id
                WHERE mo.campaign_id = ? OR r.campaign_id = ?
                ORDER BY label, e.id
                """,
            ),
        )
        for link_type, query in queries:
            params = (campaign_id, campaign_id) if link_type == "module_entity" else (campaign_id,)
            options.extend(
                {
                    "type": link_type,
                    "id": str(row["id"]),
                    "label": str(row["label"]),
                }
                for row in self.connection.execute(query, params).fetchall()
            )
        return options

    def _require_valid_link(
        self,
        campaign_id: str,
        link_type: str | None,
        link_id: str | None,
    ) -> None:
        if link_type is None or link_id is None:
            return
        valid = any(
            option["type"] == link_type and option["id"] == link_id
            for option in self.list_handout_link_options(campaign_id)
        )
        if not valid:
            raise ValueError("Handout link target does not belong to this campaign")

    def mark_handout_read(self, handout_id: str, member_id: str) -> dict:
        handout = self.get_handout(handout_id)
        if handout["status"] != "revealed":
            raise PermissionError("Only revealed handouts can be read")
        self.connection.execute(
            """
            INSERT INTO handout_read_receipts (handout_id, member_id)
            VALUES (?, ?)
            ON CONFLICT(handout_id, member_id) DO UPDATE
            SET last_read_at = CURRENT_TIMESTAMP
            """,
            (handout_id, member_id),
        )
        return self.get_handout(handout_id)


__all__ = ["HandoutRepository"]

"""Evidence-backed memory timeline projection and immutable KP curation."""

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository

CLASSIFICATION_BY_SCOPE = {
    "pc_major": "major",
    "major": "major",
    "pc_side": "side",
    "side": "side",
    "npc_relation": "npc",
    "npc": "npc",
    "clue": "clue",
}


class MemoryTimelineRepository(SQLiteRepository):
    """Project memories with their visible evidence and latest curation head."""

    def list_memory_timeline(
        self,
        campaign_id: str,
        *,
        pc_id: str | None = None,
        view: str = "kp",
        classification: str | None = None,
        query: str | None = None,
        include_hidden: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        filters = ["m.campaign_id = ?"]
        params: list[object] = [campaign_id]
        if pc_id:
            filters.append("m.pc_id = ?")
            params.append(pc_id)
        if view == "player":
            filters.append("m.visibility IN ('table', 'player')")
            filters.append("COALESCE(head.hidden, 0) = 0")
        elif not include_hidden:
            filters.append("COALESCE(head.hidden, 0) = 0")
        if query and query.strip():
            filters.append("(m.text LIKE ? ESCAPE '\\' OR e.summary LIKE ? ESCAPE '\\')")
            escaped = (
                query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            params.extend((f"%{escaped}%", f"%{escaped}%"))

        rows = self.connection.execute(
            f"""
            SELECT m.id, m.campaign_id, m.pc_id, m.npc_id, m.scope,
                   m.importance AS original_importance, m.visibility,
                   m.happened_at, m.text, m.source_event_id, m.created_at,
                   pc.name AS pc_name,
                   ci.investigator_id, i.name AS investigator_name,
                   n.name AS npc_name,
                   e.event_type AS source_event_type,
                   e.summary AS source_event_summary,
                   e.visibility AS source_event_visibility,
                   e.happened_at AS source_event_happened_at,
                   e.created_at AS source_event_created_at,
                   head.id AS curation_head_id,
                   head.classification AS curated_classification,
                   head.importance AS curated_importance,
                   COALESCE(head.hidden, 0) AS hidden,
                   head.reason AS curation_reason,
                   head.created_by_member_id AS curated_by_member_id,
                   head.created_at AS curated_at
            FROM memories m
            LEFT JOIN player_characters pc ON pc.id = m.pc_id
            LEFT JOIN campaign_investigators ci
              ON ci.campaign_id = m.campaign_id AND ci.legacy_pc_id = m.pc_id
            LEFT JOIN investigators i ON i.id = ci.investigator_id
            LEFT JOIN npcs n ON n.id = m.npc_id
            LEFT JOIN events e
              ON e.id = m.source_event_id AND e.campaign_id = m.campaign_id
            LEFT JOIN memory_curation_actions head
              ON head.id = (
                SELECT action.id
                FROM memory_curation_actions action
                WHERE action.memory_id = m.id
                  AND NOT EXISTS (
                    SELECT 1 FROM memory_curation_actions successor
                    WHERE successor.supersedes_action_id = action.id
                  )
                LIMIT 1
              )
            WHERE {" AND ".join(filters)}
            ORDER BY COALESCE(m.happened_at, e.happened_at, m.created_at) DESC, m.id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        items = [self._project_timeline_row(row_to_dict(row), view=view) for row in rows]
        if classification:
            items = [item for item in items if item["classification"] == classification]
        return items

    def append_memory_curation(
        self,
        campaign_id: str,
        memory_id: str,
        *,
        classification: str,
        importance: int,
        hidden: bool,
        reason: str,
        expected_head_action_id: str | None,
        created_by_member_id: str,
    ) -> dict:
        self.begin_immediate()
        memory = self.connection.execute(
            "SELECT id FROM memories WHERE id = ? AND campaign_id = ?",
            (memory_id, campaign_id),
        ).fetchone()
        if memory is None:
            raise KeyError(f"Memory not found: {memory_id}")
        current = self.connection.execute(
            """
            SELECT action.id FROM memory_curation_actions action
            WHERE action.memory_id = ?
              AND NOT EXISTS (
                SELECT 1 FROM memory_curation_actions successor
                WHERE successor.supersedes_action_id = action.id
              )
            LIMIT 1
            """,
            (memory_id,),
        ).fetchone()
        current_id = str(current["id"]) if current else None
        if current_id != expected_head_action_id:
            raise ValueError("Memory curation changed; refresh before saving")
        action_id = new_id("mca")
        self.connection.execute(
            """
            INSERT INTO memory_curation_actions
              (id, campaign_id, memory_id, classification, importance, hidden,
               reason, supersedes_action_id, created_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                campaign_id,
                memory_id,
                classification,
                importance,
                int(hidden),
                reason.strip(),
                current_id,
                created_by_member_id,
            ),
        )
        return row_to_dict(
            self.connection.execute(
                "SELECT * FROM memory_curation_actions WHERE id = ?",
                (action_id,),
            ).fetchone()
        )

    @staticmethod
    def _project_timeline_row(item: dict, *, view: str) -> dict:
        item["classification"] = item.pop("curated_classification") or (
            CLASSIFICATION_BY_SCOPE.get(str(item["scope"]), "other")
        )
        item["importance"] = item.pop("curated_importance") or item.pop(
            "original_importance"
        )
        item["hidden"] = bool(item["hidden"])
        item["effective_time"] = (
            item["happened_at"]
            or item["source_event_happened_at"]
            or item["created_at"]
        )
        if view == "player":
            if item.pop("source_event_visibility", None) not in (None, "table", "player"):
                item["source_event_type"] = None
                item["source_event_summary"] = None
                item["source_event_happened_at"] = None
                item["source_event_created_at"] = None
            item.pop("curation_reason", None)
            item.pop("curated_by_member_id", None)
            item.pop("curation_head_id", None)
            item.pop("curated_at", None)
        else:
            item.pop("source_event_visibility", None)
        return item

import json
import sqlite3
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.modules.ingestion import ModuleChunk


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


class Repository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_campaign(self, title: str, system: str = "coc7", current_time: str | None = None) -> dict:
        campaign_id = new_id("camp")
        self.connection.execute(
            "INSERT INTO campaigns (id, title, system, current_time) VALUES (?, ?, ?, ?)",
            (campaign_id, title, system, current_time),
        )
        return self.get_campaign(campaign_id)

    def get_campaign(self, campaign_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Campaign not found: {campaign_id}")
        return row_to_dict(row)

    def list_campaigns(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM campaigns ORDER BY created_at DESC"
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def create_pc(self, campaign_id: str, name: str, sheet: dict | None = None) -> dict:
        pc_id = new_id("pc")
        self.connection.execute(
            "INSERT INTO player_characters (id, campaign_id, name, sheet_json) VALUES (?, ?, ?, ?)",
            (pc_id, campaign_id, name, json.dumps(sheet or {}, ensure_ascii=False)),
        )
        return row_to_dict(
            self.connection.execute(
                "SELECT * FROM player_characters WHERE id = ?", (pc_id,)
            ).fetchone()
        )

    def create_npc(
        self,
        name: str,
        home_location: str | None = None,
        profession: str | None = None,
        public_notes: str = "",
        secret_notes: str = "",
    ) -> dict:
        npc_id = new_id("npc")
        self.connection.execute(
            """
            INSERT INTO npcs (id, name, home_location, profession, public_notes, secret_notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (npc_id, name, home_location, profession, public_notes, secret_notes),
        )
        return row_to_dict(self.connection.execute("SELECT * FROM npcs WHERE id = ?", (npc_id,)).fetchone())

    def link_npc_to_campaign(
        self,
        campaign_id: str,
        npc_id: str,
        role: str = "encountered",
        first_seen_time: str | None = None,
        last_seen_time: str | None = None,
        relationship_score: int = 0,
        notes: str = "",
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO campaign_npcs
              (campaign_id, npc_id, role, first_seen_time, last_seen_time, relationship_score, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, npc_id) DO UPDATE SET
              role = excluded.role,
              last_seen_time = COALESCE(excluded.last_seen_time, campaign_npcs.last_seen_time),
              relationship_score = excluded.relationship_score,
              notes = excluded.notes
            """,
            (campaign_id, npc_id, role, first_seen_time, last_seen_time, relationship_score, notes),
        )

    def create_module(
        self,
        campaign_id: str | None,
        title: str,
        chunks: list[ModuleChunk],
        source_type: str = "plaintext",
    ) -> dict:
        module_id = new_id("mod")
        self.connection.execute(
            "INSERT INTO modules (id, campaign_id, title, source_type) VALUES (?, ?, ?, ?)",
            (module_id, campaign_id, title, source_type),
        )
        for chunk in chunks:
            self.connection.execute(
                """
                INSERT INTO module_chunks
                  (id, module_id, title, text, visibility, spoiler_tag, scene_key, order_index)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("chunk"),
                    module_id,
                    chunk.title,
                    chunk.text,
                    chunk.visibility,
                    chunk.spoiler_tag,
                    chunk.scene_key,
                    chunk.order_index,
                ),
            )
        return self.get_module(module_id)

    def get_module(self, module_id: str) -> dict:
        row = self.connection.execute("SELECT * FROM modules WHERE id = ?", (module_id,)).fetchone()
        if row is None:
            raise KeyError(f"Module not found: {module_id}")
        return row_to_dict(row)

    def list_modules(self, campaign_id: str | None = None) -> list[dict]:
        if campaign_id:
            rows = self.connection.execute(
                "SELECT * FROM modules WHERE campaign_id = ? OR campaign_id IS NULL ORDER BY created_at DESC",
                (campaign_id,),
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM modules ORDER BY created_at DESC").fetchall()
        return [row_to_dict(row) for row in rows]

    def list_module_chunks(
        self,
        module_id: str,
        allowed_visibility: tuple[str, ...] = ("player", "table", "kp"),
        spoiler_tags: tuple[str, ...] | None = None,
    ) -> list[dict]:
        params: list[object] = [module_id, *allowed_visibility]
        visibility_placeholders = ",".join("?" for _ in allowed_visibility)
        filters = [f"module_id = ?", f"visibility IN ({visibility_placeholders})"]
        if spoiler_tags is not None:
            spoiler_placeholders = ",".join("?" for _ in spoiler_tags)
            filters.append(f"(spoiler_tag IS NULL OR spoiler_tag IN ({spoiler_placeholders}))")
            params.extend(spoiler_tags)
        rows = self.connection.execute(
            f"""
            SELECT * FROM module_chunks
            WHERE {" AND ".join(filters)}
            ORDER BY order_index ASC
            """,
            params,
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def append_event(
        self,
        campaign_id: str,
        actor_type: str,
        event_type: str,
        summary: str,
        actor_id: str | None = None,
        visibility: str = "table",
        happened_at: str | None = None,
        payload: dict | None = None,
    ) -> dict:
        event_id = new_id("evt")
        self.connection.execute(
            """
            INSERT INTO events
              (id, campaign_id, actor_type, actor_id, visibility, event_type, happened_at, summary, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                campaign_id,
                actor_type,
                actor_id,
                visibility,
                event_type,
                happened_at,
                summary,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )
        return row_to_dict(self.connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone())

    def add_memory(
        self,
        text: str,
        scope: str,
        campaign_id: str | None = None,
        pc_id: str | None = None,
        npc_id: str | None = None,
        importance: int = 1,
        visibility: str = "table",
        happened_at: str | None = None,
        source_event_id: str | None = None,
    ) -> dict:
        memory_id = new_id("mem")
        self.connection.execute(
            """
            INSERT INTO memories
              (id, campaign_id, pc_id, npc_id, scope, importance, visibility, happened_at, text, source_event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                campaign_id,
                pc_id,
                npc_id,
                scope,
                importance,
                visibility,
                happened_at,
                text,
                source_event_id,
            ),
        )
        return row_to_dict(
            self.connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        )

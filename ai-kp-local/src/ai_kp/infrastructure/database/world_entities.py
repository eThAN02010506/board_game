"""SQLite persistence for ruleset-neutral campaign world entities."""

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class WorldEntityRepository(SQLiteRepository):
    def begin_campaign_world_entity_state_change(self) -> None:
        self.connection.execute("SAVEPOINT campaign_world_entity_state_change")

    def finish_campaign_world_entity_state_change(self) -> None:
        self.connection.execute("RELEASE SAVEPOINT campaign_world_entity_state_change")

    def rollback_campaign_world_entity_state_change(self) -> None:
        self.connection.execute("ROLLBACK TO SAVEPOINT campaign_world_entity_state_change")
        self.connection.execute("RELEASE SAVEPOINT campaign_world_entity_state_change")

    def get_campaign_world_entity(self, entity_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM campaign_world_entities WHERE id = ?",
            (entity_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Campaign world entity not found: {entity_id}")
        return self._decode_world_entity(row)

    def find_campaign_world_entity_by_origin(
        self,
        campaign_id: str,
        origin_kind: str,
        origin_ref: str,
    ) -> dict | None:
        row = self.connection.execute(
            """
            SELECT * FROM campaign_world_entities
            WHERE campaign_id = ? AND origin_kind = ? AND origin_ref = ?
            """,
            (campaign_id, origin_kind, origin_ref),
        ).fetchone()
        return self._decode_world_entity(row) if row is not None else None

    def create_campaign_world_entity(self, **values: Any) -> dict:
        entity_id = str(values.get("entity_id") or new_id("worldent"))
        self.connection.execute(
            """
            INSERT INTO campaign_world_entities
              (id, campaign_id, entity_kind, archetype_id, name, description,
               visibility, origin_kind, origin_ref, npc_id, created_from_event_id,
               data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_id,
                values["campaign_id"],
                values["entity_kind"],
                values.get("archetype_id"),
                values["name"],
                values.get("description", ""),
                values.get("visibility", "table"),
                values["origin_kind"],
                values["origin_ref"],
                values.get("npc_id"),
                values["created_from_event_id"],
                json.dumps(values.get("data", {}), ensure_ascii=False, sort_keys=True),
            ),
        )
        row = self.connection.execute(
            "SELECT * FROM campaign_world_entities WHERE id = ?",
            (entity_id,),
        ).fetchone()
        return self._decode_world_entity(row)

    def create_campaign_world_entity_relation(self, **values: Any) -> dict:
        relation_id = str(values.get("relation_id") or new_id("worldrel"))
        self.connection.execute(
            """
            INSERT INTO campaign_world_entity_relations
              (id, campaign_id, source_entity_id, relation_slot_id,
               target_entity_id, created_from_event_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                relation_id,
                values["campaign_id"],
                values["source_entity_id"],
                values["relation_slot_id"],
                values["target_entity_id"],
                values["created_from_event_id"],
            ),
        )
        row = self.connection.execute(
            "SELECT * FROM campaign_world_entity_relations WHERE id = ?",
            (relation_id,),
        ).fetchone()
        return row_to_dict(row)

    def list_campaign_world_entities(self, campaign_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM campaign_world_entities
            WHERE campaign_id = ? ORDER BY created_at, id
            """,
            (campaign_id,),
        ).fetchall()
        return [self._decode_world_entity(row) for row in rows]

    def list_campaign_world_entity_relations(self, campaign_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM campaign_world_entity_relations
            WHERE campaign_id = ? ORDER BY created_at, id
            """,
            (campaign_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def list_campaign_world_entity_states(self, campaign_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT state.* FROM campaign_world_entity_states state
            JOIN campaign_world_entities entity ON entity.id = state.entity_id
            WHERE entity.campaign_id = ?
            ORDER BY entity.created_at, state.dimension
            """,
            (campaign_id,),
        ).fetchall()
        return [self._decode_world_entity_state(row) for row in rows]

    def get_campaign_world_entity_state(
        self, entity_id: str, dimension: str
    ) -> dict | None:
        row = self.connection.execute(
            """
            SELECT * FROM campaign_world_entity_states
            WHERE entity_id = ? AND dimension = ?
            """,
            (entity_id, dimension),
        ).fetchone()
        return self._decode_world_entity_state(row) if row is not None else None

    def find_campaign_world_entity_state_change_by_key(
        self,
        campaign_id: str,
        idempotency_key: str,
    ) -> dict | None:
        row = self.connection.execute(
            """
            SELECT * FROM campaign_world_entity_state_changes
            WHERE campaign_id = ? AND idempotency_key = ?
            """,
            (campaign_id, idempotency_key),
        ).fetchone()
        return self._decode_world_entity_state_change(row) if row is not None else None

    def list_campaign_world_entity_state_changes(self, campaign_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM campaign_world_entity_state_changes
            WHERE campaign_id = ? ORDER BY created_at, id
            """,
            (campaign_id,),
        ).fetchall()
        return [self._decode_world_entity_state_change(row) for row in rows]

    def bump_campaign_world_entity_state_version(
        self,
        entity_id: str,
        expected_version: int,
    ) -> bool:
        cursor = self.connection.execute(
            """
            UPDATE campaign_world_entities
            SET state_version = state_version + 1
            WHERE id = ? AND state_version = ?
            """,
            (entity_id, expected_version),
        )
        return cursor.rowcount == 1

    def set_campaign_world_entity_state(
        self,
        *,
        entity_id: str,
        dimension: str,
        value: Any | None,
        visibility: str,
        source_event_id: str,
    ) -> None:
        if value is None:
            self.connection.execute(
                "DELETE FROM campaign_world_entity_states WHERE entity_id = ? AND dimension = ?",
                (entity_id, dimension),
            )
            return
        self.connection.execute(
            """
            INSERT INTO campaign_world_entity_states
              (entity_id, dimension, value_json, visibility, source_event_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(entity_id, dimension) DO UPDATE SET
              value_json = excluded.value_json,
              visibility = excluded.visibility,
              version = campaign_world_entity_states.version + 1,
              source_event_id = excluded.source_event_id,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                entity_id,
                dimension,
                json.dumps(value, ensure_ascii=False, sort_keys=True),
                visibility,
                source_event_id,
            ),
        )

    def create_campaign_world_entity_state_change(self, **values: Any) -> dict:
        change_id = str(values.get("change_id") or new_id("worldstate"))
        self.connection.execute(
            """
            INSERT INTO campaign_world_entity_state_changes
              (id, campaign_id, entity_id, dimension, from_value_json,
               to_value_json, visibility, note, source_kind, state_version,
               idempotency_key, command_hash, event_id, changed_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                change_id,
                values["campaign_id"],
                values["entity_id"],
                values["dimension"],
                self._json_or_none(values.get("from_value")),
                self._json_or_none(values.get("to_value")),
                values["visibility"],
                values.get("note", ""),
                values["source_kind"],
                values["state_version"],
                values["idempotency_key"],
                values["command_hash"],
                values["event_id"],
                values.get("changed_by_member_id"),
            ),
        )
        row = self.connection.execute(
            "SELECT * FROM campaign_world_entity_state_changes WHERE id = ?",
            (change_id,),
        ).fetchone()
        return self._decode_world_entity_state_change(row)

    @staticmethod
    def _decode_world_entity(row) -> dict:
        result = row_to_dict(row)
        result["data"] = decode_json_field(result.pop("data_json"), {})
        return result

    @staticmethod
    def _decode_world_entity_state(row) -> dict:
        result = row_to_dict(row)
        result["value"] = json.loads(result.pop("value_json"))
        return result

    @staticmethod
    def _decode_world_entity_state_change(row) -> dict:
        result = row_to_dict(row)
        for source, target in (
            ("from_value_json", "from_value"),
            ("to_value_json", "to_value"),
        ):
            raw = result.pop(source)
            result[target] = json.loads(raw) if raw is not None else None
        return result

    @staticmethod
    def _json_or_none(value: Any | None) -> str | None:
        return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)


__all__ = ["WorldEntityRepository"]

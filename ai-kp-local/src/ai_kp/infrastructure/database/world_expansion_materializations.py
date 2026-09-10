"""SQLite ledger for one-time world-expansion encounter materialization."""

import json
from typing import Any

from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class WorldExpansionMaterializationRepository(SQLiteRepository):
    """Persist an idempotent receipt after every atomic world projection."""

    def begin_world_expansion_materialization(self) -> None:
        self.connection.execute("SAVEPOINT materialize_world_expansion")

    def finish_world_expansion_materialization(self) -> None:
        self.connection.execute("RELEASE SAVEPOINT materialize_world_expansion")

    def rollback_world_expansion_materialization(self) -> None:
        self.connection.execute("ROLLBACK TO SAVEPOINT materialize_world_expansion")
        self.connection.execute("RELEASE SAVEPOINT materialize_world_expansion")

    def get_world_expansion_materialization(
        self,
        proposal_id: str,
    ) -> dict | None:
        row = self.connection.execute(
            """
            SELECT * FROM world_expansion_materializations
            WHERE proposal_id = ?
            """,
            (proposal_id,),
        ).fetchone()
        return self._decode_materialization(row) if row is not None else None

    def find_world_expansion_materialization_by_key(
        self,
        campaign_id: str,
        idempotency_key: str,
    ) -> dict | None:
        row = self.connection.execute(
            """
            SELECT * FROM world_expansion_materializations
            WHERE campaign_id = ? AND idempotency_key = ?
            """,
            (campaign_id, idempotency_key),
        ).fetchone()
        return self._decode_materialization(row) if row is not None else None

    def create_world_expansion_materialization(
        self,
        *,
        materialization_id: str,
        proposal_id: str,
        campaign_id: str,
        idempotency_key: str,
        command_hash: str,
        encounter_event_id: str,
        fact_event_ids: list[str],
        npc_id: str | None,
        map_token_id: str | None,
        created_by_member_id: str,
        payload: dict,
    ) -> dict:
        self.connection.execute(
            """
            INSERT INTO world_expansion_materializations
              (id, proposal_id, campaign_id, idempotency_key, command_hash,
               encounter_event_id, npc_id, map_token_id, created_by_member_id,
               payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                materialization_id,
                proposal_id,
                campaign_id,
                idempotency_key,
                command_hash,
                encounter_event_id,
                npc_id,
                map_token_id,
                created_by_member_id,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        for order_index, event_id in enumerate(fact_event_ids):
            self.connection.execute(
                """
                INSERT INTO world_expansion_materialization_facts
                  (materialization_id, fact_event_id, order_index)
                VALUES (?, ?, ?)
                """,
                (materialization_id, event_id, order_index),
            )
        saved = self.get_world_expansion_materialization(proposal_id)
        if saved is None:
            raise RuntimeError("World expansion materialization was not persisted")
        return saved

    def attach_world_expansion_materialization_entities(
        self,
        materialization_id: str,
        entities: list[dict[str, Any]],
    ) -> None:
        for order_index, item in enumerate(entities):
            self.connection.execute(
                """
                INSERT INTO world_expansion_materialization_entities
                  (materialization_id, world_entity_id, role, local_ref, order_index)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    materialization_id,
                    item["world_entity_id"],
                    item["role"],
                    item.get("local_ref"),
                    order_index,
                ),
            )

    def _decode_materialization(self, row) -> dict:
        result = row_to_dict(row)
        result["payload"] = decode_json_field(result.pop("payload_json"), {})
        fact_rows = self.connection.execute(
            """
            SELECT fact_event_id
            FROM world_expansion_materialization_facts
            WHERE materialization_id = ?
            ORDER BY order_index
            """,
            (result["id"],),
        ).fetchall()
        result["fact_event_ids"] = [str(item["fact_event_id"]) for item in fact_rows]
        entity_rows = self.connection.execute(
            """
            SELECT world_entity_id, role, local_ref
            FROM world_expansion_materialization_entities
            WHERE materialization_id = ?
            ORDER BY order_index
            """,
            (result["id"],),
        ).fetchall()
        result["world_entities"] = [row_to_dict(item) for item in entity_rows]
        result["world_entity_ids"] = [
            str(item["world_entity_id"]) for item in entity_rows
        ]
        return result


__all__ = ["WorldExpansionMaterializationRepository"]

"""SQLite ledger for immutable KP-private random-resolution receipts."""

import json

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class PrivateRandomResolutionRepository(SQLiteRepository):
    def begin_private_random_resolution(self) -> None:
        self.connection.execute("SAVEPOINT private_random_resolution")

    def finish_private_random_resolution(self) -> None:
        self.connection.execute("RELEASE SAVEPOINT private_random_resolution")

    def rollback_private_random_resolution(self) -> None:
        self.connection.execute("ROLLBACK TO SAVEPOINT private_random_resolution")
        self.connection.execute("RELEASE SAVEPOINT private_random_resolution")

    def find_npc_hidden_appearance_resolution(
        self,
        campaign_id: str,
        idempotency_key: str,
    ) -> dict | None:
        row = self.connection.execute(
            """
            SELECT r.*, n.name AS npc_name
            FROM npc_hidden_appearance_resolutions r
            JOIN npcs n ON n.id = r.npc_id
            WHERE r.campaign_id = ? AND r.idempotency_key = ?
            """,
            (campaign_id, idempotency_key),
        ).fetchone()
        return self._decode_hidden_appearance(row) if row is not None else None

    def create_npc_hidden_appearance_resolution(self, **values) -> dict:
        resolution_id = new_id("hiddenroll")
        self.connection.execute(
            """
            INSERT INTO npc_hidden_appearance_resolutions
              (id, campaign_id, npc_id, idempotency_key, command_hash,
               trigger_text, appearance_chance, appearance_roll, appears,
               eligible_locations_json, selected_location_id,
               selected_location_name, location_roll, created_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolution_id,
                values["campaign_id"],
                values["npc_id"],
                values["idempotency_key"],
                values["command_hash"],
                values["trigger_text"],
                values["appearance_chance"],
                values["appearance_roll"],
                int(values["appears"]),
                json.dumps(
                    values["eligible_locations"], ensure_ascii=False, sort_keys=True
                ),
                values.get("selected_location_id"),
                values.get("selected_location_name"),
                values.get("location_roll"),
                values["created_by_member_id"],
            ),
        )
        saved = self.find_npc_hidden_appearance_resolution(
            values["campaign_id"], values["idempotency_key"]
        )
        if saved is None:
            raise RuntimeError("Hidden appearance resolution was not persisted")
        return saved

    def list_npc_hidden_appearance_resolutions(
        self,
        campaign_id: str,
        limit: int = 50,
    ) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT r.*, n.name AS npc_name
            FROM npc_hidden_appearance_resolutions r
            JOIN npcs n ON n.id = r.npc_id
            WHERE r.campaign_id = ?
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ?
            """,
            (campaign_id, limit),
        ).fetchall()
        return [self._decode_hidden_appearance(row) for row in rows]

    @staticmethod
    def _decode_hidden_appearance(row) -> dict:
        result = row_to_dict(row)
        result["appears"] = bool(result["appears"])
        result["eligible_locations"] = decode_json_field(
            result.pop("eligible_locations_json"), []
        )
        result["public_result"] = {
            "appears": result["appears"],
            "location_name": (
                result["selected_location_name"] if result["appears"] else None
            ),
        }
        return result


__all__ = ["PrivateRandomResolutionRepository"]

"""SQLite persistence for explicit campaign travel locations and routes."""

from __future__ import annotations

import json

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class TravelGraphRepository(SQLiteRepository):
    def list_travel_locations(self, campaign_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM campaign_travel_locations
            WHERE campaign_id = ?
            ORDER BY normalized_name, id
            """,
            (campaign_id,),
        ).fetchall()
        results = []
        for row in rows:
            result = row_to_dict(row)
            result["aliases"] = json.loads(result.pop("aliases_json"))
            results.append(result)
        return results

    def create_travel_location(
        self,
        campaign_id: str,
        *,
        name: str,
        normalized_name: str,
        aliases: list[str],
        source_kind: str,
        source_ref: str | None,
        kp_notes: str,
    ) -> dict:
        location_id = new_id("travelloc")
        self.connection.execute(
            """
            INSERT INTO campaign_travel_locations
              (id, campaign_id, name, normalized_name, aliases_json,
               source_kind, source_ref, kp_notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                location_id,
                campaign_id,
                name,
                normalized_name,
                json.dumps(aliases, ensure_ascii=False),
                source_kind,
                source_ref,
                kp_notes,
            ),
        )
        return next(
            item
            for item in self.list_travel_locations(campaign_id)
            if item["id"] == location_id
        )

    def delete_travel_location(self, campaign_id: str, location_id: str) -> None:
        result = self.connection.execute(
            """
            DELETE FROM campaign_travel_locations
            WHERE campaign_id = ? AND id = ?
            """,
            (campaign_id, location_id),
        )
        if result.rowcount != 1:
            raise KeyError(f"Travel location not found: {location_id}")

    def list_travel_routes(self, campaign_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT r.*, origin.name AS from_name, destination.name AS to_name
            FROM campaign_travel_routes r
            JOIN campaign_travel_locations origin ON origin.id = r.from_location_id
            JOIN campaign_travel_locations destination ON destination.id = r.to_location_id
            WHERE r.campaign_id = ?
            ORDER BY origin.normalized_name, destination.normalized_name, r.id
            """,
            (campaign_id,),
        ).fetchall()
        results = [row_to_dict(row) for row in rows]
        for result in results:
            result["bidirectional"] = bool(result["bidirectional"])
        return results

    def create_travel_route(self, campaign_id: str, **values) -> dict:
        location_count = self.connection.execute(
            """
            SELECT COUNT(*) FROM campaign_travel_locations
            WHERE campaign_id = ? AND id IN (?, ?)
            """,
            (
                campaign_id,
                values["from_location_id"],
                values["to_location_id"],
            ),
        ).fetchone()[0]
        if location_count != 2:
            raise ValueError("Travel route endpoints must belong to this campaign")
        route_id = new_id("travelroute")
        self.connection.execute(
            """
            INSERT INTO campaign_travel_routes
              (id, campaign_id, from_location_id, to_location_id,
               travel_minutes, travel_mode, bidirectional, status, kp_notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                route_id,
                campaign_id,
                values["from_location_id"],
                values["to_location_id"],
                values["travel_minutes"],
                values["travel_mode"],
                int(values["bidirectional"]),
                values["status"],
                values["kp_notes"],
            ),
        )
        return next(
            item
            for item in self.list_travel_routes(campaign_id)
            if item["id"] == route_id
        )

    def delete_travel_route(self, campaign_id: str, route_id: str) -> None:
        result = self.connection.execute(
            """
            DELETE FROM campaign_travel_routes
            WHERE campaign_id = ? AND id = ?
            """,
            (campaign_id, route_id),
        )
        if result.rowcount != 1:
            raise KeyError(f"Travel route not found: {route_id}")


__all__ = ["TravelGraphRepository"]

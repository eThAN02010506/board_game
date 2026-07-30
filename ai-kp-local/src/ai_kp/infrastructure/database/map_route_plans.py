"""SQLite persistence for revision-bound, multi-token route plans."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class MapRoutePlanRepository(SQLiteRepository):
    def create_map_route_plan(
        self,
        *,
        campaign_id: str,
        map_id: str,
        member_id: str,
        title: str,
        note: str,
        token_routes: list[dict],
        initial_status: str,
        visibility: str,
        allowed_visibility: tuple[str, ...],
    ) -> dict:
        self.begin_immediate()
        map_row = self.connection.execute(
            "SELECT campaign_id, current_revision_id FROM maps WHERE id = ?",
            (map_id,),
        ).fetchone()
        if map_row is None or str(map_row["campaign_id"]) != campaign_id:
            raise KeyError(f"Map not found in campaign: {map_id}")
        revision_id = map_row["current_revision_id"]
        if not revision_id:
            raise ValueError("Map requires a current revision")
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Route plan title is required")

        seen_tokens: set[str] = set()
        validated: list[tuple[str, list[tuple[str, str, str | None]]]] = []
        for route in token_routes:
            token_id = str(route["token_id"])
            if token_id in seen_tokens:
                raise ValueError("Each token may appear only once in a route plan")
            seen_tokens.add(token_id)
            waypoints = [str(item).strip() for item in route["waypoints"]]
            if len(waypoints) < 2 or any(not item for item in waypoints):
                raise ValueError("Each token route requires at least two waypoints")
            token = self.connection.execute(
                """
                SELECT t.id, l.name AS location_name
                FROM map_tokens t
                JOIN map_locations l ON l.id = t.location_id
                WHERE t.id = ? AND t.map_id = ?
                """,
                (token_id, map_id),
            ).fetchone()
            if token is None:
                raise ValueError("Route token does not belong to this map")
            if str(token["location_name"]) != waypoints[0]:
                raise ValueError(
                    f"Route for {token_id} must begin at its current location"
                )
            legs = [
                (
                    start,
                    end,
                    self._route_element_id(
                        map_id,
                        start,
                        end,
                        allowed_visibility=allowed_visibility,
                    ),
                )
                for start, end in pairwise(waypoints)
            ]
            validated.append((token_id, legs))

        plan_id = new_id("routeplan")
        self.connection.execute(
            """
            INSERT INTO map_route_plans
              (id, campaign_id, map_id, revision_id, title, note, status,
               visibility, created_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                campaign_id,
                map_id,
                revision_id,
                clean_title,
                note.strip(),
                initial_status,
                visibility,
                member_id,
            ),
        )
        for token_id, legs in validated:
            for sequence_no, (start, end, route_element_id) in enumerate(legs):
                self.connection.execute(
                    """
                    INSERT INTO map_route_plan_legs
                      (id, plan_id, token_id, sequence_no, from_location_name,
                       to_location_name, route_element_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id("routeleg"),
                        plan_id,
                        token_id,
                        sequence_no,
                        start,
                        end,
                        route_element_id,
                    ),
                )
        return self.get_map_route_plan(plan_id)

    def _route_element_id(
        self,
        map_id: str,
        start: str,
        end: str,
        *,
        allowed_visibility: tuple[str, ...],
    ) -> str | None:
        placeholders = ",".join("?" for _ in allowed_visibility)
        row = self.connection.execute(
            f"""
            SELECT r.element_id
            FROM map_routes r
            JOIN map_locations a ON a.id = r.start_location_id
            JOIN map_locations b ON b.id = r.end_location_id
            WHERE r.map_id = ?
              AND ((a.name = ? AND b.name = ?) OR (a.name = ? AND b.name = ?))
              AND r.visibility IN ({placeholders})
              AND a.visibility IN ({placeholders})
              AND b.visibility IN ({placeholders})
            LIMIT 1
            """,
            (
                map_id,
                start,
                end,
                end,
                start,
                *allowed_visibility,
                *allowed_visibility,
                *allowed_visibility,
            ),
        ).fetchone()
        if row is None:
            raise ValueError(f"No visible route connects {start} and {end}")
        return str(row["element_id"]) if row["element_id"] else None

    def get_map_route_plan(self, plan_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM map_route_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Map route plan not found: {plan_id}")
        result = row_to_dict(row)
        legs = self.connection.execute(
            """
            SELECT l.*, t.label AS token_label
            FROM map_route_plan_legs l
            JOIN map_tokens t ON t.id = l.token_id
            WHERE l.plan_id = ?
            ORDER BY t.label, l.token_id, l.sequence_no
            """,
            (plan_id,),
        ).fetchall()
        result["legs"] = [row_to_dict(item) for item in legs]
        return result

    def list_map_route_plans(
        self,
        campaign_id: str,
        *,
        map_id: str | None,
        member_token_id: str | None,
        include_kp: bool,
    ) -> list[dict]:
        filters = ["p.campaign_id = ?"]
        values: list[Any] = [campaign_id]
        if map_id is not None:
            filters.append("p.map_id = ?")
            values.append(map_id)
        if not include_kp:
            filters.append("p.visibility = 'session'")
            if member_token_id is None:
                return []
            filters.append(
                "EXISTS (SELECT 1 FROM map_route_plan_legs own "
                "WHERE own.plan_id = p.id AND own.token_id = ?)"
            )
            values.append(member_token_id)
        rows = self.connection.execute(
            f"""
            SELECT p.id FROM map_route_plans p
            WHERE {" AND ".join(filters)}
            ORDER BY p.created_at DESC, p.id DESC
            """,
            values,
        ).fetchall()
        return [self.get_map_route_plan(str(row["id"])) for row in rows]

    def update_map_route_plan_status(
        self,
        plan_id: str,
        *,
        expected_version: int,
        status: str,
    ) -> dict:
        current = self.get_map_route_plan(plan_id)
        allowed = {
            "proposed": {"approved", "cancelled"},
            "approved": {"executing", "cancelled"},
            "executing": {"completed", "cancelled"},
            "completed": set(),
            "cancelled": set(),
        }
        if status not in allowed[str(current["status"])]:
            raise ValueError(
                f"Invalid route plan transition: {current['status']} -> {status}"
            )
        cursor = self.connection.execute(
            """
            UPDATE map_route_plans
            SET status = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ?
            """,
            (status, plan_id, expected_version),
        )
        if cursor.rowcount != 1:
            raise ValueError("Route plan changed; refresh before updating")
        return self.get_map_route_plan(plan_id)

    def record_route_plan_move(
        self,
        *,
        token_id: str,
        from_location_name: str,
        to_location_name: str,
        move_id: str,
    ) -> None:
        rows = self.connection.execute(
            """
            SELECT l.id, l.plan_id
            FROM map_route_plan_legs l
            JOIN map_route_plans p ON p.id = l.plan_id
            WHERE l.token_id = ? AND l.status = 'pending'
              AND l.from_location_name = ? AND l.to_location_name = ?
              AND p.status IN ('approved', 'executing')
              AND NOT EXISTS (
                SELECT 1 FROM map_route_plan_legs earlier
                WHERE earlier.plan_id = l.plan_id
                  AND earlier.token_id = l.token_id
                  AND earlier.sequence_no < l.sequence_no
                  AND earlier.status = 'pending'
              )
            """,
            (token_id, from_location_name, to_location_name),
        ).fetchall()
        for row in rows:
            self.connection.execute(
                """
                UPDATE map_route_plan_legs
                SET status = 'completed', completed_move_id = ?,
                    completed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'pending'
                """,
                (move_id, row["id"]),
            )
            pending = self.connection.execute(
                """
                SELECT COUNT(*) FROM map_route_plan_legs
                WHERE plan_id = ? AND status = 'pending'
                """,
                (row["plan_id"],),
            ).fetchone()[0]
            self.connection.execute(
                """
                UPDATE map_route_plans
                SET status = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status IN ('approved', 'executing')
                """,
                ("completed" if not pending else "executing", row["plan_id"]),
            )


__all__ = ["MapRoutePlanRepository"]

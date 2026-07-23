"""SQLite adapter for maps, locations, routes, tokens, and movement history."""

import sqlite3

from ai_kp.core.ids import new_id
from ai_kp.platform.scenes.map_generation import (
    GeneratedLocation,
    GeneratedMap,
    GeneratedRoute,
    render_svg,
)
from ai_kp.infrastructure.database.rows import row_to_dict


class MapRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_map(self, campaign_id: str, generated_map: GeneratedMap, created_by: str = "ai") -> dict:
        map_id = new_id("map")
        self.connection.execute(
            """
            INSERT INTO maps (id, campaign_id, title, prompt, style, width, height, svg_text, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                map_id,
                campaign_id,
                generated_map.title,
                generated_map.prompt,
                generated_map.style,
                generated_map.width,
                generated_map.height,
                generated_map.svg_text,
                created_by,
            ),
        )
        location_ids: dict[str, str] = {}
        for order_index, location in enumerate(generated_map.locations):
            location_id = new_id("loc")
            location_ids[location.name] = location_id
            self.connection.execute(
                """
                INSERT INTO map_locations
                  (id, map_id, name, x, y, visibility, notes, order_index)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    location_id,
                    map_id,
                    location.name,
                    location.x,
                    location.y,
                    location.visibility,
                    location.notes,
                    order_index,
                ),
            )
        for route in generated_map.routes:
            start_id = location_ids.get(route.start)
            end_id = location_ids.get(route.end)
            if not start_id or not end_id:
                continue
            self.connection.execute(
                """
                INSERT INTO map_routes
                  (id, map_id, start_location_id, end_location_id, travel_time, visibility, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("route"),
                    map_id,
                    start_id,
                    end_id,
                    route.travel_time,
                    route.visibility,
                    route.notes,
                ),
            )
        return self.get_map(map_id)

    def list_maps(
        self,
        campaign_id: str,
        include_prompt: bool = True,
        published_only: bool = False,
    ) -> list[dict]:
        status_filter = "AND status = 'published'" if published_only else ""
        rows = self.connection.execute(
            f"""
            SELECT id, campaign_id, title, prompt, style, status, width, height, created_by, created_at
            FROM maps
            WHERE campaign_id = ?
              {status_filter}
            ORDER BY created_at DESC
            """,
            (campaign_id,),
        ).fetchall()
        results = [row_to_dict(row) for row in rows]
        if not include_prompt:
            for result in results:
                result["prompt"] = ""
        return results

    def set_map_status(self, map_id: str, status: str) -> dict:
        if status not in {"draft", "published"}:
            raise ValueError(f"Unsupported map status: {status}")
        updated = self.connection.execute(
            "UPDATE maps SET status = ? WHERE id = ?",
            (status, map_id),
        )
        if updated.rowcount != 1:
            raise KeyError(f"Map not found: {map_id}")
        return self.get_map(map_id)

    def is_map_published(self, map_id: str) -> bool:
        row = self.connection.execute(
            "SELECT status FROM maps WHERE id = ?",
            (map_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Map not found: {map_id}")
        return row["status"] == "published"

    def is_map_token_player_visible(self, token_id: str) -> bool:
        row = self.connection.execute(
            """
            SELECT 1
            FROM map_tokens t
            JOIN maps m ON m.id = t.map_id
            JOIN map_locations l ON l.id = t.location_id
            WHERE t.id = ?
              AND m.status = 'published'
              AND t.visibility IN ('player', 'table')
              AND l.visibility IN ('player', 'table')
            """,
            (token_id,),
        ).fetchone()
        return row is not None

    def get_map(
        self,
        map_id: str,
        allowed_visibility: tuple[str, ...] = ("player", "table", "kp"),
    ) -> dict:
        map_row = self.connection.execute("SELECT * FROM maps WHERE id = ?", (map_id,)).fetchone()
        if map_row is None:
            raise KeyError(f"Map not found: {map_id}")
        visibility_placeholders = ",".join("?" for _ in allowed_visibility)
        locations = self.connection.execute(
            f"""
            SELECT * FROM map_locations
            WHERE map_id = ? AND visibility IN ({visibility_placeholders})
            ORDER BY order_index ASC
            """,
            (map_id, *allowed_visibility),
        ).fetchall()
        routes = self.connection.execute(
            f"""
            SELECT
              r.*,
              start.name AS start_name,
              end.name AS end_name
            FROM map_routes r
            JOIN map_locations start ON start.id = r.start_location_id
            JOIN map_locations end ON end.id = r.end_location_id
            WHERE r.map_id = ?
              AND r.visibility IN ({visibility_placeholders})
              AND start.visibility IN ({visibility_placeholders})
              AND end.visibility IN ({visibility_placeholders})
            """,
            (map_id, *allowed_visibility, *allowed_visibility, *allowed_visibility),
        ).fetchall()
        result = row_to_dict(map_row)
        result["locations"] = [row_to_dict(row) for row in locations]
        result["routes"] = [row_to_dict(row) for row in routes]
        result["tokens"] = self.list_map_tokens(map_id, allowed_visibility=allowed_visibility)
        if "kp" not in allowed_visibility:
            result["prompt"] = ""
            filtered_map = GeneratedMap(
                title=result["title"],
                prompt="",
                style=result["style"],
                width=result["width"],
                height=result["height"],
                locations=[
                    GeneratedLocation(
                        name=row["name"],
                        x=row["x"],
                        y=row["y"],
                        visibility=row["visibility"],
                        notes=row["notes"],
                    )
                    for row in locations
                ],
                routes=[
                    GeneratedRoute(
                        start=row["start_name"],
                        end=row["end_name"],
                        travel_time=row["travel_time"],
                        visibility=row["visibility"],
                        notes=row["notes"],
                    )
                    for row in routes
                ],
            )
            result["svg_text"] = render_svg(filtered_map)
        return result

    def find_map_location_id(self, map_id: str, location_name: str) -> str:
        row = self.connection.execute(
            "SELECT id FROM map_locations WHERE map_id = ? AND name = ?",
            (map_id, location_name),
        ).fetchone()
        if row is None:
            raise KeyError(f"Location not found on map {map_id}: {location_name}")
        return str(row["id"])

    def place_map_token(
        self,
        map_id: str,
        label: str,
        location_name: str,
        actor_type: str = "pc",
        actor_id: str | None = None,
        visibility: str = "table",
        color: str = "#b93f2d",
    ) -> dict:
        map_row = self.connection.execute(
            "SELECT campaign_id FROM maps WHERE id = ?", (map_id,)
        ).fetchone()
        if map_row is None:
            raise KeyError(f"Map not found: {map_id}")
        if actor_id and actor_type == "pc":
            actor = self.connection.execute(
                "SELECT id FROM player_characters WHERE id = ? AND campaign_id = ?",
                (actor_id, map_row["campaign_id"]),
            ).fetchone()
            if actor is None:
                raise ValueError("PC token actor does not belong to this campaign")
        if actor_id and actor_type == "npc":
            actor = self.connection.execute(
                """
                SELECT npc_id FROM campaign_npcs WHERE npc_id = ? AND campaign_id = ?
                """,
                (actor_id, map_row["campaign_id"]),
            ).fetchone()
            if actor is None:
                raise ValueError("NPC token actor does not belong to this campaign")
        location_id = self.find_map_location_id(map_id, location_name)
        token_id = new_id("token")
        self.connection.execute(
            """
            INSERT INTO map_tokens
              (id, map_id, label, actor_type, actor_id, location_id, visibility, color)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (token_id, map_id, label, actor_type, actor_id, location_id, visibility, color),
        )
        self.connection.execute(
            """
            INSERT INTO map_token_moves
              (id, token_id, from_location_id, to_location_id, moved_by, note)
            VALUES (?, ?, NULL, ?, 'system', 'placed token')
            """,
            (new_id("move"), token_id, location_id),
        )
        return self.get_map_token(token_id)

    def get_map_token(self, token_id: str) -> dict:
        row = self.connection.execute(
            """
            SELECT t.*, l.name AS location_name, l.x, l.y
            FROM map_tokens t
            JOIN map_locations l ON l.id = t.location_id
            WHERE t.id = ?
            """,
            (token_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Map token not found: {token_id}")
        return row_to_dict(row)

    def list_map_tokens(
        self,
        map_id: str,
        allowed_visibility: tuple[str, ...] = ("player", "table", "kp"),
    ) -> list[dict]:
        visibility_placeholders = ",".join("?" for _ in allowed_visibility)
        rows = self.connection.execute(
            f"""
            SELECT t.*, l.name AS location_name, l.x, l.y
            FROM map_tokens t
            JOIN map_locations l ON l.id = t.location_id
            WHERE t.map_id = ?
              AND t.visibility IN ({visibility_placeholders})
              AND l.visibility IN ({visibility_placeholders})
            ORDER BY t.created_at ASC
            """,
            (map_id, *allowed_visibility, *allowed_visibility),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def move_map_token(
        self,
        token_id: str,
        to_location_name: str,
        moved_by: str = "player",
        note: str = "",
        require_route: bool = True,
        allowed_visibility: tuple[str, ...] | None = None,
        expected_version: int | None = None,
    ) -> dict:
        token = self.get_map_token(token_id)
        if allowed_visibility:
            placeholders = ",".join("?" for _ in allowed_visibility)
            if token["visibility"] not in allowed_visibility:
                raise ValueError("Token is not available in the current view")
            from_location = self.connection.execute(
                f"""SELECT id FROM map_locations
                WHERE id = ? AND visibility IN ({placeholders})""",
                (token["location_id"], *allowed_visibility),
            ).fetchone()
            to_location = self.connection.execute(
                f"""SELECT id FROM map_locations
                WHERE map_id = ? AND name = ? AND visibility IN ({placeholders})""",
                (token["map_id"], to_location_name, *allowed_visibility),
            ).fetchone()
            if from_location is None or to_location is None:
                raise ValueError("Destination is not available in the current view")
            to_location_id = str(to_location["id"])
        else:
            to_location_id = self.find_map_location_id(token["map_id"], to_location_name)
        from_location_id = token["location_id"]
        if require_route and from_location_id != to_location_id:
            visibility_filter = ""
            params: list[object] = [
                token["map_id"],
                from_location_id,
                to_location_id,
                to_location_id,
                from_location_id,
            ]
            if allowed_visibility:
                placeholders = ",".join("?" for _ in allowed_visibility)
                visibility_filter = f"AND visibility IN ({placeholders})"
                params.extend(allowed_visibility)
            route = self.connection.execute(
                f"""
                SELECT id FROM map_routes
                WHERE map_id = ?
                  AND (
                    (start_location_id = ? AND end_location_id = ?)
                    OR (start_location_id = ? AND end_location_id = ?)
                  )
                  {visibility_filter}
                """,
                params,
            ).fetchone()
            if route is None:
                raise ValueError(f"No route from {token['location_name']} to {to_location_name}")
        current_version = int(token.get("version", 0))
        required_version = current_version if expected_version is None else expected_version
        updated = self.connection.execute(
            """
            UPDATE map_tokens
            SET location_id = ?, version = version + 1
            WHERE id = ? AND version = ?
            """,
            (to_location_id, token_id, required_version),
        )
        if updated.rowcount != 1:
            raise ValueError("Map token changed; refresh the map and retry")
        self.connection.execute(
            """
            INSERT INTO map_token_moves
              (id, token_id, from_location_id, to_location_id, moved_by, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_id("move"), token_id, from_location_id, to_location_id, moved_by, note),
        )
        return self.get_map_token(token_id)

    def list_map_token_moves(
        self,
        token_id: str,
        allowed_visibility: tuple[str, ...] | None = None,
    ) -> list[dict]:
        filters = ["m.token_id = ?"]
        params: list[object] = [token_id]
        if allowed_visibility:
            placeholders = ",".join("?" for _ in allowed_visibility)
            filters.append(f"to_location.visibility IN ({placeholders})")
            params.extend(allowed_visibility)
            filters.append(
                f"(from_location.id IS NULL OR from_location.visibility IN ({placeholders}))"
            )
            params.extend(allowed_visibility)
        rows = self.connection.execute(
            f"""
            SELECT
              m.*,
              from_location.name AS from_location_name,
              to_location.name AS to_location_name
            FROM map_token_moves m
            LEFT JOIN map_locations from_location ON from_location.id = m.from_location_id
            JOIN map_locations to_location ON to_location.id = m.to_location_id
            WHERE {" AND ".join(filters)}
            ORDER BY m.created_at ASC
            """,
            params,
        ).fetchall()
        return [row_to_dict(row) for row in rows]

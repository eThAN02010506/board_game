"""SQLite adapter for maps, locations, routes, tokens, and movement history."""

import json
import sqlite3
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.platform.scenes.map_generation import (
    GeneratedLocation,
    GeneratedMap,
    GeneratedRoute,
    map_spec_for_generated_map,
    render_overlay_svg,
    render_svg,
)
from ai_kp.platform.scenes.map_spec import (
    MAP_SPEC_VERSION,
    map_layout_hash,
    map_spec_hash,
    project_map_spec,
    require_valid_map_spec,
)
from ai_kp.infrastructure.database.rows import row_to_dict


class MapRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create_map(self, campaign_id: str, generated_map: GeneratedMap, created_by: str = "ai") -> dict:
        map_id = new_id("map")
        map_spec = map_spec_for_generated_map(generated_map)
        validation_report = (
            generated_map.validation_report or require_valid_map_spec(map_spec).to_dict()
        )
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
        spec_location_by_name = {
            str(item["name"]): item for item in map_spec.get("locations", [])
        }
        location_ids: dict[str, str] = {}
        for order_index, location in enumerate(generated_map.locations):
            location_id = new_id("loc")
            location_ids[location.name] = location_id
            spec_location = spec_location_by_name.get(location.name, {})
            self.connection.execute(
                """
                INSERT INTO map_locations
                  (id, map_id, element_id, name, x, y, visibility, notes, order_index)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    location_id,
                    map_id,
                    spec_location.get("id"),
                    location.name,
                    location.x,
                    location.y,
                    location.visibility,
                    location.notes,
                    order_index,
                ),
            )
        spec_connections = list(map_spec.get("connections", []))
        for route_index, route in enumerate(generated_map.routes):
            start_id = location_ids.get(route.start)
            end_id = location_ids.get(route.end)
            if not start_id or not end_id:
                raise ValueError(f"Unknown route endpoint: {route.start} -> {route.end}")
            spec_connection = (
                spec_connections[route_index]
                if route_index < len(spec_connections)
                else {}
            )
            self.connection.execute(
                """
                INSERT INTO map_routes
                  (id, map_id, element_id, start_location_id, end_location_id,
                   travel_time, visibility, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("route"),
                    map_id,
                    spec_connection.get("id"),
                    start_id,
                    end_id,
                    route.travel_time,
                    route.visibility,
                    route.notes,
                ),
            )
        revision_id = self._create_map_revision(
            map_id,
            map_spec,
            validation_report,
            created_by=created_by,
        )
        self.connection.execute(
            "UPDATE maps SET current_revision_id = ? WHERE id = ?",
            (revision_id, map_id),
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
            SELECT
              m.id, m.campaign_id, m.title, m.prompt, m.style, m.status,
              m.width, m.height, m.created_by, m.created_at,
              m.current_revision_id, m.selected_public_asset_id, m.reveal_version,
              r.revision_no, r.spec_version, r.spec_hash, r.layout_hash
            FROM maps m
            LEFT JOIN map_revisions r ON r.id = m.current_revision_id
            WHERE m.campaign_id = ?
              {status_filter}
            ORDER BY m.created_at DESC
            """,
            (campaign_id,),
        ).fetchall()
        results = [row_to_dict(row) for row in rows]
        if not include_prompt:
            for result in results:
                result["prompt"] = ""
                for private_key in (
                    "current_revision_id",
                    "selected_public_asset_id",
                    "reveal_version",
                    "revision_no",
                    "spec_hash",
                    "layout_hash",
                ):
                    result.pop(private_key, None)
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

    def get_map_publish_snapshot(self, map_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT m.status, m.current_revision_id, m.selected_public_asset_id,
                   r.validation_json
            FROM maps m
            LEFT JOIN map_revisions r ON r.id = m.current_revision_id
            WHERE m.id = ?
            """,
            (map_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Map not found: {map_id}")
        result = row_to_dict(row)
        validation_json = result.pop("validation_json", None)
        result["validation"] = (
            json.loads(validation_json) if validation_json else None
        )
        return result

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
        revision = self.get_current_map_revision(map_id)
        if revision is not None:
            full_spec = revision["spec"]
            projected_spec = project_map_spec(full_spec, allowed_visibility)
            result.update(
                {
                    "revision_id": revision["id"],
                    "revision_no": revision["revision_no"],
                    "spec_version": revision["spec_version"],
                    "spec_hash": revision["spec_hash"],
                    "layout_hash": revision["layout_hash"],
                    "validation": revision["validation"],
                    "map_spec": projected_spec,
                }
            )
        else:
            projected_spec = self._legacy_spec_from_rows(result, locations, routes)

        rendered_map = GeneratedMap(
            title=result["title"],
            prompt=result["prompt"] if "kp" in allowed_visibility else "",
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
            map_spec=projected_spec,
        )
        result["svg_text"] = render_svg(rendered_map)
        result["overlay_svg_text"] = render_overlay_svg(rendered_map)
        if "kp" not in allowed_visibility:
            result["prompt"] = ""
            result.pop("validation", None)
            for private_key in (
                "current_revision_id",
                "selected_public_asset_id",
                "reveal_version",
                "revision_id",
                "revision_no",
                "spec_hash",
                "layout_hash",
            ):
                result.pop(private_key, None)
        selected_asset = self._selected_public_asset(map_id)
        result["render"] = {
            "background_asset_url": (
                f"/map-assets/{selected_asset['id']}/content" if selected_asset else None
            ),
            "selected_asset_id": selected_asset["id"] if selected_asset else None,
            "asset_status": selected_asset["status"] if selected_asset else "none",
            "fallback_svg": selected_asset is None,
        }
        if "kp" in allowed_visibility:
            result["assets"] = [
                self._asset_for_response(item) for item in self.list_map_assets(map_id)
            ]
        return result

    def _create_map_revision(
        self,
        map_id: str,
        map_spec: dict[str, Any],
        validation_report: dict[str, Any],
        *,
        created_by: str,
    ) -> str:
        revision_id = new_id("maprev")
        revision_no = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(revision_no), 0) + 1 FROM map_revisions WHERE map_id = ?",
                (map_id,),
            ).fetchone()[0]
        )
        self.connection.execute(
            """
            INSERT INTO map_revisions
              (id, map_id, revision_no, spec_version, spec_json, spec_hash, layout_hash,
               validation_json, source_kind, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision_id,
                map_id,
                revision_no,
                MAP_SPEC_VERSION,
                json.dumps(map_spec, ensure_ascii=False, sort_keys=True),
                map_spec_hash(map_spec),
                map_layout_hash(map_spec),
                json.dumps(validation_report, ensure_ascii=False, sort_keys=True),
                str(map_spec.get("provenance", {}).get("source_kind", "kp_brief")),
                created_by,
            ),
        )
        return revision_id

    def get_current_map_revision(self, map_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT r.*
            FROM maps m
            JOIN map_revisions r ON r.id = m.current_revision_id
            WHERE m.id = ?
            """,
            (map_id,),
        ).fetchone()
        if row is None:
            return None
        result = row_to_dict(row)
        result["spec"] = json.loads(result.pop("spec_json"))
        result["validation"] = json.loads(result.pop("validation_json"))
        return result

    def _legacy_spec_from_rows(
        self,
        map_row: dict[str, Any],
        locations: list[sqlite3.Row],
        routes: list[sqlite3.Row],
    ) -> dict[str, Any]:
        legacy_map = GeneratedMap(
            title=map_row["title"],
            prompt=map_row["prompt"],
            style=map_row["style"],
            width=map_row["width"],
            height=map_row["height"],
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
        return map_spec_for_generated_map(legacy_map)

    def create_map_asset(
        self,
        *,
        map_id: str,
        revision_id: str,
        generation_input_hash: str,
        content_hash: str | None,
        storage_path: str | None,
        mime_type: str | None,
        width: int | None,
        height: int | None,
        provider: str,
        model: str,
        seed: int | None,
        parameters: dict[str, Any],
        prompt_text: str,
        status: str = "ready",
        error_text: str | None = None,
    ) -> dict[str, Any]:
        asset_id = new_id("mapasset")
        self.connection.execute(
            """
            INSERT INTO map_assets
              (id, map_id, revision_id, audience, kind, status, generation_input_hash,
               content_hash, storage_path, mime_type, width, height, provider, model, seed,
               parameters_json, prompt_text, error_text)
            VALUES (?, ?, ?, 'table', 'background', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                map_id,
                revision_id,
                status,
                generation_input_hash,
                content_hash,
                storage_path,
                mime_type,
                width,
                height,
                provider,
                model,
                seed,
                json.dumps(parameters, ensure_ascii=False, sort_keys=True),
                prompt_text,
                error_text,
            ),
        )
        return self.get_map_asset(asset_id)

    def repair_map_asset(
        self,
        asset_id: str,
        *,
        revision_id: str,
        content_hash: str,
        storage_path: str,
        mime_type: str,
        width: int,
        height: int,
        provider: str,
        model: str,
        seed: int | None,
        parameters: dict[str, Any],
        prompt_text: str,
    ) -> dict[str, Any]:
        updated = self.connection.execute(
            """
            UPDATE map_assets
            SET revision_id = ?, status = 'ready', content_hash = ?, storage_path = ?,
                mime_type = ?, width = ?, height = ?, provider = ?, model = ?, seed = ?,
                parameters_json = ?, prompt_text = ?, error_text = NULL
            WHERE id = ?
            """,
            (
                revision_id,
                content_hash,
                storage_path,
                mime_type,
                width,
                height,
                provider,
                model,
                seed,
                json.dumps(parameters, ensure_ascii=False, sort_keys=True),
                prompt_text,
                asset_id,
            ),
        )
        if updated.rowcount != 1:
            raise KeyError(f"Map asset not found: {asset_id}")
        return self.get_map_asset(asset_id)

    def find_map_asset_by_generation_hash(
        self,
        map_id: str,
        generation_input_hash: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM map_assets
            WHERE map_id = ? AND generation_input_hash = ? AND status = 'ready'
            """,
            (map_id, generation_input_hash),
        ).fetchone()
        return self._decode_asset(row) if row is not None else None

    def get_map_asset(self, asset_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM map_assets WHERE id = ?",
            (asset_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Map asset not found: {asset_id}")
        return self._decode_asset(row)

    def is_map_asset_player_visible(self, asset_id: str) -> bool:
        row = self.connection.execute(
            """
            SELECT 1
            FROM map_assets a
            JOIN maps m ON m.id = a.map_id
            WHERE a.id = ?
              AND m.status = 'published'
              AND m.selected_public_asset_id = a.id
              AND a.audience = 'table'
              AND a.status = 'ready'
            """,
            (asset_id,),
        ).fetchone()
        return row is not None

    def list_map_assets(self, map_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM map_assets WHERE map_id = ? ORDER BY created_at DESC, id DESC",
            (map_id,),
        ).fetchall()
        return [self._decode_asset(row) for row in rows]

    def select_public_map_asset(self, map_id: str, asset_id: str) -> dict[str, Any]:
        asset = self.get_map_asset(asset_id)
        if asset["map_id"] != map_id or asset["audience"] != "table":
            raise ValueError("Map asset does not belong to this public map view")
        if asset["status"] != "ready":
            raise ValueError("Only a ready map asset can be selected")
        current_revision = self.get_current_map_revision(map_id)
        if current_revision is None or asset["revision_id"] != current_revision["id"]:
            raise ValueError("Map asset belongs to an older map revision")
        self.connection.execute(
            "UPDATE maps SET selected_public_asset_id = ? WHERE id = ?",
            (asset_id, map_id),
        )
        return asset

    def _selected_public_asset(self, map_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT a.*
            FROM maps m
            JOIN map_assets a ON a.id = m.selected_public_asset_id
            WHERE m.id = ? AND a.status = 'ready' AND a.audience = 'table'
            """,
            (map_id,),
        ).fetchone()
        return self._decode_asset(row) if row is not None else None

    @staticmethod
    def _decode_asset(row: sqlite3.Row) -> dict[str, Any]:
        result = row_to_dict(row)
        result["parameters"] = json.loads(result.pop("parameters_json"))
        result["content_url"] = f"/map-assets/{result['id']}/content"
        return result

    @staticmethod
    def _asset_for_response(asset: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in asset.items()
            if key not in {"storage_path"}
        }

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

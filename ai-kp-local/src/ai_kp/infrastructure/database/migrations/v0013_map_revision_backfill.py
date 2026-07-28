"""Migration 13: backfill legacy map specs and guard map revision pointers."""

from hashlib import sha256
import json
import sqlite3


VERSION = 13
NAME = "backfill_map_revisions_and_guard_pointers"
MAP_SPEC_VERSION = "map-spec.v1"


CURRENT_REVISION_GUARD_DDL = """
CREATE TRIGGER IF NOT EXISTS trg_maps_current_revision_guard
BEFORE UPDATE OF current_revision_id ON maps
WHEN NEW.current_revision_id IS NOT NULL
BEGIN
  SELECT CASE
    WHEN NOT EXISTS (
      SELECT 1 FROM map_revisions
      WHERE id = NEW.current_revision_id AND map_id = NEW.id
    )
    THEN RAISE(ABORT, 'map current revision must belong to the same map')
  END;
  SELECT CASE
    WHEN NEW.selected_public_asset_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM map_assets
      WHERE id = NEW.selected_public_asset_id
        AND map_id = NEW.id
        AND revision_id = NEW.current_revision_id
        AND audience = 'table'
        AND status = 'ready'
    )
    THEN RAISE(ABORT, 'selected map asset must match the current revision')
  END;
END
"""


SELECTED_ASSET_GUARD_DDL = """
CREATE TRIGGER IF NOT EXISTS trg_maps_selected_asset_guard
BEFORE UPDATE OF selected_public_asset_id ON maps
WHEN NEW.selected_public_asset_id IS NOT NULL
BEGIN
  SELECT CASE
    WHEN NOT EXISTS (
      SELECT 1 FROM map_assets
      WHERE id = NEW.selected_public_asset_id
        AND map_id = NEW.id
        AND revision_id = NEW.current_revision_id
        AND audience = 'table'
        AND status = 'ready'
    )
    THEN RAISE(ABORT, 'selected map asset must be a ready current public asset')
  END;
END
"""


def _legacy_spec(connection: sqlite3.Connection, map_row: sqlite3.Row) -> dict:
    location_rows = connection.execute(
        """
        SELECT id, element_id, name, x, y, visibility, notes
        FROM map_locations
        WHERE map_id = ?
        ORDER BY order_index, id
        """,
        (map_row["id"],),
    ).fetchall()
    route_rows = connection.execute(
        """
        SELECT r.id, r.element_id, r.start_location_id, r.end_location_id,
               start.name AS start_name, end.name AS end_name,
               r.travel_time, r.visibility, r.notes
        FROM map_routes r
        JOIN map_locations start ON start.id = r.start_location_id
        JOIN map_locations end ON end.id = r.end_location_id
        WHERE r.map_id = ?
        ORDER BY r.id
        """,
        (map_row["id"],),
    ).fetchall()
    locations = [
        {
            "id": str(row["element_id"] or row["id"]),
            "name": str(row["name"]),
            "kind": "place",
            "position": {"x": float(row["x"]), "y": float(row["y"])},
            "visibility": str(row["visibility"]),
            "public_description": str(row["notes"]),
            "kp_notes": "",
            "tags": [],
            "render_policy": "structure_overlay",
        }
        for row in location_rows
    ]
    connections = [
        {
            "id": str(row["element_id"] or row["id"]),
            "from_location_id": str(row["start_location_id"]),
            "to_location_id": str(row["end_location_id"]),
            "from_name": str(row["start_name"]),
            "to_name": str(row["end_name"]),
            "kind": "route",
            "direction": "both",
            "traversal": {
                "allowed": True,
                "locked": False,
                "travel_time": row["travel_time"],
            },
            "visibility": str(row["visibility"]),
            "public_description": str(row["notes"]),
            "kp_notes": "",
        }
        for row in route_rows
    ]
    return {
        "schema_version": MAP_SPEC_VERSION,
        "title": str(map_row["title"]),
        "scene_brief": str(map_row["prompt"]),
        "style": str(map_row["style"]),
        "map_kind": "regional",
        "canvas": {
            "width": int(map_row["width"]),
            "height": int(map_row["height"]),
            "coordinate_unit": "logical_px",
            "origin": "top_left",
        },
        "era": {
            "year": None,
            "locale": "",
            "season": "",
            "time_of_day": "",
            "weather": "",
            "public_architecture": [
                "historically neutral materials",
                "restrained practical construction",
            ],
            "technology": [],
            "forbidden_visuals": [
                "anachronistic electronics",
                "unverified modern branding",
            ],
        },
        "locations": locations,
        "connections": connections,
        "features": [],
        "spawn_points": [],
        "visual_brief": {
            "viewpoint": "orthographic_top_down",
            "style_preset": "period_illustrated_map",
            "palette": [
                "aged parchment",
                "charcoal",
                "muted umber",
                "weathered stone",
            ],
            "lighting": "clear readable practical lighting",
            "text_policy": "no_generated_text",
            "people_policy": "no_people",
            "clutter_level": "medium",
        },
        "coverage": {
            "required_element_names": list(
                dict.fromkeys(
                    str(row["name"]).strip()
                    for row in location_rows
                    if str(row["name"]).strip()
                )
            )
        },
        "provenance": {
            "source_kind": "legacy_map_migration",
            "campaign_time": None,
            "generated_assumptions": [
                "The legacy map did not store structured era metadata."
            ],
            "kp_review_forbidden_visuals": [],
            "prompt_version": "map-spec-v1",
        },
    }


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _spec_hash(spec: dict) -> str:
    return sha256(_canonical_json(spec).encode("utf-8")).hexdigest()


def _layout_hash(spec: dict) -> str:
    layout = {
        "schema_version": spec.get("schema_version"),
        "map_kind": spec.get("map_kind"),
        "canvas": spec.get("canvas"),
        "locations": spec.get("locations", []),
        "connections": spec.get("connections", []),
        "features": spec.get("features", []),
        "visual_brief": spec.get("visual_brief", {}),
    }
    return sha256(_canonical_json(layout).encode("utf-8")).hexdigest()


def _validate_legacy_locations(
    spec: dict,
    issues: list[dict],
    *,
    width: int,
    height: int,
) -> set[str]:
    valid_visibility = {"player", "table", "kp"}
    location_ids: set[str] = set()
    location_names: set[str] = set()
    for location in spec["locations"]:
        element_id = str(location["id"])
        name = str(location["name"]).strip()
        if not element_id or element_id in location_ids:
            issues.append(
                _legacy_issue(
                    "error",
                    "duplicate_location_id",
                    "旧地图地点 ID 为空或重复。",
                    element_id,
                )
            )
        if not name or name in location_names:
            issues.append(
                _legacy_issue(
                    "error",
                    "duplicate_location_name",
                    "旧地图地点名称为空或重复。",
                    element_id,
                )
            )
        location_ids.add(element_id)
        location_names.add(name)
        position = location["position"]
        if not (
            0 <= float(position["x"]) <= width
            and 0 <= float(position["y"]) <= height
        ):
            issues.append(
                _legacy_issue(
                    "error",
                    "location_out_of_bounds",
                    f"旧地图地点“{name}”超出画布。",
                    element_id,
                )
            )
        if location["visibility"] not in valid_visibility:
            issues.append(
                _legacy_issue(
                    "error",
                    "visibility",
                    f"旧地图地点“{name}”可见性非法。",
                    element_id,
                )
            )
    return location_ids


def _validate_legacy_connections(
    spec: dict,
    issues: list[dict],
    *,
    location_ids: set[str],
) -> None:
    valid_visibility = {"player", "table", "kp"}
    for connection in spec["connections"]:
        element_id = str(connection["id"])
        if (
            connection["from_location_id"] not in location_ids
            or connection["to_location_id"] not in location_ids
        ):
            issues.append(
                _legacy_issue(
                    "error",
                    "unknown_route_endpoint",
                    "旧地图路线引用了不存在的地点。",
                    element_id,
                )
            )
        if connection["visibility"] not in valid_visibility:
            issues.append(
                _legacy_issue(
                    "error",
                    "visibility",
                    "旧地图路线可见性非法。",
                    element_id,
                )
            )


def _legacy_issue(
    level: str,
    code: str,
    message: str,
    element_id: str | None = None,
) -> dict:
    return {
        "level": level,
        "code": code,
        "message": message,
        "element_id": element_id,
    }


def _legacy_validation(spec: dict) -> dict:
    """Frozen best-effort validation for data created before MapSpec existed."""
    issues: list[dict] = []

    canvas = spec["canvas"]
    width = int(canvas["width"])
    height = int(canvas["height"])
    if width < 320 or height < 240:
        issues.append(
            _legacy_issue(
                "error",
                "canvas_size",
                "旧地图画布小于当前最低尺寸，需要 KP 审核。",
            )
        )

    location_ids = _validate_legacy_locations(
        spec,
        issues,
        width=width,
        height=height,
    )
    _validate_legacy_connections(spec, issues, location_ids=location_ids)
    issues.append(
        _legacy_issue(
            "warning",
            "legacy_era_unknown",
            "旧地图没有结构化年代资料，请由 KP 审核。",
        )
    )
    required_count = len(spec["coverage"]["required_element_names"])
    return {
        "schema_version": MAP_SPEC_VERSION,
        "valid": not any(issue["level"] == "error" for issue in issues),
        "coverage": {
            "required": required_count,
            "covered": required_count,
            "percent": 100,
        },
        "issues": issues,
    }


def _backfill_revisions(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        UPDATE maps
        SET selected_public_asset_id = NULL
        WHERE selected_public_asset_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM map_assets a
            WHERE a.id = maps.selected_public_asset_id AND a.map_id = maps.id
          )
        """
    )
    connection.execute(
        """
        UPDATE maps
        SET current_revision_id = NULL
        WHERE current_revision_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM map_revisions r
            WHERE r.id = maps.current_revision_id AND r.map_id = maps.id
          )
        """
    )
    maps = connection.execute(
        "SELECT * FROM maps WHERE current_revision_id IS NULL ORDER BY created_at, id"
    ).fetchall()
    for map_row in maps:
        existing = connection.execute(
            "SELECT id FROM map_revisions WHERE map_id = ? ORDER BY revision_no LIMIT 1",
            (map_row["id"],),
        ).fetchone()
        if existing is not None:
            revision_id = str(existing["id"])
        else:
            spec = _legacy_spec(connection, map_row)
            validation = _legacy_validation(spec)
            revision_id = "maprev_" + sha256(
                f"legacy:{map_row['id']}".encode()
            ).hexdigest()[:16]
            connection.execute(
                """
                INSERT INTO map_revisions
                  (id, map_id, revision_no, spec_version, spec_json, spec_hash,
                   layout_hash, validation_json, source_kind, created_by)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, 'legacy_map_migration', ?)
                """,
                (
                    revision_id,
                    map_row["id"],
                    MAP_SPEC_VERSION,
                    json.dumps(spec, ensure_ascii=False, sort_keys=True),
                    _spec_hash(spec),
                    _layout_hash(spec),
                    json.dumps(validation, ensure_ascii=False, sort_keys=True),
                    map_row["created_by"],
                ),
            )
        connection.execute(
            "UPDATE maps SET current_revision_id = ? WHERE id = ?",
            (revision_id, map_row["id"]),
        )
    connection.execute(
        """
        UPDATE maps
        SET selected_public_asset_id = NULL
        WHERE selected_public_asset_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM map_assets a
            WHERE a.id = maps.selected_public_asset_id
              AND a.map_id = maps.id
              AND a.revision_id = maps.current_revision_id
              AND a.audience = 'table'
              AND a.status = 'ready'
          )
        """
    )


def migrate(connection: sqlite3.Connection) -> None:
    _backfill_revisions(connection)
    connection.execute(CURRENT_REVISION_GUARD_DDL)
    connection.execute(SELECTED_ASSET_GUARD_DDL)

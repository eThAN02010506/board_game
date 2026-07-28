"""Versioned, validated map specifications and era-safe visual briefs."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

MAP_SPEC_VERSION = "map-spec.v1"
VALID_VISIBILITY = {"player", "table", "kp"}
VALID_MAP_KINDS = {"regional", "site", "floorplan"}


@dataclass(frozen=True)
class MapValidationIssue:
    level: str
    code: str
    message: str
    element_id: str | None = None


@dataclass(frozen=True)
class MapValidationReport:
    schema_version: str
    issues: tuple[MapValidationIssue, ...]
    required_count: int
    covered_count: int

    @property
    def valid(self) -> bool:
        return not any(issue.level == "error" for issue in self.issues)

    @property
    def coverage_percent(self) -> int:
        if self.required_count == 0:
            return 100
        return round(self.covered_count / self.required_count * 100)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "valid": self.valid,
            "coverage": {
                "required": self.required_count,
                "covered": self.covered_count,
                "percent": self.coverage_percent,
            },
            "issues": [asdict(issue) for issue in self.issues],
        }


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def map_spec_hash(spec: dict[str, Any]) -> str:
    return sha256(canonical_json(spec).encode("utf-8")).hexdigest()


def map_layout_hash(spec: dict[str, Any]) -> str:
    layout = {
        "schema_version": spec.get("schema_version"),
        "map_kind": spec.get("map_kind"),
        "canvas": spec.get("canvas"),
        "locations": spec.get("locations", []),
        "connections": spec.get("connections", []),
        "features": spec.get("features", []),
        "visual_brief": spec.get("visual_brief", {}),
    }
    return sha256(canonical_json(layout).encode("utf-8")).hexdigest()


def infer_era_year(explicit_year: int | None, *sources: str | None) -> int | None:
    if explicit_year is not None:
        return explicit_year
    for source in sources:
        if not source:
            continue
        match = re.search(r"(?<!\d)(1[0-9]{3}|20[0-9]{2}|21[0-9]{2})(?!\d)", source)
        if match:
            return int(match.group(1))
    return None


def _era_defaults(year: int | None) -> tuple[list[str], list[str], list[str], list[str]]:
    if year is None:
        return (
            ["historically neutral materials", "restrained practical construction"],
            [],
            ["anachronistic electronics", "unverified modern branding"],
            ["aged parchment", "charcoal", "muted umber", "weathered stone"],
        )
    if year < 1880:
        return (
            ["masonry", "timber framing", "hand-finished joinery"],
            ["candles", "oil lamps", "hand tools", "horse-drawn transport"],
            ["electric lighting", "telephones", "motor vehicles", "plastic furniture"],
            ["aged parchment", "lamp black", "raw umber", "oxidized copper"],
        )
    if year <= 1945:
        return (
            ["brick masonry", "dark timber", "painted plaster", "brass fittings"],
            ["wired telephone", "tungsten lamps", "mechanical typewriter", "period vehicles"],
            [
                "LED lighting",
                "LCD screens",
                "CCTV cameras",
                "modern plastic furniture",
                "post-war vehicles",
                "digital signage",
            ],
            ["aged paper", "dark walnut", "muted brass", "brick red", "smoke grey"],
        )
    if year <= 1989:
        return (
            ["painted concrete", "wood veneer", "steel fittings", "period masonry"],
            ["landline telephone", "fluorescent lamps", "analog equipment", "period vehicles"],
            ["smartphones", "flat-panel displays", "LED strips", "modern electric vehicles"],
            ["warm grey", "faded teal", "tobacco brown", "institutional green"],
        )
    return (
        ["locally appropriate contemporary construction"],
        ["period-appropriate contemporary equipment"],
        [],
        ["neutral stone", "charcoal", "natural wood", "muted accent color"],
    )


def _element_id(prefix: str, name: str, index: int) -> str:
    digest = sha256(f"{prefix}:{index}:{name}".encode()).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _floorprint(x: float, y: float, width: int, height: int) -> dict[str, Any]:
    room_width = max(150.0, min(240.0, width * 0.2))
    room_height = max(100.0, min(160.0, height * 0.2))
    left = max(24.0, min(width - room_width - 24.0, x - room_width / 2))
    top = max(82.0, min(height - room_height - 24.0, y - room_height / 2))
    return {
        "type": "rectangle",
        "x": round(left, 2),
        "y": round(top, 2),
        "width": round(room_width, 2),
        "height": round(room_height, 2),
    }


def build_map_spec(
    *,
    title: str,
    prompt: str,
    style: str,
    width: int,
    height: int,
    locations: Iterable[dict[str, Any]],
    routes: Iterable[dict[str, Any]],
    map_kind: str = "regional",
    era_year: int | None = None,
    locale: str = "",
    season: str = "",
    time_of_day: str = "",
    weather: str = "",
    public_architecture: Iterable[str] = (),
    feature_names: Iterable[str] = (),
    required_elements: Iterable[str] = (),
    forbidden_elements: Iterable[str] = (),
    visual_style: str = "period_illustrated_map",
    campaign_time: str | None = None,
    source_kind: str = "kp_brief",
) -> dict[str, Any]:
    location_rows = [dict(item) for item in locations]
    route_rows = [dict(item) for item in routes]
    resolved_year = infer_era_year(era_year, campaign_time, title, prompt)
    default_architecture, technology, default_forbidden, palette = _era_defaults(resolved_year)
    public_architecture_values = list(
        dict.fromkeys(item.strip() for item in public_architecture if item.strip())
    )
    review_forbidden_values = list(
        dict.fromkeys(item.strip() for item in forbidden_elements if item.strip())
    )

    name_to_id: dict[str, str] = {}
    spec_locations: list[dict[str, Any]] = []
    for index, location in enumerate(location_rows):
        name = str(location["name"]).strip()
        element_id = str(location.get("element_id") or _element_id("loc", name, index))
        name_to_id[name] = element_id
        entry: dict[str, Any] = {
            "id": element_id,
            "name": name,
            "kind": "room" if map_kind == "floorplan" else "place",
            "position": {
                "x": round(float(location["x"]), 2),
                "y": round(float(location["y"]), 2),
            },
            "visibility": str(location.get("visibility", "table")),
            "public_description": str(location.get("notes", "")),
            "kp_notes": str(location.get("kp_notes", "")),
            "tags": [],
            "render_policy": "structure_overlay",
        }
        if map_kind == "floorplan":
            entry["footprint"] = _floorprint(
                float(location["x"]),
                float(location["y"]),
                width,
                height,
            )
        spec_locations.append(entry)

    spec_connections: list[dict[str, Any]] = []
    for index, route in enumerate(route_rows):
        start_name = str(route["start"]).strip()
        end_name = str(route["end"]).strip()
        spec_connections.append(
            {
                "id": str(
                    route.get("element_id")
                    or _element_id("conn", f"{start_name}>{end_name}", index)
                ),
                "from_location_id": name_to_id.get(start_name, ""),
                "to_location_id": name_to_id.get(end_name, ""),
                "from_name": start_name,
                "to_name": end_name,
                "kind": "door" if map_kind == "floorplan" else "route",
                "direction": "both",
                "traversal": {
                    "allowed": True,
                    "locked": False,
                    "travel_time": route.get("travel_time"),
                },
                "visibility": str(route.get("visibility", "table")),
                "public_description": str(route.get("notes", "")),
                "kp_notes": str(route.get("kp_notes", "")),
            }
        )

    public_locations = [
        item for item in spec_locations if item["visibility"] in {"player", "table"}
    ]
    spec_features: list[dict[str, Any]] = []
    for index, raw_name in enumerate(feature_names):
        name = raw_name.strip()
        if not name:
            continue
        location = public_locations[index % len(public_locations)] if public_locations else None
        position = (
            {
                "x": round(float(location["position"]["x"]) + 18.0, 2),
                "y": round(float(location["position"]["y"]) + 24.0, 2),
            }
            if location
            else {"x": round(width / 2, 2), "y": round(height / 2, 2)}
        )
        spec_features.append(
            {
                "id": _element_id("feature", name, index),
                "name": name,
                "location_id": location["id"] if location else None,
                "kind": "scene_element",
                "position": position,
                "visual_description": name,
                "visibility": "table",
                "render_policy": "background_or_overlay",
            }
        )

    available_names = [
        *(item["name"] for item in spec_locations),
        *(item["name"] for item in spec_features),
    ]
    required_values = list(
        dict.fromkeys(item.strip() for item in required_elements if item.strip())
    )
    if not required_values:
        required_values = available_names

    return {
        "schema_version": MAP_SPEC_VERSION,
        "title": title.strip(),
        "scene_brief": prompt.strip(),
        "style": style,
        "map_kind": map_kind,
        "canvas": {
            "width": width,
            "height": height,
            "coordinate_unit": "logical_px",
            "origin": "top_left",
        },
        "era": {
            "year": resolved_year,
            "locale": locale.strip(),
            "season": season.strip(),
            "time_of_day": time_of_day.strip(),
            "weather": weather.strip(),
            "public_architecture": public_architecture_values or default_architecture,
            "technology": technology,
            # Only deterministic era defaults may enter the public image prompt.
            # Free-form KP review notes live in provenance and are never projected.
            "forbidden_visuals": default_forbidden,
        },
        "locations": spec_locations,
        "connections": spec_connections,
        "features": spec_features,
        "spawn_points": [],
        "visual_brief": {
            "viewpoint": "orthographic_top_down",
            "style_preset": visual_style,
            "palette": palette,
            "lighting": time_of_day.strip() or "clear readable practical lighting",
            "text_policy": "no_generated_text",
            "people_policy": "no_people",
            "clutter_level": "medium",
        },
        "coverage": {
            "required_element_names": required_values,
        },
        "provenance": {
            "source_kind": source_kind,
            "campaign_time": campaign_time,
            "generated_assumptions": [],
            "kp_review_forbidden_visuals": review_forbidden_values,
            "prompt_version": "map-spec-v1",
        },
    }


def _number(value: Any, default: float = -1) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _validate_header(
    spec: dict[str, Any],
    issues: list[MapValidationIssue],
) -> tuple[int, int]:
    canvas = spec.get("canvas") if isinstance(spec.get("canvas"), dict) else {}
    width = int(_number(canvas.get("width"), 0))
    height = int(_number(canvas.get("height"), 0))
    if spec.get("schema_version") != MAP_SPEC_VERSION:
        issues.append(
            MapValidationIssue("error", "schema_version", "不支持的地图规范版本。")
        )
    if spec.get("map_kind") not in VALID_MAP_KINDS:
        issues.append(MapValidationIssue("error", "map_kind", "地图类型不受支持。"))
    if width < 320 or height < 240:
        issues.append(MapValidationIssue("error", "canvas_size", "地图画布尺寸过小。"))
    return width, height


def _validate_locations(
    spec: dict[str, Any],
    width: int,
    height: int,
    issues: list[MapValidationIssue],
) -> tuple[set[str], set[str]]:
    locations = spec.get("locations") if isinstance(spec.get("locations"), list) else []
    location_ids: set[str] = set()
    location_names: set[str] = set()
    for location in locations:
        if not isinstance(location, dict):
            issues.append(MapValidationIssue("error", "location_shape", "地点必须是对象。"))
            continue
        element_id = str(location.get("id", ""))
        name = str(location.get("name", "")).strip()
        if not element_id or element_id in location_ids:
            issues.append(
                MapValidationIssue("error", "duplicate_location_id", "地点 ID 为空或重复。", element_id)
            )
        if not name or name in location_names:
            issues.append(
                MapValidationIssue("error", "duplicate_location_name", "地点名称为空或重复。", element_id)
            )
        location_ids.add(element_id)
        location_names.add(name)
        position = location.get("position") if isinstance(location.get("position"), dict) else {}
        x = _number(position.get("x"))
        y = _number(position.get("y"))
        if not (0 <= x <= width and 0 <= y <= height):
            issues.append(
                MapValidationIssue("error", "location_out_of_bounds", f"地点“{name}”超出画布。", element_id)
            )
        if location.get("visibility") not in VALID_VISIBILITY:
            issues.append(
                MapValidationIssue("error", "visibility", f"地点“{name}”可见性非法。", element_id)
            )
    return location_ids, location_names


def _validate_connections(
    spec: dict[str, Any],
    location_ids: set[str],
    issues: list[MapValidationIssue],
) -> dict[str, set[str]]:
    connections = (
        spec.get("connections") if isinstance(spec.get("connections"), list) else []
    )
    connection_ids: set[str] = set()
    adjacency: dict[str, set[str]] = {item: set() for item in location_ids}
    for connection in connections:
        if not isinstance(connection, dict):
            issues.append(MapValidationIssue("error", "connection_shape", "路线必须是对象。"))
            continue
        element_id = str(connection.get("id", ""))
        start_id = str(connection.get("from_location_id", ""))
        end_id = str(connection.get("to_location_id", ""))
        if not element_id or element_id in connection_ids:
            issues.append(
                MapValidationIssue("error", "duplicate_connection_id", "路线 ID 为空或重复。", element_id)
            )
        connection_ids.add(element_id)
        if start_id not in location_ids or end_id not in location_ids:
            label = f"{connection.get('from_name', '?')} → {connection.get('to_name', '?')}"
            issues.append(
                MapValidationIssue(
                    "error",
                    "unknown_route_endpoint",
                    f"路线“{label}”引用了不存在的地点。",
                    element_id,
                )
            )
            continue
        adjacency[start_id].add(end_id)
        adjacency[end_id].add(start_id)
        if connection.get("visibility") not in VALID_VISIBILITY:
            issues.append(
                MapValidationIssue("error", "visibility", "路线可见性非法。", element_id)
            )
    return adjacency


def _validate_connectivity(
    location_ids: set[str],
    adjacency: dict[str, set[str]],
    issues: list[MapValidationIssue],
) -> None:
    if len(location_ids) <= 1:
        return
    start = next(iter(location_ids))
    visited = {start}
    pending = [start]
    while pending:
        current = pending.pop()
        for neighbor in adjacency[current] - visited:
            visited.add(neighbor)
            pending.append(neighbor)
    if visited != location_ids:
        issues.append(
            MapValidationIssue(
                "warning",
                "disconnected_locations",
                "部分地点没有连通路线；请确认这是刻意设计。",
            )
        )


def _validate_features(
    spec: dict[str, Any],
    location_ids: set[str],
    width: int,
    height: int,
    issues: list[MapValidationIssue],
) -> set[str]:
    features = spec.get("features") if isinstance(spec.get("features"), list) else []
    feature_ids: set[str] = set()
    feature_names: set[str] = set()
    for feature in features:
        if not isinstance(feature, dict):
            issues.append(MapValidationIssue("error", "feature_shape", "场景元素必须是对象。"))
            continue
        element_id = str(feature.get("id", ""))
        name = str(feature.get("name", "")).strip()
        if not element_id or element_id in feature_ids:
            issues.append(
                MapValidationIssue("error", "duplicate_feature_id", "场景元素 ID 为空或重复。", element_id)
            )
        if not name or name in feature_names:
            issues.append(
                MapValidationIssue(
                    "error",
                    "duplicate_feature_name",
                    "场景元素名称为空或重复。",
                    element_id,
                )
            )
        feature_ids.add(element_id)
        feature_names.add(name)
        location_id = feature.get("location_id")
        if location_id is not None and location_id not in location_ids:
            issues.append(
                MapValidationIssue(
                    "error",
                    "unknown_feature_location",
                    f"场景元素“{name}”引用了不存在的地点。",
                    element_id,
                )
            )
        position = feature.get("position")
        if isinstance(position, dict) and not (
            0 <= _number(position.get("x")) <= width
            and 0 <= _number(position.get("y")) <= height
        ):
            issues.append(
                MapValidationIssue(
                    "error",
                    "feature_out_of_bounds",
                    f"场景元素“{name}”超出画布。",
                    element_id,
                )
            )
        if feature.get("visibility") not in VALID_VISIBILITY:
            issues.append(
                MapValidationIssue(
                    "error",
                    "visibility",
                    f"场景元素“{name}”可见性非法。",
                    element_id,
                )
            )
    return feature_names


def _validate_coverage(
    spec: dict[str, Any],
    available_names: set[str],
    issues: list[MapValidationIssue],
) -> tuple[int, int]:
    coverage = spec.get("coverage") if isinstance(spec.get("coverage"), dict) else {}
    required = coverage.get("required_element_names", [])
    if not isinstance(required, list):
        issues.append(
            MapValidationIssue("error", "coverage_shape", "必需元素清单必须是数组。")
        )
        required = []
    required_names = {str(item).strip() for item in required if str(item).strip()}
    covered_names = required_names & available_names
    for missing in sorted(required_names - available_names):
        issues.append(
            MapValidationIssue(
                "error",
                "required_element_missing",
                f"必需元素“{missing}”没有进入结构化地图。",
            )
        )
    return len(required_names), len(covered_names)


def validate_map_spec(spec: dict[str, Any]) -> MapValidationReport:
    issues: list[MapValidationIssue] = []
    width, height = _validate_header(spec, issues)
    location_ids, location_names = _validate_locations(
        spec,
        width,
        height,
        issues,
    )
    adjacency = _validate_connections(spec, location_ids, issues)
    _validate_connectivity(location_ids, adjacency, issues)
    feature_names = _validate_features(spec, location_ids, width, height, issues)
    required_count, covered_count = _validate_coverage(
        spec,
        location_names | feature_names,
        issues,
    )
    era = spec.get("era") if isinstance(spec.get("era"), dict) else {}
    if era.get("year") is None:
        issues.append(
            MapValidationIssue(
                "warning",
                "era_unknown",
                "没有可靠年代；视觉提示将避免未经确认的现代细节。",
            )
        )

    return MapValidationReport(
        schema_version=MAP_SPEC_VERSION,
        issues=tuple(issues),
        required_count=required_count,
        covered_count=covered_count,
    )


def require_valid_map_spec(spec: dict[str, Any]) -> MapValidationReport:
    report = validate_map_spec(spec)
    errors = [issue.message for issue in report.issues if issue.level == "error"]
    if errors:
        raise ValueError("地图规范校验失败：" + "；".join(errors))
    return report


def project_map_spec(
    spec: dict[str, Any],
    allowed_visibility: tuple[str, ...],
) -> dict[str, Any]:
    allowed = set(allowed_visibility)
    if "kp" in allowed:
        return deepcopy(spec)

    locations = [
        _copy_public_fields(
            item,
            (
                "id",
                "name",
                "kind",
                "position",
                "footprint",
                "visibility",
                "public_description",
                "render_policy",
            ),
        )
        for item in spec.get("locations", [])
        if item.get("visibility") in allowed
    ]
    location_ids = {str(item["id"]) for item in locations}
    connections = [
        _copy_public_fields(
            item,
            (
                "id",
                "from_location_id",
                "to_location_id",
                "from_name",
                "to_name",
                "kind",
                "direction",
                "traversal",
                "visibility",
                "public_description",
            ),
        )
        for item in spec.get("connections", [])
        if item.get("visibility") in allowed
        and item.get("from_location_id") in location_ids
        and item.get("to_location_id") in location_ids
    ]
    features = [
        _copy_public_fields(
            item,
            (
                "id",
                "name",
                "location_id",
                "kind",
                "position",
                "geometry",
                "visual_description",
                "visibility",
                "render_policy",
            ),
        )
        for item in spec.get("features", [])
        if item.get("visibility") in allowed
        and (item.get("location_id") is None or item.get("location_id") in location_ids)
    ]
    spawn_points = [
        _copy_public_fields(
            item,
            ("id", "location_id", "position", "allowed_actor_types"),
        )
        for item in spec.get("spawn_points", [])
        if item.get("location_id") in location_ids
    ]
    visible_names = {
        *(str(item.get("name", "")) for item in locations),
        *(str(item.get("name", "")) for item in features),
    }
    era = spec.get("era") if isinstance(spec.get("era"), dict) else {}
    visual = (
        spec.get("visual_brief") if isinstance(spec.get("visual_brief"), dict) else {}
    )
    return {
        "schema_version": spec.get("schema_version"),
        "title": spec.get("title"),
        "style": spec.get("style"),
        "map_kind": spec.get("map_kind"),
        "canvas": deepcopy(spec.get("canvas", {})),
        "era": _copy_public_fields(
            era,
            (
                "year",
                "locale",
                "season",
                "time_of_day",
                "weather",
                "public_architecture",
                "technology",
            ),
        ),
        "locations": locations,
        "connections": connections,
        "features": features,
        "spawn_points": spawn_points,
        "visual_brief": _copy_public_fields(
            visual,
            (
                "viewpoint",
                "style_preset",
                "palette",
                "lighting",
                "text_policy",
                "people_policy",
                "clutter_level",
            ),
        ),
        "coverage": {
            "required_element_names": [
                name
                for name in spec.get("coverage", {}).get("required_element_names", [])
                if name in visible_names
            ]
        },
    }


def _copy_public_fields(source: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {name: deepcopy(source[name]) for name in names if name in source}


def _relative_position(
    position: dict[str, Any],
    canvas: dict[str, Any],
) -> str:
    width = max(_number(canvas.get("width"), 1), 1)
    height = max(_number(canvas.get("height"), 1), 1)
    x_ratio = _number(position.get("x"), width / 2) / width
    y_ratio = _number(position.get("y"), height / 2) / height
    horizontal = "left" if x_ratio < 0.34 else "right" if x_ratio > 0.66 else "center"
    vertical = "upper" if y_ratio < 0.34 else "lower" if y_ratio > 0.66 else "middle"
    return f"{vertical}-{horizontal}"


def _public_layout_summary(projected: dict[str, Any]) -> tuple[str, str]:
    canvas = projected.get("canvas", {})
    if not isinstance(canvas, dict):
        canvas = {}
    locations = projected.get("locations", [])
    location_by_id = {item.get("id"): item.get("name") for item in locations}
    anchors: list[str] = []
    for item in locations[:16]:
        position = item.get("position")
        safe_position = position if isinstance(position, dict) else {}
        anchors.append(
            f"{item.get('name')} at {_relative_position(safe_position, canvas)}"
        )
    links = [
        (
            f"{location_by_id.get(item.get('from_location_id'), item.get('from_name', '?'))}"
            f" to {location_by_id.get(item.get('to_location_id'), item.get('to_name', '?'))}"
        )
        for item in projected.get("connections", [])[:24]
    ]
    return ", ".join(anchors) or "none specified", ", ".join(links) or "none specified"


def build_image_prompt(spec: dict[str, Any], audience: str = "table") -> str:
    allowed = ("player", "table") if audience == "table" else ("player", "table", "kp")
    projected = project_map_spec(spec, allowed)
    era = projected.get("era", {})
    visual = projected.get("visual_brief", {})
    location_names = [item["name"] for item in projected.get("locations", [])]
    feature_names = [item["name"] for item in projected.get("features", [])]
    safe_forbidden = (
        spec.get("era", {}).get("forbidden_visuals", [])
        if isinstance(spec.get("era"), dict)
        else []
    )
    layout_anchors, circulation_links = _public_layout_summary(projected)
    era_label = str(era.get("year") or "historically neutral, unspecified year")
    return "\n".join(
        [
            "Create a polished tabletop RPG map background.",
            f"Map kind: {projected.get('map_kind', 'site')}.",
            f"Era: {era_label}. Locale: {era.get('locale') or 'unspecified'}.",
            (
                f"Season: {era.get('season') or 'unspecified'}; "
                f"time: {era.get('time_of_day') or 'unspecified'}; "
                f"weather: {era.get('weather') or 'unspecified'}."
            ),
            "Confirmed-public architecture and materials: "
            + ", ".join(era.get("public_architecture", []))
            + ".",
            "Period technology allowed: " + ", ".join(era.get("technology", [])) + ".",
            "Visible areas that the composition must support: " + ", ".join(location_names) + ".",
            "Visible environmental elements: " + ", ".join(feature_names or ["none specified"]) + ".",
            "Approximate public layout anchors: " + layout_anchors + ".",
            "Public circulation links: " + circulation_links + ".",
            (
                f"Viewpoint: {visual.get('viewpoint', 'orthographic_top_down')}; "
                f"style: {visual.get('style_preset', 'period_illustrated_map')}; "
                f"palette: {', '.join(visual.get('palette', []))}."
            ),
            (
                "Do not generate any text, labels, legends, people, characters, tokens, clues, "
                "secret rooms, or hidden passages. Leave readable negative space for deterministic "
                "SVG labels and gameplay overlays."
            ),
            "Forbidden anachronisms and visual elements: "
            + ", ".join(safe_forbidden or ["none specified"])
            + ".",
            (
                "Maintain a coherent, practical layout with clear circulation, plausible scale, "
                "period-appropriate materials, attractive lighting, and restrained detail."
            ),
        ]
    )


def image_generation_input_hash(
    spec: dict[str, Any],
    *,
    provider: str,
    model: str,
    width: int,
    height: int,
    seed: int | None,
) -> str:
    public_spec = project_map_spec(spec, ("player", "table"))
    payload = {
        "public_spec": public_spec,
        "prompt": build_image_prompt(spec, "table"),
        "provider": provider,
        "model": model,
        "width": width,
        "height": height,
        "seed": seed,
        "prompt_version": "map-image-v1",
    }
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()

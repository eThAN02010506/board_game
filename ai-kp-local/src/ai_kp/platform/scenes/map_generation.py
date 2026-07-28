"""Canonical deterministic structured-map generation and layered SVG rendering."""

import math
import re
from dataclasses import dataclass, field
from html import escape
from typing import Any

from ai_kp.platform.scenes.map_spec import (
    build_map_spec,
    require_valid_map_spec,
)


@dataclass(frozen=True)
class GeneratedLocation:
    name: str
    x: float
    y: float
    visibility: str = "table"
    notes: str = ""


@dataclass(frozen=True)
class GeneratedRoute:
    start: str
    end: str
    travel_time: str | None = None
    visibility: str = "table"
    notes: str = ""


@dataclass(frozen=True)
class GeneratedMap:
    title: str
    prompt: str
    style: str
    width: int
    height: int
    locations: list[GeneratedLocation] = field(default_factory=list)
    routes: list[GeneratedRoute] = field(default_factory=list)
    svg_text: str = ""
    map_spec: dict[str, Any] = field(default_factory=dict)
    validation_report: dict[str, Any] = field(default_factory=dict)


def generate_map(
    title: str,
    prompt: str,
    location_names: list[str] | None = None,
    routes: list[tuple[str, str]] | None = None,
    style: str = "investigation",
    width: int = 960,
    height: int = 640,
    *,
    map_kind: str = "regional",
    era_year: int | None = None,
    locale: str = "",
    season: str = "",
    time_of_day: str = "",
    weather: str = "",
    public_architecture: list[str] | None = None,
    feature_names: list[str] | None = None,
    required_elements: list[str] | None = None,
    forbidden_elements: list[str] | None = None,
    visual_style: str = "period_illustrated_map",
    campaign_time: str | None = None,
) -> GeneratedMap:
    names = location_names or _infer_location_names(prompt)
    locations = _place_locations(names, width=width, height=height, map_kind=map_kind)
    generated_routes = _generate_routes(names, routes)
    map_spec = build_map_spec(
        title=title,
        prompt=prompt,
        style=style,
        width=width,
        height=height,
        locations=[
            {
                "name": item.name,
                "x": item.x,
                "y": item.y,
                "visibility": item.visibility,
                "notes": item.notes,
            }
            for item in locations
        ],
        routes=[
            {
                "start": item.start,
                "end": item.end,
                "travel_time": item.travel_time,
                "visibility": item.visibility,
                "notes": item.notes,
            }
            for item in generated_routes
        ],
        map_kind=map_kind,
        era_year=era_year,
        locale=locale,
        season=season,
        time_of_day=time_of_day,
        weather=weather,
        public_architecture=public_architecture or (),
        feature_names=feature_names or (),
        required_elements=required_elements or (),
        forbidden_elements=forbidden_elements or (),
        visual_style=visual_style,
        campaign_time=campaign_time,
    )
    validation_report = require_valid_map_spec(map_spec).to_dict()
    draft = GeneratedMap(
        title=title,
        prompt=prompt,
        style=style,
        width=width,
        height=height,
        locations=locations,
        routes=generated_routes,
        map_spec=map_spec,
        validation_report=validation_report,
    )
    return GeneratedMap(
        title=draft.title,
        prompt=draft.prompt,
        style=draft.style,
        width=draft.width,
        height=draft.height,
        locations=draft.locations,
        routes=draft.routes,
        svg_text=render_svg(draft),
        map_spec=map_spec,
        validation_report=validation_report,
    )


def render_svg(generated_map: GeneratedMap, *, overlay_only: bool = False) -> str:
    spec = map_spec_for_generated_map(generated_map)
    location_by_name = {location.name: location for location in generated_map.locations}
    era = spec.get("era", {})
    era_year = era.get("year")
    era_label = str(era_year) if era_year else "年代待确认"
    map_kind = spec.get("map_kind", "regional")
    lines = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {generated_map.width} {generated_map.height}" role="img" '
            f'aria-label="{escape(generated_map.title)}">'
        ),
        "<defs>",
        '<linearGradient id="paper" x1="0" y1="0" x2="1" y2="1">',
        '<stop offset="0" stop-color="#f4ecd8"/>',
        '<stop offset=".52" stop-color="#e8dcc1"/>',
        '<stop offset="1" stop-color="#d9c9a8"/>',
        "</linearGradient>",
        '<pattern id="grain" width="18" height="18" patternUnits="userSpaceOnUse">',
        '<circle cx="3" cy="5" r=".7" fill="#5f4c34" opacity=".10"/>',
        '<circle cx="13" cy="12" r=".55" fill="#fff" opacity=".18"/>',
        '<path d="M0 18L18 0" stroke="#6b573c" stroke-width=".3" opacity=".06"/>',
        "</pattern>",
        '<filter id="shadow" x="-30%" y="-30%" width="160%" height="160%">',
        '<feDropShadow dx="0" dy="4" stdDeviation="5" flood-color="#20170d" flood-opacity=".22"/>',
        "</filter>",
        "</defs>",
    ]
    if not overlay_only:
        lines.extend(
            [
                '<rect width="100%" height="100%" fill="url(#paper)"/>',
                '<rect width="100%" height="100%" fill="url(#grain)"/>',
                (
                    f'<rect x="18" y="18" width="{generated_map.width - 36}" '
                    f'height="{generated_map.height - 36}" rx="12" fill="none" '
                    'stroke="#5b4931" stroke-width="2" opacity=".55"/>'
                ),
                (
                    f'<rect x="31" y="31" width="{generated_map.width - 62}" '
                    'height="47" rx="8" fill="#20352f" opacity=".94"/>'
                ),
                (
                    f'<text x="48" y="63" font-family="serif" font-size="25" '
                    f'font-weight="700" fill="#f4ead2">{escape(generated_map.title)}</text>'
                ),
                (
                    f'<text x="{generated_map.width - 48}" y="61" text-anchor="end" '
                    'font-family="sans-serif" font-size="13" letter-spacing="2" '
                    f'fill="#d8c9aa">{escape(era_label)} · {escape(map_kind.upper())}</text>'
                ),
                (
                    f'<g transform="translate({generated_map.width - 67} '
                    f'{generated_map.height - 66})" opacity=".62">'
                ),
                '<circle r="25" fill="#f5ecd7" stroke="#5b4931" stroke-width="1.5"/>',
                '<path d="M0-20L5 0L0 20L-5 0Z" fill="#263a33"/>',
                '<path d="M-20 0L0 5L20 0L0-5Z" fill="#9a4938"/>',
                '<text y="-30" text-anchor="middle" font-family="serif" font-size="11" fill="#263a33">N</text>',
                "</g>",
            ]
        )
    for route in generated_map.routes:
        start = location_by_name.get(route.start)
        end = location_by_name.get(route.end)
        if not start or not end:
            continue
        stroke = "#7f6440" if route.visibility in {"player", "table"} else "#a64232"
        dash = ' stroke-dasharray="10 8"' if route.visibility == "kp" else ""
        lines.append(
            f'<line x1="{start.x:.1f}" y1="{start.y:.1f}" x2="{end.x:.1f}" y2="{end.y:.1f}" '
            'stroke="#f3ead5" stroke-width="10" stroke-linecap="round" opacity=".82"/>'
        )
        lines.append(
            f'<line x1="{start.x:.1f}" y1="{start.y:.1f}" x2="{end.x:.1f}" y2="{end.y:.1f}" '
            f'stroke="{stroke}" stroke-width="3.5" stroke-linecap="round"{dash}/>'
        )
    spec_locations = {item["name"]: item for item in spec.get("locations", [])}
    for index, location in enumerate(generated_map.locations):
        spec_location = spec_locations.get(location.name, {})
        footprint = spec_location.get("footprint")
        fill = "#f9f2df" if location.visibility in {"player", "table"} else "#eed2cb"
        accent = "#263a33" if location.visibility in {"player", "table"} else "#9a3f32"
        if isinstance(footprint, dict) and footprint.get("type") == "rectangle":
            lines.extend(
                [
                    (
                        f'<rect x="{float(footprint["x"]):.1f}" '
                        f'y="{float(footprint["y"]):.1f}" '
                        f'width="{float(footprint["width"]):.1f}" '
                        f'height="{float(footprint["height"]):.1f}" rx="8" '
                        f'fill="{fill}" stroke="{accent}" stroke-width="3" '
                        'filter="url(#shadow)" opacity=".96"/>'
                    ),
                    (
                        f'<path d="M{float(footprint["x"]) + 14:.1f} '
                        f'{float(footprint["y"]) + 15:.1f}h38" '
                        f'stroke="{accent}" stroke-width="3" opacity=".65"/>'
                    ),
                ]
            )
        else:
            lines.append(
                f'<circle cx="{location.x:.1f}" cy="{location.y:.1f}" r="42" fill="{fill}" '
                f'stroke="{accent}" stroke-width="3" filter="url(#shadow)"/>'
            )
        lines.append(
            f'<circle cx="{location.x:.1f}" cy="{location.y:.1f}" r="12" '
            f'fill="{accent}" stroke="#f8f0dc" stroke-width="3"/>'
        )
        lines.append(
            f'<text x="{location.x:.1f}" y="{location.y + 61:.1f}" text-anchor="middle" '
            'font-family="serif" font-size="18" font-weight="700" '
            f'paint-order="stroke" stroke="#f4ecd8" stroke-width="5" fill="{accent}">'
            f"{escape(location.name)}</text>"
        )
        lines.append(
            f'<text x="{location.x + 22:.1f}" y="{location.y - 20:.1f}" '
            f'font-family="sans-serif" font-size="10" fill="{accent}" opacity=".72">'
            f"{index + 1:02d}</text>"
        )
    for feature in spec.get("features", []):
        position = feature.get("position", {})
        x = float(position.get("x", generated_map.width / 2))
        y = float(position.get("y", generated_map.height / 2))
        lines.extend(
            [
                (
                    f'<rect x="{x - 7:.1f}" y="{y - 7:.1f}" width="14" height="14" '
                    'transform="rotate(45 '
                    f'{x:.1f} {y:.1f})" fill="#a66a35" stroke="#f7ecd3" stroke-width="2"/>'
                ),
                (
                    f'<text x="{x + 13:.1f}" y="{y + 4:.1f}" font-family="sans-serif" '
                    'font-size="12" paint-order="stroke" stroke="#f4ecd8" stroke-width="4" '
                    f'fill="#4c3824">{escape(str(feature.get("name", "")))}</text>'
                ),
            ]
        )
    lines.append("</svg>")
    return "\n".join(lines)


def render_overlay_svg(generated_map: GeneratedMap) -> str:
    return render_svg(generated_map, overlay_only=True)


def map_spec_for_generated_map(generated_map: GeneratedMap) -> dict[str, Any]:
    if generated_map.map_spec:
        return generated_map.map_spec
    return build_map_spec(
        title=generated_map.title,
        prompt=generated_map.prompt,
        style=generated_map.style,
        width=generated_map.width,
        height=generated_map.height,
        locations=[
            {
                "name": item.name,
                "x": item.x,
                "y": item.y,
                "visibility": item.visibility,
                "notes": item.notes,
            }
            for item in generated_map.locations
        ],
        routes=[
            {
                "start": item.start,
                "end": item.end,
                "travel_time": item.travel_time,
                "visibility": item.visibility,
                "notes": item.notes,
            }
            for item in generated_map.routes
        ],
        source_kind="legacy_map",
    )


def _infer_location_names(prompt: str) -> list[str]:
    candidates = [item.strip() for item in re.split(r"[，,、;；\n]", prompt) if item.strip()]
    names = [item for item in candidates if 2 <= len(item) <= 12]
    if len(names) >= 3:
        return names[:8]
    return ["起点", "线索地点", "危险区域", "关键地点"]


def _place_locations(
    names: list[str],
    width: int,
    height: int,
    map_kind: str,
) -> list[GeneratedLocation]:
    if map_kind == "floorplan":
        return _place_floorplan_locations(names, width, height)
    center_x = width / 2
    center_y = height / 2 + 24
    radius_x = max(width * 0.32, 120)
    radius_y = max(height * 0.26, 90)
    locations: list[GeneratedLocation] = []
    for index, name in enumerate(names):
        angle = (math.tau * index / max(len(names), 1)) - math.pi / 2
        locations.append(
            GeneratedLocation(
                name=name,
                x=center_x + math.cos(angle) * radius_x,
                y=center_y + math.sin(angle) * radius_y,
            )
        )
    return locations


def _place_floorplan_locations(
    names: list[str],
    width: int,
    height: int,
) -> list[GeneratedLocation]:
    columns = max(2, min(4, math.ceil(math.sqrt(max(len(names), 1)))))
    rows = max(1, math.ceil(len(names) / columns))
    usable_width = max(1, width - 180)
    usable_height = max(1, height - 190)
    return [
        GeneratedLocation(
            name=name,
            x=90 + ((index % columns) + 0.5) * (usable_width / columns),
            y=110 + ((index // columns) + 0.5) * (usable_height / rows),
        )
        for index, name in enumerate(names)
    ]


def _generate_routes(names: list[str], routes: list[tuple[str, str]] | None) -> list[GeneratedRoute]:
    if routes:
        return [GeneratedRoute(start=start, end=end) for start, end in routes]
    generated: list[GeneratedRoute] = []
    for index in range(len(names) - 1):
        generated.append(GeneratedRoute(start=names[index], end=names[index + 1]))
    if len(names) > 2:
        generated.append(GeneratedRoute(start=names[-1], end=names[0]))
    return generated

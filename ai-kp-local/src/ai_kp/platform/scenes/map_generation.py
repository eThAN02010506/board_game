"""Canonical deterministic structured-map generation and SVG rendering."""

from dataclasses import dataclass, field
from html import escape
import math
import re


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


def generate_map(
    title: str,
    prompt: str,
    location_names: list[str] | None = None,
    routes: list[tuple[str, str]] | None = None,
    style: str = "investigation",
    width: int = 960,
    height: int = 640,
) -> GeneratedMap:
    names = location_names or _infer_location_names(prompt)
    locations = _place_locations(names, width=width, height=height)
    generated_routes = _generate_routes(names, routes)
    draft = GeneratedMap(
        title=title,
        prompt=prompt,
        style=style,
        width=width,
        height=height,
        locations=locations,
        routes=generated_routes,
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
    )


def render_svg(generated_map: GeneratedMap) -> str:
    location_by_name = {location.name: location for location in generated_map.locations}
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {generated_map.width} {generated_map.height}" role="img">',
        '<rect width="100%" height="100%" fill="#f7f3e8"/>',
        f'<text x="32" y="44" font-family="serif" font-size="28" fill="#1f2a24">{escape(generated_map.title)}</text>',
    ]
    for route in generated_map.routes:
        start = location_by_name.get(route.start)
        end = location_by_name.get(route.end)
        if not start or not end:
            continue
        stroke = "#8b6f47" if route.visibility != "secret" else "#b84a3a"
        dash = ' stroke-dasharray="8 8"' if route.visibility in {"kp", "secret"} else ""
        lines.append(
            f'<line x1="{start.x:.1f}" y1="{start.y:.1f}" x2="{end.x:.1f}" y2="{end.y:.1f}" '
            f'stroke="{stroke}" stroke-width="4"{dash}/>'
        )
    for location in generated_map.locations:
        fill = "#fffdf7" if location.visibility != "secret" else "#f4d6d1"
        lines.append(
            f'<circle cx="{location.x:.1f}" cy="{location.y:.1f}" r="34" fill="{fill}" '
            'stroke="#22352d" stroke-width="3"/>'
        )
        lines.append(
            f'<text x="{location.x:.1f}" y="{location.y + 56:.1f}" text-anchor="middle" '
            f'font-family="sans-serif" font-size="18" fill="#1f2a24">{escape(location.name)}</text>'
        )
    lines.append("</svg>")
    return "\n".join(lines)


def _infer_location_names(prompt: str) -> list[str]:
    candidates = [item.strip() for item in re.split(r"[，,、;；\n]", prompt) if item.strip()]
    names = [item for item in candidates if 2 <= len(item) <= 12]
    if len(names) >= 3:
        return names[:8]
    return ["起点", "线索地点", "危险区域", "关键地点"]


def _place_locations(names: list[str], width: int, height: int) -> list[GeneratedLocation]:
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


def _generate_routes(names: list[str], routes: list[tuple[str, str]] | None) -> list[GeneratedRoute]:
    if routes:
        return [GeneratedRoute(start=start, end=end) for start, end in routes]
    generated: list[GeneratedRoute] = []
    for index in range(len(names) - 1):
        generated.append(GeneratedRoute(start=names[index], end=names[index + 1]))
    if len(names) > 2:
        generated.append(GeneratedRoute(start=names[-1], end=names[0]))
    return generated

"""Canonical safe plaintext module chunking and visibility parsing."""

import re
from dataclasses import dataclass


META_RE = re.compile(r"@(?P<key>[a-zA-Z_]+)=(?P<value>[^\s]+)")
META_LINE_RE = re.compile(r"^\s*(?:@[a-zA-Z_]+=[^\s]+\s*)+$")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*#*\s*$")
ALLOWED_VISIBILITY = {"player", "table", "kp", "secret"}
ALLOWED_METADATA = {"visibility", "spoiler", "scene"}
NULL_METADATA_VALUES = {"-", "none", "null"}


@dataclass(frozen=True)
class ModuleChunk:
    title: str
    text: str
    visibility: str
    order_index: int
    spoiler_tag: str | None = None
    scene_key: str | None = None


@dataclass(frozen=True)
class _ParsedSection:
    heading: str | None
    metadata: dict[str, str]
    body: str


def chunk_plaintext_module(
    text: str,
    title: str,
    visibility: str = "kp",
) -> list[ModuleChunk]:
    """Create deterministic chunks from trusted directives and ordinary prose."""

    normalized_title = title.strip()
    if not normalized_title:
        raise ValueError("Module title cannot be blank")
    _validate_visibility(visibility)

    sections = _split_sections(text)
    chunks: list[ModuleChunk] = []
    current_title = normalized_title
    inherited: dict[str, str | None] = {
        "visibility": visibility,
        "spoiler": None,
        "scene": None,
    }
    for section in sections:
        parsed = _parse_section(section)
        if parsed.heading is not None:
            current_title = parsed.heading

        is_scope_directive = parsed.heading is not None or not parsed.body
        if is_scope_directive:
            inherited = _apply_metadata(inherited, parsed.metadata)
            effective = inherited
        else:
            effective = _apply_metadata(inherited, parsed.metadata)

        if not parsed.body:
            continue

        chunk_visibility = str(effective["visibility"])
        _validate_visibility(chunk_visibility)
        chunks.append(
            ModuleChunk(
                title=current_title,
                text=parsed.body,
                visibility=chunk_visibility,
                order_index=len(chunks),
                spoiler_tag=_optional_text(effective["spoiler"]),
                scene_key=_optional_text(effective["scene"]),
            )
        )
    return chunks


def _split_sections(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return [
        section.strip()
        for section in re.split(r"\n[ \t]*\n+", normalized)
        if section.strip()
    ]


def _parse_section(section: str) -> _ParsedSection:
    lines = section.splitlines()
    heading: str | None = None
    metadata: dict[str, str] = {}
    while lines:
        heading_match = HEADING_RE.fullmatch(lines[0])
        if heading is None and heading_match:
            heading = heading_match.group("title").strip()
            if not heading:
                raise ValueError("Module heading cannot be blank")
            lines.pop(0)
            continue
        if META_LINE_RE.fullmatch(lines[0]):
            metadata.update(_parse_metadata_line(lines.pop(0), existing=metadata))
            continue
        break
    return _ParsedSection(
        heading=heading,
        metadata=metadata,
        body="\n".join(lines).strip(),
    )


def _parse_metadata_line(
    line: str,
    *,
    existing: dict[str, str] | None = None,
) -> dict[str, str]:
    metadata: dict[str, str] = {}
    seen = set(existing or {})
    for match in META_RE.finditer(line):
        key = match.group("key").lower()
        if key not in ALLOWED_METADATA:
            raise ValueError(f"Unknown module metadata: {key}")
        if key in seen:
            raise ValueError(f"Duplicate module metadata: {key}")
        seen.add(key)
        metadata[key] = match.group("value")
    return metadata


def _apply_metadata(
    base: dict[str, str | None],
    metadata: dict[str, str],
) -> dict[str, str | None]:
    result = dict(base)
    if "visibility" in metadata:
        _validate_visibility(metadata["visibility"])
        result["visibility"] = metadata["visibility"]
    for key in ("spoiler", "scene"):
        if key not in metadata:
            continue
        value = metadata[key].strip()
        result[key] = None if value.lower() in NULL_METADATA_VALUES else value
    return result


def _validate_visibility(visibility: str) -> None:
    if visibility not in ALLOWED_VISIBILITY:
        raise ValueError(f"Unknown visibility: {visibility}")


def _optional_text(value: str | None) -> str | None:
    return value if value else None

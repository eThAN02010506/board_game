"""Canonical safe plaintext module chunking and visibility parsing."""

from dataclasses import dataclass
import re


META_RE = re.compile(r"@(?P<key>[a-zA-Z_]+)=(?P<value>[^\s]+)")
ALLOWED_VISIBILITY = {"player", "table", "kp", "secret"}


@dataclass(frozen=True)
class ModuleChunk:
    title: str
    text: str
    visibility: str
    order_index: int
    spoiler_tag: str | None = None
    scene_key: str | None = None


def chunk_plaintext_module(text: str, title: str, visibility: str = "kp") -> list[ModuleChunk]:
    sections = [section.strip() for section in text.split("\n\n") if section.strip()]
    chunks: list[ModuleChunk] = []
    for index, section in enumerate(sections):
        metadata = _parse_metadata(section)
        body = _strip_metadata(section).strip()
        chunk_visibility = metadata.get("visibility", visibility)
        if chunk_visibility not in ALLOWED_VISIBILITY:
            raise ValueError(f"Unknown visibility: {chunk_visibility}")
        chunks.append(
            ModuleChunk(
            title=title,
                text=body,
                visibility=chunk_visibility,
            order_index=index,
                spoiler_tag=metadata.get("spoiler"),
                scene_key=metadata.get("scene"),
        )
        )
    return chunks


def _parse_metadata(section: str) -> dict[str, str]:
    first_line = section.splitlines()[0] if section.splitlines() else ""
    return {match.group("key"): match.group("value") for match in META_RE.finditer(first_line)}


def _strip_metadata(section: str) -> str:
    lines = section.splitlines()
    if not lines:
        return section
    first_line_without_metadata = META_RE.sub("", lines[0]).strip()
    if first_line_without_metadata:
        return "\n".join([first_line_without_metadata, *lines[1:]])
    return "\n".join(lines[1:])

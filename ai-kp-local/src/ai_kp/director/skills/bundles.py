"""Validated loader for packaged, prompt-only AI Skill bundles."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_SKILL_BYTES = 16_000


@dataclass(frozen=True)
class AiSkillBundle:
    name: str
    description: str
    instructions: str
    content_hash: str


@lru_cache(maxsize=32)
def load_ai_skill_bundle(name: str) -> AiSkillBundle:
    if not SKILL_NAME_RE.fullmatch(name):
        raise ValueError(f"Invalid AI skill bundle name: {name}")
    resource = files("ai_kp.director.skills").joinpath("bundles", name, "SKILL.md")
    try:
        raw = resource.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError(f"AI skill bundle is not installed: {name}") from exc
    if not raw or len(raw) > MAX_SKILL_BYTES:
        raise ValueError(f"AI skill bundle has invalid size: {name}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"AI skill bundle must be UTF-8: {name}") from exc
    metadata, instructions = _parse_skill_markdown(text, expected_name=name)
    return AiSkillBundle(
        name=name,
        description=metadata["description"],
        instructions=instructions,
        content_hash=hashlib.sha256(raw).hexdigest(),
    )


def _parse_skill_markdown(
    text: str,
    *,
    expected_name: str,
) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if len(lines) < 5 or lines[0] != "---":
        raise ValueError(f"AI skill bundle lacks YAML frontmatter: {expected_name}")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError(f"AI skill bundle has unterminated frontmatter: {expected_name}") from exc
    metadata: dict[str, str] = {}
    for line in lines[1:closing]:
        key, separator, value = line.partition(":")
        normalized_key = key.strip()
        if not separator or normalized_key not in {"name", "description"}:
            raise ValueError(f"AI skill bundle has unsupported frontmatter: {expected_name}")
        if normalized_key in metadata:
            raise ValueError(f"AI skill bundle has duplicate metadata: {expected_name}")
        metadata[normalized_key] = value.strip()
    if set(metadata) != {"name", "description"}:
        raise ValueError(f"AI skill bundle metadata is incomplete: {expected_name}")
    if metadata["name"] != expected_name or not metadata["description"]:
        raise ValueError(f"AI skill bundle metadata does not match: {expected_name}")
    instructions = "\n".join(lines[closing + 1 :]).strip()
    if not instructions or "TODO" in instructions:
        raise ValueError(f"AI skill bundle instructions are incomplete: {expected_name}")
    return metadata, instructions


__all__ = ["AiSkillBundle", "load_ai_skill_bundle"]

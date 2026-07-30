"""Declarative boundaries for AI behavior that never owns authoritative state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

SkillCategory = Literal[
    "action_understanding",
    "scene_direction",
    "npc_portrayal",
    "world_expansion",
    "map_planning",
    "memory_curation",
    "module_understanding",
    "safety_review",
]


@dataclass(frozen=True)
class AiSkillManifest:
    skill_id: str
    version: str
    display_name: str
    category: SkillCategory
    description: str
    input_schema_version: str
    output_schema_version: str
    allowed_tools: tuple[str, ...]
    source_requirements: tuple[str, ...]
    ruleset_scope: str | None = None
    authority: Literal["proposal_only"] = "proposal_only"

    def __post_init__(self) -> None:
        required = {
            "skill_id": self.skill_id,
            "version": self.version,
            "display_name": self.display_name,
            "description": self.description,
            "input_schema_version": self.input_schema_version,
            "output_schema_version": self.output_schema_version,
        }
        if missing := [key for key, value in required.items() if not value.strip()]:
            raise ValueError(f"AI skill fields are required: {', '.join(missing)}")
        for name, values in {
            "allowed_tools": self.allowed_tools,
            "source_requirements": self.source_requirements,
        }.items():
            if not values or any(not value.strip() for value in values):
                raise ValueError(f"AI skill {name} must contain non-empty values")
            if len(values) != len(set(values)):
                raise ValueError(f"AI skill {name} contains duplicates")
        if self.authority != "proposal_only":
            raise ValueError("AI skills cannot own authoritative state")

    def as_dict(self) -> dict:
        result = asdict(self)
        result["allowed_tools"] = list(self.allowed_tools)
        result["source_requirements"] = list(self.source_requirements)
        return result


__all__ = ["AiSkillManifest", "SkillCategory"]

"""Strict, ruleset-neutral Session 0 and safety-tool contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CampaignSetupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ruleset_id: str = Field(min_length=1, max_length=120)
    ruleset_version: str = Field(min_length=1, max_length=80)
    worldview: str = Field(default="", max_length=4000)
    hosting_mode: Literal["human_kp", "hybrid", "ai_kp"] = "ai_kp"
    expected_player_count: int = Field(default=4, ge=1, le=12)
    campaign_type: str = Field(default="ongoing", min_length=1, max_length=120)
    starting_power: str = Field(default="standard", min_length=1, max_length=120)
    allowed_character_options: tuple[str, ...] = ()
    house_rules: tuple[str, ...] = ()
    default_visibility: Literal["public", "party", "private_by_default"] = "party"
    style: dict[str, int | str] = Field(default_factory=dict)
    content_warnings: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()
    veils: tuple[str, ...] = ()
    safety_default: Literal["pause", "fade", "change", "rewind"] = "pause"
    idle_policy: Literal["wait", "skip", "defend", "delegate", "pause"] = "wait"
    idle_timeout_seconds: int = Field(default=300, ge=30, le=3600)
    allow_player_whispers: bool = False


class SessionZeroPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    public_style: dict[str, int | str] = Field(default_factory=dict)
    private_style: dict[str, int | str] = Field(default_factory=dict)
    lines: tuple[str, ...] = ()
    veils: tuple[str, ...] = ()


class SessionZeroModelPolicy(BaseModel):
    """Identity-free policy safe to expose to generation agents."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content_warnings: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()
    veils: tuple[str, ...] = ()
    safety_default: Literal["pause", "fade", "change", "rewind"] = "pause"


SafetyResponse = Literal["pause", "fade", "change", "rewind"]


__all__ = [
    "CampaignSetupConfig",
    "SafetyResponse",
    "SessionZeroModelPolicy",
    "SessionZeroPreferences",
]

"""Strict HTTP command contracts for dynamic-branch progression."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DynamicBranchBeatResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    command_id: str = Field(min_length=8, max_length=200)
    outcome: Literal["succeeded", "failed", "skipped"]
    note: str = Field(default="", max_length=2000)
    observed_effects: list[str] = Field(default_factory=list, max_length=8)


class DynamicBranchResume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    command_id: str = Field(min_length=8, max_length=200)
    note: str = Field(min_length=1, max_length=2000)


class DynamicBranchAbandon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    command_id: str = Field(min_length=8, max_length=200)
    note: str = Field(min_length=1, max_length=2000)


__all__ = [
    "DynamicBranchAbandon",
    "DynamicBranchBeatResolution",
    "DynamicBranchResume",
]

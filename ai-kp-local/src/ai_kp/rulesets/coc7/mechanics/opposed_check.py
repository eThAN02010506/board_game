"""Pure deterministic comparison for CoC7 opposed checks."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from ai_kp.rulesets.coc7.mechanics.skill_check import (
    D100Resolution,
    RULESET_ID,
    RULESET_VERSION,
    SOURCE_REFERENCE,
    SuccessLevel,
    success_level,
)


OpposedOutcome = Literal["left_wins", "right_wins", "tie"]
OpposedDecision = Literal["success_level", "target", "unresolved_tie"]
TieOption = Literal["stalemate", "reroll"]

_SUCCESS_RANK: dict[SuccessLevel, int] = {
    "fumble": -1,
    "failure": 0,
    "regular": 1,
    "hard": 2,
    "extreme": 3,
    "critical": 4,
}
_TIE_OPTIONS: tuple[TieOption, ...] = ("stalemate", "reroll")


@dataclass(frozen=True)
class OpposedParticipant:
    """One side's already-selected percentile result."""

    participant_id: str
    target: int
    roll: int

    def __post_init__(self) -> None:
        if type(self.participant_id) is not str or not self.participant_id.strip():
            raise ValueError("Opposed participant ID cannot be blank")
        if type(self.target) is not int:
            raise ValueError("Opposed target must be an integer")
        if type(self.roll) is not int:
            raise ValueError("Opposed roll must be an integer")
        success_level(self.target, self.roll)

    @property
    def success_level(self) -> SuccessLevel:
        return success_level(self.target, self.roll)

    @classmethod
    def from_d100_resolution(
        cls,
        participant_id: str,
        resolution: D100Resolution,
    ) -> OpposedParticipant:
        """Use the selected roll after bonus or penalty dice are resolved."""

        return cls(
            participant_id=participant_id,
            target=resolution.target,
            roll=resolution.selected_roll,
        )

    def as_dict(self) -> dict:
        return {
            "participant_id": self.participant_id,
            "target": self.target,
            "roll": self.roll,
            "success_level": self.success_level,
        }


@dataclass(frozen=True)
class OpposedResolution:
    left: OpposedParticipant
    right: OpposedParticipant
    outcome: OpposedOutcome
    decided_by: OpposedDecision

    @property
    def winner_id(self) -> str | None:
        if self.outcome == "left_wins":
            return self.left.participant_id
        if self.outcome == "right_wins":
            return self.right.participant_id
        return None

    @property
    def loser_id(self) -> str | None:
        if self.outcome == "left_wins":
            return self.right.participant_id
        if self.outcome == "right_wins":
            return self.left.participant_id
        return None

    @property
    def tie_options(self) -> tuple[TieOption, ...]:
        return _TIE_OPTIONS if self.outcome == "tie" else ()

    def as_dict(self) -> dict:
        return {
            "left": self.left.as_dict(),
            "right": self.right.as_dict(),
            "outcome": self.outcome,
            "winner_id": self.winner_id,
            "loser_id": self.loser_id,
            "decided_by": self.decided_by,
            "tie_options": list(self.tie_options),
            "push_allowed": False,
            "difficulty_used": False,
            "ruleset_id": RULESET_ID,
            "ruleset_version": RULESET_VERSION,
            "source_reference": deepcopy(SOURCE_REFERENCE),
        }


def resolve_opposed(
    left: OpposedParticipant,
    right: OpposedParticipant,
) -> OpposedResolution:
    """Compare two mutually exclusive CoC7 goals without choosing a tie policy."""

    if left.participant_id == right.participant_id:
        raise ValueError("Opposed participants must be distinct")

    left_rank = _SUCCESS_RANK[left.success_level]
    right_rank = _SUCCESS_RANK[right.success_level]
    if left_rank > right_rank:
        return OpposedResolution(left, right, "left_wins", "success_level")
    if right_rank > left_rank:
        return OpposedResolution(left, right, "right_wins", "success_level")
    if left.target > right.target:
        return OpposedResolution(left, right, "left_wins", "target")
    if right.target > left.target:
        return OpposedResolution(left, right, "right_wins", "target")
    return OpposedResolution(left, right, "tie", "unresolved_tie")


__all__ = [
    "OpposedDecision",
    "OpposedOutcome",
    "OpposedParticipant",
    "OpposedResolution",
    "TieOption",
    "resolve_opposed",
]

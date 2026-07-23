"""Deterministic planning for checks created by an approved proposal."""

from dataclasses import dataclass
from typing import Any

from ai_kp.rulesets.base import Ruleset


@dataclass(frozen=True)
class PlannedSkillCheck:
    skill_name: str
    difficulty: str
    hidden: bool
    pc_id: str | None


def plan_proposed_checks(
    proposal: dict[str, Any],
    ruleset: Ruleset,
) -> tuple[PlannedSkillCheck, ...]:
    plans: list[PlannedSkillCheck] = []
    for proposed_check in proposal["proposed_checks"]:
        difficulty = str(proposed_check["difficulty"])
        ruleset.validate_check(
            target=None,
            difficulty=difficulty,
            bonus_dice=0,
        )
        plans.append(
            PlannedSkillCheck(
                skill_name=str(proposed_check["skill"]),
                difficulty=difficulty,
                hidden=bool(proposed_check.get("hidden")),
                pc_id=proposed_check.get("pc_id") or proposal.get("pc_id"),
            )
        )
    return tuple(plans)

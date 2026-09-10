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
    bonus_dice: int
    allow_push: bool
    check_plan: dict[str, Any]


def plan_proposed_checks(
    proposal: dict[str, Any],
    ruleset: Ruleset,
) -> tuple[PlannedSkillCheck, ...]:
    plans: list[PlannedSkillCheck] = []
    for proposed_check in proposal["proposed_checks"]:
        difficulty = str(proposed_check["difficulty"])
        hidden = bool(proposed_check.get("hidden"))
        ruleset.validate_check(
            target=None,
            difficulty=difficulty,
            bonus_dice=int(proposed_check.get("bonus_dice") or 0),
        )
        plans.append(
            PlannedSkillCheck(
                skill_name=str(proposed_check["skill"]),
                difficulty=difficulty,
                hidden=hidden,
                # The server derives the PC from the authenticated action, never
                # from a model-supplied pc_id on an individual check.
                pc_id=proposal.get("pc_id"),
                bonus_dice=int(proposed_check.get("bonus_dice") or 0),
                # A player cannot make the required informed, method-changing
                # push decision for a result they are not allowed to see.
                allow_push=(
                    False
                    if hidden
                    else bool(proposed_check.get("allow_push", True))
                ),
                check_plan={
                    key: proposed_check.get(key)
                    for key in (
                        "scope",
                        "supporting_factors",
                        "automatic_information",
                        "failure_stakes",
                        "pushed_failure_stakes",
                    )
                },
            )
        )
    return tuple(plans)

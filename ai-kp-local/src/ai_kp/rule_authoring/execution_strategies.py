"""Execution strategies for the closed declarative rule kinds."""

from copy import deepcopy
from typing import Any

from ai_kp.rule_authoring.models import RuleExecution
from ai_kp.rule_authoring.runtime import (
    RuleExecutionError,
    apply_effect,
    condition_matches,
    read_field,
)


def execute_lookup(
    execution: RuleExecution,
    result: dict[str, Any],
) -> dict[str, Any]:
    value = read_field(result, execution.lookup_input or "")
    for row in execution.rows:
        matches = row.equals == value if row.equals is not None else True
        if row.minimum is not None:
            matches = matches and value >= row.minimum
        if row.maximum is not None:
            matches = matches and value <= row.maximum
        if matches:
            result.update(deepcopy(row.output))
            return result
    raise RuleExecutionError("No lookup row matched the supplied input")


def execute_condition_effects(
    execution: RuleExecution,
    result: dict[str, Any],
) -> dict[str, Any]:
    matched: list[str] = []
    for branch in execution.branches:
        if not all(condition_matches(condition, result) for condition in branch.all):
            continue
        for effect in branch.effects:
            apply_effect(effect, result)
        matched.append(branch.label or f"branch-{len(matched) + 1}")
    result["_matched_branches"] = matched
    return result

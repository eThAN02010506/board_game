"""Execution strategies for the closed declarative rule kinds."""

from copy import deepcopy
from typing import Any

from ai_kp.rule_authoring.models import RuleExecution
from ai_kp.rule_authoring.runtime import (
    RuleExecutionError,
    apply_effect,
    condition_matches,
    read_field,
    validate_json_value,
)


def execute_lookup(
    execution: RuleExecution,
    result: dict[str, Any],
) -> dict[str, Any]:
    value = read_field(result, execution.lookup_input or "")
    for row in execution.rows:
        try:
            matches = row.equals == value if row.equals is not None else True
            if row.minimum is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise TypeError
                matches = matches and value >= row.minimum
            if row.maximum is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise TypeError
                matches = matches and value <= row.maximum
        except (ArithmeticError, TypeError) as error:
            raise RuleExecutionError(
                "Incompatible lookup input and row matcher"
            ) from error
        if matches:
            validate_json_value(row.output, label="lookup row output")
            result.update(deepcopy(row.output))
            validate_json_value(result, label="rule result")
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
    validate_json_value(result, label="rule result")
    return result

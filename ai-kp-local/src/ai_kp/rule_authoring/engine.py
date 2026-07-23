"""Closed deterministic executor for approved declarative rule objects."""

from copy import deepcopy
from typing import Any

from ai_kp.rule_authoring.execution_strategies import (
    execute_condition_effects,
    execute_lookup,
)
from ai_kp.rule_authoring.models import RuleObject
from ai_kp.rule_authoring.runtime import RuleExecutionError, read_field


def execute_rule(rule: RuleObject, inputs: dict[str, Any]) -> dict[str, Any]:
    """Execute the closed DSL without evaluating Python expressions."""

    result = deepcopy(inputs)
    execution = rule.execution
    for required in execution.inputs:
        read_field(result, required)
    if execution.kind == "reference_only":
        raise RuleExecutionError("Reference-only rules are not executable")
    if execution.kind == "lookup_table":
        return execute_lookup(execution, result)
    return execute_condition_effects(execution, result)


__all__ = ["RuleExecutionError", "execute_rule"]

"""Closed deterministic executor for approved declarative rule objects."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ai_kp.rule_authoring.models import Condition, Operand, RuleObject


class RuleExecutionError(ValueError):
    pass


def _read_field(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise RuleExecutionError(f"Missing required input: {path}")
        current = current[part]
    return current


def _write_field(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        child = current.get(part)
        if child is None:
            child = {}
            current[part] = child
        if not isinstance(child, dict):
            raise RuleExecutionError(f"Effect target parent is not an object: {path}")
        current = child
    current[parts[-1]] = value


def _operand_value(operand: Operand, data: dict[str, Any]) -> Any:
    raw = _read_field(data, operand.field) if operand.field else operand.value
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        if operand.multiplier != 1 or operand.divisor != 1:
            raise RuleExecutionError("Only numeric operands can be scaled")
        return raw
    return raw * operand.multiplier / operand.divisor


def _condition_matches(condition: Condition, data: dict[str, Any]) -> bool:
    left = _operand_value(condition.left, data)
    right = _operand_value(condition.right, data)
    operations = {
        "eq": lambda: left == right,
        "ne": lambda: left != right,
        "lt": lambda: left < right,
        "lte": lambda: left <= right,
        "gt": lambda: left > right,
        "gte": lambda: left >= right,
        "in": lambda: left in right,
    }
    try:
        return bool(operations[condition.operator]())
    except TypeError as exc:
        raise RuleExecutionError(f"Incompatible condition operands for {condition.operator}") from exc


def execute_rule(rule: RuleObject, inputs: dict[str, Any]) -> dict[str, Any]:
    """Execute only the closed, declarative rule DSL. Python expressions are never evaluated."""

    result = deepcopy(inputs)
    execution = rule.execution
    for required in execution.inputs:
        _read_field(result, required)

    if execution.kind == "reference_only":
        raise RuleExecutionError("Reference-only rules are not executable")
    if execution.kind == "lookup_table":
        value = _read_field(result, execution.lookup_input or "")
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

    matched: list[str] = []
    for branch in execution.branches:
        if not all(_condition_matches(condition, result) for condition in branch.all):
            continue
        for effect in branch.effects:
            operand = _operand_value(effect.operand, result)
            try:
                current = _read_field(result, effect.target)
            except RuleExecutionError:
                current = None
            if effect.operation == "set":
                updated = operand
            elif current is None:
                raise RuleExecutionError(f"Effect target is missing: {effect.target}")
            elif effect.operation == "add":
                updated = current + operand
            elif effect.operation == "subtract":
                updated = current - operand
            elif effect.operation == "multiply":
                updated = current * operand
            elif effect.operation == "min":
                updated = min(current, operand)
            elif effect.operation == "max":
                updated = max(current, operand)
            _write_field(result, effect.target, updated)
        matched.append(branch.label or f"branch-{len(matched) + 1}")
    result["_matched_branches"] = matched
    return result

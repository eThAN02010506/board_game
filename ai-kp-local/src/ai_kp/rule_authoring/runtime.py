"""Primitive field, operand, condition, and effect operations for the rule DSL."""

from typing import Any

from ai_kp.rule_authoring.models import Condition, Effect, Operand


class RuleExecutionError(ValueError):
    pass


def read_field(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise RuleExecutionError(f"Missing required input: {path}")
        current = current[part]
    return current


def write_field(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        child = current.get(part)
        if child is None:
            child = {}
            current[part] = child
        if not isinstance(child, dict):
            raise RuleExecutionError(
                f"Effect target parent is not an object: {path}"
            )
        current = child
    current[parts[-1]] = value


def operand_value(operand: Operand, data: dict[str, Any]) -> Any:
    raw = read_field(data, operand.field) if operand.field else operand.value
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        if operand.multiplier != 1 or operand.divisor != 1:
            raise RuleExecutionError("Only numeric operands can be scaled")
        return raw
    return raw * operand.multiplier / operand.divisor


def condition_matches(condition: Condition, data: dict[str, Any]) -> bool:
    left = operand_value(condition.left, data)
    right = operand_value(condition.right, data)
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
    except TypeError as error:
        raise RuleExecutionError(
            f"Incompatible condition operands for {condition.operator}"
        ) from error


def apply_effect(effect: Effect, data: dict[str, Any]) -> None:
    operand = operand_value(effect.operand, data)
    if effect.operation == "set":
        write_field(data, effect.target, operand)
        return
    try:
        current = read_field(data, effect.target)
    except RuleExecutionError as error:
        raise RuleExecutionError(
            f"Effect target is missing: {effect.target}"
        ) from error
    operations = {
        "add": lambda: current + operand,
        "subtract": lambda: current - operand,
        "multiply": lambda: current * operand,
        "min": lambda: min(current, operand),
        "max": lambda: max(current, operand),
    }
    try:
        updated = operations[effect.operation]()
    except TypeError as error:
        raise RuleExecutionError(
            f"Incompatible effect operands for {effect.operation}"
        ) from error
    write_field(data, effect.target, updated)

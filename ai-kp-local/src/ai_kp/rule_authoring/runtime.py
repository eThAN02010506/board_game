"""Primitive field, operand, condition, and effect operations for the rule DSL."""

from math import isfinite
import re
from typing import Any

from ai_kp.rule_authoring.models import (
    FIELD_PATH_PATTERN,
    Condition,
    Effect,
    Operand,
)


MAX_EXECUTION_DEPTH = 32
MAX_EXECUTION_NODES = 20_000
MAX_CONTAINER_ITEMS = 4_096
MAX_STRING_LENGTH = 100_000
MAX_INTEGER_BITS = 4_096

_FIELD_PATH = re.compile(FIELD_PATH_PATTERN)


class RuleExecutionError(ValueError):
    pass


def validate_json_value(value: Any, *, label: str) -> None:
    """Validate the inert JSON-shaped data accepted by the executor."""

    stack: list[tuple[Any, int, tuple[int, ...]]] = [(value, 0, ())]
    node_count = 0
    while stack:
        current, depth, ancestors = stack.pop()
        node_count += 1
        if node_count > MAX_EXECUTION_NODES:
            raise RuleExecutionError(
                f"{label} exceeds the {MAX_EXECUTION_NODES}-node limit"
            )
        if depth > MAX_EXECUTION_DEPTH:
            raise RuleExecutionError(
                f"{label} exceeds the {MAX_EXECUTION_DEPTH}-level depth limit"
            )
        if current is None or type(current) is bool:
            continue
        if type(current) is int:
            if current.bit_length() > MAX_INTEGER_BITS:
                raise RuleExecutionError(
                    f"{label} contains an integer larger than {MAX_INTEGER_BITS} bits"
                )
            continue
        if type(current) is float:
            if not isfinite(current):
                raise RuleExecutionError(f"{label} contains a non-finite number")
            continue
        if type(current) is str:
            if len(current) > MAX_STRING_LENGTH:
                raise RuleExecutionError(
                    f"{label} contains a string longer than {MAX_STRING_LENGTH} characters"
                )
            continue
        if type(current) not in {dict, list}:
            raise RuleExecutionError(
                f"{label} must contain only JSON-compatible values"
            )

        container_id = id(current)
        if container_id in ancestors:
            raise RuleExecutionError(f"{label} cannot contain cyclic containers")
        child_ancestors = (*ancestors, container_id)
        if len(current) > MAX_CONTAINER_ITEMS:
            raise RuleExecutionError(
                f"{label} contains a collection larger than {MAX_CONTAINER_ITEMS} items"
            )
        if type(current) is list:
            stack.extend(
                (item, depth + 1, child_ancestors) for item in current
            )
            continue
        for key, item in current.items():
            if type(key) is not str:
                raise RuleExecutionError(f"{label} object keys must be strings")
            if len(key) > MAX_STRING_LENGTH:
                raise RuleExecutionError(
                    f"{label} contains an object key longer than "
                    f"{MAX_STRING_LENGTH} characters"
                )
            stack.append((item, depth + 1, child_ancestors))


def _path_parts(path: str) -> list[str]:
    if not isinstance(path, str) or not _FIELD_PATH.fullmatch(path):
        raise RuleExecutionError(f"Invalid field path: {path!r}")
    return path.split(".")


def read_field(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in _path_parts(path):
        if not isinstance(current, dict) or part not in current:
            raise RuleExecutionError(f"Missing required input: {path}")
        current = current[part]
    return current


def write_field(data: dict[str, Any], path: str, value: Any) -> None:
    parts = _path_parts(path)
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
    raw = read_field(data, operand.field) if operand.field is not None else operand.value
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        if operand.multiplier != 1 or operand.divisor != 1:
            raise RuleExecutionError("Only numeric operands can be scaled")
        return raw
    try:
        scaled = raw * operand.multiplier / operand.divisor
    except ArithmeticError as error:
        raise RuleExecutionError("Numeric operand scaling failed") from error
    validate_json_value(scaled, label="scaled operand")
    return scaled


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
    operation = operations.get(condition.operator)
    if operation is None:
        raise RuleExecutionError(
            f"Unsupported condition operator: {condition.operator}"
        )
    try:
        return bool(operation())
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
    operation = operations.get(effect.operation)
    if operation is None:
        raise RuleExecutionError(f"Unsupported effect operation: {effect.operation}")
    try:
        updated = operation()
    except (ArithmeticError, TypeError) as error:
        raise RuleExecutionError(
            f"Incompatible effect operands for {effect.operation}"
        ) from error
    validate_json_value(updated, label=f"effect result for {effect.target}")
    write_field(data, effect.target, updated)

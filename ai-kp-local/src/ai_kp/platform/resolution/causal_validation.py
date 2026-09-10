"""Shared deterministic causal invariants for terminal scenario transitions."""

from __future__ import annotations

from ai_kp.platform.resolution.contracts import ActionOperator, ScenarioContract


def operator_has_causal_result(operator: ActionOperator) -> bool:
    """Return whether execution establishes more than its own outcome label."""

    return bool(
        operator.skill_choices
        or operator.automatic_information
        or operator.response_obligation_ids
        or operator.always_commands
        or operator.success_commands
        or operator.failure_commands
        or operator.outcome_branches
        or operator.narrative_cues
    )


def unsafe_ending_operator_ids(contract: ScenarioContract) -> frozenset[str]:
    """Find terminal operators that cannot serve as causal proof, including legacy data."""

    # Imported lazily to keep the contract models independent of the reducer.
    from ai_kp.platform.resolution.kernel import operator_outcome_path

    by_path = {
        operator_outcome_path(operator.operator_id): operator
        for operator in contract.operators
    }
    unsafe: set[str] = set()
    for ending in contract.endings:
        for condition in (*ending.all_conditions, *ending.any_conditions):
            operator = by_path.get(condition.path)
            if operator is None:
                continue
            no_causal_result = not operator_has_causal_result(operator)
            ungated_automatic = (
                operator.policy == "automatic"
                and not operator.preconditions
                and not operator.skill_choices
            )
            if no_causal_result or ungated_automatic:
                unsafe.add(operator.operator_id)
    return frozenset(unsafe)


__all__ = ["operator_has_causal_result", "unsafe_ending_operator_ids"]

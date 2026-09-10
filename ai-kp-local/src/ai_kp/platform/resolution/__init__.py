"""Ruleset-neutral proposal and resolution safety policies."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ai_kp.platform.resolution.authority_basis import KernelAuthorityBasis
from ai_kp.platform.resolution.check_consequences import (
    CHECK_CONSEQUENCE_SCHEMA_VERSION,
    HIDDEN_CHECK_PUBLIC_NARRATION,
    build_check_consequence_snapshot,
    check_consequence_fingerprint,
    exact_kernel_outcome,
    has_pending_push_decision,
    project_public_check_consequences,
)

if TYPE_CHECKING:
    from ai_kp.platform.resolution.selected_action import (
        SelectedKernelAction,
        SelectedOperator,
    )


def __getattr__(name: str) -> Any:
    """Load the narrative-bearing action facade without a package import cycle."""

    if name in {
        "SelectedKernelAction",
        "SelectedOperator",
        "prepare_selected_kernel_action",
        "selected_operator_from_semantic",
    }:
        from ai_kp.platform.resolution import selected_action

        return getattr(selected_action, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "CHECK_CONSEQUENCE_SCHEMA_VERSION",
    "HIDDEN_CHECK_PUBLIC_NARRATION",
    "KernelAuthorityBasis",
    "SelectedKernelAction",
    "SelectedOperator",
    "build_check_consequence_snapshot",
    "check_consequence_fingerprint",
    "exact_kernel_outcome",
    "has_pending_push_decision",
    "prepare_selected_kernel_action",
    "project_public_check_consequences",
    "selected_operator_from_semantic",
]

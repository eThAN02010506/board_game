"""Source-neutral preparation for one selected scenario operator.

AI directors, human KPs, parallel planners, and persistence revalidation only
provide a selected operator and optional skill. This module owns the shared
deterministic boundary that turns that choice into an authoritative preview;
selection provenance must not change game mechanics.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from ai_kp.platform.resolution.contracts import (
    ActionIntent,
    ActionOperator,
    ResolutionPreview,
    ScenarioContract,
    ScenarioSnapshot,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.narrative_adapter import (
    KernelNarrativeBundle,
    deterministic_kernel_narrative,
)
from ai_kp.platform.resolution.semantic_adapter import SemanticSelection


@dataclass(frozen=True)
class SelectedKernelAction:
    """A contract-verified primitive ready for proposal persistence."""

    operator: ActionOperator
    preview: ResolutionPreview
    deterministic_narrative: KernelNarrativeBundle


class SelectedOperator(BaseModel):
    """The smallest source-independent input accepted by the rules kernel."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operator_id: str = Field(min_length=1, max_length=160)
    requested_skill_key: str | None = Field(default=None, min_length=1, max_length=120)


def selected_operator_from_semantic(selection: SemanticSelection) -> SelectedOperator:
    """Discard model-only metadata after validating a primitive selection."""

    if selection.kind != "operator" or selection.candidate_id is None:
        raise ValueError("Primitive preparation requires one selected operator")
    return SelectedOperator(
        operator_id=selection.candidate_id,
        requested_skill_key=selection.requested_skill_key,
    )


def prepare_selected_kernel_action(
    contract: ScenarioContract,
    snapshot: ScenarioSnapshot,
    *,
    action_id: str,
    actor_id: str,
    player_action: str,
    selection: SelectedOperator,
) -> SelectedKernelAction:
    """Resolve any source's selection through the same closed authority path."""

    operator = next(
        (item for item in contract.operators if item.operator_id == selection.operator_id),
        None,
    )
    if operator is None:
        raise ValueError("Selected operator is outside the bound scenario contract")
    allowed_skills = {choice.skill_key for choice in operator.skill_choices}
    if (
        selection.requested_skill_key is not None
        and selection.requested_skill_key not in allowed_skills
    ):
        raise ValueError("Selected skill is outside the operator's allowed choices")

    preview = ActionResolutionKernel.from_contract(contract).preview(
        snapshot,
        ActionIntent(
            action_id=action_id,
            actor_id=actor_id,
            goal=player_action,
            operator_id=operator.operator_id,
            requested_skill_key=selection.requested_skill_key,
        ),
    )
    narrative = deterministic_kernel_narrative(
        contract,
        preview,
        player_action,
        snapshot=snapshot,
    )
    return SelectedKernelAction(
        operator=operator,
        preview=preview,
        deterministic_narrative=narrative,
    )


__all__ = [
    "SelectedKernelAction",
    "SelectedOperator",
    "prepare_selected_kernel_action",
    "selected_operator_from_semantic",
]

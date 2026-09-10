"""Target-specific weak-model proposals materialized into server-owned Scenario IR."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_kp.platform.resolution.check_catalog import (
    ScenarioSourceCheck,
    check_term_occurs_in_text,
)
from ai_kp.platform.resolution.contracts import OutcomeNarrativeCue, WorldCommand
from ai_kp.platform.resolution.effect_catalog import ScenarioSourceEffect
from ai_kp.platform.resolution.scenario_ir_models import (
    IrAbstractCheck,
    IrAction,
    ScenarioIrBatch,
)
from ai_kp.platform.resolution.source_coverage import SourceCoverageSupplementTarget
from ai_kp.platform.resolution.source_outcomes import (
    action_goal_boundary_command,
    extract_explicit_fact_assignments,
    extract_source_outcome_clauses,
    source_authorizes_push,
    unspecified_outcome_summary,
)

_ACTION_TARGET_KEYS = frozenset({"explicit_checks", "explicit_ruleset_effect"})
SERVER_COVERAGE_ACTION_ASSUMPTION_PREFIX = "Server-authored coverage action: "
SERVER_ACTION_GOAL_BOUNDARY_ASSUMPTION_PREFIX = (
    "Server action-goal boundary applied: "
)
_EFFECT_SLOT_KEYS = (
    "effect_key",
    "always_payload",
    "success_payload",
    "failure_payload",
)


class CoverageActionProposal(BaseModel):
    """Semantic slots only; identity, provenance, policy, and commands stay server-owned."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=240)


class CoverageActionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    confidence: Literal["low", "medium", "high"] = "medium"
    assumptions: tuple[str, ...] = Field(default=(), max_length=8)
    actions: tuple[CoverageActionProposal, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def reject_duplicate_actions(self) -> CoverageActionEnvelope:
        signatures = tuple(action.model_dump_json() for action in self.actions)
        if len(signatures) != len(set(signatures)):
            raise ValueError("Coverage action proposals must be unique")
        return self


def _action_schema_json(*, include_effects: bool) -> str:
    return json.dumps(
        CoverageActionEnvelope.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )


_COVERAGE_CHECK_SCHEMA_JSON = _action_schema_json(include_effects=False)


def coverage_action_schema_json(*, include_effects: bool) -> str:
    return _COVERAGE_CHECK_SCHEMA_JSON


def narrow_action_proposal_payload(
    payload: Any,
    *,
    include_effects: bool,
) -> tuple[Any, int]:
    """Discard model-owned effects; values are always extracted from source."""

    if not isinstance(payload, dict):
        return payload, 0
    actions = payload.get("actions")
    if not isinstance(actions, list):
        return payload, 0
    discarded = 0
    for action in actions:
        if not isinstance(action, dict):
            continue
        for key in _EFFECT_SLOT_KEYS:
            if key in action:
                action.pop(key)
                discarded += 1
    return payload, discarded


def supports_action_materialization(
    targets: tuple[SourceCoverageSupplementTarget, ...],
) -> bool:
    return bool(targets) and all(
        target.requirement_key in _ACTION_TARGET_KEYS
        and target.acceptable_record_kinds == ("operators",)
        for target in targets
    )


def _effect_command(effect: ScenarioSourceEffect) -> WorldCommand:
    return WorldCommand(
        kind="apply_ruleset_effect",
        event_type=effect.effect_key,
        payload=effect.payload,
    )


def _checks_for_proposal(
    proposal: CoverageActionProposal,
    source_checks: tuple[ScenarioSourceCheck, ...],
) -> tuple[ScenarioSourceCheck, ...]:
    matched = tuple(
        item
        for item in source_checks
        if check_term_occurs_in_text(item.term, proposal.title)
    )
    if matched:
        return matched
    if not source_checks:
        return ()
    raise ValueError(
        "Action title must name the source check it represents"
    )


def _effects_for_proposal(
    proposal: CoverageActionProposal,
    source_effects: tuple[ScenarioSourceEffect, ...],
) -> tuple[ScenarioSourceEffect, ...]:
    matched = tuple(
        effect
        for effect in source_effects
        if any(str(value).casefold() in proposal.title.casefold() for value in effect.payload.values())
    )
    if matched:
        return matched
    # Catalog extraction, not presentation text, owns effect authority.  A
    # canonical payload can legitimately differ from its source spelling (for
    # example whitespace is removed, or ``damage bonus (1d4)`` becomes
    # ``+1d4``).  When the source exposes exactly one effect there is no unsafe
    # assignment choice, so retain it for this source-local coverage action.
    # Multiple unmatched effects remain unresolved instead of being combined
    # onto an arbitrary action.
    if len(source_effects) == 1:
        return source_effects
    return ()


def _effect_commands(
    effects: tuple[ScenarioSourceEffect, ...],
    outcome: str,
) -> tuple[WorldCommand, ...]:
    return tuple(
        _effect_command(effect)
        for effect in effects
        if effect.outcome == outcome
    )


def materialize_action_envelope(
    envelope: CoverageActionEnvelope,
    *,
    source_block_id: str,
    record_id_prefix: str,
    source_checks: tuple[ScenarioSourceCheck, ...],
    source_effects: tuple[ScenarioSourceEffect, ...] = (),
    source_text: str = "",
) -> ScenarioIrBatch:
    """Build strict model-facing records without accepting model-owned authority fields."""

    actions: list[IrAction] = []
    for index, proposal in enumerate(envelope.actions, start=1):
        action_id = f"{record_id_prefix}action_{index:02d}"
        proposal_checks = _checks_for_proposal(proposal, source_checks)
        proposal_effects = _effects_for_proposal(proposal, source_effects)
        # Outcome prose cannot be assigned safely when one source block describes
        # several checks but this proposal represents only a subset of them.
        outcome_clauses = (
            extract_source_outcome_clauses(source_text)
            if len(proposal_checks) == len(source_checks)
            else ()
        )
        cue_by_outcome = {item.outcome_key: item for item in outcome_clauses}
        push_authorized = (
            source_authorizes_push(source_text)
            and "pushed_failure" in cue_by_outcome
        )
        abstract_checks = tuple(
            IrAbstractCheck(
                term=source_check.term,
                difficulty=source_check.difficulty,
                hidden=False,
                reason="来源点名检定；服务器规则目录从目标原文提取候选。",
                allow_push=push_authorized,
                failure_stakes=(
                    cue_by_outcome["failure"].public_summary
                    if "failure" in cue_by_outcome
                    else ""
                ),
                pushed_failure_stakes=(
                    cue_by_outcome["pushed_failure"].public_summary
                    if push_authorized and "pushed_failure" in cue_by_outcome
                    else ""
                ),
            )
            for source_check in proposal_checks
        )
        outcome_commands = {
            outcome: _effect_commands(proposal_effects, outcome)
            for outcome in ("success", "failure", "pushed_failure")
        }
        for outcome in tuple(outcome_commands):
            if outcome in cue_by_outcome and not outcome_commands[outcome]:
                explicit_assignments = extract_explicit_fact_assignments(
                    cue_by_outcome[outcome].public_summary
                )
                outcome_commands[outcome] = tuple(
                    WorldCommand(
                        kind="set_fact",
                        path=assignment.path,
                        value=assignment.value,
                    )
                    for assignment in explicit_assignments
                ) or (
                    WorldCommand(
                        kind="set_fact",
                        path=f"source_outcomes.{action_id}.{outcome}_observed",
                        value=True,
                    ),
                )
        actions.append(
            IrAction(
                id=action_id,
                title=proposal.title,
                intent_hints=(proposal.title[:160],),
                policy="required_check" if abstract_checks else "automatic",
                abstract_checks=abstract_checks,
                public_setup=(
                    "The action enters resolution." if outcome_clauses else ""
                ),
                narrative_cues=tuple(
                    OutcomeNarrativeCue(
                        outcome_key=item.outcome_key,
                        public_summary=item.public_summary,
                    )
                    for item in outcome_clauses
                ),
                always=_effect_commands(proposal_effects, "always"),
                on_success=outcome_commands["success"],
                on_failure=outcome_commands["failure"],
                on_pushed_failure=outcome_commands["pushed_failure"],
                source_block_ids=(source_block_id,),
            )
        )
    return ScenarioIrBatch(
        confidence=envelope.confidence,
        assumptions=(
            *envelope.assumptions,
            *(
                f"{SERVER_COVERAGE_ACTION_ASSUMPTION_PREFIX}{action.id}"
                for action in actions
            ),
        ),
        actions=tuple(actions),
    )


def ensure_unspecified_check_goal_boundaries(
    batch: ScenarioIrBatch,
) -> ScenarioIrBatch:
    """Complete unstated checked branches with outcome-only server authority.

    This runs after ruleset shorthand has been split into real outcome effects.
    It therefore fills only branches that still have no authoritative command and
    cannot shadow source-authored rewards, clues, damage, or other consequences.
    """

    actions: list[IrAction] = []
    boundary_action_ids: list[str] = []
    for action in batch.actions:
        if not action.abstract_checks and not action.checks:
            actions.append(action)
            continue
        success = action.on_success
        failure = action.on_failure
        added_outcomes: list[Literal["success", "failure"]] = []
        if not success:
            success = (action_goal_boundary_command(action.id, "success"),)
            added_outcomes.append("success")
        if not failure:
            failure = (action_goal_boundary_command(action.id, "failure"),)
            added_outcomes.append("failure")
        if not added_outcomes:
            actions.append(action)
            continue
        boundary_action_ids.append(action.id)
        cues = list(action.narrative_cues)
        narrated = {item.outcome_key for item in cues}
        cues.extend(
            OutcomeNarrativeCue(
                outcome_key=outcome,
                public_summary=unspecified_outcome_summary(action.title, outcome),
            )
            for outcome in added_outcomes
            if outcome not in narrated
        )
        abstract_checks = tuple(
            check.model_copy(
                update={
                    "failure_stakes": unspecified_outcome_summary(
                        action.title,
                        "failure",
                    )
                }
            )
            if "failure" in added_outcomes and not check.failure_stakes
            else check
            for check in action.abstract_checks
        )
        checks = tuple(
            check.model_copy(
                update={
                    "failure_stakes": unspecified_outcome_summary(
                        action.title,
                        "failure",
                    )
                }
            )
            if "failure" in added_outcomes and not check.failure_stakes
            else check
            for check in action.checks
        )
        actions.append(action.model_copy(update={
            "abstract_checks": abstract_checks,
            "checks": checks,
            "on_success": success,
            "on_failure": failure,
            "narrative_cues": tuple(cues),
            "public_setup": action.public_setup or "The action enters resolution.",
        }))
    assumptions = tuple(
        dict.fromkeys(
            (
                *(
                    f"{SERVER_ACTION_GOAL_BOUNDARY_ASSUMPTION_PREFIX}{action_id}"
                    for action_id in boundary_action_ids
                ),
                *batch.assumptions,
            )
        )
    )[:16]
    return batch.model_copy(
        update={"actions": tuple(actions), "assumptions": assumptions}
    )


__all__ = [
    "SERVER_ACTION_GOAL_BOUNDARY_ASSUMPTION_PREFIX",
    "SERVER_COVERAGE_ACTION_ASSUMPTION_PREFIX",
    "CoverageActionEnvelope",
    "coverage_action_schema_json",
    "ensure_unspecified_check_goal_boundaries",
    "materialize_action_envelope",
    "narrow_action_proposal_payload",
    "supports_action_materialization",
]

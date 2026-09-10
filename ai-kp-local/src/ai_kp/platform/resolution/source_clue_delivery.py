"""Conservative recovery of source-declared clue discovery semantics."""

from __future__ import annotations

import re
from collections.abc import Mapping

from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    OutcomeNarrativeCue,
    ScenarioContract,
)
from ai_kp.platform.resolution.source_outcomes import normalized_source_contains

_PRIVATE_SOURCE_MARKER = re.compile(
    r"(?:仅供守秘人|守秘人信息|不得告知玩家|不向玩家公开|"
    r"\b(?:keeper|gm)\s+only\b|\bsecret\s+(?:keeper|gm)\s+information\b)",
    re.IGNORECASE,
)


def _commits(operator: ActionOperator, path: str, value: object) -> bool:
    expected = path.removeprefix("facts.")
    return any(
        command.kind == "set_fact"
        and command.path == expected
        and type(command.value) is type(value)
        and command.value == value
        for command in (*operator.always_commands, *operator.success_commands)
    )


def _source_authorizes_delivery(
    *,
    source_id: str,
    content: str,
    source_texts: Mapping[str, str],
) -> bool:
    text = source_texts.get(source_id, "")
    if not text or not normalized_source_contains(text, content):
        return False
    # These are explicit table-boundary labels, not ordinary story words such
    # as "secret".  A declared route is never allowed to turn KP-only prose
    # into player-visible output.
    return _PRIVATE_SOURCE_MARKER.search(text) is None


def materialize_source_declared_clue_delivery(
    contract: ScenarioContract,
    *,
    source_texts: Mapping[str, str],
) -> ScenarioContract:
    """Complete an exact, source-declared clue outcome.

    Models sometimes declare the right clue/operator relationship but omit the
    mechanical fact commit or its public success cue.  This repair is limited
    to one already-declared route whose operator and clue share exact source
    blocks, and whose public payload is present
    verbatim after source normalization.  It does not invent routes,
    infer locations, or expose explicitly private source material.  Operator
    availability is unchanged; the final compiler remains the sole authority
    that proves whether the declared route is reachable.
    """

    operators = {item.operator_id: item for item in contract.operators}
    proposals: list[tuple[str, str, tuple[str, ...]]] = []
    for clue in contract.clues:
        if clue.fact_value is not True or not clue.fact_path.removeprefix(
            "facts."
        ).startswith("clues."):
            continue
        content = tuple(item.strip() for item in clue.public_content if item.strip())
        if not content:
            continue
        clue_sources = {ref.source_block_id for ref in clue.source_refs}
        candidates: list[str] = []
        for operator_id in clue.discovery_operator_ids:
            operator = operators.get(operator_id)
            if operator is None or operator.policy in {"impossible", "clarification"}:
                continue
            if not _commits(operator, clue.fact_path, clue.fact_value):
                # Disclosure may complete an authoritative state transition;
                # it must never manufacture that transition from a model-owned
                # discovery id or from server-injected presentation content.
                continue
            shared = clue_sources & {
                ref.source_block_id for ref in operator.source_refs
            }
            if not shared:
                continue
            if all(
                any(
                    _source_authorizes_delivery(
                        source_id=source_id,
                        content=item,
                        source_texts=source_texts,
                    )
                    for source_id in shared
                )
                for item in content
            ):
                candidates.append(operator_id)
        if len(candidates) == 1:
            proposals.append((clue.clue_id, candidates[0], content))

    if not proposals:
        return contract
    grouped: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for clue_id, operator_id, content in proposals:
        grouped.setdefault(operator_id, []).append((clue_id, content))
    # One action publishing several independently authored clue records is not
    # enough evidence to decide their ordering or disclosure boundary.
    proposal_by_operator = {
        operator_id: items[0]
        for operator_id, items in grouped.items()
        if len(items) == 1
    }
    if not proposal_by_operator:
        return contract

    clues = {item.clue_id: item for item in contract.clues}
    changed = False
    repaired: list[ActionOperator] = []
    for operator in contract.operators:
        proposal = proposal_by_operator.get(operator.operator_id)
        if proposal is None:
            repaired.append(operator)
            continue
        clue_id, content = proposal
        clue = clues[clue_id]
        success_cue = next(
            (cue for cue in operator.narrative_cues if cue.outcome_key == "success"),
            None,
        )
        delivered = {
            *operator.automatic_information,
            *(cue.public_summary for cue in operator.narrative_cues if cue.outcome_key == "success"),
        }
        if len(content) > 1 and not all(item in delivered for item in content):
            repaired.append(operator)
            continue
        if len(content) == 1 and success_cue is not None and content[0] not in delivered:
            repaired.append(operator)
            continue
        commands = operator.success_commands
        cues = operator.narrative_cues
        if not all(item in delivered for item in content):
            if len(cues) >= 8:
                repaired.append(operator)
                continue
            cues = (
                *cues,
                OutcomeNarrativeCue(outcome_key="success", public_summary=content[0]),
            )
        replacement = operator.model_copy(
            update={
                "public_setup": (
                    operator.public_setup or "该行动将按来源约定结算可公开信息。"
                ),
                "narrative_cues": cues,
                "success_commands": commands,
            }
        )
        changed = changed or replacement != operator
        repaired.append(replacement)
    return contract.model_copy(update={"operators": tuple(repaired)}) if changed else contract


__all__ = ["materialize_source_declared_clue_delivery"]

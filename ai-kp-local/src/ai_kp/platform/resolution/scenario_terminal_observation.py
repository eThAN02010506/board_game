"""Server-owned actions that confirm source-explicit terminal observations."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from ai_kp.platform.resolution.contracts import OutcomeNarrativeCue, WorldCommand
from ai_kp.platform.resolution.scenario_ir_models import IrAction, ScenarioIrBatch
from ai_kp.platform.resolution.source_coverage import (
    SourceCoverageSupplementTarget,
    extract_terminal_observation_clauses,
)

_TERMINAL_OBSERVATION_FACT_PREFIX = "source_terminal_observations."
_TERMINAL_OBSERVATION_FACT = re.compile(
    r"^source_terminal_observations\.[0-9a-f]{24}\.confirmed$"
)


def supports_terminal_observation_materialization(
    targets: tuple[SourceCoverageSupplementTarget, ...],
) -> bool:
    return bool(targets) and all(
        target.requirement_key == "explicit_terminal_observation"
        and target.acceptable_record_kinds == ("operators",)
        for target in targets
    )


def terminal_observation_fact_path(
    *, source_block_id: str, clause: str
) -> str:
    identity = json.dumps(
        {"source_block_id": source_block_id, "clause": clause},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"{_TERMINAL_OBSERVATION_FACT_PREFIX}{digest}.confirmed"


def materialize_terminal_observation_actions(
    *,
    clauses: tuple[str, ...],
    source_block_id: str,
    record_id_prefix: str,
) -> ScenarioIrBatch:
    """Create explicit confirmations, never attempts or invented resolution rolls."""

    actions = tuple(
        IrAction(
            id=f"{record_id_prefix}terminal_observation_{index:02d}",
            title=clause,
            intent_hints=(clause[:160],),
            public_setup=clause,
            narrative_cues=(
                OutcomeNarrativeCue(
                    outcome_key="success",
                    public_summary=clause,
                ),
            ),
            # Confirmation is an explicit table choice after the condition is
            # observable. It never rolls or turns a desired attempt into success.
            policy="choice",
            global_action=True,
            on_success=(
                WorldCommand(
                    kind="set_fact",
                    path=terminal_observation_fact_path(
                        source_block_id=source_block_id,
                        clause=clause,
                    ),
                    value=True,
                ),
            ),
            source_block_ids=(source_block_id,),
        )
        for index, clause in enumerate(clauses, start=1)
    )
    return ScenarioIrBatch(confidence="high", actions=actions)


def is_source_terminal_observation_action(
    action: IrAction,
    *,
    source_texts: Mapping[str, str] | None,
) -> bool:
    """Authenticate the complete server-derived action from immutable source."""

    if (
        source_texts is None
        or action.policy != "choice"
        or not action.global_action
        or action.location_slot is not None
        or action.location_id is not None
        or action.preconditions
        or action.checks
        or action.abstract_checks
        or action.automatic_information
        or action.response_obligation_ids
        or len(action.source_block_ids) != 1
        or action.always
        or action.on_failure
        or action.on_pushed_failure
        or len(action.on_success) != 1
        or action.rationale
        or action.maximum_effect
        or action.clarification_prompt
    ):
        return False
    source_block_id = action.source_block_ids[0]
    source_text = source_texts.get(source_block_id)
    if not source_text:
        return False
    command = action.on_success[0]
    for clause in extract_terminal_observation_clauses(source_text):
        if (
            action.title == clause
            and action.intent_hints == (clause[:160],)
            and action.public_setup == clause
            and action.narrative_cues
            == (
                OutcomeNarrativeCue(
                    outcome_key="success",
                    public_summary=clause,
                ),
            )
            and command.kind == "set_fact"
            and command.path
            == terminal_observation_fact_path(
                source_block_id=source_block_id,
                clause=clause,
            )
            and command.value is True
        ):
            return True
    return False


def is_terminal_observation_fact_command(command: WorldCommand) -> bool:
    return bool(
        command.kind == "set_fact"
        and command.path is not None
        and _TERMINAL_OBSERVATION_FACT.fullmatch(command.path)
        and command.value is True
    )


__all__ = [
    "is_source_terminal_observation_action",
    "is_terminal_observation_fact_command",
    "materialize_terminal_observation_actions",
    "supports_terminal_observation_materialization",
    "terminal_observation_fact_path",
]

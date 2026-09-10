"""Server-owned ending catalogs and deterministic review contractions."""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from typing import Protocol

from ai_kp.platform.resolution.candidate_ranking import (
    MINIMUM_CANDIDATE_SCORE,
    SMALL_CANDIDATE_LIMIT,
    SemanticCandidateRanker,
)
from ai_kp.platform.resolution.causal_validation import operator_has_causal_result
from ai_kp.platform.resolution.contracts import EndingRule, ScenarioContract, StateCondition
from ai_kp.platform.resolution.evidence_compiler import EvidenceBoundContractCandidate
from ai_kp.platform.resolution.playability import (
    ReachableOutcomeExploration,
    ScenarioPlayabilityAnalyzer,
)
from ai_kp.platform.resolution.scenario_ending_supplement import (
    EndingOperatorCandidate,
    EndingStateCandidate,
    EndingStateProducer,
)


def _contract_cache_key(contract: ScenarioContract) -> str:
    return json.dumps(
        contract.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _source_mentions_state_candidate(
    source_text: str, candidate: EndingStateCandidate
) -> bool:
    """Require a source-visible state name before exposing its values to a model."""

    compact_source = "".join(
        re.findall(r"[^\W_]", source_text.casefold(), re.UNICODE)
    )
    for phrase in (candidate.title, candidate.path):
        compact_phrase = "".join(
            re.findall(r"[^\W_]", phrase.casefold(), re.UNICODE)
        )
        if len(compact_phrase) >= 4 and compact_phrase in compact_source:
            return True
    return False


@lru_cache(maxsize=32)
def _reachable_outcomes_cached(
    contract_json: str, maximum_states: int
) -> ReachableOutcomeExploration:
    contract = ScenarioContract.model_validate_json(contract_json)
    return ScenarioPlayabilityAnalyzer(
        maximum_states=maximum_states
    ).reachable_operator_outcome_exploration(contract)


def clear_ending_catalog_cache() -> None:
    """Test/operational hook for invalidating bounded contract exploration."""

    _reachable_outcomes_cached.cache_clear()


class _ReviewIssue(Protocol):
    group: str
    record_id: str
    problem: str


class _CompilerReview(Protocol):
    review_kind: str
    issues: tuple[_ReviewIssue, ...]


def build_ending_operator_candidates(
    contract: ScenarioContract,
    source_text: str,
    *,
    reachable_outcomes: frozenset[tuple[str, str]] | None = None,
) -> tuple[EndingOperatorCandidate, ...]:
    """Rank a bounded executable outcome catalog for one ending source."""

    ranked = SemanticCandidateRanker().rank(
        contract,
        source_text,
        snapshot=None,
        include_task_methods=False,
        # Rank the bounded contract collection before applying reachability;
        # otherwise high-scoring stale operators could occupy an intermediate
        # top-32 window and hide the first executable producer.
        limit=max(SMALL_CANDIDATE_LIMIT, len(contract.operators)),
    )
    operators = {item.operator_id: item for item in contract.operators}
    if reachable_outcomes is None:
        reachable_outcomes = _reachable_outcomes_cached(
            _contract_cache_key(contract), 10_000
        ).outcomes
    candidates: list[EndingOperatorCandidate] = []
    for item in ranked:
        operator = operators[item.candidate_id]
        if not item.available or item.score < MINIMUM_CANDIDATE_SCORE:
            continue
        # The catalog is an authority boundary, not merely a retrieval result.
        # Never advertise an outcome that the compiler/kernel would reject as
        # terminal proof.  In particular, document extraction often yields an
        # ungated automatic prose operator for text such as "HP reaches zero";
        # allowing a weak model to link an ending to that operator makes the
        # ending immediately selectable without the stated cause occurring.
        if not operator_has_causal_result(operator) or (
            operator.policy == "automatic"
            and not operator.preconditions
            and not operator.skill_choices
        ):
            continue
        proposed_outcomes = [
            "success",
            *(branch.outcome_key for branch in operator.outcome_branches),
        ]
        if operator.skill_choices or operator.failure_commands:
            proposed_outcomes.append("failure")
        outcomes = tuple(
            dict.fromkeys(
                outcome
                for outcome in proposed_outcomes
                if (operator.operator_id, outcome) in reachable_outcomes
            )
        )
        if not outcomes:
            continue
        candidates.append(
            EndingOperatorCandidate(
                operator_id=operator.operator_id,
                title=operator.title,
                aliases=tuple(
                    dict.fromkeys(
                        hint
                        for hint in operator.intent_hints
                        if hint != operator.title
                    )
                ),
                allowed_outcomes=outcomes,
            )
        )
        if len(candidates) >= SMALL_CANDIDATE_LIMIT:
            break
    return tuple(candidates)


def build_ending_state_candidates(
    contract: ScenarioContract,
    *,
    reachable_outcomes: frozenset[tuple[str, str]] | None = None,
) -> tuple[EndingStateCandidate, ...]:
    """Expose a bounded catalog of authoritative produced state, never free paths."""

    candidates: list[EndingStateCandidate] = []
    if reachable_outcomes is None:
        reachable_outcomes = _reachable_outcomes_cached(
            _contract_cache_key(contract), 10_000
        ).outcomes
    numeric_operators = ("eq", "ne", "lt", "lte", "gt", "gte")
    scalar_operators = ("eq", "ne")
    for entity in contract.entities:
        produced: list[tuple[object, str, str]] = []
        for operator in contract.operators:
            if operator.policy in {"impossible", "clarification"}:
                continue
            outcomes = [("success", operator.success_commands)]
            if operator.skill_choices or operator.failure_commands:
                outcomes.append(("failure", operator.failure_commands))
            outcomes.extend(
                (branch.outcome_key, branch.commands)
                for branch in operator.outcome_branches
            )
            for outcome, branch_commands in outcomes:
                if (operator.operator_id, outcome) not in reachable_outcomes:
                    continue
                for command in (*operator.always_commands, *branch_commands):
                    if (
                        command.kind == "set_entity_status"
                        and command.entity_id == entity.entity_id
                    ):
                        production = (command.value, operator.operator_id, outcome)
                        if production not in produced:
                            produced.append(production)
        if not produced:
            continue
        values = tuple(
            dict.fromkeys(
                (entity.initial_status, *(value for value, _, _ in produced))
            )
        )[:16]
        candidates.append(
            EndingStateCandidate(
                path=f"entities.{entity.entity_id}",
                title=entity.title,
                allowed_operators=scalar_operators,
                value_kind="scalar",
                allowed_values=values,
                producers=tuple(
                    EndingStateProducer(
                        operator_id=operator_id,
                        outcome=outcome,
                        value=value,
                    )
                    for value, operator_id, outcome in produced[:16]
                ),
            )
        )
    for clock in contract.clocks:
        producers: list[EndingStateProducer] = []
        for operator in contract.operators:
            if operator.policy in {"impossible", "clarification"}:
                continue
            outcomes = [("success", operator.success_commands)]
            if operator.skill_choices or operator.failure_commands:
                outcomes.append(("failure", operator.failure_commands))
            outcomes.extend(
                (branch.outcome_key, branch.commands)
                for branch in operator.outcome_branches
            )
            for outcome, commands in outcomes:
                if (operator.operator_id, outcome) not in reachable_outcomes:
                    continue
                delta = sum(
                    command.delta or 0
                    for command in (*operator.always_commands, *commands)
                    if command.kind == "advance_clock"
                    and command.clock_id == clock.clock_id
                )
                if not delta:
                    continue
                value = min(
                    clock.maximum_value,
                    max(0, clock.initial_value + delta),
                )
                producer = EndingStateProducer(
                    operator_id=operator.operator_id,
                    outcome=outcome,
                    value=value,
                    repeatable_delta=delta,
                )
                if producer not in producers:
                    producers.append(producer)
        candidates.append(
            EndingStateCandidate(
                path=f"clocks.{clock.clock_id}",
                title=clock.title,
                allowed_operators=numeric_operators,
                value_kind="number",
                initial_value=clock.initial_value,
                minimum_value=0,
                maximum_value=clock.maximum_value,
                producers=tuple(producers[:16]),
            )
        )
    for resource in contract.resources:
        candidates.append(
            EndingStateCandidate(
                path=f"resources.{resource.resource_id}",
                title=resource.title,
                allowed_operators=numeric_operators,
                value_kind="number",
                minimum_value=resource.minimum_value,
                maximum_value=resource.maximum_value,
            )
        )

    fact_values: dict[str, list[object]] = {}
    fact_titles: dict[str, str] = {}
    fact_producers: dict[str, list[tuple[object, str, str]]] = {}

    def canonical_fact_path(path: str) -> str:
        return f"facts.{path.removeprefix('facts.')}"

    def add_fact(path: str, title: str, value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                add_fact(f"{path}.{key}" if path else str(key), str(key), child)
            return
        if not (
            value is None
            or isinstance(value, (str, bool, int))
            or isinstance(value, float) and math.isfinite(value)
        ):
            return
        canonical = canonical_fact_path(path)
        fact_titles.setdefault(canonical, title)
        values = fact_values.setdefault(canonical, [])
        if not any(type(value) is type(item) and value == item for item in values):
            values.append(value)

    for path, value in contract.initial_facts.items():
        add_fact(str(path), str(path), value)
    for operator in contract.operators:
        outcomes = [("success", operator.success_commands)]
        if operator.skill_choices or operator.failure_commands:
            outcomes.append(("failure", operator.failure_commands))
        outcomes.extend(
            (branch.outcome_key, branch.commands)
            for branch in operator.outcome_branches
        )
        safe_ending_producer = (
            operator.policy not in {"impossible", "clarification"}
            and (
                operator.policy != "automatic"
                or bool(operator.preconditions)
                or bool(operator.skill_choices)
            )
        )
        for outcome, branch_commands in outcomes:
            for command in (*operator.always_commands, *branch_commands):
                if command.kind != "set_fact" or not command.path:
                    continue
                add_fact(command.path, command.path, command.value)
                if safe_ending_producer and (
                    operator.operator_id, outcome
                ) in reachable_outcomes:
                    canonical = canonical_fact_path(command.path)
                    producers = fact_producers.setdefault(canonical, [])
                    production = (command.value, operator.operator_id, outcome)
                    if production not in producers:
                        producers.append(production)
    for clue in contract.clues:
        add_fact(clue.fact_path, clue.title, clue.fact_value)
    for path, values in fact_values.items():
        advertised_values = tuple(values[:16])
        producers = tuple(
            EndingStateProducer(
                operator_id=operator_id,
                outcome=outcome,
                value=value,
            )
            for value, operator_id, outcome in fact_producers.get(path, ())
            if any(
                type(value) is type(item) and value == item
                for item in advertised_values
            )
        )
        candidates.append(
            EndingStateCandidate(
                path=path,
                title=fact_titles[path],
                allowed_operators=scalar_operators,
                value_kind="scalar",
                allowed_values=advertised_values,
                producers=producers[:16],
            )
        )
    return tuple(candidates[:32])


def build_ending_catalogs(
    contract: ScenarioContract,
    source_text: str,
    *,
    maximum_states: int = 10_000,
) -> tuple[tuple[EndingOperatorCandidate, ...], tuple[EndingStateCandidate, ...]]:
    """Build catalogs from witnessed outcomes in one bounded exploration.

    When the state cap is reached the witnessed set is a safe under-approximation:
    an omitted outcome can only withhold a proposal, while every advertised
    outcome still has a concrete deterministic path.  Final release readiness
    remains governed by the complete compiler/playability proof.
    """

    reachable = _reachable_outcomes_cached(
        _contract_cache_key(contract), maximum_states
    ).outcomes
    state_candidates = build_ending_state_candidates(
        contract, reachable_outcomes=reachable
    )
    return (
        build_ending_operator_candidates(
            contract, source_text, reachable_outcomes=reachable
        ),
        # A source ending needs an executable transition, not merely a path that
        # exists in the schema or initial snapshot.  Keep the broader standalone
        # state catalog available for diagnostics, but never expose an
        # unproduced clock/resource/fact to the model-owned ending selector.
        tuple(
            item
            for item in state_candidates
            if item.producers and _source_mentions_state_candidate(source_text, item)
        ),
    )


def contract_compiler_proven_premature_endings(
    candidate: EvidenceBoundContractCandidate,
    review: _CompilerReview,
) -> tuple[EvidenceBoundContractCandidate, tuple[str, ...]]:
    """Remove only terminal rules proved true in the authoritative initial state."""

    if review.review_kind != "deterministic_compiler":
        return candidate, ()
    premature_ids = tuple(
        dict.fromkeys(
            issue.record_id
            for issue in review.issues
            if issue.group == "endings"
            and "is already satisfied in the initial state" in issue.problem
        )
    )
    if not premature_ids:
        return candidate, ()
    payload = candidate.contract.model_dump(mode="json")
    known_ids = {str(item["ending_id"]) for item in payload.get("endings", [])}
    removed = tuple(item for item in premature_ids if item in known_ids)
    if not removed:
        return candidate, ()
    removed_set = set(removed)
    payload["endings"] = [
        item
        for item in payload.get("endings", [])
        if item.get("ending_id") not in removed_set
    ]
    contracted = candidate.model_copy(
        update={
            "contract": ScenarioContract.model_validate(payload),
            "assumptions": tuple(
                dict.fromkeys(
                    (
                        *candidate.assumptions,
                        *(
                            "Server premature ending contracted: " + ending_id
                            for ending_id in removed
                        ),
                    )
                )
            )[:64],
        }
    )
    return contracted, removed


def merge_equivalent_endings(
    existing: tuple[EndingRule, ...],
    additions: tuple[EndingRule, ...],
) -> tuple[EndingRule, ...]:
    """Append terminal rules once by authoritative condition/command semantics.

    Supplement IDs are transport identities and change on every repair cycle.
    Keeping them in the identity would allow an unchanged weak-model proposal to
    grow the contract forever. The first accepted rule remains canonical while
    any additional provenance is retained.
    """

    def condition_identity(condition: StateCondition) -> str:
        payload = condition.model_dump(mode="json")
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def signature(ending: EndingRule) -> tuple[object, ...]:
        return (
            tuple(sorted(condition_identity(item) for item in ending.all_conditions)),
            tuple(sorted(condition_identity(item) for item in ending.any_conditions)),
            tuple(
                json.dumps(
                    command.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for command in ending.commands
            ),
        )

    merged = list(existing)
    indexes = {signature(item): index for index, item in enumerate(merged)}
    for addition in additions:
        identity = signature(addition)
        previous_index = indexes.get(identity)
        if previous_index is None:
            indexes[identity] = len(merged)
            merged.append(addition)
            continue
        previous = merged[previous_index]
        refs = tuple(dict.fromkeys((*previous.source_refs, *addition.source_refs)))[:16]
        if refs != previous.source_refs:
            merged[previous_index] = previous.model_copy(update={"source_refs": refs})
    return tuple(merged)


__all__ = [
    "build_ending_catalogs",
    "build_ending_operator_candidates",
    "build_ending_state_candidates",
    "clear_ending_catalog_cache",
    "contract_compiler_proven_premature_endings",
    "merge_equivalent_endings",
]

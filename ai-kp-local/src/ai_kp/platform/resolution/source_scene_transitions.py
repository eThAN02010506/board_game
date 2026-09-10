"""Materialize only source-explicit, scene-scoped transitions.

This module handles two narrow cases that are not ordinary map edges: a player
deliberately opening a source-declared passage, and a checked action whose
source-declared failure moves the scene.  It never invents reverse travel.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    ScenarioContract,
    SourceRef,
    StateCondition,
    WorldCommand,
)
from ai_kp.platform.resolution.scenario_action_scope import canonical_location_alias
from ai_kp.platform.resolution.source_scene_location_authority import (
    SourceLocationAuthority,
    mentioned_source_locations,
    same_immediate_source_container,
    same_source_scene_container,
    source_location_catalog,
    source_location_from_ancestry,
)

_CLAUSE_SPLIT = re.compile(r"[.。！？!?]+|\n+")
_PLAYER_PASSAGE = re.compile(
    r"(?:如果|若(?:是)?)\s*(?:调查员(?:们)?|玩家(?:们)?|角色(?:们)?)"
    r"[^.。！？!?\n]{0,120}?(?:打开|移除|打穿|破坏)"
    r"[^.。！？!?\n]{1,120}?(?:就会|会|可以|就可以)"
    r"[^.。！？!?\n]{0,60}?(?:来到|进入)(?P<destination>[^.。！？!?\n]{1,120})|"
    r"\b(?:if|when)\s+(?:the\s+)?(?:investigators?|players?|characters?)\b"
    r"[^.!?\n]{0,120}?\b(?:open|remove|break\s+through|destroy)\b"
    r"[^.!?\n]{1,120}?\b(?:can|will|may)\s+(?:reach|enter)\s+"
    r"(?P<english_destination>[^.!?\n]{1,120})",
    re.IGNORECASE,
)
_FAILURE_MARKER = re.compile(
    r"否则|(?:如果|若(?:是)?)?[^.。！？!?\n]{0,50}?检定失败|"
    r"\b(?:otherwise|if\s+(?:the\s+)?check\s+fails?)\b",
    re.IGNORECASE,
)
_EXPLICIT_CHECK_FAILURE = re.compile(
    r"检定失败|\bcheck\s+fails?\b",
    re.IGNORECASE,
)
_FAILURE_MOVEMENT = re.compile(
    r"(?:调查员(?:们)?|玩家(?:们)?|他们|其|角色(?:们)?|"
    r"\b(?:investigators?|players?|characters?|they)\b)"
    r"[^.。！？!?\n]{0,100}?(?:掉落|掉入|跌入|落入|进入|"
    r"\b(?:fall|drop|enter)(?:s|ed)?\s+(?:into|in|to)\b)",
    re.IGNORECASE,
)
_CHECK_TERM = re.compile(r"[【\[](?P<term>[^】\]]{1,80})[】\]]")
_CHECK_POLICIES = frozenset(
    {"required_check", "optional_check", "conditional_check", "opposed_check"}
)
def _stable_transition_id(
    contract_id: str, source_id: str, origin_id: str, destination_id: str
) -> str:
    payload = f"{contract_id}\x1f{source_id}\x1f{origin_id}\x1f{destination_id}"
    return "source_scene_transition_" + hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()[:24]


def _passage_transitions(
    contract: ScenarioContract,
    *,
    source_refs: Mapping[str, SourceRef],
    source_texts: Mapping[str, str],
    source_section_paths: Mapping[str, tuple[str, ...]],
    catalog: tuple[SourceLocationAuthority, ...],
) -> tuple[ActionOperator, ...]:
    generated: list[ActionOperator] = []
    existing_by_id = {item.operator_id: item for item in contract.operators}
    titles = {item.location_id: item.title for item in contract.locations}
    for source_id, text in source_texts.items():
        source_ref = source_refs.get(source_id)
        section_path = source_section_paths.get(source_id, ())
        origin = source_location_from_ancestry(section_path, catalog)
        if source_ref is None or origin is None:
            continue
        for matched in _PLAYER_PASSAGE.finditer(text):
            destination_text = matched.group("destination") or matched.group(
                "english_destination"
            )
            destinations = tuple(
                item
                for item in mentioned_source_locations(destination_text, catalog)
                if item.location.location_id != origin.location.location_id
                and same_immediate_source_container(origin, item)
            )
            if len(destinations) != 1:
                continue
            destination = destinations[0]
            operator_id = _stable_transition_id(
                contract.contract_id,
                source_id,
                origin.location.location_id,
                destination.location.location_id,
            )
            destination_title = titles[destination.location.location_id]
            intended = ActionOperator(
                operator_id=operator_id,
                title=f"进入{destination_title}",
                intent_hints=(f"进入{destination_title}",),
                public_setup="来源明确说明了打开这条通路的方法。",
                policy="automatic",
                preconditions=(
                    StateCondition(
                        path="scene_id",
                        operator="eq",
                        value=origin.location.location_id,
                    ),
                ),
                success_commands=(
                    WorldCommand(
                        kind="set_scene",
                        value=destination.location.location_id,
                    ),
                ),
                rationale="Server-materialized source-explicit passage transition.",
                maximum_effect="Changes only the active public scene.",
                source_refs=(source_ref,),
            )
            existing = existing_by_id.get(operator_id)
            if existing is not None:
                if existing != intended:
                    raise ValueError(
                        "Source scene transition reserved id conflicts with "
                        f"an authored operator: {operator_id}"
                    )
                continue
            generated.append(intended)
            existing_by_id[operator_id] = intended
    return tuple(generated)


def _scene_origin(operator: ActionOperator) -> str | None:
    values = {
        condition.value
        for condition in operator.preconditions
        if condition.path == "scene_id"
        and condition.operator == "eq"
        and isinstance(condition.value, str)
    }
    return next(iter(values)) if len(values) == 1 else None


def _normalized_check_label(value: str) -> str:
    alias = canonical_location_alias(value)
    for suffix in ("技能检定", "检定", "skillcheck", "check"):
        alias = alias.removesuffix(suffix)
    return alias


def _checked_operator(
    contract: ScenarioContract,
    *,
    source_id: str,
    section_path: tuple[str, ...],
    check_term: str | None,
    source_section_paths: Mapping[str, tuple[str, ...]],
    exact_source_only: bool = False,
) -> ActionOperator | None:
    candidates = tuple(
        operator
        for operator in contract.operators
        if operator.policy in _CHECK_POLICIES
        and operator.skill_choices
        and _scene_origin(operator) is not None
        and any(
            ref.source_block_id == source_id
            or (
                not exact_source_only
                and source_section_paths.get(ref.source_block_id, ()) == section_path
            )
            for ref in operator.source_refs
        )
    )
    if check_term is not None:
        expected = _normalized_check_label(check_term)
        candidates = tuple(
            item
            for item in candidates
            if _normalized_check_label(item.title) == expected
        )
    return candidates[0] if len(candidates) == 1 else None


def _append_failure_transitions(
    contract: ScenarioContract,
    *,
    source_texts: Mapping[str, str],
    source_section_paths: Mapping[str, tuple[str, ...]],
    catalog: tuple[SourceLocationAuthority, ...],
) -> ScenarioContract:
    replacements: dict[str, ActionOperator] = {}
    for source_id, text in source_texts.items():
        section_path = source_section_paths.get(source_id, ())
        for marker in _FAILURE_MARKER.finditer(text):
            outcome = text[marker.end() :]
            movement = _FAILURE_MOVEMENT.search(outcome)
            if movement is None:
                continue
            # A failed check may introduce a second check before movement.  In
            # that case the movement belongs to the later check's own failure
            # marker (for example Luck failure -> Jump -> otherwise fall).
            if _CHECK_TERM.search(outcome[: movement.start()]):
                continue
            outcome_clause = _CLAUSE_SPLIT.split(outcome, maxsplit=1)[0]
            check_terms = tuple(_CHECK_TERM.finditer(text[: marker.start()]))
            check_term = check_terms[-1].group("term") if check_terms else None
            if check_term is None and not _EXPLICIT_CHECK_FAILURE.search(
                marker.group(0)
            ):
                continue
            operator = _checked_operator(
                contract,
                source_id=source_id,
                section_path=section_path,
                check_term=check_term,
                source_section_paths=source_section_paths,
                exact_source_only=check_term is None,
            )
            if operator is None or operator.operator_id in replacements:
                continue
            origin_id = _scene_origin(operator)
            destinations = tuple(
                item
                for item in mentioned_source_locations(outcome_clause, catalog)
                if origin_id is not None
                and item.location.location_id != origin_id
                and same_source_scene_container(
                    origin_id, item, catalog, section_path
                )
            )
            if len(destinations) != 1:
                continue
            destination_id = destinations[0].location.location_id
            command = WorldCommand(kind="set_scene", value=destination_id)
            if command in operator.failure_commands:
                continue
            replacements[operator.operator_id] = operator.model_copy(
                update={
                    "failure_commands": (*operator.failure_commands, command),
                }
            )
    if not replacements:
        return contract
    return contract.model_copy(
        update={
            "operators": tuple(
                replacements.get(item.operator_id, item)
                for item in contract.operators
            )
        }
    )


def materialize_source_scene_transitions(
    contract: ScenarioContract,
    *,
    source_refs: Mapping[str, SourceRef],
    source_texts: Mapping[str, str],
    source_section_paths: Mapping[str, tuple[str, ...]],
    source_scene_keys: Mapping[str, str] | None = None,
) -> ScenarioContract:
    """Apply the two closed source transition forms and nothing else."""

    if not contract.locations:
        return contract
    catalog = source_location_catalog(
        contract,
        source_section_paths=source_section_paths,
        source_scene_keys=source_scene_keys,
    )
    patched = _append_failure_transitions(
        contract,
        source_texts=source_texts,
        source_section_paths=source_section_paths,
        catalog=catalog,
    )
    passages = _passage_transitions(
        patched,
        source_refs=source_refs,
        source_texts=source_texts,
        source_section_paths=source_section_paths,
        catalog=catalog,
    )
    if not passages:
        return patched
    return patched.model_copy(update={"operators": (*patched.operators, *passages)})


__all__ = ["materialize_source_scene_transitions"]

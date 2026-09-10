"""Materialize source-declared, fact-gated investigation destinations.

These opportunities are not physical location links.  They represent facts such
as records naming the office that holds another file, or a witness identifying
the next place to investigate.  Source prose may select only an existing exact
location alias, while an existing operator remains the sole authority for the
fact that unlocks travel.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping

from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    ScenarioContract,
    SourceRef,
    StateCondition,
    WorldCommand,
)
from ai_kp.platform.resolution.scenario_action_scope import canonical_location_alias

_CLAUSE_SPLIT = re.compile(r"[;；。！？!?]+|\n+")
_KNOWN_DESTINATION_RELATION = re.compile(
    r"(?:位于|坐落(?:于)?|记录.{0,80}(?:由.{0,40}保存|保存在)|"
    r"(?:现在|目前|如今)(?:位于|在)|决定前往|指出.{0,60}(?:位置|地方)|"
    r"\b(?:located|situated)\s+(?:at|in)|"
    r"\brecords?.{0,80}(?:kept|held|stored)\s+(?:at|in|by)|"
    r"\bdecid(?:e|es|ed)\s+to\s+(?:go|travel|head)\s+to|"
    r"\bpoint(?:s|ed)?\s+out.{0,60}(?:location|place)\b)",
    re.IGNORECASE,
)
_UNSAFE_RELATION = re.compile(
    r"(?:除非|发现后|解锁|打穿|暗门|墙后|秘密通道|隐藏入口|"
    r"\b(?:unless|after\s+discovering|unlock|"
    r"break\s+through|secret\s+(?:door|passage)|hidden\s+entrance)\b)",
    re.IGNORECASE,
)
_CONDITIONAL_RELATION = re.compile(
    r"(?:如果|若(?:是)?|\b(?:if|when)\b)",
    re.IGNORECASE,
)
_PRODUCER_SUCCESS_CONDITION = re.compile(
    r"(?:(?:如果|若(?:是)?).{0,100}成功|"
    r"\b(?:if|when).{0,100}success(?:ful(?:ly)?)?\b)",
    re.IGNORECASE,
)
_INITIAL_CHOICE = re.compile(
    r"(?:可以.{0,100}(?:也可以|或者|或).{0,100}(?:由你们决定|自由选择)|"
    r"\b(?:choose|decide).{0,120}(?:between|among|where|which)\b)",
    re.IGNORECASE,
)
_UNSAFE_SECTION_COMPONENT = re.compile(
    r"(?:秘密|隐藏|隐秘|藏身处|墙后|暗门|密室|夹层|"
    r"如果|若(?:是)?|除非|解锁后|发现后|"
    r"\b(?:secret|hidden|concealed|behind\s+(?:the\s+)?wall|"
    r"hideout|lair|if|when|unless|after\s+(?:unlocking|discovering))\b)",
    re.IGNORECASE,
)
_COMPOSITE_SEPARATOR = re.compile(r"[/／;；|]")
_RESERVED_FACT_PREFIXES = ("events.operator_outcomes.",)


def _fact_path(path: str) -> str:
    return path.removeprefix("facts.")


def _condition_path(path: str) -> str:
    return f"facts.{_fact_path(path)}"


def _section_group(
    source_id: str,
    source_section_paths: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...] | None:
    path = source_section_paths.get(source_id, ())
    return path or None


def _stable_operator_id(
    contract_id: str,
    source_id: str,
    producer_id: str,
    fact_path: str,
    destination_id: str,
) -> str:
    payload = (
        f"{contract_id}\x1f{source_id}\x1f{producer_id}\x1f"
        f"{fact_path}\x1f{destination_id}"
    )
    return "source_scene_opportunity_" + hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()[:24]


def _location_alias_catalog(
    contract: ScenarioContract,
    *,
    source_scene_keys: Mapping[str, str] | None,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    aliases_by_location: dict[str, set[str]] = {}
    owners: dict[str, set[str]] = defaultdict(set)
    for location in contract.locations:
        values = (
            location.title,
            *(
                (source_scene_keys or {}).get(ref.source_block_id, "")
                for ref in location.source_refs
            ),
        )
        aliases: set[str] = set()
        for value in values:
            alias = canonical_location_alias(value)
            if alias:
                aliases.add(alias)
            aliases.update(
                component_alias
                for component in _COMPOSITE_SEPARATOR.split(value)
                if (component_alias := canonical_location_alias(component))
            )
        aliases_by_location[location.location_id] = aliases
        for alias in aliases:
            owners[alias].add(location.location_id)
    return aliases_by_location, owners


def _clause_destination(
    clause: str,
    *,
    aliases_by_location: Mapping[str, set[str]],
    alias_owners: Mapping[str, set[str]],
) -> str | None:
    if (
        not _KNOWN_DESTINATION_RELATION.search(clause)
        or _UNSAFE_RELATION.search(clause)
        or (
            _CONDITIONAL_RELATION.search(clause)
            and not _PRODUCER_SUCCESS_CONDITION.search(clause)
        )
        or _INITIAL_CHOICE.search(clause)
    ):
        return None
    canonical = canonical_location_alias(
        unicodedata.normalize("NFKC", clause).casefold()
    )
    matched: set[str] = set()
    for aliases in aliases_by_location.values():
        for alias in aliases:
            if alias in canonical and len(alias_owners.get(alias, ())) == 1:
                matched.update(alias_owners[alias])
    return next(iter(matched)) if len(matched) == 1 else None


def _eligible_gate_candidates(
    contract: ScenarioContract,
    *,
    source_id: str,
    source_section_paths: Mapping[str, tuple[str, ...]],
    direct_source_only: bool = False,
) -> tuple[tuple[ActionOperator, str], ...]:
    relation_group = _section_group(source_id, source_section_paths)

    def shares_authority(operator: ActionOperator) -> bool:
        operator_source_ids = tuple(ref.source_block_id for ref in operator.source_refs)
        if source_id in operator_source_ids:
            return True
        if direct_source_only:
            return False
        return bool(
            relation_group is not None
            and any(
                _section_group(item, source_section_paths) == relation_group
                for item in operator_source_ids
            )
        )

    candidates: list[tuple[ActionOperator, str]] = []
    direct_candidates: list[tuple[ActionOperator, str]] = []
    for operator in contract.operators:
        if not shares_authority(operator):
            continue
        for command in operator.success_commands:
            if (
                command.kind == "set_fact"
                and command.path
                and command.value is True
                and not _fact_path(command.path).startswith(_RESERVED_FACT_PREFIXES)
            ):
                candidate = (operator, _fact_path(command.path))
                if candidate not in candidates:
                    candidates.append(candidate)
                if (
                    any(ref.source_block_id == source_id for ref in operator.source_refs)
                    and candidate not in direct_candidates
                ):
                    direct_candidates.append(candidate)

    # A source-bound clue is the strongest available mapping from prose to the
    # exact successful fact.  Prefer it when present, without inventing a path.
    clue_backed: list[tuple[ActionOperator, str]] = []
    operator_by_id = {item.operator_id: item for item in contract.operators}
    for clue in contract.clues:
        clue_source_ids = tuple(ref.source_block_id for ref in clue.source_refs)
        same_authority = source_id in clue_source_ids or bool(
            relation_group is not None
            and any(
                _section_group(item, source_section_paths) == relation_group
                for item in clue_source_ids
            )
        )
        if not same_authority or clue.fact_value is not True:
            continue
        clue_path = _fact_path(clue.fact_path)
        for operator_id in clue.discovery_operator_ids:
            operator = operator_by_id.get(operator_id)
            candidate = (operator, clue_path) if operator is not None else None
            if (
                candidate is not None
                and any(
                    command.kind == "set_fact"
                    and _fact_path(command.path or "") == clue_path
                    and command.value is True
                    for command in operator.success_commands
                )
                and candidate not in clue_backed
            ):
                clue_backed.append(candidate)
    # Exact section co-location is useful only for locating a declared clue;
    # by itself it cannot make an unrelated fact producer authorize travel.
    return tuple(clue_backed or direct_candidates)


def materialize_source_scene_opportunities(
    contract: ScenarioContract,
    *,
    source_refs: Mapping[str, SourceRef],
    source_texts: Mapping[str, str],
    source_section_paths: Mapping[str, tuple[str, ...]],
    source_scene_keys: Mapping[str, str] | None = None,
) -> ScenarioContract:
    """Add automatic fact-gated travel to exact source-known destinations."""

    if not contract.locations or not contract.operators:
        return contract
    aliases_by_location, alias_owners = _location_alias_catalog(
        contract,
        source_scene_keys=source_scene_keys,
    )
    existing_by_id = {item.operator_id: item for item in contract.operators}
    generated: list[ActionOperator] = []
    for source_id, text in source_texts.items():
        source_ref = source_refs.get(source_id)
        section_group = _section_group(source_id, source_section_paths)
        if source_ref is None or (
            section_group is not None
            and _UNSAFE_SECTION_COMPONENT.search("\n".join(section_group))
        ):
            continue
        destination_authorities: dict[str, bool] = {}
        for clause in _CLAUSE_SPLIT.split(text):
            destination = _clause_destination(
                clause,
                aliases_by_location=aliases_by_location,
                alias_owners=alias_owners,
            )
            if destination is None:
                continue
            requires_direct_source = bool(_CONDITIONAL_RELATION.search(clause))
            destination_authorities[destination] = (
                destination_authorities.get(destination, True)
                and requires_direct_source
            )
        if not destination_authorities:
            continue
        location_by_id = {item.location_id: item for item in contract.locations}
        for destination_id, direct_source_only in destination_authorities.items():
            gates = _eligible_gate_candidates(
                contract,
                source_id=source_id,
                source_section_paths=source_section_paths,
                direct_source_only=direct_source_only,
            )
            if len(gates) != 1:
                continue
            producer, gate_path = gates[0]
            refs = tuple(dict.fromkeys((source_ref, *producer.source_refs)))[:16]
            operator_id = _stable_operator_id(
                contract.contract_id,
                source_id,
                producer.operator_id,
                gate_path,
                destination_id,
            )
            destination = location_by_id[destination_id]
            intended = ActionOperator(
                operator_id=operator_id,
                title=f"前往{destination.title}",
                intent_hints=(
                    f"前往{destination.title}",
                    f"去{destination.title}",
                ),
                public_setup=f"调查获得的信息指向{destination.title}。",
                policy="automatic",
                preconditions=(
                    StateCondition(
                        path=_condition_path(gate_path),
                        operator="eq",
                        value=True,
                    ),
                ),
                success_commands=(
                    WorldCommand(kind="set_scene", value=destination_id),
                ),
                rationale=(
                    "Server-materialized source-declared investigation destination."
                ),
                maximum_effect="Changes only the active public scene.",
                source_refs=refs,
            )
            existing = existing_by_id.get(operator_id)
            if existing is not None:
                if existing != intended:
                    raise ValueError(
                        "Source scene opportunity reserved id conflicts with "
                        f"an authored operator: {operator_id}"
                    )
                continue
            generated.append(intended)
            existing_by_id[operator_id] = intended
    if not generated:
        return contract
    return contract.model_copy(update={"operators": (*contract.operators, *generated)})


__all__ = ["materialize_source_scene_opportunities"]

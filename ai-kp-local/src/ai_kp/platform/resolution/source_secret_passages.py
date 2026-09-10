"""Materialize source-proven, discovery-gated passages between sibling scenes.

This is intentionally separate from ordinary navigation and direct scene
transitions. A hidden destination becomes executable only after one exact
source block establishes a discovery fact.
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
from ai_kp.platform.resolution.source_scene_location_authority import (
    SourceLocationAuthority,
    mentioned_source_locations,
    same_immediate_source_container,
    source_location_catalog,
    source_location_from_ancestry,
)

_CLAUSE_SPLIT = re.compile(r"[.。！？!?]+|\n+")
_HIDDEN_OPENING_DISCOVERY = re.compile(
    r"(?:(?:检查|搜查|搜寻|观察|侦查).{0,80}(?:发现|找到|揭示)"
    r".{0,80}(?:墙后|木板后|空洞|夹层|通道|入口|暗门)|"
    r"(?:发现|找到|揭示).{0,80}(?:墙后|木板后|空洞|夹层|通道|入口|暗门)|"
    r"\b(?:inspect(?:ion)?|search(?:ing)?|look(?:ing)?)\b.{0,100}"
    r"\b(?:finds?|discovers?|reveals?)\b.{0,80}"
    r"\b(?:cavity|opening|passage|entrance|secret\s+door|behind\s+the\s+wall)\b)",
    re.IGNORECASE,
)
_AUTOMATIC_OPENING_DISCOVERY = re.compile(
    r"(?:粗略|简单|一眼|很容易|无需.{0,12}检定|不需要.{0,12}检定|"
    r"\b(?:cursory|casual|brief|obvious|automatically|without\s+(?:a\s+)?check)\b)",
    re.IGNORECASE,
)
_DESTINATION_EXPOSURE = re.compile(
    r"(?:(?:墙|木板|隔板|隔墙).{0,70}(?:被)?(?:破坏|移除|打穿|拆除|打开)"
    r".{0,80}(?:暴露|露出|显露|通往|进入).{0,80}(?:空间|空洞|夹层|通道|入口|房间)|"
    r"\b(?:wall|boards?|partition)\b.{0,80}"
    r"\b(?:broken|destroyed|removed|opened|break\s+through)\b.{0,80}"
    r"\b(?:reveals?|exposes?|opens?\s+(?:into|onto)|leads?\s+to)\b.{0,80}"
    r"\b(?:space|cavity|passage|opening|room)\b)",
    re.IGNORECASE,
)


def _stable_fact_path(
    contract_id: str, discovery_source_id: str, destination_id: str
) -> str:
    payload = f"{contract_id}\x1f{discovery_source_id}\x1f{destination_id}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"source_scene_passages.{digest}.discovered"


def _stable_transition_id(
    contract_id: str, source_id: str, origin_id: str, destination_id: str
) -> str:
    payload = f"{contract_id}\x1f{source_id}\x1f{origin_id}\x1f{destination_id}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"source_scene_transition_{digest}"


def _scene_origin(operator: ActionOperator) -> str | None:
    values = {
        condition.value
        for condition in operator.preconditions
        if condition.path == "scene_id"
        and condition.operator == "eq"
        and isinstance(condition.value, str)
    }
    return next(iter(values)) if len(values) == 1 else None


def _matching_clauses(text: str, pattern: re.Pattern[str]) -> tuple[str, ...]:
    return tuple(
        clause.strip()
        for clause in _CLAUSE_SPLIT.split(text)
        if clause.strip() and pattern.search(clause)
    )


def _exposure_sources(
    destination: SourceLocationAuthority,
    *,
    source_texts: Mapping[str, str],
    source_section_paths: Mapping[str, tuple[str, ...]],
    catalog: tuple[SourceLocationAuthority, ...],
) -> tuple[str, ...]:
    return tuple(
        source_id
        for source_id, text in source_texts.items()
        if _matching_clauses(text, _DESTINATION_EXPOSURE)
        and (
            owner := source_location_from_ancestry(
                source_section_paths.get(source_id, ()), catalog
            )
        )
        is not None
        and owner.location.location_id == destination.location.location_id
    )


def _direct_discovery_fact(
    contract: ScenarioContract, *, source_id: str, origin_id: str
) -> str | None:
    """Reuse only the server-owned goal boundary of an exact-source action."""

    facts = {
        command.path
        for operator in contract.operators
        if _scene_origin(operator) == origin_id
        and any(ref.source_block_id == source_id for ref in operator.source_refs)
        for command in operator.success_commands
        if command.kind == "set_fact"
        and command.path == f"action_goals.{operator.operator_id}.achieved"
        and command.value is True
    }
    return next(iter(facts)) if len(facts) == 1 else None


def materialize_source_secret_passages(
    contract: ScenarioContract,
    *,
    source_refs: Mapping[str, SourceRef],
    source_texts: Mapping[str, str],
    source_section_paths: Mapping[str, tuple[str, ...]],
    source_scene_keys: Mapping[str, str] | None = None,
) -> ScenarioContract:
    """Add exact-source discovery/open chains without publishing destinations."""

    if not contract.locations:
        return contract
    catalog = source_location_catalog(
        contract,
        source_section_paths=source_section_paths,
        source_scene_keys=source_scene_keys,
    )
    generated: list[ActionOperator] = []
    existing_by_id = {item.operator_id: item for item in contract.operators}
    for source_id, text in source_texts.items():
        source_ref = source_refs.get(source_id)
        origin = source_location_from_ancestry(
            source_section_paths.get(source_id, ()), catalog
        )
        clauses = _matching_clauses(text, _HIDDEN_OPENING_DISCOVERY)
        if source_ref is None or origin is None or not clauses:
            continue
        mentioned = {
            item.location.location_id: item
            for clause in clauses
            for item in mentioned_source_locations(clause, catalog)
            if item.location.location_id != origin.location.location_id
            and same_immediate_source_container(origin, item)
        }
        supported = tuple(
            (item, evidence_ids)
            for item in mentioned.values()
            if (
                evidence_ids := _exposure_sources(
                    item,
                    source_texts=source_texts,
                    source_section_paths=source_section_paths,
                    catalog=catalog,
                )
            )
        )
        if len(supported) != 1:
            continue
        destination, exposure_ids = supported[0]
        fact_path = _direct_discovery_fact(
            contract,
            source_id=source_id,
            origin_id=origin.location.location_id,
        )
        if fact_path is None:
            if not any(_AUTOMATIC_OPENING_DISCOVERY.search(item) for item in clauses):
                continue
            fact_path = _stable_fact_path(
                contract.contract_id,
                source_id,
                destination.location.location_id,
            )
            discovery_id = _stable_transition_id(
                contract.contract_id,
                source_id,
                origin.location.location_id,
                "discovery",
            )
            discovery = ActionOperator(
                operator_id=discovery_id,
                title="检查可疑结构",
                intent_hints=("检查墙面", "检查木板"),
                public_setup="这里的结构可以进行一次简单检查。",
                policy="automatic",
                preconditions=(
                    StateCondition(
                        path="scene_id",
                        operator="eq",
                        value=origin.location.location_id,
                    ),
                    StateCondition(
                        path=f"facts.{fact_path}", operator="not_exists"
                    ),
                ),
                success_commands=(
                    WorldCommand(kind="set_fact", path=fact_path, value=True),
                ),
                rationale="Server-materialized source-explicit passage discovery.",
                maximum_effect="Records only that the hidden opening was discovered.",
                source_refs=(source_ref,),
            )
            existing = existing_by_id.get(discovery_id)
            if existing is not None and existing != discovery:
                raise ValueError(
                    "Source secret passage reserved id conflicts with an authored "
                    f"operator: {discovery_id}"
                )
            if existing is None:
                generated.append(discovery)
                existing_by_id[discovery_id] = discovery

        transition_id = _stable_transition_id(
            contract.contract_id,
            source_id,
            origin.location.location_id,
            destination.location.location_id,
        )
        transition = ActionOperator(
            operator_id=transition_id,
            title="打开已发现的通路",
            intent_hints=("打开通路", "移除墙板", "破坏墙体"),
            public_setup="这处结构可以按来源说明打开。",
            policy="automatic",
            preconditions=(
                StateCondition(
                    path="scene_id",
                    operator="eq",
                    value=origin.location.location_id,
                ),
                StateCondition(
                    path=f"facts.{fact_path}", operator="eq", value=True
                ),
            ),
            success_commands=(
                WorldCommand(
                    kind="set_scene", value=destination.location.location_id
                ),
            ),
            rationale="Server-materialized source-explicit discovered passage transition.",
            maximum_effect="Changes only the active public scene after discovery.",
            source_refs=(
                source_ref,
                *tuple(
                    source_refs[item]
                    for item in exposure_ids
                    if item in source_refs and item != source_id
                ),
            ),
        )
        existing = existing_by_id.get(transition_id)
        if existing is not None and existing != transition:
            raise ValueError(
                "Source secret passage reserved id conflicts with an authored "
                f"operator: {transition_id}"
            )
        if existing is None:
            generated.append(transition)
            existing_by_id[transition_id] = transition
    if not generated:
        return contract
    return contract.model_copy(
        update={"operators": (*contract.operators, *generated)}
    )


__all__ = ["materialize_source_secret_passages"]

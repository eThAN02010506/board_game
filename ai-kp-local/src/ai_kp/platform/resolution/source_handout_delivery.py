"""Bind explicit source handouts to the checked outcomes that deliver them."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping

from ai_kp.platform.resolution.contracts import (
    ClueSpec,
    OutcomeNarrativeCue,
    ScenarioContract,
    SourceRef,
    WorldCommand,
)

_HANDOUT_LABEL = re.compile(
    r"(?:文字材料|玩家资料|handout)\s*(?:no\.?\s*)?(\d+)", re.IGNORECASE
)
_SUCCESSFUL_DELIVERY = re.compile(
    r"(?:如果|若).{0,80}(?:检定)?成功.{0,100}(?:交给|给|出示)|"
    r"(?:on|after)\s+(?:a\s+)?successful.{0,100}(?:give|show|hand)",
    re.IGNORECASE,
)


def _canonical(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\s:：,，.。()（）\-_]+", "", normalized)


def _handout_number(value: str) -> str | None:
    match = _HANDOUT_LABEL.search(value)
    return match.group(1) if match is not None else None


def _stable_identity(
    contract_id: str, structural_key: str, number: str
) -> tuple[str, str]:
    digest = hashlib.sha256(
        f"{contract_id}\x1f{structural_key}\x1f{number}".encode()
    ).hexdigest()[:24]
    return f"source_handout_{digest}", f"source_handouts.{digest}.delivered"


def _common_prefix_length(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    length = 0
    for left_item, right_item in zip(left, right, strict=False):
        if left_item != right_item:
            break
        length += 1
    return length


def materialize_source_handout_delivery(
    contract: ScenarioContract,
    *,
    source_refs: Mapping[str, SourceRef],
    source_texts: Mapping[str, str],
    source_section_paths: Mapping[str, tuple[str, ...]],
) -> ScenarioContract:
    """Commit and disclose a handout only on its explicit successful check.

    The source must name a numbered handout and the success boundary in one
    instruction block. Handout prose is selected only from that instruction's
    exact structural scene and numbered child section. Existing checked
    operators must cite the instruction itself or an evidence block in the same
    option section. No model-authored fact path or public paraphrase is trusted.
    """

    if not contract.operators:
        return contract
    operators = list(contract.operators)
    clues = list(contract.clues)
    existing_clue_ids = {item.clue_id for item in clues}

    for instruction_id, instruction in source_texts.items():
        number = _handout_number(instruction)
        instruction_ref = source_refs.get(instruction_id)
        instruction_path = source_section_paths.get(instruction_id, ())
        if (
            number is None
            or instruction_ref is None
            or not instruction_path
            or not _SUCCESSFUL_DELIVERY.search(instruction)
        ):
            continue
        handout_parents = {
            path[:-1]
            for path in source_section_paths.values()
            if path and _handout_number(path[-1]) == number
        }
        ranked_parents = sorted(
            (
                (_common_prefix_length(instruction_path, parent), parent)
                for parent in handout_parents
            ),
            reverse=True,
        )
        if not ranked_parents or ranked_parents[0][0] == 0 or (
            len(ranked_parents) > 1
            and ranked_parents[0][0] == ranked_parents[1][0]
        ):
            continue
        scene_path = ranked_parents[0][1]
        content_items: list[tuple[str, str]] = []
        for source_id, path in source_section_paths.items():
            if len(path) != len(scene_path) + 1 or path[:-1] != scene_path:
                continue
            if _handout_number(path[-1]) != number:
                continue
            text = source_texts.get(source_id, "").strip()
            if not text or _canonical(text) == _canonical(path[-1]):
                continue
            content_items.append((source_id, text))
        if not content_items:
            continue
        content_items = list(dict.fromkeys(content_items))[:12]
        public_summary = "\n".join(text for _, text in content_items)[:1200]
        if not public_summary:
            continue

        direct = tuple(
            index
            for index, operator in enumerate(operators)
            if operator.skill_choices
            and any(ref.source_block_id == instruction_id for ref in operator.source_refs)
        )
        if direct:
            candidate_indexes = direct
        else:
            candidate_indexes = tuple(
                index
                for index, operator in enumerate(operators)
                if operator.skill_choices
                and any(
                    source_section_paths.get(ref.source_block_id) == instruction_path
                    for ref in operator.source_refs
                )
            )
        if not candidate_indexes:
            continue

        clue_id, fact_path = _stable_identity(
            contract.contract_id, "\x1f".join(scene_path), number
        )
        source_ids = tuple(dict.fromkeys((
            instruction_id,
            *(source_id for source_id, _ in content_items),
        )))
        refs = tuple(source_refs[source_id] for source_id in source_ids if source_id in source_refs)
        if len(refs) != len(source_ids):
            continue
        route_ids: list[str] = []
        for index in candidate_indexes:
            operator = operators[index]
            command = WorldCommand(kind="set_fact", path=fact_path, value=True)
            success_commands = operator.success_commands
            if command not in success_commands:
                success_commands = (*success_commands, command)
            cues = tuple(
                item for item in operator.narrative_cues if item.outcome_key != "success"
            )
            cue = OutcomeNarrativeCue(
                outcome_key="success", public_summary=public_summary
            )
            if len(cues) >= 8:
                continue
            cues = (*cues, cue)
            operators[index] = operator.model_copy(update={
                "public_setup": (
                    operator.public_setup or "该行动将按来源约定结算可公开信息。"
                ),
                "success_commands": success_commands,
                "narrative_cues": cues,
            })
            route_ids.append(operator.operator_id)
        if not route_ids:
            continue
        if clue_id in existing_clue_ids:
            clue_index = next(
                index for index, clue in enumerate(clues) if clue.clue_id == clue_id
            )
            existing_clue = clues[clue_index]
            clues[clue_index] = existing_clue.model_copy(update={
                "discovery_operator_ids": tuple(dict.fromkeys((
                    *existing_clue.discovery_operator_ids,
                    *route_ids,
                ))),
                "source_refs": tuple(dict.fromkeys((
                    *existing_clue.source_refs,
                    *refs,
                ))),
            })
            continue
        clues.append(
            ClueSpec(
                clue_id=clue_id,
                title=f"文字材料 {number}",
                importance="supporting",
                discovery_operator_ids=tuple(route_ids),
                fact_path=fact_path,
                fact_value=True,
                recoverable=True,
                public_content=(public_summary,),
                source_refs=refs,
            )
        )
        existing_clue_ids.add(clue_id)

    updated = contract.model_copy(update={
        "operators": tuple(operators),
        "clues": tuple(clues),
    })
    return updated if updated != contract else contract


__all__ = ["materialize_source_handout_delivery"]

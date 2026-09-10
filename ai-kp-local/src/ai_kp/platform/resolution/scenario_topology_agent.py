"""Context-bounded Agent for source-grounded scenario topology repair."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai_kp.platform.ports.llm import ChatMessage, LlmClient
from ai_kp.platform.resolution.contracts import ScenarioContract, SourceRef
from ai_kp.platform.resolution.scenario_scene_graph_supplement import (
    SceneGraphSourceContext,
    SceneGraphSupplementEnvelope,
    apply_scene_graph_envelope,
    evidence_asserts_initial_scene,
    opening_scene_section_key,
    scene_graph_location_catalog,
)
from ai_kp.platform.structured_json import StructuredJsonError, decode_json_object

_BATCH_BLOCK_LIMIT = 16
_BATCH_CHARACTER_LIMIT = 24_000
_PROMPT_CHARACTER_LIMIT = 48_000
_INITIAL_OPTION_LIMIT = 16
_INITIAL_EVIDENCE_TEXT_LIMIT = 1_200
_SCHEMA = json.dumps(
    SceneGraphSupplementEnvelope.model_json_schema(),
    ensure_ascii=False,
    separators=(",", ":"),
)


class _InitialSceneSelection(BaseModel):
    """One narrow Agent decision, isolated from route selection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    initial_scene_slot: int | None = Field(default=None, ge=0, le=255)
    source_block_ids: tuple[str, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def require_evidence(self) -> _InitialSceneSelection:
        if self.initial_scene_slot is None and self.source_block_ids:
            raise ValueError("Initial-scene evidence requires a location selection")
        if self.initial_scene_slot is not None and not self.source_block_ids:
            raise ValueError("An initial scene requires source evidence")
        return self


_INITIAL_SCHEMA = json.dumps(
    _InitialSceneSelection.model_json_schema(),
    ensure_ascii=False,
    separators=(",", ":"),
)


@dataclass(frozen=True)
class TopologyEvidence:
    source_block_id: str
    text: str
    source_ref: SourceRef
    descriptor: dict[str, Any]

    def source_context(self) -> SceneGraphSourceContext:
        raw_path = self.descriptor.get("section_path", ())
        section_path = (
            tuple(str(item) for item in raw_path)
            if isinstance(raw_path, (list, tuple))
            else ()
        )
        return SceneGraphSourceContext(
            title=str(self.descriptor.get("title", "")),
            section_path=section_path,
        )


@dataclass(frozen=True)
class TopologyRepairResult:
    contract: ScenarioContract
    assumptions: tuple[str, ...]


@dataclass(frozen=True)
class _InitialSceneOption:
    slot: int
    title: str
    source_block_ids: tuple[str, ...]
    evidence: tuple[dict[str, Any], ...]

    def descriptor(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "title": self.title,
            "source_block_ids": self.source_block_ids,
            "evidence": self.evidence,
        }


def _initial_scene_options(
    contract: ScenarioContract,
    evidence: tuple[TopologyEvidence, ...],
) -> tuple[_InitialSceneOption, ...]:
    """Precompute only slots that already pass the deterministic entry proof."""

    options: list[_InitialSceneOption] = []
    for slot, location in enumerate(contract.locations):
        supporting = tuple(
            item
            for item in evidence
            if evidence_asserts_initial_scene(
                location.title,
                (item.source_block_id,),
                source_refs={item.source_block_id: item.source_ref},
                source_texts={item.source_block_id: item.text},
                source_contexts={item.source_block_id: item.source_context()},
            )
        )
        if not supporting:
            continue
        selected = supporting[:1]
        options.append(_InitialSceneOption(
            slot=slot,
            title=location.title,
            source_block_ids=tuple(item.source_block_id for item in selected),
            evidence=tuple({
                "source_block_id": item.source_block_id,
                "title": item.source_context().title,
                "text": item.text[:_INITIAL_EVIDENCE_TEXT_LIMIT],
                "page": item.source_ref.page,
                "paragraph": item.source_ref.paragraph,
                "section_path": item.source_context().section_path,
            } for item in selected),
        ))
    # Prefer more specific lexical identities when the safety cap is reached;
    # the Agent still chooses among all retained, already-proven options.
    return tuple(sorted(
        options,
        key=lambda item: (-len(item.title.split()), -len(item.title), item.slot),
    )[:_INITIAL_OPTION_LIMIT])


def _evidence_batches(
    evidence: tuple[TopologyEvidence, ...],
    *,
    location_titles: tuple[str, ...],
    cited_source_ids: set[str],
    issue_source_ids: set[str],
    character_limit: int,
) -> tuple[tuple[TopologyEvidence, ...], ...]:
    """Keep only evidence that can prove an entry or a direct route."""

    normalized_titles = tuple(
        dict.fromkeys(title.strip().casefold() for title in location_titles if title.strip())
    )
    required_ids = cited_source_ids | issue_source_ids
    selected: list[tuple[TopologyEvidence, int]] = []
    for item in evidence:
        text = item.text.casefold()
        title_matches = sum(title in text for title in normalized_titles)
        opening_context = opening_scene_section_key(item.source_context()) is not None
        if (
            item.source_block_id not in required_ids
            and title_matches < 2
            and not opening_context
        ):
            continue
        size = len(
            json.dumps(item.descriptor, ensure_ascii=False, separators=(",", ":"))
        )
        if size <= character_limit:
            selected.append((item, size))

    batches: list[tuple[TopologyEvidence, ...]] = []
    current: list[TopologyEvidence] = []
    current_characters = 0
    for item, size in selected:
        if current and (
            len(current) >= _BATCH_BLOCK_LIMIT
            or current_characters + size > character_limit
        ):
            batches.append(tuple(current))
            current = []
            current_characters = 0
        current.append(item)
        current_characters += size
    if current:
        batches.append(tuple(current))
    return tuple(batches)


class ScenarioTopologyRepairAgent:
    """Select existing location slots without gaining world-writing authority."""

    def __init__(self, llm: LlmClient) -> None:
        self.llm = llm

    async def repair(
        self,
        contract: ScenarioContract,
        evidence: tuple[TopologyEvidence, ...],
        *,
        problem: str,
        issue_source_ids: set[str],
    ) -> TopologyRepairResult | None:
        if not contract.locations:
            return None
        try:
            catalog = scene_graph_location_catalog(contract)
        except (ValidationError, ValueError):
            return None
        catalog_json = json.dumps(
            [item.model_dump(mode="json") for item in catalog],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        fixed_characters = len(_SCHEMA) + len(problem[:6000]) + len(catalog_json) + 8_000
        evidence_character_limit = min(
            _BATCH_CHARACTER_LIMIT,
            _PROMPT_CHARACTER_LIMIT - fixed_characters,
        )
        if evidence_character_limit <= 0:
            return None
        batches = _evidence_batches(
            evidence,
            location_titles=tuple(item.title for item in contract.locations),
            cited_source_ids={
                source_id for item in catalog for source_id in item.source_block_ids
            },
            issue_source_ids=issue_source_ids,
            character_limit=evidence_character_limit,
        )
        if not batches:
            return None

        slots = {item.location_id: slot for slot, item in enumerate(contract.locations)}
        repaired = contract
        assumptions: list[str] = []
        if contract.initial_scene_id is None:
            initial_options = _initial_scene_options(contract, evidence)
            initial_selection = await self._select_initial_scene(
                initial_options,
                problem=problem,
            )
            if (
                initial_selection is not None
                and initial_selection.initial_scene_slot is not None
            ):
                selected_option = next(
                    (
                        option
                        for option in initial_options
                        if option.slot == initial_selection.initial_scene_slot
                    ),
                    None,
                )
                selected_ids = set(initial_selection.source_block_ids)
                if (
                    selected_option is not None
                    and selected_ids
                    and selected_ids.issubset(selected_option.source_block_ids)
                ):
                    initial_envelope = SceneGraphSupplementEnvelope(
                        initial_scene_slot=initial_selection.initial_scene_slot,
                        initial_scene_source_block_ids=(
                            initial_selection.source_block_ids
                        ),
                    )
                    try:
                        repaired = self._apply_envelope(
                            initial_envelope, repaired, evidence
                        )
                    except (ValidationError, ValueError):
                        pass
                    else:
                        assumptions.append(
                            "Source-asserted initial scene selected: "
                            + selected_option.title
                            + " ["
                            + ", ".join(initial_selection.source_block_ids)
                            + "]"
                        )
        for index, batch in enumerate(batches, start=1):
            rejection_feedback = ""
            for _attempt in range(2):
                envelope, decode_error = await self._select(
                    repaired,
                    batch,
                    catalog_json=catalog_json,
                    slots=slots,
                    problem=problem,
                    batch_index=index,
                    batch_count=len(batches),
                    rejection_feedback=rejection_feedback,
                )
                if envelope is None:
                    rejection_feedback = decode_error[:800]
                    if rejection_feedback:
                        continue
                    break
                if envelope.initial_scene_slot is not None:
                    envelope = envelope.model_copy(update={
                        "initial_scene_slot": None,
                        "initial_scene_source_block_ids": (),
                    })
                try:
                    next_contract = self._apply_envelope(
                        envelope, repaired, batch
                    )
                except (ValidationError, ValueError) as exc:
                    rejection_feedback = str(exc)[:800]
                    continue
                repaired = next_contract
                break
        if repaired == contract:
            return None
        return TopologyRepairResult(repaired, tuple(dict.fromkeys(assumptions)))

    @staticmethod
    def _apply_envelope(
        envelope: SceneGraphSupplementEnvelope,
        contract: ScenarioContract,
        evidence: tuple[TopologyEvidence, ...],
    ) -> ScenarioContract:
        return apply_scene_graph_envelope(
            envelope,
            contract=contract,
            source_refs={item.source_block_id: item.source_ref for item in evidence},
            source_texts={item.source_block_id: item.text for item in evidence},
            source_contexts={
                item.source_block_id: item.source_context() for item in evidence
            },
        )

    async def _select_initial_scene(
        self,
        options: tuple[_InitialSceneOption, ...],
        *,
        problem: str,
    ) -> _InitialSceneSelection | None:
        if not options:
            return None
        messages = [
            ChatMessage(role="system", content=(
                "只返回 JSON。你是开场地点选择 Agent，仅选择玩家实际开始游戏时所在的一个"
                "既有 location slot；不选择路线、不写剧情、不生成地点。必须引用直接证明玩家"
                "当前位置的 source_block_ids。Opening/Starting Scene 章节中描述玩家已聚集、"
                "居住、站立或身处某地点的正文是直接证明。调查目标、想去的地点、秘密地点和"
                "背景事件都不是开场。地点标题允许 X's Room / room of X 这类所有格语序变化，"
                "但所有标题实词必须出现在证明正文中。候选均已通过程序词法校验；若多个候选"
                "成立，选择故事实际开始行动时最具体的位置，而不是更宽泛的建筑或居住地。"
                "只能引用所选候选自带的 source_block_ids。证据不足时返回 null 和空数组。"
            )),
            ChatMessage(role="user", content=(
                "响应 JSON Schema：" + _INITIAL_SCHEMA
                + "\n编译器问题：" + problem[:3000]
                + "\n已通过程序验证的开场候选：" + json.dumps(
                    [option.descriptor() for option in options],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )),
        ]
        try:
            raw = await self.llm.complete(messages, temperature=0.0)
            return _InitialSceneSelection.model_validate(decode_json_object(raw))
        except (StructuredJsonError, ValidationError, ValueError):
            return None

    async def _select(
        self,
        contract: ScenarioContract,
        evidence: tuple[TopologyEvidence, ...],
        *,
        catalog_json: str,
        slots: dict[str, int],
        problem: str,
        batch_index: int,
        batch_count: int,
        rejection_feedback: str = "",
    ) -> tuple[SceneGraphSupplementEnvelope | None, str]:
        existing_links = [
            {
                "from_slot": slots[item.from_location_id],
                "to_slot": slots[item.to_location_id],
                "one_way": item.one_way,
            }
            for item in contract.location_links
        ]
        messages = [
            ChatMessage(role="system", content=(
                "只返回 JSON。你是场景路线修复 Agent，不写剧情，也不生成地点。只能从服务器"
                "给出的 location slot 选择来源明确写出的地点间可移动关系；每个选择必须引用"
                "直接支持该关系的 source_block_ids。initial_scene_slot 必须为 null。章节顺序、"
                "地点同时出现、常识"
                "上的距离、建议调查地点，均不等于两地点直接相连。来源只说‘可去某处’但没有说明"
                "从哪里出发时，不得猜 from_slot。秘密地点、尚未发现的入口或有条件路线不得补成"
                "link；只有来源明确说明玩家此时知道且可直接移动的无条件路线才能选择。不得补写"
                "门锁、线索门槛或其他 precondition。source_block_ids 只能逐字复制本批证据目录"
                "中存在的 ID，禁止概括、拼接或改写 ID。只返回缺失路线，不得重复既有连接；"
                "one_way=false 已表示双向连接，不得再返回反向项。证据不足时 links 留空。"
            )),
            ChatMessage(role="user", content=(
                "响应 JSON Schema：" + _SCHEMA
                + "\n编译器问题：" + problem[:6000]
                + f"\n证据批次：{batch_index}/{batch_count}。"
                + "初始场景由独立窄 Agent 处理；initial_scene_slot 必须为 null。"
                + "\nlocation slot 目录：" + catalog_json
                + "\n既有连接：" + json.dumps(
                    existing_links, ensure_ascii=False, separators=(",", ":")
                )
                + "\n本批证据目录：" + json.dumps(
                    [item.descriptor for item in evidence],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + (
                    "\n上次响应被服务器拒绝："
                    + rejection_feedback
                    + "。请返回完整的新 JSON；不要沿用无效选择。若无法严格满足约束，"
                    "返回 links 为空数组。"
                    if rejection_feedback
                    else ""
                )
            )),
        ]
        try:
            raw = await self.llm.complete(messages, temperature=0.0)
            return (
                SceneGraphSupplementEnvelope.model_validate(
                    decode_json_object(raw)
                ),
                "",
            )
        except (StructuredJsonError, ValidationError, ValueError) as exc:
            return None, str(exc)


__all__ = ["ScenarioTopologyRepairAgent", "TopologyEvidence", "TopologyRepairResult"]

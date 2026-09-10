"""Narrow authoring collaborator for source-explicit terminal methods."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from ai_kp.platform.ports.llm import ChatMessage, LlmClient
from ai_kp.platform.resolution.scenario_ir_models import ScenarioIrBatch
from ai_kp.platform.resolution.scenario_terminal_method import (
    SourceTerminalMethod,
    TerminalEntityCandidate,
    TerminalMethodEnvelope,
    coverage_terminal_method_schema_json,
    extract_source_terminal_methods,
    materialize_terminal_method_envelope,
    materialize_terminal_method_fallback,
)
from ai_kp.platform.resolution.source_coverage import SourceCoverageSupplementTarget
from ai_kp.platform.structured_json import StructuredJsonError, decode_json_object


class TerminalMethodEvidence(Protocol):
    source_block_id: str
    text: str

    def public_descriptor(self) -> dict[str, object]: ...


@dataclass(frozen=True, slots=True)
class TerminalMethodAuthoringOutcome:
    batch: ScenarioIrBatch | None
    attempt_count: int
    validation_errors: tuple[str, ...] = ()


class TerminalMethodSupplementAuthor:
    """Own only the bounded terminal-method slot workflow."""

    def __init__(self, llm: LlmClient) -> None:
        self.llm = llm

    async def author(
        self,
        evidence: tuple[TerminalMethodEvidence, ...],
        *,
        ruleset_id: str,
        partition_index: int,
        max_attempts: int,
        coverage_targets: tuple[SourceCoverageSupplementTarget, ...],
        record_id_prefix: str,
        entity_candidates: tuple[TerminalEntityCandidate, ...],
        validate_batch_source_ids: Callable[[ScenarioIrBatch], None],
        batch_gate_errors: Callable[[ScenarioIrBatch], tuple[str, ...]],
    ) -> TerminalMethodAuthoringOutcome:
        target_ids = {item.source_block_id for item in coverage_targets}
        if len(target_ids) != 1:
            return TerminalMethodAuthoringOutcome(
                batch=None,
                attempt_count=1,
                validation_errors=(
                    f"partition {partition_index}: terminal method tool requires one target source",
                ),
            )
        source_block_id = next(iter(target_ids))
        source_evidence = next(
            (item for item in evidence if item.source_block_id == source_block_id),
            None,
        )
        if source_evidence is None:
            return TerminalMethodAuthoringOutcome(
                batch=None,
                attempt_count=1,
                validation_errors=(
                    f"partition {partition_index}: terminal method source is outside its evidence window",
                ),
            )
        methods = extract_source_terminal_methods(source_evidence.text)
        required_count = max(
            target.required_additional_count for target in coverage_targets
        )
        if len(methods) < required_count:
            return TerminalMethodAuthoringOutcome(
                batch=None,
                attempt_count=1,
                validation_errors=(
                    f"partition {partition_index}: terminal source catalog cannot satisfy target",
                ),
            )
        if not entity_candidates:
            try:
                batch = materialize_terminal_method_fallback(
                    methods=methods[:required_count],
                    source_block_id=source_block_id,
                    record_id_prefix=record_id_prefix,
                )
                validate_batch_source_ids(batch)
                gate_errors = batch_gate_errors(batch)
                if gate_errors:
                    raise ValueError("; ".join(gate_errors))
                return TerminalMethodAuthoringOutcome(batch=batch, attempt_count=1)
            except (ValidationError, ValueError) as exc:
                return TerminalMethodAuthoringOutcome(
                    batch=None,
                    attempt_count=1,
                    validation_errors=(
                        f"partition {partition_index}: {str(exc)[:1200]}",
                    ),
                )
        errors: list[str] = []
        for attempt in range(1, max_attempts + 1):
            raw = await self.llm.complete(
                self.messages(
                    evidence,
                    ruleset_id=ruleset_id,
                    partition_index=partition_index,
                    targets=coverage_targets,
                    methods=methods,
                    entity_candidates=entity_candidates,
                    errors=errors,
                ),
                temperature=0.1,
            )
            try:
                envelope = TerminalMethodEnvelope.model_validate(
                    decode_json_object(raw)
                )
                batch = materialize_terminal_method_envelope(
                    envelope,
                    methods=methods,
                    entity_candidates=entity_candidates,
                    source_block_id=source_block_id,
                    record_id_prefix=record_id_prefix,
                )
                validate_batch_source_ids(batch)
                gate_errors = batch_gate_errors(batch)
                if gate_errors:
                    raise ValueError("; ".join(gate_errors))
                return TerminalMethodAuthoringOutcome(
                    batch=batch,
                    attempt_count=attempt,
                    validation_errors=tuple(errors),
                )
            except (StructuredJsonError, ValidationError, ValueError) as exc:
                errors.append(f"partition {partition_index}: {str(exc)[:1200]}")
        return TerminalMethodAuthoringOutcome(
            batch=None,
            attempt_count=max_attempts,
            validation_errors=tuple(errors),
        )

    @staticmethod
    def messages(
        evidence: tuple[TerminalMethodEvidence, ...],
        *,
        ruleset_id: str,
        partition_index: int,
        targets: tuple[SourceCoverageSupplementTarget, ...],
        methods: tuple[SourceTerminalMethod, ...],
        entity_candidates: tuple[TerminalEntityCandidate, ...],
        errors: list[str],
    ) -> list[ChatMessage]:
        correction = f"\n上次槽位输出未通过校验：{errors[-1]}" if errors else ""
        return [
            ChatMessage(
                role="system",
                content=(
                    "只返回 JSON。你只能把服务器从来源解析出的终结手段槽位链接到"
                    "服务器给出的既有实体槽位。不得输出或修改标题、ID、状态、地点、"
                    "检定、前置条件、命令或来源引用；服务器会确定性生成全部权威字段。"
                    "实体必须是来源结果中被终结的对象；目录不足或含义不确定时让输出"
                    "校验失败，不得猜测目录外实体。"
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"规则系统：{ruleset_id}\n补写分区序号：{partition_index}\n"
                    "终结手段槽位 JSON Schema："
                    + coverage_terminal_method_schema_json()
                    + "\n补写目标："
                    + json.dumps(
                        [item.model_dump(mode="json") for item in targets],
                        ensure_ascii=False,
                    )
                    + "\n来源解析手段目录："
                    + json.dumps(
                        [item.model_dump(mode="json") for item in methods],
                        ensure_ascii=False,
                    )
                    + "\n允许的既有实体目录："
                    + json.dumps(
                        [item.model_dump(mode="json") for item in entity_candidates],
                        ensure_ascii=False,
                    )
                    + "\n证据目录："
                    + json.dumps(
                        [item.public_descriptor() for item in evidence],
                        ensure_ascii=False,
                    )
                    + correction
                ),
            ),
        ]


__all__ = [
    "TerminalMethodAuthoringOutcome",
    "TerminalMethodEvidence",
    "TerminalMethodSupplementAuthor",
]

"""Independent, narrow output verification for weak-model Agent pipelines."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from ai_kp.platform.ports.llm import ChatMessage, LlmClient
from ai_kp.platform.structured_json import StructuredJsonError, decode_json_object


class AgentVerificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    accepted: StrictBool
    missing: tuple[str, ...] = Field(default=(), max_length=24)
    contradictions: tuple[str, ...] = Field(default=(), max_length=24)
    patch_instruction: str = Field(default="", max_length=1200)
    source: Literal["deterministic", "model", "fallback"] = "deterministic"


class ConstrainedOutputVerifier:
    """Verify obligations separately from generation; rejection grants no authority."""

    def __init__(self, llm: LlmClient):
        self.llm = llm

    async def review(
        self,
        *,
        public_narration: str,
        required_content: tuple[str, ...],
        forbidden_disclosures: tuple[str, ...],
        authority_summary: dict[str, Any],
    ) -> AgentVerificationReport:
        deterministic = self.deterministic_review(
            public_narration=public_narration,
            required_content=required_content,
            forbidden_disclosures=forbidden_disclosures,
        )
        if not deterministic.accepted:
            return deterministic
        try:
            raw = await self.llm.complete(
                [
                    ChatMessage(
                        role="system",
                        content=(
                            "你是独立的叙事审查 Agent，只返回 JSON。检查草稿是否遗漏明确"
                            "义务、泄露禁止内容、把推测写成事实，或声称权威摘要之外的规则"
                            "结果。不能润色、补写或改变任何状态。"
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            "权威摘要："
                            + json.dumps(authority_summary, ensure_ascii=False)
                            + "\n必须覆盖："
                            + json.dumps(required_content, ensure_ascii=False)
                            + "\n禁止披露："
                            + json.dumps(forbidden_disclosures, ensure_ascii=False)
                            + f"\n待审草稿：{public_narration}\n"
                            "返回 {accepted,missing,contradictions,patch_instruction}。"
                        ),
                    ),
                ],
                temperature=0.0,
            )
            payload = decode_json_object(raw)
            if "source" in payload:
                raise ValueError("Verifier output cannot set server-owned source")
            report = AgentVerificationReport.model_validate({**payload, "source": "model"})
            if report.accepted and (
                report.missing or report.contradictions or report.patch_instruction
            ):
                raise ValueError("Accepted verifier output cannot contain issues")
            return report
        except (StructuredJsonError, ValidationError, ValueError) as exc:
            return AgentVerificationReport(
                accepted=False,
                missing=deterministic.missing,
                contradictions=deterministic.contradictions,
                patch_instruction=(
                    "独立审查器未返回可验证的严格结果；不得采用模型叙事，"
                    "应退回确定性叙事。"
                    f" ({type(exc).__name__})"
                ),
                source="fallback",
            )

    @staticmethod
    def deterministic_review(
        *,
        public_narration: str,
        required_content: tuple[str, ...],
        forbidden_disclosures: tuple[str, ...],
    ) -> AgentVerificationReport:
        missing = tuple(item for item in required_content if item not in public_narration)
        disclosed = tuple(
            item
            for item in forbidden_disclosures
            if item and item in public_narration and item not in required_content
        )
        accepted = not missing and not disclosed
        issues = (*missing, *disclosed)
        return AgentVerificationReport(
            accepted=accepted,
            missing=missing,
            contradictions=tuple(f"禁止披露：{item}" for item in disclosed),
            patch_instruction=(
                "只补入缺失义务并删除禁止披露内容；不得重写既有行动和规则结果："
                + "；".join(issues)
                if issues
                else ""
            ),
        )


__all__ = ["AgentVerificationReport", "ConstrainedOutputVerifier"]

"""Constrained presentation over an immutable kernel preview."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_kp.platform.agents import (
    ConstrainedOutputVerifier,
    EntityActingFrame,
    EntityAgentContextBuilder,
)
from ai_kp.platform.ports.llm import ChatMessage, LlmClient
from ai_kp.platform.resolution.contracts import (
    ResolutionPreview,
    ScenarioContract,
    ScenarioSnapshot,
)
from ai_kp.platform.resolution.narrative_safety import narration_claims_success
from ai_kp.platform.structured_json import decode_json_object

_NUMBER = re.compile(r"(?<![\w])[-+]?\d+(?:\.\d+)?")


class KernelOutcomeNarration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    outcome_key: str = Field(min_length=1, max_length=120)
    public_narration: str = Field(min_length=1, max_length=1600)
    speaker_entity_id: str | None = Field(default=None, max_length=160)


class KernelNarrativeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    basis_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    preview_narration: str = Field(min_length=1, max_length=1200)
    outcomes: tuple[KernelOutcomeNarration, ...] = Field(max_length=8)


class KernelNarrativeBundle(KernelNarrativeDraft):
    source: Literal["deterministic", "model"]
    attempt_count: int = Field(ge=0, le=2)
    validation_errors: tuple[str, ...] = ()

    def narration_for(self, outcome_key: str) -> str:
        match = next(
            (item for item in self.outcomes if item.outcome_key == outcome_key),
            None,
        )
        if match is not None:
            return match.public_narration
        if outcome_key == "failure":
            return "行动已经结算，但没有达成预期目标；世界状态仅按规则结果变化。"
        return "行动已经按规则内核的已验证结果结算。"


class ConstrainedKernelNarrativeAdapter:
    """Let a model realize public cues without receiving commands or secret state."""

    def __init__(self, llm: LlmClient):
        self.llm = llm

    async def create(
        self,
        contract: ScenarioContract,
        preview: ResolutionPreview,
        player_action: str,
        snapshot: ScenarioSnapshot | None = None,
    ) -> KernelNarrativeBundle:
        frame = self._frame(contract, preview, player_action, snapshot=snapshot)
        fallback = deterministic_kernel_narrative(
            contract, preview, player_action, snapshot=snapshot
        )
        if not frame["outcomes"]:
            return fallback
        errors: list[str] = []
        for attempt in range(1, 3):
            raw = await self.llm.complete(
                self._messages(frame, errors), temperature=0.3
            )
            try:
                draft = KernelNarrativeDraft.model_validate(decode_json_object(raw))
                self._validate(draft, frame)
                verification = await self._verify_agent_output(draft, frame)
                if not verification.accepted:
                    raise ValueError(
                        verification.patch_instruction
                        or "Independent verifier rejected the actor narration"
                    )
                return KernelNarrativeBundle(
                    **draft.model_dump(mode="python"),
                    source="model",
                    attempt_count=attempt,
                    validation_errors=tuple(errors),
                )
            except (ValidationError, ValueError) as exc:
                errors.append(str(exc)[:600])
        return fallback.model_copy(
            update={"attempt_count": 2, "validation_errors": tuple(errors)}
        )

    @staticmethod
    def _frame(
        contract: ScenarioContract,
        preview: ResolutionPreview,
        player_action: str,
        *,
        snapshot: ScenarioSnapshot | None = None,
    ) -> dict:
        operator = next(
            item for item in contract.operators if item.operator_id == preview.operator_id
        )
        entity_titles = {item.entity_id: item.title for item in contract.entities}
        active_snapshot = snapshot or contract.initial_snapshot(preview.run_id)
        context_builder = EntityAgentContextBuilder()
        outcomes = []
        for cue in operator.narrative_cues:
            acting = (
                context_builder.build(
                    contract,
                    active_snapshot,
                    preview,
                    entity_id=cue.speaker_entity_id,
                    player_action=player_action,
                )
                if cue.speaker_entity_id
                else None
            )
            outcomes.append({
                "outcome_key": cue.outcome_key,
                "public_summary": cue.public_summary,
                "speaker_entity_id": cue.speaker_entity_id,
                "speaker_title": entity_titles.get(cue.speaker_entity_id or ""),
                "tone": cue.tone,
                "actor_context": (
                    {
                        "canonical_summary": acting.canonical_profile.summary,
                        "known_facts": acting.canonical_profile.known_facts,
                        "behavioral_directives": (
                            acting.canonical_profile.behavioral_directives
                        ),
                        "derived_profile": acting.derived_profile.model_dump(mode="json"),
                        "runtime_state": acting.runtime_state.model_dump(mode="json"),
                    }
                    if acting is not None
                    else None
                ),
                "required_content": tuple(dict.fromkeys((
                    *(acting.required_public_content if acting is not None else ()),
                    *(
                        preview.automatic_information
                        if cue.outcome_key == "success"
                        else ()
                    ),
                ))),
                "forbidden_disclosures": (
                    acting.forbidden_disclosures if acting is not None else ()
                ),
                "requires_verification": (
                    ConstrainedKernelNarrativeAdapter._requires_actor_verification(
                        acting
                    )
                    if acting is not None
                    else False
                ),
            })
        if preview.automatic_information and not any(
            item["outcome_key"] == "success" for item in outcomes
        ):
            outcomes.append({
                "outcome_key": "success",
                "public_summary": "行动成功，并确认了以下信息。",
                "speaker_entity_id": None,
                "speaker_title": None,
                "tone": "",
                "actor_context": None,
                "required_content": preview.automatic_information,
                "forbidden_disclosures": (),
                "requires_verification": False,
            })
        public_basis = {
            "preview_hash": preview.preview_hash,
            "player_action": player_action,
            "public_setup": operator.public_setup,
            "outcomes": outcomes,
        }
        encoded = json.dumps(
            public_basis,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {**public_basis, "basis_hash": hashlib.sha256(encoded).hexdigest()}

    @staticmethod
    def _requires_actor_verification(acting: EntityActingFrame) -> bool:
        canonical = acting.canonical_profile
        return bool(
            acting.obligations
            or acting.forbidden_disclosures
            or canonical.summary
            or canonical.known_facts
            or canonical.behavioral_directives
            or any(acting.derived_profile.model_dump(mode="python").values())
        )

    @staticmethod
    def _messages(frame: dict, errors: list[str]) -> list[ChatMessage]:
        correction = f"\n上次输出未通过校验：{errors[-1]}" if errors else ""
        generation_frame = {
            **frame,
            "outcomes": [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"forbidden_disclosures", "requires_verification"}
                }
                for item in frame["outcomes"]
            ],
        }
        return [
            ChatMessage(
                role="system",
                content=(
                    "只返回 JSON。你只能润色给定的玩家可见 setup 与 outcome summary；"
                    "不得添加地点、物品、未建立的关系、权限、伤害、骰值、事实或结局，不得"
                    "改变 outcome_key、speaker_entity_id 或 basis_hash。preview_narration "
                    "只能描述行动正在等待玩家确认，不能提前声称成功。NPC 台词只能由"
                    "对应 speaker 的临时 Actor Agent 表演，并受 actor_context 约束。不得猜测"
                    "或披露未提供的秘密。preview 必须原样包含 public_setup，每项结果必须"
                    "原样包含对应 public_summary 和 required_content 的每一项；不能用‘已经"
                    "解释/讲述/点头’代替具体义务内容。"
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"公开叙事依据：{json.dumps(generation_frame, ensure_ascii=False)}\n"
                    "返回 {basis_hash,preview_narration,outcomes:[{outcome_key,"
                    "public_narration,speaker_entity_id}]}。outcomes 必须逐项且仅覆盖输入。"
                    + correction
                ),
            ),
        ]

    @staticmethod
    def _validate(draft: KernelNarrativeDraft, frame: dict) -> None:
        if draft.basis_hash != frame["basis_hash"]:
            raise ValueError("Narrative basis hash does not match the kernel preview")
        expected = {
            item["outcome_key"]: item for item in frame["outcomes"]
        }
        actual = {item.outcome_key: item for item in draft.outcomes}
        if len(actual) != len(draft.outcomes) or set(actual) != set(expected):
            raise ValueError("Narrative outcomes must exactly match public cue keys")
        if narration_claims_success(draft.preview_narration):
            raise ValueError("Preview narration cannot claim a completed success")
        ConstrainedKernelNarrativeAdapter._validate_grounded(
            draft.preview_narration,
            str(frame["public_setup"]),
        )
        ConstrainedKernelNarrativeAdapter._validate_numbers(
            draft.preview_narration,
            str(frame["public_setup"]),
        )
        for key, output in actual.items():
            source = expected[key]
            if output.speaker_entity_id != source["speaker_entity_id"]:
                raise ValueError("Narrative speaker does not match the public cue")
            if key == "failure" and narration_claims_success(
                output.public_narration
            ):
                raise ValueError("Failure narration cannot claim success")
            ConstrainedKernelNarrativeAdapter._validate_grounded(
                output.public_narration,
                str(source["public_summary"]),
                supporting=" ".join(
                    str(value) for value in source.get("required_content") or ()
                ),
            )
            ConstrainedKernelNarrativeAdapter._validate_numbers(
                output.public_narration,
                " ".join(
                    str(value or "")
                    for value in (
                        source["public_summary"],
                        source["speaker_title"],
                        source["tone"],
                        " ".join(
                            str(value)
                            for value in source.get("required_content") or ()
                        ),
                    )
                ),
            )
            for required in source.get("required_content") or ():
                if str(required) not in output.public_narration:
                    raise ValueError(
                        f"Actor narration omitted required content: {required}"
                    )
            for forbidden in source.get("forbidden_disclosures") or ():
                if (
                    str(forbidden)
                    and str(forbidden) in output.public_narration
                    and str(forbidden) not in (source.get("required_content") or ())
                ):
                    raise ValueError("Actor narration disclosed forbidden entity content")

    async def _verify_agent_output(
        self, draft: KernelNarrativeDraft, frame: dict
    ):
        actor_outcomes = [
            item for item in frame["outcomes"] if item.get("requires_verification")
        ]
        if not actor_outcomes:
            from ai_kp.platform.agents import AgentVerificationReport

            return AgentVerificationReport(accepted=True)
        required = tuple(
            dict.fromkeys(
                str(value)
                for item in actor_outcomes
                for value in item.get("required_content") or ()
            )
        )
        forbidden = tuple(
            dict.fromkeys(
                str(value)
                for item in actor_outcomes
                for value in item.get("forbidden_disclosures") or ()
            )
        )
        narration = "\n".join(item.public_narration for item in draft.outcomes)
        return await ConstrainedOutputVerifier(self.llm).review(
            public_narration=narration,
            required_content=required,
            forbidden_disclosures=forbidden,
            authority_summary={
                "preview_hash": frame["preview_hash"],
                "public_setup": frame["public_setup"],
                "outcomes": [
                    {
                        "outcome_key": item["outcome_key"],
                        "public_summary": item["public_summary"],
                        "actor_context": item["actor_context"],
                    }
                    for item in actor_outcomes
                ],
            },
        )

    @staticmethod
    def _validate_numbers(output: str, source: str) -> None:
        allowed = set(_NUMBER.findall(source))
        introduced = set(_NUMBER.findall(output)) - allowed
        if introduced:
            raise ValueError(
                "Narration introduced unsupported numeric claims: "
                + ", ".join(sorted(introduced))
            )

    @staticmethod
    def _validate_grounded(output: str, source: str, *, supporting: str = "") -> None:
        grounding = source.strip()
        if grounding not in output:
            raise ValueError("Narration must preserve its public grounding verbatim")
        allowance = max(120, len(grounding) + len(supporting) * 2)
        if len(output) > len(grounding) + allowance:
            raise ValueError("Narration exceeds its bounded public grounding envelope")


def deterministic_kernel_narrative(
    contract: ScenarioContract,
    preview: ResolutionPreview,
    player_action: str,
    *,
    snapshot: ScenarioSnapshot | None = None,
) -> KernelNarrativeBundle:
    frame = ConstrainedKernelNarrativeAdapter._frame(
        contract, preview, player_action, snapshot=snapshot
    )
    setup = str(frame["public_setup"]).strip()
    return KernelNarrativeBundle(
        basis_hash=str(frame["basis_hash"]),
        preview_narration=(
            setup
            or "行动已由规则内核解析；确认裁定后才会结算并公开结果。"
        ),
        outcomes=tuple(
            KernelOutcomeNarration(
                outcome_key=str(item["outcome_key"]),
                public_narration="；".join(
                    dict.fromkeys(
                        (
                            str(item["public_summary"]),
                            *(str(value) for value in item.get("required_content") or ()),
                        )
                    )
                ),
                speaker_entity_id=item["speaker_entity_id"],
            )
            for item in frame["outcomes"]
        ),
        source="deterministic",
        attempt_count=0,
    )


__all__ = [
    "ConstrainedKernelNarrativeAdapter",
    "KernelNarrativeBundle",
    "KernelNarrativeDraft",
    "KernelOutcomeNarration",
    "deterministic_kernel_narrative",
]

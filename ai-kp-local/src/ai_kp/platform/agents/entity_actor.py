"""One-entity-at-a-time Actor Agent for bounded tabletop dialogue."""

from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_kp.platform.agents.entity_context import EntityActingFrame
from ai_kp.platform.agents.verifier import ConstrainedOutputVerifier
from ai_kp.platform.ports.llm import ChatMessage, LlmClient
from ai_kp.platform.structured_json import decode_json_object

_NUMBER = re.compile(r"(?<![\w])[-+]?\d+(?:\.\d+)?")
_MAX_ACTOR_NARRATION_CHARS = 12_000
_MAX_ACTOR_ATTEMPTS = 2

ActorExecutionErrorCode = Literal[
    "actor_provider_unavailable",
    "actor_output_rejected",
    "actor_required_content_missing",
    "actor_verifier_rejected",
    "actor_turn_deadline_exhausted",
    "actor_model_budget_exhausted",
]


class ActorExecutionTrace(BaseModel):
    """Bounded, player-safe observability for one isolated Actor invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal["entity-actor-trace.v1"] = "entity-actor-trace.v1"
    entity_id: str = Field(min_length=1, max_length=160)
    entity_title: str = Field(min_length=1, max_length=240)
    execution: Literal["model", "deterministic_fallback"]
    generation_attempt_count: int = Field(ge=0, le=_MAX_ACTOR_ATTEMPTS)
    error_codes: tuple[ActorExecutionErrorCode, ...] = Field(default=(), max_length=6)


class EntityActorResponse(BaseModel):
    """A public response owned by exactly one server-selected entity."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    entity_id: str = Field(min_length=1, max_length=160)
    public_narration: str = Field(min_length=1, max_length=_MAX_ACTOR_NARRATION_CHARS)
    source: Literal["model", "deterministic"]
    attempt_count: int = Field(default=0, ge=0, le=_MAX_ACTOR_ATTEMPTS)
    validation_errors: tuple[ActorExecutionErrorCode, ...] = ()


class _EntityActorDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    basis_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    public_narration: str = Field(min_length=1, max_length=_MAX_ACTOR_NARRATION_CHARS)
    speaker_entity_ids: tuple[str, ...] = Field(min_length=1, max_length=1)


class SingleEntityActorAdapter:
    """Generate and verify one NPC without exposing any other NPC's private context."""

    def __init__(self, llm: LlmClient):
        self.llm = llm

    async def create(
        self,
        *,
        actor: EntityActingFrame,
        visible_state: dict[str, Any],
        player_text: str,
        dialogue: str,
        use_model: bool = True,
    ) -> EntityActorResponse:
        basis = self._safe_projection(
            actor=actor,
            visible_state=visible_state,
            player_text=player_text,
            dialogue=dialogue,
        )
        fallback = self._fallback(actor)
        if not use_model:
            return fallback

        errors: list[ActorExecutionErrorCode] = []
        for attempt in range(1, _MAX_ACTOR_ATTEMPTS + 1):
            try:
                raw = await self.llm.complete(self._messages(basis), temperature=0.3)
                payload = decode_json_object(raw)
                # Identity and authority bindings are always supplied by the server.
                payload = {**payload, "basis_hash": basis["basis_hash"]}
                draft = _EntityActorDraft.model_validate(payload)
                self._validate(draft, basis, require_complete=False)
                missing = [
                    item for item in basis["required_content"] if item not in draft.public_narration
                ]
                if missing:
                    return fallback.model_copy(
                        update={
                            "attempt_count": attempt,
                            "validation_errors": ("actor_required_content_missing",),
                        }
                    )
                self._validate(draft, basis)
                report = await ConstrainedOutputVerifier(self.llm).review(
                    public_narration=draft.public_narration,
                    required_content=tuple(basis["required_content"]),
                    forbidden_disclosures=tuple(basis["private_boundaries"]),
                    authority_summary={
                        "route": "roleplay",
                        "visible_state": visible_state,
                        "actor": basis["actor_public_context"],
                    },
                )
                if not report.accepted:
                    return fallback.model_copy(
                        update={
                            "attempt_count": attempt,
                            "validation_errors": ("actor_verifier_rejected",),
                        }
                    )
                return EntityActorResponse(
                    entity_id=actor.entity_id,
                    public_narration=draft.public_narration,
                    source="model",
                    attempt_count=attempt,
                    validation_errors=tuple(errors),
                )
            # Provider/network/model failures are scoped to this one Actor. Never
            # persist raw provider or verifier text: it may echo the private prompt.
            # Cancellation still propagates because asyncio cancellation is a
            # BaseException on supported Python versions.
            except Exception as exc:  # noqa: BLE001 - isolate one provider-backed Actor
                errors.append(_public_error_code(exc))
        return fallback.model_copy(
            update={
                "attempt_count": _MAX_ACTOR_ATTEMPTS,
                "validation_errors": tuple(errors),
            }
        )

    @staticmethod
    def _safe_projection(
        *,
        actor: EntityActingFrame,
        visible_state: dict[str, Any],
        player_text: str,
        dialogue: str,
    ) -> dict[str, Any]:
        """Project exactly one actor; private material informs refusal but is never public."""

        public_context = {
            "entity_id": actor.entity_id,
            "title": actor.title,
            "canonical_summary": actor.canonical_profile.summary,
            "known_facts": actor.canonical_profile.known_facts,
            "behavioral_directives": actor.canonical_profile.behavioral_directives,
            "derived_profile": actor.derived_profile.model_dump(mode="json"),
            "runtime_state": actor.runtime_state.model_dump(mode="json"),
        }
        payload: dict[str, Any] = {
            "route": "roleplay",
            "player_text": player_text,
            "dialogue": dialogue,
            "visible_state": visible_state,
            "actor_public_context": public_context,
            # Only this actor's private boundary is present. It may guide a refusal,
            # but deterministic validation forbids copying it into public narration.
            "private_boundaries": actor.forbidden_disclosures,
            "required_content": actor.required_public_content,
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return {**payload, "basis_hash": hashlib.sha256(encoded).hexdigest()}

    @staticmethod
    def _messages(basis: dict[str, Any]) -> list[ChatMessage]:
        return [
            ChatMessage(
                role="system",
                content=(
                    "只返回 JSON。你只表演受限依据中唯一的 Actor，不得表演其他实体。"
                    "private_boundaries 是该 Actor 知道但绝对不能向玩家公开的内容；"
                    "可以据此拒绝、回避或说谎，但不得复述其原文。必须逐字覆盖 "
                    "required_content，用具体台词、动作或拒绝真正回应玩家；"
                    "不得宣布检定、伤害、资源变化、世界状态或结局。"
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"受限依据：{json.dumps(basis, ensure_ascii=False)}\n"
                    "返回 {basis_hash,public_narration,speaker_entity_ids}；"
                    "speaker_entity_ids 必须且只能包含当前 Actor。"
                ),
            ),
        ]

    @staticmethod
    def _validate(
        draft: _EntityActorDraft,
        basis: dict[str, Any],
        *,
        require_complete: bool = True,
    ) -> None:
        entity_id = str(basis["actor_public_context"]["entity_id"])
        if draft.basis_hash != basis["basis_hash"]:
            raise ValueError("Actor response changed its authority basis")
        if draft.speaker_entity_ids != (entity_id,):
            raise ValueError("Actor response used a speaker outside its isolated frame")
        dialogue = str(basis.get("dialogue") or "").strip()
        if (
            dialogue
            and SequenceMatcher(None, dialogue, draft.public_narration.strip()).ratio() >= 0.72
        ):
            raise ValueError("NPC response merely echoed the player's dialogue")
        for required in basis["required_content"]:
            if require_complete and required not in draft.public_narration:
                raise ValueError(f"Response omitted required content: {required}")
        for forbidden in basis["private_boundaries"]:
            if (
                forbidden
                and forbidden in draft.public_narration
                and forbidden not in basis["required_content"]
            ):
                raise ValueError("Response disclosed forbidden actor information")
        source_numbers = set(
            _NUMBER.findall(
                json.dumps(
                    {
                        "visible_state": basis["visible_state"],
                        "actor": basis["actor_public_context"],
                    },
                    ensure_ascii=False,
                )
            )
        )
        unsupported = set(_NUMBER.findall(draft.public_narration)) - source_numbers
        if unsupported:
            raise ValueError("Response introduced unsupported numeric claims")

    @staticmethod
    def _fallback(actor: EntityActingFrame) -> EntityActorResponse:
        required = [str(item) for item in actor.required_public_content]
        state = actor.runtime_state
        concrete = "；".join(
            value
            for value in (
                state.emotional_state,
                state.physical_state,
                *required,
            )
            if value
        )
        narration = (
            f"{actor.title}立即作出可观察的回应。"
            f"{concrete or '对方保持沉默，明确没有回答这个问题。'}"
        )
        return EntityActorResponse(
            entity_id=actor.entity_id,
            public_narration=narration,
            source="deterministic",
        )


def actor_execution_trace(
    actor: EntityActingFrame,
    response: EntityActorResponse,
) -> ActorExecutionTrace:
    """Reduce an Actor response to a stable public allowlist."""

    return ActorExecutionTrace(
        entity_id=actor.entity_id,
        entity_title=actor.title,
        execution=("model" if response.source == "model" else "deterministic_fallback"),
        generation_attempt_count=response.attempt_count,
        error_codes=tuple(dict.fromkeys(response.validation_errors)),
    )


def _public_error_code(exc: Exception) -> ActorExecutionErrorCode:
    """Map private provider/model failures to a stable player-safe diagnostic."""

    if isinstance(exc, (ValueError, TypeError)):
        return "actor_output_rejected"
    return "actor_provider_unavailable"


__all__ = [
    "ActorExecutionErrorCode",
    "ActorExecutionTrace",
    "EntityActorResponse",
    "SingleEntityActorAdapter",
    "actor_execution_trace",
]

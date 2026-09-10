"""Bounded narration for non-mechanical tabletop conversation turns."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Literal

from pydantic import Field

from ai_kp.platform.agents import (
    ActorExecutionTrace,
    EntityActingFrame,
    EntityAgentContextBuilder,
    SingleEntityActorAdapter,
    actor_execution_trace,
)
from ai_kp.platform.ports.llm import LlmClient
from ai_kp.platform.resolution.contracts import ScenarioContract, ScenarioSnapshot
from ai_kp.platform.resolution.tabletop_turn import (
    TabletopModel,
    TabletopRoute,
    TabletopTurnFrame,
)

_MAX_MODEL_ACTORS = 4
_ACTOR_TURN_DEADLINE_SECONDS = 180.0
_MAX_TABLETOP_NARRATION_CHARS = 100_000


class TabletopConversationResponse(TabletopModel):
    basis_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    public_narration: str = Field(min_length=1, max_length=_MAX_TABLETOP_NARRATION_CHARS)
    speaker_entity_ids: tuple[str, ...] = Field(default=(), max_length=8)
    source: Literal["model", "model_repaired", "deterministic"] = "model"
    attempt_count: int = Field(default=1, ge=0, le=2)
    validation_errors: tuple[str, ...] = ()
    actor_traces: tuple[ActorExecutionTrace, ...] = Field(default=(), max_length=8)


class ConstrainedTabletopResponseAdapter:
    """Perform an information/RP turn without granting state mutation authority."""

    def __init__(self, llm: LlmClient):
        self.llm = llm

    async def create(
        self,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        player_text: str,
        frame: TabletopTurnFrame,
        route: TabletopRoute,
    ) -> TabletopConversationResponse:
        basis = self._basis(contract, snapshot, player_text, frame, route)
        fallback = self._fallback(basis)
        if route != "roleplay":
            return fallback
        actors = self._acting_frames(contract, snapshot, player_text, frame)
        if not actors:
            return fallback

        adapter = SingleEntityActorAdapter(self.llm)
        responses = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _ACTOR_TURN_DEADLINE_SECONDS
        for index, actor in enumerate(actors):
            remaining = deadline - loop.time()
            use_model = index < _MAX_MODEL_ACTORS and remaining > 0
            if use_model:
                try:
                    async with asyncio.timeout(remaining):
                        response = await adapter.create(
                            actor=actor,
                            visible_state=basis["visible_state"],
                            player_text=player_text,
                            dialogue=frame.dialogue,
                        )
                except TimeoutError:
                    response = await adapter.create(
                        actor=actor,
                        visible_state=basis["visible_state"],
                        player_text=player_text,
                        dialogue=frame.dialogue,
                        use_model=False,
                    )
                    response = response.model_copy(
                        update={
                            "validation_errors": ("actor_turn_deadline_exhausted",),
                        }
                    )
            else:
                response = await adapter.create(
                    actor=actor,
                    visible_state=basis["visible_state"],
                    player_text=player_text,
                    dialogue=frame.dialogue,
                    use_model=False,
                )
                response = response.model_copy(
                    update={
                        "validation_errors": (
                            "actor_model_budget_exhausted"
                            if index >= _MAX_MODEL_ACTORS
                            else "actor_turn_deadline_exhausted",
                        ),
                    }
                )
            responses.append(response)

        model_count = sum(item.source == "model" for item in responses)
        if model_count == len(responses):
            source = "model"
        elif model_count:
            source = "model_repaired"
        else:
            source = "deterministic"
        errors = tuple(
            f"{item.entity_id}: {error}" for item in responses for error in item.validation_errors
        )
        return TabletopConversationResponse(
            basis_hash=basis["basis_hash"],
            public_narration=(
                " ".join(item.public_narration for item in responses) + " 现在轮到你决定如何回应。"
            ),
            speaker_entity_ids=tuple(item.entity_id for item in responses),
            source=source,
            attempt_count=max(item.attempt_count for item in responses),
            validation_errors=errors,
            actor_traces=tuple(
                actor_execution_trace(actor, response)
                for actor, response in zip(actors, responses, strict=True)
            ),
        )

    @staticmethod
    def _acting_frames(
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        player_text: str,
        frame: TabletopTurnFrame,
    ) -> tuple[EntityActingFrame, ...]:
        builder = EntityAgentContextBuilder()
        return tuple(
            item
            for entity_id in dict.fromkeys(frame.target_entity_ids)
            if (
                item := builder.build(
                    contract,
                    snapshot,
                    None,
                    entity_id=entity_id,
                    player_action=player_text,
                )
            )
            is not None
        )

    @staticmethod
    def _basis(
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        player_text: str,
        frame: TabletopTurnFrame,
        route: TabletopRoute,
    ) -> dict:
        acting = ConstrainedTabletopResponseAdapter._acting_frames(
            contract, snapshot, player_text, frame
        )
        actor_locations = set(snapshot.actor_locations.values())
        if snapshot.scene_id is not None:
            actor_locations.add(snapshot.scene_id)
        visible_locations = [
            {"id": item.location_id, "title": item.title}
            for item in contract.locations
            if item.location_id in actor_locations
        ]

        def current_location(entity_id: str, initial_location_id: str | None) -> str | None:
            runtime = snapshot.entity_runtime.get(entity_id)
            return runtime.location_id if runtime is not None else initial_location_id

        visible_entities = [
            {
                "id": item.entity_id,
                "title": item.title,
                "type": item.entity_type,
                "status": snapshot.entities.get(item.entity_id, item.initial_status),
                "location_id": current_location(item.entity_id, item.initial_location_id),
            }
            for item in contract.entities
            if item.entity_id in frame.target_entity_ids
            or current_location(item.entity_id, item.initial_location_id) in actor_locations
        ]
        actors = [
            {
                "entity_id": item.entity_id,
                "title": item.title,
                "canonical_summary": item.canonical_profile.summary,
                "known_facts": item.canonical_profile.known_facts,
                "behavioral_directives": item.canonical_profile.behavioral_directives,
                "derived_profile": item.derived_profile.model_dump(mode="json"),
                "runtime_state": item.runtime_state.model_dump(mode="json"),
            }
            for item in acting
        ]
        required = tuple(
            dict.fromkeys(value for item in acting for value in item.required_public_content)
        )
        forbidden = tuple(
            dict.fromkeys(value for item in acting for value in item.forbidden_disclosures)
        )
        payload = {
            "route": route,
            "player_text": player_text,
            "frame": frame.model_dump(mode="json"),
            "visible_state": {
                "scene_id": snapshot.scene_id,
                "locations": visible_locations,
                "entities": visible_entities,
            },
            "actors": actors,
            "required_content": required,
            "forbidden_disclosures": forbidden,
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return {**payload, "basis_hash": hashlib.sha256(encoded).hexdigest()}

    @staticmethod
    def _fallback(basis: dict) -> TabletopConversationResponse:
        route = basis["route"]
        required = [str(item) for item in basis["required_content"]]
        if route == "conversation":
            narration = "这是桌外交流，不会改变游戏世界。请继续说明角色接下来做什么。"
            speakers: tuple[str, ...] = ()
        elif route == "information":
            visible = basis["visible_state"]
            names = [item["title"] for item in visible["locations"]]
            names.extend(item["title"] for item in visible["entities"])
            detail = "、".join(dict.fromkeys(names))
            narration = (
                f"当前能直接确认的有：{detail}。你接下来做什么？"
                if detail
                else "现有公开状态不足以确认更多细节。你可以说明准备查看哪里或询问谁。"
            )
            speakers = ()
        else:
            actors = basis["actors"]
            if actors:
                actor = actors[0]
                state = actor["runtime_state"]
                behavior = next(iter(actor["behavioral_directives"]), "")
                facts = "；".join(str(value) for value in actor["known_facts"][:2])
                concrete = "；".join(
                    value
                    for value in (
                        facts,
                        behavior,
                        str(state.get("emotional_state") or ""),
                        *required,
                    )
                    if value
                )
                narration = (
                    f"{actor['title']}立即作出可观察的回应。{concrete or '对方保持沉默，明确没有回答这个问题。'}"
                    " 现在轮到你决定如何回应。"
                )
                speakers = (str(actor["entity_id"]),)
            else:
                narration = "当前没有可确认的交谈对象。请说明你是在对谁说话。"
                speakers = ()
        return TabletopConversationResponse(
            basis_hash=basis["basis_hash"],
            public_narration=narration,
            speaker_entity_ids=speakers,
            source="deterministic",
            attempt_count=0,
        )


__all__ = ["ConstrainedTabletopResponseAdapter", "TabletopConversationResponse"]

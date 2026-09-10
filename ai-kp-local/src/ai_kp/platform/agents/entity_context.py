"""Pure entity-context projection for an on-demand Actor Agent."""

from __future__ import annotations

import unicodedata

from pydantic import BaseModel, ConfigDict, Field

from ai_kp.platform.resolution.contracts import (
    EntityCanonicalProfile,
    EntityDerivedProfile,
    EntityRuntimeState,
    ResolutionPreview,
    ResponseObligation,
    ScenarioContract,
    ScenarioSnapshot,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel


class EntityActingFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: str
    title: str
    canonical_profile: EntityCanonicalProfile
    derived_profile: EntityDerivedProfile
    runtime_state: EntityRuntimeState
    obligations: tuple[ResponseObligation, ...] = ()
    forbidden_disclosures: tuple[str, ...] = Field(default=(), max_length=32)

    @property
    def required_public_content(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                item
                for obligation in self.obligations
                for item in (
                    *obligation.automatic_information,
                    *obligation.facts_to_convey,
                    *obligation.state_to_express,
                    *obligation.physical_behaviors,
                )
                if item.strip()
            )
        )


class EntityAgentContextBuilder:
    """Select source-backed entity material without maintaining an Agent per NPC."""

    def build(
        self,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        preview: ResolutionPreview | None,
        *,
        entity_id: str,
        player_action: str,
    ) -> EntityActingFrame | None:
        entity = next(
            (item for item in contract.entities if item.entity_id == entity_id),
            None,
        )
        if entity is None:
            return None
        query = ActionResolutionKernel.from_contract(contract).query_snapshot(snapshot)
        explicit = set(preview.response_obligation_ids) if preview is not None else set()
        obligations = tuple(
            item
            for item in contract.response_obligations
            if item.entity_id == entity_id
            and query.conditions_satisfied(item.conditions)
            and (
                item.obligation_id in explicit
                or (
                    not explicit
                    and self._topic_matches(item.trigger_topics, player_action)
                )
            )
        )
        runtime = snapshot.entity_runtime.get(
            entity_id,
            EntityRuntimeState(
                status=snapshot.entities.get(entity_id, entity.initial_status),
                location_id=entity.initial_location_id,
            ),
        )
        boundaries = tuple(
            dict.fromkeys(
                (
                    *entity.canonical_profile.secrets,
                    *entity.canonical_profile.boundaries,
                    *(value for item in obligations for value in item.boundaries),
                )
            )
        )
        return EntityActingFrame(
            entity_id=entity.entity_id,
            title=entity.title,
            canonical_profile=entity.canonical_profile,
            derived_profile=entity.derived_profile,
            runtime_state=runtime,
            obligations=obligations,
            forbidden_disclosures=boundaries,
        )

    @staticmethod
    def _topic_matches(topics: tuple[str, ...], player_action: str) -> bool:
        if not topics:
            return True
        normalized = unicodedata.normalize("NFKC", player_action).casefold()
        return any(
            unicodedata.normalize("NFKC", topic).casefold() in normalized
            for topic in topics
            if topic.strip()
        )


__all__ = ["EntityActingFrame", "EntityAgentContextBuilder"]

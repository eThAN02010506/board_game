"""Deterministically realize approved template entities inside one caller-owned transaction."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal

from ai_kp.application.errors import ConflictError, InvalidInputError
from ai_kp.application.ports.world_entities import WorldEntityStore
from ai_kp.director.world_expansion import (
    WorldExpansionCandidate,
    validate_world_expansion_plan,
)
from ai_kp.platform.scenes.settlement_templates import EntityArchetypeKind

WorldEntityVisibility = Literal["table", "kp", "secret"]

_MODULE_KIND: dict[str, EntityArchetypeKind] = {
    "npc": "npc",
    "organization": "organization",
    "item": "item",
    "clue": "clue_carrier",
    "event": "event",
}


@dataclass(frozen=True)
class EncounterEntityRealization:
    local_ref: str
    name: str
    description: str = ""
    visibility: WorldEntityVisibility = "table"


@dataclass(frozen=True)
class MaterializedEntitySet:
    entities_by_ref: dict[str, dict]
    attachments: tuple[dict, ...]
    npc_records: tuple[dict, ...]


class WorldEntityMaterializer:
    """Own the candidate-to-ledger mapping shared by AI and human KP workflows."""

    def __init__(self, repo: WorldEntityStore):
        self.repo = repo

    def materialize(
        self,
        *,
        campaign_id: str,
        proposal_id: str,
        encounter_event_id: str,
        expansion: dict,
        realizations: tuple[EncounterEntityRealization, ...],
        happened_at: str | None,
    ) -> MaterializedEntitySet:
        candidate = WorldExpansionCandidate.model_validate(expansion["candidate"])
        analysis = expansion["analysis"]
        validate_world_expansion_plan(candidate, analysis)
        template_binding = candidate.template_binding
        generated = tuple(template_binding.entity_bindings) if template_binding else ()
        realization_by_ref = self._validate_realizations(generated, realizations)

        entities_by_ref: dict[str, dict] = {}
        attachments: list[dict] = []
        npc_records: list[dict] = []
        source_bindings = {
            str(item["module_entity_id"]): item
            for item in (analysis.get("settlement_template") or {}).get(
                "source_entity_bindings", ()
            )
        }
        selected_slot = next(
            (
                item
                for item in (analysis.get("settlement_template") or {}).get(
                    "scene_slots", ()
                )
                if template_binding is not None
                and item.get("slot_id") == template_binding.slot_id
            ),
            {},
        )
        archetypes = {
            str(item.get("archetype_id")): item
            for item in selected_slot.get("entity_archetype_candidates", ())
            if item.get("archetype_id")
        }
        state_entities = {
            str(item["entity_id"]): item
            for item in analysis.get("entity_states") or ()
            if item.get("entity_id")
        }
        source_ids = list(template_binding.source_entity_ids) if template_binding else []
        relation_target_ids = [
            relation.target_ref
            for entity in generated
            for relation in entity.relation_bindings
            if relation.target_source == "existing_entity"
        ]
        for source_id in dict.fromkeys([*source_ids, *relation_target_ids]):
            binding = source_bindings.get(source_id)
            state = state_entities.get(source_id)
            if binding is not None:
                source = binding.get("entity") or {}
                archetype = binding.get("archetype") or {}
                entity_kind = str(archetype.get("entity_kind") or "")
                archetype_id = str(binding.get("archetype_id") or "") or None
                name = str(source.get("name") or "")
                description = str(source.get("description") or "")
                state_dimensions = list(archetype.get("state_dimensions") or ())
            elif state is not None:
                entity_kind = _MODULE_KIND.get(str(state.get("entity_type") or ""), "")
                archetype_id = None
                name = str(state.get("name") or "")
                description = ""
                state_dimensions = []
            else:
                raise InvalidInputError(f"Unknown approved source entity: {source_id}")
            if not entity_kind or not name:
                raise InvalidInputError(
                    f"Approved source entity lacks materialization metadata: {source_id}"
                )
            entity, npc = self._ensure_entity(
                campaign_id=campaign_id,
                entity_kind=entity_kind,
                archetype_id=archetype_id,
                name=name,
                description=description,
                visibility="kp",
                origin_kind="module_source",
                origin_ref=source_id,
                created_from_event_id=encounter_event_id,
                happened_at=happened_at,
                role="source_entity",
                data={
                    "module_entity_id": source_id,
                    "state_dimensions": state_dimensions,
                },
            )
            entities_by_ref[source_id] = entity
            attachments.append(
                {
                    "world_entity_id": entity["id"],
                    "role": "source" if source_id in source_ids else "relation_target",
                    "local_ref": source_id,
                }
            )
            if npc is not None:
                npc_records.append(npc)

        for binding in generated:
            realization = realization_by_ref[binding.local_ref]
            archetype = archetypes.get(binding.archetype_id) or {}
            entity, npc = self._ensure_entity(
                campaign_id=campaign_id,
                entity_kind=binding.entity_kind,
                archetype_id=binding.archetype_id,
                name=realization.name,
                description=realization.description,
                visibility=realization.visibility,
                origin_kind="world_expansion",
                origin_ref=f"{proposal_id}:{binding.local_ref}",
                created_from_event_id=encounter_event_id,
                happened_at=happened_at,
                role=binding.label_variant,
                data={
                    "proposal_id": proposal_id,
                    "local_ref": binding.local_ref,
                    "label_variant": binding.label_variant,
                    "profession_ids": list(binding.profession_ids),
                    "candidate_subject": candidate.subject,
                    "candidate_proposal": candidate.proposal,
                    "state_dimensions": list(archetype.get("state_dimensions") or ()),
                },
            )
            entities_by_ref[binding.local_ref] = entity
            attachments.append(
                {
                    "world_entity_id": entity["id"],
                    "role": "generated",
                    "local_ref": binding.local_ref,
                }
            )
            if npc is not None:
                npc_records.append(npc)

        for binding in generated:
            source = entities_by_ref[binding.local_ref]
            for relation in binding.relation_bindings:
                target = entities_by_ref.get(relation.target_ref)
                if target is None:
                    raise InvalidInputError(
                        f"Approved relation target was not materialized: {relation.target_ref}"
                    )
                self.repo.create_campaign_world_entity_relation(
                    campaign_id=campaign_id,
                    source_entity_id=source["id"],
                    relation_slot_id=relation.relation_slot_id,
                    target_entity_id=target["id"],
                    created_from_event_id=encounter_event_id,
                )
        return MaterializedEntitySet(
            entities_by_ref=entities_by_ref,
            attachments=tuple(self._unique_attachments(attachments)),
            npc_records=tuple(self._unique_npcs(npc_records)),
        )

    @staticmethod
    def _validate_realizations(generated, realizations):
        expected = [item.local_ref for item in generated]
        supplied = [item.local_ref for item in realizations]
        if len(supplied) != len(set(supplied)):
            raise InvalidInputError("World entity realizations must use unique local refs")
        if set(supplied) != set(expected):
            raise InvalidInputError(
                "World entity realizations must exactly match approved candidate refs; "
                f"missing={sorted(set(expected) - set(supplied))}, "
                f"unknown={sorted(set(supplied) - set(expected))}"
            )
        return {item.local_ref: item for item in realizations}

    def _ensure_entity(
        self,
        *,
        campaign_id: str,
        entity_kind: str,
        archetype_id: str | None,
        name: str,
        description: str,
        visibility: WorldEntityVisibility,
        origin_kind: str,
        origin_ref: str,
        created_from_event_id: str,
        happened_at: str | None,
        role: str,
        data: dict,
    ) -> tuple[dict, dict | None]:
        normalized_name = self._text(name, "world_entity.name", 240)
        normalized_description = self._text(
            description, "world_entity.description", 4000, allow_blank=True
        )
        existing = self.repo.find_campaign_world_entity_by_origin(
            campaign_id, origin_kind, origin_ref
        )
        if existing is not None:
            if (
                existing["entity_kind"] != entity_kind
                or existing.get("archetype_id") != archetype_id
                or existing["name"] != normalized_name
            ):
                raise ConflictError("World entity origin already has incompatible identity")
            npc = self.repo.get_npc(str(existing["npc_id"])) if existing.get("npc_id") else None
            return existing, npc
        npc = None
        if entity_kind == "npc":
            profession_ids = data.get("profession_ids") or ()
            npc = self.repo.create_npc(
                name=normalized_name,
                home_location=None,
                profession=str(profession_ids[0]) if profession_ids else None,
                public_notes=normalized_description if visibility == "table" else "",
                secret_notes=normalized_description if visibility != "table" else "",
            )
            self.repo.link_npc_to_campaign(
                campaign_id,
                str(npc["id"]),
                role=role,
                first_seen_time=happened_at,
                last_seen_time=happened_at,
                relationship_score=0,
                notes="",
            )
        entity = self.repo.create_campaign_world_entity(
            campaign_id=campaign_id,
            entity_kind=entity_kind,
            archetype_id=archetype_id,
            name=normalized_name,
            description=normalized_description,
            visibility=visibility,
            origin_kind=origin_kind,
            origin_ref=origin_ref,
            npc_id=npc["id"] if npc else None,
            created_from_event_id=created_from_event_id,
            data=data,
        )
        return entity, npc

    @staticmethod
    def _unique_attachments(items: list[dict]) -> list[dict]:
        return list({str(item["world_entity_id"]): item for item in items}.values())

    @staticmethod
    def _unique_npcs(items: list[dict]) -> list[dict]:
        return list({str(item["id"]): item for item in items}.values())

    @staticmethod
    def _text(
        value: str,
        field_name: str,
        max_length: int,
        *,
        allow_blank: bool = False,
    ) -> str:
        normalized = " ".join(unicodedata.normalize("NFKC", value).split())
        if not normalized and not allow_blank:
            raise InvalidInputError(f"{field_name} cannot be blank")
        if len(normalized) > max_length:
            raise InvalidInputError(f"{field_name} cannot exceed {max_length} characters")
        return normalized


__all__ = [
    "EncounterEntityRealization",
    "MaterializedEntitySet",
    "WorldEntityMaterializer",
    "WorldEntityVisibility",
]

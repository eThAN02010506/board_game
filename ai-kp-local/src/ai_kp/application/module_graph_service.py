"""Use cases for provenance-bound module entities, relations, and anchor checks."""

from ai_kp.application.ports.repositories import ModuleGraphStore
from ai_kp.platform.modules.graph import (
    ModuleEntityCreate,
    ModuleRelationCreate,
    normalize_entity_name,
)
from ai_kp.platform.modules.knowledge import validate_derived_scope


class ModuleGraphService:
    def __init__(self, repo: ModuleGraphStore):
        self.repo = repo

    def create_entity(
        self,
        module_id: str,
        payload: ModuleEntityCreate,
        *,
        member_id: str,
        extra_candidate_ids: tuple[str, ...] = (),
    ) -> dict:
        candidate = self._require_approved_source(
            module_id,
            payload.source_candidate_id,
        )
        if payload.entity_type == "anchor" and candidate["kind"] != "module_anchor":
            raise ValueError("Anchor entities require an approved module_anchor source")
        if normalize_entity_name(payload.name) not in self._candidate_text(candidate):
            raise ValueError("Entity name is not present in its approved source candidate")
        self._require_scope_preserved(
            candidate,
            visibility=payload.visibility,
            spoiler_tag=payload.spoiler_tag,
            label="Entity",
        )
        validated_extra: list[str] = []
        normalized_name = normalize_entity_name(payload.name)
        for extra in dict.fromkeys(extra_candidate_ids):
            if extra == payload.source_candidate_id:
                continue
            extra_candidate = self._require_approved_source(module_id, str(extra))
            attributed_name = normalize_entity_name(
                str(extra_candidate.get("entity_name") or "")
            )
            if attributed_name:
                if attributed_name != normalized_name:
                    raise ValueError("Entity source attribution does not match entity name")
            elif normalized_name not in self._candidate_text(extra_candidate):
                raise ValueError("Entity name is not present in an additional source")
            validated_extra.append(str(extra))
        return self.repo.create_module_entity(
            module_id,
            payload,
            member_id=member_id,
            extra_candidate_ids=tuple(validated_extra),
        )

    def create_relation(
        self,
        module_id: str,
        payload: ModuleRelationCreate,
        *,
        member_id: str,
    ) -> dict:
        candidate = self._require_approved_source(
            module_id,
            payload.source_candidate_id,
        )
        source = self.repo.get_module_entity(payload.source_entity_id)
        target = self.repo.get_module_entity(payload.target_entity_id)
        if source["module_id"] != module_id or target["module_id"] != module_id:
            raise ValueError("Relation endpoints must belong to the current module")
        if payload.predicate == "same_as" and source["entity_type"] != target["entity_type"]:
            raise ValueError("same_as requires entities of the same type")
        candidate_text = self._candidate_text(candidate)
        for endpoint in (source, target):
            if normalize_entity_name(endpoint["name"]) not in candidate_text:
                raise ValueError(
                    "Relation endpoint is not present in its approved source candidate"
                )
        self._require_scope_preserved(
            candidate,
            visibility=payload.visibility,
            spoiler_tag=payload.spoiler_tag,
            label="Relation source candidate",
        )
        for label, endpoint in (
            ("Relation source entity", source),
            ("Relation target entity", target),
        ):
            # Endpoint spoiler tags remain on the endpoint rows and retrieval
            # requires all of them. A cross-act edge therefore preserves its
            # direct evidence tag without having to equal both endpoint tags.
            validate_derived_scope(
                source_visibility=str(endpoint.get("visibility") or "kp"),
                source_spoiler_tag=None,
                derived_visibility=payload.visibility,
                derived_spoiler_tag=payload.spoiler_tag,
                label=label,
            )
        return self.repo.create_module_relation(module_id, payload, member_id=member_id)

    def attach_candidate(
        self,
        module_id: str,
        entity_id: str,
        candidate_id: str,
    ) -> dict:
        """Attach current approved evidence without changing the entity's scope."""
        entity = self.repo.get_module_entity(entity_id)
        if entity["module_id"] != module_id:
            raise ValueError("Entity belongs to another module")
        candidate = self._require_approved_source(module_id, candidate_id)
        attributed_type = str(candidate.get("entity_type") or "").strip()
        if attributed_type and attributed_type != entity["entity_type"]:
            raise ValueError("Entity source attribution does not match entity type")
        normalized_name = normalize_entity_name(str(entity["name"]))
        attributed_name = normalize_entity_name(
            str(candidate.get("entity_name") or "")
        )
        if attributed_name:
            if attributed_name != normalized_name:
                raise ValueError("Entity source attribution does not match entity name")
        elif normalized_name not in self._candidate_text(candidate):
            raise ValueError("Entity name is not present in its approved source candidate")
        return self.repo.link_candidate_to_entity(entity_id, candidate_id)

    def check_reachability(
        self,
        module_id: str,
        entry_entity_ids: tuple[str, ...],
    ) -> dict:
        for entity_id in entry_entity_ids:
            entity = self.repo.get_module_entity(entity_id)
            if entity["module_id"] != module_id:
                raise ValueError("Entry entity belongs to another module")
        return self.repo.module_graph_reachability(module_id, entry_entity_ids)

    def _require_approved_source(self, module_id: str, candidate_id: str) -> dict:
        candidate = self.repo.get_module_knowledge_candidate(candidate_id)
        if candidate["module_id"] != module_id:
            raise ValueError("Source candidate belongs to another module")
        if candidate["status"] != "approved":
            raise ValueError("Entities and relations require an approved source candidate")
        if not self.repo.module_candidate_sources_are_current(
            candidate_id,
            module_id=module_id,
        ):
            raise ValueError(
                "Entities and relations require a currently valid source candidate"
            )
        return candidate

    @staticmethod
    def _candidate_text(candidate: dict) -> str:
        parts = [candidate["title"], candidate["statement"]]
        parts.extend(item["evidence_text"] for item in candidate.get("citations", ()))
        return normalize_entity_name(" ".join(str(part) for part in parts))

    @staticmethod
    def _require_scope_preserved(
        source: dict,
        *,
        visibility: str,
        spoiler_tag: str | None,
        label: str,
    ) -> None:
        validate_derived_scope(
            source_visibility=str(source.get("visibility") or "kp"),
            source_spoiler_tag=source.get("spoiler_tag"),
            derived_visibility=visibility,
            derived_spoiler_tag=spoiler_tag,
            label=label,
        )

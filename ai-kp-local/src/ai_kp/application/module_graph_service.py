"""Use cases for provenance-bound module entities, relations, and anchor checks."""

from ai_kp.application.ports.repositories import ModuleGraphStore
from ai_kp.platform.modules.graph import (
    ModuleEntityCreate,
    ModuleRelationCreate,
    normalize_entity_name,
)


class ModuleGraphService:
    def __init__(self, repo: ModuleGraphStore):
        self.repo = repo

    def create_entity(
        self,
        module_id: str,
        payload: ModuleEntityCreate,
        *,
        member_id: str,
    ) -> dict:
        candidate = self._require_approved_source(
            module_id,
            payload.source_candidate_id,
        )
        if payload.entity_type == "anchor" and candidate["kind"] != "module_anchor":
            raise ValueError("Anchor entities require an approved module_anchor source")
        if normalize_entity_name(payload.name) not in self._candidate_text(candidate):
            raise ValueError("Entity name is not present in its approved source candidate")
        return self.repo.create_module_entity(module_id, payload, member_id=member_id)

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
        return self.repo.create_module_relation(module_id, payload, member_id=member_id)

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
        return candidate

    @staticmethod
    def _candidate_text(candidate: dict) -> str:
        parts = [candidate["title"], candidate["statement"]]
        parts.extend(item["evidence_text"] for item in candidate.get("citations", ()))
        return normalize_entity_name(" ".join(str(part) for part in parts))

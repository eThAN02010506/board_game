"""Review and materialize provenance-bound module entities."""

from __future__ import annotations

import sqlite3

from ai_kp.application.module_graph_service import ModuleGraphService
from ai_kp.application.module_knowledge_service import ModuleKnowledgeService
from ai_kp.application.ports.repositories import ModuleEntityMaterializationStore
from ai_kp.platform.modules.graph import ModuleEntityCreate, normalize_entity_name
from ai_kp.platform.modules.knowledge import VISIBILITY_RANK


class ModuleEntityMaterializationService:
    def __init__(self, repo: ModuleEntityMaterializationStore):
        self.repo = repo
        self.knowledge = ModuleKnowledgeService(repo)
        self.graph = ModuleGraphService(repo)

    def review_and_materialize(
        self,
        module_id: str,
        candidate_ids: tuple[str, ...],
        *,
        member_id: str | None,
        note: str,
    ) -> list[dict]:
        qualified: list[dict] = []
        for candidate_id in dict.fromkeys(candidate_ids):
            candidate = self.repo.get_module_knowledge_candidate(candidate_id)
            if candidate.get("module_id") != module_id or candidate.get("status") != "pending":
                continue
            if candidate.get("kind") not in {"module_canon", "module_anchor"}:
                continue
            try:
                self.repo.begin_immediate()
                self.knowledge.review_candidate(
                    candidate_id,
                    decision="approved",
                    member_id=member_id,
                    note=note,
                )
                self.repo.commit()
                qualified.append(
                    self.repo.get_module_knowledge_candidate(candidate_id)
                )
            except (KeyError, ValueError):
                self.repo.rollback()
        self.materialize_approved(module_id, qualified, member_id=member_id)
        return qualified

    def materialize_approved(
        self,
        module_id: str,
        candidates: list[dict],
        *,
        member_id: str | None,
    ) -> None:
        attributed: dict[tuple[str, str], list[dict]] = {}
        legacy: list[dict] = []
        for candidate in candidates:
            entity_name = str(candidate.get("entity_name") or "").strip()
            entity_type = str(candidate.get("entity_type") or "").strip()
            if entity_name and entity_type:
                attributed.setdefault((entity_type, entity_name), []).append(candidate)
            else:
                legacy.append(candidate)
        for (entity_type, entity_name), group in attributed.items():
            self._materialize_group(
                module_id,
                entity_type,
                entity_name,
                group,
                member_id=member_id,
            )
        for candidate in legacy:
            self._materialize_legacy(module_id, candidate, member_id=member_id)

    def _materialize_group(
        self,
        module_id: str,
        entity_type: str,
        entity_name: str,
        candidates: list[dict],
        *,
        member_id: str | None,
    ) -> None:
        ordered = sorted(
            candidates,
            key=lambda item: (str(item.get("created_at") or ""), str(item["id"])),
        )
        primary = min(
            ordered,
            key=lambda item: (
                VISIBILITY_RANK.get(str(item.get("visibility") or "kp"), 0),
                item.get("spoiler_tag") is not None,
                str(item.get("created_at") or ""),
                str(item["id"]),
            ),
        )
        rest = tuple(
            str(candidate["id"])
            for candidate in ordered
            if candidate["id"] != primary["id"]
        )
        existing = self.repo.find_module_entity(
            module_id,
            entity_type=entity_type,
            normalized_name=normalize_entity_name(entity_name),
        )
        if existing is not None:
            self._attach_group(
                module_id,
                str(existing["id"]),
                ordered,
            )
            return
        try:
            self.graph.create_entity(
                module_id,
                ModuleEntityCreate(
                    entity_type=entity_type,
                    name=entity_name,
                    description=str(primary.get("statement") or ""),
                    visibility=str(primary.get("visibility") or "kp"),
                    spoiler_tag=primary.get("spoiler_tag"),
                    source_candidate_id=str(primary["id"]),
                ),
                member_id=member_id,
                extra_candidate_ids=rest,
            )
            self.repo.commit()
        except sqlite3.IntegrityError:
            self.repo.rollback()
            # Another worker may have created the same entity after our lookup.
            existing = self.repo.find_module_entity(
                module_id,
                entity_type=entity_type,
                normalized_name=normalize_entity_name(entity_name),
            )
            if existing is not None:
                self._attach_group(module_id, str(existing["id"]), ordered)
        except (KeyError, ValueError):
            self.repo.rollback()

    def _attach_group(
        self,
        module_id: str,
        entity_id: str,
        candidates: list[dict],
    ) -> None:
        try:
            for candidate in candidates:
                self.graph.attach_candidate(
                    module_id,
                    entity_id,
                    str(candidate["id"]),
                )
            self.repo.commit()
        except (KeyError, ValueError, sqlite3.IntegrityError):
            self.repo.rollback()

    def _materialize_legacy(
        self,
        module_id: str,
        candidate: dict,
        *,
        member_id: str | None,
    ) -> None:
        entity_type = entity_type_for_candidate(candidate)
        if entity_type is None:
            return
        try:
            self.graph.create_entity(
                module_id,
                ModuleEntityCreate(
                    entity_type=entity_type,
                    name=str(candidate.get("title") or "").strip(),
                    description=str(candidate.get("statement") or ""),
                    visibility=str(candidate.get("visibility") or "kp"),
                    spoiler_tag=candidate.get("spoiler_tag"),
                    source_candidate_id=str(candidate["id"]),
                ),
                member_id=member_id,
            )
            self.repo.commit()
        except (KeyError, ValueError, sqlite3.IntegrityError):
            self.repo.rollback()


def entity_type_for_candidate(candidate: dict) -> str | None:
    """Use extracted structure only; free text never determines entity authority."""

    entity_type = str(candidate.get("entity_type") or "").strip()
    return entity_type if entity_type in {"clue", "npc", "location"} else None


__all__ = ["ModuleEntityMaterializationService", "entity_type_for_candidate"]

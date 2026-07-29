"""SQLite persistence and deterministic traversal for module entity graphs."""

from __future__ import annotations

from collections import defaultdict, deque

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.module_knowledge import (
    get_current_module_candidate_scope,
)
from ai_kp.infrastructure.database.rows import row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.modules.graph import (
    CONFLICT_PREDICATES,
    TRAVERSABLE_PREDICATES,
    ModuleEntityCreate,
    ModuleRelationCreate,
    normalize_entity_name,
)
from ai_kp.platform.modules.knowledge import validate_derived_scope

_ALL_VISIBILITY = ("player", "table", "kp", "secret")


class ModuleGraphRepository(SQLiteRepository):
    def create_module_entity(
        self,
        module_id: str,
        entity: ModuleEntityCreate,
        *,
        member_id: str,
    ) -> dict:
        entity_id = new_id("modent")
        self.connection.execute(
            """
            INSERT INTO module_entities
              (id, module_id, entity_type, name, normalized_name, description,
               visibility, spoiler_tag, source_candidate_id, created_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_id,
                module_id,
                entity.entity_type,
                entity.name.strip(),
                normalize_entity_name(entity.name),
                entity.description.strip(),
                entity.visibility,
                entity.spoiler_tag,
                entity.source_candidate_id,
                member_id,
            ),
        )
        return self.get_module_entity(entity_id)

    def get_module_entity(self, entity_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM module_entities WHERE id = ?",
            (entity_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Module entity not found: {entity_id}")
        return row_to_dict(row)

    def list_module_entities(self, module_id: str) -> list[dict]:
        entities, _relations = self._current_module_graph(module_id)
        return entities

    def create_module_relation(
        self,
        module_id: str,
        relation: ModuleRelationCreate,
        *,
        member_id: str,
    ) -> dict:
        relation_id = new_id("modrel")
        self.connection.execute(
            """
            INSERT INTO module_entity_relations
              (id, module_id, source_entity_id, predicate, target_entity_id,
               source_candidate_id, confidence, visibility, spoiler_tag, note,
               created_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                relation_id,
                module_id,
                relation.source_entity_id,
                relation.predicate,
                relation.target_entity_id,
                relation.source_candidate_id,
                relation.confidence,
                relation.visibility,
                relation.spoiler_tag,
                relation.note.strip(),
                member_id,
            ),
        )
        return self.get_module_relation(relation_id)

    def get_module_relation(self, relation_id: str) -> dict:
        row = self.connection.execute(
            """
            SELECT r.*, source.name AS source_name, target.name AS target_name
            FROM module_entity_relations r
            JOIN module_entities source ON source.id = r.source_entity_id
            JOIN module_entities target ON target.id = r.target_entity_id
            WHERE r.id = ?
            """,
            (relation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Module relation not found: {relation_id}")
        return row_to_dict(row)

    def list_module_relations(self, module_id: str) -> list[dict]:
        _entities, relations = self._current_module_graph(module_id)
        return relations

    def module_graph_reachability(
        self,
        module_id: str,
        entry_entity_ids: tuple[str, ...],
    ) -> dict:
        if not entry_entity_ids:
            raise ValueError("At least one entry entity is required")
        entities, relations = self._current_module_graph(module_id)
        valid_entity_ids = {str(entity["id"]) for entity in entities}
        reached_ids = {
            entity_id for entity_id in entry_entity_ids if entity_id in valid_entity_ids
        }
        adjacency: dict[str, list[str]] = defaultdict(list)
        for relation in relations:
            if relation["predicate"] in TRAVERSABLE_PREDICATES:
                adjacency[str(relation["source_entity_id"])].append(
                    str(relation["target_entity_id"])
                )
        frontier = deque(reached_ids)
        while frontier:
            current = frontier.popleft()
            for target_id in adjacency[current]:
                if target_id in reached_ids:
                    continue
                reached_ids.add(target_id)
                frontier.append(target_id)
        anchors = [
            {**entity, "reachable": entity["id"] in reached_ids}
            for entity in entities
            if entity["entity_type"] == "anchor"
        ]
        conflict_rows = [
            relation
            for relation in relations
            if relation["predicate"] in CONFLICT_PREDICATES
            and (
                relation["source_entity_id"] in reached_ids
                or relation["target_entity_id"] in reached_ids
            )
        ]
        all_anchors_reachable = bool(anchors) and all(
            anchor["reachable"] for anchor in anchors
        )
        return {
            "module_id": module_id,
            "entry_entity_ids": list(entry_entity_ids),
            "invalid_entry_entity_ids": sorted(
                set(entry_entity_ids).difference(valid_entity_ids)
            ),
            "reached_entity_ids": sorted(reached_ids),
            "anchors": anchors,
            "all_anchors_reachable": all_anchors_reachable,
            "has_conflicts": bool(conflict_rows),
            "safe": all_anchors_reachable and not conflict_rows,
            "conflicts": conflict_rows,
        }

    def _current_module_graph(self, module_id: str) -> tuple[list[dict], list[dict]]:
        """Build a read-time graph without deleting invalid provenance rows."""

        candidate_scopes: dict[str, dict | None] = {}

        def candidate_scope(candidate_id: str) -> dict | None:
            if candidate_id not in candidate_scopes:
                candidate_scopes[candidate_id] = get_current_module_candidate_scope(
                    self.connection,
                    candidate_id,
                    module_id=module_id,
                    allowed_visibility=_ALL_VISIBILITY,
                    spoiler_tags=None,
                )
            return candidate_scopes[candidate_id]

        entity_rows = self.connection.execute(
            """
            SELECT * FROM module_entities
            WHERE module_id = ?
            ORDER BY entity_type, name, id
            """,
            (module_id,),
        ).fetchall()
        entities: list[dict] = []
        for row in entity_rows:
            entity = row_to_dict(row)
            scope = candidate_scope(str(entity["source_candidate_id"]))
            if scope is None or not _derived_scope_is_current(
                scope,
                entity,
                label="Entity",
            ):
                continue
            entities.append(entity)

        entities_by_id = {str(entity["id"]): entity for entity in entities}
        relation_rows = self.connection.execute(
            """
            SELECT r.*, source.name AS source_name, target.name AS target_name
            FROM module_entity_relations r
            JOIN module_entities source ON source.id = r.source_entity_id
            JOIN module_entities target ON target.id = r.target_entity_id
            WHERE r.module_id = ?
            ORDER BY source.name, r.predicate, target.name, r.id
            """,
            (module_id,),
        ).fetchall()
        relations: list[dict] = []
        for row in relation_rows:
            relation = row_to_dict(row)
            source_entity = entities_by_id.get(str(relation["source_entity_id"]))
            target_entity = entities_by_id.get(str(relation["target_entity_id"]))
            if source_entity is None or target_entity is None:
                continue
            scope = candidate_scope(str(relation["source_candidate_id"]))
            if scope is None or not _derived_scope_is_current(
                scope,
                relation,
                label="Relation source candidate",
            ):
                continue
            if any(
                not _derived_scope_is_current(
                    {**endpoint, "spoiler_tag": None},
                    relation,
                    label=label,
                )
                for label, endpoint in (
                    ("Relation source entity", source_entity),
                    ("Relation target entity", target_entity),
                )
            ):
                continue
            relations.append(relation)
        return entities, relations


def _derived_scope_is_current(source: dict, derived: dict, *, label: str) -> bool:
    try:
        validate_derived_scope(
            source_visibility=str(source.get("visibility") or "kp"),
            source_spoiler_tag=source.get("spoiler_tag"),
            derived_visibility=str(derived.get("visibility") or "kp"),
            derived_spoiler_tag=derived.get("spoiler_tag"),
            label=label,
        )
    except (KeyError, ValueError):
        return False
    return True

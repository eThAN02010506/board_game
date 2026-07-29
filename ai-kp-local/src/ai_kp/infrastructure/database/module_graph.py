"""SQLite persistence and deterministic traversal for module entity graphs."""

from __future__ import annotations

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.modules.graph import (
    CONFLICT_PREDICATES,
    TRAVERSABLE_PREDICATES,
    ModuleEntityCreate,
    ModuleRelationCreate,
    normalize_entity_name,
)


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
        rows = self.connection.execute(
            """
            SELECT * FROM module_entities
            WHERE module_id = ?
            ORDER BY entity_type, name, id
            """,
            (module_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

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
        rows = self.connection.execute(
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
        return [row_to_dict(row) for row in rows]

    def module_graph_reachability(
        self,
        module_id: str,
        entry_entity_ids: tuple[str, ...],
    ) -> dict:
        if not entry_entity_ids:
            raise ValueError("At least one entry entity is required")
        placeholders = ",".join("?" for _ in entry_entity_ids)
        predicate_placeholders = ",".join("?" for _ in TRAVERSABLE_PREDICATES)
        reached_rows = self.connection.execute(
            f"""
            WITH RECURSIVE reachable(entity_id) AS (
              SELECT id FROM module_entities
              WHERE module_id = ? AND id IN ({placeholders})
              UNION
              SELECT r.target_entity_id
              FROM module_entity_relations r
              JOIN reachable current ON current.entity_id = r.source_entity_id
              WHERE r.module_id = ?
                AND r.predicate IN ({predicate_placeholders})
            )
            SELECT entity_id FROM reachable
            """,
            (
                module_id,
                *entry_entity_ids,
                module_id,
                *TRAVERSABLE_PREDICATES,
            ),
        ).fetchall()
        reached_ids = {str(row["entity_id"]) for row in reached_rows}
        entities = self.list_module_entities(module_id)
        anchors = [
            {**entity, "reachable": entity["id"] in reached_ids}
            for entity in entities
            if entity["entity_type"] == "anchor"
        ]
        conflict_placeholders = ",".join("?" for _ in CONFLICT_PREDICATES)
        conflicts = []
        if reached_ids:
            reached_placeholders = ",".join("?" for _ in reached_ids)
            conflicts = self.connection.execute(
                f"""
                SELECT r.*, source.name AS source_name, target.name AS target_name
                FROM module_entity_relations r
                JOIN module_entities source ON source.id = r.source_entity_id
                JOIN module_entities target ON target.id = r.target_entity_id
                WHERE r.module_id = ?
                  AND r.predicate IN ({conflict_placeholders})
                  AND (
                    r.source_entity_id IN ({reached_placeholders})
                    OR r.target_entity_id IN ({reached_placeholders})
                  )
                ORDER BY source.name, r.predicate, target.name
                """,
                (
                    module_id,
                    *CONFLICT_PREDICATES,
                    *reached_ids,
                    *reached_ids,
                ),
            ).fetchall()
        conflict_rows = [row_to_dict(row) for row in conflicts]
        all_anchors_reachable = bool(anchors) and all(
            anchor["reachable"] for anchor in anchors
        )
        return {
            "module_id": module_id,
            "entry_entity_ids": list(entry_entity_ids),
            "reached_entity_ids": sorted(reached_ids),
            "anchors": anchors,
            "all_anchors_reachable": all_anchors_reachable,
            "has_conflicts": bool(conflict_rows),
            "safe": all_anchors_reachable and not conflict_rows,
            "conflicts": conflict_rows,
        }

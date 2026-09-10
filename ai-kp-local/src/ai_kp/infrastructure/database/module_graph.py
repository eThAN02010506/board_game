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
        extra_candidate_ids: tuple[str, ...] = (),
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
        candidate_ids = (entity.source_candidate_id, *extra_candidate_ids)
        for index, candidate_id in enumerate(dict.fromkeys(candidate_ids)):
            self.connection.execute(
                """
                INSERT INTO module_entity_candidates
                  (id, entity_id, candidate_id, role)
                VALUES (?, ?, ?, ?)
                """,
                (
                    new_id("modentcand"),
                    entity_id,
                    candidate_id,
                    "source" if index == 0 else "reference",
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

    def find_module_entity(
        self,
        module_id: str,
        *,
        entity_type: str,
        normalized_name: str,
    ) -> dict | None:
        row = self.connection.execute(
            """
            SELECT * FROM module_entities
            WHERE module_id = ? AND entity_type = ? AND normalized_name = ?
            """,
            (module_id, entity_type, normalized_name),
        ).fetchone()
        return row_to_dict(row) if row is not None else None

    def list_entity_candidates(self, entity_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT c.*, ec.role AS entity_role
            FROM module_entity_candidates ec
            JOIN module_knowledge_candidates c ON c.id = ec.candidate_id
            WHERE ec.entity_id = ?
            ORDER BY c.created_at, c.id
            """,
            (entity_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def link_candidate_to_entity(
        self,
        entity_id: str,
        candidate_id: str,
    ) -> dict:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO module_entity_candidates
              (id, entity_id, candidate_id, role)
            VALUES (?, ?, ?, 'reference')
            """,
            (new_id("modentcand"), entity_id, candidate_id),
        )
        return self.get_module_entity(entity_id)

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

    def create_module_campaign_import(
        self,
        *,
        campaign_id: str,
        module_id: str,
        created_by_member_id: str,
    ) -> dict:
        import_id = new_id("moduleimport")
        self.connection.execute(
            """
            INSERT INTO campaign_module_imports
              (id, campaign_id, module_id, status, created_by_member_id, applied_at)
            VALUES (?, ?, ?, 'applied', ?, CURRENT_TIMESTAMP)
            """,
            (import_id, campaign_id, module_id, created_by_member_id),
        )
        return self.get_module_campaign_import(import_id)

    def create_module_campaign_import_item(self, **values) -> dict:
        item_id = new_id("moduleimportitem")
        self.connection.execute(
            """
            INSERT INTO campaign_module_import_items
              (id, import_id, item_kind, module_entity_id, module_relation_id,
               module_candidate_id, travel_location_id, npc_id, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                values["import_id"],
                values["item_kind"],
                values.get("module_entity_id"),
                values.get("module_relation_id"),
                values.get("module_candidate_id"),
                values.get("travel_location_id"),
                values.get("npc_id"),
                values.get("payload_json", "{}"),
            ),
        )
        row = self.connection.execute(
            "SELECT * FROM campaign_module_import_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        return row_to_dict(row)

    def get_module_campaign_import(self, import_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM campaign_module_imports WHERE id = ?",
            (import_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Module campaign import not found: {import_id}")
        return row_to_dict(row)

    def list_module_campaign_imports(self, campaign_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT i.*, COUNT(item.id) AS item_count
            FROM campaign_module_imports i
            LEFT JOIN campaign_module_import_items item ON item.import_id = i.id
            WHERE i.campaign_id = ?
            GROUP BY i.id
            ORDER BY i.created_at, i.id
            """,
            (campaign_id,),
        ).fetchall()
        results = []
        for row in rows:
            result = row_to_dict(row)
            items = self.connection.execute(
                """
                SELECT * FROM campaign_module_import_items
                WHERE import_id = ?
                ORDER BY item_kind, id
                """,
                (str(result["id"]),),
            ).fetchall()
            result["items"] = [row_to_dict(item) for item in items]
            results.append(result)
        return results


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

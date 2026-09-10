"""Import approved module entities into the campaign travel graph and NPC profiles.

The module entity graph is authoritative knowledge. It only becomes campaign travel
facts or NPC availability constraints after an approved-source import batch is
confirmed by the KP. A confirm transaction revalidates the module graph, so a source
that became stale between preview and confirm fails closed instead of landing.

An approved module NPC entity is matched to the global NPC identity by normalized
name. It is never auto-linked to a campaign: NPCs that do not already belong to the
campaign are reported as skipped and require a separate KP decision.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

from ai_kp.application.npc_reappearance_service import NpcReappearanceService
from ai_kp.application.travel_graph_service import (
    TravelGraphService,
    TravelLocationCommand,
    TravelRouteCommand,
)

_ROUTE_RELATION_PREDICATES = frozenset({"located_at", "contains"})
_YEAR_PATTERN = re.compile(r"(?<!\d)(1[0-9]{3}|20[0-9]{2}|21[0-9]{2})(?!\d)")
_DEFAULT_ROUTE_MINUTES = {"located_at": 60, "contains": 10}


@dataclass(frozen=True)
class ImportLocationChoice:
    module_entity_id: str
    include: bool = True
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImportRouteChoice:
    module_relation_id: str
    include: bool = True
    travel_minutes: int | None = None
    travel_mode: str = "other"
    bidirectional: bool = True


@dataclass(frozen=True)
class ImportNpcChoice:
    module_entity_id: str
    include: bool = True
    born_year: int | None = None
    died_year: int | None = None
    active_from_year: int | None = None
    active_until_year: int | None = None


@dataclass(frozen=True)
class ModuleImportConfirmCommand:
    locations: tuple[ImportLocationChoice, ...] = ()
    routes: tuple[ImportRouteChoice, ...] = ()
    npcs: tuple[ImportNpcChoice, ...] = ()
    apply_time_constraints: bool = True


def normalize_npc_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class ModuleCampaignImportService:
    def __init__(self, repo):
        self.repo = repo

    def preview(self, campaign_id: str, module_id: str) -> dict:
        module = self._require_campaign_module(campaign_id, module_id)
        entities, relations = self._current_graph(module_id)
        npc_ids_by_name = {
            normalize_npc_name(str(row["name"])): str(row["id"])
            for row in self.repo.list_global_npcs()
        }
        campaign_npc_ids = {
            str(row["npc_id"])
            for row in self.repo.list_campaign_npc_ids(campaign_id)
        }

        locations = [
            {
                "module_entity_id": entity["id"],
                "name": entity["name"],
                "visibility": entity["visibility"],
                "spoiler_tag": entity.get("spoiler_tag"),
                "source_candidate_id": entity["source_candidate_id"],
            }
            for entity in entities
            if entity["entity_type"] == "location"
        ]

        routes = []
        npc_profiles = []
        npc_ids_by_entity: dict[str, str] = {}
        for relation in relations:
            predicate = str(relation["predicate"])
            if predicate not in _ROUTE_RELATION_PREDICATES:
                continue
            source = self._entity_by_id(entities, str(relation["source_entity_id"]))
            target = self._entity_by_id(entities, str(relation["target_entity_id"]))
            if source is None or target is None:
                continue
            if source["entity_type"] == "location" and target["entity_type"] == "location":
                routes.append(
                    {
                        "module_relation_id": relation["id"],
                        "from_entity_id": source["id"],
                        "from_name": source["name"],
                        "to_entity_id": target["id"],
                        "to_name": target["name"],
                        "predicate": predicate,
                        "default_minutes": _DEFAULT_ROUTE_MINUTES[predicate],
                    }
                )
                continue
            npc_entity = None
            location_entity = None
            if source["entity_type"] == "npc" and target["entity_type"] == "location":
                npc_entity, location_entity = source, target
            elif source["entity_type"] == "location" and target["entity_type"] == "npc":
                npc_entity, location_entity = target, source
            if npc_entity is None or location_entity is None:
                continue
            npc_id = npc_ids_by_name.get(normalize_npc_name(npc_entity["name"]))
            if npc_id is None:
                npc_id = self.repo.find_npc_by_name(npc_entity["name"])
            if npc_id is None:
                continue
            npc_ids_by_entity[str(npc_entity["id"])] = npc_id
            npc_profiles.append(
                {
                    "npc_entity_id": npc_entity["id"],
                    "npc_id": npc_id,
                    "npc_name": npc_entity["name"],
                    "location_name": location_entity["name"],
                    "location_entity_id": location_entity["id"],
                    "relation_id": relation["id"],
                    "predicate": predicate,
                    "linked_to_campaign": npc_id in campaign_npc_ids,
                    "source_candidate_id": npc_entity["source_candidate_id"],
                }
            )

        candidate_ids = {
            entity["source_candidate_id"]
            for entity in entities
            if entity["entity_type"] in {"npc", "location"}
        }
        time_constraints = []
        for candidate_id in sorted(candidate_ids):
            try:
                candidate = self.repo.get_module_knowledge_candidate(candidate_id)
            except KeyError:
                continue
            statement = str(candidate.get("statement") or "")
            evidence = " ".join(
                str(item.get("evidence_text") or "")
                for item in candidate.get("citations", ())
            )
            years = sorted(
                {
                    int(match)
                    for match in _YEAR_PATTERN.findall(f"{statement} {evidence}")
                }
            )
            if years:
                time_constraints.append(
                    {
                        "candidate_id": candidate_id,
                        "statement": statement[:200],
                        "years": years,
                    }
                )
        has_unlinked_npcs = any(
            not item.get("linked_to_campaign") for item in npc_profiles
        )
        return {
            "module_id": module_id,
            "module_title": module["title"],
            "campaign_id": campaign_id,
            "locations": locations,
            "routes": routes,
            "npc_profiles": npc_profiles,
            "time_constraints": time_constraints,
            "has_unlinked_npcs": has_unlinked_npcs,
        }

    def confirm(
        self,
        campaign_id: str,
        module_id: str,
        command: ModuleImportConfirmCommand,
        *,
        member_id: str,
    ) -> dict:
        self._require_campaign_module(campaign_id, module_id)
        self._validate_confirm_command(command)
        self.repo.begin_immediate()
        # Revalidate the graph inside the write transaction so stale or unapproved
        # sources fail closed instead of racing into the campaign facts.
        entities, relations = self._current_graph(module_id)
        entities_by_id = {str(entity["id"]): entity for entity in entities}
        relations_by_id = {str(relation["id"]): relation for relation in relations}
        npc_ids_by_name = {
            normalize_npc_name(str(row["name"])): str(row["id"])
            for row in self.repo.list_global_npcs()
        }

        travel = TravelGraphService(self.repo)
        npc_service = NpcReappearanceService(self.repo)

        created_locations: list[str] = []
        merged_locations: list[str] = []
        location_id_by_entity: dict[str, str] = {}
        for choice in command.locations:
            if not choice.include:
                continue
            entity = entities_by_id.get(choice.module_entity_id)
            if entity is None or entity["entity_type"] != "location":
                raise ValueError("导入的地点实体已失效或未通过来源审核")
            existing = self.repo.find_travel_location_by_normalized_name(
                campaign_id,
                TravelGraphService.normalize(entity["name"]),
            )
            if existing is not None:
                location_id_by_entity[choice.module_entity_id] = str(existing["id"])
                merged_locations.append(entity["name"])
                continue
            created = travel.create_location(
                campaign_id,
                TravelLocationCommand(
                    name=entity["name"],
                    aliases=tuple(choice.aliases),
                    source_kind="module",
                    source_ref=choice.module_entity_id,
                    kp_notes=f"来源：{entity['source_candidate_id']}",
                ),
            )
            location_id_by_entity[choice.module_entity_id] = str(created["id"])
            created_locations.append(entity["name"])

        created_routes: list[str] = []
        skipped_routes: list[str] = []
        for choice in command.routes:
            if not choice.include:
                continue
            relation = relations_by_id.get(choice.module_relation_id)
            if relation is None or relation["predicate"] not in _ROUTE_RELATION_PREDICATES:
                raise ValueError("导入的路线关系已失效或未通过来源审核")
            from_id = location_id_by_entity.get(str(relation["source_entity_id"]))
            to_id = location_id_by_entity.get(str(relation["target_entity_id"]))
            if from_id is None or to_id is None:
                skipped_routes.append(
                    f"{relation.get('source_name')} → {relation.get('target_name')}"
                )
                continue
            existing_route = self.repo.find_travel_route(
                campaign_id,
                from_id,
                to_id,
                choice.travel_mode,
            )
            if existing_route is not None:
                skipped_routes.append(
                    f"{relation.get('source_name')} → {relation.get('target_name')}"
                )
                continue
            travel_minutes = (
                choice.travel_minutes
                if choice.travel_minutes is not None
                else _DEFAULT_ROUTE_MINUTES[str(relation["predicate"])]
            )
            travel.create_route(
                campaign_id,
                TravelRouteCommand(
                    from_location_id=from_id,
                    to_location_id=to_id,
                    travel_minutes=travel_minutes,
                    travel_mode=choice.travel_mode,
                    bidirectional=choice.bidirectional,
                    kp_notes=f"来源：{relation['source_candidate_id']}",
                ),
            )
            created_routes.append(
                f"{relation.get('source_name')} → {relation.get('target_name')}"
            )

        npc_ids_by_entity: dict[str, str] = {}
        npc_location_names: dict[str, list[str]] = {}
        npc_choices: dict[str, ImportNpcChoice] = {}
        for relation in relations:
            predicate = str(relation["predicate"])
            if predicate not in _ROUTE_RELATION_PREDICATES:
                continue
            source = entities_by_id.get(str(relation["source_entity_id"]))
            target = entities_by_id.get(str(relation["target_entity_id"]))
            if source is None or target is None:
                continue
            npc_entity = None
            location_entity = None
            if source["entity_type"] == "npc" and target["entity_type"] == "location":
                npc_entity, location_entity = source, target
            elif source["entity_type"] == "location" and target["entity_type"] == "npc":
                npc_entity, location_entity = target, source
            if npc_entity is None or location_entity is None:
                continue
            npc_id = npc_ids_by_name.get(normalize_npc_name(npc_entity["name"]))
            if npc_id is None:
                continue
            npc_ids_by_entity[str(npc_entity["id"])] = npc_id
            npc_location_names.setdefault(npc_id, []).append(location_entity["name"])

        updated_npc_profiles: list[str] = []
        skipped_npcs: list[str] = []
        applied_time_constraints: list[dict] = []
        for choice in command.npcs:
            if not choice.include:
                continue
            npc_choices[choice.module_entity_id] = choice
            npc_id = npc_ids_by_entity.get(choice.module_entity_id)
            entity = entities_by_id.get(choice.module_entity_id)
            if npc_id is None or entity is None:
                skipped_npcs.append(str(entity["name"]) if entity else choice.module_entity_id)
                continue
            if not self.repo.npc_is_linked_to_campaign(campaign_id, npc_id):
                skipped_npcs.append(entity["name"])
                continue
            values: dict = {"lifecycle_state": "unknown"}
            location_names = npc_location_names.get(npc_id) or []
            if location_names:
                existing_profile = self.repo.get_npc_availability_profile(npc_id)
                existing_tags = (
                    list(existing_profile.get("location_tags") or [])
                    if existing_profile is not None
                    else []
                )
                values["location_tags"] = existing_tags + list(dict.fromkeys(location_names))
            if command.apply_time_constraints:
                for field in (
                    "born_year",
                    "died_year",
                    "active_from_year",
                    "active_until_year",
                ):
                    value = getattr(choice, field)
                    if value is not None:
                        values[field] = value
            if not values or values == {"lifecycle_state": "unknown"}:
                continue
            npc_service.save_availability_profile(campaign_id, npc_id, values)
            updated_npc_profiles.append(npc_id)
            applied_time_constraints.append({"npc_id": npc_id, "values": values})

        import_record = self.repo.create_module_campaign_import(
            campaign_id=campaign_id,
            module_id=module_id,
            created_by_member_id=member_id,
        )
        import_id = str(import_record["id"])
        for choice in command.locations:
            if not choice.include:
                continue
            entity = entities_by_id.get(choice.module_entity_id)
            if entity is None:
                continue
            self.repo.create_module_campaign_import_item(
                import_id=import_id,
                item_kind="location",
                module_entity_id=choice.module_entity_id,
                module_candidate_id=entity["source_candidate_id"],
                travel_location_id=location_id_by_entity.get(choice.module_entity_id),
                payload_json=json.dumps(
                    {"name": entity["name"]},
                    ensure_ascii=False,
                ),
            )
        for choice in command.routes:
            if not choice.include:
                continue
            relation = relations_by_id.get(choice.module_relation_id)
            if relation is None:
                continue
            self.repo.create_module_campaign_import_item(
                import_id=import_id,
                item_kind="route",
                module_relation_id=choice.module_relation_id,
                module_candidate_id=relation["source_candidate_id"],
                payload_json=json.dumps(
                    {
                        "source_name": relation.get("source_name"),
                        "target_name": relation.get("target_name"),
                    },
                    ensure_ascii=False,
                ),
            )
        for npc_id in updated_npc_profiles:
            entity_id = next(
                (
                    entity_key
                    for entity_key, linked_npc_id in npc_ids_by_entity.items()
                    if linked_npc_id == npc_id
                ),
                None,
            )
            if entity_id is None:
                continue
            entity = entities_by_id.get(entity_id)
            if entity is None:
                continue
            self.repo.create_module_campaign_import_item(
                import_id=import_id,
                item_kind="npc_profile",
                module_entity_id=entity_id,
                module_candidate_id=entity["source_candidate_id"],
                npc_id=npc_id,
                payload_json=json.dumps({"npc_id": npc_id}, ensure_ascii=False),
            )

        return {
            "import_id": import_id,
            "module_id": module_id,
            "created_locations": created_locations,
            "merged_locations": merged_locations,
            "created_routes": created_routes,
            "skipped_routes": skipped_routes,
            "updated_npc_profiles": updated_npc_profiles,
            "skipped_npcs": skipped_npcs,
            "time_constraints_applied": applied_time_constraints,
        }

    def list_imports(self, campaign_id: str) -> list[dict]:
        return self.repo.list_module_campaign_imports(campaign_id)

    def _require_campaign_module(self, campaign_id: str, module_id: str) -> dict:
        module = self.repo.get_module(module_id)
        if str(module.get("campaign_id")) != campaign_id:
            raise ValueError("模块不属于当前团，无法导入")
        return module

    def _current_graph(self, module_id: str) -> tuple[list[dict], list[dict]]:
        return (
            self.repo.list_module_entities(module_id),
            self.repo.list_module_relations(module_id),
        )

    @staticmethod
    def _validate_confirm_command(command: ModuleImportConfirmCommand) -> None:
        collections = (
            (
                "地点",
                (choice.module_entity_id for choice in command.locations),
            ),
            (
                "路线",
                (choice.module_relation_id for choice in command.routes),
            ),
            (
                "NPC",
                (choice.module_entity_id for choice in command.npcs),
            ),
        )
        for label, identifiers in collections:
            seen: set[str] = set()
            for identifier in identifiers:
                if identifier in seen:
                    raise ValueError(f"同一次导入不能重复选择{label}：{identifier}")
                seen.add(identifier)
        for choice in command.routes:
            if choice.travel_minutes is not None and choice.travel_minutes <= 0:
                raise ValueError("路线耗时必须大于 0 分钟")

    @staticmethod
    def _entity_by_id(entities: list[dict], entity_id: str) -> dict | None:
        return next(
            (entity for entity in entities if str(entity["id"]) == entity_id),
            None,
        )


__all__ = [
    "ImportLocationChoice",
    "ImportNpcChoice",
    "ImportRouteChoice",
    "ModuleCampaignImportService",
    "ModuleImportConfirmCommand",
]

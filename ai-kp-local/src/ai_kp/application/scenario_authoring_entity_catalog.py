"""Project confirmed source identities and explicit template dimensions, read-only."""

from typing import Any

from ai_kp.platform.resolution.authoring_entity_catalog import AuthoringEntityCandidate
from ai_kp.platform.scenes.builtin_setting_packs import get_setting_pack
from ai_kp.platform.scenes.setting_profiles import SettingProfileDocument, validate_setting_profile


def authoring_entity_catalog(
    repo: Any, module: dict, source_ids: set[str]
) -> dict[str, tuple[AuthoringEntityCandidate, ...]]:
    entities = repo.list_module_entities(module["id"])
    if not entities:
        return {}
    dimensions: dict[str, tuple[str, ...]] = {}
    floors: dict[str, str] = {}
    run = repo.get_active_campaign_module_run(module["campaign_id"])
    if run is not None and run["module_id"] == module["id"]:
        selection = repo.get_module_run_setting_selection(run["id"])
        if selection is not None:
            profile = selection["profile"]
            pack = get_setting_pack(profile["setting_pack_id"])
            if pack.pack_version != profile["setting_pack_version"]:
                raise ValueError("Selected setting pack version is unavailable for authoring")
            document = SettingProfileDocument.model_validate(profile["document"])
            validate_setting_profile(pack, document, entities)
            archetypes = {item.archetype_id: item for item in pack.entity_archetypes}
            for binding in document.entity_bindings:
                dimensions[binding.module_entity_id] = tuple(
                    archetypes[binding.archetype_id].state_dimensions
                )
    # Already realized entities own their actual dimensions, even after a profile
    # is edited. No current values or private history are sent as source evidence.
    for entity in repo.list_campaign_world_entities(module["campaign_id"]):
        if entity["origin_kind"] == "module_source":
            dimensions[entity["origin_ref"]] = tuple(
                entity.get("data", {}).get("state_dimensions") or ()
            )
            floors[entity["origin_ref"]] = entity["visibility"]
    candidates: dict[str, list[AuthoringEntityCandidate]] = {}
    citations: dict[str, set[str]] = {}
    for entity in sorted(entities, key=lambda item: item["id"]):
        candidate_id = entity["source_candidate_id"]
        if candidate_id not in citations:
            source = repo.get_module_knowledge_candidate(candidate_id)
            citations[candidate_id] = {item["chunk_id"] for item in source["citations"]}
        candidate = AuthoringEntityCandidate(
            module_entity_id=entity["id"],
            name=entity["name"],
            entity_type=entity["entity_type"],
            visibility=entity["visibility"],
            description=(entity.get("description") or "")[:500],
            state_dimensions=dimensions.get(entity["id"], ()),
            state_visibility_floor=floors.get(
                entity["id"], "secret" if entity["visibility"] == "secret" else "kp"
            ),
        )
        for source_id in sorted(citations[candidate_id] & source_ids):
            candidates.setdefault(source_id, []).append(candidate)
    if any(len(items) > 64 for items in candidates.values()):
        raise ValueError(
            "Source block has over 64 authoring entity candidates; split its source scope"
        )
    return {key: tuple(items) for key, items in sorted(candidates.items())}

"""Bounded server-owned identities offered to scenario authors."""

from pydantic import BaseModel, ConfigDict, Field

from ai_kp.platform.resolution.contracts import ScenarioContract


class AuthoringEntityCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    module_entity_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=240)
    entity_type: str = Field(min_length=1, max_length=80)
    visibility: str = Field(min_length=1, max_length=20)
    description: str = Field(default="", max_length=500)
    state_dimensions: tuple[str, ...] = Field(default=(), max_length=64)
    state_visibility_floor: str | None = None


def validate_authoring_entities(
    contract: ScenarioContract,
    catalog: dict[str, tuple[AuthoringEntityCandidate, ...]],
) -> None:
    from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler

    selected = {}
    for entity in contract.entities:
        if entity.module_entity_id is None:
            continue
        candidates = [
            candidate
            for ref in entity.source_refs
            for candidate in catalog.get(ref.source_block_id, ())
            if candidate.module_entity_id == entity.module_entity_id
        ]
        if not candidates:
            raise ValueError("Entity module identity was not offered in its cited evidence catalog")
        if any(candidate != candidates[0] for candidate in candidates[1:]):
            raise ValueError("Conflicting entity catalog snapshots")
        selected[entity.entity_id] = candidates[0]
    rank = {"table": 0, "kp": 1, "secret": 2}
    state_paths = {
        f"{root}.{entity_id}.{dimension}"
        for entity_id, candidate in selected.items()
        for dimension in candidate.state_dimensions
        for root in ("entities", "entity_runtime", "world_entities", "world_entity_states")
    }
    for _, command in ScenarioContractCompiler._all_commands(contract):
        if command.kind in {"set_fact", "remove_fact"}:
            path = str(command.path).removeprefix("facts.")
            if path in state_paths:
                raise ValueError(
                    "Template state must use set_world_entity_state, not a shadow fact"
                )
        if command.kind != "set_world_entity_state":
            continue
        candidate = selected.get(command.entity_id)
        if candidate is None or command.path not in candidate.state_dimensions:
            raise ValueError("World state dimension was not offered for this source entity")
        if rank[command.payload["visibility"]] < rank.get(candidate.state_visibility_floor, 2):
            raise ValueError("World state visibility is broader than its offered entity floor")

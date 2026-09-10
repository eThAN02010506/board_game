"""Pure, source-bound projection for AI and human KP director assistance."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from ai_kp.platform.resolution.action_catalog import (
    ScenarioActionCandidate,
    ScenarioActionCatalogProjector,
    ScenarioActionEvidence,
)
from ai_kp.platform.resolution.candidate_ranking import SemanticCandidateRanker
from ai_kp.platform.resolution.contracts import (
    ScenarioContract,
    ScenarioSnapshot,
    SkillChoice,
)
from ai_kp.platform.resolution.json_projection import (
    DIRECTOR_JSON_CONTAINER_ITEMS,
    DIRECTOR_JSON_MAX_DEPTH,
    DIRECTOR_JSON_STRING_CHARS,
    DIRECTOR_JSON_VALUE_BYTES,
    bounded_json_value,
    canonical_json_bytes,
    json_byte_size,
    searchable_json,
    shorten_json_string,
)

DIRECTOR_FACT_LIMIT = 24
DIRECTOR_ENTITY_LIMIT = 16
DIRECTOR_ACTOR_LOCATION_LIMIT = 16
DIRECTOR_RESOURCE_LIMIT = 16
DIRECTOR_CLOCK_LIMIT = 16
DIRECTOR_EVENT_LIMIT = 12

# The state projection is designed to leave useful context and answer room in a
# 16k-token model.  These are UTF-8 byte budgets over canonical compact JSON,
# rather than character counts, so CJK-heavy campaigns remain bounded too.
DIRECTOR_FACTS_JSON_BYTES = 4 * 1024
DIRECTOR_ENTITIES_JSON_BYTES = 6 * 1024
DIRECTOR_ACTOR_LOCATIONS_JSON_BYTES = 2 * 1024
DIRECTOR_RESOURCES_JSON_BYTES = 2 * 1024
DIRECTOR_CLOCKS_JSON_BYTES = 2 * 1024
DIRECTOR_EVENTS_JSON_BYTES = 4 * 1024
DIRECTOR_STATE_JSON_BYTES = 32 * 1024

_CANDIDATE_LIMIT = 8
_QUESTION_LIMIT = 2000
_SEARCH_PREVIEW_JSON_BYTES = 256
_FACT_KEY_JSON_BYTES = 512

# Per-field budgets prevent one otherwise-valid entity from consuming the
# entire entity group.  Stable hash suffixes retain distinguishability when a
# display value is shortened.
_ENTITY_STRING_JSON_BYTES = {
    "entity_id": 512,
    "title": 640,
    "status": 384,
    "location_id": 512,
    "emotional_state": 640,
    "physical_state": 640,
    "attitude": 640,
    "short_term_goal": 1024,
}


class DirectorBriefModel(BaseModel):
    """Strict immutable value object exposed by the director-brief boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


_DirectorBriefItem = TypeVar("_DirectorBriefItem", bound=DirectorBriefModel)


class DirectorBriefMetric(DirectorBriefModel):
    metric_id: str
    title: str
    value: int | float
    minimum_value: int | float | None = None
    maximum_value: int | float | None = None


class DirectorBriefEntityState(DirectorBriefModel):
    entity_id: str
    title: str
    entity_type: str
    status: str
    location_id: str | None = None
    emotional_state: str = ""
    physical_state: str = ""
    attitude: str = ""
    short_term_goal: str = ""
    truncated: bool = False
    truncated_fields: tuple[str, ...] = ()


class DirectorBriefActorLocation(DirectorBriefModel):
    actor_id: str
    location_id: str


class DirectorBriefJsonPreview(DirectorBriefModel):
    """A JSON value plus an unambiguous notice that it was projected."""

    value: Any
    truncated: bool = False
    key_truncated: bool = False


class DirectorBriefState(DirectorBriefModel):
    run_id: str
    contract_id: str
    scenario_version: int
    state_version: int
    status: Literal["active", "paused", "completed"]
    scene_id: str | None = None
    scene_title: str | None = None
    ending_id: str | None = None
    facts: dict[str, DirectorBriefJsonPreview] = Field(default_factory=dict)
    facts_total_count: int = Field(default=0, ge=0)
    facts_truncated: bool = False
    entities: tuple[DirectorBriefEntityState, ...] = ()
    entities_total_count: int = Field(default=0, ge=0)
    entities_truncated: bool = False
    actor_locations: tuple[DirectorBriefActorLocation, ...] = ()
    actor_locations_total_count: int = Field(default=0, ge=0)
    actor_locations_truncated: bool = False
    resources: tuple[DirectorBriefMetric, ...] = ()
    resources_total_count: int = Field(default=0, ge=0)
    resources_truncated: bool = False
    clocks: tuple[DirectorBriefMetric, ...] = ()
    clocks_total_count: int = Field(default=0, ge=0)
    clocks_truncated: bool = False
    recent_events: tuple[DirectorBriefJsonPreview, ...] = ()
    recent_events_total_count: int = Field(default=0, ge=0)
    recent_events_truncated: bool = False


class DirectorBriefEvidence(ScenarioActionEvidence):
    """Compatibility name for the shared contract-evidence value object."""


class DirectorBriefCandidate(DirectorBriefModel):
    candidate_id: str
    kind: Literal["operator", "task_method"]
    title: str
    score: int
    available: bool
    reason: str
    policy: str | None = None
    skill_choices: tuple[SkillChoice, ...] = ()
    automatic_information: tuple[str, ...] = ()
    maximum_effect: str = ""
    step_operator_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


class DirectorBrief(DirectorBriefModel):
    basis_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    question: str
    state: DirectorBriefState
    candidates: tuple[DirectorBriefCandidate, ...] = ()
    evidence: tuple[DirectorBriefEvidence, ...] = ()


class DirectorBriefProjector:
    """Project one immutable KP view without database access or state mutation."""

    @classmethod
    def project(
        cls,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        contract_hash: str,
        question: str,
    ) -> DirectorBrief:
        normalized_question = str(question or "").strip()
        if not normalized_question:
            raise ValueError("Director question is required")
        if len(normalized_question) > _QUESTION_LIMIT:
            raise ValueError("Director question is too long")
        normalized_hash = str(contract_hash or "").strip().casefold()
        if len(normalized_hash) != 64 or any(
            character not in "0123456789abcdef" for character in normalized_hash
        ):
            raise ValueError("Contract hash must be a 64-character hexadecimal digest")

        # model_copy(update=...) deliberately bypasses Pydantic validation.
        # Validate Python-native Any values before mode="json" can stringify
        # keys or turn sets into unstable arrays, then revalidate every typed
        # snapshot field as well.
        snapshot.validate_json_state()
        snapshot = ScenarioSnapshot.model_validate(snapshot.model_dump(mode="python"))

        catalog = ScenarioActionCatalogProjector.project(
            contract,
            snapshot,
            normalized_question,
            include_task_methods=True,
            limit=_CANDIDATE_LIMIT,
        )
        state = cls._state(
            contract,
            snapshot,
            question=normalized_question,
            ranked=catalog.candidates,
        )

        return DirectorBrief(
            basis_hash=cls._basis_hash(
                normalized_question,
                normalized_hash,
                snapshot,
                state,
            ),
            contract_hash=normalized_hash,
            question=normalized_question,
            state=state,
            candidates=tuple(
                DirectorBriefCandidate(
                    candidate_id=item.candidate_id,
                    kind=item.kind,
                    title=item.title,
                    score=item.score,
                    available=item.available,
                    reason=item.reason,
                    policy=item.policy,
                    skill_choices=item.skill_choices,
                    automatic_information=item.automatic_information,
                    maximum_effect=item.maximum_effect,
                    step_operator_ids=item.step_operator_ids,
                    evidence_ids=item.evidence_ids,
                )
                for item in catalog.candidates
            ),
            evidence=tuple(
                DirectorBriefEvidence(
                    evidence_id=item.evidence_id,
                    kind=item.kind,
                    record_id=item.record_id,
                    title=item.title,
                    summary=item.summary,
                    source_refs=item.source_refs,
                )
                for item in catalog.evidence
            ),
        )

    @classmethod
    def _state(
        cls,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        *,
        question: str,
        ranked: tuple[ScenarioActionCandidate, ...],
    ) -> DirectorBriefState:
        locations = {item.location_id: item for item in contract.locations}
        entities = {item.entity_id: item for item in contract.entities}
        resources = {item.resource_id: item for item in contract.resources}
        clocks = {item.clock_id: item for item in contract.clocks}
        references = cls._candidate_references(contract, ranked)
        dumped_snapshot = snapshot.model_dump(mode="json")
        projected_facts, facts_truncated = cls._project_facts(
            dumped_snapshot["facts"],
            question,
            referenced_fact_ids=references["facts"],
        )

        entity_ids = set(entities) | set(snapshot.entities) | set(snapshot.entity_runtime)

        def entity_location(entity_id: str) -> str | None:
            spec = entities.get(entity_id)
            runtime = snapshot.entity_runtime.get(entity_id)
            return (
                runtime.location_id
                if runtime is not None
                else spec.initial_location_id
                if spec is not None
                else None
            )

        ordered_entity_ids = sorted(
            entity_ids,
            key=lambda entity_id: (
                not (
                    entity_location(entity_id) == snapshot.scene_id
                    and entity_id in references["entities"]
                ),
                entity_id not in references["entities"],
                entity_location(entity_id) != snapshot.scene_id,
                -cls._relevance_score(
                    question,
                    entity_id,
                    entities[entity_id].title if entity_id in entities else "",
                    snapshot.entities.get(entity_id, ""),
                ),
                entity_id,
            ),
        )
        projected_entities = []
        for entity_id in ordered_entity_ids:
            spec = entities.get(entity_id)
            runtime = snapshot.entity_runtime.get(entity_id)
            projected_entities.append(
                cls._project_entity(
                    entity_id=entity_id,
                    title=spec.title if spec is not None else entity_id,
                    entity_type=spec.entity_type if spec is not None else "other",
                    status=snapshot.entities.get(
                        entity_id, spec.initial_status if spec is not None else "unknown"
                    ),
                    location_id=(
                        runtime.location_id
                        if runtime is not None
                        else spec.initial_location_id
                        if spec is not None
                        else None
                    ),
                    emotional_state=runtime.emotional_state if runtime else "",
                    physical_state=runtime.physical_state if runtime else "",
                    attitude=runtime.attitude if runtime else "",
                    short_term_goal=runtime.short_term_goal if runtime else "",
                )
            )
        bounded_entities, entities_truncated = cls._bounded_model_sequence(
            projected_entities,
            total_count=len(entity_ids),
            limit=DIRECTOR_ENTITY_LIMIT,
            json_budget=DIRECTOR_ENTITIES_JSON_BYTES,
        )

        ordered_actor_locations = sorted(
            snapshot.actor_locations.items(),
            key=lambda item: (
                not (
                    item[1] == snapshot.scene_id
                    and (item[0] in references["actors"] or item[0] in references["entities"])
                ),
                item[0] not in references["actors"],
                item[1] != snapshot.scene_id,
                -cls._relevance_score(question, item[0], item[1]),
                item[0],
            ),
        )
        bounded_actor_locations, actor_locations_truncated = cls._bounded_model_sequence(
            (
                DirectorBriefActorLocation(
                    actor_id=actor_id,
                    location_id=location_id,
                )
                for actor_id, location_id in ordered_actor_locations
            ),
            total_count=len(snapshot.actor_locations),
            limit=DIRECTOR_ACTOR_LOCATION_LIMIT,
            json_budget=DIRECTOR_ACTOR_LOCATIONS_JSON_BYTES,
        )

        ordered_resources = sorted(
            snapshot.resources.items(),
            key=lambda item: (
                item[0] not in references["resources"],
                -cls._relevance_score(
                    question,
                    item[0],
                    resources[item[0]].title if item[0] in resources else "",
                ),
                item[0],
            ),
        )
        bounded_resources, resources_truncated = cls._bounded_model_sequence(
            (
                DirectorBriefMetric(
                    metric_id=resource_id,
                    title=(
                        resources[resource_id].title if resource_id in resources else resource_id
                    ),
                    value=value,
                    minimum_value=(
                        resources[resource_id].minimum_value if resource_id in resources else None
                    ),
                    maximum_value=(
                        resources[resource_id].maximum_value if resource_id in resources else None
                    ),
                )
                for resource_id, value in ordered_resources
            ),
            total_count=len(snapshot.resources),
            limit=DIRECTOR_RESOURCE_LIMIT,
            json_budget=DIRECTOR_RESOURCES_JSON_BYTES,
        )

        ordered_clocks = sorted(
            snapshot.clocks.items(),
            key=lambda item: (
                item[0] not in references["clocks"],
                -cls._relevance_score(
                    question,
                    item[0],
                    clocks[item[0]].title if item[0] in clocks else "",
                ),
                item[0],
            ),
        )
        bounded_clocks, clocks_truncated = cls._bounded_model_sequence(
            (
                DirectorBriefMetric(
                    metric_id=clock_id,
                    title=clocks[clock_id].title if clock_id in clocks else clock_id,
                    value=value,
                    minimum_value=0,
                    maximum_value=(clocks[clock_id].maximum_value if clock_id in clocks else None),
                )
                for clock_id, value in ordered_clocks
            ),
            total_count=len(snapshot.clocks),
            limit=DIRECTOR_CLOCK_LIMIT,
            json_budget=DIRECTOR_CLOCKS_JSON_BYTES,
        )

        recent_events, events_truncated = cls._project_recent_events(dumped_snapshot["events"])
        state = DirectorBriefState(
            run_id=snapshot.run_id,
            contract_id=snapshot.contract_id,
            scenario_version=snapshot.scenario_version,
            state_version=snapshot.run_version,
            status=snapshot.status,
            scene_id=snapshot.scene_id,
            scene_title=(
                locations[snapshot.scene_id].title if snapshot.scene_id in locations else None
            ),
            ending_id=snapshot.ending_id,
            facts=projected_facts,
            facts_total_count=len(dumped_snapshot["facts"]),
            facts_truncated=facts_truncated,
            entities=bounded_entities,
            entities_total_count=len(entity_ids),
            entities_truncated=entities_truncated,
            actor_locations=bounded_actor_locations,
            actor_locations_total_count=len(snapshot.actor_locations),
            actor_locations_truncated=actor_locations_truncated,
            resources=bounded_resources,
            resources_total_count=len(snapshot.resources),
            resources_truncated=resources_truncated,
            clocks=bounded_clocks,
            clocks_total_count=len(snapshot.clocks),
            clocks_truncated=clocks_truncated,
            recent_events=recent_events,
            recent_events_total_count=len(dumped_snapshot["events"]),
            recent_events_truncated=events_truncated,
        )
        state_size = json_byte_size(state.model_dump(mode="json"))
        if state_size > DIRECTOR_STATE_JSON_BYTES:
            raise ValueError("Director brief state projection exceeded its JSON budget")
        return state

    @staticmethod
    def _project_entity(
        *,
        entity_id: str,
        title: str,
        entity_type: str,
        status: str,
        location_id: str | None,
        emotional_state: str,
        physical_state: str,
        attitude: str,
        short_term_goal: str,
    ) -> DirectorBriefEntityState:
        raw_fields: dict[str, str | None] = {
            "entity_id": entity_id,
            "title": title,
            "status": status,
            "location_id": location_id,
            "emotional_state": emotional_state,
            "physical_state": physical_state,
            "attitude": attitude,
            "short_term_goal": short_term_goal,
        }
        projected: dict[str, str | None] = {}
        truncated_fields: list[str] = []
        for field_name, raw_value in raw_fields.items():
            if raw_value is None:
                projected[field_name] = None
                continue
            bounded, truncated = shorten_json_string(
                raw_value,
                json_budget=_ENTITY_STRING_JSON_BYTES[field_name],
            )
            projected[field_name] = bounded
            if truncated:
                truncated_fields.append(field_name)
        return DirectorBriefEntityState(
            **projected,
            entity_type=entity_type,
            truncated=bool(truncated_fields),
            truncated_fields=tuple(truncated_fields),
        )

    @classmethod
    def _project_facts(
        cls,
        facts: dict[str, Any],
        question: str,
        *,
        referenced_fact_ids: set[str],
    ) -> tuple[dict[str, DirectorBriefJsonPreview], bool]:
        ranked_facts = sorted(
            facts.items(),
            key=lambda item: (
                item[0] not in referenced_fact_ids,
                -cls._relevance_score(
                    question,
                    item[0],
                    searchable_json(
                        item[1],
                        json_budget=_SEARCH_PREVIEW_JSON_BYTES,
                    ),
                ),
                item[0],
            ),
        )
        selected: dict[str, DirectorBriefJsonPreview] = {}
        used_bytes = 2  # {}
        nested_truncated = False
        for fact_key, raw_value in ranked_facts:
            if len(selected) >= DIRECTOR_FACT_LIMIT:
                break
            value, value_truncated = bounded_json_value(
                raw_value,
                json_budget=DIRECTOR_JSON_VALUE_BYTES,
            )
            projected_key, key_truncated = shorten_json_string(
                fact_key,
                json_budget=_FACT_KEY_JSON_BYTES,
            )
            if projected_key in selected:
                digest = hashlib.sha256(fact_key.encode("utf-8")).hexdigest()
                for collision_index in range(len(selected) + 1):
                    candidate_key = f"#fact-{digest}-{collision_index}"
                    if candidate_key not in selected:
                        projected_key = candidate_key
                        break
                key_truncated = True
            preview = DirectorBriefJsonPreview(
                value=value,
                truncated=value_truncated,
                key_truncated=key_truncated,
            )
            key_size = json_byte_size(projected_key)
            preview_size = json_byte_size(preview.model_dump(mode="json"))
            addition = (
                (1 if selected else 0)
                + key_size
                + 1  # colon
                + preview_size
            )
            if used_bytes + addition > DIRECTOR_FACTS_JSON_BYTES:
                nested_truncated = True
                continue
            selected[projected_key] = preview
            used_bytes += addition
            nested_truncated = nested_truncated or value_truncated or key_truncated
        return selected, nested_truncated or len(selected) < len(facts)

    @classmethod
    def _project_recent_events(
        cls,
        events: list[dict[str, Any]],
    ) -> tuple[tuple[DirectorBriefJsonPreview, ...], bool]:
        selected_newest_first: list[DirectorBriefJsonPreview] = []
        used_bytes = 2  # []
        nested_truncated = False
        for raw_event in reversed(events[-DIRECTOR_EVENT_LIMIT:]):
            value, value_truncated = bounded_json_value(
                raw_event,
                json_budget=DIRECTOR_JSON_VALUE_BYTES,
            )
            preview = DirectorBriefJsonPreview(
                value=value,
                truncated=value_truncated,
            )
            preview_size = json_byte_size(preview.model_dump(mode="json"))
            addition = (1 if selected_newest_first else 0) + preview_size
            if used_bytes + addition > DIRECTOR_EVENTS_JSON_BYTES:
                nested_truncated = True
                continue
            selected_newest_first.append(preview)
            used_bytes += addition
            nested_truncated = nested_truncated or value_truncated
        selected_newest_first.reverse()
        return (
            tuple(selected_newest_first),
            nested_truncated or len(selected_newest_first) < len(events),
        )

    @classmethod
    def _bounded_model_sequence(
        cls,
        items: Iterable[_DirectorBriefItem],
        *,
        total_count: int,
        limit: int,
        json_budget: int,
    ) -> tuple[tuple[_DirectorBriefItem, ...], bool]:
        selected: list[_DirectorBriefItem] = []
        used_bytes = 2  # []
        for item in items:
            if len(selected) >= limit:
                break
            item_size = json_byte_size(item.model_dump(mode="json"))
            addition = (1 if selected else 0) + item_size
            if used_bytes + addition > json_budget:
                continue
            selected.append(item)
            used_bytes += addition
        return tuple(selected), len(selected) < total_count

    @staticmethod
    def _relevance_score(question: str, *texts: str) -> int:
        # Candidate ranking already owns the project's Unicode/CJK feature
        # semantics.  Inputs are clipped here because snapshot keys are not
        # length-constrained at the authoritative schema boundary.
        return SemanticCandidateRanker._score(
            question,
            tuple(str(text)[:DIRECTOR_JSON_STRING_CHARS] for text in texts),
        )

    @classmethod
    def _candidate_references(
        cls,
        contract: ScenarioContract,
        ranked: tuple[ScenarioActionCandidate, ...],
    ) -> dict[str, set[str]]:
        references: dict[str, set[str]] = {
            "facts": set(),
            "entities": set(),
            "actors": set(),
            "resources": set(),
            "clocks": set(),
        }
        operators = {item.operator_id: item for item in contract.operators}
        methods = {item.method_id: item for item in contract.task_methods}
        selected_operator_ids: set[str] = set()
        for item in ranked:
            if item.kind == "operator":
                selected_operator_ids.add(item.candidate_id)
                continue
            method = methods[item.candidate_id]
            selected_operator_ids.update(step.operator_id for step in method.steps)
            for condition in method.preconditions:
                cls._add_path_reference(references, condition.path)

        obligations = {item.obligation_id: item for item in contract.response_obligations}
        for operator_id in selected_operator_ids:
            operator = operators[operator_id]
            for condition in operator.preconditions:
                cls._add_path_reference(references, condition.path)
            for cue in operator.narrative_cues:
                if cue.speaker_entity_id:
                    references["entities"].add(cue.speaker_entity_id)
            for obligation_id in operator.response_obligation_ids:
                references["entities"].add(obligations[obligation_id].entity_id)
            commands = (
                *operator.always_commands,
                *operator.success_commands,
                *operator.failure_commands,
                *(command for branch in operator.outcome_branches for command in branch.commands),
            )
            for command in commands:
                if command.kind in {"set_fact", "remove_fact"} and command.path:
                    fact_path = command.path.removeprefix("facts.")
                    fact_id = fact_path.split(".", 1)[0]
                    if fact_id:
                        references["facts"].add(fact_id)
                if command.entity_id:
                    references["entities"].add(command.entity_id)
                if command.actor_id:
                    references["actors"].add(command.actor_id)
                    references["entities"].add(command.actor_id)
                if command.clock_id:
                    references["clocks"].add(command.clock_id)
                if command.kind == "adjust_resource" and command.path:
                    references["resources"].add(command.path)
        return references

    @staticmethod
    def _add_path_reference(
        references: dict[str, set[str]],
        path: str,
    ) -> None:
        root, separator, identifier = path.partition(".")
        if not separator:
            return
        identifier = identifier.split(".", 1)[0]
        target = {
            "facts": "facts",
            "entities": "entities",
            "entity_runtime": "entities",
            "actor_locations": "actors",
            "resources": "resources",
            "clocks": "clocks",
        }.get(root)
        if target is not None and identifier:
            references[target].add(identifier)

    @staticmethod
    def _basis_hash(
        question: str,
        contract_hash: str,
        snapshot: ScenarioSnapshot,
        state: DirectorBriefState,
    ) -> str:
        encoded = canonical_json_bytes(
            {
                "question": question,
                "contract_hash": contract_hash,
                "snapshot": snapshot.model_dump(mode="json"),
                "state_projection": state.model_dump(mode="json"),
            }
        )
        return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "DIRECTOR_ACTOR_LOCATIONS_JSON_BYTES",
    "DIRECTOR_ACTOR_LOCATION_LIMIT",
    "DIRECTOR_CLOCKS_JSON_BYTES",
    "DIRECTOR_CLOCK_LIMIT",
    "DIRECTOR_ENTITIES_JSON_BYTES",
    "DIRECTOR_ENTITY_LIMIT",
    "DIRECTOR_EVENTS_JSON_BYTES",
    "DIRECTOR_EVENT_LIMIT",
    "DIRECTOR_FACTS_JSON_BYTES",
    "DIRECTOR_FACT_LIMIT",
    "DIRECTOR_JSON_CONTAINER_ITEMS",
    "DIRECTOR_JSON_MAX_DEPTH",
    "DIRECTOR_JSON_STRING_CHARS",
    "DIRECTOR_JSON_VALUE_BYTES",
    "DIRECTOR_RESOURCES_JSON_BYTES",
    "DIRECTOR_RESOURCE_LIMIT",
    "DIRECTOR_STATE_JSON_BYTES",
    "DirectorBrief",
    "DirectorBriefActorLocation",
    "DirectorBriefCandidate",
    "DirectorBriefEntityState",
    "DirectorBriefEvidence",
    "DirectorBriefJsonPreview",
    "DirectorBriefMetric",
    "DirectorBriefProjector",
    "DirectorBriefState",
]

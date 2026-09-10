"""Compact model-facing IR and deterministic ScenarioContract assembly."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any, TypeVar

from ai_kp.platform.resolution.authoritative_failure_stakes import (
    authoritative_failure_stakes,
)
from ai_kp.platform.resolution.check_catalog import ScenarioCheckCatalog
from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    ClockSpec,
    ClueSpec,
    ConsequenceSignalBand,
    ConsequenceSignalSpec,
    EndingRule,
    EntitySpec,
    LocationLink,
    LocationSpec,
    OutcomeBranch,
    OutcomeNarrativeCue,
    PressureTrackSpec,
    ReactivePolicy,
    ReactiveRule,
    ResourceSpec,
    ResponseObligation,
    ScenarioContract,
    SkillChoice,
    SourceRef,
    StateCondition,
    TaskMethod,
    TriggerRule,
    WorldCommand,
)
from ai_kp.platform.resolution.effect_catalog import ScenarioEffectCatalog
from ai_kp.platform.resolution.investigation_hub import (
    InvestigationEvidence,
    materialize_investigation_hub,
)
from ai_kp.platform.resolution.kernel import operator_outcome_path
from ai_kp.platform.resolution.location_travel import (
    materialize_location_travel_operators,
)
from ai_kp.platform.resolution.narrative_safety import narration_claims_success
from ai_kp.platform.resolution.scenario_action_scope import (
    ActionScopeLocation,
    bind_action_location_scope,
    resolve_partition_action_location_slots,
)
from ai_kp.platform.resolution.scenario_authoring_audit import (
    bounded_authoring_assumptions,
)
from ai_kp.platform.resolution.scenario_entity_identity import (
    converge_scenario_entity_identities,
)
from ai_kp.platform.resolution.scenario_ir_models import (
    IrAction,
    IrClue,
    IrConsequenceSignal,
    IrEnding,
    IrEntity,
    IrLocation,
    IrLocationLink,
    IrReactivePolicy,
    IrSourced,
    IrTaskMethod,
    ScenarioCheckMapping,
    ScenarioIrAssembly,
    ScenarioIrBatch,
    ScenarioIrNormalization,
)
from ai_kp.platform.resolution.scenario_location_identity import (
    reconcile_location_identities,
)
from ai_kp.platform.resolution.scenario_scene_graph_supplement import (
    retain_source_grounded_location_links,
)
from ai_kp.platform.resolution.source_clue_delivery import (
    materialize_source_declared_clue_delivery,
)
from ai_kp.platform.resolution.source_handout_delivery import (
    materialize_source_handout_delivery,
)
from ai_kp.platform.resolution.source_outcomes import (
    OutcomeKey,
    is_action_goal_boundary_command,
    normalized_source_contains,
    source_authorizes_push,
    unspecified_outcome_summary,
)
from ai_kp.platform.resolution.source_scene_locations import (
    materialize_source_scene_locations,
)
from ai_kp.platform.resolution.source_scene_opportunities import (
    materialize_source_scene_opportunities,
)
from ai_kp.platform.resolution.source_scene_transitions import (
    materialize_source_scene_transitions,
)
from ai_kp.platform.resolution.source_secret_passages import (
    materialize_source_secret_passages,
)
from ai_kp.platform.resolution.source_structure_navigation import (
    materialize_source_structure_navigation,
)
from ai_kp.platform.resolution.world_state_effect_ir import lower_world_state_effects

SourcedT = TypeVar("SourcedT", bound=IrSourced)


class ScenarioIrAssembler:
    """Merge independent partitions deterministically; never ask a model to bind authority."""

    def assemble(
        self,
        batches: tuple[ScenarioIrBatch, ...],
        *,
        source_refs: dict[str, SourceRef],
        contract_id: str,
        source_version: int,
        ruleset_id: str,
        title: str,
        corpus_truncated: bool,
        check_catalog: ScenarioCheckCatalog | None = None,
        effect_catalog: ScenarioEffectCatalog | None = None,
        source_texts: dict[str, str] | None = None,
        source_titles: dict[str, str] | None = None,
        source_section_paths: dict[str, tuple[str, ...]] | None = None,
        source_scene_keys: dict[str, str] | None = None,
        source_semantic_kinds: dict[str, str] | None = None,
        external_action_locations: dict[str, str] | None = None,
        external_initial_scene_id: str | None = None,
        external_locations: tuple[LocationSpec, ...] = (),
        external_entities: tuple[EntitySpec, ...] = (),
        infer_action_location_ids: tuple[str, ...] = (),
        external_operator_ids: tuple[str, ...] = (),
        external_produced_paths: tuple[str, ...] = (),
        allow_empty_actions: bool = False,
    ) -> ScenarioIrAssembly:
        if not batches:
            raise ValueError("Scenario IR assembly requires at least one batch")
        assumptions = [
            item
            for batch in batches
            for item in batch.assumptions
            if item.strip()
        ]
        action_location_inference_ids = set(infer_action_location_ids)
        batches = resolve_partition_action_location_slots(batches, assumptions)
        batches = tuple(batch.model_copy(update={
            "actions": tuple(lower_world_state_effects(action) for action in batch.actions)
        }) for batch in batches)
        batches = materialize_source_scene_locations(
            batches,
            source_texts=source_texts,
            source_scene_keys=source_scene_keys,
            source_semantic_kinds=source_semantic_kinds,
            assumptions=assumptions,
        )
        batches = reconcile_location_identities(
            batches,
            source_section_paths=source_section_paths,
            source_texts=source_texts,
            source_titles=source_titles,
            source_scene_keys=source_scene_keys,
            source_semantic_kinds=source_semantic_kinds,
            protected_location_ids=frozenset(
                item.location_id for item in external_locations
            ),
            assumptions=assumptions,
        )
        batches = self._namespace_partition_response_obligations(batches, assumptions)
        proposed_initial_scene_ids = tuple(
            dict.fromkeys(
                batch.initial_scene_id
                for batch in batches
                if batch.initial_scene_id
            )
        )
        initial_scene_id = external_initial_scene_id or (
            proposed_initial_scene_ids[0] if proposed_initial_scene_ids else None
        )
        if external_initial_scene_id and any(
            item != external_initial_scene_id for item in proposed_initial_scene_ids
        ):
            assumptions.append(
                "Supplement proposed a different initial scene; base authority was retained."
            )
        scene_ids = {
            batch.initial_scene_id for batch in batches if batch.initial_scene_id
        }
        if len(scene_ids) > 1:
            assumptions.append(
                "Source partitions proposed conflicting initial scenes; the first was retained."
            )
        initial_facts: dict[str, Any] = {}
        for batch in batches:
            for key, value in batch.initial_facts.items():
                if key in initial_facts and initial_facts[key] != value:
                    assumptions.append(
                        f"Source partitions proposed conflicting initial fact: {key}"
                    )
                    continue
                initial_facts[key] = value

        locations = self._merge(
            (item for batch in batches for item in batch.locations), "id", assumptions
        )
        links = self._merge(
            (item for batch in batches for item in batch.location_links),
            ("from_id", "to_id", "one_way"),
            assumptions,
        )
        entities = self._merge(
            (item for batch in batches for item in batch.entities), "id", assumptions
        )
        clocks = self._merge(
            (item for batch in batches for item in batch.clocks), "id", assumptions
        )
        resources = self._merge(
            (item for batch in batches for item in batch.resources), "id", assumptions
        )
        clues = self._merge(
            (item for batch in batches for item in batch.clues), "id", assumptions
        )
        actions = self._merge(
            (item for batch in batches for item in batch.actions), "id", assumptions
        )
        methods = self._merge(
            (item for batch in batches for item in batch.task_methods), "id", assumptions
        )
        policies = self._merge(
            (item for batch in batches for item in batch.reactive_policies),
            "id",
            assumptions,
        )
        signals = self._merge(
            (item for batch in batches for item in batch.consequence_signals),
            "id",
            assumptions,
        )
        endings = self._merge(
            (item for batch in batches for item in batch.endings), "id", assumptions
        )
        authored_location_ids = {item.id for item in locations}
        location_ids = {
            *authored_location_ids,
            *(item.location_id for item in external_locations),
        }
        location_by_id = {item.id: item for item in locations}
        action_location_by_id: dict[str, IrLocation | ActionScopeLocation] = {
            location_id: ActionScopeLocation(id=location_id, title=location_title)
            for location_id, location_title in (external_action_locations or {}).items()
        }
        for location in locations:
            action_location_by_id[location.id] = location
        authored_entity_ids = {item.id for item in entities}
        entity_ids = {
            *authored_entity_ids,
            *(item.entity_id for item in external_entities),
        }
        clock_ids = {item.id for item in clocks}
        resource_ids = {item.id for item in resources}
        if initial_scene_id not in location_ids:
            if initial_scene_id is not None:
                assumptions.append(
                    f"Unknown proposed initial scene was discarded: {initial_scene_id}"
                )
            initial_scene_id = None
        if (
            initial_scene_id is not None
            and len(locations) > 1
            and location_by_id[initial_scene_id].visibility == "hidden"
        ):
            replacement = next(
                (
                    candidate_id
                    for candidate_id in proposed_initial_scene_ids
                    if candidate_id in location_by_id
                    and location_by_id[candidate_id].visibility in {"known", "visited"}
                ),
                None,
            )
            assumptions.append(
                "A hidden location cannot be the unexplained player entry point; "
                + (
                    f"the visible proposal {replacement} was retained."
                    if replacement is not None
                    else "the proposed initial scene was discarded."
                )
            )
            initial_scene_id = replacement
        if initial_scene_id is None and len(locations) == 1:
            initial_scene_id = locations[0].id
        proven_initial_scene_id = (
            external_initial_scene_id
            if initial_scene_id == external_initial_scene_id
            else None
        )

        links = tuple(
            item
            for item in links
            if self._keep_location_link(item, location_ids, assumptions)
        )
        entities = tuple(
            item
            if item.location_id is None or item.location_id in location_ids
            else self._clear_unknown_entity_location(item, assumptions)
            for item in entities
        )
        entities = self._normalize_response_obligation_ids(entities, assumptions)
        response_obligation_ids = {
            obligation.id
            for entity in entities
            for obligation in entity.response_obligations
        }
        check_mappings: list[ScenarioCheckMapping] = []
        normalizations: list[ScenarioIrNormalization] = []
        safe_actions: list[IrAction] = []
        for item in actions:
            safe_action = self._safe_action(
                item,
                location_ids=location_ids,
                action_location_by_id=action_location_by_id,
                entity_ids=entity_ids,
                clock_ids=clock_ids,
                resource_ids=resource_ids,
                response_obligation_ids=response_obligation_ids,
                assumptions=assumptions,
                check_catalog=check_catalog,
                effect_catalog=effect_catalog,
                ruleset_id=ruleset_id,
                check_mappings=check_mappings,
                source_texts=source_texts,
                source_titles=source_titles,
                source_section_paths=source_section_paths,
                source_scene_keys=source_scene_keys,
                proven_initial_scene_id=proven_initial_scene_id,
                infer_unselected_action_location=(
                    item.id in action_location_inference_ids
                ),
            )
            if safe_action is not None:
                safe_actions.append(safe_action)
        actions = self._discard_unreferenced_noop_actions(
            tuple(safe_actions),
            clues=clues,
            methods=methods,
            links=links,
            policies=policies,
            signals=signals,
            endings=endings,
            assumptions=assumptions,
        )
        action_ids = {item.id for item in actions}
        methods = tuple(
            item
            for item in methods
            if self._keep_method(item, action_ids, assumptions)
        )
        safe_policies: list[IrReactivePolicy] = []
        for item in policies:
            safe_policy = self._safe_policy(
                item,
                entity_ids=entity_ids,
                location_ids=location_ids,
                clock_ids=clock_ids,
                resource_ids=resource_ids,
                assumptions=assumptions,
            )
            if safe_policy is not None:
                safe_policies.append(safe_policy)
        policies = tuple(safe_policies)
        produced_paths = self._ir_produced_paths(
            initial_facts,
            actions,
            policies,
            entity_ids=entity_ids,
            clock_ids=clock_ids,
            resource_ids=resource_ids,
        )
        produced_paths.update(
            operator_outcome_path(operator_id)
            for operator_id in external_operator_ids
        )
        produced_paths.update(external_produced_paths)
        safe_signals: list[IrConsequenceSignal] = []
        for item in signals:
            safe_signal = self._safe_signal(
                item,
                produced_paths=produced_paths,
                clock_ids=clock_ids,
                resource_ids=resource_ids,
                entity_ids=entity_ids,
                assumptions=assumptions,
                normalizations=normalizations,
            )
            if safe_signal is not None:
                safe_signals.append(safe_signal)
        signals = tuple(safe_signals)
        safe_endings: list[IrEnding] = []
        for item in endings:
            safe_ending = self._safe_ending(
                item,
                produced_paths=produced_paths,
                location_ids=location_ids,
                entity_ids=entity_ids,
                clock_ids=clock_ids,
                resource_ids=resource_ids,
                assumptions=assumptions,
            )
            if safe_ending is not None:
                safe_endings.append(safe_ending)
        endings = tuple(safe_endings)
        clues = self._safe_clues(
            clues,
            actions,
            initial_facts=initial_facts,
            assumptions=assumptions,
        )
        actions = self._bind_clue_content(actions, clues)

        def refs(item: IrSourced) -> tuple[SourceRef, ...]:
            unknown = sorted(set(item.source_block_ids) - source_refs.keys())
            if unknown:
                raise ValueError(f"IR record cites unknown source blocks: {unknown}")
            return tuple(
                source_refs[source_id]
                for source_id in dict.fromkeys(item.source_block_ids)
            )

        contract = ScenarioContract(
            contract_id=contract_id,
            source_version=source_version,
            ruleset_id=ruleset_id,
            title=title,
            initial_scene_id=initial_scene_id,
            initial_facts=initial_facts,
            locations=(
                *tuple(
                    item
                    for item in external_locations
                    if item.location_id not in authored_location_ids
                ),
                *tuple(LocationSpec(
                    location_id=item.id,
                    title=item.title,
                    initial_visibility=(
                        "visited" if item.id == initial_scene_id else "hidden"
                    ),
                    tags=item.tags,
                    source_refs=refs(item),
                )
                for item in locations),
            ),
            location_links=tuple(
                LocationLink(
                    from_location_id=item.from_id,
                    to_location_id=item.to_id,
                    one_way=item.one_way,
                    preconditions=self._conditions(
                        item.preconditions,
                        clock_ids=clock_ids,
                        location_ids=location_ids,
                        entity_ids=entity_ids,
                    ),
                    source_refs=refs(item),
                )
                for item in links
            ),
            entities=(
                *tuple(
                    item
                    for item in external_entities
                    if item.entity_id not in authored_entity_ids
                ),
                *tuple(EntitySpec(
                    entity_id=item.id,
                    module_entity_id=item.module_entity_id,
                    entity_type=item.type,
                    title=item.title,
                    initial_status=item.status,
                    initial_location_id=item.location_id,
                    canonical_profile=item.canonical_profile,
                    derived_profile=item.derived_profile,
                    source_refs=refs(item),
                )
                for item in entities),
            ),
            clocks=tuple(
                ClockSpec(
                    clock_id=item.id,
                    title=item.title,
                    clock_kind=item.kind,
                    initial_value=item.initial,
                    maximum_value=item.maximum,
                    source_refs=refs(item),
                )
                for item in clocks
            ),
            resources=tuple(
                ResourceSpec(
                    resource_id=item.id,
                    title=item.title,
                    initial_value=item.initial,
                    minimum_value=item.minimum,
                    maximum_value=item.maximum,
                    source_refs=refs(item),
                )
                for item in resources
            ),
            clues=tuple(
                ClueSpec(
                    clue_id=item.id,
                    title=item.title,
                    importance=item.importance,
                    discovery_operator_ids=item.discovery_action_ids,
                    fact_path=self._fact_path(item.fact_path),
                    fact_value=item.fact_value,
                    recoverable=item.recoverable,
                    public_content=item.public_content,
                    source_refs=refs(item),
                )
                for item in clues
            ),
            operators=tuple(
                ActionOperator(
                    operator_id=item.id,
                    title=item.title,
                    intent_hints=tuple(dict.fromkeys(item.intent_hints)),
                    public_setup=item.public_setup,
                    narrative_cues=item.narrative_cues,
                    policy=item.policy,
                    preconditions=self._conditions(
                        item.preconditions,
                        clock_ids=clock_ids,
                        location_ids=location_ids,
                        entity_ids=entity_ids,
                    ),
                    skill_choices=item.checks,
                    automatic_information=item.automatic_information,
                    response_obligation_ids=item.response_obligation_ids,
                    always_commands=self._commands(item.always),
                    success_commands=self._commands(item.on_success),
                    failure_commands=self._commands(item.on_failure),
                    outcome_branches=(
                        (
                            OutcomeBranch(
                                outcome_key="pushed_failure",
                                commands=self._commands(item.on_pushed_failure),
                            ),
                        )
                        if item.on_pushed_failure
                        else ()
                    ),
                    rationale=item.rationale,
                    maximum_effect=item.maximum_effect,
                    clarification_prompt=item.clarification_prompt,
                    source_refs=refs(item),
                )
                for item in actions
            ),
            task_methods=tuple(
                TaskMethod(
                    method_id=item.id,
                    task_key=item.task_key,
                    title=item.title,
                    intent_hints=tuple(dict.fromkeys(item.intent_hints)),
                    preconditions=self._conditions(
                        item.preconditions,
                        clock_ids=clock_ids,
                        location_ids=location_ids,
                        entity_ids=entity_ids,
                    ),
                    steps=item.steps,
                    source_refs=refs(item),
                )
                for item in methods
            ),
            reactive_policies=tuple(
                ReactivePolicy(
                    policy_id=item.id,
                    entity_id=item.entity_id,
                    rules=tuple(
                        self._reactive_rule(
                            rule,
                            clock_ids=clock_ids,
                            location_ids=location_ids,
                            entity_ids=entity_ids,
                        )
                        for rule in item.rules
                        if rule.trigger != "semantic_event"
                    ),
                    source_refs=refs(item),
                )
                for item in policies
                if any(rule.trigger != "semantic_event" for rule in item.rules)
            ),
            response_obligations=tuple(
                ResponseObligation(
                    obligation_id=obligation.id,
                    entity_id=entity.id,
                    trigger_topics=obligation.trigger_topics,
                    conditions=self._conditions(
                        obligation.conditions,
                        clock_ids=clock_ids,
                        location_ids=location_ids,
                        entity_ids=entity_ids,
                    ),
                    facts_to_convey=obligation.facts_to_convey,
                    state_to_express=obligation.state_to_express,
                    physical_behaviors=obligation.physical_behaviors,
                    boundaries=obligation.boundaries,
                    automatic_information=obligation.automatic_information,
                    source_refs=refs(entity),
                )
                for entity in entities
                for obligation in entity.response_obligations
            ),
            trigger_rules=tuple(
                TriggerRule(
                    trigger_id=f"{policy.id}.{rule.rule_id}",
                    event_type=str(rule.event_type),
                    target_entity_id=rule.target_entity_id or policy.entity_id,
                    priority=rule.priority,
                    conditions=self._conditions(
                        rule.conditions,
                        clock_ids=clock_ids,
                        location_ids=location_ids,
                        entity_ids=entity_ids,
                    ),
                    commands=self._commands(rule.commands),
                    rationale=rule.rationale,
                    source_refs=refs(policy),
                )
                for policy in policies
                for rule in policy.rules
                if rule.trigger == "semantic_event"
            ),
            pressure_tracks=tuple(
                PressureTrackSpec(
                    pressure_id=f"pressure.{item.id}",
                    title=item.title,
                    clock_id=item.id,
                    visibility=item.pressure_visibility,
                    display_mode=item.pressure_display_mode,
                    stages=item.pressure_stages,
                    source_refs=refs(item),
                )
                for item in clocks
                if item.pressure_visibility is not None and item.pressure_stages
            ),
            consequence_signals=tuple(
                ConsequenceSignalSpec(
                    signal_id=item.id,
                    title=item.title,
                    visibility=item.visibility,
                    display_mode=item.display_mode,
                    source_path=self._signal_source_path(
                        item.source_path,
                        clock_ids=clock_ids,
                        resource_ids=resource_ids,
                        entity_ids=entity_ids,
                    ),
                    public_title=item.public_title,
                    public_summary=item.public_summary,
                    bands=tuple(self._signal_band(band) for band in item.bands),
                    source_refs=refs(item),
                )
                for item in signals
            ),
            endings=tuple(
                EndingRule(
                    ending_id=item.id,
                    title=item.title,
                    priority=item.priority,
                    all_conditions=self._conditions(
                        item.all_conditions,
                        clock_ids=clock_ids,
                        location_ids=location_ids,
                        entity_ids=entity_ids,
                    ),
                    any_conditions=self._conditions(
                        item.any_conditions,
                        clock_ids=clock_ids,
                        location_ids=location_ids,
                        entity_ids=entity_ids,
                    ),
                    commands=self._commands(item.commands),
                    source_refs=refs(item),
                )
                for item in endings
            ),
        )
        if source_texts is not None:
            contract = retain_source_grounded_location_links(
                contract,
                source_texts=source_texts,
            )
            contract = materialize_source_structure_navigation(
                contract,
                source_texts=source_texts,
                source_section_paths=source_section_paths or {},
                source_scene_keys=source_scene_keys,
            )
            contract = materialize_source_handout_delivery(
                contract,
                source_refs=source_refs,
                source_texts=source_texts,
                source_section_paths=source_section_paths or {},
            )
            contract = materialize_investigation_hub(
                contract,
                tuple(
                    InvestigationEvidence(
                        source_ref=source_ref,
                        title=(source_titles or {}).get(source_id, ""),
                        text=source_texts[source_id],
                        section_path=(source_section_paths or {}).get(source_id, ()),
                        scene_key=(source_scene_keys or {}).get(source_id),
                    )
                    for source_id, source_ref in source_refs.items()
                    if source_id in source_texts
                ),
            )
            contract = materialize_source_scene_opportunities(
                contract,
                source_refs=source_refs,
                source_texts=source_texts,
                source_section_paths=source_section_paths or {},
                source_scene_keys=source_scene_keys,
            )
            contract = materialize_source_scene_transitions(
                contract,
                source_refs=source_refs,
                source_texts=source_texts,
                source_section_paths=source_section_paths or {},
                source_scene_keys=source_scene_keys,
            )
            contract = materialize_source_secret_passages(
                contract,
                source_refs=source_refs,
                source_texts=source_texts,
                source_section_paths=source_section_paths or {},
                source_scene_keys=source_scene_keys,
            )
        contract = materialize_location_travel_operators(contract)
        entity_convergence = converge_scenario_entity_identities(contract)
        contract = entity_convergence.contract
        assumptions.extend(entity_convergence.diagnostics)
        if source_texts is not None:
            contract = materialize_source_declared_clue_delivery(
                contract,
                source_texts=source_texts,
            )

        if not contract.operators and not allow_empty_actions:
            raise ValueError("Assembled scenario contract requires an executable action")
        if corpus_truncated:
            assumptions.append("The source corpus exceeded the bounded authoring window.")
        confidence = (
            "low"
            if any(batch.confidence == "low" for batch in batches)
            else "medium"
            if any(batch.confidence == "medium" for batch in batches)
            else "high"
        )
        return ScenarioIrAssembly(
            contract=contract,
            confidence=confidence,
            assumptions=bounded_authoring_assumptions(assumptions),
            check_mappings=tuple(check_mappings),
            normalizations=tuple(normalizations),
        )

    @classmethod
    def _bind_clue_content(
        cls,
        actions: tuple[IrAction, ...],
        clues: tuple[IrClue, ...],
    ) -> tuple[IrAction, ...]:
        """Attach clue payloads only to already-authoritative success commits."""

        actions_by_id = {action.id: action for action in actions}
        automatic_by_action: dict[str, list[str]] = {}
        success_by_action: dict[str, list[str]] = {}
        for clue in clues:
            expected_path = cls._fact_path(clue.fact_path)
            for action_id in clue.discovery_action_ids:
                action = actions_by_id.get(action_id)
                if action is None:
                    continue
                always_commits = any(
                    command.kind == "set_fact"
                    and cls._fact_path(command.path or "") == expected_path
                    and type(command.value) is type(clue.fact_value)
                    and command.value == clue.fact_value
                    for command in action.always
                )
                success_commits = any(
                    command.kind == "set_fact"
                    and cls._fact_path(command.path or "") == expected_path
                    and type(command.value) is type(clue.fact_value)
                    and command.value == clue.fact_value
                    for command in action.on_success
                )
                if always_commits:
                    automatic_by_action.setdefault(action_id, []).extend(
                        clue.public_content
                    )
                elif success_commits:
                    success_by_action.setdefault(action_id, []).extend(
                        clue.public_content
                    )

        bound: list[IrAction] = []
        for action in actions:
            update: dict[str, Any] = {}
            automatic = automatic_by_action.get(action.id, ())
            if automatic:
                update["automatic_information"] = tuple(
                    dict.fromkeys((*action.automatic_information, *automatic))
                )
            success_content = tuple(
                dict.fromkeys(success_by_action.get(action.id, ()))
            )
            existing_success = next(
                (
                    cue
                    for cue in action.narrative_cues
                    if cue.outcome_key == "success"
                ),
                None,
            )
            if (
                len(success_content) == 1
                and existing_success is None
                and len(action.narrative_cues) < 8
            ):
                update["public_setup"] = (
                    action.public_setup or "该行动将按来源约定结算可公开信息。"
                )
                update["narrative_cues"] = (
                    *action.narrative_cues,
                    OutcomeNarrativeCue(
                        outcome_key="success",
                        public_summary=success_content[0],
                    ),
                )
            bound.append(action.model_copy(update=update) if update else action)
        return tuple(bound)

    @staticmethod
    def _merge(
        records: Iterable[SourcedT],
        key_fields: str | tuple[str, ...],
        assumptions: list[str],
    ) -> tuple[SourcedT, ...]:
        fields = (key_fields,) if isinstance(key_fields, str) else key_fields
        merged: dict[tuple[Any, ...], SourcedT] = {}
        for record in records:
            key = tuple(getattr(record, field) for field in fields)
            previous = merged.get(key)
            if previous is None:
                merged[key] = record
                continue
            previous_body = previous.model_dump(exclude={"source_block_ids"})
            current_body = record.model_dump(exclude={"source_block_ids"})
            if previous_body != current_body:
                assumptions.append(
                    "Source partitions proposed conflicting definitions for "
                    + "/".join(str(part) for part in key)
                )
                continue
            merged[key] = previous.model_copy(
                update={
                    "source_block_ids": tuple(
                        dict.fromkeys(
                            (*previous.source_block_ids, *record.source_block_ids)
                        )
                    )[:8]
                }
            )
        return tuple(merged.values())

    @staticmethod
    def _keep_location_link(
        link: IrLocationLink,
        location_ids: set[str],
        assumptions: list[str],
    ) -> bool:
        if link.from_id in location_ids and link.to_id in location_ids:
            return True
        assumptions.append(
            f"Location link with unknown endpoint was discarded: {link.from_id}->{link.to_id}"
        )
        return False

    @staticmethod
    def _clear_unknown_entity_location(
        entity: IrEntity,
        assumptions: list[str],
    ) -> IrEntity:
        assumptions.append(
            f"Unknown initial location was removed from entity {entity.id}: "
            f"{entity.location_id}"
        )
        return entity.model_copy(update={"location_id": None})

    @staticmethod
    def _normalize_response_obligation_ids(
        entities: tuple[IrEntity, ...], assumptions: list[str]
    ) -> tuple[IrEntity, ...]:
        """Prevent cross-entity obligation IDs from overwriting one another.

        Exact duplicates owned by the same entity are one logical obligation.
        Conflicting duplicates are given a deterministic entity-scoped ID; this
        preserves both source-derived performances without guessing which body an
        action meant. Existing action references retain the first definition.
        """

        owners: dict[str, tuple[str, dict[str, Any]]] = {}
        used_ids: set[str] = set()
        normalized_entities: list[IrEntity] = []
        for entity in entities:
            obligations = []
            for obligation in entity.response_obligations:
                body = obligation.model_dump(exclude={"id"})
                previous = owners.get(obligation.id)
                if previous is None:
                    owners[obligation.id] = (entity.id, body)
                    used_ids.add(obligation.id)
                    obligations.append(obligation)
                    continue
                if previous == (entity.id, body):
                    assumptions.append(
                        "Logically equivalent response obligation was merged: "
                        f"{obligation.id}"
                    )
                    continue
                digest = hashlib.sha256(
                    f"{entity.id}\0{obligation.id}".encode()
                ).hexdigest()[:10]
                stem = f"{obligation.id}@{entity.id}"
                renamed_id = f"{stem[:149]}-{digest}"
                counter = 2
                while renamed_id in used_ids:
                    suffix = f"-{counter}"
                    renamed_id = f"{stem[:160 - len(suffix)]}{suffix}"
                    counter += 1
                used_ids.add(renamed_id)
                owners[renamed_id] = (entity.id, body)
                obligations.append(obligation.model_copy(update={"id": renamed_id}))
                assumptions.append(
                    "Conflicting response obligation ID was safely renamed: "
                    f"{obligation.id} -> {renamed_id}"
                )
            normalized_entities.append(
                entity.model_copy(update={"response_obligations": tuple(obligations)})
            )
        return tuple(normalized_entities)

    @staticmethod
    def _namespace_partition_response_obligations(
        batches: tuple[ScenarioIrBatch, ...], assumptions: list[str]
    ) -> tuple[ScenarioIrBatch, ...]:
        """Rename cross-partition collisions while references are still local."""

        owners: dict[str, tuple[str, dict[str, Any]]] = {}
        used_ids: set[str] = set()
        normalized_batches: list[ScenarioIrBatch] = []
        for batch in batches:
            occurrence_counts: dict[str, int] = {}
            for entity in batch.entities:
                for obligation in entity.response_obligations:
                    occurrence_counts[obligation.id] = (
                        occurrence_counts.get(obligation.id, 0) + 1
                    )
            renames: dict[str, str] = {}
            ambiguous: set[str] = set()
            entities: list[IrEntity] = []
            for entity in batch.entities:
                obligations = []
                for obligation in entity.response_obligations:
                    body = obligation.model_dump(exclude={"id"})
                    previous = owners.get(obligation.id)
                    if previous is None or previous == (entity.id, body):
                        owners.setdefault(obligation.id, (entity.id, body))
                        used_ids.add(obligation.id)
                        obligations.append(obligation)
                        continue
                    digest = hashlib.sha256(
                        f"{entity.id}\0{obligation.id}".encode()
                    ).hexdigest()[:10]
                    stem = f"{obligation.id}@{entity.id}"
                    renamed_id = f"{stem[:149]}-{digest}"
                    counter = 2
                    while renamed_id in used_ids:
                        suffix = f"-{counter}"
                        renamed_id = f"{stem[:160 - len(suffix)]}{suffix}"
                        counter += 1
                    used_ids.add(renamed_id)
                    owners[renamed_id] = (entity.id, body)
                    obligations.append(
                        obligation.model_copy(update={"id": renamed_id})
                    )
                    if occurrence_counts[obligation.id] == 1:
                        renames[obligation.id] = renamed_id
                    else:
                        ambiguous.add(obligation.id)
                    assumptions.append(
                        "Conflicting response obligation ID was safely renamed: "
                        f"{obligation.id} -> {renamed_id}"
                    )
                entities.append(
                    entity.model_copy(
                        update={"response_obligations": tuple(obligations)}
                    )
                )
            actions = tuple(
                action.model_copy(
                    update={
                        "response_obligation_ids": tuple(
                            renames.get(obligation_id, obligation_id)
                            for obligation_id in action.response_obligation_ids
                            if obligation_id not in ambiguous
                        )
                    }
                )
                for action in batch.actions
            )
            if ambiguous:
                assumptions.append(
                    "Ambiguous response obligation references were rejected: "
                    + ", ".join(sorted(ambiguous))
                )
            normalized_batches.append(
                batch.model_copy(
                    update={"entities": tuple(entities), "actions": actions}
                )
            )
        return tuple(normalized_batches)

    @classmethod
    def _safe_action(
        cls,
        action: IrAction,
        *,
        location_ids: set[str],
        action_location_by_id: dict[str, IrLocation | ActionScopeLocation],
        entity_ids: set[str],
        clock_ids: set[str],
        resource_ids: set[str],
        response_obligation_ids: set[str],
        assumptions: list[str],
        check_catalog: ScenarioCheckCatalog | None,
        effect_catalog: ScenarioEffectCatalog | None,
        ruleset_id: str,
        check_mappings: list[ScenarioCheckMapping],
        source_texts: dict[str, str] | None,
        source_titles: dict[str, str] | None,
        source_section_paths: dict[str, tuple[str, ...]] | None,
        source_scene_keys: dict[str, str] | None,
        proven_initial_scene_id: str | None,
        infer_unselected_action_location: bool,
    ) -> IrAction | None:
        canonical_conditions = cls._conditions(
            action.preconditions,
            location_ids=set(action_location_by_id),
            entity_ids=entity_ids,
        )
        action = bind_action_location_scope(
            action,
            canonical_conditions=canonical_conditions,
            location_by_id=action_location_by_id,
            source_texts=source_texts,
            source_titles=source_titles,
            source_section_paths=source_section_paths,
            source_scene_keys=source_scene_keys,
            proven_initial_scene_id=proven_initial_scene_id,
            infer_unselected_location=infer_unselected_action_location,
            assumptions=assumptions,
        )
        if action is None:
            return None
        policy = action.policy
        always = cls._safe_commands(
            action.always,
            owner=action.id,
            location_ids=location_ids,
            entity_ids=entity_ids,
            clock_ids=clock_ids,
            resource_ids=resource_ids,
            assumptions=assumptions,
        )
        success = cls._safe_commands(
            action.on_success,
            owner=action.id,
            location_ids=location_ids,
            entity_ids=entity_ids,
            clock_ids=clock_ids,
            resource_ids=resource_ids,
            assumptions=assumptions,
        )
        failure = cls._safe_commands(
            action.on_failure,
            owner=action.id,
            location_ids=location_ids,
            entity_ids=entity_ids,
            clock_ids=clock_ids,
            resource_ids=resource_ids,
            assumptions=assumptions,
        )
        pushed_failure = cls._safe_commands(
            action.on_pushed_failure,
            owner=action.id,
            location_ids=location_ids,
            entity_ids=entity_ids,
            clock_ids=clock_ids,
            resource_ids=resource_ids,
            assumptions=assumptions,
        )
        clarification = action.clarification_prompt
        public_setup = action.public_setup
        narrative_cues = cls._safe_narrative_cues(
            action,
            source_texts=source_texts,
            assumptions=assumptions,
        )
        safe_response_obligation_ids = tuple(
            obligation_id
            for obligation_id in action.response_obligation_ids
            if obligation_id in response_obligation_ids
        )
        unknown_response_obligations = sorted(
            set(action.response_obligation_ids) - response_obligation_ids
        )
        if unknown_response_obligations:
            assumptions.append(
                f"Unknown optional response obligations were removed from action "
                f"{action.id}: {unknown_response_obligations}"
            )
        checks = cls._safe_checks(
            action,
            failure_commands=failure,
            check_catalog=check_catalog,
            effect_catalog=effect_catalog,
            ruleset_id=ruleset_id,
            assumptions=assumptions,
            check_mappings=check_mappings,
            source_texts=source_texts,
        )
        check_policies = {
            "required_check",
            "optional_check",
            "conditional_check",
            "opposed_check",
        }
        if policy in check_policies and not checks:
            assumptions.append(
                f"Action {action.id} without a source-provable skill choice was discarded."
            )
            return None
        if policy in {"clarification", "impossible"}:
            always = success = failure = pushed_failure = ()
        if public_setup and narration_claims_success(public_setup):
            assumptions.append(
                f"Outcome-claiming public setup was removed from action {action.id}."
            )
            public_setup = ""
        if narrative_cues and not public_setup:
            public_setup = "The action enters resolution."
        return action.model_copy(
            update={
                "policy": policy,
                "always": always,
                "on_success": success,
                "on_failure": failure,
                "on_pushed_failure": pushed_failure,
                "clarification_prompt": clarification,
                "public_setup": public_setup,
                "narrative_cues": narrative_cues,
                "checks": checks,
                "response_obligation_ids": safe_response_obligation_ids,
            }
        )

    @staticmethod
    def _safe_narrative_cues(
        action: IrAction,
        *,
        source_texts: dict[str, str] | None,
        assumptions: list[str],
    ) -> tuple[OutcomeNarrativeCue, ...]:
        if not action.narrative_cues:
            return ()
        cited_texts = (
            tuple(
                source_texts[source_id]
                for source_id in action.source_block_ids
                if source_id in source_texts
            )
            if source_texts is not None
            else ()
        )
        safe: list[OutcomeNarrativeCue] = []
        for cue in action.narrative_cues:
            if cue.outcome_key not in {"success", "failure", "pushed_failure"}:
                assumptions.append(
                    f"Narrative cue with unsupported outcome was discarded from "
                    f"action {action.id}: {cue.outcome_key}"
                )
                continue
            if cue.outcome_key == "failure" and narration_claims_success(
                cue.public_summary
            ):
                assumptions.append(
                    f"Success-claiming failure narrative cue was discarded from "
                    f"action {action.id}."
                )
                continue
            server_boundary = (
                cue.outcome_key in {"success", "failure"}
                and cue.public_summary
                == unspecified_outcome_summary(action.title, cue.outcome_key)
                and ScenarioIrAssembler._has_action_goal_boundary(
                    action,
                    cue.outcome_key,
                )
            )
            if not server_boundary and not any(
                normalized_source_contains(text, cue.public_summary)
                for text in cited_texts
            ):
                assumptions.append(
                    f"Source-unsupported narrative cue was discarded from action "
                    f"{action.id}: {cue.outcome_key}"
                )
                continue
            safe.append(
                cue.model_copy(update={"speaker_entity_id": None, "tone": ""})
            )
        return tuple(safe)

    @staticmethod
    def _safe_checks(
        action: IrAction,
        *,
        failure_commands: tuple[WorldCommand, ...],
        check_catalog: ScenarioCheckCatalog | None,
        effect_catalog: ScenarioEffectCatalog | None,
        ruleset_id: str,
        assumptions: list[str],
        check_mappings: list[ScenarioCheckMapping],
        source_texts: dict[str, str] | None,
    ) -> tuple[SkillChoice, ...]:
        if check_catalog is None:
            if action.abstract_checks:
                assumptions.append(
                    f"Action {action.id} has abstract checks but no ruleset catalog."
                )
            return action.checks

        resolved: list[SkillChoice] = []
        for choice in action.checks:
            keys = check_catalog.resolve(choice.skill_key)
            if len(keys) != 1:
                assumptions.append(
                    f"Action {action.id} proposed unknown or ambiguous check key: "
                    f"{choice.skill_key}"
                )
                continue
            resolved.append(choice.model_copy(update={"skill_key": keys[0]}))
        for abstract in action.abstract_checks:
            keys = check_catalog.resolve(abstract.term)
            check_mappings.append(
                ScenarioCheckMapping(
                    action_id=action.id,
                    source_term=abstract.term,
                    resolved_skill_keys=keys,
                    status="resolved" if keys else "unresolved",
                )
            )
            if not keys:
                assumptions.append(
                    f"Action {action.id} has an unresolved abstract check: {abstract.term}"
                )
                continue
            resolved.extend(
                SkillChoice(
                    skill_key=key,
                    difficulty=abstract.difficulty,
                    reason=abstract.reason,
                    hidden=abstract.hidden,
                    allow_push=abstract.allow_push,
                    failure_stakes=abstract.failure_stakes,
                    pushed_failure_stakes=abstract.pushed_failure_stakes,
                )
                for key in keys
            )
        unique: dict[tuple[str, str, bool], SkillChoice] = {}
        for choice in resolved:
            unique.setdefault(
                (choice.skill_key, choice.difficulty, choice.hidden), choice
            )
        if source_texts is None:
            return tuple(unique.values())
        cited_texts = tuple(
            source_texts[source_id]
            for source_id in action.source_block_ids
            if source_id in source_texts
        )
        allowed_keys = {
            key
            for text in cited_texts
            for key in check_catalog.source_present_keys(text)
        }
        unsupported = sorted(
            {choice.skill_key for choice in unique.values()} - allowed_keys
        )
        if unsupported:
            assumptions.append(
                f"Action {action.id} source-absent skill choices were discarded: "
                f"{unsupported}"
            )
        safe_choices: list[SkillChoice] = []
        for choice in unique.values():
            if choice.skill_key not in allowed_keys:
                continue
            authoritative_fallback = authoritative_failure_stakes(
                action_id=action.id,
                action_title=action.title,
                commands=failure_commands,
                ruleset_id=ruleset_id,
                effect_catalog=effect_catalog,
            )
            failure_stakes = (
                choice.failure_stakes
                if (
                    choice.failure_stakes
                    == unspecified_outcome_summary(action.title, "failure")
                    and authoritative_fallback == choice.failure_stakes
                )
                or any(
                    normalized_source_contains(text, choice.failure_stakes)
                    for text in cited_texts
                )
                else authoritative_fallback
            )
            pushed_failure_stakes = (
                choice.pushed_failure_stakes
                if any(
                    normalized_source_contains(text, choice.pushed_failure_stakes)
                    for text in cited_texts
                )
                else ""
            )
            allow_push = choice.allow_push and any(
                source_authorizes_push(text) for text in cited_texts
            )
            if choice.failure_stakes and not failure_stakes:
                assumptions.append(
                    f"Source-unsupported failure stakes were removed from action "
                    f"{action.id}: {choice.skill_key}"
                )
            if choice.pushed_failure_stakes and not pushed_failure_stakes:
                assumptions.append(
                    f"Source-unsupported pushed-failure stakes were removed from "
                    f"action {action.id}: {choice.skill_key}"
                )
            safe_choices.append(
                choice.model_copy(
                    update={
                        "allow_push": allow_push,
                        "failure_stakes": failure_stakes,
                        "pushed_failure_stakes": (
                            pushed_failure_stakes if allow_push else ""
                        ),
                    }
                )
            )
        return tuple(safe_choices)

    @staticmethod
    def _has_action_goal_boundary(
        action: IrAction,
        outcome: OutcomeKey,
    ) -> bool:
        commands = (
            action.on_success
            if outcome == "success"
            else action.on_failure
            if outcome == "failure"
            else action.on_pushed_failure
        )
        return any(
            is_action_goal_boundary_command(
                command,
                action_id=action.id,
                outcome=outcome,
            )
            for command in commands
        )

    @classmethod
    def _discard_unreferenced_noop_actions(
        cls,
        actions: tuple[IrAction, ...],
        *,
        clues: tuple[IrClue, ...],
        methods: tuple[IrTaskMethod, ...],
        links: tuple[IrLocationLink, ...],
        policies: tuple[IrReactivePolicy, ...],
        signals: tuple[IrConsequenceSignal, ...],
        endings: tuple[IrEnding, ...],
        assumptions: list[str],
    ) -> tuple[IrAction, ...]:
        referenced_ids = {
            action_id for clue in clues for action_id in clue.discovery_action_ids
        }
        referenced_ids.update(
            step.operator_id for method in methods for step in method.steps
        )
        condition_paths = {
            condition.path
            for action in actions
            for condition in action.preconditions
        }
        condition_paths.update(
            condition.path for link in links for condition in link.preconditions
        )
        condition_paths.update(
            condition.path for method in methods for condition in method.preconditions
        )
        condition_paths.update(
            condition.path
            for policy in policies
            for rule in policy.rules
            for condition in rule.conditions
        )
        condition_paths.update(
            condition.path
            for signal in signals
            for band in signal.bands
            for condition in band.all_conditions
        )
        condition_paths.update(
            condition.path
            for ending in endings
            for condition in (*ending.all_conditions, *ending.any_conditions)
        )
        condition_paths.update(
            signal.source_path for signal in signals if signal.source_path is not None
        )

        kept: list[IrAction] = []
        discarded: list[IrAction] = []
        for action in actions:
            no_authoritative_result = not (
                action.checks
                or action.abstract_checks
                or action.always
                or action.on_success
                or action.on_failure
                or action.on_pushed_failure
            )
            referenced = (
                action.id in referenced_ids
                or operator_outcome_path(action.id) in condition_paths
            )
            if action.policy == "automatic" and no_authoritative_result and not referenced:
                assumptions.append(
                    f"Unreferenced no-op action was discarded: {action.id}"
                )
                discarded.append(action)
                continue
            kept.append(action)
        if not kept and discarded:
            restored = discarded[0]
            kept.append(restored)
            assumptions.remove(
                f"Unreferenced no-op action was discarded: {restored.id}"
            )
        return tuple(kept)

    @classmethod
    def _safe_commands(
        cls,
        commands: tuple[WorldCommand, ...],
        *,
        owner: str,
        location_ids: set[str],
        entity_ids: set[str],
        clock_ids: set[str],
        resource_ids: set[str],
        assumptions: list[str],
    ) -> tuple[WorldCommand, ...]:
        safe: list[WorldCommand] = []
        for command in commands:
            valid = True
            if command.kind in {
                "activate_contract_overlay",
                "register_entity",
                "register_clock",
                "register_resource",
            }:
                valid = False
            elif command.kind in {"set_scene", "move_actor"}:
                valid = command.value in location_ids
            elif command.kind in {"set_entity_status", "update_entity_runtime"}:
                valid = command.entity_id in entity_ids
            elif command.kind == "advance_clock":
                valid = command.clock_id in clock_ids
            elif command.kind == "adjust_resource":
                valid = command.path in resource_ids
            if not valid:
                assumptions.append(
                    f"Command {command.kind} with an unknown or forbidden target was "
                    f"discarded from {owner}."
                )
                continue
            safe.append(cls._command(command))
        return tuple(safe)

    @staticmethod
    def _keep_method(
        method: IrTaskMethod,
        action_ids: set[str],
        assumptions: list[str],
    ) -> bool:
        unknown = sorted({step.operator_id for step in method.steps} - action_ids)
        if not unknown:
            return True
        assumptions.append(
            f"Task method {method.id} with unknown actions was discarded: {unknown}"
        )
        return False

    @staticmethod
    def _safe_policy(
        policy: IrReactivePolicy,
        *,
        entity_ids: set[str],
        location_ids: set[str],
        clock_ids: set[str],
        resource_ids: set[str],
        assumptions: list[str],
    ) -> IrReactivePolicy | None:
        if policy.entity_id not in entity_ids:
            assumptions.append(
                f"Reactive policy {policy.id} with unknown entity was discarded: "
                f"{policy.entity_id}"
            )
            return None
        rules: list[ReactiveRule] = []
        for rule in policy.rules:
            commands = ScenarioIrAssembler._safe_commands(
                rule.commands,
                owner=f"{policy.id}/{rule.rule_id}",
                location_ids=location_ids,
                entity_ids=entity_ids,
                clock_ids=clock_ids,
                resource_ids=resource_ids,
                assumptions=assumptions,
            )
            if not commands:
                assumptions.append(
                    f"Reactive rule without a safe command was discarded: {rule.rule_id}"
                )
                continue
            rules.append(
                rule.model_copy(
                    update={
                        "conditions": ScenarioIrAssembler._conditions(
                            rule.conditions,
                            clock_ids=clock_ids,
                            location_ids=location_ids,
                            entity_ids=entity_ids,
                        ),
                        "commands": commands,
                    }
                )
            )
        if not rules:
            return None
        return policy.model_copy(update={"rules": tuple(rules)})

    @classmethod
    def _safe_signal(
        cls,
        signal: IrConsequenceSignal,
        *,
        produced_paths: set[str],
        clock_ids: set[str],
        resource_ids: set[str],
        entity_ids: set[str],
        assumptions: list[str],
        normalizations: list[ScenarioIrNormalization],
    ) -> IrConsequenceSignal | None:
        source_path = cls._signal_source_path(
            signal.source_path,
            clock_ids=clock_ids,
            resource_ids=resource_ids,
            entity_ids=entity_ids,
        )
        bands = tuple(
            cls._signal_band(band, clock_ids=clock_ids)
            for band in signal.bands
        )
        seen_band_ids: set[str] = set()
        unique_bands: list[ConsequenceSignalBand] = []
        for band in bands:
            if band.band_id in seen_band_ids:
                continue
            seen_band_ids.add(band.band_id)
            unique_bands.append(band)
        if len(unique_bands) != len(bands):
            bands = tuple(unique_bands)
            normalizations.append(
                ScenarioIrNormalization(
                    code="duplicate_signal_bands_removed",
                    record_kind="consequence_signals",
                    record_id=signal.id,
                )
            )
        if signal.display_mode == "exact" and source_path is None:
            signal = signal.model_copy(update={"display_mode": "stage"})
            normalizations.append(
                ScenarioIrNormalization(
                    code="exact_signal_precision_downgraded",
                    record_kind="consequence_signals",
                    record_id=signal.id,
                )
            )
        if signal.visibility == "table":
            visible_bands = tuple(band for band in bands if band.player_visible)
            public_paths = (
                *(condition.path for band in bands for condition in band.all_conditions),
                *([source_path] if source_path is not None else []),
            )
            allowed_public_roots = {
                "facts",
                "entities",
                "resources",
                "clocks",
                "scene_id",
            }
            unsafe_public_projection = (
                not signal.public_title.strip()
                or not visible_bands
                or any(not band.public_label.strip() for band in visible_bands)
                or any(
                    path.split(".", 1)[0] not in allowed_public_roots
                    for path in public_paths
                )
            )
            if unsafe_public_projection:
                signal = signal.model_copy(
                    update={
                        "visibility": "kp",
                        "public_title": "",
                        "public_summary": "",
                    }
                )
                bands = cls._private_signal_bands(bands)
                normalizations.append(
                    ScenarioIrNormalization(
                        code="table_signal_downgraded",
                        record_kind="consequence_signals",
                        record_id=signal.id,
                    )
                )
        if signal.visibility == "kp":
            has_public_projection = bool(
                signal.public_title.strip()
                or signal.public_summary.strip()
                or any(
                    band.player_visible
                    or band.public_label.strip()
                    or band.public_description.strip()
                    for band in bands
                )
            )
            if has_public_projection:
                normalizations.append(
                    ScenarioIrNormalization(
                        code="kp_signal_public_projection_removed",
                        record_kind="consequence_signals",
                        record_id=signal.id,
                    )
                )
                bands = cls._private_signal_bands(bands)
                signal = signal.model_copy(
                    update={"public_title": "", "public_summary": ""}
                )
        invalid_paths = {
            condition.path
            for band in bands
            for condition in band.all_conditions
            if condition.path not in produced_paths
            and not condition.path.startswith(("actor_locations.", "events."))
        }
        if source_path is not None and source_path not in produced_paths:
            invalid_paths.add(source_path)
        if invalid_paths:
            assumptions.append(
                f"Consequence signal {signal.id} with unproduced state paths was "
                f"discarded: {sorted(invalid_paths)}"
            )
            return None
        return signal.model_copy(
            update={"source_path": source_path, "bands": bands}
        )

    @staticmethod
    def _private_signal_bands(
        bands: tuple[ConsequenceSignalBand, ...],
    ) -> tuple[ConsequenceSignalBand, ...]:
        return tuple(
            band.model_copy(
                update={
                    "player_visible": False,
                    "public_label": "",
                    "public_description": "",
                }
            )
            for band in bands
        )

    @classmethod
    def _ir_produced_paths(
        cls,
        initial_facts: dict[str, Any],
        actions: tuple[IrAction, ...],
        policies: tuple[IrReactivePolicy, ...],
        *,
        entity_ids: set[str],
        clock_ids: set[str],
        resource_ids: set[str],
    ) -> set[str]:
        paths = {"scene_id", "status", "run_version"}
        paths.update(f"entities.{item}" for item in entity_ids)
        paths.update(f"clocks.{item}" for item in clock_ids)
        paths.update(f"resources.{item}" for item in resource_ids)

        def flatten(value: dict[str, Any], prefix: str = "") -> None:
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                paths.add(f"facts.{path}")
                if isinstance(child, dict):
                    flatten(child, path)

        def add_commands(commands: Iterable[WorldCommand]) -> None:
            for original in commands:
                command = cls._command(original)
                if command.kind == "set_fact":
                    paths.add(f"facts.{command.path}")
                elif command.kind == "set_entity_status":
                    paths.add(f"entities.{command.entity_id}")
                elif command.kind == "update_entity_runtime":
                    paths.add(f"entity_runtime.{command.entity_id}")
                elif command.kind == "adjust_resource":
                    paths.add(f"resources.{command.path}")
                elif command.kind == "advance_clock":
                    paths.add(f"clocks.{command.clock_id}")
                elif command.kind == "move_actor":
                    paths.add(f"actor_locations.{command.actor_id}")
                elif command.kind == "set_scene":
                    paths.add("scene_id")

        flatten(initial_facts)
        for action in actions:
            paths.add(operator_outcome_path(action.id))
            add_commands(
                (
                    *action.always,
                    *action.on_success,
                    *action.on_failure,
                    *action.on_pushed_failure,
                )
            )
        for policy in policies:
            for rule in policy.rules:
                add_commands(rule.commands)
        return paths

    @classmethod
    def _safe_ending(
        cls,
        ending: IrEnding,
        *,
        produced_paths: set[str],
        location_ids: set[str],
        entity_ids: set[str],
        clock_ids: set[str],
        resource_ids: set[str],
        assumptions: list[str],
    ) -> IrEnding | None:
        all_conditions = cls._conditions(
            ending.all_conditions,
            clock_ids=clock_ids,
            location_ids=location_ids,
            entity_ids=entity_ids,
        )
        any_conditions = cls._conditions(
            ending.any_conditions,
            clock_ids=clock_ids,
            location_ids=location_ids,
            entity_ids=entity_ids,
        )
        invalid_paths = sorted(
            {
                condition.path
                for condition in (*all_conditions, *any_conditions)
                if condition.path not in produced_paths
            }
        )
        if invalid_paths:
            assumptions.append(
                f"Ending {ending.id} with unproduced state paths was discarded: "
                f"{invalid_paths}"
            )
            return None
        commands = ending.commands
        if any(command.kind == "complete_run" for command in commands):
            assumptions.append(
                f"Redundant complete_run command was removed from ending {ending.id}."
            )
            commands = tuple(
                command for command in commands if command.kind != "complete_run"
            )
        return ending.model_copy(
            update={
                "all_conditions": all_conditions,
                "any_conditions": any_conditions,
                "commands": cls._safe_commands(
                    commands,
                    owner=ending.id,
                    location_ids=location_ids,
                    entity_ids=entity_ids,
                    clock_ids=clock_ids,
                    resource_ids=resource_ids,
                    assumptions=assumptions,
                ),
            }
        )

    @classmethod
    def _safe_clues(
        cls,
        clues: tuple[IrClue, ...],
        actions: tuple[IrAction, ...],
        *,
        initial_facts: dict[str, Any],
        assumptions: list[str],
    ) -> tuple[IrClue, ...]:
        action_ids = {action.id for action in actions}
        producers: dict[str, list[str]] = {}
        for action in actions:
            for command in (
                *action.always,
                *action.on_success,
                *action.on_failure,
                *action.on_pushed_failure,
            ):
                if command.kind == "set_fact" and command.path:
                    producers.setdefault(cls._fact_path(command.path), []).append(action.id)
        safe: list[IrClue] = []
        for clue in clues:
            fact_path = cls._fact_path(clue.fact_path)
            discovery = tuple(
                dict.fromkeys(
                    (
                        *(item for item in clue.discovery_action_ids if item in action_ids),
                        *producers.get(fact_path, ()),
                    )
                )
            )
            if (
                clue.importance == "core"
                and not discovery
                and not cls._mapping_has_path(initial_facts, fact_path)
            ):
                assumptions.append(
                    f"Core clue without a provable discovery route was discarded: {clue.id}"
                )
                continue
            safe.append(
                clue.model_copy(
                    update={
                        "fact_path": fact_path,
                        "discovery_action_ids": discovery,
                    }
                )
            )
        return tuple(safe)

    @staticmethod
    def _mapping_has_path(root: dict[str, Any], path: str) -> bool:
        current: Any = root
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        return True

    @staticmethod
    def _dotted_path(path: str) -> str:
        normalized = re.sub(r"\[['\"]?([^\]'\"]+)['\"]?\]", r".\1", path)
        return normalized.removeprefix("$.").strip(".")

    @classmethod
    def _fact_path(cls, path: str) -> str:
        return cls._dotted_path(path).removeprefix("facts.")

    @classmethod
    def _signal_source_path(
        cls,
        path: str | None,
        *,
        clock_ids: set[str],
        resource_ids: set[str],
        entity_ids: set[str],
    ) -> str | None:
        if path is None:
            return None
        dotted = cls._dotted_path(path)
        if dotted in clock_ids:
            return f"clocks.{dotted}"
        if dotted in resource_ids:
            return f"resources.{dotted}"
        if dotted in entity_ids:
            return f"entities.{dotted}"
        if dotted.split(".", 1)[0] in {
            "facts",
            "entities",
            "resources",
            "clocks",
            "scene_id",
        }:
            return dotted
        return f"facts.{dotted}"

    @classmethod
    def _condition(
        cls,
        condition: StateCondition,
        *,
        clock_ids: set[str] | frozenset[str] = frozenset(),
        location_ids: set[str] | frozenset[str] = frozenset(),
        entity_ids: set[str] | frozenset[str] = frozenset(),
    ) -> StateCondition:
        path = cls._dotted_path(condition.path)
        # A clock is a scalar in the authoritative snapshot. Weak authoring
        # models commonly project SDK-style wrappers such as
        # ``facts.<clock_id>.current`` or ``clocks.<clock_id>.current``. Only
        # rewrite an exact, server-declared clock identity; arbitrary fact paths
        # remain facts and no semantic guess is involved.
        clock_alias = path.removeprefix("facts.")
        clock_alias = clock_alias.removeprefix("clocks.").removeprefix("clock.")
        if clock_alias.endswith(".current"):
            clock_alias = clock_alias.removesuffix(".current")
        if clock_alias in clock_ids:
            path = f"clocks.{clock_alias}"
        parts = path.split(".")
        entity_location_alias = (
            len(parts) == 3
            and parts[0] in {"actor", "actors", "entities"}
            and parts[2] in {"location", "location_id"}
        )
        alias_subject = parts[1] if entity_location_alias else None
        player_scene_alias = path in {
            "current_location",
            "current_location_id",
            "current_scene",
            "current_scene_id",
            "facts.current_location",
            "facts.current_location_id",
            "facts.current_scene",
            "facts.current_scene_id",
        }
        if (
            (
                path in {"location", "facts.location"}
                or player_scene_alias
                or entity_location_alias
            )
            and isinstance(condition.value, str)
            and condition.value in location_ids
            and alias_subject not in entity_ids
        ):
            path = "scene_id"
        allowed_roots = {
            "run_id",
            "contract_id",
            "scenario_version",
            "run_version",
            "status",
            "scene_id",
            "facts",
            "entities",
            "actor_locations",
            "resources",
            "clocks",
            "events",
            "ending_id",
        }
        if path.split(".", 1)[0] not in allowed_roots:
            path = f"facts.{path}"
        return condition.model_copy(update={"path": path})

    @classmethod
    def _conditions(
        cls,
        conditions: tuple[StateCondition, ...],
        *,
        clock_ids: set[str] | frozenset[str] = frozenset(),
        location_ids: set[str] | frozenset[str] = frozenset(),
        entity_ids: set[str] | frozenset[str] = frozenset(),
    ) -> tuple[StateCondition, ...]:
        return tuple(
            cls._condition(
                condition,
                clock_ids=clock_ids,
                location_ids=location_ids,
                entity_ids=entity_ids,
            )
            for condition in conditions
        )

    @classmethod
    def _command(cls, command: WorldCommand) -> WorldCommand:
        if command.kind not in {"set_fact", "remove_fact"} or command.path is None:
            return command
        path = cls._fact_path(command.path)
        return command.model_copy(update={"path": path})

    @classmethod
    def _commands(
        cls, commands: tuple[WorldCommand, ...]
    ) -> tuple[WorldCommand, ...]:
        return tuple(cls._command(command) for command in commands)

    @classmethod
    def _reactive_rule(
        cls,
        rule: ReactiveRule,
        *,
        clock_ids: set[str] | frozenset[str] = frozenset(),
        location_ids: set[str] | frozenset[str] = frozenset(),
        entity_ids: set[str] | frozenset[str] = frozenset(),
    ) -> ReactiveRule:
        return rule.model_copy(
            update={
                "conditions": cls._conditions(
                    rule.conditions,
                    clock_ids=clock_ids,
                    location_ids=location_ids,
                    entity_ids=entity_ids,
                ),
                "commands": cls._commands(rule.commands),
            }
        )

    @classmethod
    def _signal_band(
        cls,
        band: ConsequenceSignalBand,
        *,
        clock_ids: set[str] | frozenset[str] = frozenset(),
    ) -> ConsequenceSignalBand:
        return band.model_copy(
            update={
                "all_conditions": cls._conditions(
                    band.all_conditions,
                    clock_ids=clock_ids,
                )
            }
        )


__all__ = [
    "ScenarioIrAssembler",
    "ScenarioIrAssembly",
    "ScenarioIrBatch",
]

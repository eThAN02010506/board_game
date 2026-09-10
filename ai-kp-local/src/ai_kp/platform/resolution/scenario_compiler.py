"""Deterministic compilation and static analysis for generic scenario contracts."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_kp.platform.resolution.candidate_ranking import (
    LARGE_CANDIDATE_LIMIT,
    SMALL_CANDIDATE_LIMIT,
)
from ai_kp.platform.resolution.causal_validation import operator_has_causal_result
from ai_kp.platform.resolution.command_writes import exclusive_assignments
from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    ScenarioContract,
    StateCondition,
    WorldCommand,
)
from ai_kp.platform.resolution.effect_catalog import ScenarioEffectCatalog
from ai_kp.platform.resolution.kernel import operator_outcome_path
from ai_kp.platform.resolution.location_travel import (
    CanonicalTravelOperatorConflict,
    canonical_travel_operator_ids,
    materialize_location_travel_operators,
)
from ai_kp.platform.resolution.playability import (
    PlayabilityReport,
    ScenarioPlayabilityAnalyzer,
)


class ContractValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: Literal["error", "warning"]
    code: str = Field(min_length=1, max_length=120)
    path: str = Field(default="", max_length=500)
    message: str = Field(min_length=1, max_length=1000)


class ContractValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    release_ready: bool = False
    provenance_ready: bool = False
    issues: tuple[ContractValidationIssue, ...] = ()
    reachable_location_ids: tuple[str, ...] = ()
    unreachable_location_ids: tuple[str, ...] = ()
    contract_hash: str | None = None
    playability: PlayabilityReport = PlayabilityReport()


class ScenarioCompilationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    contract: ScenarioContract | None = None
    report: ContractValidationReport


class ScenarioContractCompiler:
    """Build a strict contract and perform conservative, model-free analysis."""

    _SNAPSHOT_ROOTS: ClassVar[frozenset[str]] = frozenset({
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
    })

    def __init__(self, effect_catalog: ScenarioEffectCatalog | None = None):
        self.effect_catalog = effect_catalog
        self.playability_analyzer = ScenarioPlayabilityAnalyzer()

    def compile(
        self,
        payload: Mapping[str, Any] | ScenarioContract,
        *,
        provenance_exempt_record_keys: tuple[str, ...] = (),
    ) -> ScenarioCompilationResult:
        try:
            contract = ScenarioContract.model_validate(
                payload.model_dump(mode="python")
                if isinstance(payload, ScenarioContract)
                else payload
            )
            exemption_counts = Counter(provenance_exempt_record_keys)
            derived_exempt_operator_ids = {
                operator_id
                for link in contract.location_links
                if not link.source_refs
                and exemption_counts[
                    self.provenance_record_key("location_links", link)
                ]
                > 0
                for operator_id in canonical_travel_operator_ids(link)
            }
            contract = materialize_location_travel_operators(contract)
            effective_provenance_exemptions = (
                *provenance_exempt_record_keys,
                *(
                    self.provenance_record_key("operators", operator)
                    for operator in contract.operators
                    if operator.operator_id in derived_exempt_operator_ids
                ),
            )
        except ValidationError as exc:
            issues = tuple(
                ContractValidationIssue(
                    severity="error",
                    code="schema_validation",
                    path=".".join(str(part) for part in error["loc"]),
                    message=str(error["msg"]),
                )
                for error in exc.errors(include_url=False)
            )
            return ScenarioCompilationResult(
                report=ContractValidationReport(valid=False, issues=issues)
            )
        except CanonicalTravelOperatorConflict as exc:
            return ScenarioCompilationResult(
                report=ContractValidationReport(
                    valid=False,
                    issues=(
                        ContractValidationIssue(
                            severity="error",
                            code="canonical_travel_conflict",
                            path="operators",
                            message=str(exc),
                        ),
                    ),
                )
            )

        issues: list[ContractValidationIssue] = []
        issues.extend(self._entrypoint_issues(contract))
        issues.extend(self._reactive_trigger_issues(contract))
        playability = self.playability_analyzer.analyze(contract)
        reachable = tuple(
            location.location_id
            for location in contract.locations
            if location.location_id in playability.reachable_scene_ids
        )
        unreachable = tuple(
            location.location_id
            for location in contract.locations
            if location.location_id not in playability.reachable_scene_ids
        )
        if unreachable:
            issues.append(
                ContractValidationIssue(
                    severity="warning",
                    code="unreachable_locations",
                    path="locations",
                    message="Locations are not reachable from the initial scene: "
                    + ", ".join(unreachable),
                )
            )
        issues.extend(self._condition_issues(contract))
        issues.extend(self._command_conflicts(contract))
        issues.extend(self._ruleset_effect_issues(contract))
        issues.extend(self._ending_issues(contract))
        issues.extend(self._clue_issues(contract))
        issues.extend(self._signal_issues(contract))
        issues.extend(self._narrative_issues(contract))
        issues.extend(self._semantic_retrieval_issues(contract))
        missing_provenance = self._missing_provenance(
            contract,
            exempt_record_keys=effective_provenance_exemptions,
        )
        issues.extend(self._provenance_issues(missing_provenance))
        issues.extend(self._playability_issues(playability))
        contract_hash = self.contract_hash(contract)
        valid = not any(issue.severity == "error" for issue in issues)
        return ScenarioCompilationResult(
            contract=contract,
            report=ContractValidationReport(
                valid=valid,
                release_ready=valid and playability.ready and not missing_provenance,
                provenance_ready=not missing_provenance,
                issues=tuple(issues),
                reachable_location_ids=reachable,
                unreachable_location_ids=unreachable,
                contract_hash=contract_hash,
                playability=playability,
            ),
        )

    @staticmethod
    def _reactive_trigger_issues(
        contract: ScenarioContract,
    ) -> Iterable[ContractValidationIssue]:
        """Fail closed for triggers which have no authoritative policy runtime."""

        for policy_index, policy in enumerate(contract.reactive_policies):
            for rule_index, rule in enumerate(policy.rules):
                path = (
                    f"reactive_policies.{policy_index}.rules.{rule_index}.trigger"
                )
                if rule.trigger == "semantic_event":
                    yield ContractValidationIssue(
                        severity="error",
                        code="semantic_reactive_trigger_unsupported",
                        path=path,
                        message=(
                            "Semantic consequences must be authored as TriggerRule; "
                            "ReactivePolicy is reserved for lifecycle selectors."
                        ),
                    )
                elif rule.trigger == "background_tick":
                    yield ContractValidationIssue(
                        severity="error",
                        code="background_tick_runtime_unavailable",
                        path=path,
                        message=(
                            "Background ticks have no durable scenario-batch runtime "
                            "and cannot be published yet."
                        ),
                    )

    @staticmethod
    def _playability_issues(
        report: PlayabilityReport,
    ) -> Iterable[ContractValidationIssue]:
        """Expose proof failures without conflating draft validity and release readiness."""

        if not report.exploration_complete:
            yield ContractValidationIssue(
                severity="warning",
                code="playability_state_space_indeterminate",
                path="playability.state_space",
                message=(
                    "The bounded authoritative state exploration did not finish; "
                    "release readiness remains fail-closed."
                ),
            )

        for proof in report.proofs:
            if proof.status == "passed":
                continue
            detail = "; ".join(proof.counterexamples[:4])
            if len(proof.counterexamples) > 4:
                detail += f"; and {len(proof.counterexamples) - 4} more"
            yield ContractValidationIssue(
                severity="warning",
                code=f"playability_{proof.invariant}_{proof.status}",
                path=f"playability.{proof.invariant}",
                message=detail or "The invariant has no deterministic witness.",
            )

    @staticmethod
    def _entrypoint_issues(
        contract: ScenarioContract,
    ) -> Iterable[ContractValidationIssue]:
        if contract.locations and contract.initial_scene_id is None:
            yield ContractValidationIssue(
                severity="warning",
                code="initial_scene_missing",
                path="initial_scene_id",
                message=(
                    "A playable location graph requires an explicit player entry point; "
                    "the runtime must not guess one from a hidden location."
                ),
            )

    def _ruleset_effect_issues(
        self, contract: ScenarioContract
    ) -> Iterable[ContractValidationIssue]:
        for path, command in self._all_commands(contract):
            if command.kind != "apply_ruleset_effect":
                continue
            if not path.startswith("operators."):
                yield ContractValidationIssue(
                    severity="error",
                    code="ruleset_effect_requires_action_actor",
                    path=path,
                    message="Ruleset effects may only run from an actor-bound action.",
                )
            if command.actor_id not in {None, "$actor"}:
                yield ContractValidationIssue(
                    severity="error",
                    code="ruleset_effect_forbidden_actor",
                    path=path,
                    message="A contract cannot choose the investigator affected by an action.",
                )
            if self.effect_catalog is None or not self.effect_catalog.supports(
                contract.ruleset_id
            ):
                yield ContractValidationIssue(
                    severity="error",
                    code="ruleset_effect_catalog_missing",
                    path=path,
                    message="No matching ruleset effect catalog is installed.",
                )
                continue
            errors = self.effect_catalog.validate_effect(
                str(command.event_type), command.payload
            )
            for error in errors:
                yield ContractValidationIssue(
                    severity="error",
                    code="invalid_ruleset_effect",
                    path=path,
                    message=error,
                )

    @staticmethod
    def _all_commands(
        contract: ScenarioContract,
    ) -> Iterable[tuple[str, WorldCommand]]:
        for operator_index, operator in enumerate(contract.operators):
            groups = (
                ("always_commands", operator.always_commands),
                ("success_commands", operator.success_commands),
                ("failure_commands", operator.failure_commands),
            )
            for group, commands in groups:
                for command_index, command in enumerate(commands):
                    yield f"operators.{operator_index}.{group}.{command_index}", command
            for branch_index, branch in enumerate(operator.outcome_branches):
                for command_index, command in enumerate(branch.commands):
                    yield (
                        f"operators.{operator_index}.outcome_branches.{branch_index}."
                        f"commands.{command_index}"
                    ), command
        for policy_index, policy in enumerate(contract.reactive_policies):
            for rule_index, rule in enumerate(policy.rules):
                for command_index, command in enumerate(rule.commands):
                    yield (
                        f"reactive_policies.{policy_index}.rules.{rule_index}."
                        f"commands.{command_index}"
                    ), command
        for ending_index, ending in enumerate(contract.endings):
            for command_index, command in enumerate(ending.commands):
                yield f"endings.{ending_index}.commands.{command_index}", command

    @staticmethod
    def contract_hash(contract: ScenarioContract) -> str:
        encoded = json.dumps(
            contract.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _condition_issues(
        self, contract: ScenarioContract
    ) -> Iterable[ContractValidationIssue]:
        condition_groups: list[tuple[str, tuple[StateCondition, ...]]] = []
        for index, operator in enumerate(contract.operators):
            condition_groups.append((f"operators.{index}.preconditions", operator.preconditions))
        for index, link in enumerate(contract.location_links):
            condition_groups.append((f"location_links.{index}.preconditions", link.preconditions))
        for index, method in enumerate(contract.task_methods):
            condition_groups.append((f"task_methods.{index}.preconditions", method.preconditions))
        for policy_index, policy in enumerate(contract.reactive_policies):
            for rule_index, rule in enumerate(policy.rules):
                condition_groups.append(
                    (
                        f"reactive_policies.{policy_index}.rules.{rule_index}.conditions",
                        rule.conditions,
                    )
                )
        for signal_index, signal in enumerate(contract.consequence_signals):
            for band_index, band in enumerate(signal.bands):
                condition_groups.append(
                    (
                        f"consequence_signals.{signal_index}.bands.{band_index}.all_conditions",
                        band.all_conditions,
                    )
                )
        for index, ending in enumerate(contract.endings):
            condition_groups.extend(
                (
                    (f"endings.{index}.all_conditions", ending.all_conditions),
                    (f"endings.{index}.any_conditions", ending.any_conditions),
                )
            )
        for base_path, conditions in condition_groups:
            for index, condition in enumerate(conditions):
                root = condition.path.split(".", 1)[0]
                if root not in self._SNAPSHOT_ROOTS:
                    yield ContractValidationIssue(
                        severity="error",
                        code="unknown_condition_root",
                        path=f"{base_path}.{index}.path",
                        message=f"Unknown snapshot condition root: {root}",
                    )

    def _command_conflicts(
        self, contract: ScenarioContract
    ) -> Iterable[ContractValidationIssue]:
        for operator_index, operator in enumerate(contract.operators):
            outcomes = [
                (
                    "success_commands",
                    (*operator.always_commands, *operator.success_commands),
                ),
                (
                    "failure_commands",
                    (*operator.always_commands, *operator.failure_commands),
                ),
            ]
            outcomes.extend(
                (
                    f"outcome_branches.{index}.commands",
                    (*operator.always_commands, *branch.commands),
                )
                for index, branch in enumerate(operator.outcome_branches)
            )
            for outcome, commands in outcomes:
                seen: dict[tuple[str, str], Any] = {}
                for command_index, command in enumerate(commands):
                    for target, value in exclusive_assignments(command):
                        if target in seen and seen[target] != value:
                            yield ContractValidationIssue(
                                severity="error",
                                code="conflicting_commands",
                                path=(
                                    f"operators.{operator_index}.{outcome}.{command_index}"
                                ),
                                message=f"One outcome assigns conflicting values to {target[1]}",
                            )
                        seen[target] = value

    def _ending_issues(
        self, contract: ScenarioContract
    ) -> Iterable[ContractValidationIssue]:
        if not contract.endings:
            yield ContractValidationIssue(
                severity="warning",
                code="no_endings",
                path="endings",
                message="The contract has no terminal rule.",
            )
            return
        produced_paths = self._produced_paths(contract)
        outcome_operators = {
            operator_outcome_path(operator.operator_id): operator
            for operator in contract.operators
        }
        for ending_index, ending in enumerate(contract.endings):
            for condition_index, condition in enumerate(
                (*ending.all_conditions, *ending.any_conditions)
            ):
                if condition.operator not in {"eq", "exists"}:
                    continue
                operator = outcome_operators.get(condition.path)
                if (
                    condition.path.startswith("events.operator_outcomes.")
                    and operator is None
                ):
                    yield ContractValidationIssue(
                        severity="error",
                        code="ending_operator_unknown",
                        path=f"endings.{ending_index}.conditions.{condition_index}",
                        message=(
                            "An ending references an operator outcome that is not present "
                            "in this contract. Operator outcomes are kernel-owned and cannot "
                            "be treated as external events."
                        ),
                    )
                    continue
                if operator is not None and not operator_has_causal_result(operator):
                    yield ContractValidationIssue(
                        severity="error",
                        code="ending_operator_has_no_causal_result",
                        path=f"endings.{ending_index}.conditions.{condition_index}",
                        message=(
                            "An ending cannot be proved by a no-op action. The referenced "
                            "operator needs a check, a state command, or an observable "
                            "information/response obligation."
                        ),
                    )
                if (
                    operator is not None
                    and operator.policy == "automatic"
                    and not operator.preconditions
                    and not operator.skill_choices
                ):
                    yield ContractValidationIssue(
                        severity="error",
                        code="ungated_automatic_ending_operator",
                        path=f"endings.{ending_index}.conditions.{condition_index}",
                        message=(
                            "An automatic operator cannot end a run without an established "
                            "precondition. Use a checked action, a state-gated transition, or "
                            "an explicit choice operator for a genuine irreversible choice."
                        ),
                    )
                if self._condition_can_be_external(condition):
                    continue
                if condition.path not in produced_paths:
                    yield ContractValidationIssue(
                        severity="error",
                        code="ending_condition_unproduced",
                        path=f"endings.{ending_index}.conditions.{condition_index}",
                        message=(
                            "No initial state or command can produce the ending condition path: "
                            f"{condition.path}"
                        ),
                    )

    @staticmethod
    def _clue_issues(
        contract: ScenarioContract,
    ) -> Iterable[ContractValidationIssue]:
        operators = {item.operator_id: item for item in contract.operators}
        for clue_index, clue in enumerate(contract.clues):
            routes = [operators[item] for item in clue.discovery_operator_ids]
            if routes and all(
                not route.automatic_information
                and not any(
                    cue.outcome_key == "success" for cue in route.narrative_cues
                )
                for route in routes
            ):
                yield ContractValidationIssue(
                    severity="error" if clue.importance == "core" else "warning",
                    code="clue_discovery_has_no_public_payload",
                    path=f"clues.{clue_index}.discovery_operator_ids",
                    message=(
                        "A clue route must disclose concrete player-visible information on "
                        "success; changing a hidden fact alone is not a playable result."
                    ),
                )
            if clue.importance != "core" or not clue.recoverable:
                continue
            if len(routes) == 1 and not ScenarioContractCompiler._route_recovers_clue(
                routes[0], clue.fact_path, clue.fact_value
            ):
                yield ContractValidationIssue(
                    severity="error",
                    code="fragile_core_clue",
                    path=f"clues.{clue_index}",
                    message=(
                        "A recoverable core clue has only one route and that route can end "
                        "without committing the clue. Add an independent route, make discovery "
                        "automatic, or deliver it on every rolled outcome with different costs."
                    ),
                )

    @staticmethod
    def _route_recovers_clue(
        operator: ActionOperator,
        fact_path: str,
        fact_value: Any,
    ) -> bool:
        """Return whether one route commits the core clue on every possible outcome."""

        def commits(commands: tuple[WorldCommand, ...]) -> bool:
            return any(
                command.kind == "set_fact"
                and command.path == fact_path.removeprefix("facts.")
                and command.value == fact_value
                for command in commands
            )

        if commits(operator.always_commands):
            return True
        if operator.policy == "automatic":
            return commits(operator.success_commands)
        if operator.outcome_branches:
            return all(
                commits(branch.commands) for branch in operator.outcome_branches
            )
        if operator.skill_choices:
            return commits(operator.success_commands) and commits(
                operator.failure_commands
            )
        return commits(operator.success_commands)

    @classmethod
    def _signal_issues(
        cls, contract: ScenarioContract
    ) -> Iterable[ContractValidationIssue]:
        produced_paths = cls._produced_paths(contract)
        for signal_index, signal in enumerate(contract.consequence_signals):
            if signal.source_path is not None and signal.source_path not in produced_paths:
                yield ContractValidationIssue(
                    severity="error",
                    code="signal_source_unproduced",
                    path=f"consequence_signals.{signal_index}.source_path",
                    message=(
                        "Consequence signal source is not part of the executable state: "
                        f"{signal.source_path}"
                    ),
                )
            for band_index, band in enumerate(signal.bands):
                for condition_index, condition in enumerate(band.all_conditions):
                    if condition.path in produced_paths or cls._condition_can_be_external(
                        condition
                    ):
                        continue
                    yield ContractValidationIssue(
                        severity="error",
                        code="signal_condition_unproduced",
                        path=(
                            f"consequence_signals.{signal_index}.bands.{band_index}."
                            f"all_conditions.{condition_index}.path"
                        ),
                        message=(
                            "Consequence signal condition is not part of executable state: "
                            f"{condition.path}"
                        ),
                    )

    @staticmethod
    def _narrative_issues(
        contract: ScenarioContract,
    ) -> Iterable[ContractValidationIssue]:
        entities = {item.entity_id: item for item in contract.entities}
        for operator_index, operator in enumerate(contract.operators):
            for cue_index, cue in enumerate(operator.narrative_cues):
                if cue.speaker_entity_id is None:
                    continue
                entity = entities.get(cue.speaker_entity_id)
                if entity is None:
                    yield ContractValidationIssue(
                        severity="error",
                        code="narrative_speaker_unknown",
                        path=(
                            f"operators.{operator_index}.narrative_cues.{cue_index}."
                            "speaker_entity_id"
                        ),
                        message=(
                            "Narrative cue speaker is not a contract entity: "
                            f"{cue.speaker_entity_id}"
                        ),
                    )
                elif entity.entity_type != "npc":
                    yield ContractValidationIssue(
                        severity="error",
                        code="narrative_speaker_not_npc",
                        path=(
                            f"operators.{operator_index}.narrative_cues.{cue_index}."
                            "speaker_entity_id"
                        ),
                        message=(
                            "Narrative cue speakers must be NPC entities: "
                            f"{cue.speaker_entity_id}"
                        ),
                    )

    @staticmethod
    def _semantic_retrieval_issues(
        contract: ScenarioContract,
    ) -> Iterable[ContractValidationIssue]:
        issues: list[ContractValidationIssue] = []
        missing_operators = [
            item.operator_id for item in contract.operators if not item.intent_hints
        ]
        if (
            len(contract.operators) > SMALL_CANDIDATE_LIMIT
            and missing_operators
        ):
            issues.append(
                ContractValidationIssue(
                    severity="warning",
                    code="semantic_retrieval_hints_missing",
                    path="operators",
                    message=(
                        "Large operator catalogs should provide intent_hints for "
                        "deterministic small-model retrieval; missing: "
                        + ScenarioContractCompiler._identifier_preview(
                            missing_operators
                        )
                    ),
                )
            )
        missing_large_candidates = [
            *(f"operator:{item}" for item in missing_operators),
            *(
                f"method:{item.method_id}"
                for item in contract.task_methods
                if not item.intent_hints
            ),
        ]
        if (
            len(contract.operators) + len(contract.task_methods)
            > LARGE_CANDIDATE_LIMIT
            and missing_large_candidates
        ):
            issues.append(
                ContractValidationIssue(
                    severity="warning",
                    code="semantic_large_retrieval_hints_missing",
                    path="operators,task_methods",
                    message=(
                        "Large combined catalogs should provide intent_hints for "
                        "deterministic large-model retrieval; missing: "
                        + ScenarioContractCompiler._identifier_preview(
                            missing_large_candidates
                        )
                    ),
                ),
            )
        return tuple(issues)

    @staticmethod
    def _identifier_preview(identifiers: list[str]) -> str:
        preview = ", ".join(identifiers[:8])
        return preview + ("…" if len(identifiers) > 8 else "")

    @staticmethod
    def _missing_provenance(
        contract: ScenarioContract,
        *,
        exempt_record_keys: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        records: list[tuple[str, str, tuple[str, ...], Any, Any]] = [
            (
                f"locations.{index}",
                "locations",
                (item.location_id,),
                item.source_refs,
                item,
            )
            for index, item in enumerate(contract.locations)
        ]
        records.extend(
            (
                f"location_links.{index}",
                "location_links",
                (item.from_location_id, item.to_location_id),
                item.source_refs,
                item,
            )
            for index, item in enumerate(contract.location_links)
        )
        for group, items, id_field in (
            ("entities", contract.entities, "entity_id"),
            ("clocks", contract.clocks, "clock_id"),
            ("resources", contract.resources, "resource_id"),
            ("clues", contract.clues, "clue_id"),
            ("operators", contract.operators, "operator_id"),
            ("task_methods", contract.task_methods, "method_id"),
            ("reactive_policies", contract.reactive_policies, "policy_id"),
            ("response_obligations", contract.response_obligations, "obligation_id"),
            ("trigger_rules", contract.trigger_rules, "trigger_id"),
            ("pressure_tracks", contract.pressure_tracks, "pressure_id"),
            ("consequence_signals", contract.consequence_signals, "signal_id"),
            ("endings", contract.endings, "ending_id"),
        ):
            records.extend(
                (
                    f"{group}.{index}",
                    group,
                    (str(getattr(item, id_field)),),
                    item.source_refs,
                    item,
                )
                for index, item in enumerate(items)
            )
        remaining_exemptions = Counter(exempt_record_keys)
        missing: list[str] = []
        for path, group, identifiers, source_refs, record in records:
            if source_refs:
                continue
            record_key = ScenarioContractCompiler.provenance_record_key(
                group, record
            )
            if remaining_exemptions[record_key] > 0:
                remaining_exemptions[record_key] -= 1
                continue
            missing.append(path)
        return tuple(missing)

    @staticmethod
    def provenance_record_key(group: str, record: BaseModel) -> str:
        encoded = record.model_dump_json().encode("utf-8")
        return f"{group}:{hashlib.sha256(encoded).hexdigest()}"

    @classmethod
    def has_complete_provenance(
        cls,
        contract: ScenarioContract,
    ) -> bool:
        """Re-evaluate provenance from contract records, independent of stored reports."""

        return not cls._missing_provenance(
            contract,
        )

    @staticmethod
    def _provenance_issues(
        missing: tuple[str, ...],
    ) -> Iterable[ContractValidationIssue]:
        if missing:
            yield ContractValidationIssue(
                severity="warning",
                code="missing_provenance",
                path="source_refs",
                message=(
                    f"Executable records without source provenance: {len(missing)} "
                    f"({ScenarioContractCompiler._identifier_preview(list(missing))})"
                ),
            )

    @classmethod
    def _produced_paths(cls, contract: ScenarioContract) -> set[str]:
        paths = {"scene_id", "status", "run_version"}
        paths.update(f"facts.{path}" for path in cls._flatten_paths(contract.initial_facts))
        paths.update(f"entities.{item.entity_id}" for item in contract.entities)
        paths.update(f"resources.{item.resource_id}" for item in contract.resources)
        paths.update(f"clocks.{item.clock_id}" for item in contract.clocks)
        for operator in contract.operators:
            commands = [
                *operator.always_commands,
                *operator.success_commands,
                *operator.failure_commands,
            ]
            commands.extend(
                command
                for branch in operator.outcome_branches
                for command in branch.commands
            )
            for command in commands:
                if command.kind == "set_fact":
                    paths.add(f"facts.{command.path}")
                elif command.kind == "set_entity_status":
                    paths.add(f"entities.{command.entity_id}")
                elif command.kind == "adjust_resource":
                    paths.add(f"resources.{command.path}")
                elif command.kind == "advance_clock":
                    paths.add(f"clocks.{command.clock_id}")
                elif command.kind == "move_actor":
                    paths.add(f"actor_locations.{command.actor_id}")
                elif command.kind == "set_scene":
                    paths.add("scene_id")
        for policy in contract.reactive_policies:
            for rule in policy.rules:
                for command in rule.commands:
                    if command.kind == "set_fact":
                        paths.add(f"facts.{command.path}")
                    elif command.kind == "set_entity_status":
                        paths.add(f"entities.{command.entity_id}")
                    elif command.kind == "adjust_resource":
                        paths.add(f"resources.{command.path}")
                    elif command.kind == "advance_clock":
                        paths.add(f"clocks.{command.clock_id}")
                    elif command.kind == "move_actor":
                        paths.add(f"actor_locations.{command.actor_id}")
                    elif command.kind == "set_scene":
                        paths.add("scene_id")
        return paths

    @classmethod
    def produced_paths(cls, contract: ScenarioContract) -> frozenset[str]:
        """Expose the deterministic state-path authority to bounded assemblers."""

        return frozenset(cls._produced_paths(contract))

    @classmethod
    def _flatten_paths(cls, value: Mapping[str, Any], prefix: str = "") -> set[str]:
        paths: set[str] = set()
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.add(path)
            if isinstance(child, Mapping):
                paths.update(cls._flatten_paths(child, path))
        return paths

    @staticmethod
    def _condition_can_be_external(condition: StateCondition) -> bool:
        return condition.path.startswith(("actor_locations.", "events."))


__all__ = [
    "ContractValidationIssue",
    "ContractValidationReport",
    "ScenarioCompilationResult",
    "ScenarioContractCompiler",
]

"""Conservative validation for additive, run-scoped scenario extensions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_kp.platform.resolution.check_catalog import ScenarioCheckCatalog
from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    ClockSpec,
    ClueSpec,
    ConsequenceSignalSpec,
    EntitySpec,
    LocationLink,
    LocationSpec,
    ReactivePolicy,
    ResourceSpec,
    ScenarioContract,
    TaskMethod,
    WorldCommand,
)
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler

AutomationLevel = Literal["conservative", "balanced", "ai_kp"]
ExpansionDecisionStatus = Literal["auto_approved", "review_required", "rejected"]


class ExpansionRecords(BaseModel):
    """Only additive records are representable; source records and endings are absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    locations: tuple[LocationSpec, ...] = Field(default=(), max_length=4)
    location_links: tuple[LocationLink, ...] = Field(default=(), max_length=8)
    entities: tuple[EntitySpec, ...] = Field(default=(), max_length=4)
    clocks: tuple[ClockSpec, ...] = Field(default=(), max_length=2)
    resources: tuple[ResourceSpec, ...] = Field(default=(), max_length=2)
    clues: tuple[ClueSpec, ...] = Field(default=(), max_length=4)
    operators: tuple[ActionOperator, ...] = Field(default=(), max_length=4)
    task_methods: tuple[TaskMethod, ...] = Field(default=(), max_length=2)
    reactive_policies: tuple[ReactivePolicy, ...] = Field(default=(), max_length=2)
    consequence_signals: tuple[ConsequenceSignalSpec, ...] = Field(
        default=(), max_length=2
    )


class WorldExpansionContractProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    base_contract_id: str = Field(min_length=1, max_length=160)
    base_source_version: int = Field(ge=1)
    base_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_state_version: int = Field(ge=0)
    confidence: Literal["low", "medium", "high"]
    assumptions: tuple[str, ...] = Field(min_length=1, max_length=8)
    rationale: str = Field(min_length=1, max_length=2000)
    records: ExpansionRecords


class WorldExpansionContractDecision(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, arbitrary_types_allowed=True
    )

    status: ExpansionDecisionStatus
    blockers: tuple[str, ...]
    merged_contract: ScenarioContract | None = None
    merged_contract_hash: str | None = None


class WorldExpansionContractValidator:
    """Validate an AI proposal without granting it direct mutation authority."""

    def __init__(
        self,
        compiler: ScenarioContractCompiler | None = None,
        check_catalog: ScenarioCheckCatalog | None = None,
    ):
        self.compiler = compiler or ScenarioContractCompiler()
        self.check_catalog = check_catalog

    def validate(
        self,
        base: ScenarioContract,
        proposal: WorldExpansionContractProposal,
        *,
        automation_level: AutomationLevel,
        trusted_dynamic_records: tuple[ExpansionRecords, ...] = (),
    ) -> WorldExpansionContractDecision:
        blockers = self._boundary_blockers(base, proposal)
        if blockers:
            return WorldExpansionContractDecision(
                status="rejected", blockers=tuple(sorted(set(blockers)))
            )

        merged = self._merge(base, proposal.records)
        compilation = self.compiler.compile(
            merged,
            # Consume exact records from the current proposal and previously
            # activated overlays. A namespace prefix alone is not provenance.
            provenance_exempt_record_keys=self._provenance_exemption_keys(
                (*trusted_dynamic_records, proposal.records)
            ),
        )
        if not compilation.report.valid or compilation.contract is None:
            reasons = [
                f"compiler:{item.code}:{item.path}"
                for item in compilation.report.issues
                if item.severity == "error"
            ]
            return WorldExpansionContractDecision(
                status="rejected", blockers=tuple(reasons or ["compiler:invalid"])
            )

        release_blockers: list[str] = []
        if not compilation.report.release_ready:
            release_blockers.extend(
                f"release_ready:{proof.invariant}"
                for proof in compilation.report.playability.proofs
                if proof.status != "passed"
            )
            if not compilation.report.provenance_ready:
                release_blockers.append("release_ready:provenance")
            return WorldExpansionContractDecision(
                status="rejected",
                blockers=tuple(release_blockers or ["release_ready:unavailable"]),
            )

        review_blockers: list[str] = []
        if automation_level != "ai_kp":
            review_blockers.append(f"automation_level:{automation_level}")
        if proposal.confidence != "high":
            review_blockers.append(f"confidence:{proposal.confidence}")
        return WorldExpansionContractDecision(
            status="review_required" if review_blockers else "auto_approved",
            blockers=tuple(review_blockers),
            merged_contract=compilation.contract,
            merged_contract_hash=compilation.report.contract_hash,
        )

    def _boundary_blockers(
        self,
        base: ScenarioContract,
        proposal: WorldExpansionContractProposal,
    ) -> list[str]:
        blockers: list[str] = []
        if proposal.base_contract_id != base.contract_id:
            blockers.append("base_contract_id_mismatch")
        if proposal.base_source_version != base.source_version:
            blockers.append("base_source_version_mismatch")
        if proposal.base_contract_hash != self.compiler.contract_hash(base):
            blockers.append("base_contract_hash_mismatch")

        prefix = f"expansion.{proposal.proposal_id}."
        identifiers = self._introduced_identifiers(proposal.records)
        allowed_location_ids = {
            *(item.location_id for item in base.locations),
            *(item.location_id for item in proposal.records.locations),
        }
        if not identifiers:
            blockers.append("empty_expansion")
        if self._has_claimed_source_refs(proposal.records):
            blockers.append("model_claimed_source_provenance")
        blockers.extend(
            f"identifier_outside_namespace:{identifier}"
            for identifier in identifiers
            if not identifier.startswith(prefix)
        )
        new_entity_ids = {item.entity_id for item in proposal.records.entities}
        for operator in proposal.records.operators:
            if not operator.skill_choices and (
                operator.failure_commands or operator.outcome_branches
            ):
                blockers.append(f"unreachable_outcome_commands:{operator.operator_id}")
            for choice in operator.skill_choices:
                if self.check_catalog is not None and not self.check_catalog.contains(
                    choice.skill_key
                ):
                    blockers.append(f"unknown_skill_key:{choice.skill_key}")
            for cue in operator.narrative_cues:
                if (
                    cue.speaker_entity_id is not None
                    and cue.speaker_entity_id not in new_entity_ids
                ):
                    blockers.append(
                        f"source_narrative_speaker:{cue.speaker_entity_id}"
                    )
            commands = (
                *operator.always_commands,
                *operator.success_commands,
                *operator.failure_commands,
                *(
                    command
                    for branch in operator.outcome_branches
                    for command in branch.commands
                ),
            )
            for command in commands:
                blockers.extend(self._command_blockers(
                    command, prefix, identifiers, allowed_location_ids
                ))
            if not self._operator_has_bounded_effect(operator):
                blockers.append(f"operator_without_cost:{operator.operator_id}")
        for clue in proposal.records.clues:
            if clue.importance == "core":
                blockers.append(f"dynamic_core_clue:{clue.clue_id}")
            if not clue.fact_path.startswith(prefix):
                blockers.append(f"source_clue_fact_path:{clue.fact_path}")
        for signal in proposal.records.consequence_signals:
            if signal.source_path is not None and not self._is_expansion_state_path(
                signal.source_path, prefix
            ):
                blockers.append(f"source_signal_path:{signal.source_path}")
            for band in signal.bands:
                if band.player_visible and not band.all_conditions:
                    blockers.append(
                        f"unconditional_public_signal:{signal.signal_id}:{band.band_id}"
                    )
                for condition in band.all_conditions:
                    if not self._is_expansion_state_path(condition.path, prefix):
                        blockers.append(
                            f"source_signal_condition:{condition.path}"
                        )
        for policy in proposal.records.reactive_policies:
            if policy.entity_id not in new_entity_ids:
                blockers.append(f"source_entity_policy:{policy.entity_id}")
            for rule in policy.rules:
                if not rule.rule_id.startswith(prefix):
                    blockers.append(f"rule_outside_namespace:{rule.rule_id}")
                for command in rule.commands:
                    blockers.extend(
                        self._command_blockers(
                            command, prefix, identifiers, allowed_location_ids
                        )
                    )
        return blockers

    @staticmethod
    def _has_claimed_source_refs(records: ExpansionRecords) -> bool:
        sourced = (
            *records.locations,
            *records.location_links,
            *records.entities,
            *records.clocks,
            *records.resources,
            *records.clues,
            *records.operators,
            *records.task_methods,
            *records.reactive_policies,
            *records.consequence_signals,
        )
        return any(item.source_refs for item in sourced)

    @staticmethod
    def _introduced_identifiers(records: ExpansionRecords) -> set[str]:
        return {
            *(item.location_id for item in records.locations),
            *(item.entity_id for item in records.entities),
            *(item.clock_id for item in records.clocks),
            *(item.resource_id for item in records.resources),
            *(item.clue_id for item in records.clues),
            *(item.operator_id for item in records.operators),
            *(item.method_id for item in records.task_methods),
            *(item.policy_id for item in records.reactive_policies),
            *(item.signal_id for item in records.consequence_signals),
        }

    @staticmethod
    def _is_expansion_state_path(path: str, prefix: str) -> bool:
        root, separator, remainder = path.partition(".")
        return bool(separator) and root in {
            "facts",
            "entities",
            "resources",
            "clocks",
        } and remainder.startswith(prefix)

    @staticmethod
    def _command_blockers(
        command: WorldCommand,
        prefix: str,
        introduced_identifiers: set[str],
        allowed_location_ids: set[str],
    ) -> list[str]:
        if command.kind == "move_actor":
            if (
                command.actor_id != "$actor"
                or str(command.value) not in allowed_location_ids
            ):
                return ["forbidden_command:move_actor"]
            return []
        if command.kind in {
            "complete_run",
            "remove_fact",
            "activate_contract_overlay",
            "register_entity",
            "register_clock",
            "register_resource",
            "set_scene",
        }:
            return [f"forbidden_command:{command.kind}"]
        if command.kind == "set_fact" and not str(command.path).startswith(prefix):
            return [f"source_fact_mutation:{command.path}"]
        if command.kind == "set_entity_status" and command.entity_id not in introduced_identifiers:
            return [f"source_entity_mutation:{command.entity_id}"]
        if command.kind == "emit_event" and not str(command.event_type).startswith(
            prefix
        ):
            return [f"reserved_event_type:{command.event_type}"]
        return []

    @staticmethod
    def _has_bounded_effect(commands: tuple[WorldCommand, ...]) -> bool:
        return any(
            (command.kind == "advance_clock" and (command.delta or 0) > 0)
            or (command.kind == "adjust_resource" and (command.delta or 0) < 0)
            or command.kind in {"move_actor", "emit_event", "set_fact", "set_entity_status"}
            for command in commands
        )

    @classmethod
    def _operator_has_bounded_effect(cls, operator: ActionOperator) -> bool:
        common = operator.always_commands
        if not operator.skill_choices and operator.policy in {"automatic", "choice"}:
            # These policies commit the success path directly. Failure-only
            # commands are unreachable and cannot make the action substantive.
            outcomes = [(*common, *operator.success_commands)]
        elif operator.skill_choices:
            outcomes = [
                (*common, *operator.success_commands),
                (*common, *operator.failure_commands),
                *(
                    (*common, *branch.commands)
                    for branch in operator.outcome_branches
                ),
            ]
        else:
            return False
        return all(cls._has_bounded_effect(item) for item in outcomes)

    @staticmethod
    def _merge(base: ScenarioContract, records: ExpansionRecords) -> ScenarioContract:
        return base.model_copy(
            update={
                "source_version": base.source_version + 1,
                "locations": (*base.locations, *records.locations),
                "location_links": (*base.location_links, *records.location_links),
                "entities": (*base.entities, *records.entities),
                "clocks": (*base.clocks, *records.clocks),
                "resources": (*base.resources, *records.resources),
                "clues": (*base.clues, *records.clues),
                "operators": (*base.operators, *records.operators),
                "task_methods": (*base.task_methods, *records.task_methods),
                "reactive_policies": (
                    *base.reactive_policies,
                    *records.reactive_policies,
                ),
                "consequence_signals": (
                    *base.consequence_signals,
                    *records.consequence_signals,
                ),
            }
        )

    def _provenance_exemption_keys(
        self,
        record_sets: tuple[ExpansionRecords, ...],
    ) -> tuple[str, ...]:
        return tuple(
            self.compiler.provenance_record_key(group, record)
            for records in record_sets
            for group in ExpansionRecords.model_fields
            for record in getattr(records, group)
        )


__all__ = [
    "ExpansionRecords",
    "WorldExpansionContractDecision",
    "WorldExpansionContractProposal",
    "WorldExpansionContractValidator",
]

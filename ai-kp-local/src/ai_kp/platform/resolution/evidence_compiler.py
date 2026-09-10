"""Evidence-bound gate for automatically generated scenario contracts."""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from ai_kp.platform.resolution.contracts import ScenarioContract, SourceRef
from ai_kp.platform.resolution.scenario_compiler import (
    ContractValidationIssue,
    ContractValidationReport,
    ScenarioContractCompiler,
)
from ai_kp.platform.resolution.scenario_terminal_method import (
    is_terminal_method_fact_command,
)
from ai_kp.platform.resolution.scenario_terminal_observation import (
    is_terminal_observation_fact_command,
)
from ai_kp.platform.resolution.source_coverage import (
    ScenarioRecordKind,
    SourceCoverageRequirement,
    SourceCoverageSupplementTarget,
)


class EvidenceBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_block_id: str = Field(min_length=1, max_length=160)
    document_id: str = Field(min_length=1, max_length=160)
    page: int | None = Field(default=None, ge=1)
    paragraph: int | None = Field(default=None, ge=0)
    text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_kind: str = Field(default="text", min_length=1, max_length=80)
    classification_confidence: float = Field(default=0.5, ge=0, le=1)
    coverage_requirements: tuple[SourceCoverageRequirement, ...] = ()


class SourceCoverageItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_block_id: str
    semantic_kind: str
    requirement_key: str
    acceptable_record_kinds: tuple[ScenarioRecordKind, ...]
    minimum_record_count: int
    matched_record_count: int
    matched_record_kinds: tuple[ScenarioRecordKind, ...]
    blocking: bool
    covered: bool


class SourceCoverageReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required_item_count: int = Field(ge=0)
    covered_item_count: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0, le=1)
    blocking_item_count: int = Field(default=0, ge=0)
    covered_blocking_item_count: int = Field(default=0, ge=0)
    items: tuple[SourceCoverageItem, ...] = ()

    @property
    def blocking_complete(self) -> bool:
        return self.covered_blocking_item_count == self.blocking_item_count

    def supplement_targets(
        self, *, blocking_only: bool = False
    ) -> tuple[SourceCoverageSupplementTarget, ...]:
        return tuple(
            SourceCoverageSupplementTarget(
                source_block_id=item.source_block_id,
                requirement_key=item.requirement_key,
                acceptable_record_kinds=item.acceptable_record_kinds,
                required_additional_count=(
                    item.minimum_record_count - item.matched_record_count
                ),
                blocking=item.blocking,
                reason=(
                    "来源义务尚未覆盖；只能补写允许类型且必须引用该来源块。"
                ),
            )
            for item in self.items
            if not item.covered and (item.blocking or not blocking_only)
        )


class EvidenceBoundContractCandidate(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, arbitrary_types_allowed=True
    )

    contract: ScenarioContract
    confidence: Literal["low", "medium", "high"]
    # Preserve the complete bounded assembly audit for the independent reviewer.
    # Truncating here can hide the very uncertainty that full-AI review must resolve.
    assumptions: tuple[str, ...] = Field(default=(), max_length=64)
    evidence_blocks: tuple[EvidenceBlock, ...] = Field(min_length=1)


class EvidenceBoundCompilationResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, arbitrary_types_allowed=True
    )

    decision: Literal["auto_publishable", "review_required", "rejected"]
    report: ContractValidationReport
    contract: ScenarioContract | None = None
    coverage: SourceCoverageReport = SourceCoverageReport(
        required_item_count=0,
        covered_item_count=0,
        coverage_ratio=1,
    )


def scenario_contract_record_groups(
    contract: ScenarioContract,
) -> tuple[tuple[str, tuple[Any, ...]], ...]:
    """Enumerate every contract record whose authority can affect a run."""

    return (
        ("locations", contract.locations),
        ("location_links", contract.location_links),
        ("entities", contract.entities),
        ("clocks", contract.clocks),
        ("resources", contract.resources),
        ("clues", contract.clues),
        ("operators", contract.operators),
        ("task_methods", contract.task_methods),
        ("reactive_policies", contract.reactive_policies),
        ("response_obligations", contract.response_obligations),
        ("trigger_rules", contract.trigger_rules),
        ("pressure_tracks", contract.pressure_tracks),
        ("consequence_signals", contract.consequence_signals),
        ("endings", contract.endings),
    )


def validate_contract_provenance(
    contract: ScenarioContract,
    evidence_blocks: tuple[EvidenceBlock, ...],
) -> tuple[ContractValidationIssue, ...]:
    """Resolve every SourceRef against one authoritative, immutable corpus."""

    evidence_by_id = {item.source_block_id: item for item in evidence_blocks}
    issues: list[ContractValidationIssue] = []
    for group, records in scenario_contract_record_groups(contract):
        for record_index, record in enumerate(records):
            path = f"{group}.{record_index}"
            if not record.source_refs:
                issues.append(
                    ContractValidationIssue(
                        severity="error",
                        code="missing_executable_provenance",
                        path=path,
                        message="Executable record lacks authoritative source evidence.",
                    )
                )
                continue
            for ref_index, ref in enumerate(record.source_refs):
                ref_path = f"{path}.source_refs.{ref_index}"
                block = evidence_by_id.get(ref.source_block_id)
                if block is None:
                    issues.append(
                        ContractValidationIssue(
                            severity="error",
                            code="unknown_source_reference",
                            path=ref_path,
                            message=(
                                "Source reference does not belong to the authoritative "
                                "module corpus."
                            ),
                        )
                    )
                    continue
                if ref.document_id != block.document_id:
                    issues.append(
                        ContractValidationIssue(
                            severity="error",
                            code="source_document_mismatch",
                            path=f"{ref_path}.document_id",
                            message="Source document does not own the referenced block.",
                        )
                    )
                if ref.page != block.page:
                    issues.append(
                        ContractValidationIssue(
                            severity="error",
                            code="source_locator_mismatch",
                            path=f"{ref_path}.page",
                            message="Source page disagrees with the authoritative block.",
                        )
                    )
                if ref.paragraph != block.paragraph:
                    issues.append(
                        ContractValidationIssue(
                            severity="error",
                            code="source_locator_mismatch",
                            path=f"{ref_path}.paragraph",
                            message=(
                                "Source paragraph disagrees with the authoritative block."
                            ),
                        )
                    )
    return tuple(issues)


class EvidenceBoundScenarioCompiler:
    """Require exact source provenance before AI-authored mechanics can publish."""

    def __init__(self, compiler: ScenarioContractCompiler | None = None):
        self.compiler = compiler or ScenarioContractCompiler()

    def compile(
        self, candidate: EvidenceBoundContractCandidate
    ) -> EvidenceBoundCompilationResult:
        compilation = self.compiler.compile(candidate.contract)
        if compilation.contract is None:
            return EvidenceBoundCompilationResult(
                decision="rejected", report=compilation.report
            )

        issues = list(compilation.report.issues)
        if compilation.contract.locations and compilation.contract.initial_scene_id is None:
            issues.append(
                ContractValidationIssue(
                    severity="error",
                    code="authored_initial_scene_missing",
                    path="initial_scene_id",
                    message=(
                        "An AI-authored location graph must identify a source-grounded "
                        "player entry point; automatic publication cannot guess one."
                    ),
                )
            )
        provenance_issues = validate_contract_provenance(
            compilation.contract,
            candidate.evidence_blocks,
        )
        issues.extend(provenance_issues)

        coverage = self._coverage_report(compilation.contract, candidate.evidence_blocks)
        for item in coverage.items:
            if item.covered:
                continue
            issues.append(
                ContractValidationIssue(
                    severity="warning",
                    code="uncovered_source_mechanic",
                    path=(
                        f"source_coverage.{item.source_block_id}."
                        f"{item.requirement_key}"
                    ),
                    message=(
                        "Source obligation is not represented by enough cited records: "
                        f"expected {item.minimum_record_count} in "
                        f"{', '.join(item.acceptable_record_kinds)}, found "
                        f"{item.matched_record_count}."
                    ),
                )
            )

        for index, assumption in enumerate(candidate.assumptions):
            issues.append(
                ContractValidationIssue(
                    severity="warning",
                    code="unresolved_authoring_assumption",
                    path=f"authoring.assumptions.{index}",
                    message=assumption,
                )
            )

        provenance_ready = bool(
            compilation.report.provenance_ready and not provenance_issues
        )
        evidence_release_ready = bool(
            compilation.report.release_ready
            and provenance_ready
            and candidate.confidence == "high"
            and not candidate.assumptions
            and coverage.blocking_complete
        )
        valid = not any(item.severity == "error" for item in issues)
        report = compilation.report.model_copy(
            update={
                "valid": valid,
                "release_ready": valid and evidence_release_ready,
                "provenance_ready": provenance_ready,
                "issues": tuple(issues),
            }
        )
        if not report.valid:
            decision = "rejected"
        elif (
            report.release_ready
        ):
            decision = "auto_publishable"
        else:
            decision = "review_required"
        return EvidenceBoundCompilationResult(
            decision=decision,
            report=report,
            contract=compilation.contract,
            coverage=coverage,
        )

    @classmethod
    def _coverage_report(
        cls,
        contract: ScenarioContract,
        evidence_blocks: tuple[EvidenceBlock, ...],
    ) -> SourceCoverageReport:
        citations: dict[
            tuple[str, str], list[tuple[ScenarioRecordKind, Any]]
        ] = {}
        for group, records in cls._record_groups(contract):
            kind = cast(ScenarioRecordKind, group)
            for record in records:
                for source_identity in {
                    (ref.source_block_id, ref.document_id)
                    for ref in record.source_refs
                }:
                    citations.setdefault(source_identity, []).append((kind, record))
        items: list[SourceCoverageItem] = []
        for block in evidence_blocks:
            cited = citations.get((block.source_block_id, block.document_id), [])
            for requirement in block.coverage_requirements:
                matches = [
                    kind
                    for kind, record in cited
                    if kind in requirement.acceptable_record_kinds
                    and cls._record_satisfies_requirement(
                        requirement.requirement_key, kind, record
                    )
                ]
                items.append(
                    SourceCoverageItem(
                        source_block_id=block.source_block_id,
                        semantic_kind=block.semantic_kind,
                        requirement_key=requirement.requirement_key,
                        acceptable_record_kinds=requirement.acceptable_record_kinds,
                        minimum_record_count=requirement.minimum_record_count,
                        matched_record_count=len(matches),
                        matched_record_kinds=tuple(sorted(set(matches))),
                        blocking=requirement.blocking,
                        covered=len(matches) >= requirement.minimum_record_count,
                    )
                )
        covered = sum(item.covered for item in items)
        blocking = tuple(item for item in items if item.blocking)
        return SourceCoverageReport(
            required_item_count=len(items),
            covered_item_count=covered,
            coverage_ratio=covered / len(items) if items else 1,
            blocking_item_count=len(blocking),
            covered_blocking_item_count=sum(item.covered for item in blocking),
            items=tuple(items),
        )

    @staticmethod
    def _executable_records(
        contract: ScenarioContract,
    ) -> list[tuple[str, tuple[SourceRef, ...]]]:
        return [
            (f"{group}.{index}", item.source_refs)
            for group, records in scenario_contract_record_groups(contract)
            for index, item in enumerate(records)
        ]

    @staticmethod
    def _record_groups(contract: ScenarioContract) -> tuple[tuple[str, tuple[Any, ...]], ...]:
        return scenario_contract_record_groups(contract)

    @staticmethod
    def _record_satisfies_requirement(
        requirement_key: str,
        kind: ScenarioRecordKind,
        record: Any,
    ) -> bool:
        if requirement_key == "explicit_checks":
            return bool(
                kind == "operators"
                and record.policy
                in {
                    "choice",
                    "required_check",
                    "optional_check",
                    "conditional_check",
                    "opposed_check",
                }
                and record.skill_choices
                and (
                    record.success_commands
                    or record.automatic_information
                    or any(
                        cue.outcome_key == "success"
                        for cue in record.narrative_cues
                    )
                )
                and (
                    record.failure_commands
                    or any(
                        cue.outcome_key == "failure"
                        for cue in record.narrative_cues
                    )
                )
            )
        if requirement_key in {"clue_path", "handout_content"}:
            if kind == "clues":
                return bool(record.discovery_operator_ids and record.public_content)
            if kind == "operators":
                return bool(
                    record.automatic_information
                    and (
                        record.always_commands
                        or record.success_commands
                    )
                )
            if kind == "entities":
                return bool(
                    record.canonical_profile.known_facts
                    or record.canonical_profile.summary
                )
            return False
        if requirement_key == "explicit_pressure":
            if kind in {"clocks", "consequence_signals"}:
                return True
            if kind == "operators":
                commands = (
                    *record.always_commands,
                    *record.success_commands,
                    *record.failure_commands,
                    *(
                        command
                        for branch in record.outcome_branches
                        for command in branch.commands
                    ),
                )
                return any(command.kind == "advance_clock" for command in commands)
            if kind == "reactive_policies":
                return any(
                    command.kind == "advance_clock"
                    for rule in record.rules
                    for command in rule.commands
                )
            return False
        if requirement_key == "explicit_ruleset_effect":
            if kind != "operators":
                return False
            commands = (
                *record.always_commands,
                *record.success_commands,
                *record.failure_commands,
                *(
                    command
                    for branch in record.outcome_branches
                    for command in branch.commands
                ),
            )
            return any(command.kind == "apply_ruleset_effect" for command in commands)
        if requirement_key == "explicit_terminal_method":
            if kind != "operators":
                return False
            return any(
                command.kind == "set_entity_status"
                or is_terminal_method_fact_command(command)
                for command in record.success_commands
            )
        if requirement_key == "explicit_terminal_observation":
            if kind != "operators":
                return False
            return any(
                is_terminal_observation_fact_command(command)
                for command in record.success_commands
            )
        return True


__all__ = [
    "EvidenceBlock",
    "EvidenceBoundCompilationResult",
    "EvidenceBoundContractCandidate",
    "EvidenceBoundScenarioCompiler",
    "SourceCoverageItem",
    "SourceCoverageReport",
    "scenario_contract_record_groups",
    "validate_contract_provenance",
]

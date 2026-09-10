"""Constrained model authoring for evidence-bound executable scenario contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator
from copy import deepcopy
from itertools import pairwise
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_kp.platform.ports.llm import ChatMessage, LlmClient
from ai_kp.platform.resolution.authoring_entity_catalog import AuthoringEntityCandidate
from ai_kp.platform.resolution.check_catalog import (
    ScenarioCheckCatalog,
    check_term_occurs_in_text,
)
from ai_kp.platform.resolution.contracts import ScenarioContract, SourceRef, WorldCommand
from ai_kp.platform.resolution.effect_catalog import ScenarioEffectCatalog
from ai_kp.platform.resolution.evidence_compiler import (
    EvidenceBlock,
    EvidenceBoundContractCandidate,
)
from ai_kp.platform.resolution.kernel import operator_outcome_path
from ai_kp.platform.resolution.location_references import (
    condition_references_location,
)
from ai_kp.platform.resolution.location_travel import canonical_travel_operator_ids
from ai_kp.platform.resolution.scenario_authoring_audit import (
    bounded_authoring_assumptions as _bounded_authoring_assumptions,
)
from ai_kp.platform.resolution.scenario_authoring_audit import (
    is_server_review_diagnostic as _is_server_review_diagnostic,
)
from ai_kp.platform.resolution.scenario_clue_alignment import (
    align_automatic_clue_state,
)
from ai_kp.platform.resolution.scenario_clue_supplement import (
    CoverageClueEnvelope,
    coverage_clue_schema_json,
    materialize_clue_envelope,
    supports_clue_materialization,
)
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.platform.resolution.scenario_effect_repair import (
    contract_invalid_ruleset_effects,
)
from ai_kp.platform.resolution.scenario_ending_authority import (
    build_ending_catalogs,
    build_ending_operator_candidates,
    build_ending_state_candidates,
    merge_equivalent_endings,
)
from ai_kp.platform.resolution.scenario_ending_authority import (
    contract_compiler_proven_premature_endings as _contract_premature_endings,
)
from ai_kp.platform.resolution.scenario_ending_supplement import (
    SERVER_COVERAGE_ENDING_ASSUMPTION_PREFIX,
    CoverageEndingEnvelope,
    EndingOperatorCandidate,
    EndingStateCandidate,
    coverage_ending_schema_json,
    deterministic_ending_envelope,
    materialize_ending_envelope,
    narrow_ending_envelope_payload,
    supports_ending_materialization,
)
from ai_kp.platform.resolution.scenario_ir import (
    ScenarioIrAssembler,
    ScenarioIrBatch,
)
from ai_kp.platform.resolution.scenario_ir_models import (
    IrAction,
    ScenarioCheckMapping,
    ScenarioIrNormalization,
)
from ai_kp.platform.resolution.scenario_ir_repair import (
    ScenarioIrCitationRepairEnvelope,
    ScenarioIrCitationRepairPlan,
    ScenarioIrRepairDiagnostic,
    ScenarioIrRepairEnvelope,
    ScenarioIrRepairPlan,
    apply_repair_envelope,
    apply_source_citation_envelope,
    contract_invalid_reactive_children,
    decode_source_citation_repair,
    discard_repair_targets,
    discard_unknown_source_records,
    plan_source_citation_repairs,
    plan_validation_repairs,
)
from ai_kp.platform.resolution.scenario_location_identity import (
    source_supports_playable_location,
)
from ai_kp.platform.resolution.scenario_supplement import (
    SERVER_ACTION_GOAL_BOUNDARY_ASSUMPTION_PREFIX,
    SERVER_COVERAGE_ACTION_ASSUMPTION_PREFIX,
    CoverageActionEnvelope,
    CoverageActionProposal,
    ensure_unspecified_check_goal_boundaries,
    materialize_action_envelope,
    supports_action_materialization,
)
from ai_kp.platform.resolution.scenario_terminal_method import (
    TerminalEntityCandidate,
    build_terminal_entity_candidates,
    is_terminal_method_fact_command,
    supports_terminal_method_materialization,
)
from ai_kp.platform.resolution.scenario_terminal_method_author import (
    TerminalMethodSupplementAuthor,
)
from ai_kp.platform.resolution.scenario_terminal_observation import (
    is_terminal_observation_fact_command,
    materialize_terminal_observation_actions,
    supports_terminal_observation_materialization,
)
from ai_kp.platform.resolution.scenario_topology_agent import (
    ScenarioTopologyRepairAgent,
    TopologyEvidence,
)
from ai_kp.platform.resolution.source_coverage import (
    SourceCoverageRequirement,
    SourceCoverageSupplementTarget,
    extract_terminal_observation_clauses,
    infer_source_coverage_requirements,
)
from ai_kp.platform.structured_json import StructuredJsonError, decode_json_object

# The model emits local records, not one deeply nested authoritative contract.
_IR_SCHEMA = ScenarioIrBatch.model_json_schema()
_IR_JSON_SCHEMA = json.dumps(
    _IR_SCHEMA,
    ensure_ascii=False,
    separators=(",", ":"),
)
_REPAIR_JSON_SCHEMA = json.dumps(
    ScenarioIrRepairEnvelope.model_json_schema(),
    ensure_ascii=False,
    separators=(",", ":"),
)
_CITATION_REPAIR_JSON_SCHEMA = json.dumps(
    ScenarioIrCitationRepairEnvelope.model_json_schema(),
    ensure_ascii=False,
    separators=(",", ":"),
)
_PARTITION_BLOCK_LIMIT = 16
_PARTITION_CHARACTER_LIMIT = 28_000
_REVIEW_REPAIR_BATCH_SIZE = 1

_CONTRACT_KIND_TO_IR_FIELD = {
    "locations": "locations",
    "location_links": "location_links",
    "entities": "entities",
    "clocks": "clocks",
    "resources": "resources",
    "clues": "clues",
    "operators": "actions",
    "task_methods": "task_methods",
    "reactive_policies": "reactive_policies",
    "consequence_signals": "consequence_signals",
    "endings": "endings",
}
_CONTRACT_RECORD_ID_FIELDS = {
    "locations": "location_id",
    "entities": "entity_id",
    "clocks": "clock_id",
    "resources": "resource_id",
    "clues": "clue_id",
    "operators": "operator_id",
    "task_methods": "method_id",
    "reactive_policies": "policy_id",
    "consequence_signals": "signal_id",
    "endings": "ending_id",
}
_WORLD_COMMAND_ALIASES = {
    "set_fact": {"fact_path": "path", "fact_value": "value"},
    "set_entity_status": {"status": "value"},
    "set_scene": {"scene_id": "value"},
    "adjust_resource": {"resource_id": "path", "amount": "delta"},
    "advance_clock": {"amount": "delta"},
    "emit_event": {"event": "event_type"},
}


def _coverage_supplement_json_schema(
    targets: tuple[SourceCoverageSupplementTarget, ...],
) -> str:
    """Keep weak-model supplement context limited to server-allowed record kinds."""

    selected_fields = {
        _CONTRACT_KIND_TO_IR_FIELD[kind]
        for target in targets
        for kind in target.acceptable_record_kinds
    }
    property_order = (
        "confidence",
        "assumptions",
        *_CONTRACT_KIND_TO_IR_FIELD.values(),
    )
    properties = {
        key: _IR_SCHEMA["properties"][key]
        for key in property_order
        if key in {"confidence", "assumptions", *selected_fields}
    }
    definitions: dict[str, Any] = {}

    def include_references(value: Any) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                name = reference.removeprefix("#/$defs/")
                if name not in definitions:
                    definitions[name] = _IR_SCHEMA["$defs"][name]
                    include_references(definitions[name])
            for nested in value.values():
                include_references(nested)
        elif isinstance(value, list):
            for nested in value:
                include_references(nested)

    include_references(properties)
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if definitions:
        schema["$defs"] = definitions
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"))


class ScenarioAuthoringEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    source_block_id: str = Field(min_length=1, max_length=160)
    document_id: str = Field(min_length=1, max_length=160)
    title: str = Field(default="", max_length=240)
    text: str = Field(min_length=1, max_length=16000)
    page: int | None = Field(default=None, ge=1)
    paragraph: int | None = Field(default=None, ge=0)
    semantic_kind: str = Field(default="text", min_length=1, max_length=80)
    classification_confidence: float = Field(default=0.5, ge=0, le=1)
    heading_level: int | None = Field(default=None, ge=1, le=9)
    section_path: tuple[str, ...] = Field(default=(), max_length=16)
    scene_key: str | None = Field(default=None, max_length=160)
    coverage_requirements: tuple[SourceCoverageRequirement, ...] = ()
    entity_candidates: tuple[AuthoringEntityCandidate, ...] = Field(default=(), max_length=64)

    def public_descriptor(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def evidence_block(self) -> EvidenceBlock:
        current_mechanics = {
            item.requirement_key: item
            for item in infer_source_coverage_requirements(
                semantic_kind=self.semantic_kind,
                classification_confidence=self.classification_confidence,
                text=self.text,
            )
            if item.requirement_key
            in {
                "explicit_checks",
                "explicit_terminal_observation",
                "ending_rule",
            }
        }
        coverage_requirements = tuple(
            current_mechanics[item.requirement_key]
            for item in self.coverage_requirements
            if item.requirement_key in current_mechanics
        ) + tuple(
            item
            for item in self.coverage_requirements
            if item.requirement_key
            not in {
                "explicit_checks",
                "explicit_terminal_observation",
                "ending_rule",
            }
        )
        return EvidenceBlock(
            source_block_id=self.source_block_id,
            document_id=self.document_id,
            page=self.page,
            paragraph=self.paragraph,
            text_hash=hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
            semantic_kind=self.semantic_kind,
            classification_confidence=self.classification_confidence,
            # Persisted jobs can outlive improvements to deterministic
            # inference. Revalidate mechanically inferred obligations from the
            # immutable source text instead of carrying stale counts forever.
            coverage_requirements=coverage_requirements,
        )

    def source_ref(self) -> SourceRef:
        return SourceRef(
            source_block_id=self.source_block_id,
            document_id=self.document_id,
            page=self.page,
            paragraph=self.paragraph,
        )


class ScenarioContractAuthoringResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, arbitrary_types_allowed=True
    )

    candidate: EvidenceBoundContractCandidate | None
    attempt_count: int = Field(ge=1)
    partition_count: int = Field(ge=1)
    completed_partition_count: int = Field(ge=0)
    validation_errors: tuple[str, ...] = ()
    check_mappings: tuple[ScenarioCheckMapping, ...] = ()
    normalizations: tuple[ScenarioIrNormalization, ...] = ()
    repair_diagnostics: tuple[ScenarioIrRepairDiagnostic, ...] = ()


class ScenarioPartitionAuthoringResult(BaseModel):
    """Serializable output for one independently retryable evidence partition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    batch: ScenarioIrBatch | None
    attempt_count: int = Field(ge=1)
    validation_errors: tuple[str, ...] = ()
    repair_diagnostics: tuple[ScenarioIrRepairDiagnostic, ...] = ()


class ScenarioContractReviewIssue(BaseModel):
    """A reviewer finding anchored to one replaceable contract record."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    group: Literal[
        "locations",
        "entities",
        "clocks",
        "resources",
        "clues",
        "operators",
        "task_methods",
        "reactive_policies",
        "consequence_signals",
        "endings",
    ]
    record_id: str = Field(min_length=1, max_length=160)
    problem: str = Field(min_length=1, max_length=800)
    source_block_ids: tuple[str, ...] = Field(default=(), max_length=16)


class ScenarioContractReview(BaseModel):
    """Closed semantic review result; invalid reviewer output means rejection."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    decision: Literal["approve", "reject"]
    review_kind: Literal["independent_ai", "deterministic_compiler"] = (
        "independent_ai"
    )
    findings: tuple[str, ...] = Field(default=(), max_length=16)
    issues: tuple[ScenarioContractReviewIssue, ...] = Field(default=(), max_length=16)
    # Computed by the server from the union of every bounded evidence review.
    # The reviewer cannot choose these indices; persisting them lets the worker
    # make one auditable, metadata-only contraction without trusting prose.
    unsupported_assumption_indices: tuple[int, ...] = Field(
        default=(), max_length=64
    )
    # A rejection is already fail-closed, so weak reviewers may omit this field.
    # Approval still requires an explicit true value below.
    assumptions_resolved: bool = False


class _ScenarioContractReviewBatch(BaseModel):
    """Bounded reviewer transport aggregated by the server."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["approve", "reject"]
    findings: tuple[str, ...] = Field(default=(), max_length=16)
    issues: tuple[ScenarioContractReviewIssue, ...] = Field(default=(), max_length=16)
    supported_assumption_indices: tuple[int, ...] = Field(default=(), max_length=64)


def _narrow_review_batch_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Bound verbose weak-reviewer output without converting it into approval.

    Oversized findings are transport noise, but an oversized rejection can still
    contain useful record-addressed issues.  Keep a deterministic prefix and force
    rejection so omitted tail entries are handled by the next review cycle rather
    than silently treated as approved.
    """

    narrowed = deepcopy(payload)
    overflow = False
    for field, limit in (("findings", 16), ("issues", 16)):
        values = narrowed.get(field)
        if isinstance(values, list) and len(values) > limit:
            narrowed[field] = values[:limit]
            overflow = True
    findings = narrowed.get("findings")
    if isinstance(findings, list):
        bounded_findings = []
        for finding in findings:
            if isinstance(finding, str) and len(finding) > 800:
                bounded_findings.append(finding[:800])
                overflow = True
            else:
                bounded_findings.append(finding)
        narrowed["findings"] = bounded_findings
    issues = narrowed.get("issues")
    if isinstance(issues, list):
        bounded_issues = []
        for issue in issues:
            if not isinstance(issue, dict):
                bounded_issues.append(issue)
                continue
            bounded_issue = dict(issue)
            problem = bounded_issue.get("problem")
            if isinstance(problem, str) and len(problem) > 800:
                bounded_issue["problem"] = problem[:800]
                overflow = True
            source_ids = bounded_issue.get("source_block_ids")
            if isinstance(source_ids, list):
                unique_source_ids = list(dict.fromkeys(source_ids))
                if len(unique_source_ids) != len(source_ids):
                    overflow = True
                if len(unique_source_ids) > 16:
                    unique_source_ids = unique_source_ids[:16]
                    overflow = True
                bounded_issue["source_block_ids"] = unique_source_ids
            bounded_issues.append(bounded_issue)
        narrowed["issues"] = bounded_issues
    indices = narrowed.get("supported_assumption_indices")
    if isinstance(indices, list) and len(indices) > 64:
        narrowed["supported_assumption_indices"] = indices[:64]
        overflow = True
    if overflow:
        narrowed["decision"] = "reject"
    return narrowed


class _ScenarioContractReviewRepair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group: str
    record_id: str
    replacement: dict[str, Any] | None


class _ScenarioContractReviewRepairEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    repairs: tuple[_ScenarioContractReviewRepair, ...] = Field(max_length=16)


_REVIEW_TRANSPORT_LEAK = re.compile(
    r'^\s*\{?\s*"?supported_assumption_indices"?\s*:\s*'
    r"\[\s*\d*(?:\s*,\s*\d+)*\s*\]\s*\}?\s*$"
)

_REVIEW_ENVELOPE_FRAGMENT = re.compile(
    r"^\s*[\"']?(?:issues|findings|decision|supported_assumption_indices)"
    r"[\"']?\s*:\s*[\[{]{0,2}\s*$",
    re.IGNORECASE,
)


def _is_review_transport_leak(value: str) -> bool:
    return bool(
        _REVIEW_TRANSPORT_LEAK.fullmatch(value)
        or _REVIEW_ENVELOPE_FRAGMENT.fullmatch(value)
    )


_POSITIVE_REVIEW_MARKERS = re.compile(
    r"(?:\b(?:supported|consistent|correct|valid|no\s+(?:issue|problem)s?)\b"
    r"|与来源一致|来源支持|符合来源|无问题|正确)",
    re.IGNORECASE,
)
_NEGATIVE_REVIEW_MARKERS = re.compile(
    r"(?:\b(?:unsupported|not\s+supported|inconsistent|incorrect|invalid|missing|"
    r"contradict(?:s|ed|ory|ion)?)\b|不受[^\s。；，]{0,12}来源支持|没有来源支持|"
    r"无来源|不一致|未一致|"
    r"不符合|未符合|缺少|缺失|错误|矛盾)",
    re.IGNORECASE,
)
_EXPLICIT_NON_PROBLEM_REVIEW = re.compile(
    r"(?:未发现[^\s。；]{0,40}(?:不支持|矛盾|错误|问题)|"
    r"不\s*reject|不应拒绝|不构成(?:问题|缺陷))",
    re.IGNORECASE,
)


def _is_non_problem_review_statement(value: str) -> bool:
    """Reject praise accidentally emitted in a reviewer's issue channel."""

    if _EXPLICIT_NON_PROBLEM_REVIEW.search(value):
        return True
    return not _NEGATIVE_REVIEW_MARKERS.search(value) and bool(
        _POSITIVE_REVIEW_MARKERS.search(value)
    )


def _normalize_review_issue(
    issue: ScenarioContractReviewIssue,
    candidate: EvidenceBoundContractCandidate,
) -> ScenarioContractReviewIssue | None:
    """Correct weak-model group labels only when a record id is globally unique."""

    if _is_non_problem_review_statement(issue.problem):
        return None

    matches: list[str] = []
    for group, id_field in _CONTRACT_RECORD_ID_FIELDS.items():
        if any(
            getattr(record, id_field) == issue.record_id
            for record in getattr(candidate.contract, group)
        ):
            matches.append(group)
    if len(matches) != 1:
        return None
    return issue.model_copy(update={"group": matches[0]})


def _review_issue_contradicts_authoritative_record(
    issue: ScenarioContractReviewIssue,
    candidate: EvidenceBoundContractCandidate,
    check_catalog: ScenarioCheckCatalog | None = None,
) -> bool:
    """Discard reviewer claims disproved by typed, server-validated record fields."""

    normalized = _normalize_review_issue(issue, candidate)
    if normalized is None:
        return False
    if normalized.group == "operators" and (
        f"{SERVER_COVERAGE_ACTION_ASSUMPTION_PREFIX}{normalized.record_id}"
        in candidate.assumptions
        or re.fullmatch(r"supp\d+_\d+_action_\d+", normalized.record_id)
    ):
        # Coverage actions are assembled from the ruleset check/effect catalogs;
        # the model only supplied bounded display text. The reserved supplement
        # namespace remains authoritative even after the diagnostic list reaches
        # its persistence cap; a second model cannot overrule that provenance.
        return True
    if normalized.group == "operators":
        operator = next(
            item
            for item in candidate.contract.operators
            if item.operator_id == normalized.record_id
        )
        boundary_path = f"action_goals.{normalized.record_id}.achieved"
        has_server_boundary_diagnostic = (
            f"{SERVER_ACTION_GOAL_BOUNDARY_ASSUMPTION_PREFIX}{normalized.record_id}"
            in candidate.assumptions
        )
        has_success_boundary = any(
            command.kind == "set_fact"
            and command.path == boundary_path
            and command.value is True
            for command in operator.success_commands
        )
        has_failure_boundary = any(
            command.kind == "set_fact"
            and command.path == boundary_path
            and command.value is False
            for command in operator.failure_commands
        )
        has_server_boundary = has_server_boundary_diagnostic or (
            has_success_boundary and has_failure_boundary
        )
        problem = re.sub(
            r"[^\w\u3400-\u9fff]+", "", normalized.problem.casefold()
        ).replace("_", "")
        targets_boundary = "actiongoals" in problem or (
            any(token in problem for token in ("来源未定义", "来源外", "无来源"))
            and any(token in problem for token in ("成功", "失败", "outcome", "branch"))
        )
        if has_server_boundary and targets_boundary:
            # The paired boolean is an outcome ledger owned by the server,
            # not a source-authored reward or consequence. Protect only review
            # claims aimed at that ledger; other issues on the operator remain.
            return True
    if normalized.group == "endings" and (
        f"{SERVER_COVERAGE_ENDING_ASSUMPTION_PREFIX}{normalized.record_id}"
        in candidate.assumptions
        or re.fullmatch(
            r"supp\d+_\d+_source_ending_\d+", normalized.record_id
        )
    ):
        # This ending contains only a server-parsed source condition and
        # kernel-owned outcome/trigger path. The model never supplied state or
        # effects, so a later reviewer cannot revoke that deterministic record.
        return True
    problem = normalized.problem.casefold()
    compact_problem = re.sub(r"[^\w\u3400-\u9fff]+", "", problem).replace("_", "")
    if normalized.group == "entities" and any(
        token in problem
        for token in (
            "initial location removal",
            "initial_location_id removal",
            "初始地点被移除",
            "移除初始地点",
        )
    ) and any(
        assumption.startswith(
            f"Unknown initial location was removed from entity {normalized.record_id}:"
        )
        for assumption in candidate.assumptions
    ):
        # The assembler owns referential integrity. When a weak partition names a
        # location that does not exist in the assembled graph, retaining the entity
        # with an unknown location is the lossless, fail-closed representation. A
        # reviewer may assess the fictional entity, but cannot demand restoration of
        # a dangling foreign key that the kernel deliberately removed.
        return True
    if normalized.group == "locations" and any(
        token in problem for token in ("visibility", "可见性", "可见范围")
    ):
        # Visibility is the kernel's disclosure policy, not a claim about the
        # fictional place. Source entailment validates the location itself;
        # source prose need not name this internal enum.
        return True
    if normalized.group == "clues" and any(
        token in problem
        for token in (
            "fact_path",
            "fact path",
            "fact value",
            "fact_value",
            "事实值",
            "clues.discovered",
        )
    ):
        # A clue fact is an opaque kernel handle whose boolean value means
        # "discovered". Entailment review covers the clue title/source, not the
        # server-owned state encoding.
        return True
    if normalized.group == "clues" and any(
        token in problem for token in ("no effect", "缺少效果", "没有效果")
    ):
        clue = next(
            (
                item
                for item in candidate.contract.clues
                if item.clue_id == normalized.record_id
            ),
            None,
        )
        if clue is not None and clue.discovery_operator_ids:
            return True
    if normalized.group == "operators" and "clues.discovered" in problem:
        operator = next(
            (
                item
                for item in candidate.contract.operators
                if item.operator_id == normalized.record_id
            ),
            None,
        )
        if operator is not None and any(
            command.kind == "set_fact"
            and (command.path or "").startswith("clues.discovered.")
            for command in (
                *operator.always_commands,
                *operator.success_commands,
                *operator.failure_commands,
            )
        ):
            return True
    if normalized.group != "operators" and any(
        token in problem
        for token in (
            "skill",
            "skill_key",
            "skill key",
            "skill choice",
            "skill_choice",
            "技能",
            "技能键",
            "技能选择",
        )
    ):
        # Skill selections are fields of action operators. A reviewer cannot
        # attach a skill-schema complaint to a location, clue, entity, or
        # ending and thereby gain authority over an unrelated record type.
        return True
    if normalized.group == "clocks" and any(
        assumption.startswith("Server fail-forward pressure applied: ")
        and assumption.split(" -> ", 1)[0].rsplit(": ", 1)[-1] in problem
        and assumption.endswith(f"-> {normalized.record_id}")
        for assumption in candidate.assumptions
    ):
        # Applying a source-backed clock as a generic action cost is platform
        # policy; the clock's fictional meaning remains independently reviewable.
        return True
    if normalized.group != "operators":
        return False
    operator = next(
        (
            item
            for item in candidate.contract.operators
            if item.operator_id == normalized.record_id
        ),
        None,
    )
    if operator is None:
        return False
    if any(
        assumption.startswith(
            f"Server fail-forward pressure applied: {operator.operator_id} -> "
        )
        for assumption in candidate.assumptions
    ) and any(
        token in problem
        for token in (
            "failure_commands",
            "pushed_failure",
            "failure stakes",
            "失败风险",
            "推动失败",
            "推进",
            "时钟",
            "clock",
        )
    ):
        return True
    operator_commands = (
        *operator.always_commands,
        *operator.success_commands,
        *operator.failure_commands,
        *(
            command
            for branch in operator.outcome_branches
            for command in branch.commands
        ),
    )
    if any(
        clue.fact_path in problem
        and any(
            command.kind == "set_fact" and command.path == clue.fact_path
            for command in operator_commands
        )
        for clue in candidate.contract.clues
    ) and any(
        token in problem
        for token in ("fact path", "事实路径", "路径或名称", "未明确该事实")
    ):
        # The public clue content and branch remain reviewable. Its opaque
        # persistence handle does not need to be named by source prose.
        return True
    if operator.policy == "automatic" and any(
        token in problem
        for token in (
            "missing success",
            "no success",
            "缺少成功命令",
            "没有成功命令",
            "成功命令缺失",
        )
    ):
        return True
    for choice in operator.skill_choices:
        aliases = {
            choice.skill_key.casefold(),
            choice.skill_key.rsplit(".", 1)[-1].casefold(),
        }
        if check_catalog is not None:
            entry = next(
                (
                    item
                    for item in check_catalog.entries
                    if item.check_key == choice.skill_key
                ),
                None,
            )
            if entry is not None:
                aliases.update(
                    value.casefold()
                    for value in (entry.display_name, *entry.direct_aliases)
                )
        if any(
            (
                re.search(rf"(?<![\w.]){re.escape(alias)}(?![\w.])", problem)
                if alias.isascii()
                else alias in problem
            )
            or re.sub(r"[^\w\u3400-\u9fff]+", "", alias).replace("_", "")
            in compact_problem
            for alias in aliases
        ):
            # Skill choices have already survived the closed ruleset catalog and
            # source-allowance reconciliation. Review may assess the surrounding
            # narrative, but cannot relitigate an accepted key or its aliases.
            return True
    return False


def _remove_contract_record_with_dependents(
    payload: dict[str, Any], group: str, record_id: str
) -> dict[str, Any]:
    """Conservatively remove one record and direct typed dependents."""

    contracted = deepcopy(payload)
    id_field = _CONTRACT_RECORD_ID_FIELDS[group]
    contracted[group] = [
        item for item in contracted[group] if item.get(id_field) != record_id
    ]
    if group == "locations":
        if contracted.get("initial_scene_id") == record_id:
            return payload
        contracted["location_links"] = [
            link
            for link in contracted.get("location_links", [])
            if record_id
            not in (link.get("from_location_id"), link.get("to_location_id"))
        ]
        for entity in contracted.get("entities", []):
            if entity.get("initial_location_id") != record_id:
                continue
            entity["initial_location_id"] = None
            runtime = entity.get("initial_runtime")
            if isinstance(runtime, dict):
                runtime["location_id"] = None
        contracted = _contract_location_command_dependents(contracted, record_id)
        return contracted
    if group != "operators":
        return contracted
    outcome_path = operator_outcome_path(record_id)
    contracted["task_methods"] = [
        method
        for method in contracted["task_methods"]
        if not any(step.get("operator_id") == record_id for step in method.get("steps", []))
    ]
    kept_clues: list[dict[str, Any]] = []
    for clue in contracted["clues"]:
        discovery_ids = [
            item
            for item in clue.get("discovery_operator_ids", [])
            if item != record_id
        ]
        clue["discovery_operator_ids"] = discovery_ids
        if clue.get("importance") == "core" and not discovery_ids:
            continue
        kept_clues.append(clue)
    contracted["clues"] = kept_clues
    contracted["endings"] = [
        ending
        for ending in contracted["endings"]
        if not any(
            condition.get("path") == outcome_path
            for condition in (
                *ending.get("all_conditions", []),
                *ending.get("any_conditions", []),
            )
        )
    ]
    return contracted


def _command_references_location(command: dict[str, Any], location_id: str) -> bool:
    return command.get("kind") in {"set_scene", "move_actor"} and (
        command.get("value") == location_id
    )


def _commands_reference_location(
    commands: list[dict[str, Any]], location_id: str
) -> bool:
    return any(_command_references_location(command, location_id) for command in commands)


def _conditions_reference_location(
    conditions: list[dict[str, Any]], location_id: str
) -> bool:
    return any(
        condition_references_location(
            str(condition.get("path") or ""), condition.get("value"), location_id
        )
        for condition in conditions
    )


def _contract_location_command_dependents(
    payload: dict[str, Any], location_id: str
) -> dict[str, Any]:
    """Remove executable records whose effect requires a contracted location.

    Keeping a command while deleting its destination makes the whole contract
    invalid. Rewriting the destination would invent causality, so contraction
    removes the smallest typed executable container and lets later playability
    proof decide whether the remaining graph is still sufficient.
    """

    contracted = payload
    removed_operator_ids: list[str] = []
    for operator in contracted.get("operators", []):
        command_groups = [
            operator.get("always_commands", []),
            operator.get("success_commands", []),
            operator.get("failure_commands", []),
            *(
                branch.get("commands", [])
                for branch in operator.get("outcome_branches", [])
            ),
        ]
        if _conditions_reference_location(
            operator.get("preconditions", []), location_id
        ) or any(
            _commands_reference_location(commands, location_id)
            for commands in command_groups
        ):
            removed_operator_ids.append(str(operator["operator_id"]))
    for operator_id in removed_operator_ids:
        contracted = _remove_contract_record_with_dependents(
            contracted, "operators", operator_id
        )

    contracted["location_links"] = [
        link
        for link in contracted.get("location_links", [])
        if not _conditions_reference_location(
            link.get("preconditions", []), location_id
        )
    ]
    contracted["task_methods"] = [
        method
        for method in contracted.get("task_methods", [])
        if not _conditions_reference_location(
            method.get("preconditions", []), location_id
        )
    ]

    removed_obligation_ids = {
        str(obligation["obligation_id"])
        for obligation in contracted.get("response_obligations", [])
        if _conditions_reference_location(
            obligation.get("conditions", []), location_id
        )
    }
    if removed_obligation_ids:
        contracted["response_obligations"] = [
            obligation
            for obligation in contracted.get("response_obligations", [])
            if obligation.get("obligation_id") not in removed_obligation_ids
        ]
        for operator in contracted.get("operators", []):
            operator["response_obligation_ids"] = [
                obligation_id
                for obligation_id in operator.get("response_obligation_ids", [])
                if obligation_id not in removed_obligation_ids
            ]

    retained_policies: list[dict[str, Any]] = []
    for policy in contracted.get("reactive_policies", []):
        rules = [
            rule
            for rule in policy.get("rules", [])
            if not _conditions_reference_location(
                rule.get("conditions", []), location_id
            )
            and not _commands_reference_location(rule.get("commands", []), location_id)
        ]
        if rules:
            policy["rules"] = rules
            retained_policies.append(policy)
    contracted["reactive_policies"] = retained_policies

    contracted["trigger_rules"] = [
        trigger
        for trigger in contracted.get("trigger_rules", [])
        if not _conditions_reference_location(
            trigger.get("conditions", []), location_id
        )
        and not _commands_reference_location(
            trigger.get("commands", []), location_id
        )
    ]

    retained_signals: list[dict[str, Any]] = []
    for signal in contracted.get("consequence_signals", []):
        bands = [
            band
            for band in signal.get("bands", [])
            if not _conditions_reference_location(
                band.get("all_conditions", []), location_id
            )
        ]
        if bands:
            signal["bands"] = bands
            retained_signals.append(signal)
    contracted["consequence_signals"] = retained_signals
    retained_pressure_tracks: list[dict[str, Any]] = []
    for track in contracted.get("pressure_tracks", []):
        stages = [
            stage
            for stage in track.get("stages", [])
            if not _commands_reference_location(
                stage.get("commands", []), location_id
            )
        ]
        if stages:
            track["stages"] = stages
            retained_pressure_tracks.append(track)
    contracted["pressure_tracks"] = retained_pressure_tracks
    contracted["endings"] = [
        ending
        for ending in contracted.get("endings", [])
        if not _conditions_reference_location(
            [
                *ending.get("all_conditions", []),
                *ending.get("any_conditions", []),
            ],
            location_id,
        )
        and not _commands_reference_location(ending.get("commands", []), location_id)
    ]
    return contracted


def _issues_explicitly_named_in_findings(
    findings: tuple[str, ...],
    candidate: EvidenceBoundContractCandidate,
) -> tuple[ScenarioContractReviewIssue, ...]:
    """Recover record anchors only from exact ids literally named by the reviewer."""

    records: dict[str, tuple[str, tuple[str, ...]]] = {}
    duplicate_ids: set[str] = set()
    titled_records: dict[str, tuple[str, str, tuple[str, ...]]] = {}
    duplicate_titles: set[str] = set()
    for group, id_field in _CONTRACT_RECORD_ID_FIELDS.items():
        for record in getattr(candidate.contract, group):
            record_id = getattr(record, id_field)
            source_block_ids = tuple(ref.source_block_id for ref in record.source_refs)
            if record_id in records:
                duplicate_ids.add(record_id)
                continue
            records[record_id] = (group, source_block_ids)
            title = getattr(record, "title", "").strip()
            if len(title) >= 8:
                if title in titled_records:
                    duplicate_titles.add(title)
                else:
                    titled_records[title] = (group, record_id, source_block_ids)
    recovered: list[ScenarioContractReviewIssue] = []
    for finding in findings:
        if finding.strip().casefold() in {"none", "无", "无问题"}:
            continue
        for record_id, (group, source_block_ids) in records.items():
            if record_id in duplicate_ids:
                continue
            if re.search(rf"(?<![\w-]){re.escape(record_id)}(?![\w-])", finding):
                recovered.append(
                    ScenarioContractReviewIssue(
                        group=group,
                        record_id=record_id,
                        problem=finding,
                        source_block_ids=source_block_ids,
                    )
                )
        for title, (group, record_id, source_block_ids) in titled_records.items():
            if title in duplicate_titles or title not in finding:
                continue
            recovered.append(
                ScenarioContractReviewIssue(
                    group=group,
                    record_id=record_id,
                    problem=finding,
                    source_block_ids=source_block_ids,
                )
            )
    return tuple(recovered)


def _iter_state_paths(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        path = value.get("path")
        if isinstance(path, str):
            yield path
        for nested in value.values():
            yield from _iter_state_paths(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_state_paths(nested)


def _count_exact_string(value: Any, target: str) -> int:
    if isinstance(value, dict):
        return sum(_count_exact_string(item, target) for item in value.values())
    if isinstance(value, list):
        return sum(_count_exact_string(item, target) for item in value)
    return int(value == target)


def _replace_exact_strings(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_exact_strings(item, replacements)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_exact_strings(item, replacements) for item in value]
    return replacements.get(value, value) if isinstance(value, str) else value


def _deduplicate_semantically_identical_operators(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Collapse exact duplicate executable records and preserve all inbound links."""

    contracted = deepcopy(payload)
    groups: dict[str, list[str]] = {}
    for operator in contracted["operators"]:
        semantic = {key: value for key, value in operator.items() if key != "operator_id"}
        signature = json.dumps(
            semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        groups.setdefault(signature, []).append(str(operator["operator_id"]))

    replacements: dict[str, str] = {}
    for operator_ids in groups.values():
        if len(operator_ids) < 2:
            continue
        canonical = max(
            operator_ids,
            key=lambda item: (
                _count_exact_string(contracted, item)
                + _count_exact_string(contracted, operator_outcome_path(item)),
                item,
            ),
        )
        for duplicate in operator_ids:
            if duplicate != canonical:
                replacements[duplicate] = canonical
                replacements[operator_outcome_path(duplicate)] = operator_outcome_path(
                    canonical
                )
    if not replacements:
        return contracted
    contracted = _replace_exact_strings(contracted, replacements)
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    for operator in contracted["operators"]:
        operator_id = str(operator["operator_id"])
        if operator_id in seen:
            continue
        seen.add(operator_id)
        kept.append(operator)
    contracted["operators"] = kept
    return contracted


def candidate_after_independent_review(
    candidate: EvidenceBoundContractCandidate,
    review: ScenarioContractReview,
) -> EvidenceBoundContractCandidate:
    """Apply a closed review decision without erasing its persisted audit input."""

    if review.decision == "approve":
        return candidate.model_copy(
            update={"confidence": "high", "assumptions": ()}
        )
    review_assumptions = tuple(
        f"AI review: {finding}" for finding in review.findings
    )
    return candidate.model_copy(
        update={
            "assumptions": tuple(
                dict.fromkeys(
                    (
                        *review_assumptions,
                        "Independent AI review did not approve automatic publication.",
                        *candidate.assumptions,
                    )
                )
            )[:64]
        }
    )


class ConstrainedScenarioContractAuthoringAdapter:
    """Let a model write contract data, never authority bindings or executable code."""

    def __init__(
        self,
        llm: LlmClient,
        *,
        check_catalog: ScenarioCheckCatalog | None = None,
        effect_catalog: ScenarioEffectCatalog | None = None,
    ):
        self.llm = llm
        self.check_catalog = check_catalog
        self.effect_catalog = effect_catalog

    def reconcile_candidate_authority(
        self,
        candidate: EvidenceBoundContractCandidate,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
    ) -> EvidenceBoundContractCandidate:
        """Reapply server-owned authority after model review or checkpoint recovery.

        Review replacements may select an existing evidence block, but locator fields
        and ruleset-effect semantics never belong to the model. Historical checkpoints
        can also contain executable operators without provenance, so contract those
        records before the strict compiler sees the recovered candidate.
        """

        payload = candidate.contract.model_dump(mode="json")
        canonical_refs = {
            item.source_block_id: item.source_ref().model_dump(mode="json")
            for item in evidence
        }
        for group in (
            "locations",
            "location_links",
            "entities",
            "clocks",
            "resources",
            "clues",
            "operators",
            "task_methods",
            "reactive_policies",
            "consequence_signals",
            "endings",
        ):
            for record in payload[group]:
                rebound: list[dict[str, Any]] = []
                seen: set[str] = set()
                for source_ref in record.get("source_refs", []):
                    source_id = source_ref.get("source_block_id")
                    if source_id in seen:
                        continue
                    seen.add(source_id)
                    rebound.append(canonical_refs.get(source_id, source_ref))
                record["source_refs"] = rebound

        # A checkpoint produced by an older or interrupted review may already
        # have lost provenance. Contract any such record when doing so preserves
        # a structurally valid graph; otherwise leave it for the strict compiler
        # to reject rather than guessing an evidence source.
        for group, id_field in _CONTRACT_RECORD_ID_FIELDS.items():
            unsupported_ids = tuple(
                record[id_field]
                for record in payload[group]
                if not record.get("source_refs")
            )
            for record_id in unsupported_ids:
                tentative = _remove_contract_record_with_dependents(
                    payload, group, record_id
                )
                try:
                    ScenarioContract.model_validate(tentative)
                except ValidationError:
                    continue
                payload = tentative

        location_role_diagnostics: list[str] = []
        evidence_texts = {item.source_block_id: item.text for item in evidence}
        evidence_titles = {item.source_block_id: item.title for item in evidence}
        evidence_sections = {
            item.source_block_id: item.section_path for item in evidence
        }
        evidence_scene_keys = {
            item.source_block_id: item.scene_key for item in evidence if item.scene_key
        }
        evidence_semantic_kinds = {
            item.source_block_id: item.semantic_kind for item in evidence
        }
        for location in tuple(payload.get("locations", [])):
            location_id = str(location["location_id"])
            if location_id.startswith("system_investigation_phase_"):
                continue
            source_ids = tuple(
                str(ref["source_block_id"])
                for ref in location.get("source_refs", [])
                if ref.get("source_block_id")
            )
            if source_supports_playable_location(
                str(location.get("title") or ""),
                source_ids,
                source_texts=evidence_texts,
                source_titles=evidence_titles,
                source_section_paths=evidence_sections,
                source_scene_keys=evidence_scene_keys,
                source_semantic_kinds=evidence_semantic_kinds,
            ):
                continue
            tentative = _remove_contract_record_with_dependents(
                payload, "locations", location_id
            )
            if tentative is payload:
                # Never silently replace an unsupported entry scene. The strict
                # compiler must keep it visible as a release blocker.
                continue
            try:
                ScenarioContract.model_validate(tentative)
            except ValidationError:
                continue
            payload = tentative
            location_role_diagnostics.append(
                "Source-unproven playable location contracted: "
                f"{location_id} ({location.get('title') or ''})"
            )

        payload = _deduplicate_semantically_identical_operators(payload)
        payload = self._normalize_contract_outcome_pair_effects(
            payload, self.effect_catalog
        )
        payload, link_diagnostics = self._materialize_source_explicit_location_links(
            payload, evidence
        )
        payload, fail_forward_diagnostics = self._apply_bounded_fail_forward_pressure(
            payload
        )
        payload, clue_state_diagnostics = self._align_automatic_clue_state(
            payload, evidence
        )
        contract = ScenarioContract.model_validate(payload)
        from ai_kp.platform.resolution.authoring_entity_catalog import validate_authoring_entities

        validate_authoring_entities(contract, {
            item.source_block_id: item.entity_candidates for item in evidence
        })
        return candidate.model_copy(update={
            "contract": contract,
            "evidence_blocks": tuple(item.evidence_block() for item in evidence),
            "assumptions": _bounded_authoring_assumptions(
                tuple(location_role_diagnostics),
                link_diagnostics,
                fail_forward_diagnostics,
                clue_state_diagnostics,
                candidate.assumptions,
            ),
        })

    @staticmethod
    def _align_automatic_clue_state(
        payload: dict[str, Any],
        evidence: tuple[ScenarioAuthoringEvidence, ...],
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        """Align clue disclosure with server-owned outcomes and source evidence."""

        return align_automatic_clue_state(
            payload,
            {item.source_block_id: item.text for item in evidence},
        )

    @staticmethod
    def _materialize_source_explicit_location_links(
        payload: dict[str, Any],
        evidence: tuple[ScenarioAuthoringEvidence, ...],
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        """Recover only source-explicit adjacency omitted by a weak model."""

        connector = re.compile(
            r"(?:[-—–→↔⇄]|可到|通往|前往|连接|相连|抵达|到达|\b(?:leads?|connects?)\s+to\b)",
            re.IGNORECASE,
        )
        locations = tuple(payload.get("locations", []))
        if len(locations) < 2:
            return payload, ()
        existing: set[tuple[str, str]] = set()
        for link in payload.get("location_links", []):
            start = link["from_location_id"]
            end = link["to_location_id"]
            existing.add((start, end))
            if not link.get("one_way", False):
                existing.add((end, start))
        diagnostics: list[str] = []
        for source in evidence:
            occurrences: list[tuple[int, int, dict[str, Any]]] = []
            for location in locations:
                title = str(location.get("title", "")).strip()
                if len(title) < 2:
                    continue
                for match in re.finditer(re.escape(title), source.text, re.IGNORECASE):
                    occurrences.append((match.start(), match.end(), location))
            occurrences.sort(key=lambda item: (item[0], item[1]))
            for left, right in pairwise(occurrences):
                _, left_end, left_location = left
                right_start, _, right_location = right
                if left_location["location_id"] == right_location["location_id"]:
                    continue
                between = source.text[left_end:right_start]
                if len(between) > 48 or not connector.search(between):
                    continue
                start = left_location["location_id"]
                end = right_location["location_id"]
                if (start, end) in existing:
                    continue
                explicit_one_way = bool(
                    re.search(r"(?:单向|one[- ]way)", between, re.IGNORECASE)
                )
                payload.setdefault("location_links", []).append({
                    "from_location_id": start,
                    "to_location_id": end,
                    "one_way": explicit_one_way,
                    "preconditions": [],
                    "source_refs": [source.source_ref().model_dump(mode="json")],
                })
                existing.add((start, end))
                if not explicit_one_way:
                    existing.add((end, start))
                diagnostics.append(
                    "Server source-explicit location link materialized: "
                    f"{start} {'->' if explicit_one_way else '<->'} {end}"
                )
        return payload, tuple(diagnostics)

    @staticmethod
    def _apply_bounded_fail_forward_pressure(
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        """Complete missing check costs using one existing source-backed clock.

        No clock, fact, or fictional consequence is invented. Ambiguous clock
        choice remains a compiler blocker for bounded repair or human review.
        """

        clocks = payload.get("clocks", [])
        if not clocks:
            return payload, ()
        referenced_paths = tuple(_iter_state_paths({
            "endings": payload.get("endings", []),
            "consequence_signals": payload.get("consequence_signals", []),
        }))
        pressure_clock_ids = tuple(
            str(track.get("clock_id"))
            for track in payload.get("pressure_tracks", [])
            if track.get("clock_id")
        )

        def select_graph_pressure_clock(
            candidates: list[dict[str, Any]],
        ) -> list[dict[str, Any]]:
            if len(candidates) <= 1:
                return candidates
            scored = [
                (
                    sum(
                        path == f"clocks.{clock['clock_id']}"
                        for path in referenced_paths
                    )
                    + pressure_clock_ids.count(str(clock["clock_id"])),
                    clock,
                )
                for clock in candidates
            ]
            best = max(score for score, _ in scored)
            winners = [clock for score, clock in scored if score == best]
            return winners if best > 0 and len(winners) == 1 else candidates

        diagnostics: list[str] = []
        for operator in payload.get("operators", []):
            choices = operator.get("skill_choices", [])
            if not choices:
                continue
            operator_sources = {
                item.get("source_block_id")
                for item in operator.get("source_refs", [])
                if item.get("source_block_id")
            }
            related_clocks = [
                clock
                for clock in clocks
                if operator_sources
                & {
                    item.get("source_block_id")
                    for item in clock.get("source_refs", [])
                    if item.get("source_block_id")
                }
            ]
            candidates = select_graph_pressure_clock(related_clocks or clocks)
            if len(candidates) != 1:
                continue
            clock = candidates[0]
            clock_id = clock["clock_id"]
            title = clock.get("title") or clock_id
            changed = False
            if not operator.get("failure_commands"):
                operator["failure_commands"] = [{
                    "kind": "advance_clock",
                    "clock_id": clock_id,
                    "delta": 1,
                }]
                for choice in choices:
                    if not choice.get("failure_stakes"):
                        choice["failure_stakes"] = f"行动耗时，并推进压力：{title}。"
                changed = True
            branches = operator.setdefault("outcome_branches", [])
            branch_keys = {branch.get("outcome_key") for branch in branches}
            if any(choice.get("allow_push") for choice in choices) and (
                "pushed_failure" not in branch_keys
            ):
                branches.append({
                    "outcome_key": "pushed_failure",
                    "commands": [{
                        "kind": "advance_clock",
                        "clock_id": clock_id,
                        "delta": 2,
                    }],
                })
                for choice in choices:
                    if choice.get("allow_push") and not choice.get(
                        "pushed_failure_stakes"
                    ):
                        choice["pushed_failure_stakes"] = (
                            f"孤注一掷失败会让局势恶化，并额外推进压力：{title}。"
                        )
                changed = True
            if changed:
                diagnostics.append(
                    "Server fail-forward pressure applied: "
                    f"{operator['operator_id']} -> {clock_id}"
                )
        return payload, tuple(diagnostics)

    @staticmethod
    def _normalize_contract_outcome_pair_effects(
        payload: dict[str, Any],
        catalog: ScenarioEffectCatalog | None,
    ) -> dict[str, Any]:
        """Split source shorthand such as ``1/1d6`` at the check branch boundary."""

        if catalog is None:
            return payload
        normalized = deepcopy(payload)
        for operator in normalized["operators"]:
            if operator.get("policy") not in {
                "required_check",
                "optional_check",
                "conditional_check",
                "opposed_check",
            }:
                continue
            always: list[dict[str, Any]] = []
            success: list[dict[str, Any]] = []
            failure: list[dict[str, Any]] = []
            pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for branch, key in (
                (always, "always_commands"),
                (success, "success_commands"),
                (failure, "failure_commands"),
            ):
                for command in operator.get(key, []):
                    split = (
                        catalog.split_outcome_payload(
                            str(command.get("event_type")),
                            command.get("payload", {}),
                        )
                        if command.get("kind") == "apply_ruleset_effect"
                        else None
                    )
                    if split is None:
                        branch.append(command)
                        continue
                    pairs.append(
                        (
                            {**command, "payload": split[0]},
                            {**command, "payload": split[1]},
                        )
                    )
            for success_command, failure_command in pairs:
                if success_command not in success:
                    success.append(success_command)
                if failure_command not in failure:
                    failure.append(failure_command)
            operator["always_commands"] = always
            operator["success_commands"] = success
            operator["failure_commands"] = failure
        return normalized

    @staticmethod
    def ending_operator_candidates(
        contract: ScenarioContract,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        targets: tuple[SourceCoverageSupplementTarget, ...],
    ) -> tuple[EndingOperatorCandidate, ...]:
        """Rank a bounded, executable outcome catalog for one ending source."""

        target_ids = {item.source_block_id for item in targets}
        source_text = "\n".join(
            item.text for item in evidence if item.source_block_id in target_ids
        )
        return build_ending_operator_candidates(contract, source_text)

    @staticmethod
    def ending_state_candidates(
        contract: ScenarioContract,
    ) -> tuple[EndingStateCandidate, ...]:
        """Expose a bounded catalog of already-produced state, never free paths."""

        return build_ending_state_candidates(contract)

    @staticmethod
    def ending_catalogs(
        contract: ScenarioContract,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        targets: tuple[SourceCoverageSupplementTarget, ...],
    ) -> tuple[
        tuple[EndingOperatorCandidate, ...],
        tuple[EndingStateCandidate, ...],
    ]:
        """Build current reachable operator/state catalogs in one exploration."""

        target_ids = {item.source_block_id for item in targets}
        source_text = "\n".join(
            item.text for item in evidence if item.source_block_id in target_ids
        )
        return build_ending_catalogs(contract, source_text)

    @staticmethod
    def terminal_entity_candidates(
        contract: ScenarioContract,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        targets: tuple[SourceCoverageSupplementTarget, ...],
    ) -> tuple[TerminalEntityCandidate, ...]:
        """Expose only existing entities exactly named by the target source."""

        target_ids = {item.source_block_id for item in targets}
        source_text = "\n".join(
            item.text for item in evidence if item.source_block_id in target_ids
        )
        return build_terminal_entity_candidates(contract, source_text)

    async def author(
        self,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        *,
        contract_id: str,
        source_version: int,
        ruleset_id: str,
        title: str,
        corpus_truncated: bool,
    ) -> ScenarioContractAuthoringResult:
        if not evidence:
            raise ValueError("Scenario contract authoring requires source evidence")
        batches: list[ScenarioIrBatch] = []
        errors: list[str] = []
        repair_diagnostics: list[ScenarioIrRepairDiagnostic] = []
        attempts = 0
        partitions = self.partitions(evidence)
        for partition_index, partition in enumerate(partitions):
            partition_result = await self.author_partition(
                partition,
                ruleset_id=ruleset_id,
                partition_index=partition_index,
            )
            attempts += partition_result.attempt_count
            errors.extend(partition_result.validation_errors)
            repair_diagnostics.extend(partition_result.repair_diagnostics)
            if partition_result.batch is None:
                return ScenarioContractAuthoringResult(
                    candidate=None,
                    attempt_count=attempts,
                    partition_count=len(partitions),
                    completed_partition_count=len(batches),
                    validation_errors=tuple(errors),
                    repair_diagnostics=tuple(repair_diagnostics),
                )
            batches.append(partition_result.batch)

        assembled = self.assemble(
            evidence,
            tuple(batches),
            contract_id=contract_id,
            source_version=source_version,
            ruleset_id=ruleset_id,
            title=title,
            corpus_truncated=corpus_truncated,
            attempt_count=attempts,
            validation_errors=tuple(errors),
            repair_diagnostics=tuple(repair_diagnostics),
        )
        errors = list(assembled.validation_errors)
        # A one-partition module can safely repair the same bounded evidence.
        # Multi-partition assembly failures require cross-partition diagnosis
        # and are left for review instead of repeatedly spending model calls.
        while assembled.candidate is None and len(partitions) == 1 and attempts < 3:
            repaired = await self.author_partition(
                partitions[0],
                ruleset_id=ruleset_id,
                partition_index=0,
            )
            attempts += repaired.attempt_count
            errors.extend(repaired.validation_errors)
            repair_diagnostics.extend(repaired.repair_diagnostics)
            if repaired.batch is None:
                break
            batches[0] = repaired.batch
            assembled = self.assemble(
                evidence,
                tuple(batches),
                contract_id=contract_id,
                source_version=source_version,
                ruleset_id=ruleset_id,
                title=title,
                corpus_truncated=corpus_truncated,
                attempt_count=attempts,
                validation_errors=tuple(errors),
                repair_diagnostics=tuple(repair_diagnostics),
            )
            errors = list(assembled.validation_errors)
        return assembled

    async def author_partition(
        self,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        *,
        ruleset_id: str,
        partition_index: int,
        max_attempts: int = 3,
        repair_observer: Callable[
            [ScenarioIrRepairPlan | ScenarioIrCitationRepairPlan, int], None
        ]
        | None = None,
        coverage_targets: tuple[SourceCoverageSupplementTarget, ...] = (),
        record_id_prefix: str = "",
        ending_candidates: tuple[EndingOperatorCandidate, ...] = (),
        ending_state_candidates: tuple[EndingStateCandidate, ...] = (),
        terminal_entity_candidates: tuple[TerminalEntityCandidate, ...] = (),
    ) -> ScenarioPartitionAuthoringResult:
        """Generate one bounded IR batch without knowing any other partition."""

        if not evidence:
            raise ValueError("Scenario IR partition requires source evidence")
        if not 1 <= max_attempts <= 3:
            raise ValueError("Scenario IR partition attempts must be between 1 and 3")
        if supports_clue_materialization(coverage_targets):
            return await self._author_clue_supplement(
                evidence,
                ruleset_id=ruleset_id,
                partition_index=partition_index,
                max_attempts=max_attempts,
                coverage_targets=coverage_targets,
                record_id_prefix=record_id_prefix,
            )
        if supports_action_materialization(coverage_targets):
            return await self._author_action_supplement(
                evidence,
                ruleset_id=ruleset_id,
                partition_index=partition_index,
                max_attempts=max_attempts,
                coverage_targets=coverage_targets,
                record_id_prefix=record_id_prefix,
            )
        if supports_terminal_method_materialization(coverage_targets):
            return await self._author_terminal_method_supplement(
                evidence,
                ruleset_id=ruleset_id,
                partition_index=partition_index,
                max_attempts=max_attempts,
                coverage_targets=coverage_targets,
                record_id_prefix=record_id_prefix,
                entity_candidates=terminal_entity_candidates,
            )
        if supports_terminal_observation_materialization(coverage_targets):
            return self._author_terminal_observation_supplement(
                evidence,
                partition_index=partition_index,
                coverage_targets=coverage_targets,
                record_id_prefix=record_id_prefix,
            )
        if supports_ending_materialization(coverage_targets):
            return await self._author_ending_supplement(
                evidence,
                ruleset_id=ruleset_id,
                partition_index=partition_index,
                max_attempts=max_attempts,
                coverage_targets=coverage_targets,
                record_id_prefix=record_id_prefix,
                ending_candidates=ending_candidates,
                ending_state_candidates=ending_state_candidates,
            )
        errors: list[str] = []
        diagnostics: list[ScenarioIrRepairDiagnostic] = []
        current_payload: dict[str, Any] | None = None
        repair_plan: ScenarioIrRepairPlan | ScenarioIrCitationRepairPlan | None = None
        allowed_source_ids = {item.source_block_id for item in evidence}
        for attempt in range(1, max_attempts + 1):
            repairing = current_payload is not None and repair_plan is not None
            if repairing and repair_observer is not None and repair_plan is not None:
                repair_observer(repair_plan, attempt)
            raw = await self.llm.complete(
                self._repair_messages(
                    evidence,
                    ruleset_id=ruleset_id,
                    partition_index=partition_index,
                    plan=repair_plan,
                    errors=errors,
                )
                if repairing
                else (
                    self._coverage_supplement_messages(
                        evidence,
                        ruleset_id=ruleset_id,
                        partition_index=partition_index,
                        targets=coverage_targets,
                        errors=errors,
                        effect_catalog=self.effect_catalog,
                        record_id_prefix=record_id_prefix,
                    )
                    if coverage_targets
                    else self._messages(
                        evidence,
                        ruleset_id=ruleset_id,
                        partition_index=partition_index,
                        errors=errors,
                        effect_catalog=self.effect_catalog,
                    )
                ),
                temperature=0.2,
            )
            try:
                decoded = decode_json_object(raw)
            except StructuredJsonError as exc:
                errors.append(f"partition {partition_index}: {str(exc)[:1200]}")
                if repairing and repair_plan is not None:
                    diagnostics.extend(
                        self._repair_diagnostics(
                            repair_plan,
                            partition_index=partition_index,
                            model_attempt=attempt,
                            status="failed",
                        )
                    )
                continue
            if repairing and repair_plan is not None:
                try:
                    if isinstance(repair_plan, ScenarioIrCitationRepairPlan):
                        citation_envelope = decode_source_citation_repair(
                            decoded, current_payload, repair_plan
                        )
                        current_payload = apply_source_citation_envelope(
                            current_payload, repair_plan, citation_envelope
                        )
                    else:
                        envelope = ScenarioIrRepairEnvelope.model_validate(decoded)
                        current_payload = apply_repair_envelope(
                            current_payload,
                            repair_plan,
                            envelope,
                        )
                except (ValidationError, ValueError) as exc:
                    errors.append(
                        f"partition {partition_index} repair: {str(exc)[:1200]}"
                    )
                    diagnostics.extend(
                        self._repair_diagnostics(
                            repair_plan,
                            partition_index=partition_index,
                            model_attempt=attempt,
                            status="failed",
                        )
                    )
                    continue
                diagnostics.extend(
                    self._repair_diagnostics(
                        repair_plan,
                        partition_index=partition_index,
                        model_attempt=attempt,
                        status="applied",
                    )
                )
            elif isinstance(decoded, dict):
                current_payload = decoded
            else:
                current_payload = None
                repair_plan = None
                errors.append(
                    f"partition {partition_index}: root value must be a JSON object"
                )
                continue

            current_payload = self._normalize_ir_transport(current_payload, evidence)
            citation_plan = plan_source_citation_repairs(
                current_payload,
                allowed_source_ids=allowed_source_ids,
            )
            if citation_plan is not None:
                repair_plan = citation_plan
                errors.append(
                    f"partition {partition_index}: record provenance requires "
                    "citation-only bounded repair"
                )
                continue
            try:
                batch = ScenarioIrBatch.model_validate(current_payload)
            except ValidationError as exc:
                errors.append(f"partition {partition_index}: {str(exc)[:1200]}")
                contracted_payload, removed_children = (
                    contract_invalid_reactive_children(current_payload, exc)
                )
                if removed_children:
                    current_payload = contracted_payload
                    try:
                        batch = ScenarioIrBatch.model_validate(current_payload)
                    except ValidationError as contracted_error:
                        repair_plan = plan_validation_repairs(
                            current_payload, contracted_error
                        )
                        if repair_plan is None:
                            current_payload = None
                        continue
                else:
                    repair_plan = plan_validation_repairs(current_payload, exc)
                    if repair_plan is None:
                        current_payload = None
                    continue
            batch = self._normalize_outcome_pair_effects(batch, self.effect_catalog)
            batch = contract_invalid_ruleset_effects(
                batch,
                self.effect_catalog,
                source_texts={item.source_block_id: item.text for item in evidence},
            )
            batch = ensure_unspecified_check_goal_boundaries(batch)

            current_payload, discarded_unknown_sources = discard_unknown_source_records(
                current_payload,
                allowed_source_ids=allowed_source_ids,
            )
            if discarded_unknown_sources:
                errors.append(
                    f"partition {partition_index}: discarded "
                    f"{discarded_unknown_sources} records with no authorized "
                    "citation-only repair path"
                )
                batch = ScenarioIrBatch.model_validate(current_payload)
                batch = self._normalize_outcome_pair_effects(batch, self.effect_catalog)
                batch = contract_invalid_ruleset_effects(
                    batch,
                    self.effect_catalog,
                    source_texts={item.source_block_id: item.text for item in evidence},
                )
                batch = ensure_unspecified_check_goal_boundaries(batch)
            self._validate_batch_source_ids(batch, evidence)
            gate_errors = self._batch_gate_errors(
                batch,
                effect_catalog=self.effect_catalog,
                coverage_targets=coverage_targets,
                record_id_prefix=record_id_prefix,
            )
            if gate_errors:
                errors.append(f"partition {partition_index}: " + "; ".join(gate_errors))
                current_payload = None
                repair_plan = None
                continue
            return ScenarioPartitionAuthoringResult(
                batch=batch,
                attempt_count=attempt,
                validation_errors=tuple(errors),
                repair_diagnostics=tuple(diagnostics),
            )
        if current_payload is not None and repair_plan is not None:
            try:
                degraded_payload = discard_repair_targets(current_payload, repair_plan)
                degraded_payload, discarded_unknown_sources = (
                    discard_unknown_source_records(
                        degraded_payload,
                        allowed_source_ids=allowed_source_ids,
                    )
                )
                if discarded_unknown_sources:
                    errors.append(
                        f"partition {partition_index}: discarded "
                        f"{discarded_unknown_sources} records with no authorized "
                        "original citation after bounded repair"
                    )
                degraded_batch = ScenarioIrBatch.model_validate(degraded_payload)
                degraded_batch = self._normalize_outcome_pair_effects(
                    degraded_batch, self.effect_catalog
                )
                degraded_batch = contract_invalid_ruleset_effects(
                    degraded_batch,
                    self.effect_catalog,
                    source_texts={item.source_block_id: item.text for item in evidence},
                )
                degraded_batch = ensure_unspecified_check_goal_boundaries(
                    degraded_batch
                )
                if plan_source_citation_repairs(
                    degraded_payload,
                    allowed_source_ids=allowed_source_ids,
                ) is None:
                    self._validate_batch_source_ids(degraded_batch, evidence)
                    gate_errors = self._batch_gate_errors(
                        degraded_batch,
                        effect_catalog=self.effect_catalog,
                        coverage_targets=coverage_targets,
                        record_id_prefix=record_id_prefix,
                    )
                    if gate_errors:
                        errors.append(
                            f"partition {partition_index}: degraded batch failed final "
                            "gates: " + "; ".join(gate_errors)
                        )
                        return ScenarioPartitionAuthoringResult(
                            batch=None,
                            attempt_count=max_attempts,
                            validation_errors=tuple(errors),
                            repair_diagnostics=tuple(diagnostics),
                        )
                    diagnostics.extend(
                        self._repair_diagnostics(
                            repair_plan,
                            partition_index=partition_index,
                            model_attempt=max_attempts,
                            status="discarded",
                        )
                    )
                    errors.append(
                        f"partition {partition_index}: irreparable records were "
                        "discarded after bounded repair"
                    )
                    return ScenarioPartitionAuthoringResult(
                        batch=degraded_batch,
                        attempt_count=max_attempts,
                        validation_errors=tuple(errors),
                        repair_diagnostics=tuple(diagnostics),
                    )
            except (TypeError, ValidationError, ValueError):
                pass
        return ScenarioPartitionAuthoringResult(
            batch=None,
            attempt_count=max_attempts,
            validation_errors=tuple(errors),
            repair_diagnostics=tuple(diagnostics),
        )

    async def _author_action_supplement(
        self,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        *,
        ruleset_id: str,
        partition_index: int,
        max_attempts: int,
        coverage_targets: tuple[SourceCoverageSupplementTarget, ...],
        record_id_prefix: str,
    ) -> ScenarioPartitionAuthoringResult:
        target_source_ids = {
            target.source_block_id for target in coverage_targets
        }
        if len(target_source_ids) != 1:
            raise ValueError("Action supplement targets must share one source block")
        source_block_id = next(iter(target_source_ids))
        source_evidence = next(
            (item for item in evidence if item.source_block_id == source_block_id),
            None,
        )
        if source_evidence is None:
            raise ValueError("Action supplement source is outside its evidence window")
        include_effects = any(
            target.requirement_key == "explicit_ruleset_effect"
            for target in coverage_targets
        )
        requires_checks = any(
            target.requirement_key == "explicit_checks"
            for target in coverage_targets
        )
        if requires_checks and self.check_catalog is None:
            return ScenarioPartitionAuthoringResult(
                batch=None,
                attempt_count=1,
                validation_errors=(
                    f"partition {partition_index}: ruleset check catalog is unavailable",
                ),
            )
        if include_effects and self.effect_catalog is None:
            return ScenarioPartitionAuthoringResult(
                batch=None,
                attempt_count=1,
                validation_errors=(
                    f"partition {partition_index}: ruleset effect catalog is unavailable",
                ),
            )
        source_checks = (
            self.check_catalog.extract_source_checks(source_evidence.text)
            if self.check_catalog is not None
            else ()
        )
        if requires_checks and not source_checks:
            return ScenarioPartitionAuthoringResult(
                batch=None,
                attempt_count=1,
                validation_errors=(
                    (
                        f"partition {partition_index}: target source has no "
                        "ruleset-resolvable check terms"
                    ),
                ),
            )
        source_effects = (
            self.effect_catalog.extract_source_effects(source_evidence.text)
            if include_effects and self.effect_catalog is not None
            else ()
        )
        if include_effects and not source_effects:
            return ScenarioPartitionAuthoringResult(
                batch=None,
                attempt_count=1,
                validation_errors=(
                    (
                        f"partition {partition_index}: target source has no "
                        "ruleset-resolvable effect payload"
                    ),
                ),
            )
        required_action_count = max(
            target.required_additional_count for target in coverage_targets
        )
        if required_action_count == 1:
            source_title = source_evidence.text.strip()[:240]
            if not source_checks or any(
                check_term_occurs_in_text(item.term, source_title)
                for item in source_checks
            ):
                titles = (source_title,)
            else:
                mechanic_terms = tuple(dict.fromkeys((
                    *(item.term for item in source_checks),
                    *(
                        str(value)
                        for effect in source_effects
                        for value in effect.payload.values()
                    ),
                )))
                titles = ((" · ".join(mechanic_terms))[:240],)
        elif len(source_checks) >= required_action_count:
            title_list = [
                f"{item.term} 检定" for item in source_checks[:required_action_count]
            ]
            if source_effects and self.check_catalog is not None:
                effect_suffix = " ".join(
                    str(value)
                    for effect in source_effects
                    for value in effect.payload.values()
                )
                effect_index = next(
                    (
                        index
                        for index, item in enumerate(
                            source_checks[:required_action_count]
                        )
                        if "san" in self.check_catalog.resolve(item.term)
                    ),
                    0,
                )
                title_list[effect_index] = (
                    f"{title_list[effect_index]} {effect_suffix}"
                )[:240]
            titles = tuple(title_list)
        else:
            return ScenarioPartitionAuthoringResult(
                batch=None,
                attempt_count=1,
                validation_errors=(
                    (
                        f"partition {partition_index}: source exposes fewer distinct "
                        "checks than the required action count"
                    ),
                ),
            )
        try:
            envelope = CoverageActionEnvelope(
                confidence="high",
                actions=tuple(CoverageActionProposal(title=title) for title in titles),
            )
            batch = materialize_action_envelope(
                envelope,
                source_block_id=source_block_id,
                record_id_prefix=record_id_prefix,
                source_checks=source_checks,
                source_effects=source_effects,
                source_text=source_evidence.text,
            )
            batch = self._normalize_outcome_pair_effects(batch, self.effect_catalog)
            batch = ensure_unspecified_check_goal_boundaries(batch)
            self._validate_batch_source_ids(batch, evidence)
            gate_errors = self._batch_gate_errors(
                batch,
                effect_catalog=self.effect_catalog,
                coverage_targets=coverage_targets,
                record_id_prefix=record_id_prefix,
            )
            if gate_errors:
                raise ValueError("; ".join(gate_errors))
        except (ValidationError, ValueError) as exc:
            return ScenarioPartitionAuthoringResult(
                batch=None,
                attempt_count=1,
                validation_errors=(
                    f"partition {partition_index}: {str(exc)[:1200]}",
                ),
            )
        return ScenarioPartitionAuthoringResult(batch=batch, attempt_count=1)

    async def _author_clue_supplement(
        self,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        *,
        ruleset_id: str,
        partition_index: int,
        max_attempts: int,
        coverage_targets: tuple[SourceCoverageSupplementTarget, ...],
        record_id_prefix: str,
    ) -> ScenarioPartitionAuthoringResult:
        target_ids = {item.source_block_id for item in coverage_targets}
        if len(target_ids) != 1:
            raise ValueError("Clue supplement targets must share one source block")
        source_block_id = next(iter(target_ids))
        errors: list[str] = []
        for attempt in range(1, max_attempts + 1):
            raw = await self.llm.complete(
                self._clue_supplement_messages(
                    evidence,
                    ruleset_id=ruleset_id,
                    partition_index=partition_index,
                    targets=coverage_targets,
                    errors=errors,
                ),
                temperature=0.1,
            )
            try:
                envelope = CoverageClueEnvelope.model_validate_json(raw)
                batch = materialize_clue_envelope(
                    envelope,
                    source_block_id=source_block_id,
                    record_id_prefix=record_id_prefix,
                )
                self._validate_batch_source_ids(batch, evidence)
                gate_errors = self._batch_gate_errors(
                    batch,
                    effect_catalog=self.effect_catalog,
                    coverage_targets=coverage_targets,
                    record_id_prefix=record_id_prefix,
                )
                if gate_errors:
                    raise ValueError("; ".join(gate_errors))
                return ScenarioPartitionAuthoringResult(
                    batch=batch,
                    attempt_count=attempt,
                    validation_errors=tuple(errors),
                )
            except (ValidationError, ValueError) as exc:
                errors.append(f"partition {partition_index}: {str(exc)[:1200]}")
        return ScenarioPartitionAuthoringResult(
            batch=None,
            attempt_count=max_attempts,
            validation_errors=tuple(errors),
        )

    async def _author_terminal_method_supplement(
        self,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        *,
        ruleset_id: str,
        partition_index: int,
        max_attempts: int,
        coverage_targets: tuple[SourceCoverageSupplementTarget, ...],
        record_id_prefix: str,
        entity_candidates: tuple[TerminalEntityCandidate, ...],
    ) -> ScenarioPartitionAuthoringResult:
        outcome = await TerminalMethodSupplementAuthor(self.llm).author(
            evidence,
            ruleset_id=ruleset_id,
            partition_index=partition_index,
            max_attempts=max_attempts,
            coverage_targets=coverage_targets,
            record_id_prefix=record_id_prefix,
            entity_candidates=entity_candidates,
            validate_batch_source_ids=lambda batch: self._validate_batch_source_ids(
                batch, evidence
            ),
            batch_gate_errors=lambda batch: self._batch_gate_errors(
                batch,
                effect_catalog=self.effect_catalog,
                coverage_targets=coverage_targets,
                record_id_prefix=record_id_prefix,
            ),
        )
        return ScenarioPartitionAuthoringResult(
            batch=outcome.batch,
            attempt_count=outcome.attempt_count,
            validation_errors=outcome.validation_errors,
        )

    def _author_terminal_observation_supplement(
        self,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        *,
        partition_index: int,
        coverage_targets: tuple[SourceCoverageSupplementTarget, ...],
        record_id_prefix: str,
    ) -> ScenarioPartitionAuthoringResult:
        target_ids = {item.source_block_id for item in coverage_targets}
        if len(target_ids) != 1:
            return ScenarioPartitionAuthoringResult(
                batch=None,
                attempt_count=1,
                validation_errors=(
                    (
                        f"partition {partition_index}: terminal observation tool "
                        "requires one target source"
                    ),
                ),
            )
        source_block_id = next(iter(target_ids))
        source = next(
            (item for item in evidence if item.source_block_id == source_block_id),
            None,
        )
        if source is None:
            return ScenarioPartitionAuthoringResult(
                batch=None,
                attempt_count=1,
                validation_errors=(
                    (
                        f"partition {partition_index}: terminal observation source "
                        "is outside its evidence window"
                    ),
                ),
            )
        clauses = extract_terminal_observation_clauses(source.text)
        required_count = max(
            target.required_additional_count for target in coverage_targets
        )
        if len(clauses) < required_count:
            return ScenarioPartitionAuthoringResult(
                batch=None,
                attempt_count=1,
                validation_errors=(
                    (
                        f"partition {partition_index}: terminal observation catalog "
                        "cannot satisfy target"
                    ),
                ),
            )
        try:
            batch = materialize_terminal_observation_actions(
                clauses=clauses[:required_count],
                source_block_id=source_block_id,
                record_id_prefix=record_id_prefix,
            )
            self._validate_batch_source_ids(batch, evidence)
            gate_errors = self._batch_gate_errors(
                batch,
                effect_catalog=self.effect_catalog,
                coverage_targets=coverage_targets,
                record_id_prefix=record_id_prefix,
            )
            if gate_errors:
                raise ValueError("; ".join(gate_errors))
        except (ValidationError, ValueError) as exc:
            return ScenarioPartitionAuthoringResult(
                batch=None,
                attempt_count=1,
                validation_errors=(
                    f"partition {partition_index}: {str(exc)[:1200]}",
                ),
            )
        return ScenarioPartitionAuthoringResult(batch=batch, attempt_count=1)

    async def _author_ending_supplement(
        self,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        *,
        ruleset_id: str,
        partition_index: int,
        max_attempts: int,
        coverage_targets: tuple[SourceCoverageSupplementTarget, ...],
        record_id_prefix: str,
        ending_candidates: tuple[EndingOperatorCandidate, ...],
        ending_state_candidates: tuple[EndingStateCandidate, ...],
    ) -> ScenarioPartitionAuthoringResult:
        target_ids = {item.source_block_id for item in coverage_targets}
        if len(target_ids) != 1:
            return ScenarioPartitionAuthoringResult(
                batch=None,
                attempt_count=1,
                validation_errors=(
                    f"partition {partition_index}: ending tool requires one target source",
                ),
            )
        source_block_id = next(iter(target_ids))
        source_evidence = next(
            (item for item in evidence if item.source_block_id == source_block_id),
            None,
        )
        if source_evidence is None:
            return ScenarioPartitionAuthoringResult(
                batch=None,
                attempt_count=1,
                validation_errors=(
                    f"partition {partition_index}: ending source is outside its evidence window",
                ),
            )
        deterministic = deterministic_ending_envelope(
            source_evidence.text,
            candidates=ending_candidates,
            state_candidates=ending_state_candidates,
        )
        if deterministic is not None:
            try:
                batch = materialize_ending_envelope(
                    deterministic,
                    candidates=ending_candidates,
                    state_candidates=ending_state_candidates,
                    source_block_id=source_block_id,
                    record_id_prefix=f"{record_id_prefix}source_",
                )
                batch = batch.model_copy(update={
                    "assumptions": tuple(
                        f"{SERVER_COVERAGE_ENDING_ASSUMPTION_PREFIX}{item.id}"
                        for item in batch.endings
                    ),
                })
                gate_errors = self._batch_gate_errors(
                    batch,
                    effect_catalog=self.effect_catalog,
                    coverage_targets=coverage_targets,
                    record_id_prefix=record_id_prefix,
                )
                if gate_errors:
                    raise ValueError("; ".join(gate_errors))
                return ScenarioPartitionAuthoringResult(batch=batch, attempt_count=1)
            except (ValidationError, ValueError):
                # Fall through to the bounded model tool when exact candidate
                # linkage cannot form a valid strict batch.
                pass
        errors: list[str] = []
        for attempt in range(1, max_attempts + 1):
            raw = await self.llm.complete(
                self._ending_supplement_messages(
                    evidence,
                    ruleset_id=ruleset_id,
                    partition_index=partition_index,
                    targets=coverage_targets,
                    candidates=ending_candidates,
                    state_candidates=ending_state_candidates,
                    errors=errors,
                ),
                temperature=0.1,
            )
            try:
                envelope = CoverageEndingEnvelope.model_validate(
                    narrow_ending_envelope_payload(decode_json_object(raw))
                )
                batch = materialize_ending_envelope(
                    envelope,
                    candidates=ending_candidates,
                    state_candidates=ending_state_candidates,
                    source_block_id=source_block_id,
                    record_id_prefix=record_id_prefix,
                )
                gate_errors = self._batch_gate_errors(
                    batch,
                    effect_catalog=self.effect_catalog,
                    coverage_targets=coverage_targets,
                    record_id_prefix=record_id_prefix,
                )
                if gate_errors:
                    raise ValueError("; ".join(gate_errors))
                return ScenarioPartitionAuthoringResult(
                    batch=batch,
                    attempt_count=attempt,
                    validation_errors=tuple(errors),
                )
            except (ValidationError, ValueError) as exc:
                errors.append(f"partition {partition_index}: {str(exc)[:1200]}")
        return ScenarioPartitionAuthoringResult(
            batch=None,
            attempt_count=max_attempts,
            validation_errors=tuple(errors),
        )

    def assemble(
        self,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        batches: tuple[ScenarioIrBatch, ...],
        *,
        contract_id: str,
        source_version: int,
        ruleset_id: str,
        title: str,
        corpus_truncated: bool,
        attempt_count: int,
        validation_errors: tuple[str, ...] = (),
        repair_diagnostics: tuple[ScenarioIrRepairDiagnostic, ...] = (),
        supplement_batches: tuple[ScenarioIrBatch, ...] = (),
    ) -> ScenarioContractAuthoringResult:
        """Deterministically assemble persisted IR batches into one candidate."""

        errors = list(validation_errors)
        partition_count = len(self.partitions(evidence))
        if len(batches) != partition_count:
            errors.append(
                f"assembly: expected {partition_count} batches, received {len(batches)}"
            )
            return ScenarioContractAuthoringResult(
                candidate=None,
                attempt_count=max(attempt_count, 1),
                partition_count=partition_count,
                completed_partition_count=len(batches),
                validation_errors=tuple(errors),
                repair_diagnostics=repair_diagnostics,
            )
        assembler = ScenarioIrAssembler()
        assembly = None
        while assembly is None:
            try:
                assembly = assembler.assemble(
                    (*batches, *supplement_batches),
                    source_refs={
                        item.source_block_id: item.source_ref() for item in evidence
                    },
                    contract_id=contract_id,
                    source_version=source_version,
                    ruleset_id=ruleset_id,
                    title=title,
                    corpus_truncated=corpus_truncated,
                    check_catalog=self.check_catalog,
                    effect_catalog=self.effect_catalog,
                    source_texts={
                        item.source_block_id: item.text for item in evidence
                    },
                    source_titles={
                        item.source_block_id: item.title for item in evidence
                    },
                    source_section_paths={
                        item.source_block_id: item.section_path for item in evidence
                    },
                    source_scene_keys={
                        item.source_block_id: item.scene_key
                        for item in evidence
                        if item.scene_key
                    },
                    source_semantic_kinds={
                        item.source_block_id: item.semantic_kind for item in evidence
                    },
                    infer_action_location_ids=tuple(
                        action.id
                        for batch in supplement_batches
                        for action in batch.actions
                    ),
                )
            except (ValidationError, ValueError) as exc:
                message = f"assembly: {str(exc)[:2000]}"
                errors.append(message)
                return ScenarioContractAuthoringResult(
                    candidate=None,
                    attempt_count=max(attempt_count, 1),
                    partition_count=partition_count,
                    completed_partition_count=len(batches),
                    validation_errors=tuple(errors),
                    repair_diagnostics=repair_diagnostics,
                )

        try:
            assumptions = tuple(dict.fromkeys(assembly.assumptions))
            candidate = EvidenceBoundContractCandidate(
                contract=assembly.contract,
                confidence=assembly.confidence,
                assumptions=assumptions,
                evidence_blocks=tuple(item.evidence_block() for item in evidence),
            )
            self._validate_source_refs(candidate.contract, evidence)
        except (ValidationError, ValueError) as exc:
            errors.append(f"candidate: {str(exc)[:2000]}")
            return ScenarioContractAuthoringResult(
                candidate=None,
                attempt_count=max(attempt_count, 1),
                partition_count=partition_count,
                completed_partition_count=len(batches),
                validation_errors=tuple(errors),
                repair_diagnostics=repair_diagnostics,
            )
        return ScenarioContractAuthoringResult(
            candidate=candidate,
            attempt_count=max(attempt_count, 1),
            partition_count=partition_count,
            completed_partition_count=len(batches),
            validation_errors=tuple(errors),
            check_mappings=assembly.check_mappings,
            normalizations=assembly.normalizations,
            repair_diagnostics=repair_diagnostics,
        )

    def merge_supplement_batches(
        self,
        candidate: EvidenceBoundContractCandidate,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        batches: tuple[ScenarioIrBatch, ...],
    ) -> EvidenceBoundContractCandidate:
        """Compile one later coverage cycle and transactionally extend a candidate.

        Review repair works on the strict contract, so rebuilding from the original
        IR would resurrect records the reviewer removed. Later supplements are
        instead assembled in isolation and appended only after the combined strict
        contract validates.
        """

        if not batches:
            return candidate
        base = candidate.contract
        assembly = ScenarioIrAssembler().assemble(
            batches,
            source_refs={item.source_block_id: item.source_ref() for item in evidence},
            contract_id=base.contract_id,
            source_version=base.source_version,
            ruleset_id=base.ruleset_id,
            title=base.title,
            corpus_truncated=False,
            check_catalog=self.check_catalog,
            effect_catalog=self.effect_catalog,
            source_texts={item.source_block_id: item.text for item in evidence},
            source_titles={item.source_block_id: item.title for item in evidence},
            source_section_paths={
                item.source_block_id: item.section_path for item in evidence
            },
            source_scene_keys={
                item.source_block_id: item.scene_key
                for item in evidence
                if item.scene_key
            },
            source_semantic_kinds={
                item.source_block_id: item.semantic_kind for item in evidence
            },
            external_action_locations={
                location.location_id: location.title for location in base.locations
            },
            external_initial_scene_id=base.initial_scene_id,
            external_locations=base.locations,
            external_entities=base.entities,
            infer_action_location_ids=tuple(
                action.id for batch in batches for action in batch.actions
            ),
            external_operator_ids=tuple(item.operator_id for item in base.operators),
            external_produced_paths=tuple(
                ScenarioContractCompiler.produced_paths(base)
            ),
            allow_empty_actions=True,
        )
        addition = assembly.contract
        collection_id_fields: dict[str, tuple[str, ...]] = {
            "locations": ("location_id",),
            "location_links": ("from_location_id", "to_location_id", "one_way"),
            "entities": ("entity_id",),
            "clocks": ("clock_id",),
            "resources": ("resource_id",),
            "clues": ("clue_id",),
            "operators": ("operator_id",),
            "task_methods": ("method_id",),
            "reactive_policies": ("policy_id",),
            "response_obligations": ("obligation_id",),
            "trigger_rules": ("trigger_id",),
            "pressure_tracks": ("pressure_id",),
            "consequence_signals": ("signal_id",),
            "endings": ("ending_id",),
        }

        def merge_identity_collection(name: str) -> tuple[object, ...]:
            records = [*getattr(base, name)]
            indexes = {
                tuple(getattr(item, field) for field in collection_id_fields[name]): index
                for index, item in enumerate(records)
            }
            for addition_record in getattr(addition, name):
                identity = tuple(
                    getattr(addition_record, field)
                    for field in collection_id_fields[name]
                )
                previous_index = indexes.get(identity)
                if previous_index is None:
                    indexes[identity] = len(records)
                    records.append(addition_record)
                    continue
                previous = records[previous_index]
                previous_body = previous.model_dump(
                    mode="json", exclude={"source_refs"}
                )
                addition_body = addition_record.model_dump(
                    mode="json", exclude={"source_refs"}
                )
                if previous_body != addition_body:
                    rendered_identity = "/".join(str(item) for item in identity)
                    raise ValueError(
                        "Cross-batch supplement identity conflict: "
                        f"{name}/{rendered_identity}"
                    )
                if hasattr(previous, "source_refs"):
                    refs = tuple(
                        dict.fromkeys(
                            (*previous.source_refs, *addition_record.source_refs)
                        )
                    )[:16]
                    records[previous_index] = previous.model_copy(
                        update={"source_refs": refs}
                    )
            return tuple(records)

        payload = base.model_dump(mode="python")
        for name in collection_id_fields:
            payload[name] = merge_identity_collection(name)
        payload["endings"] = merge_equivalent_endings(
            (),
            payload["endings"],
        )
        contract = ScenarioContract.model_validate(payload)
        assumptions = tuple(
            dict.fromkeys((*candidate.assumptions, *assembly.assumptions))
        )[:64]
        return candidate.model_copy(
            update={"contract": contract, "assumptions": assumptions}
        )

    async def review(
        self,
        candidate: EvidenceBoundContractCandidate,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
    ) -> ScenarioContractReview:
        """Run a separate, fail-closed entailment review for full-AI publication."""

        partitions = self.partitions(evidence)
        supported_assumptions = {
            index
            for index, assumption in enumerate(candidate.assumptions)
            if _is_server_review_diagnostic(assumption)
        }
        reviewable_assumptions = {
            index: assumption
            for index, assumption in enumerate(candidate.assumptions)
            if index not in supported_assumptions
        }
        findings: list[str] = []
        issues: list[ScenarioContractReviewIssue] = []
        for batch_index, partition in enumerate(partitions):
            messages = self._review_messages(
                candidate,
                partition,
                all_evidence=evidence,
                batch_index=batch_index,
                batch_count=len(partitions),
                reviewable_assumptions=reviewable_assumptions,
            )
            batch: _ScenarioContractReviewBatch | None = None
            batch_findings: tuple[str, ...] = ()
            last_error = ""
            for review_attempt in range(3):
                try:
                    attempt_messages = messages
                    if review_attempt:
                        attempt_messages = [
                            *messages,
                            ChatMessage(
                                role="user",
                                content=(
                                    "上次审核响应未通过协议校验："
                                    + last_error[:400]
                                    + "。重新只返回完整 JSON；findings 每项必须是具体的契约"
                                    "语义问题，reject 的 issues 必须指向输入中真实存在的 record_id。"
                                ),
                            ),
                        ]
                    raw = await self.llm.complete(attempt_messages, temperature=0.0)
                    parsed = _ScenarioContractReviewBatch.model_validate(
                        _narrow_review_batch_payload(decode_json_object(raw))
                    )
                    clean_findings = tuple(
                        item
                        for item in parsed.findings
                        if not _is_review_transport_leak(item)
                        and not _is_non_problem_review_statement(item)
                    )
                    clean_issues = tuple(
                        issue
                        for issue in parsed.issues
                        if not _is_non_problem_review_statement(issue.problem)
                    )
                    if parsed.findings and all(
                        _is_review_transport_leak(item)
                        for item in parsed.findings
                    ):
                        last_error = "findings contained only JSON transport fields"
                        continue
                    if (
                        parsed.decision == "reject"
                        and (parsed.findings or parsed.issues)
                        and not clean_findings
                        and not clean_issues
                    ):
                        parsed = parsed.model_copy(update={
                            "decision": "approve",
                            "findings": (),
                            "issues": (),
                        })
                    else:
                        parsed = parsed.model_copy(update={
                            "findings": clean_findings,
                            "issues": clean_issues,
                        })
                    if (
                        parsed.decision == "reject"
                        and not clean_findings
                        and not parsed.issues
                    ):
                        last_error = "reject decision did not identify a reviewable problem"
                        continue
                    if parsed.decision == "reject" and not any(
                        _normalize_review_issue(issue, candidate) is not None
                        for issue in parsed.issues
                    ) and not _issues_explicitly_named_in_findings(
                        clean_findings, candidate
                    ):
                        last_error = (
                            "reject decision did not anchor a currently existing "
                            "contract record"
                        )
                        continue
                    batch = parsed
                    batch_findings = parsed.findings
                    break
                except (
                    json.JSONDecodeError,
                    ValidationError,
                    ValueError,
                ) as exc:
                    last_error = str(exc)[:400]
            if batch is None:
                findings.append(
                    f"Review batch {batch_index + 1}/{len(partitions)} was invalid: "
                    f"{last_error}"
                )
                continue
            supported_assumptions.update(
                set(batch.supported_assumption_indices)
                & reviewable_assumptions.keys()
            )
            normalized_candidates = tuple(
                normalized
                for issue in batch.issues
                if (normalized := _normalize_review_issue(issue, candidate)) is not None
            )
            named_issues = _issues_explicitly_named_in_findings(
                batch_findings, candidate
            )
            # Weak reviewers often put an otherwise well-formed, record-addressed
            # issue only in ``findings``. Normalize those recovered anchors before
            # applying server authority; doing it afterwards would let the same
            # disproved claim bypass the authority check through the free-text path.
            all_issues = tuple(
                {
                    (issue.group, issue.record_id, issue.problem): issue
                    for issue in (*normalized_candidates, *named_issues)
                }.values()
            )
            contradicted_problems = {
                issue.problem
                for issue in all_issues
                if _review_issue_contradicts_authoritative_record(
                    issue, candidate, self.check_catalog
                )
            }
            batch_findings = tuple(
                finding
                for finding in batch_findings
                if finding not in contradicted_problems
            )
            normalized_issues = tuple(
                issue
                for issue in all_issues
                if issue.problem not in contradicted_problems
            )
            if batch.decision == "reject":
                if normalized_issues:
                    # The structured, record-addressed problem is the blocking
                    # authority. Free-text findings are display prose and may
                    # not preserve a contradicted or unrelated claim under
                    # different wording.
                    findings.extend(item.problem for item in normalized_issues)
                    issues.extend(normalized_issues)
                elif not contradicted_problems:
                    findings.append(
                        f"Review batch {batch_index + 1} rejected without findings."
                    )
            elif normalized_issues:
                issues.extend(normalized_issues)
                findings.extend(item.problem for item in normalized_issues)
            # An explicit approve decision cannot be overturned by an
            # unaddressed prose note. Only a structured issue, or a finding
            # deterministically recovered to one existing record, is
            # actionable review authority.

        unresolved_indices = sorted(
            reviewable_assumptions.keys() - supported_assumptions
        )
        if unresolved_indices:
            findings.insert(
                0,
                "No evidence review batch directly supported authoring assumptions: "
                + ", ".join(str(item) for item in unresolved_indices)
            )
        unique_findings = tuple(dict.fromkeys(findings))[:16]
        unique_issues = tuple(
            {
                (issue.group, issue.record_id, issue.problem): issue
                for issue in issues
            }.values()
        )[:16]
        return ScenarioContractReview(
            decision="reject" if unique_findings else "approve",
            findings=unique_findings,
            issues=unique_issues,
            unsupported_assumption_indices=tuple(unresolved_indices),
            assumptions_resolved=not unresolved_indices and not unique_findings,
        )

    def review_compilation_failure(
        self,
        candidate: EvidenceBoundContractCandidate,
        report: Any,
    ) -> ScenarioContractReview | None:
        """Translate deterministic release blockers into record-addressed repairs.

        This is server authority, not another model opinion. It deliberately
        ignores unresolved authoring assumptions, which belong to the separate
        evidence review, and only targets immutable records named by a compiler
        path or message.
        """

        blocking = tuple(
            issue
            for issue in report.issues
            if issue.code != "unresolved_authoring_assumption"
            and (
                issue.severity == "error"
                or issue.code.startswith("playability_")
                or issue.code == "unreachable_locations"
            )
        )
        if not blocking:
            return None
        records: dict[str, tuple[tuple[str, str], ...]] = {
            group: tuple(
                (str(getattr(record, id_field)), group)
                for record in getattr(candidate.contract, group)
            )
            for group, id_field in _CONTRACT_RECORD_ID_FIELDS.items()
        }
        issues: list[ScenarioContractReviewIssue] = []
        findings: list[str] = []
        for blocker in blocking:
            findings.append(f"{blocker.code}: {blocker.message}")
            targets: set[tuple[str, str]] = set()
            path_match = re.match(r"^([a-z_]+)\.(\d+)(?:\.|$)", blocker.path)
            if path_match and path_match.group(1) in records:
                group = path_match.group(1)
                index = int(path_match.group(2))
                group_records = records[group]
                if index < len(group_records):
                    record_id, _ = group_records[index]
                    targets.add((group, record_id))
            searchable = f"{blocker.path}\n{blocker.message}"
            for group, group_records in records.items():
                for record_id, _ in group_records:
                    if record_id and re.search(
                        rf"(?<![\w]){re.escape(record_id)}(?![\w])", searchable
                    ):
                        targets.add((group, record_id))
            if blocker.code.startswith("playability_ending_reachability"):
                premature_ids = set(re.findall(
                    r"ending\s+([^\s;]+)\s+is already satisfied in the initial state",
                    blocker.message,
                ))
                targets.update(
                    ("endings", record_id)
                    for record_id, _ in records["endings"]
                    if not premature_ids or record_id in premature_ids
                )
            for group, record_id in sorted(targets):
                candidate_issue = ScenarioContractReviewIssue(
                    group=group,
                    record_id=record_id,
                    problem=f"{blocker.code}: {blocker.message}",
                )
                server_owned = (
                    group == "operators"
                    and (
                        f"{SERVER_COVERAGE_ACTION_ASSUMPTION_PREFIX}{record_id}"
                        in candidate.assumptions
                        or re.fullmatch(r"supp\d+_\d+_action_\d+", record_id)
                    )
                ) or (
                    group == "endings"
                    and (
                        f"{SERVER_COVERAGE_ENDING_ASSUMPTION_PREFIX}{record_id}"
                        in candidate.assumptions
                        or re.fullmatch(
                            r"supp\d+_\d+_source_ending_\d+", record_id
                        )
                    )
                )
                if not server_owned:
                    issues.append(candidate_issue)
        unique_issues = tuple({
            (issue.group, issue.record_id, issue.problem): issue
            for issue in issues
        }.values())
        prioritized_issues = tuple(sorted(
            unique_issues,
            key=lambda issue: (
                "is already satisfied in the initial state" not in issue.problem,
                issue.group,
                issue.record_id,
                issue.problem,
            ),
        ))[:16]
        return ScenarioContractReview(
            decision="reject",
            review_kind="deterministic_compiler",
            findings=tuple(dict.fromkeys(findings))[:16],
            issues=prioritized_issues,
            assumptions_resolved=False,
        )

    @staticmethod
    def shrink_unsupported_assumptions(
        candidate: EvidenceBoundContractCandidate,
        review: ScenarioContractReview,
    ) -> EvidenceBoundContractCandidate | None:
        """Drop only server-identified, inert authoring assumptions.

        Contract records are deliberately untouched. Record-addressed findings
        must first go through the bounded repair path; otherwise deleting an
        assumption could hide a semantic defect instead of narrowing a draft.
        """

        if (
            review.decision != "reject"
            or review.issues
            or not review.unsupported_assumption_indices
        ):
            return None
        indices = tuple(dict.fromkeys(review.unsupported_assumption_indices))
        if any(index < 0 or index >= len(candidate.assumptions) for index in indices):
            return None
        rejected = set(indices)
        retained = tuple(
            assumption
            for index, assumption in enumerate(candidate.assumptions)
            if index not in rejected
        )
        if retained == candidate.assumptions:
            return None
        diagnostic = (
            "Unsupported authoring assumptions were removed after independent "
            "review: " + ", ".join(str(index) for index in indices)
        )
        return candidate.model_copy(
            update={"assumptions": (*retained, diagnostic)[:64]}
        )

    async def repair_review_rejection(
        self,
        candidate: EvidenceBoundContractCandidate,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        review: ScenarioContractReview,
    ) -> EvidenceBoundContractCandidate | None:
        """Apply one bounded, record-addressed repair proposed from review issues."""

        if review.decision != "reject":
            return None
        candidate, removed_premature = self.contract_compiler_proven_premature_endings(
            candidate, review
        )
        if removed_premature:
            # Premature endings make the runtime terminal at the initial state.
            # Recompute the graph before accepting any scene-unreachability issue
            # produced by that poisoned exploration.
            fresh_report = ScenarioContractCompiler(
                effect_catalog=self.effect_catalog
            ).compile(candidate.contract).report
            fresh_review = self.review_compilation_failure(candidate, fresh_report)
            if fresh_review is None:
                return candidate
            review = fresh_review
        while True:
            candidate, removed_travel_conflicts = (
                self.contract_compiler_proven_canonical_travel_conflicts(
                    candidate, review
                )
            )
            if not removed_travel_conflicts:
                break
            # Compilation reports the first reserved-identity collision because
            # it cannot safely materialize a partial contract.  Remove that
            # exact, link-proven occupant and compile again: a weak model can
            # independently collide with more than one canonical link.
            fresh_report = ScenarioContractCompiler(
                effect_catalog=self.effect_catalog
            ).compile(candidate.contract).report
            fresh_review = self.review_compilation_failure(candidate, fresh_report)
            if fresh_review is None:
                return candidate
            review = fresh_review
        scene_graph_repair = await self._repair_missing_scene_graph(
            candidate, evidence, review
        )
        if scene_graph_repair is not None:
            return scene_graph_repair
        if not review.issues:
            return None
        candidate, review = self.contract_compiler_proven_unreachable_locations(
            candidate, review
        )
        if not review.issues:
            return candidate
        records_by_target: dict[tuple[str, str], dict[str, Any]] = {}
        for issue in review.issues:
            id_field = _CONTRACT_RECORD_ID_FIELDS[issue.group]
            for record in getattr(candidate.contract, issue.group):
                if getattr(record, id_field) == issue.record_id:
                    records_by_target[(issue.group, issue.record_id)] = (
                        record.model_dump(mode="json")
                    )
                    break
        targets = {(issue.group, issue.record_id) for issue in review.issues}
        if set(records_by_target) != targets:
            return None
        evidence_by_id = {item.source_block_id: item for item in evidence}
        allowed_sources_by_target = {
            target: {
                ref["source_block_id"]
                for ref in records_by_target[target].get("source_refs", [])
            }
            | {
                source_id
                for issue in review.issues
                if (issue.group, issue.record_id) == target
                for source_id in issue.source_block_ids
                if source_id in evidence_by_id
            }
            for target in targets
        }
        payload = candidate.contract.model_dump(mode="json")
        changed = False
        ordered_targets = tuple(records_by_target)
        for offset in range(0, len(ordered_targets), _REVIEW_REPAIR_BATCH_SIZE):
            batch_targets = set(
                ordered_targets[offset : offset + _REVIEW_REPAIR_BATCH_SIZE]
            )
            batch_issues = tuple(
                issue
                for issue in review.issues
                if (issue.group, issue.record_id) in batch_targets
            )
            batch_source_ids = set().union(
                *(allowed_sources_by_target[target] for target in batch_targets)
            )
            repair_authority = (
                "这是确定性编译器修复：来源已经证明记录存在，replacement 必须是非 null "
                "完整记录。若无法修复，原样返回当前记录；服务器会判定为无进展并保留检查点。"
                if review.review_kind == "deterministic_compiler"
                else "若证据无法支持该记录，replacement 可以为 null；不得用常识补写。"
            )
            messages = [
                ChatMessage(
                    role="system",
                    content=(
                        "只返回 JSON。你修复独立审核点名的契约记录，每个目标恰好返回一次。"
                        "replacement 为修正后的完整记录。不得新增记录、"
                        "改 ID、改未点名记录或引用目录外证据。结局 replacement 只能修正标题和"
                        "条件，服务器会保留其优先级与命令。" + repair_authority
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=(
                        "响应格式："
                        '{"repairs":[{"group":"...","record_id":"...",'
                        '"replacement":null|{}}]}\n审核问题：'
                        + json.dumps(
                            [item.model_dump(mode="json") for item in batch_issues],
                            ensure_ascii=False,
                        )
                        + "\n当前目标记录："
                        + json.dumps(
                            [
                                {"group": group, "record_id": record_id, "record": record}
                                for (group, record_id), record in records_by_target.items()
                                if (group, record_id) in batch_targets
                            ],
                            ensure_ascii=False,
                        )
                        + "\n可用证据："
                        + json.dumps(
                            [evidence_by_id[item].public_descriptor() for item in sorted(batch_source_ids)],
                            ensure_ascii=False,
                        )
                    ),
                ),
                ]
            envelope: _ScenarioContractReviewRepairEnvelope | None = None
            error = ""
            for attempt in range(2):
                attempt_messages = messages
                if attempt:
                    attempt_messages = [
                        *messages,
                        ChatMessage(
                            role="user",
                            content=(
                                "上次修复响应未通过服务器校验："
                                + error[:800]
                                + "。重新返回完整 JSON；repairs 必须恰好覆盖每个目标一次，"
                                "不要输出 Markdown、说明文字或省略字段。"
                            ),
                        ),
                    ]
                try:
                    raw = await self.llm.complete(attempt_messages, temperature=0.0)
                    envelope = _ScenarioContractReviewRepairEnvelope.model_validate(
                        decode_json_object(raw)
                    )
                    returned_targets = {
                        (item.group, item.record_id) for item in envelope.repairs
                    }
                    if returned_targets != batch_targets:
                        raise ValueError("repair targets do not match the requested batch")
                    break
                except (ValidationError, ValueError) as exc:
                    envelope = None
                    error = str(exc)
            if envelope is None:
                # A transport/schema failure contains no semantic decision.  Treating
                # it as a batch of null replacements used to turn weak-model output
                # into destructive authority: one malformed response could erase
                # source-grounded scenes, clues, and endings (plus typed dependents).
                # Fail closed and keep the last persisted checkpoint intact.
                return None
            for repair in envelope.repairs:
                id_field = _CONTRACT_RECORD_ID_FIELDS[repair.group]
                index = next(
                    (
                        index
                        for index, record in enumerate(payload[repair.group])
                        if record[id_field] == repair.record_id
                    ),
                    None,
                )
                if index is None:
                    # Removing an earlier operator also contracts directly
                    # dependent clues/endings. A later issue targeting that same
                    # dependent is already satisfied, not a transaction failure.
                    continue
                if (
                    repair.replacement is not None
                    and repair.replacement.get(id_field) != repair.record_id
                ):
                    return None
                replacement_source_ids = {
                    ref.get("source_block_id")
                    for ref in (
                        repair.replacement.get("source_refs", [])
                        if repair.replacement is not None
                        else []
                    )
                }
                allowed_source_ids = allowed_sources_by_target[
                    (repair.group, repair.record_id)
                ]
                if (
                    None in replacement_source_ids
                    or not replacement_source_ids <= allowed_source_ids
                ):
                    return None
                if (
                    repair.replacement is not None
                    and not replacement_source_ids
                    and records_by_target[(repair.group, repair.record_id)].get(
                        "source_refs"
                    )
                ):
                    # Small models sometimes return an otherwise complete
                    # replacement but silently omit provenance. The model is not
                    # allowed to erase server-bound evidence: inherit the target's
                    # original refs, then canonicalize their locators below.
                    repair.replacement["source_refs"] = deepcopy(
                        records_by_target[(repair.group, repair.record_id)][
                            "source_refs"
                        ]
                    )
                safe_replacement = repair.replacement
                if (
                    review.review_kind == "deterministic_compiler"
                    and safe_replacement is None
                ):
                    # Compiler findings prove that a record is currently invalid;
                    # they do not prove that source-grounded world content never
                    # existed.  Deterministic repair may rewrite a named record, but
                    # must not contract it merely because a model chose `null`.
                    continue
                if repair.group == "endings" and safe_replacement is not None:
                    safe_replacement = deepcopy(safe_replacement)
                    original = records_by_target[(repair.group, repair.record_id)]
                    # Conditions are re-proved by both the compiler and the next
                    # independent review. Terminal effects remain immutable.
                    safe_replacement["commands"] = deepcopy(
                        original.get("commands", [])
                    )
                    safe_replacement["priority"] = original.get("priority", 0)
                # Only an explicit, validated null from an independent semantic
                # review may delete a record.  An echoed/invalid replacement is not
                # evidence for deletion and therefore makes no progress.
                candidates = (safe_replacement,)
                for replacement in dict.fromkeys(
                    json.dumps(item, sort_keys=True) if item is not None else "null"
                    for item in candidates
                ):
                    decoded_replacement = (
                        None if replacement == "null" else json.loads(replacement)
                    )
                    if (
                        decoded_replacement is not None
                        and decoded_replacement == payload[repair.group][index]
                    ):
                        # A weak model may echo the record without resolving the
                        # issue. That is not progress; continue to the conservative
                        # null/removal fallback instead of burning a review cycle.
                        continue
                    tentative = deepcopy(payload)
                    if decoded_replacement is None:
                        tentative = _remove_contract_record_with_dependents(
                            tentative, repair.group, repair.record_id
                        )
                    else:
                        tentative[repair.group][index] = decoded_replacement
                    try:
                        ScenarioContract.model_validate(tentative)
                    except ValidationError:
                        continue
                    payload = tentative
                    changed = True
                    break
        if not changed:
            return None
        try:
            contract = ScenarioContract.model_validate(payload)
        except ValidationError:
            return None
        return self.reconcile_candidate_authority(
            candidate.model_copy(update={"contract": contract}), evidence
        )

    async def _repair_missing_scene_graph(
        self,
        candidate: EvidenceBoundContractCandidate,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        review: ScenarioContractReview,
    ) -> EvidenceBoundContractCandidate | None:
        """Ask a context-bounded topology Agent to select sourced existing slots.

        This is attempted only for deterministic compiler findings.  The result
        must compile again and then pass the ordinary independent entailment
        review, which sees every new link with its exact source locator.
        """

        if review.review_kind != "deterministic_compiler":
            return None
        problem = "\n".join((
            *review.findings,
            *(issue.problem for issue in review.issues),
        ))
        topology_codes = (
            "authored_initial_scene_missing",
            "initial_scene_missing",
            "unreachable_locations",
        )
        if not any(code in problem for code in topology_codes):
            return None
        result = await ScenarioTopologyRepairAgent(self.llm).repair(
            candidate.contract,
            tuple(
                TopologyEvidence(
                    source_block_id=item.source_block_id,
                    text=item.text,
                    source_ref=item.source_ref(),
                    descriptor=item.public_descriptor(),
                )
                for item in evidence
            ),
            problem=problem,
            issue_source_ids={
                source_id
                for issue in review.issues
                for source_id in issue.source_block_ids
            },
        )
        if result is None:
            return None
        return candidate.model_copy(update={
            "contract": result.contract,
            "assumptions": _bounded_authoring_assumptions(
                result.assumptions,
                candidate.assumptions,
            ),
        })

    @staticmethod
    def contract_compiler_proven_premature_endings(
        candidate: EvidenceBoundContractCandidate,
        review: ScenarioContractReview,
    ) -> tuple[EvidenceBoundContractCandidate, tuple[str, ...]]:
        """Remove only terminal rules proved true in the authoritative initial state."""

        return _contract_premature_endings(candidate, review)

    @staticmethod
    def contract_compiler_proven_canonical_travel_conflicts(
        candidate: EvidenceBoundContractCandidate,
        review: ScenarioContractReview,
    ) -> tuple[EvidenceBoundContractCandidate, tuple[str, ...]]:
        """Remove only reserved travel IDs proved derivable from existing links."""

        if review.review_kind != "deterministic_compiler":
            return candidate, ()
        problem = "\n".join((
            *review.findings,
            *(issue.problem for issue in review.issues),
        ))
        named_conflicts = set(re.findall(
            r"canonical location travel:\s+(travel-link-[0-9a-f]{64})",
            problem,
        ))
        canonical_ids = {
            operator_id
            for link in candidate.contract.location_links
            for operator_id in canonical_travel_operator_ids(link)
        }
        removable = named_conflicts & canonical_ids
        if not removable:
            return candidate, ()
        operators = tuple(
            item
            for item in candidate.contract.operators
            if item.operator_id not in removable
        )
        removed = tuple(sorted(
            removable
            & {item.operator_id for item in candidate.contract.operators}
        ))
        if not removed:
            return candidate, ()
        contract = candidate.contract.model_copy(update={"operators": operators})
        return candidate.model_copy(update={
            "contract": contract,
            "assumptions": _bounded_authoring_assumptions(
                tuple(
                    f"Server canonical travel conflict contracted: {operator_id}"
                    for operator_id in removed
                ),
                candidate.assumptions,
            ),
        }), removed

    @staticmethod
    def contract_compiler_proven_unreachable_locations(
        candidate: EvidenceBoundContractCandidate,
        review: ScenarioContractReview,
    ) -> tuple[EvidenceBoundContractCandidate, ScenarioContractReview]:
        """Contract only ungrounded locations rejected by the deterministic graph.

        A reachability failure proves that the authored links are incomplete, not
        that a cited scene is absent from the source.  Grounded records stay in the
        checkpoint so a later repair/coverage cycle can reconnect them.
        """

        removable = tuple(
            issue
            for issue in review.issues
            if issue.group == "locations"
            and (
                "playability_scene_reachability" in issue.problem
                or "unreachable_locations" in issue.problem
            )
            and not any(
                location.location_id == issue.record_id and location.source_refs
                for location in candidate.contract.locations
            )
        )
        if not removable:
            return candidate, review
        payload = candidate.contract.model_dump(mode="json")
        removed: list[str] = []
        for issue in removable:
            if not any(
                item["location_id"] == issue.record_id
                for item in payload.get("locations", [])
            ):
                continue
            tentative = _remove_contract_record_with_dependents(
                payload, "locations", issue.record_id
            )
            try:
                ScenarioContract.model_validate(tentative)
            except ValidationError:
                continue
            payload = tentative
            removed.append(issue.record_id)
        if not removed:
            return candidate, review
        removed_set = set(removed)
        contracted = candidate.model_copy(update={
            "contract": ScenarioContract.model_validate(payload),
            "assumptions": _bounded_authoring_assumptions(
                tuple(
                    f"Server unreachable location contracted: {location_id}"
                    for location_id in removed
                ),
                candidate.assumptions,
            ),
        })
        remaining = tuple(
            issue
            for issue in review.issues
            if not (issue.group == "locations" and issue.record_id in removed_set)
        )
        return contracted, review.model_copy(update={"issues": remaining})

    def _review_messages(
        self,
        candidate: EvidenceBoundContractCandidate,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        *,
        all_evidence: tuple[ScenarioAuthoringEvidence, ...],
        batch_index: int,
        batch_count: int,
        reviewable_assumptions: dict[int, str],
    ) -> list[ChatMessage]:
        source_ids = {item.source_block_id for item in evidence}
        records: dict[str, list[dict[str, Any]]] = {}
        cited_source_ids: set[str] = set()
        for group_name in (
            "locations",
            "location_links",
            "entities",
            "clocks",
            "resources",
            "clues",
            "operators",
            "task_methods",
            "reactive_policies",
            "consequence_signals",
            "endings",
        ):
            group = getattr(candidate.contract, group_name)
            selected = [
                record
                for record in group
                if any(ref.source_block_id in source_ids for ref in record.source_refs)
            ]
            records[group_name] = [record.model_dump(mode="json") for record in selected]
            cited_source_ids.update(
                ref.source_block_id for record in selected for ref in record.source_refs
            )
        evidence_by_id = {item.source_block_id: item for item in all_evidence}
        review_evidence = list(evidence)
        review_evidence.extend(
            evidence_by_id[source_id]
            for source_id in sorted(cited_source_ids - source_ids)
            if source_id in evidence_by_id
        )
        referenced_paths = set(_iter_state_paths(records))
        operator_outcome_legend = {
            path: {
                "operator_id": operator.operator_id,
                "operator_title": operator.title,
            }
            for operator in candidate.contract.operators
            if (path := operator_outcome_path(operator.operator_id))
            in referenced_paths
        }
        selected_operator_ids = {
            str(item["operator_id"])
            for item in records["operators"]
            if item.get("operator_id")
        }
        operator_source_rule_legend: dict[str, dict[str, Any]] = {}
        for operator in candidate.contract.operators:
            if operator.operator_id not in selected_operator_ids:
                continue
            source_texts = tuple(
                evidence_by_id[ref.source_block_id].text
                for ref in operator.source_refs
                if ref.source_block_id in evidence_by_id
            )
            allowed_skill_keys: tuple[str, ...] = ()
            if self.check_catalog is not None:
                allowed_skill_keys = tuple(
                    dict.fromkeys(
                        key
                        for source_text in source_texts
                        for key in self.check_catalog.source_present_keys(source_text)
                    )
                )
            source_effects = tuple(
                effect.model_dump(mode="json")
                for source_text in source_texts
                for effect in (
                    self.effect_catalog.extract_source_effects(source_text)
                    if self.effect_catalog is not None
                    else ()
                )
            )
            operator_source_rule_legend[operator.operator_id] = {
                "source_allowed_skill_keys": allowed_skill_keys,
                "source_effects": source_effects,
            }
        global_state = (
            {
                "initial_scene_id": candidate.contract.initial_scene_id,
                "initial_facts": candidate.contract.initial_facts,
            }
            if batch_index == 0
            else {}
        )
        return [
            ChatMessage(
                role="system",
                content=(
                    "你是独立的模组契约分区审核器，只返回 JSON。逐项检查本批契约记录是否被"
                    "本批所引来源文本直接支持；来源 ID 正确但文本不支持效果也必须 reject。"
                    "不得用常识补齐规则、数值、秘密、结局或因果效果。来源覆盖完整性由"
                    "服务器的确定性 coverage compiler 独立检查；本批不要因未物化叙事细节、"
                    "背景资料或没有待审核记录而 reject，也不要要求契约复述整篇来源。只审"
                    "现有记录的语义蕴含、方向和因果是否正确。operator_outcome_path_legend 是"
                    "服务器对 SHA-256 状态路径的权威映射，路径不是明文 operator ID。"
                    "operator_source_rule_legend 是服务器从每条记录自己引用的来源局部提取的"
                    "技能和规则效果；不得用相邻段落为记录增加或删除技能/数值，也不得提出与"
                    "该图例矛盾的检定或效果 finding。policy 为 clarification/impossible 的记录"
                    "不会执行检定或命令，缺少 skill_choices 是预期的保守阻断，不能据此 reject。"
                    "location_links 表示从起点立即向玩家展示目的地名称并允许直接移动；如果来源"
                    "只把目的地作为秘密、未发现入口、有条件路线或没有明确起点，必须 reject，"
                    "不能把 KP 知道地点存在误当成玩家已知可达。"
                    "findings 只列本批记录不受支持或语义错误的问题。authoring_assumptions 使用服务器"
                    "给出的整数索引；只有本批证据直接支持某条假设时才把索引放入 "
                    "supported_assumption_indices，未涉及的假设不要因此 reject。approve 时"
                    " findings 必须为空。输出 "
                    "每个记录级问题还必须写入 issues，group 和 record_id 必须逐字来自"
                    "待审核记录，source_block_ids 只列本问题实际核对的证据 ID；无法锚定到"
                    "单条记录的问题只写 findings。输出 "
                    '{"decision":"approve|reject","findings":["..."],'
                    '"issues":[{"group":"clues","record_id":"...",'
                    '"problem":"...","source_block_ids":["..."]}],'
                    '"supported_assumption_indices":[]}。'
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"审核分区：{batch_index + 1}/{batch_count}\n证据："
                    + json.dumps(
                        [item.public_descriptor() for item in review_evidence],
                        ensure_ascii=False,
                    )
                    + "\n本批待审核契约记录："
                    + json.dumps(records, ensure_ascii=False)
                    + "\noperator_outcome_path_legend："
                    + json.dumps(operator_outcome_legend, ensure_ascii=False)
                    + "\noperator_source_rule_legend："
                    + json.dumps(operator_source_rule_legend, ensure_ascii=False)
                    + "\n仅首批审核的全局初始状态："
                    + json.dumps(global_state, ensure_ascii=False)
                    + "\nauthoring_assumptions："
                    + json.dumps(reviewable_assumptions, ensure_ascii=False)
                    + "\n允许返回的假设索引："
                    + json.dumps(sorted(reviewable_assumptions))
                ),
            ),
        ]

    @staticmethod
    def _validate_batch_source_ids(
        batch: ScenarioIrBatch,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
    ) -> None:
        allowed = {item.source_block_id for item in evidence}
        groups = (
            batch.locations,
            batch.location_links,
            batch.entities,
            batch.clocks,
            batch.resources,
            batch.clues,
            batch.actions,
            batch.task_methods,
            batch.reactive_policies,
            batch.consequence_signals,
            batch.endings,
        )
        unknown = sorted(
            source_id
            for group in groups
            for record in group
            for source_id in record.source_block_ids
            if source_id not in allowed
        )
        if unknown:
            raise ValueError(f"IR batch cites source blocks outside its partition: {unknown}")

    @staticmethod
    def _batch_effect_errors(
        batch: ScenarioIrBatch,
        catalog: ScenarioEffectCatalog | None,
    ) -> tuple[str, ...]:
        commands = [
            command
            for action in batch.actions
            for command in (
                *action.always,
                *action.on_success,
                *action.on_failure,
                *action.on_pushed_failure,
            )
        ]
        commands.extend(
            command
            for policy in batch.reactive_policies
            for rule in policy.rules
            for command in rule.commands
        )
        commands.extend(
            command
            for clock in batch.clocks
            for stage in clock.pressure_stages
            for command in stage.commands
        )
        commands.extend(command for ending in batch.endings for command in ending.commands)
        errors: list[str] = []
        for command in commands:
            if command.kind != "apply_ruleset_effect":
                continue
            if catalog is None:
                errors.append("ruleset effect catalog is unavailable")
                continue
            errors.extend(
                catalog.validate_effect(str(command.event_type), command.payload)
            )
        return tuple(errors)

    @classmethod
    def _normalize_outcome_pair_effects(
        cls,
        batch: ScenarioIrBatch,
        catalog: ScenarioEffectCatalog | None,
    ) -> ScenarioIrBatch:
        if catalog is None:
            return batch
        actions: list[IrAction] = []
        normalized_action_ids: list[str] = []
        for action in batch.actions:
            if not action.checks and not action.abstract_checks:
                actions.append(action)
                continue
            always, success, failure, changed = cls._split_action_effect_pairs(
                action, catalog
            )
            if not changed or len(success) > 3 or len(failure) > 3:
                actions.append(action)
                continue
            actions.append(
                action.model_copy(
                    update={
                        "always": always,
                        "on_success": success,
                        "on_failure": failure,
                    }
                )
            )
            normalized_action_ids.append(action.id)
        if not normalized_action_ids:
            return batch
        note = (
            "Ruleset outcome-pair notation was split into success/failure effects: "
            + ", ".join(normalized_action_ids)
        )
        # Keep the deterministic rewrite visible to the independent reviewer even
        # when the weak model already filled the bounded assumptions list.
        assumptions = tuple(dict.fromkeys((note, *batch.assumptions)))[:16]
        return batch.model_copy(
            update={"actions": tuple(actions), "assumptions": assumptions}
        )

    @staticmethod
    def _normalize_ir_transport(
        payload: dict[str, Any],
        evidence: tuple[ScenarioAuthoringEvidence, ...],
    ) -> dict[str, Any]:
        """Rewrite only unambiguous model-facing aliases before strict validation."""

        command_alias_count = 0
        source_alias_count = 0
        paragraph_sources: dict[str, list[str]] = {}
        for item in evidence:
            if item.paragraph is None:
                continue
            paragraph_sources.setdefault(
                f"chunk_{item.paragraph}", []
            ).append(item.source_block_id)
        source_aliases = {
            alias: source_ids[0]
            for alias, source_ids in paragraph_sources.items()
            if len(source_ids) == 1
        }

        def visit(value: Any) -> Any:
            nonlocal command_alias_count, source_alias_count
            if isinstance(value, list):
                return [visit(item) for item in value]
            if not isinstance(value, dict):
                return value
            normalized = {key: visit(item) for key, item in value.items()}
            source_ids = normalized.get("source_block_ids")
            if isinstance(source_ids, list):
                rebound_ids = []
                for source_id in source_ids:
                    rebound = (
                        source_aliases.get(source_id, source_id)
                        if isinstance(source_id, str)
                        else source_id
                    )
                    source_alias_count += int(rebound != source_id)
                    rebound_ids.append(rebound)
                normalized["source_block_ids"] = rebound_ids
            aliases = _WORLD_COMMAND_ALIASES.get(normalized.get("kind"), {})
            for alias, canonical in aliases.items():
                if alias in normalized and canonical not in normalized:
                    normalized[canonical] = normalized.pop(alias)
                    command_alias_count += 1
            return normalized

        normalized = visit(payload)
        removed_completion_count = 0
        endings = normalized.get("endings")
        if isinstance(endings, list):
            for ending in endings:
                if not isinstance(ending, dict) or not isinstance(
                    ending.get("commands"), list
                ):
                    continue
                commands = ending["commands"]
                retained = [
                    command
                    for command in commands
                    if not (
                        isinstance(command, dict)
                        and command.get("kind") == "complete_run"
                    )
                ]
                removed_completion_count += len(commands) - len(retained)
                ending["commands"] = retained
        if not command_alias_count and not source_alias_count and not removed_completion_count:
            return normalized
        note = (
            "Deterministic IR transport normalization applied: "
            f"{command_alias_count} command alias fields rewritten, "
            f"{source_alias_count} unique paragraph source aliases rebound, "
            f"{removed_completion_count} redundant ending completions removed."
        )
        raw_assumptions = normalized.get("assumptions")
        assumptions = raw_assumptions if isinstance(raw_assumptions, list) else []
        normalized["assumptions"] = list(dict.fromkeys((note, *assumptions)))[:16]
        return normalized

    @staticmethod
    def _split_action_effect_pairs(
        action: IrAction,
        catalog: ScenarioEffectCatalog,
    ) -> tuple[
        tuple[WorldCommand, ...],
        tuple[WorldCommand, ...],
        tuple[WorldCommand, ...],
        bool,
    ]:
        always: list[WorldCommand] = []
        success: list[WorldCommand] = []
        failure: list[WorldCommand] = []
        pairs: list[tuple[WorldCommand, WorldCommand]] = []
        for branch, commands in (
            ("always", action.always),
            ("success", action.on_success),
            ("failure", action.on_failure),
        ):
            destination = (
                always
                if branch == "always"
                else success
                if branch == "success"
                else failure
            )
            for command in commands:
                split = (
                    catalog.split_outcome_payload(
                        str(command.event_type), command.payload
                    )
                    if command.kind == "apply_ruleset_effect"
                    else None
                )
                if split is None:
                    destination.append(command)
                    continue
                pairs.append(
                    (
                        command.model_copy(update={"payload": split[0]}),
                        command.model_copy(update={"payload": split[1]}),
                    )
                )
        for success_command, failure_command in pairs:
            if success_command not in success:
                success.append(success_command)
            if failure_command not in failure:
                failure.append(failure_command)
        return tuple(always), tuple(success), tuple(failure), bool(pairs)

    @classmethod
    def _batch_gate_errors(
        cls,
        batch: ScenarioIrBatch,
        *,
        effect_catalog: ScenarioEffectCatalog | None,
        coverage_targets: tuple[SourceCoverageSupplementTarget, ...],
        record_id_prefix: str,
    ) -> tuple[str, ...]:
        """Run every non-Schema partition gate, including after safe degradation."""

        errors = list(cls._batch_effect_errors(batch, effect_catalog))
        invalid_ids = cls._invalid_prefixed_record_ids(batch, record_id_prefix)
        if invalid_ids:
            errors.append(
                f"coverage supplement record IDs must start with {record_id_prefix}: "
                + ", ".join(invalid_ids)
            )
        unmet_targets = cls._unmet_coverage_targets(batch, coverage_targets)
        if unmet_targets:
            errors.append(
                "coverage supplement omitted targets "
                + ", ".join(
                    f"{item.source_block_id}/{item.requirement_key}"
                    for item in unmet_targets
                )
            )
        return tuple(errors)

    @staticmethod
    def _unmet_coverage_targets(
        batch: ScenarioIrBatch,
        targets: tuple[SourceCoverageSupplementTarget, ...],
    ) -> tuple[SourceCoverageSupplementTarget, ...]:
        if not targets:
            return ()
        groups = {
            "locations": batch.locations,
            "location_links": batch.location_links,
            "entities": batch.entities,
            "clocks": batch.clocks,
            "resources": batch.resources,
            "clues": batch.clues,
            "operators": batch.actions,
            "task_methods": batch.task_methods,
            "reactive_policies": batch.reactive_policies,
            "consequence_signals": batch.consequence_signals,
            "endings": batch.endings,
        }
        unmet: list[SourceCoverageSupplementTarget] = []
        for target in targets:
            matched = sum(
                target.source_block_id in record.source_block_ids
                and ConstrainedScenarioContractAuthoringAdapter._ir_record_satisfies_requirement(
                    target.requirement_key, kind, record
                )
                for kind in target.acceptable_record_kinds
                for record in groups[kind]
            )
            if matched < target.required_additional_count:
                unmet.append(target)
        return tuple(unmet)

    @staticmethod
    def _invalid_prefixed_record_ids(
        batch: ScenarioIrBatch,
        prefix: str,
    ) -> tuple[str, ...]:
        if not prefix:
            return ()
        groups = (
            ("locations", batch.locations),
            ("entities", batch.entities),
            ("clocks", batch.clocks),
            ("resources", batch.resources),
            ("clues", batch.clues),
            ("actions", batch.actions),
            ("task_methods", batch.task_methods),
            ("reactive_policies", batch.reactive_policies),
            ("consequence_signals", batch.consequence_signals),
            ("endings", batch.endings),
        )
        return tuple(
            f"{group}.{index}={item.id}"
            for group, records in groups
            for index, item in enumerate(records)
            if not item.id.startswith(prefix)
        )

    @staticmethod
    def _ir_record_satisfies_requirement(
        requirement_key: str, kind: str, record: Any
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
                and (record.checks or record.abstract_checks)
            )
        if requirement_key == "explicit_pressure":
            if kind in {"clocks", "consequence_signals"}:
                return True
            if kind == "operators":
                return any(
                    command.kind == "advance_clock"
                    for command in (
                        *record.always,
                        *record.on_success,
                        *record.on_failure,
                        *record.on_pushed_failure,
                    )
                )
            if kind == "reactive_policies":
                return any(
                    command.kind == "advance_clock"
                    for rule in record.rules
                    for command in rule.commands
                )
            return False
        if requirement_key == "explicit_ruleset_effect":
            return bool(
                kind == "operators"
                and any(
                    command.kind == "apply_ruleset_effect"
                    for command in (
                        *record.always,
                        *record.on_success,
                        *record.on_failure,
                        *record.on_pushed_failure,
                    )
                )
            )
        if requirement_key == "explicit_terminal_method":
            return bool(
                kind == "operators"
                and any(
                    command.kind == "set_entity_status"
                    or is_terminal_method_fact_command(command)
                    for command in record.on_success
                )
            )
        if requirement_key == "explicit_terminal_observation":
            return bool(
                kind == "operators"
                and any(
                    is_terminal_observation_fact_command(command)
                    for command in record.on_success
                )
            )
        return True

    @staticmethod
    def partitions(
        evidence: tuple[ScenarioAuthoringEvidence, ...],
    ) -> tuple[tuple[ScenarioAuthoringEvidence, ...], ...]:
        partitions: list[tuple[ScenarioAuthoringEvidence, ...]] = []
        current: list[ScenarioAuthoringEvidence] = []
        characters = 0
        for item in evidence:
            size = len(item.text) + sum(len(candidate.model_dump_json()) for candidate in item.entity_candidates)
            if item.entity_candidates and size > _PARTITION_CHARACTER_LIMIT:
                raise ValueError("Entity candidate catalog exceeds source partition budget")
            if current and (
                len(current) >= _PARTITION_BLOCK_LIMIT
                or characters + size > _PARTITION_CHARACTER_LIMIT
            ):
                partitions.append(tuple(current))
                current = []
                characters = 0
            current.append(item)
            characters += size
        if current:
            partitions.append(tuple(current))
        return tuple(partitions)

    @staticmethod
    def _validate_source_refs(
        contract: ScenarioContract,
        evidence: tuple[ScenarioAuthoringEvidence, ...],
    ) -> None:
        from ai_kp.platform.resolution.authoring_entity_catalog import validate_authoring_entities

        validate_authoring_entities(contract, {
            item.source_block_id: item.entity_candidates for item in evidence
        })
        allowed = {
            (
                item.source_block_id,
                item.document_id,
                item.page,
                item.paragraph,
            )
            for item in evidence
        }
        groups = (
            contract.locations,
            contract.location_links,
            contract.entities,
            contract.clocks,
            contract.resources,
            contract.clues,
            contract.operators,
            contract.task_methods,
            contract.reactive_policies,
            contract.response_obligations,
            contract.trigger_rules,
            contract.pressure_tracks,
            contract.consequence_signals,
            contract.endings,
        )
        for record in (item for group in groups for item in group):
            for ref in record.source_refs:
                locator = (
                    ref.source_block_id,
                    ref.document_id,
                    ref.page,
                    ref.paragraph,
                )
                if locator not in allowed:
                    raise ValueError(
                        "Scenario contract used a source locator outside the evidence catalog"
                    )

    @staticmethod
    def _repair_diagnostics(
        plan: ScenarioIrRepairPlan | ScenarioIrCitationRepairPlan,
        *,
        partition_index: int,
        model_attempt: int,
        status: Literal["applied", "failed", "discarded"],
    ) -> tuple[ScenarioIrRepairDiagnostic, ...]:
        return tuple(
            ScenarioIrRepairDiagnostic(
                partition_index=partition_index,
                model_attempt=model_attempt,
                group=target.group,
                record_index=target.record_index,
                status=status,
                validation_errors=target.validation_errors,
            )
            for target in plan.targets
        )

    @staticmethod
    def _repair_messages(
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        *,
        ruleset_id: str,
        partition_index: int,
        plan: ScenarioIrRepairPlan | ScenarioIrCitationRepairPlan,
        errors: list[str],
    ) -> list[ChatMessage]:
        sources = [item.public_descriptor() for item in evidence]
        citation_only = isinstance(plan, ScenarioIrCitationRepairPlan)
        system_content = (
            "只返回 JSON。这是引用专用修复：每个目标恰好返回一次 group、"
            "record_index 和 source_block_ids；source_block_ids 必须选 1 至 8 个，"
            "且只能从该目标 allowed_source_block_ids 原样选择。不得返回或修改"
            "id、标题、正文、命令、规则、人物身份或其他字段；未知引用会被"
            "服务器拒绝。证据不足时不得发明引用。"
            if citation_only
            else (
                "只返回 JSON。你只修复服务器点名的 ScenarioIrBatch 失败记录，不重新生成"
                "分区，也不修改任何已通过记录。每个 group 和 record_index 必须与修复目标"
                "完全一致，并且每个目标恰好返回一次 replacement。record 必须符合对应记录"
                "Schema，只能引用证据目录中的 source_block_ids。保留原记录由证据支持的"
                "身份与含义；删除无法由证据支持的字段，不得补写来源外规则、效果或秘密。"
            )
        )
        response_schema = (
            _CITATION_REPAIR_JSON_SCHEMA if citation_only else _REPAIR_JSON_SCHEMA
        )
        target_schema = (
            "引用修复不提供也不接受记录正文 Schema。"
            if citation_only
            else json.dumps(plan.record_schemas(), ensure_ascii=False)
        )
        return [
            ChatMessage(
                role="system",
                content=system_content,
            ),
            ChatMessage(
                role="user",
                content=(
                    f"规则系统：{ruleset_id}\n分区序号：{partition_index}\n"
                    f"响应 JSON Schema：{response_schema}\n"
                    "目标记录 Schema："
                    + target_schema
                    + "\n证据目录："
                    + json.dumps(sources, ensure_ascii=False)
                    + "\n修复目标："
                    + plan.model_dump_json()
                    + (f"\n最近校验错误：{errors[-1]}" if errors else "")
                ),
            ),
        ]

    @staticmethod
    def _coverage_supplement_messages(
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        *,
        ruleset_id: str,
        partition_index: int,
        targets: tuple[SourceCoverageSupplementTarget, ...],
        errors: list[str],
        effect_catalog: ScenarioEffectCatalog | None,
        record_id_prefix: str,
    ) -> list[ChatMessage]:
        sources = [item.public_descriptor() for item in evidence]
        correction = f"\n上次补写未通过校验：{errors[-1]}" if errors else ""
        return [
            ChatMessage(
                role="system",
                content=(
                    "只返回 JSON。现有契约已保留，你只补写服务器点名的未覆盖来源义务，"
                    "不要重生成分区或复述已有内容。每个目标只能生成 acceptable_record_kinds "
                    "允许的记录，至少达到 required_additional_count，并且每条记录必须直接引用"
                    "目标 source_block_id。不得为满足覆盖数字虚构条件、因果、检定、损失、秘密"
                    "或结局。契约 kinds 中 operators 对应 IR 的 actions 数组，其他 kind 与 IR "
                    "数组同名；不得输出 operators 数组。证据不足时输出无法通过目标的最小结果，"
                    "让服务器保持拒绝。只有来源明确给出规则效果时，才可使用 "
                    "apply_ruleset_effect；event_type 必须来自效果目录，payload 必须精确符合参数。"
                    "若参数声明 outcome_pair_separator，必须把分隔符左/右值分别放入"
                    " on_success/on_failure，不得把成对记法当作单个骰式。"
                    f"所有新增顶层记录的 id 必须以 {record_id_prefix} 开头；这是服务器分配的"
                    "命名空间，不得省略或改写。"
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"规则系统：{ruleset_id}\n补写分区序号：{partition_index}\n"
                    "目标限定的 ScenarioIrBatch JSON Schema："
                    + _coverage_supplement_json_schema(targets)
                    + "\n"
                    "可执行规则效果目录："
                    + json.dumps(
                        effect_catalog.model_dump(mode="json")
                        if effect_catalog is not None
                        else {"entries": []},
                        ensure_ascii=False,
                    )
                    + "\n"
                    "补写目标："
                    + json.dumps(
                        [item.model_dump(mode="json") for item in targets],
                        ensure_ascii=False,
                    )
                    + "\n证据目录："
                    + json.dumps(sources, ensure_ascii=False)
                    + "\n只返回新增 ScenarioIrBatch；不得输出 contract 包装层或来源定位。"
                    + correction
                ),
            ),
        ]

    @staticmethod
    def _clue_supplement_messages(
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        *,
        ruleset_id: str,
        partition_index: int,
        targets: tuple[SourceCoverageSupplementTarget, ...],
        errors: list[str],
    ) -> list[ChatMessage]:
        correction = f"\n上次槽位输出未通过校验：{errors[-1]}" if errors else ""
        return [
            ChatMessage(
                role="system",
                content=(
                    "只返回 JSON。只从目标来源提取玩家可发现的线索标题。不得输出 ID、"
                    "source_block_ids、事实路径、命令、检定、难度或结果；服务器会为每条"
                    "线索建立可执行发现动作与布尔事实。不要把邻近上下文写成目标线索。"
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"规则系统：{ruleset_id}\n补写分区序号：{partition_index}\n"
                    "线索槽位 JSON Schema："
                    + coverage_clue_schema_json()
                    + "\n补写目标："
                    + json.dumps(
                        [item.model_dump(mode="json") for item in targets],
                        ensure_ascii=False,
                    )
                    + "\n证据目录："
                    + json.dumps(
                        [item.public_descriptor() for item in evidence],
                        ensure_ascii=False,
                    )
                    + correction
                ),
            ),
        ]

    @staticmethod
    def _ending_supplement_messages(
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        *,
        ruleset_id: str,
        partition_index: int,
        targets: tuple[SourceCoverageSupplementTarget, ...],
        candidates: tuple[EndingOperatorCandidate, ...],
        state_candidates: tuple[EndingStateCandidate, ...],
        errors: list[str],
    ) -> list[ChatMessage]:
        correction = f"\n上次槽位输出未通过校验：{errors[-1]}" if errors else ""
        return [
            ChatMessage(
                role="system",
                content=(
                    "只返回 JSON。把来源中的结局条件链接到精确匹配的既有 operator 结果"
                    "或既有权威状态；如果目录没有精确表达来源条件，本次不得伪造触发动作，"
                    "应让输出校验失败，由后续补写真正会改变世界状态的 operator。"
                    "不得输出 ID、source_block_ids、StateCondition、目录外事实路径、命令、奖励、"
                    "伤害或 SAN。all_of/state_all_of 表示全部发生，"
                    "any_of/state_any_of 表示"
                    "任一发生。既有 operator 的 outcome 方向必须和来源一致，不能用‘成功做了"
                    "另一件事’代替失败、死亡、牺牲或被吞噬。状态比较值必须来自证据，"
                    "不能自行发明阈值或事实。"
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"规则系统：{ruleset_id}\n补写分区序号：{partition_index}\n"
                    "结局槽位 JSON Schema："
                    + coverage_ending_schema_json()
                    + "\n补写目标："
                    + json.dumps(
                        [item.model_dump(mode="json") for item in targets],
                        ensure_ascii=False,
                    )
                    + "\n允许的 operator 结果目录："
                    + json.dumps(
                        [item.model_dump(mode="json") for item in candidates],
                        ensure_ascii=False,
                    )
                    + "\n允许的既有状态条件目录："
                    + json.dumps(
                        [item.model_dump(mode="json") for item in state_candidates],
                        ensure_ascii=False,
                    )
                    + "\n证据目录："
                    + json.dumps(
                        [item.public_descriptor() for item in evidence],
                        ensure_ascii=False,
                    )
                    + correction
                ),
            ),
        ]

    @staticmethod
    def _messages(
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        *,
        ruleset_id: str,
        partition_index: int,
        errors: list[str],
        effect_catalog: ScenarioEffectCatalog | None,
    ) -> list[ChatMessage]:
        sources = [item.public_descriptor() for item in evidence]
        correction = f"\n上次输出未通过校验：{errors[-1]}" if errors else ""
        return [
            ChatMessage(
                role="system",
                content=(
                    "只返回 JSON。把这一小组模组证据提取为 ScenarioIrBatch 局部记录，不复述"
                    "固定剧情路线，也不要生成完整 ScenarioContract。每条记录必须列出支持它"
                    "的 source_block_ids，且只能逐字使用证据目录中的 ID。不得生成代码、脚本、"
                    "SQL、任意命令类型或来源外规则。"
                    "玩家应可尝试自由调查、交涉、移动和有因果成本的替代方案；不存在来源"
                    "依据的检定、伤害、SAN、资源、结局或秘密不得猜测。actions.public_setup "
                    "必须保持待结算中性。核心线索应有可恢复发现路径，结局必须由显式状态条件"
                    "触发。来源明确写出检定但未给规则键时，把原文技能、属性或能力词写入 "
                    "actions.abstract_checks.term，由服务器规则插件映射；不得猜 checks.skill_key。"
                    "宽泛词（如修理、社交）可由插件映射成玩家可选的多个合理检定。"
                    "来源明确给出角色规则后果时，只能用 apply_ruleset_effect，event_type 必须来自"
                    "效果目录且 payload 参数必须精确匹配；目录外效果不得写成 emit_event 冒充。"
                    "若参数声明 outcome_pair_separator，必须把分隔符左/右值分别写入"
                    " on_success/on_failure。"
                    "人物实体必须把来源明确事实写入 canonical_profile。"
                    "实体的 module_entity_id 只能从该记录所引用证据的 entity_candidates 中逐字选择；"
                    "无匹配候选时留空，不按同名猜 ID。候选只表示身份和可用维度，不表示状态事实。"
                    "来源明确支持模板状态变化时，填写 actions.world_state_effects，不要选择命令类型："
                    "entity_id 引用本次声明的实体 id，dimension 只能选该候选的 state_dimensions；"
                    "value 是来源支持的文本/整数/布尔值或 null；visibility 不能低于 state_visibility_floor；"
                    "applies_on 明确选择 always/success/failure/pushed_failure，服务器生成 set_world_entity_state。"
                    "维度目录为空时不得生成此记录；不能把态度自动等同于合作程度。"
                    "状态变化不能用 set_fact 写入 entities.<id>.<dimension> 等影子事实路径，"
                    "也不能把执行后才成立的状态提前放入 canonical_profile.known_facts。"
                    "只有来源可合理约束的表演特征才写 derived_profile。NPC 对玩家主动询问必须提供的事实、状态表现、"
                    "身体行为和边界写入 entity.response_obligations，基础对白内容不得用检定锁住。"
                    "线索和玩家资料必须把成功后需要实际呈现的具体内容写入 "
                    "clues.public_content；不得只写‘找到线索’或资料标题。服务器会将该内容绑定到"
                    " discovery action 的成功信息义务。"
                    "检定 choices 要分别声明 scope、bonus_dice、automatic_information、失败风险"
                    "与推动失败风险；没有来源依据的修正或伤害不得猜测。on_pushed_failure "
                    "只能写推动失败时来源明确的严重效果，普通失败效果继续写 on_failure。"
                    "来源明确的通用压力写入 clock.pressure_stages。明确的 if/when 事件反应写为"
                    " reactive rule 的"
                    " trigger=semantic_event 与抽象 event_type；不得用某个模组名称或角色名充当"
                    "事件类型。若事件针对实体，使用结构化 target_entity_id；不得从玩家文本关键词"
                    "直接执行后果。"
                    "只提取当前证据直接支持的最小记录；其他数组保持为空。"
                    "每个玩家 actions 必须声明地点作用域：优先返回当前分区 locations "
                    "数组的从 0 开始 location_slot，或返回精确 location_id；服务器会"
                    "生成 scene_id 前置条件，不要自行猜 StateCondition。一个 action 只能"
                    "属于一个地点；来源同时支持多个地点时拆成多条 action。只有来源"
                    "明确说无论身处何地都可执行的跨场景/系统性行为，才可设 "
                    "global_action=true；缺失、不确定或多义时不得默认 global。"
                    "证据目录中的 section_path 和 scene_key 是服务器提取的结构"
                    "提示；选择地点时只能使用其中与已声明地点标题精确对应的"
                    "层级，不得根据语义猜测。"
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"规则系统：{ruleset_id}\n分区序号：{partition_index}\n"
                    f"ScenarioIrBatch JSON Schema：{_IR_JSON_SCHEMA}\n"
                    "可执行规则效果目录："
                    + json.dumps(
                        effect_catalog.model_dump(mode="json")
                        if effect_catalog is not None
                        else {"entries": []},
                        ensure_ascii=False,
                    )
                    + "\n"
                    f"证据目录：{json.dumps(sources, ensure_ascii=False)}\n"
                    "返回一个 ScenarioIrBatch。不要输出 contract 包装层，不要输出 source_refs、"
                    "document_id、页码或段落；服务器会从 source_block_ids 绑定这些权威字段。"
                    "无法由证据确定的内容放入 assumptions，不要伪造。" + correction
                ),
            ),
        ]


__all__ = [
    "ConstrainedScenarioContractAuthoringAdapter",
    "ScenarioAuthoringEvidence",
    "ScenarioContractAuthoringResult",
    "ScenarioContractReview",
    "ScenarioPartitionAuthoringResult",
    "candidate_after_independent_review",
]

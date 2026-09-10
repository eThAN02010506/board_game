"""Application boundary for compiling, publishing, and binding scenario contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ai_kp.application.errors import ConflictError, InvalidInputError
from ai_kp.application.scenario_authoring_entity_catalog import authoring_entity_catalog
from ai_kp.application.scenario_entity_identity import validate_scenario_entity_identities
from ai_kp.platform.modules.scenario_scopes import (
    ScenarioSourceScope,
    chunks_for_scenario_scope,
    infer_scenario_source_scopes,
)
from ai_kp.platform.ports.llm import LlmClient
from ai_kp.platform.resolution.check_catalog import ScenarioCheckCatalog
from ai_kp.platform.resolution.contracts import ScenarioContract
from ai_kp.platform.resolution.effect_catalog import ScenarioEffectCatalog
from ai_kp.platform.resolution.evidence_compiler import (
    EvidenceBlock,
    EvidenceBoundCompilationResult,
    EvidenceBoundContractCandidate,
    EvidenceBoundScenarioCompiler,
    validate_contract_provenance,
)
from ai_kp.platform.resolution.location_travel import (
    materialize_location_travel_operators,
)
from ai_kp.platform.resolution.scenario_authoring import (
    ConstrainedScenarioContractAuthoringAdapter,
    ScenarioAuthoringEvidence,
    candidate_after_independent_review,
)
from ai_kp.platform.resolution.scenario_compiler import (
    ContractValidationIssue,
    ScenarioCompilationResult,
    ScenarioContractCompiler,
)
from ai_kp.platform.resolution.source_coverage import (
    SourceCoverageSupplementTarget,
    infer_source_coverage_requirements,
)
from ai_kp.rulesets import get_ruleset

AUTHORING_CHARACTER_BUDGET = 80_000
# Character count is the primary context-cost bound. Imported office documents
# often produce hundreds of very small paragraph chunks, so a low record-count
# cap can discard most of a short source while barely using the token budget.
# MinerU and Office importers deliberately preserve paragraph-level provenance.
# A short scenario can therefore contain several hundred small evidence blocks
# while staying well below the character budget (the real acceptance corpus has
# examples above 300).  Partition authoring already limits each model call to a
# much smaller batch, so this cap bounds total work without falsely treating a
# short, fully readable scenario as a truncated source.
AUTHORING_BLOCK_LIMIT = 512
AUTHORING_BLOCK_CHARACTER_LIMIT = 12_000
COVERAGE_SUPPLEMENT_BLOCK_LIMIT = 5
COVERAGE_SUPPLEMENT_CHARACTER_LIMIT = 14_000
COVERAGE_SUPPLEMENT_TARGET_LIMIT = 6
COVERAGE_SUPPLEMENT_CONTEXT_RADIUS = 2

_SEMANTIC_PRIORITY = {
    "keeper_overview": 0,
    "scene": 0,
    "clue": 0,
    "check": 0,
    "san_check": 0,
    "npc": 0,
    "ending": 0,
    "optional_branch": 0,
    "scenario_metadata": 1,
    "heading": 1,
    "stat_block": 1,
    "handout": 2,
    "table": 2,
    "read_aloud": 3,
    "text": 4,
    "reference": 5,
}


def _bounded_authoring_text(value: Any) -> str:
    return str(value or "").strip()[:AUTHORING_BLOCK_CHARACTER_LIMIT]


@dataclass(frozen=True)
class ScenarioContractGenerationResult:
    authoring: dict[str, Any]
    compilation: EvidenceBoundCompilationResult | None
    version: dict[str, Any] | None
    auto_published: bool
    corpus_block_count: int
    corpus_total_block_count: int
    corpus_truncated: bool


@dataclass(frozen=True)
class PreparedScenarioContractGeneration:
    module: dict[str, Any]
    run: dict[str, Any] | None
    source_scope: ScenarioSourceScope
    source_fingerprint: str
    evidence: tuple[ScenarioAuthoringEvidence, ...]
    corpus_total_block_count: int
    corpus_truncated: bool


class ScenarioContractService:
    def __init__(self, repo: Any):
        self.repo = repo
        self.compiler = ScenarioContractCompiler(
            get_ruleset().scenario_effect_catalog()
        )
        self.evidence_compiler = EvidenceBoundScenarioCompiler(self.compiler)

    async def generate_from_module(
        self,
        module_id: str,
        llm: LlmClient,
        *,
        ruleset_id: str,
        automation_level: str,
        created_by_member_id: str | None,
        source_scope_key: str | None = None,
    ) -> ScenarioContractGenerationResult:
        """Author outside SQLite, then revalidate the entire source before storing."""

        prepared = self.prepare_generation(
            module_id,
            source_scope_key=source_scope_key,
            ruleset_id=ruleset_id,
        )
        source_fingerprint = prepared.source_fingerprint
        evidence = prepared.evidence
        truncated = prepared.corpus_truncated
        adapter = self.authoring_adapter(llm, ruleset_id)
        authoring = await adapter.author(
            evidence,
            contract_id=f"module-{source_fingerprint[:24]}",
            source_version=1,
            ruleset_id=ruleset_id,
            title=prepared.source_scope.title,
            corpus_truncated=truncated,
        )
        authoring_payload = authoring.model_dump(mode="json")
        if authoring.candidate is None:
            return ScenarioContractGenerationResult(
                authoring=authoring_payload,
                compilation=None,
                version=None,
                auto_published=False,
                corpus_block_count=len(evidence),
                corpus_total_block_count=prepared.corpus_total_block_count,
                corpus_truncated=truncated,
            )

        candidate = authoring.candidate
        if automation_level == "ai_kp":
            review = await adapter.review(candidate, evidence)
            authoring_payload["review"] = review.model_dump(mode="json")
            candidate = candidate_after_independent_review(candidate, review)
        else:
            authoring_payload["review"] = {
                "decision": "not_requested",
                "findings": [],
                "assumptions_resolved": False,
            }

        # Deterministic compilation can explore thousands of states. Keep it
        # outside the SQLite writer lock, then revalidate the source and persist
        # the already-computed result in one short transaction.
        compilation = self.compile_evidence_bound_candidate(module_id, candidate)
        self.repo.begin_immediate()
        refreshed_module = self.repo.get_module(module_id)
        refreshed_chunks = self.chunks_for_scope(
            module_id,
            prepared.source_scope.key,
        )
        refreshed_chunks = self.chunks_cited_by_evidence(
            refreshed_chunks,
            evidence,
        )
        if self.generation_source_fingerprint(refreshed_module, refreshed_chunks) != source_fingerprint:
            raise ConflictError(
                "Module source changed while its scenario contract was generated"
            )
        saved, auto_published = self.persist_evidence_bound_compilation(
            module_id,
            compilation,
            automation_level=automation_level,
            created_by_member_id=created_by_member_id,
        )
        return ScenarioContractGenerationResult(
            authoring=authoring_payload,
            compilation=compilation,
            version=saved,
            auto_published=auto_published,
            corpus_block_count=len(evidence),
            corpus_total_block_count=prepared.corpus_total_block_count,
            corpus_truncated=truncated,
        )

    def prepare_generation(
        self,
        module_id: str,
        *,
        source_scope_key: str | None = None,
        ruleset_id: str | None = None,
    ) -> PreparedScenarioContractGeneration:
        """Snapshot bounded evidence and its full-corpus fingerprint before queueing."""

        module = self.repo.get_module(module_id)
        chunks = self.module_chunks(module_id)
        if not chunks:
            raise InvalidInputError("Module has no textual source blocks to compile")
        scopes = infer_scenario_source_scopes(str(module["title"]), chunks)
        scope = self.select_source_scope(scopes, source_scope_key)
        scoped_chunks = chunks_for_scenario_scope(chunks, scope)
        ruleset = get_ruleset(ruleset_id)
        evidence, truncated = self.authoring_evidence(
            module,
            scoped_chunks,
            check_catalog=ruleset.scenario_check_catalog(),
            effect_catalog=ruleset.scenario_effect_catalog(),
        )
        if not evidence:
            raise InvalidInputError("Module has no textual source blocks to compile")
        entity_catalog = authoring_entity_catalog(self.repo, module, {item.source_block_id for item in evidence})
        evidence = tuple(item.model_copy(update={"entity_candidates": entity_catalog.get(item.source_block_id, ())}) for item in evidence)
        return PreparedScenarioContractGeneration(
            module=module,
            run=self.repo.get_active_campaign_module_run(str(module["campaign_id"])),
            source_scope=scope,
            source_fingerprint=self.source_fingerprint(
                module,
                self.chunks_cited_by_evidence(scoped_chunks, evidence),
                entity_catalog={key: [item.model_dump(mode="json") for item in values]
                                for key, values in entity_catalog.items()},
            ),
            evidence=evidence,
            corpus_total_block_count=len(scoped_chunks),
            corpus_truncated=truncated,
        )

    def source_scopes(self, module_id: str) -> tuple[ScenarioSourceScope, ...]:
        module = self.repo.get_module(module_id)
        return infer_scenario_source_scopes(
            str(module["title"]),
            self.module_chunks(module_id),
        )

    def chunks_for_scope(
        self,
        module_id: str,
        source_scope_key: str,
    ) -> list[dict[str, Any]]:
        module = self.repo.get_module(module_id)
        chunks = self.module_chunks(module_id)
        scopes = infer_scenario_source_scopes(str(module["title"]), chunks)
        scope = self.select_source_scope(scopes, source_scope_key)
        return chunks_for_scenario_scope(chunks, scope)

    @staticmethod
    def chunks_cited_by_evidence(
        chunks: list[dict[str, Any]],
        evidence: tuple[ScenarioAuthoringEvidence, ...],
    ) -> list[dict[str, Any]]:
        cited_ids = {item.source_block_id for item in evidence}
        return [item for item in chunks if str(item["id"]) in cited_ids]

    @staticmethod
    def select_source_scope(
        scopes: tuple[ScenarioSourceScope, ...],
        source_scope_key: str | None,
    ) -> ScenarioSourceScope:
        if not scopes:
            raise InvalidInputError("Module has no textual source blocks to compile")
        if source_scope_key is None:
            if len(scopes) > 1:
                raise InvalidInputError(
                    "This document contains multiple top-level sections; "
                    "select one source scope before generating a scenario contract"
                )
            return scopes[0]
        for scope in scopes:
            if scope.key == source_scope_key:
                return scope
        raise InvalidInputError("Unknown scenario source scope")

    @staticmethod
    def authoring_adapter(
        llm: LlmClient, ruleset_id: str
    ) -> ConstrainedScenarioContractAuthoringAdapter:
        ruleset = get_ruleset(ruleset_id)
        return ConstrainedScenarioContractAuthoringAdapter(
            llm,
            check_catalog=ruleset.scenario_check_catalog(),
            effect_catalog=ruleset.scenario_effect_catalog(),
        )

    @staticmethod
    def coverage_supplement_groups(
        evidence: tuple[ScenarioAuthoringEvidence, ...],
        targets: tuple[SourceCoverageSupplementTarget, ...],
    ) -> tuple[
        tuple[
            tuple[ScenarioAuthoringEvidence, ...],
            tuple[SourceCoverageSupplementTarget, ...],
        ],
        ...,
    ]:
        """Group only uncovered evidence into small, deterministic model calls."""

        if not targets:
            return ()
        evidence_by_id = {item.source_block_id: item for item in evidence}
        unknown = sorted(
            {item.source_block_id for item in targets} - evidence_by_id.keys()
        )
        if unknown:
            raise ValueError(f"Coverage targets cite unknown evidence blocks: {unknown}")
        targets_by_id: dict[str, list[SourceCoverageSupplementTarget]] = {}
        for target in targets:
            targets_by_id.setdefault(target.source_block_id, []).append(target)
        groups = []
        for target_index, target_evidence in enumerate(evidence):
            source_targets = targets_by_id.get(target_evidence.source_block_id)
            if not source_targets:
                continue
            context_indices = [target_index]
            characters = len(target_evidence.text)
            for distance in range(1, COVERAGE_SUPPLEMENT_CONTEXT_RADIUS + 1):
                for candidate_index in (
                    target_index - distance,
                    target_index + distance,
                ):
                    if not 0 <= candidate_index < len(evidence):
                        continue
                    candidate = evidence[candidate_index]
                    if (
                        len(context_indices) >= COVERAGE_SUPPLEMENT_BLOCK_LIMIT
                        or characters + len(candidate.text)
                        > COVERAGE_SUPPLEMENT_CHARACTER_LIMIT
                    ):
                        continue
                    context_indices.append(candidate_index)
                    characters += len(candidate.text)
            context = tuple(evidence[index] for index in sorted(context_indices))
            for offset in range(0, len(source_targets), COVERAGE_SUPPLEMENT_TARGET_LIMIT):
                groups.append(
                    (
                        context,
                        tuple(
                            source_targets[
                                offset : offset + COVERAGE_SUPPLEMENT_TARGET_LIMIT
                            ]
                        ),
                    )
                )
        return tuple(groups)

    def module_chunks(self, module_id: str) -> list[dict[str, Any]]:
        return self.repo.list_module_chunks(
            module_id,
            allowed_visibility=("player", "table", "kp", "secret"),
            spoiler_tags=None,
        )

    def authoritative_evidence_blocks(
        self, module_id: str
    ) -> tuple[EvidenceBlock, ...]:
        """Return the complete module corpus used to authenticate SourceRef values."""

        module = self.repo.get_module(module_id)
        document_id = str(module.get("source_hash") or module["id"])
        return tuple(
            EvidenceBlock(
                source_block_id=str(item["id"]),
                document_id=document_id,
                page=item.get("page_start"),
                paragraph=item.get("paragraph_start"),
                # Authoring evidence uses this same bounded source projection.
                text_hash=hashlib.sha256(
                    bounded_text.encode("utf-8")
                ).hexdigest(),
                semantic_kind=str(item.get("semantic_kind") or "text"),
                classification_confidence=float(
                    item.get("classification_confidence") or 0
                ),
            )
            for item in self.module_chunks(module_id)
            if (bounded_text := _bounded_authoring_text(item.get("text")))
        )

    def _verify_compilation_provenance(
        self,
        module_id: str,
        result: ScenarioCompilationResult,
    ) -> ScenarioCompilationResult:
        if result.contract is None:
            return result
        provenance_issues = validate_contract_provenance(
            result.contract,
            self.authoritative_evidence_blocks(module_id),
        )
        provenance_issues = (*provenance_issues, *self._entity_identity_issues(
            module_id, result.contract
        ))
        provenance_ready = not provenance_issues
        report = result.report.model_copy(
            update={
                # Manual drafts remain inspectable; authenticity is nevertheless
                # a hard publication condition.
                "provenance_ready": provenance_ready,
                "release_ready": bool(
                    result.report.release_ready
                    and provenance_ready
                ),
                "issues": (
                    *result.report.issues,
                    *(
                        issue.model_copy(update={"severity": "warning"})
                        for issue in provenance_issues
                    ),
                ),
            }
        )
        return result.model_copy(update={"report": report})

    def _entity_identity_issues(
        self, module_id: str, contract: ScenarioContract
    ) -> tuple[ContractValidationIssue, ...]:
        if not any(entity.module_entity_id for entity in contract.entities):
            return ()
        return validate_scenario_entity_identities(
            contract, self.repo.list_module_entities(module_id)
        )

    @staticmethod
    def source_fingerprint(module: dict, chunks: list[dict[str, Any]], *, entity_catalog: dict | None = None) -> str:
        payload = {
            "module_id": module["id"],
            "title": module["title"],
            "source_hash": module.get("source_hash"),
            "chunks": [
                {
                    "id": item["id"],
                    "title": item.get("title"),
                    "text": item.get("text"),
                    "visibility": item.get("visibility"),
                    "spoiler_tag": item.get("spoiler_tag"),
                    "source_locator": item.get("source_locator"),
                    "page_start": item.get("page_start"),
                    "paragraph_start": item.get("paragraph_start"),
                    "semantic_kind": item.get("semantic_kind"),
                }
                for item in chunks
            ],
        }
        if entity_catalog:
            payload["entity_catalog"] = entity_catalog
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def generation_source_fingerprint(self, module: dict, chunks: list[dict[str, Any]]) -> str:
        catalog = authoring_entity_catalog(self.repo, module, {item["id"] for item in chunks})
        return self.source_fingerprint(module, chunks, entity_catalog={
            key: [item.model_dump(mode="json") for item in values] for key, values in catalog.items()
        })

    @staticmethod
    def authoring_evidence(
        module: dict,
        chunks: list[dict[str, Any]],
        *,
        check_catalog: ScenarioCheckCatalog | None = None,
        effect_catalog: ScenarioEffectCatalog | None = None,
    ) -> tuple[tuple[ScenarioAuthoringEvidence, ...], bool]:
        candidates = sorted(
            chunks,
            key=lambda item: (
                _SEMANTIC_PRIORITY.get(str(item.get("semantic_kind") or "text"), 4),
                int(item.get("order_index") or 0),
                str(item["id"]),
            ),
        )
        selected: list[dict[str, Any]] = []
        used = 0
        for chunk in candidates:
            text = _bounded_authoring_text(chunk.get("text"))
            if not text:
                continue
            bounded = text
            if selected and used + len(bounded) > AUTHORING_CHARACTER_BUDGET:
                continue
            selected.append({**chunk, "bounded_text": bounded})
            used += len(bounded)
            if len(selected) >= AUTHORING_BLOCK_LIMIT:
                break
        selected.sort(
            key=lambda item: (int(item.get("order_index") or 0), str(item["id"]))
        )
        document_id = str(module.get("source_hash") or module["id"])
        evidence = tuple(
            ScenarioAuthoringEvidence(
                source_block_id=str(item["id"]),
                document_id=document_id,
                title=str(item.get("title") or ""),
                text=str(item["bounded_text"]),
                page=item.get("page_start"),
                paragraph=item.get("paragraph_start"),
                semantic_kind=str(item.get("semantic_kind") or "text"),
                classification_confidence=float(
                    item.get("classification_confidence") or 0
                ),
                heading_level=item.get("heading_level"),
                section_path=tuple(item.get("section_path") or ()),
                scene_key=item.get("scene_key"),
                coverage_requirements=infer_source_coverage_requirements(
                    semantic_kind=str(item.get("semantic_kind") or "text"),
                    classification_confidence=float(
                        item.get("classification_confidence") or 0
                    ),
                    text=str(item["bounded_text"]),
                    check_catalog=check_catalog,
                    effect_catalog=effect_catalog,
                ),
            )
            for item in selected
        )
        truncated = len(selected) < len(
            [item for item in chunks if str(item.get("text") or "").strip()]
        ) or any(
            len(str(item.get("text") or "").strip())
            > AUTHORING_BLOCK_CHARACTER_LIMIT
            for item in selected
        )
        return evidence, truncated

    def compile_evidence_bound_draft(
        self,
        module_id: str,
        candidate: EvidenceBoundContractCandidate,
        *,
        automation_level: str,
        created_by_member_id: str | None,
    ) -> tuple[EvidenceBoundCompilationResult, dict[str, Any] | None, bool]:
        """Persist valid candidates; only full AI may auto-publish the safest class."""

        result = self.compile_evidence_bound_candidate(module_id, candidate)
        saved, auto_published = self.persist_evidence_bound_compilation(
            module_id,
            result,
            automation_level=automation_level,
            created_by_member_id=created_by_member_id,
        )
        return result, saved, auto_published

    def compile_evidence_bound_candidate(
        self,
        module_id: str,
        candidate: EvidenceBoundContractCandidate,
    ) -> EvidenceBoundCompilationResult:
        """Compile and authenticate a candidate without acquiring a write lock."""

        result = self.evidence_compiler.compile(candidate)
        return self.authenticate_evidence_bound_compilation(
            module_id, candidate, result
        )

    def authenticate_evidence_bound_compilation(
        self,
        module_id: str,
        candidate: EvidenceBoundContractCandidate,
        result: EvidenceBoundCompilationResult,
    ) -> EvidenceBoundCompilationResult:
        """Bind a precomputed result to the module's authoritative evidence corpus."""

        authoritative = self.authoritative_evidence_blocks(module_id)
        authoritative_by_id = {
            item.source_block_id: item for item in authoritative
        }
        corpus_issues: list[ContractValidationIssue] = list(
            validate_contract_provenance(candidate.contract, authoritative)
        )
        corpus_issues.extend(self._entity_identity_issues(module_id, candidate.contract))
        for index, block in enumerate(candidate.evidence_blocks):
            expected = authoritative_by_id.get(block.source_block_id)
            if expected is None:
                corpus_issues.append(
                    ContractValidationIssue(
                        severity="error",
                        code="foreign_evidence_block",
                        path=f"evidence_blocks.{index}",
                        message="Evidence block does not belong to this module.",
                    )
                )
                continue
            if any(
                getattr(block, field) != getattr(expected, field)
                for field in (
                    "document_id",
                    "page",
                    "paragraph",
                    "text_hash",
                )
            ):
                corpus_issues.append(
                    ContractValidationIssue(
                        severity="error",
                        code="evidence_block_mismatch",
                        path=f"evidence_blocks.{index}",
                        message=(
                            "Evidence identity, locator, or content hash disagrees "
                            "with this module."
                        ),
                    )
                )
        if corpus_issues:
            report = result.report.model_copy(
                update={
                    "valid": False,
                    "release_ready": False,
                    "provenance_ready": False,
                    "issues": (*result.report.issues, *corpus_issues),
                }
            )
            result = result.model_copy(
                update={"decision": "rejected", "report": report}
            )
        return result

    def persist_evidence_bound_compilation(
        self,
        module_id: str,
        result: EvidenceBoundCompilationResult,
        *,
        automation_level: str,
        created_by_member_id: str | None,
    ) -> tuple[dict[str, Any] | None, bool]:
        """Persist one precomputed result in the caller's short write transaction."""

        self.repo.begin_immediate()
        if not result.report.valid or result.contract is None:
            return None, False
        saved = self.repo.create_scenario_contract_version(
            module_id=module_id,
            contract=result.contract,
            report=result.report,
            created_by_member_id=created_by_member_id,
        )
        if result.decision != "auto_publishable" or automation_level != "ai_kp":
            return saved, False
        published = self.repo.publish_scenario_contract_version(
            str(saved["id"]),
            expected_row_version=int(saved["row_version"]),
            published_by_member_id=created_by_member_id,
            live_report=result.report,
        )
        return published, True

    def compile_draft(
        self,
        module_id: str,
        payload: dict[str, Any],
        *,
        created_by_member_id: str | None,
    ) -> tuple[ScenarioCompilationResult, dict[str, Any] | None]:
        self.repo.begin_immediate()
        result = self._verify_compilation_provenance(
            module_id,
            self.compiler.compile(payload),
        )
        if not result.report.valid or result.contract is None:
            return result, None
        saved = self.repo.create_scenario_contract_version(
            module_id=module_id,
            contract=result.contract,
            report=result.report,
            created_by_member_id=created_by_member_id,
        )
        return result, saved

    def publish(
        self,
        version_id: str,
        *,
        expected_row_version: int,
        published_by_member_id: str | None,
    ) -> dict[str, Any]:
        self.repo.begin_immediate()
        current = self.repo.get_scenario_contract_version(version_id)
        ruleset = get_ruleset(current["contract"].ruleset_id)
        live_result = self._verify_compilation_provenance(
            str(current["module_id"]),
            ScenarioContractCompiler(
                ruleset.scenario_effect_catalog()
            ).compile(current["contract"]),
        )
        return self.repo.publish_scenario_contract_version(
            version_id,
            expected_row_version=expected_row_version,
            published_by_member_id=published_by_member_id,
            live_report=live_result.report,
        )

    def bind_run(self, run_id: str, contract_version_id: str) -> dict[str, Any]:
        self.repo.begin_immediate()
        current = self.repo.get_scenario_contract_version(contract_version_id)
        identity_issues = self._entity_identity_issues(str(current["module_id"]), current["contract"])
        if identity_issues:
            raise InvalidInputError("Scenario entity identity is no longer confirmed in the module")
        canonical = materialize_location_travel_operators(current["contract"])
        if canonical == current["contract"]:
            return self.repo.bind_module_run_contract(
                run_id=run_id, contract_version_id=contract_version_id
            )
        if current["status"] not in {"published", "superseded"}:
            return self.repo.bind_module_run_contract(
                run_id=run_id, contract_version_id=contract_version_id
            )

        # Check the run fence before publishing a replacement, so a failed
        # migration cannot supersede the module's current release as a side effect.
        self.repo.assert_unprogressed_scenario_contract_migration(run_id)
        ruleset = get_ruleset(canonical.ruleset_id)
        result = self._verify_compilation_provenance(
            str(current["module_id"]),
            ScenarioContractCompiler(
                ruleset.scenario_effect_catalog()
            ).compile(canonical),
        )
        if result.contract is None or not result.report.release_ready:
            raise ValueError(
                "Legacy scenario contract cannot be migrated to a release-ready "
                "canonical travel version; compile and review a new module version"
            )
        migration_report = result.report.model_copy(
            update={
                "issues": (
                    *result.report.issues,
                    ContractValidationIssue(
                        severity="warning",
                        code="legacy_travel_contract_migrated",
                        path=f"scenario_contract_versions.{contract_version_id}",
                        message=(
                            "This audited version replaces legacy location links that "
                            "were not executable operators; the replacement row ID is "
                            "the current scenario contract version."
                        ),
                    ),
                )
            }
        )
        if current["status"] == "superseded":
            replacement = next(
                (
                    item
                    for item in self.repo.list_module_scenario_contract_versions(
                        str(current["module_id"])
                    )
                    if item["contract_hash"] == result.report.contract_hash
                    and item["status"] == "published"
                ),
                None,
            )
            if replacement is None:
                raise ValueError(
                    "Superseded legacy contract has no published exact canonical "
                    "replacement; bind the module's current reviewed version"
                )
        else:
            replacement = self.repo.create_scenario_contract_version(
                module_id=str(current["module_id"]),
                contract=result.contract,
                report=migration_report,
                created_by_member_id=current.get("published_by_member_id"),
            )
            if replacement["status"] == "draft":
                replacement = self.repo.publish_scenario_contract_version(
                    str(replacement["id"]),
                    expected_row_version=int(replacement["row_version"]),
                    published_by_member_id=current.get("published_by_member_id"),
                    live_report=migration_report,
                )
            elif replacement["status"] != "published":
                raise ValueError(
                    "Canonical legacy replacement is no longer published; bind the "
                    "module's current reviewed version instead"
                )
        return self.repo.migrate_unprogressed_module_run_contract_binding(
            run_id=run_id,
            previous_contract_version_id=contract_version_id,
            contract_version_id=str(replacement["id"]),
        )


__all__ = ["PreparedScenarioContractGeneration", "ScenarioContractService"]

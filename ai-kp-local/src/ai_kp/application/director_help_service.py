"""Source-grounded, read-only advice for an explicitly requesting human KP."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ai_kp.application.ai_control_service import AiControlService
from ai_kp.application.director_help_projection import (
    SOURCE_REF_LIMIT,
    DirectorHelpProjector,
)
from ai_kp.application.errors import ConflictError, InvalidInputError
from ai_kp.application.ports.director import DirectorHelpDirector
from ai_kp.application.ports.repositories import DirectorHelpStore
from ai_kp.application.scenario_authority import (
    ScenarioAuthorityContext,
    load_scenario_authority_context,
    revalidate_scenario_authority_context,
)
from ai_kp.director.human_kp_help import DirectorHelpOutput
from ai_kp.platform.resolution.contracts import ScenarioContract
from ai_kp.platform.resolution.director_brief import (
    DirectorBrief,
    DirectorBriefCandidate,
    DirectorBriefProjector,
)
from ai_kp.platform.resolution.json_projection import canonical_json_bytes

_QUESTION_LIMIT = 2000
_CONTRACT_EVIDENCE_LIMIT = 12
_MODULE_EVIDENCE_LIMIT = 5
_RULE_EVIDENCE_LIMIT = 3
_CONTRACT_EVIDENCE_TEXT_LIMIT = 800
_SOURCE_EVIDENCE_TEXT_LIMIT = 1200
_EVIDENCE_VISIBILITIES = frozenset({"player", "table", "kp", "secret"})
_CONFIDENCE_CAP_REASON = "服务器已按可验证证据完整性将置信度上限设为中等。"


@dataclass(frozen=True)
class _EvidenceCollection:
    records: tuple[dict[str, Any], ...]
    total_count: int | None
    total_count_lower_bound: int
    truncated: bool


@dataclass(frozen=True)
class _AdviceBasis:
    authority: ScenarioAuthorityContext
    brief: DirectorBrief
    evidence: _EvidenceCollection
    basis_hash: str


class DirectorHelpService:
    """Build and validate one display-only answer without writing game state."""

    def __init__(self, repo: DirectorHelpStore):
        self.repo = repo

    async def advise(
        self,
        run_id: str,
        question: str,
        director: DirectorHelpDirector,
    ) -> dict[str, Any]:
        normalized_question = self._normalize_question(question)
        requested_run = self.repo.get_campaign_module_run(run_id)
        if str(requested_run.get("status")) != "active":
            raise ConflictError("Need Help is available only for the active module run")
        campaign_id = str(requested_run["campaign_id"])

        control_service = AiControlService(self.repo)
        control = control_service.authorize_kp_help(campaign_id)
        if control.run_id != run_id:
            raise ConflictError("Need Help run is no longer the campaign's active run")

        basis = self._build_basis(run_id, normalized_question)
        if basis.authority.module_run_version != control.run_version:
            raise ConflictError("Module run changed before Need Help could start")

        model_request = self._model_request(basis)
        raw_output = await director.advise_human_kp(
            campaign_id=campaign_id,
            brief=model_request,
        )

        # A model answer is useful only for the exact control, contract, source,
        # and world-state basis it saw.  None of these checks performs a write.
        try:
            revalidate_scenario_authority_context(self.repo, basis.authority)
        except ConflictError as exc:
            raise ConflictError(
                "Scenario or source evidence changed while Need Help was answering; ask again"
            ) from exc
        current = self._build_basis(run_id, normalized_question)
        if current.basis_hash != basis.basis_hash:
            raise ConflictError(
                "Scenario or source evidence changed while Need Help was answering; ask again"
            )
        control_service.revalidate(control)

        output = self._validated_output(raw_output, basis, model_request)
        selected = next(
            (
                candidate
                for candidate in basis.brief.candidates
                if candidate.candidate_id == output.candidate_id
            ),
            None,
        )
        evidence_by_id = {str(item["evidence_id"]): item for item in basis.evidence.records}
        confidence, confidence_reasons = self._verified_confidence(
            output=output,
            selected=selected,
            evidence_by_id=evidence_by_id,
            basis=basis,
            model_request=model_request,
        )
        response = {
            "run_id": run_id,
            "run_version": basis.authority.module_run_version,
            "contract_id": basis.authority.contract.contract_id,
            "contract_hash": basis.brief.contract_hash,
            "scenario_version": basis.authority.snapshot.scenario_version,
            "state_version": basis.authority.state_version,
            "basis_hash": basis.basis_hash,
            "question": normalized_question,
            "status": output.status,
            "answer": output.answer,
            "suggested_response": output.suggested_response,
            # Free-form text generated after seeing KP/secret context must never
            # be presented as player-safe. A future read-aloud path needs a
            # separate generator that receives only declassified evidence.
            "suggested_response_audience": "kp_review_only",
            "next_steps": list(output.next_steps),
            "confidence": confidence,
            "uncertainty_reasons": [
                *output.uncertainty_reasons[: 8 - len(confidence_reasons)],
                *confidence_reasons,
            ],
            "assumptions": list(output.assumptions),
            "follow_up_question": output.follow_up_question,
            "citations": [
                evidence_by_id[evidence_id]
                for evidence_id in output.evidence_ids
                if evidence_id in evidence_by_id
            ],
            "action": (
                DirectorHelpProjector.action(
                    contract=basis.authority.contract,
                    selected=selected,
                    requested_skill_key=output.requested_skill_key,
                )
                if selected is not None
                else None
            ),
            # These explicit flags keep UI wording and future integrations honest.
            "writes_performed": False,
            "can_execute": False,
        }
        # Establish the latest practical linearization point immediately before
        # returning display-only advice.  The response carries its basis hash so
        # clients never need to mistake it for a durable command authorization.
        control_service.revalidate(control)
        return response

    def _build_basis(self, run_id: str, question: str) -> _AdviceBasis:
        authority = load_scenario_authority_context(
            self.repo,
            run_id,
            state_policy="ephemeral",
        )
        if authority.run_status != "active":
            raise ConflictError("Need Help run is no longer active")

        try:
            brief = DirectorBriefProjector.project(
                authority.contract,
                authority.snapshot,
                authority.contract_hash,
                question,
            )
        except (TypeError, ValueError) as exc:
            raise ConflictError(f"Scenario contract cannot support Need Help: {exc}") from exc
        evidence = self._evidence(authority, authority.contract, brief, question)
        basis_hash = self._basis_hash(
            authority,
            brief,
            evidence,
        )
        return _AdviceBasis(
            authority=authority,
            brief=brief,
            evidence=evidence,
            basis_hash=basis_hash,
        )

    def _evidence(
        self,
        authority: ScenarioAuthorityContext,
        contract: ScenarioContract,
        brief: DirectorBrief,
        question: str,
    ) -> _EvidenceCollection:
        records: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(record: dict[str, Any]) -> None:
            evidence_id = str(record["evidence_id"])
            if evidence_id in seen:
                return
            seen.add(evidence_id)
            records.append(record)

        contract_evidence = {item.evidence_id: item for item in brief.evidence}
        priority_evidence_ids = [
            item.evidence_id for item in brief.evidence if item.kind == "location"
        ]
        priority_evidence_ids.extend(
            candidate.evidence_ids[0] for candidate in brief.candidates if candidate.evidence_ids
        )
        ordered_contract_evidence = [
            contract_evidence[evidence_id]
            for evidence_id in dict.fromkeys(
                (*priority_evidence_ids, *(item.evidence_id for item in brief.evidence))
            )
            if evidence_id in contract_evidence
        ]
        for item in ordered_contract_evidence[:_CONTRACT_EVIDENCE_LIMIT]:
            source_refs = item.source_refs[:SOURCE_REF_LIMIT]
            add(
                {
                    "evidence_id": item.evidence_id,
                    "source_type": f"scenario_contract:{item.kind}",
                    "authority": "executable_contract",
                    # ScenarioContract does not yet carry source visibility.
                    # Fail closed instead of implying its evidence is public.
                    "visibility": "kp",
                    "title": DirectorHelpProjector.citation_text(
                        item.title,
                        500,
                        fallback="Scenario evidence",
                    ),
                    "text": DirectorHelpProjector.citation_text(
                        item.summary,
                        _CONTRACT_EVIDENCE_TEXT_LIMIT,
                    ),
                    "source_locator": DirectorHelpProjector.source_locator(source_refs),
                    "source_refs": [
                        source_ref.model_dump(mode="json") for source_ref in source_refs
                    ],
                    "source_refs_total_count": len(item.source_refs),
                    "source_refs_truncated": len(item.source_refs) > len(source_refs),
                }
            )

        module_source_rows = self.repo.search_module(
            authority.module_id,
            question,
            allowed_visibility=("player", "table", "kp", "secret"),
            spoiler_tags=authority.active_spoiler_tags,
            limit=_MODULE_EVIDENCE_LIMIT + 1,
        )
        for source in module_source_rows[:_MODULE_EVIDENCE_LIMIT]:
            source_kind = str(source.get("source_type") or "unknown").strip()
            source_id = str(source.get("source_id") or "unknown").strip()
            if source_kind == "chunk":
                source_refs, source_ref_total, source_refs_truncated = (
                    DirectorHelpProjector.external_source_refs(
                        source_block_id=source_id,
                        document_id=str(source.get("module_id") or authority.module_id),
                    )
                )
            else:
                source_refs = []
                source_ref_total = 0
                source_refs_truncated = False
            evidence_id = DirectorHelpProjector.evidence_id(f"module:{source_kind}:{source_id}")
            add(
                {
                    "evidence_id": evidence_id,
                    "source_type": DirectorHelpProjector.source_type(
                        f"module:{source_kind}",
                    ),
                    "authority": "source_context_only",
                    "visibility": self._evidence_visibility(source.get("visibility")),
                    "title": DirectorHelpProjector.citation_text(
                        source.get("title") or source_id,
                        500,
                        fallback="Module source",
                    ),
                    "text": DirectorHelpProjector.citation_text(
                        source.get("text"),
                        _SOURCE_EVIDENCE_TEXT_LIMIT,
                    ),
                    "source_locator": DirectorHelpProjector.citation_locator(
                        source.get("source_locator")
                    ),
                    "source_refs": source_refs,
                    "source_refs_total_count": source_ref_total,
                    "source_refs_truncated": source_refs_truncated,
                }
            )

        try:
            rule_source = self.repo.find_rule_source(contract.ruleset_id)
        except KeyError:
            rule_chunk_rows: list[dict[str, Any]] = []
        else:
            rule_chunk_rows = self.repo.lexical_rule_chunks(
                str(rule_source["id"]),
                question,
                limit=_RULE_EVIDENCE_LIMIT + 1,
            )
        for chunk in rule_chunk_rows[:_RULE_EVIDENCE_LIMIT]:
            chapter = str(chunk.get("chapter") or "").strip()
            section = str(chunk.get("section") or "").strip()
            title = " · ".join(part for part in (chapter, section) if part)
            page_start = chunk.get("page_start")
            page_end = chunk.get("page_end")
            page_locator = (
                f"pages {page_start}-{page_end}"
                if (page_start is not None and page_end is not None and page_end != page_start)
                else f"page {page_start}"
                if page_start is not None
                else None
            )
            source_refs, source_ref_total, source_refs_truncated = (
                DirectorHelpProjector.external_source_refs(
                    source_block_id=str(chunk.get("id") or "unknown"),
                    document_id=str(rule_source.get("id") or "unknown"),
                    page=page_start,
                )
            )
            add(
                {
                    "evidence_id": DirectorHelpProjector.evidence_id(
                        f"rule_chunk:{chunk.get('id') or 'unknown'}"
                    ),
                    "source_type": "rulebook:original_text",
                    "authority": "source_context_only",
                    # Rule sources may contain Keeper-only chapters; without a
                    # finer-grained label they remain KP review material.
                    "visibility": "kp",
                    "title": DirectorHelpProjector.citation_text(
                        title or rule_source.get("title") or "Rulebook",
                        500,
                        fallback="Rulebook",
                    ),
                    "text": DirectorHelpProjector.citation_text(
                        chunk.get("text"),
                        _SOURCE_EVIDENCE_TEXT_LIMIT,
                    ),
                    "source_locator": DirectorHelpProjector.citation_locator(page_locator),
                    "source_refs": source_refs,
                    "source_refs_total_count": source_ref_total,
                    "source_refs_truncated": source_refs_truncated,
                }
            )
        source_total_is_known = (
            len(module_source_rows) <= _MODULE_EVIDENCE_LIMIT
            and len(rule_chunk_rows) <= _RULE_EVIDENCE_LIMIT
        )
        total_count_lower_bound = (
            len(ordered_contract_evidence) + len(module_source_rows) + len(rule_chunk_rows)
        )
        total_count = total_count_lower_bound if source_total_is_known else None
        truncated = (
            len(ordered_contract_evidence) > _CONTRACT_EVIDENCE_LIMIT
            or len(module_source_rows) > _MODULE_EVIDENCE_LIMIT
            or len(rule_chunk_rows) > _RULE_EVIDENCE_LIMIT
            or (total_count is not None and len(records) < total_count)
        )
        return _EvidenceCollection(
            records=tuple(records),
            total_count=total_count,
            total_count_lower_bound=max(len(records), total_count_lower_bound),
            truncated=truncated,
        )

    @staticmethod
    def _model_request(basis: _AdviceBasis) -> dict[str, Any]:
        return DirectorHelpProjector.model_request(
            contract=basis.authority.contract,
            brief=basis.brief,
            evidence=basis.evidence.records,
            evidence_total_count=basis.evidence.total_count,
            evidence_total_count_lower_bound=basis.evidence.total_count_lower_bound,
            evidence_truncated=basis.evidence.truncated,
            basis_hash=basis.basis_hash,
        )

    @staticmethod
    def _validated_output(
        raw_output: Any,
        basis: _AdviceBasis,
        model_request: dict[str, Any],
    ) -> DirectorHelpOutput:
        try:
            output = (
                raw_output
                if isinstance(raw_output, DirectorHelpOutput)
                else DirectorHelpOutput.model_validate(raw_output)
            )
        except (ValidationError, TypeError, ValueError):
            return DirectorHelpService._invalid_model_fallback()

        candidates = {candidate.candidate_id: candidate for candidate in basis.brief.candidates}
        offered_candidates = set(model_request["offered_candidate_ids"])
        selected = (
            candidates.get(output.candidate_id)
            if output.candidate_id in offered_candidates
            else None
        )
        candidate_skills = model_request["offered_candidate_skills"]
        candidate_evidence = {
            str(candidate["candidate_id"]): set(candidate["evidence_ids"])
            for candidate in model_request["director_brief"]["candidates"]
        }
        offered_evidence = set(model_request["offered_evidence_ids"])
        invalid = (
            (output.candidate_id is not None and output.candidate_id not in offered_candidates)
            or (
                output.requested_skill_key is not None
                and (
                    selected is None
                    or output.requested_skill_key
                    not in candidate_skills.get(output.candidate_id, ())
                )
            )
            or any(item not in offered_evidence for item in output.evidence_ids)
            or (
                selected is not None
                and not (
                    set(output.evidence_ids)
                    & candidate_evidence.get(output.candidate_id, set())
                    & offered_evidence
                )
            )
            or (output.status in {"answered", "partial"} and not output.evidence_ids)
            or (
                output.status in {"clarify", "no_evidence", "refused"}
                and (output.candidate_id is not None or output.requested_skill_key is not None)
            )
            or (output.status == "clarify" and output.follow_up_question is None)
            or (output.status != "clarify" and output.follow_up_question is not None)
        )
        return DirectorHelpService._invalid_model_fallback() if invalid else output

    @staticmethod
    def _verified_confidence(
        *,
        output: DirectorHelpOutput,
        selected: DirectorBriefCandidate | None,
        evidence_by_id: dict[str, dict[str, Any]],
        basis: _AdviceBasis,
        model_request: dict[str, Any],
    ) -> tuple[str, list[str]]:
        """Cap model confidence using deterministic provenance invariants."""

        if output.status in {"clarify", "no_evidence", "refused"}:
            return "low", []
        if output.confidence != "high":
            return output.confidence, []

        cited = [evidence_by_id[item] for item in output.evidence_ids]
        projected_brief = model_request["director_brief"]
        high_is_verified = (
            selected is not None
            and selected.available
            and not output.uncertainty_reasons
            and not output.assumptions
            and not basis.evidence.truncated
            and not projected_brief["evidence_truncated"]
            and bool(cited)
            and all(item["authority"] == "executable_contract" for item in cited)
        )
        if high_is_verified:
            return "high", []
        return "medium", [_CONFIDENCE_CAP_REASON]

    @staticmethod
    def _evidence_visibility(value: Any) -> str:
        normalized = str(value or "kp").strip().casefold()
        return normalized if normalized in _EVIDENCE_VISIBILITIES else "kp"

    @staticmethod
    def _invalid_model_fallback() -> DirectorHelpOutput:
        return DirectorHelpOutput(
            status="no_evidence",
            answer="当前建议未通过来源或规则约束校验，请换一种问法再试。",
            suggested_response="",
            candidate_id=None,
            requested_skill_key=None,
            next_steps=[],
            evidence_ids=[],
            confidence="low",
            uncertainty_reasons=["模型建议与当前权威候选或证据不一致。"],
            assumptions=[],
            follow_up_question=None,
        )

    @staticmethod
    def _basis_hash(
        authority: ScenarioAuthorityContext,
        brief: DirectorBrief,
        evidence: _EvidenceCollection,
    ) -> str:
        payload = {
            "authority": {
                "run_id": authority.run_id,
                "campaign_id": authority.campaign_id,
                "module_id": authority.module_id,
                "module_run_version": authority.module_run_version,
                "run_status": authority.run_status,
                "director_control_mode": authority.director_control_mode,
                "active_spoiler_tags": list(authority.active_spoiler_tags),
                "contract_version_id": authority.contract_version_id,
                "contract_hash": authority.contract_hash,
                "state_version": authority.state_version,
                "state_persisted": authority.state_persisted,
                "contract_id": authority.contract.contract_id,
                "scenario_version": authority.snapshot.scenario_version,
            },
            "director_brief_hash": brief.basis_hash,
            "evidence": {
                "records": list(evidence.records),
                "total_count": evidence.total_count,
                "total_count_lower_bound": evidence.total_count_lower_bound,
                "truncated": evidence.truncated,
            },
        }
        encoded = canonical_json_bytes(payload)
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _normalize_question(question: str) -> str:
        normalized = str(question or "").strip()
        if not normalized:
            raise InvalidInputError("Need Help question is required")
        if len(normalized) > _QUESTION_LIMIT:
            raise InvalidInputError("Need Help question is too long")
        return normalized


__all__ = ["DirectorHelpService"]

"""Source-bound action catalog shared by AI and human tabletop directors."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from ai_kp.platform.resolution.candidate_ranking import (
    MINIMUM_CANDIDATE_SCORE,
    SemanticCandidateRanker,
)
from ai_kp.platform.resolution.contracts import (
    ActionIntent,
    ScenarioContract,
    ScenarioSnapshot,
    SkillChoice,
    SourceRef,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel

ActionEvidenceKind = Literal[
    "location",
    "operator",
    "task_method",
    "clue",
    "response_obligation",
]


class ScenarioActionCatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScenarioActionEvidence(ScenarioActionCatalogModel):
    evidence_id: str
    kind: ActionEvidenceKind
    record_id: str
    title: str
    summary: str
    source_refs: tuple[SourceRef, ...] = ()


class ScenarioActionCandidate(ScenarioActionCatalogModel):
    candidate_id: str
    kind: Literal["operator", "task_method"]
    title: str
    intent_hints: tuple[str, ...] = ()
    score: int
    available: bool
    reason: str
    policy: str | None = None
    skill_choices: tuple[SkillChoice, ...] = ()
    automatic_information: tuple[str, ...] = ()
    maximum_effect: str = ""
    step_operator_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    @property
    def allowed_skill_keys(self) -> tuple[str, ...]:
        return tuple(choice.skill_key for choice in self.skill_choices)


class ScenarioActionCatalog(ScenarioActionCatalogModel):
    player_intent: str
    candidates: tuple[ScenarioActionCandidate, ...] = ()
    evidence: tuple[ScenarioActionEvidence, ...] = ()


class ScenarioActionCatalogProjector:
    """Build one deterministic candidate/evidence view without storage or models."""

    @classmethod
    def project(
        cls,
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
        player_intent: str,
        *,
        include_task_methods: bool,
        limit: int,
        minimum_score: int | None = MINIMUM_CANDIDATE_SCORE,
    ) -> ScenarioActionCatalog:
        normalized_intent = str(player_intent or "").strip()
        if not normalized_intent:
            raise ValueError("Player intent is required")
        if minimum_score is not None and minimum_score < 0:
            raise ValueError("Minimum candidate score must be non-negative")

        snapshot.validate_json_state()
        snapshot = ScenarioSnapshot.model_validate(snapshot.model_dump(mode="python"))
        kernel = ActionResolutionKernel.from_contract(contract)
        query = kernel.query_snapshot(snapshot)
        ranked = SemanticCandidateRanker().rank(
            contract,
            normalized_intent,
            snapshot=snapshot,
            include_task_methods=include_task_methods,
            limit=limit,
        )
        if minimum_score is not None:
            ranked = tuple(item for item in ranked if item.score >= minimum_score)

        evidence: dict[str, ScenarioActionEvidence] = {}
        cls._add_scene_evidence(evidence, contract, snapshot)
        operators = {item.operator_id: item for item in contract.operators}
        methods = {item.method_id: item for item in contract.task_methods}
        candidates: list[ScenarioActionCandidate] = []
        for ranked_item in ranked:
            if ranked_item.kind == "operator":
                operator = operators[ranked_item.candidate_id]
                try:
                    preview = kernel.preview(
                        snapshot,
                        ActionIntent(
                            action_id=f"action-catalog:{operator.operator_id}"[:160],
                            actor_id="action-catalog",
                            goal=normalized_intent[:1000],
                            operator_id=operator.operator_id,
                        ),
                    )
                    available = preview.allowed
                    reason = preview.reason
                except ValueError as exc:
                    available = False
                    reason = str(exc)
                candidates.append(
                    ScenarioActionCandidate(
                        candidate_id=operator.operator_id,
                        kind="operator",
                        title=operator.title,
                        intent_hints=operator.intent_hints,
                        score=ranked_item.score,
                        available=available,
                        reason=reason,
                        policy=operator.policy,
                        skill_choices=operator.skill_choices,
                        automatic_information=operator.automatic_information,
                        maximum_effect=operator.maximum_effect,
                        evidence_ids=cls._operator_evidence(
                            evidence,
                            contract,
                            operator.operator_id,
                        ),
                    )
                )
                continue

            method = methods[ranked_item.candidate_id]
            failed = tuple(
                condition.path
                for condition in method.preconditions
                if not query.condition_satisfied(condition)
            )
            available = ranked_item.available and snapshot.status == "active" and not failed
            reason = (
                "Task method preconditions satisfied"
                if available
                else (
                    "Preconditions not met: " + ", ".join(failed)
                    if failed
                    else f"Scenario run is {snapshot.status}"
                )
            )
            evidence_id = cls._add_evidence(
                evidence,
                kind="task_method",
                record_id=method.method_id,
                title=method.title,
                summary=(
                    f"task={method.task_key}; steps="
                    + ", ".join(step.operator_id for step in method.steps)
                ),
                source_refs=method.source_refs,
            )
            candidates.append(
                ScenarioActionCandidate(
                    candidate_id=method.method_id,
                    kind="task_method",
                    title=method.title,
                    intent_hints=method.intent_hints,
                    score=ranked_item.score,
                    available=available,
                    reason=reason,
                    step_operator_ids=tuple(step.operator_id for step in method.steps),
                    evidence_ids=(evidence_id,),
                )
            )
        return ScenarioActionCatalog(
            player_intent=normalized_intent,
            candidates=tuple(candidates),
            evidence=tuple(evidence.values()),
        )

    @classmethod
    def _add_scene_evidence(
        cls,
        evidence: dict[str, ScenarioActionEvidence],
        contract: ScenarioContract,
        snapshot: ScenarioSnapshot,
    ) -> None:
        location = next(
            (item for item in contract.locations if item.location_id == snapshot.scene_id),
            None,
        )
        if location is not None:
            cls._add_evidence(
                evidence,
                kind="location",
                record_id=location.location_id,
                title=location.title,
                summary=f"Current authoritative scene: {location.title}",
                source_refs=location.source_refs,
            )

    @classmethod
    def _operator_evidence(
        cls,
        evidence: dict[str, ScenarioActionEvidence],
        contract: ScenarioContract,
        operator_id: str,
    ) -> tuple[str, ...]:
        operator = next(item for item in contract.operators if item.operator_id == operator_id)
        evidence_ids = [
            cls._add_evidence(
                evidence,
                kind="operator",
                record_id=operator.operator_id,
                title=operator.title,
                summary="; ".join(
                    value
                    for value in (
                        operator.public_setup,
                        operator.rationale,
                        operator.maximum_effect,
                    )
                    if value
                )
                or f"policy={operator.policy}",
                source_refs=operator.source_refs,
            )
        ]
        for clue in contract.clues:
            if operator_id in clue.discovery_operator_ids:
                evidence_ids.append(
                    cls._add_evidence(
                        evidence,
                        kind="clue",
                        record_id=clue.clue_id,
                        title=clue.title,
                        summary="; ".join(clue.public_content) or f"fact={clue.fact_path}",
                        source_refs=clue.source_refs,
                    )
                )
        obligations = {item.obligation_id: item for item in contract.response_obligations}
        for obligation_id in operator.response_obligation_ids:
            obligation = obligations[obligation_id]
            evidence_ids.append(
                cls._add_evidence(
                    evidence,
                    kind="response_obligation",
                    record_id=obligation.obligation_id,
                    title=obligation.obligation_id,
                    summary="; ".join(
                        (
                            *obligation.facts_to_convey,
                            *obligation.state_to_express,
                            *obligation.physical_behaviors,
                            *obligation.automatic_information,
                        )
                    ),
                    source_refs=obligation.source_refs,
                )
            )
        return tuple(dict.fromkeys(evidence_ids))

    @staticmethod
    def _add_evidence(
        evidence: dict[str, ScenarioActionEvidence],
        *,
        kind: ActionEvidenceKind,
        record_id: str,
        title: str,
        summary: str,
        source_refs: tuple[SourceRef, ...],
    ) -> str:
        evidence_id = f"{kind}:{record_id}"
        evidence.setdefault(
            evidence_id,
            ScenarioActionEvidence(
                evidence_id=evidence_id,
                kind=kind,
                record_id=record_id,
                title=title,
                summary=summary,
                source_refs=source_refs,
            ),
        )
        return evidence_id


__all__ = [
    "ActionEvidenceKind",
    "ScenarioActionCandidate",
    "ScenarioActionCatalog",
    "ScenarioActionCatalogProjector",
    "ScenarioActionEvidence",
]

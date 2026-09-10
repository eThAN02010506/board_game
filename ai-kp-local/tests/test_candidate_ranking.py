from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import pytest

from ai_kp.platform.resolution.candidate_ranking import (
    LARGE_CANDIDATE_LIMIT,
    SMALL_CANDIDATE_LIMIT,
    SemanticCandidateRanker,
)
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.platform.resolution.semantic_adapter import ConstrainedSemanticAdapter
from tests.test_bounded_planning import delegation_payload


class SelectingLlm:
    def __init__(self, candidate_id: str, *, kind: str = "operator"):
        self.candidate_id = candidate_id
        self.kind = kind

    async def complete(self, messages, temperature: float = 0.7) -> str:
        return json.dumps(
            {
                "kind": self.kind,
                "candidate_id": self.candidate_id,
                "requested_skill_key": None,
                "confidence": "high",
                "clarification": None,
            }
        )


def large_contract(*, decoy_count: int = 48):
    payload = deepcopy(delegation_payload())
    payload["operators"] = [
        {
            "operator_id": f"decoy-{index:03d}",
            "title": f"Unrelated archival routine {index:03d}",
            "policy": "automatic",
        }
        for index in range(decoy_count)
    ] + payload["operators"]
    result = ScenarioContractCompiler().compile(payload)
    assert result.report.valid is True
    assert result.contract is not None
    return result.contract, result.report


def test_small_catalog_retrieves_a_late_chinese_match_before_model_selection() -> None:
    contract, _ = large_contract()
    snapshot = contract.initial_snapshot("rank-small")
    result = asyncio.run(
        ConstrainedSemanticAdapter(
            SelectingLlm("earn-funds"), profile="small"
        ).select(contract, "我先找临时工作筹钱", snapshot=snapshot)
    )

    assert 1 <= len(result.offered_candidates) <= SMALL_CANDIDATE_LIMIT
    assert result.offered_candidates[0].candidate_id == "earn-funds"
    assert "临时工作" in result.offered_candidates[0].intent_hints
    assert result.selection.candidate_id == "earn-funds"
    assert all(item.kind == "operator" for item in result.offered_candidates)


def test_large_catalog_retrieves_a_bounded_free_plan_beyond_the_raw_limit() -> None:
    contract, _ = large_contract()
    snapshot = contract.initial_snapshot("rank-large")
    result = asyncio.run(
        ConstrainedSemanticAdapter(
            SelectingLlm("fund-hire-delegate", kind="task_method"),
            profile="large",
        ).select(
            contract,
            "我先赚钱，再雇人替我调查",
            snapshot=snapshot,
        )
    )

    assert 1 <= len(result.offered_candidates) <= LARGE_CANDIDATE_LIMIT
    assert result.offered_candidates[0].candidate_id == "fund-hire-delegate"
    assert result.selection.kind == "task_method"


def test_ranking_marks_unmet_preconditions_without_hiding_the_relevant_action() -> None:
    contract, _ = large_contract(decoy_count=0)
    ranked = SemanticCandidateRanker().rank(
        contract,
        "我要雇佣代理去调查",
        snapshot=contract.initial_snapshot("rank-state"),
        include_task_methods=False,
        limit=SMALL_CANDIDATE_LIMIT,
    )

    hire = next(item for item in ranked if item.candidate_id == "hire-agent")
    assert hire.available is False
    assert ranked[0].candidate_id == "hire-agent"


def test_semantic_ranking_recognizes_canonical_travel_by_destination() -> None:
    compiled = ScenarioContractCompiler().compile({
        "contract_id": "rank-travel",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Rank travel",
        "initial_scene_id": "hall",
        "initial_facts": {"door_open": True},
        "locations": [
            {"location_id": "hall", "title": "Hall"},
            {"location_id": "archive", "title": "Archive"},
        ],
        "location_links": [{
            "from_location_id": "hall",
            "to_location_id": "archive",
            "one_way": True,
            "preconditions": [{
                "path": "facts.door_open", "operator": "eq", "value": True,
            }],
        }],
    })
    assert compiled.contract is not None
    ranked = SemanticCandidateRanker().rank(
        compiled.contract,
        "I go to the Archive",
        snapshot=compiled.contract.initial_snapshot("rank-travel"),
        include_task_methods=False,
        limit=SMALL_CANDIDATE_LIMIT,
    )

    assert ranked[0].candidate_id.startswith("travel-link-")
    assert ranked[0].available is True
    travel = next(
        item
        for item in compiled.contract.operators
        if item.operator_id == ranked[0].candidate_id
    )
    assert [item.path for item in travel.preconditions] == [
        "scene_id", "facts.door_open"
    ]


def test_large_catalog_without_explicit_hints_gets_a_compiler_warning() -> None:
    payload = delegation_payload()
    payload["operators"] = [
        {
            "operator_id": f"unhinted-{index:02d}",
            "title": f"Unhinted action {index:02d}",
            "policy": "automatic",
        }
        for index in range(SMALL_CANDIDATE_LIMIT + 1)
    ]
    payload["task_methods"] = []
    payload["endings"] = []
    result = ScenarioContractCompiler().compile(payload)

    assert result.report.valid is True
    assert any(
        item.code == "semantic_retrieval_hints_missing"
        and item.severity == "warning"
        for item in result.report.issues
    )

    combined = delegation_payload()
    combined["task_methods"] = [
        {
            "method_id": f"unhinted-method-{index:02d}",
            "task_key": f"task-{index:02d}",
            "title": f"Unhinted method {index:02d}",
            "steps": [
                {"step_id": "only-step", "operator_id": "earn-funds"}
            ],
        }
        for index in range(LARGE_CANDIDATE_LIMIT)
    ]
    combined_result = ScenarioContractCompiler().compile(combined)
    assert combined_result.report.valid is True
    assert any(
        item.code == "semantic_large_retrieval_hints_missing"
        for item in combined_result.report.issues
    )


def test_intent_hints_are_bounded_and_cannot_inflate_scores_with_duplicates() -> None:
    payload = delegation_payload()
    payload["operators"][0]["intent_hints"] = ["筹钱", "筹钱"]
    result = ScenarioContractCompiler().compile(payload)

    assert result.report.valid is False
    assert any("Duplicate operator intent hints" in item.message for item in result.report.issues)

    contract, _ = large_contract(decoy_count=0)
    with pytest.raises(ValueError, match="limit must be positive"):
        SemanticCandidateRanker().rank(
            contract,
            "任意行动",
            snapshot=None,
            include_task_methods=False,
            limit=0,
        )


def test_unrelated_catalog_is_empty_instead_of_forcing_a_low_score_match() -> None:
    contract, _ = large_contract(decoy_count=0)

    result = asyncio.run(
        ConstrainedSemanticAdapter(
            SelectingLlm("invented-outside-catalog"), profile="large"
        ).select(
            contract,
            "我声称是列车长失散的亲属，用话术骗取万能钥匙",
            snapshot=contract.initial_snapshot("unrelated-free-action"),
        )
    )

    assert result.offered_candidates == ()
    assert result.selection.kind == "clarification"


def test_common_npc_words_do_not_make_an_unrelated_clue_action_selectable() -> None:
    payload = delegation_payload()
    payload["operators"] = [{
        "operator_id": "comfort-conductor-clue",
        "title": "让乘务员恢复勇气，成功后主动告知线索",
        "policy": "automatic",
    }]
    payload["task_methods"] = []
    compiled = ScenarioContractCompiler().compile(payload)
    assert compiled.contract is not None

    result = asyncio.run(
        ConstrainedSemanticAdapter(
            SelectingLlm("comfort-conductor-clue"), profile="small"
        ).select(
            compiled.contract,
            "我声称是乘务员失散的亲属，想骗取万能钥匙",
            snapshot=compiled.contract.initial_snapshot("npc-word-collision"),
        )
    )

    assert result.offered_candidates == ()
    assert result.selection.kind == "clarification"


def test_explicit_chinese_hint_survives_pronouns_and_reordered_method_words() -> None:
    payload = delegation_payload()
    payload["operators"] = [{
        "operator_id": "move-barrier",
        "title": "推动木质屏障",
        "intent_hints": ["推动木门"],
        "policy": "automatic",
    }]
    payload["task_methods"] = []
    payload["endings"] = []
    compiled = ScenarioContractCompiler().compile(payload)
    assert compiled.contract is not None

    ranked = SemanticCandidateRanker().rank(
        compiled.contract,
        "我抓住那扇木门的边缘，把它向前推动。",
        snapshot=compiled.contract.initial_snapshot("reordered-hint"),
        include_task_methods=False,
        limit=SMALL_CANDIDATE_LIMIT,
    )

    assert ranked[0].candidate_id == "move-barrier"
    assert ranked[0].score >= 120


def test_two_common_action_words_do_not_intercept_a_multistep_detour() -> None:
    payload = delegation_payload()
    payload["operators"] = [{
        "operator_id": "comfort-conductor-clue",
        "title": "让乘务员恢复勇气，成功后主动告知调查线索",
        "policy": "automatic",
    }]
    payload["task_methods"] = []
    compiled = ScenarioContractCompiler().compile(payload)
    assert compiled.contract is not None

    result = asyncio.run(
        ConstrainedSemanticAdapter(
            SelectingLlm("comfort-conductor-clue"), profile="large"
        ).select(
            compiled.contract,
            "我去赌博筹钱，再雇佣乘务员替我调查列车前部。",
            snapshot=compiled.contract.initial_snapshot("multistep-collision"),
        )
    )

    assert result.offered_candidates == ()
    assert result.selection.kind == "clarification"

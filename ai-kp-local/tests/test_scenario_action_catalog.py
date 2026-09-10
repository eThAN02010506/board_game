from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ai_kp.application.kernel_action_service import KernelActionService
from ai_kp.platform.resolution.action_catalog import ScenarioActionCatalogProjector
from ai_kp.platform.resolution.director_brief import DirectorBriefProjector
from ai_kp.platform.resolution.semantic_adapter import ConstrainedSemanticAdapter
from tests.test_director_brief import _contract


class _CatalogRepo:
    def __init__(self):
        self.contract = _contract()
        self.snapshot = self.contract.initial_snapshot("catalog-run")
        self.run = {
            "id": "catalog-run",
            "campaign_id": "catalog-campaign",
            "module_id": "catalog-module",
            "version": 1,
            "status": "active",
            "director_control_mode": "human_kp",
            "active_spoiler_tags": [],
        }

    def get_active_campaign_module_run(self, campaign_id: str):
        assert campaign_id == self.run["campaign_id"]
        return dict(self.run)

    def get_campaign_module_run(self, run_id: str):
        assert run_id == self.run["id"]
        return dict(self.run)

    def get_module_run_contract_binding(self, run_id: str):
        assert run_id == self.run["id"]
        return {
            "run_id": run_id,
            "contract_version_id": "catalog-contract-version",
            "contract_hash": "a" * 64,
            "contract": self.contract,
        }

    def initialize_scenario_run_state(self, run_id: str):
        assert run_id == self.run["id"]
        return {
            "contract_version_id": "catalog-contract-version",
            "state_version": self.snapshot.run_version,
            "snapshot": self.snapshot,
        }


class _SelectSearchDeskLlm:
    async def complete(self, _messages, temperature: float = 0.7) -> str:
        return json.dumps(
            {
                "kind": "operator",
                "candidate_id": "search-desk",
                "requested_skill_key": "spot_hidden",
                "confidence": "high",
                "clarification": None,
            }
        )


def test_catalog_freezes_candidates_and_contract_evidence_for_all_directors() -> None:
    contract = _contract()
    catalog = ScenarioActionCatalogProjector.project(
        contract,
        contract.initial_snapshot("catalog-run"),
        "我搜索柜台寻找旅客簿",
        include_task_methods=True,
        limit=8,
    )

    candidate = next(item for item in catalog.candidates if item.candidate_id == "search-desk")
    assert candidate.available is True
    assert candidate.allowed_skill_keys == ("spot_hidden",)
    assert candidate.evidence_ids == (
        "operator:search-desk",
        "clue:guestbook",
    )
    assert {item.evidence_id for item in catalog.evidence} >= {
        "location:lobby",
        "operator:search-desk",
        "clue:guestbook",
    }
    assert all(item.source_refs for item in catalog.evidence)


def test_catalog_keeps_relevant_unavailable_action_with_kernel_reason() -> None:
    contract = _contract()
    catalog = ScenarioActionCatalogProjector.project(
        contract,
        contract.initial_snapshot("catalog-run"),
        "我要进入地下室",
        include_task_methods=False,
        limit=8,
    )

    candidate = next(item for item in catalog.candidates if item.candidate_id == "open-cellar")
    assert candidate.available is False
    assert "facts.door.unlocked" in candidate.reason


def test_manual_catalog_policy_can_include_unrelated_candidates_without_model_authority() -> None:
    contract = _contract()
    catalog = ScenarioActionCatalogProjector.project(
        contract,
        contract.initial_snapshot("catalog-run"),
        "完全无关的问题",
        include_task_methods=False,
        limit=len(contract.operators),
        minimum_score=None,
    )

    assert {item.candidate_id for item in catalog.candidates} == {
        "search-desk",
        "open-cellar",
    }


def test_ai_human_and_help_views_share_candidate_identity_and_skill_authority() -> None:
    repo = _CatalogRepo()
    intent = "我搜索柜台寻找旅客簿"
    catalog = ScenarioActionCatalogProjector.project(
        repo.contract,
        repo.snapshot,
        intent,
        include_task_methods=True,
        limit=8,
    )
    brief = DirectorBriefProjector.project(
        repo.contract,
        repo.snapshot,
        "a" * 64,
        intent,
    )
    ai = asyncio.run(
        ConstrainedSemanticAdapter(_SelectSearchDeskLlm(), profile="small").select(
            repo.contract,
            intent,
            snapshot=repo.snapshot,
        )
    )
    human = KernelActionService(repo).manual_catalog(
        {"campaign_id": "catalog-campaign", "action_text": intent}
    )

    shared = next(item for item in catalog.candidates if item.candidate_id == "search-desk")
    help_candidate = next(
        item for item in brief.candidates if item.candidate_id == shared.candidate_id
    )
    ai_candidate = next(
        item for item in ai.offered_candidates if item.candidate_id == shared.candidate_id
    )
    human_candidate = next(
        item for item in human if item["candidate_id"] == shared.candidate_id
    )
    assert ai_candidate.allowed_skill_keys == shared.allowed_skill_keys
    assert [item["skill_key"] for item in human_candidate["skill_choices"]] == list(
        shared.allowed_skill_keys
    )
    assert tuple(item.skill_key for item in help_candidate.skill_choices) == (
        shared.allowed_skill_keys
    )
    assert help_candidate.evidence_ids == shared.evidence_ids
    assert {item.evidence_id for item in brief.evidence} == {
        item.evidence_id for item in catalog.evidence
    }


def test_all_candidate_consumers_depend_on_the_shared_catalog_boundary() -> None:
    root = Path(__file__).parents[1]
    consumers = (
        root / "src/ai_kp/platform/resolution/semantic_adapter.py",
        root / "src/ai_kp/platform/resolution/director_brief.py",
        root / "src/ai_kp/application/kernel_action_service.py",
    )
    for consumer in consumers:
        assert "ScenarioActionCatalogProjector" in consumer.read_text(encoding="utf-8")

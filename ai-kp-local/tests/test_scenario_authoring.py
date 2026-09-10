from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ai_kp.application.errors import ConflictError
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.platform.modules.ingestion import ModuleChunk
from ai_kp.platform.resolution.check_catalog import check_term_occurs_in_text
from ai_kp.platform.resolution.contracts import EndingRule, ScenarioContract, StateCondition
from ai_kp.platform.resolution.evidence_compiler import (
    EvidenceBoundContractCandidate,
    EvidenceBoundScenarioCompiler,
)
from ai_kp.platform.resolution.kernel import operator_outcome_path
from ai_kp.platform.resolution.playability import ScenarioPlayabilityAnalyzer
from ai_kp.platform.resolution.scenario_authoring import (
    ConstrainedScenarioContractAuthoringAdapter,
    ScenarioAuthoringEvidence,
    ScenarioContractReview,
    ScenarioContractReviewIssue,
    _remove_contract_record_with_dependents,
    candidate_after_independent_review,
)
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.platform.resolution.scenario_ending_authority import (
    build_ending_catalogs,
    clear_ending_catalog_cache,
    merge_equivalent_endings,
)
from ai_kp.platform.resolution.scenario_ending_supplement import (
    SERVER_COVERAGE_ENDING_ASSUMPTION_PREFIX,
    CoverageEndingEnvelope,
    EndingOperatorCandidate,
    EndingStateCandidate,
    EndingStateProducer,
    deterministic_ending_envelope,
    materialize_ending_envelope,
)
from ai_kp.platform.resolution.scenario_ir_models import ScenarioIrBatch
from ai_kp.platform.resolution.scenario_supplement import (
    SERVER_COVERAGE_ACTION_ASSUMPTION_PREFIX,
)
from ai_kp.platform.resolution.scenario_terminal_method import (
    TerminalMethodEnvelope,
    build_terminal_entity_candidates,
    extract_source_terminal_methods,
    is_terminal_method_fact_command,
    materialize_terminal_method_envelope,
)
from ai_kp.platform.resolution.source_coverage import (
    SourceCoverageRequirement,
    SourceCoverageSupplementTarget,
)
from ai_kp.rulesets.coc7.scenario_checks import coc7_scenario_check_catalog
from ai_kp.rulesets.coc7.scenario_effects import coc7_scenario_effect_catalog


def authored_payload(
    *, document_id: str, block_id: str, page: int | None = 3,
    paragraph: int | None = 7,
) -> dict:
    return {
        "confidence": "high",
        "assumptions": [],
        "initial_scene_id": "waiting-room",
        "locations": [
            {
                "id": "waiting-room",
                "title": "候车室",
                "visibility": "visited",
                "source_block_ids": [block_id],
            }
        ],
        "actions": [
            {
                "id": "inspect-room",
                "title": "检查候车室",
                "intent_hints": ["检查候车室", "寻找遗失物"],
                "policy": "automatic",
                "location_slot": 0,
                "on_success": [
                    {
                        "kind": "set_fact",
                        "path": "waiting_room.inspected",
                        "value": True,
                    }
                ],
                "source_block_ids": [block_id],
            }
        ],
    }


class JsonLlm:
    def __init__(self, payloads: list[dict]):
        self.payloads = list(payloads)
        self.calls = 0

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls += 1
        return json.dumps(self.payloads.pop(0), ensure_ascii=False)


class CapturingJsonLlm(JsonLlm):
    def __init__(self, payloads: list[dict]):
        super().__init__(payloads)
        self.prompts: list[str] = []

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.prompts.append(messages[-1].content)
        return await super().complete(messages, temperature)


class BatchedRemovalLlm:
    def __init__(self):
        self.batch_sizes: list[int] = []

    async def complete(self, messages, temperature: float = 0.7) -> str:
        prompt = messages[-1].content
        targets = json.loads(
            prompt.split("当前目标记录：", 1)[1].split("\n可用证据：", 1)[0]
        )
        self.batch_sizes.append(len(targets))
        return json.dumps(
            {
                "repairs": [
                    {
                        "group": item["group"],
                        "record_id": item["record_id"],
                        "replacement": None,
                    }
                    for item in targets
                ]
            }
        )


class UnavailableLlm:
    async def complete(self, messages, temperature: float = 0.7) -> str:
        raise RuntimeError("model transport unavailable")


def evidence() -> ScenarioAuthoringEvidence:
    return ScenarioAuthoringEvidence(
        source_block_id="chunk-1",
        document_id="document-1",
        title="候车室",
        text="调查员在候车室可以检查座椅下方。",
        page=3,
        paragraph=7,
    )


def test_location_contraction_removes_condition_dependents_not_ordinary_facts() -> None:
    payload = ScenarioContract.model_validate(
        {
            "contract_id": "location-contraction",
            "source_version": 1,
            "ruleset_id": "generic",
            "title": "Location contraction",
            "initial_scene_id": "hall",
            "locations": [
                {"location_id": "hall", "title": "Hall"},
                {"location_id": "cellar", "title": "Cellar"},
            ],
            "operators": [
                {
                    "operator_id": "dead-in-cellar",
                    "title": "Dead in cellar",
                    "preconditions": [
                        {"path": "scene_id", "operator": "eq", "value": "cellar"}
                    ],
                    "policy": "automatic",
                    "success_commands": [
                        {"kind": "set_fact", "path": "searched", "value": True}
                    ],
                },
                {
                    "operator_id": "keep-story-fact",
                    "title": "Keep story fact",
                    "preconditions": [
                        {
                            "path": "facts.favorite_scene",
                            "operator": "eq",
                            "value": "cellar",
                        }
                    ],
                    "policy": "automatic",
                    "success_commands": [
                        {"kind": "set_fact", "path": "remembered", "value": True}
                    ],
                },
            ],
        }
    ).model_dump(mode="json")

    contracted = _remove_contract_record_with_dependents(
        payload, "locations", "cellar"
    )
    validated = ScenarioContract.model_validate(contracted)

    assert [item.location_id for item in validated.locations] == ["hall"]
    assert [item.operator_id for item in validated.operators] == ["keep-story-fact"]


def test_location_contraction_prunes_each_condition_container() -> None:
    location_condition = {
        "path": "scene_id",
        "operator": "eq",
        "value": "cellar",
    }
    payload = {
        "locations": [
            {"location_id": "hall"},
            {"location_id": "cellar"},
        ],
        "initial_scene_id": "hall",
        "location_links": [
            {"from_location_id": "hall", "to_location_id": "hall", "preconditions": [location_condition]}
        ],
        "entities": [],
        "operators": [
            {"operator_id": "keep", "response_obligation_ids": ["obligation"]}
        ],
        "task_methods": [
            {"method_id": "method", "preconditions": [location_condition]}
        ],
        "clues": [],
        "reactive_policies": [
            {
                "policy_id": "policy",
                "rules": [
                    {"rule_id": "rule", "conditions": [location_condition], "commands": []}
                ],
            }
        ],
        "response_obligations": [
            {"obligation_id": "obligation", "conditions": [location_condition]}
        ],
        "trigger_rules": [
            {"trigger_id": "trigger", "conditions": [location_condition], "commands": []}
        ],
        "consequence_signals": [
            {
                "signal_id": "signal",
                "bands": [
                    {"band_id": "band", "all_conditions": [location_condition]}
                ],
            }
        ],
        "pressure_tracks": [],
        "endings": [
            {"ending_id": "ending", "all_conditions": [location_condition], "commands": []}
        ],
    }

    contracted = _remove_contract_record_with_dependents(
        payload, "locations", "cellar"
    )

    assert contracted["location_links"] == []
    assert contracted["task_methods"] == []
    assert contracted["reactive_policies"] == []
    assert contracted["response_obligations"] == []
    assert contracted["operators"][0]["response_obligation_ids"] == []
    assert contracted["trigger_rules"] == []
    assert contracted["consequence_signals"] == []
    assert contracted["endings"] == []


def test_check_term_evidence_matching_preserves_ascii_boundaries() -> None:
    assert check_term_occurs_in_text("INT", "进行 INT 检定。")
    assert check_term_occurs_in_text("SAN", "SAN0/1d3")
    assert check_term_occurs_in_text("灵感检定", "这里需要灵感判定。")
    assert not check_term_occurs_in_text("INT", "The painting looks old.")
    catalog = coc7_scenario_check_catalog()
    assert catalog.resolve("幸运鉴定") == ("luck",)
    assert catalog.extract_source_terms("幸运鉴定或敏捷判定") == ("幸运", "敏捷")
    assert catalog.extract_source_terms("力量 60；进行侦查检定。") == ("侦查",)
    assert catalog.extract_source_terms("让玩家《幸运-1d20%》后鉴定") == ("幸运",)
    assert catalog.extract_source_terms("SAN0/1d3") == ("SAN",)
    assert [
        (item.term, item.difficulty)
        for item in catalog.extract_source_checks("进行困难侦查检定或极难幸运鉴定。")
    ] == [("幸运", "extreme"), ("侦查", "hard")]
    assert [
        item.term
        for item in catalog.extract_source_checks(
            "这是一段很长的场景介绍，用于说明环境、风险、行动顺序和玩家处境，"
            "在每个调查员下楼时进行【**敏捷**】检定或者【**攀爬**】技能检定。"
        )
    ] == ["敏捷", "攀爬"]


def test_evidence_block_revalidates_persisted_mechanical_obligations() -> None:
    stale_ending = SourceCoverageRequirement(
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        minimum_record_count=1,
        blocking=True,
        reason="old inference",
    )
    stale_checks = SourceCoverageRequirement(
        requirement_key="explicit_checks",
        acceptable_record_kinds=("operators",),
        minimum_record_count=2,
        blocking=True,
        reason="old inference",
    )
    source = evidence().model_copy(update={
        "semantic_kind": "ending",
        "classification_confidence": 0.9,
        "text": "这里只讨论收尾风格，不声明任何可执行终点。",
        "coverage_requirements": (stale_ending,),
    })
    assert source.evidence_block().coverage_requirements == ()

    alternatives = evidence().model_copy(update={
        "semantic_kind": "check",
        "classification_confidence": 0.9,
        "text": "进行【敏捷】检定或者【攀爬】技能检定。",
        "coverage_requirements": (stale_checks,),
    })
    refreshed = alternatives.evidence_block().coverage_requirements
    assert len(refreshed) == 1
    assert refreshed[0].minimum_record_count == 1

    stale_observation = SourceCoverageRequirement(
        requirement_key="explicit_terminal_observation",
        acceptable_record_kinds=("operators",),
        blocking=True,
        reason="old observation inference",
    )
    proposed_escape = evidence().model_copy(update={
        "semantic_kind": "ending",
        "classification_confidence": 0.9,
        "text": "If the investigators want to get away, discuss the ending.",
        "coverage_requirements": (stale_observation,),
    })
    assert proposed_escape.evidence_block().coverage_requirements == ()


def test_authoring_binds_server_authority_and_exact_evidence_catalog() -> None:
    llm = JsonLlm(
        [authored_payload(document_id="document-1", block_id="chunk-1")]
    )

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).author(
            (evidence(),),
            contract_id="module-server-bound",
            source_version=1,
            ruleset_id="coc7",
            title="真实模组名",
            corpus_truncated=False,
        )
    )

    assert result.candidate is not None
    contract = result.candidate.contract
    assert contract.contract_id == "module-server-bound"
    assert contract.source_version == 1
    assert contract.ruleset_id == "coc7"
    assert contract.title == "真实模组名"
    assert result.attempt_count == 1


def test_independent_review_resolves_medium_confidence_assumptions_explicitly() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["confidence"] = "medium"
    payload["assumptions"] = ["候车室描述需要独立核对"]
    payload["endings"] = [{
        "id": "room-inspected",
        "title": "检查完成",
        "all_conditions": [{
            "path": "facts.waiting_room.inspected",
            "operator": "eq",
            "value": True,
        }],
        "source_block_ids": ["chunk-1"],
    }]
    llm = CapturingJsonLlm([
        payload,
        {
            "decision": "approve",
            "findings": [],
            "supported_assumption_indices": [0],
        },
    ])
    adapter = ConstrainedScenarioContractAuthoringAdapter(llm)
    authored = asyncio.run(
        adapter.author(
            (evidence(),),
            contract_id="module-reviewed",
            source_version=1,
            ruleset_id="coc7",
            title="待审核模组",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None

    review = asyncio.run(adapter.review(authored.candidate, (evidence(),)))
    reviewed = candidate_after_independent_review(authored.candidate, review)

    assert review.assumptions_resolved is True
    assert reviewed.confidence == "high"
    assert reviewed.assumptions == ()
    assert "候车室描述需要独立核对" in llm.prompts[-1]
    assert EvidenceBoundScenarioCompiler().compile(reviewed).decision == (
        "auto_publishable"
    )


def test_independent_review_fails_closed_without_assumption_attestation() -> None:
    llm = JsonLlm([{
        "decision": "approve",
        "findings": [],
        "supported_assumption_indices": [],
    }])
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["assumptions"] = ["候车室描述需要证据确认"]
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([payload])
        ).author(
            (evidence(),),
            contract_id="module-unresolved",
            source_version=1,
            ruleset_id="coc7",
            title="待审核模组",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None

    review = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).review(
            authored.candidate,
            (evidence(),),
        )
    )

    assert review.decision == "reject"
    assert review.assumptions_resolved is False
    assert review.unsupported_assumption_indices == (0,)
    assert "directly supported" in review.findings[0]


def test_review_can_only_shrink_server_identified_inert_assumptions() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["assumptions"] = ["来源未说明每次移动固定耗时一小时", "保留的假设"]
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author(
            (evidence(),), contract_id="module-shrink", source_version=1,
            ruleset_id="coc7", title="收缩假设", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    review = ScenarioContractReview(
        decision="reject",
        findings=("No evidence supported assumption 0",),
        unsupported_assumption_indices=(0,),
    )

    shrunk = ConstrainedScenarioContractAuthoringAdapter(
        JsonLlm([])
    ).shrink_unsupported_assumptions(authored.candidate, review)

    assert shrunk is not None
    assert shrunk.contract == authored.candidate.contract
    assert "保留的假设" in shrunk.assumptions
    assert "来源未说明每次移动固定耗时一小时" not in shrunk.assumptions
    assert shrunk.assumptions[-1].startswith(
        "Unsupported authoring assumptions were removed"
    )


def test_review_does_not_shrink_assumptions_while_record_issue_is_open() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["assumptions"] = ["未证实假设"]
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author(
            (evidence(),), contract_id="module-no-shrink", source_version=1,
            ruleset_id="coc7", title="记录问题优先", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    review = ScenarioContractReview(
        decision="reject",
        findings=("记录仍有问题",),
        issues=(ScenarioContractReviewIssue(
            group="operators", record_id="inspect-room",
            problem="记录仍有问题", source_block_ids=("chunk-1",),
        ),),
        unsupported_assumption_indices=(0,),
    )

    assert ConstrainedScenarioContractAuthoringAdapter(
        JsonLlm([])
    ).shrink_unsupported_assumptions(authored.candidate, review) is None


def test_server_fail_forward_uses_one_existing_clock_without_inventing_state() -> None:
    payload = {
        "clocks": [{
            "clock_id": "dawn",
            "title": "黎明逼近",
            "source_refs": [{"source_block_id": "chunk-1"}],
        }],
        "operators": [{
            "operator_id": "search-room",
            "source_refs": [{"source_block_id": "chunk-1"}],
            "skill_choices": [{
                "skill_key": "coc7.spot_hidden",
                "allow_push": True,
                "failure_stakes": "",
                "pushed_failure_stakes": "",
            }],
            "failure_commands": [],
            "outcome_branches": [],
        }],
    }

    repaired, diagnostics = (
        ConstrainedScenarioContractAuthoringAdapter._apply_bounded_fail_forward_pressure(
            payload
        )
    )

    operator = repaired["operators"][0]
    assert operator["failure_commands"] == [{
        "kind": "advance_clock", "clock_id": "dawn", "delta": 1,
    }]
    assert operator["outcome_branches"] == [{
        "outcome_key": "pushed_failure",
        "commands": [{
            "kind": "advance_clock", "clock_id": "dawn", "delta": 2,
        }],
    }]
    assert "黎明逼近" in operator["skill_choices"][0]["failure_stakes"]
    assert diagnostics == (
        "Server fail-forward pressure applied: search-room -> dawn",
    )


def test_server_fail_forward_keeps_ambiguous_pressure_fail_closed() -> None:
    payload = {
        "clocks": [
            {"clock_id": "dawn", "source_refs": []},
            {"clock_id": "danger", "source_refs": []},
        ],
        "operators": [{
            "operator_id": "search-room",
            "source_refs": [],
            "skill_choices": [{"skill_key": "coc7.spot_hidden", "allow_push": True}],
            "failure_commands": [],
            "outcome_branches": [],
        }],
    }

    unchanged, diagnostics = (
        ConstrainedScenarioContractAuthoringAdapter._apply_bounded_fail_forward_pressure(
            payload
        )
    )

    assert unchanged["operators"][0]["failure_commands"] == []
    assert diagnostics == ()


def test_server_fail_forward_selects_unique_clock_central_to_state_graph() -> None:
    payload = {
        "clocks": [
            {"clock_id": "deadline", "title": "Deadline", "source_refs": []},
            {"clock_id": "alarm", "title": "Alarm", "source_refs": []},
        ],
        "pressure_tracks": [{"clock_id": "deadline"}, {"clock_id": "alarm"}],
        "endings": [{
            "all_conditions": [{
                "path": "clocks.deadline", "operator": "gte", "value": 4,
            }],
            "any_conditions": [],
        }],
        "operators": [{
            "operator_id": "search-room",
            "source_refs": [],
            "skill_choices": [{"skill_key": "coc7.spot_hidden", "allow_push": True}],
            "failure_commands": [],
            "outcome_branches": [],
        }],
    }

    repaired, diagnostics = (
        ConstrainedScenarioContractAuthoringAdapter._apply_bounded_fail_forward_pressure(
            payload
        )
    )

    assert repaired["operators"][0]["failure_commands"][0]["clock_id"] == "deadline"
    assert diagnostics == (
        "Server fail-forward pressure applied: search-room -> deadline",
    )


def test_server_materializes_only_source_explicit_adjacent_location_links() -> None:
    payload = {
        "locations": [
            {"location_id": "harbor", "title": "港口"},
            {"location_id": "path", "title": "小路"},
            {"location_id": "tower", "title": "塔楼"},
        ],
        "location_links": [],
    }
    source = evidence().model_copy(update={"text": "路线：港口可到小路；小路—塔楼。"})

    linked, diagnostics = (
        ConstrainedScenarioContractAuthoringAdapter
        ._materialize_source_explicit_location_links(payload, (source,))
    )

    assert [
        (item["from_location_id"], item["to_location_id"])
        for item in linked["location_links"]
    ] == [("harbor", "path"), ("path", "tower")]
    assert len(diagnostics) == 2


def test_server_does_not_link_locations_from_plain_cooccurrence() -> None:
    payload = {
        "locations": [
            {"location_id": "harbor", "title": "港口"},
            {"location_id": "tower", "title": "塔楼"},
        ],
        "location_links": [],
    }
    source = evidence().model_copy(update={"text": "港口很繁忙。远处可以看见塔楼。"})

    unchanged, diagnostics = (
        ConstrainedScenarioContractAuthoringAdapter
        ._materialize_source_explicit_location_links(payload, (source,))
    )

    assert unchanged["location_links"] == []
    assert diagnostics == ()


def test_server_aligns_automatic_clue_content_with_authoritative_fact() -> None:
    source = evidence().model_copy(update={
        "text": "玩家无需检定即可知道这条核心信息。",
    })
    payload = {
        "clues": [{
            "clue_id": "core-clue",
            "fact_path": "case.core_found",
            "fact_value": True,
            "public_content": ["玩家无需检定即可知道这条核心信息。"],
            "discovery_operator_ids": ["investigate"],
            "source_refs": [source.source_ref().model_dump(mode="json")],
        }],
        "operators": [{
            "operator_id": "investigate",
            "automatic_information": ["玩家无需检定即可知道这条核心信息。"],
            "always_commands": [],
            "success_commands": [{
                "kind": "set_fact",
                "path": "case.core_found",
                "value": True,
            }],
            "failure_commands": [{
                "kind": "advance_clock",
                "clock_id": "pressure",
                "delta": 1,
            }],
            "outcome_branches": [{
                "outcome_key": "pushed_failure",
                "commands": [{
                    "kind": "set_fact",
                    "path": "case.core_found",
                    "value": True,
                }],
            }],
        }],
    }

    aligned, diagnostics = (
        ConstrainedScenarioContractAuthoringAdapter._align_automatic_clue_state(
            payload, (source,)
        )
    )
    operator = aligned["operators"][0]

    assert operator["always_commands"][0]["path"] == "case.core_found"
    assert operator["success_commands"] == []
    assert operator["failure_commands"][0]["kind"] == "advance_clock"
    assert operator["outcome_branches"][0]["commands"][0]["kind"] == "advance_clock"
    assert diagnostics == (
        "Server source-authorized automatic clue aligned: investigate -> core-clue",
    )


def test_server_keeps_conditional_clue_on_its_outcome_and_adds_public_cue() -> None:
    source = evidence().model_copy(update={
        "text": "检定成功时发现湿脚印；失败时巡逻员会交付值班记录。",
    })
    ref = source.source_ref().model_dump(mode="json")
    payload = {
        "clues": [{
            "clue_id": "prints",
            "fact_path": "case.prints_found",
            "fact_value": True,
            "public_content": ["发现湿脚印"],
            "discovery_operator_ids": ["investigate"],
            "source_refs": [ref],
        }, {
            "clue_id": "log",
            "fact_path": "case.log_found",
            "fact_value": True,
            "public_content": ["巡逻员会交付值班记录"],
            "discovery_operator_ids": ["investigate"],
            "source_refs": [ref],
        }],
        "operators": [{
            "operator_id": "investigate",
            "title": "调查现场",
            "public_setup": "",
            "narrative_cues": [],
            "automatic_information": ["发现湿脚印", "巡逻员会交付值班记录"],
            "always_commands": [],
            "success_commands": [{
                "kind": "set_fact", "path": "case.prints_found", "value": True,
            }],
            "failure_commands": [{
                "kind": "set_fact", "path": "case.log_found", "value": True,
            }, {
                "kind": "apply_ruleset_effect", "event_type": "damage",
                "payload": {"damage": "1"},
            }],
            "outcome_branches": [{
                "outcome_key": "pushed_failure",
                "commands": [{
                    "kind": "advance_clock", "clock_id": "danger", "delta": 1,
                }, {
                    "kind": "apply_ruleset_effect", "event_type": "damage",
                    "payload": {"damage": "1d4"},
                }],
            }],
        }],
    }

    aligned, diagnostics = (
        ConstrainedScenarioContractAuthoringAdapter._align_automatic_clue_state(
            payload, (source,)
        )
    )
    operator = aligned["operators"][0]

    assert operator["automatic_information"] == []
    assert operator["success_commands"][0]["path"] == "case.prints_found"
    assert operator["failure_commands"][0]["path"] == "case.log_found"
    pushed_commands = operator["outcome_branches"][0]["commands"]
    assert pushed_commands[0]["path"] == "case.log_found"
    assert pushed_commands[1]["kind"] == "advance_clock"
    assert pushed_commands[2]["payload"] == {"damage": "1d4"}
    assert {"damage": "1"} not in [
        command.get("payload") for command in pushed_commands
    ]
    assert {
        cue["outcome_key"]: cue["public_summary"]
        for cue in operator["narrative_cues"]
    } == {
        "success": "发现湿脚印",
        "failure": "巡逻员会交付值班记录",
        "pushed_failure": "巡逻员会交付值班记录",
    }
    assert len(diagnostics) == 2


def test_independent_review_cannot_revoke_server_fail_forward_policy() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    adapter = ConstrainedScenarioContractAuthoringAdapter(
        JsonLlm([payload]), check_catalog=coc7_scenario_check_catalog()
    )
    authored = asyncio.run(adapter.author(
        (evidence(),), contract_id="server-fail-forward-review", source_version=1,
        ruleset_id="coc7", title="服务器失败代价", corpus_truncated=False,
    ))
    assert authored.candidate is not None
    contract_payload = authored.candidate.contract.model_dump(mode="json")
    contract_payload["clocks"] = [{
        "clock_id": "dawn", "title": "黎明逼近", "clock_kind": "hard",
        "initial_value": 0, "maximum_value": 4,
        "source_refs": [evidence().source_ref().model_dump(mode="json")],
    }]
    contract_payload["operators"][0].update({
        "policy": "required_check",
        "skill_choices": [{
            "skill_key": "coc7.spot_hidden", "difficulty": "regular",
            "reason": "检查候车室", "hidden": False,
        }],
    })
    candidate = adapter.reconcile_candidate_authority(
        authored.candidate.model_copy(update={
            "contract": ScenarioContract.model_validate(contract_payload)
        }),
        (evidence(),),
    )
    assert any(
        item.startswith("Server fail-forward pressure applied: inspect-room -> dawn")
        for item in candidate.assumptions
    )
    reviewer = ConstrainedScenarioContractAuthoringAdapter(JsonLlm([{
        "decision": "reject",
        "findings": ["inspect-room 的 pushed_failure 推进时钟不受来源支持"],
        "issues": [{
            "group": "operators", "record_id": "inspect-room",
            "problem": "pushed_failure 推进时钟不受来源支持",
            "source_block_ids": ["chunk-1"],
        }],
        "supported_assumption_indices": [],
    }]), check_catalog=coc7_scenario_check_catalog())

    review = asyncio.run(reviewer.review(candidate, (evidence(),)))

    assert review.decision == "approve"
    assert review.findings == ()


def test_review_cannot_restore_an_unknown_entity_location_removed_by_kernel() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),),
            contract_id="kernel-owned-location-contraction",
            source_version=1,
            ruleset_id="coc7",
            title="内核收缩地点",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    payload = authored.candidate.contract.model_dump(mode="json")
    payload["entities"].append({
        "entity_id": "entity-witness",
        "entity_type": "npc",
        "title": "目击者",
        "initial_location_id": None,
        "source_refs": [evidence().source_ref().model_dump(mode="json")],
    })
    candidate = authored.candidate.model_copy(update={
        "contract": type(authored.candidate.contract).model_validate(payload),
        "assumptions": (
            "Unknown initial location was removed from entity entity-witness: missing-room",
            "Action inspect-room proposed unknown or ambiguous check key: perception",
            (
                "Command set_entity_status with an unknown or forbidden target "
                "was discarded from inspect-room."
            ),
        ),
    })
    reviewer = ConstrainedScenarioContractAuthoringAdapter(JsonLlm([{
        "decision": "reject",
        "findings": [
            "Entity entity-witness initial location removal not supported by evidence"
        ],
        "issues": [{
            "group": "entities",
            "record_id": "entity-witness",
            "problem": (
                "Entity entity-witness initial location removal not supported by evidence"
            ),
            "source_block_ids": ["chunk-1"],
        }],
        "supported_assumption_indices": [],
    }]))

    review = asyncio.run(reviewer.review(candidate, (evidence(),)))

    assert review.decision == "approve"
    assert review.assumptions_resolved is True
    assert review.issues == ()


def test_independent_review_retries_unexplained_rejection_then_approves() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="review-empty-reject", source_version=1,
            ruleset_id="coc7", title="无理由拒绝", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = JsonLlm([
        {"decision": "reject", "findings": [], "issues": [],
         "supported_assumption_indices": []},
        {"decision": "approve", "findings": [], "issues": [],
         "supported_assumption_indices": []},
    ])

    review = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
            authored.candidate, (evidence(),)
        )
    )

    assert reviewer.calls == 2
    assert review.decision == "approve"


def test_independent_review_bounds_verbose_rejection_without_approving_it() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="review-verbose", source_version=1,
            ruleset_id="coc7", title="冗长审核", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    problem = "inspect-room 的效果不受当前来源支持"
    reviewer = CapturingJsonLlm([{
        "decision": "reject",
        "findings": [problem] * 20,
        "issues": [{
            "group": "operators",
            "record_id": "inspect-room",
            "problem": problem,
            "source_block_ids": ["chunk-1"],
        }] * 20,
        "supported_assumption_indices": [],
    }])

    review = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
            authored.candidate, (evidence(),)
        )
    )

    assert reviewer.calls == 1
    assert review.decision == "reject"
    assert [(item.group, item.record_id) for item in review.issues] == [
        ("operators", "inspect-room")
    ]


def test_independent_review_bounds_verbose_issue_text_without_losing_anchor() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="review-long-problem", source_version=1,
            ruleset_id="coc7", title="冗长问题", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = CapturingJsonLlm([{
        "decision": "approve",
        "findings": [],
        "issues": [{
            "group": "operators",
            "record_id": "inspect-room",
            "problem": "unreachable_locations: " + ("location-id " * 120),
            "source_block_ids": ["chunk-1"] * 20,
        }],
        "supported_assumption_indices": [],
    }])

    review = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
            authored.candidate, (evidence(),)
        )
    )

    assert review.decision == "reject"
    assert [(item.group, item.record_id) for item in review.issues] == [
        ("operators", "inspect-room")
    ]
    assert len(review.issues[0].problem) == 800
    assert review.issues[0].source_block_ids == ("chunk-1",)


def test_server_preserves_source_grounded_compiler_unreachable_locations() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="unreachable-contract", source_version=1,
            ruleset_id="coc7", title="不可达地点收缩", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    payload = authored.candidate.contract.model_dump(mode="json")
    payload["locations"].append({
        "location_id": "rumored-place",
        "title": "只在传闻中出现的地点",
        "source_refs": [evidence().source_ref().model_dump(mode="json")],
    })
    payload["entities"].append({
        "entity_id": "rumored-witness",
        "entity_type": "npc",
        "title": "传闻中的证人",
        "initial_location_id": "rumored-place",
        "source_refs": [evidence().source_ref().model_dump(mode="json")],
    })
    payload["reactive_policies"] = [{
        "policy_id": "rumored-witness-arrival",
        "entity_id": "rumored-witness",
        "rules": [{
            "rule_id": "arrive-from-rumor",
            "trigger": "background_tick",
            "commands": [{
                "kind": "move_actor",
                "actor_id": "rumored-witness",
                "value": "rumored-place",
            }],
        }],
        "source_refs": [evidence().source_ref().model_dump(mode="json")],
    }]
    candidate = authored.candidate.model_copy(update={
        "contract": ScenarioContract.model_validate(payload)
    })
    issue = ScenarioContractReviewIssue(
        group="locations",
        record_id="rumored-place",
        problem=(
            "playability_scene_reachability_indeterminate: location rumored-place "
            "has no executable path from initial_scene_id"
        ),
    )
    review = ScenarioContractReview(
        decision="reject",
        review_kind="deterministic_compiler",
        findings=(issue.problem,),
        issues=(issue,),
    )

    contracted, remaining = (
        ConstrainedScenarioContractAuthoringAdapter
        .contract_compiler_proven_unreachable_locations(candidate, review)
    )

    assert {item.location_id for item in contracted.contract.locations} == {
        "waiting-room", "rumored-place"
    }
    assert remaining == review
    assert contracted.contract.entities[0].initial_location_id == "rumored-place"
    assert len(contracted.contract.reactive_policies) == 1
    assert contracted.assumptions == candidate.assumptions


def test_premature_ending_is_removed_before_scene_reachability_contraction() -> None:
    source = evidence()
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (source,), contract_id="premature-ending", source_version=1,
            ruleset_id="coc7", title="过早结局", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    ref = source.source_ref().model_dump(mode="json")
    payload = authored.candidate.contract.model_dump(mode="json")
    payload.update({
        "initial_facts": {"case_resolved": False},
        "locations": [
            {"location_id": "waiting-room", "title": "候车室", "source_refs": [ref]},
            {"location_id": "tower", "title": "灯塔", "source_refs": [ref]},
        ],
        "location_links": [{
            "from_location_id": "waiting-room",
            "to_location_id": "tower",
            "source_refs": [ref],
        }],
        "operators": [{
            "operator_id": "resolve-case",
            "title": "解决事件",
            "policy": "automatic",
            "preconditions": [{
                "path": "facts.case_resolved", "operator": "eq", "value": False,
            }],
            "success_commands": [{
                "kind": "set_fact", "path": "case_resolved", "value": True,
            }],
            "source_refs": [ref],
        }],
        "endings": [
            {
                "ending_id": "bad-initial",
                "title": "过早失败",
                "all_conditions": [{
                    "path": "facts.case_resolved", "operator": "eq", "value": False,
                }],
                "source_refs": [ref],
            },
            {
                "ending_id": "good-reachable",
                "title": "有效结局",
                "all_conditions": [{
                    "path": "facts.case_resolved", "operator": "eq", "value": True,
                }],
                "source_refs": [ref],
            },
        ],
    })
    candidate = authored.candidate.model_copy(update={
        "contract": ScenarioContract.model_validate(payload)
    })
    adapter = ConstrainedScenarioContractAuthoringAdapter(JsonLlm([]))
    initial_report = ScenarioContractCompiler().compile(candidate.contract).report
    review = adapter.review_compilation_failure(candidate, initial_report)

    assert review is not None
    assert ("endings", "bad-initial") in {
        (item.group, item.record_id) for item in review.issues
    }
    assert ("endings", "good-reachable") not in {
        (item.group, item.record_id) for item in review.issues
    }

    repaired = asyncio.run(adapter.repair_review_rejection(candidate, (source,), review))

    assert repaired is not None
    assert {item.location_id for item in repaired.contract.locations} == {
        "waiting-room", "tower"
    }
    assert [item.ending_id for item in repaired.contract.endings] == [
        "good-reachable"
    ]
    final_report = ScenarioContractCompiler().compile(repaired.contract).report
    assert final_report.playability.proof("scene_reachability").status == "passed"
    assert final_report.playability.proof("ending_reachability").status == "passed"


def test_independent_review_propagates_transport_failure_without_semantic_decision() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="review-transport", source_version=1,
            ruleset_id="coc7", title="传输失败", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None

    with pytest.raises(RuntimeError, match="model transport unavailable"):
        asyncio.run(
            ConstrainedScenarioContractAuthoringAdapter(UnavailableLlm()).review(
                authored.candidate, (evidence(),)
            )
        )


def test_independent_review_preserves_rejection_that_omits_attestation() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),),
            contract_id="module-rejected",
            source_version=1,
            ruleset_id="coc7",
            title="待审核模组",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = ConstrainedScenarioContractAuthoringAdapter(JsonLlm([{
        "decision": "reject",
        "findings": ["缺少可执行结局"],
        "issues": [{
            "group": "operators",
            "record_id": "inspect-room",
            "problem": "缺少可执行结局",
            "source_block_ids": ["chunk-1"],
        }],
    }]))

    review = asyncio.run(reviewer.review(authored.candidate, (evidence(),)))

    assert review.decision == "reject"
    assert review.findings == ("缺少可执行结局",)
    assert review.assumptions_resolved is False


def test_review_approve_ignores_unanchored_prose_note() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),),
            contract_id="module-approved-with-note",
            source_version=1,
            ruleset_id="coc7",
            title="审核备注模组",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = ConstrainedScenarioContractAuthoringAdapter(JsonLlm([{
        "decision": "approve",
        "findings": ["Operator 1 似乎缺少条件。"],
        "issues": [],
        "supported_assumption_indices": [],
    }]))

    review = asyncio.run(reviewer.review(authored.candidate, (evidence(),)))

    assert review.decision == "approve"
    assert review.findings == ()
    assert review.issues == ()


def test_review_cannot_attach_skill_claim_to_location_record() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),),
            contract_id="module-cross-type-review",
            source_version=1,
            ruleset_id="coc7",
            title="跨类型审核模组",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = ConstrainedScenarioContractAuthoringAdapter(JsonLlm([{
        "decision": "reject",
        "findings": ["地点技能键与来源不一致"],
        "issues": [{
            "group": "locations",
            "record_id": "waiting-room",
            "problem": "技能键 coc7.credit_rating 与来源不一致",
            "source_block_ids": ["chunk-1"],
        }],
        "supported_assumption_indices": [],
    }]))

    review = asyncio.run(reviewer.review(authored.candidate, (evidence(),)))

    assert review.decision == "approve"
    assert review.findings == ()
    assert review.issues == ()


def test_review_repair_is_record_addressed_and_may_safely_remove_bad_record() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),),
            contract_id="module-review-repair",
            source_version=1,
            ruleset_id="coc7",
            title="审核修复模组",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    review = ScenarioContractReview(
        decision="reject",
        findings=("检查动作宣称了来源没有给出的结果。",),
        issues=(
            ScenarioContractReviewIssue(
                group="operators",
                record_id="inspect-room",
                problem="来源只允许检查，没有支持成功后设置事实。",
                source_block_ids=("chunk-1",),
            ),
        ),
    )
    repair_llm = CapturingJsonLlm(
        [
            {
                "repairs": [
                    {
                        "group": "operators",
                        "record_id": "inspect-room",
                        "replacement": None,
                    }
                ]
            }
        ]
    )

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(repair_llm).repair_review_rejection(
            authored.candidate,
            (evidence(),),
            review,
        )
    )

    assert repaired is not None
    assert repaired.contract.operators == ()
    assert repaired.contract.locations == authored.candidate.contract.locations
    assert "inspect-room" in repair_llm.prompts[0]


def test_review_normalizes_a_weak_models_wrong_group_by_unique_record_id() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),),
            contract_id="module-review-group",
            source_version=1,
            ruleset_id="coc7",
            title="审核分组模组",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = JsonLlm(
        [
            {
                "decision": "reject",
                "findings": ["动作效果没有来源支持。"],
                "issues": [
                    {
                        "group": "clues",
                        "record_id": "inspect-room",
                        "problem": "动作效果没有来源支持。",
                        "source_block_ids": ["chunk-1"],
                    }
                ],
                "supported_assumption_indices": [],
            }
        ]
    )

    review = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
            authored.candidate, (evidence(),)
        )
    )

    assert review.issues[0].group == "operators"
    assert review.issues[0].record_id == "inspect-room"


def test_review_discards_claim_that_typed_operator_is_missing_existing_skill() -> None:
    first_aid_evidence = evidence().model_copy(
        update={"text": "调查员可以进行急救检定。"}
    )
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["actions"][0]["policy"] = "required_check"
    payload["actions"][0]["checks"] = [{
        "skill_key": "coc7.first_aid",
        "reason": "来源点名急救",
    }]
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author(
            (first_aid_evidence,), contract_id="typed-review", source_version=1,
            ruleset_id="coc7", title="权威字段审核", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = JsonLlm([{
        "decision": "reject",
        "findings": ["缺少急救检定"],
        "issues": [{
            "group": "operators", "record_id": "inspect-room",
            "problem": "缺少急救检定", "source_block_ids": ["chunk-1"],
        }],
        "supported_assumption_indices": [],
    }])

    review = asyncio.run(ConstrainedScenarioContractAuthoringAdapter(
        reviewer, check_catalog=coc7_scenario_check_catalog()
    ).review(authored.candidate, (first_aid_evidence,)))

    assert review.decision == "approve"
    assert review.issues == ()


def test_review_cannot_delete_server_owned_action_goal_boundary() -> None:
    source = evidence().model_copy(update={"text": "调查员进行侦查检定。"})
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["actions"][0].update({
        "policy": "required_check",
        "checks": [{
            "skill_key": "coc7.spot_hidden",
            "reason": "来源点名侦查",
        }],
        "on_failure": [],
    })
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([payload]), check_catalog=coc7_scenario_check_catalog()
        ).author(
            (source,), contract_id="goal-boundary-review", source_version=1,
            ruleset_id="coc7", title="服务器结果边界", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    assert any(
        item == "Server action-goal boundary applied: inspect-room"
        for item in authored.candidate.assumptions
    )
    reviewer = JsonLlm([{
        "decision": "reject",
        "findings": ["action_goals.inspect-room.achieved 来源未定义。"],
        "issues": [{
            "group": "operators",
            "record_id": "inspect-room",
            "problem": "action_goals.inspect-room.achieved 是来源未定义的失败事实。",
            "source_block_ids": ["chunk-1"],
        }],
        "supported_assumption_indices": [],
    }])

    review = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            reviewer, check_catalog=coc7_scenario_check_catalog()
        ).review(authored.candidate, (source,))
    )

    assert review.decision == "approve"
    assert review.issues == ()


def test_review_does_not_semantically_judge_server_owned_clue_fact_handle() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["clues"] = [{
        "id": "clue-room", "title": "座椅下方有东西", "importance": "supporting",
        "fact_path": "clues.discovered.opaque", "fact_value": True,
        "source_block_ids": ["chunk-1"],
    }]
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author(
            (evidence(),), contract_id="clue-review", source_version=1,
            ruleset_id="coc7", title="线索句柄审核", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = JsonLlm([{
        "decision": "reject", "findings": ["fact path not directly supported"],
        "issues": [{
            "group": "clues", "record_id": "clue-room",
            "problem": "fact path not directly supported", "source_block_ids": ["chunk-1"],
        }], "supported_assumption_indices": [],
    }])

    review = asyncio.run(ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
        authored.candidate, (evidence(),)
    ))

    assert review.decision == "approve"


def test_review_does_not_require_source_to_name_location_visibility_enum() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author(
            (evidence(),), contract_id="location-visibility", source_version=1,
            ruleset_id="coc7", title="位置可见性审核", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    location_id = authored.candidate.contract.locations[0].location_id
    reviewer = JsonLlm([{
        "decision": "reject", "findings": ["位置初始可见性 known 未由来源声明"],
        "issues": [{
            "group": "locations", "record_id": location_id,
            "problem": "位置初始可见性 known 未由来源声明",
            "source_block_ids": ["chunk-1"],
        }], "supported_assumption_indices": [],
    }])

    review = asyncio.run(ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
        authored.candidate, (evidence(),)
    ))

    assert review.decision == "approve"
    assert review.issues == ()


def test_review_discards_positive_clue_transport_claims_but_keeps_real_issues() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["clues"] = [{
        "id": "clue-room", "title": "座椅下方有东西", "importance": "core",
        "discovery_action_ids": ["inspect-room"],
        "fact_path": "clues.discovered.opaque", "fact_value": True,
        "source_block_ids": ["chunk-1"],
    }]
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author(
            (evidence(),), contract_id="positive-clue-review", source_version=1,
            ruleset_id="coc7", title="正面传输", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = JsonLlm([{
        "decision": "reject",
        "findings": [
            "事实值为 true，符合 clues.discovered 语义。",
            "core but no effect; source supports discovery",
        ],
        "issues": [
            {"group": "clues", "record_id": "clue-room",
             "problem": "事实值为 true，符合 clues.discovered 语义。",
             "source_block_ids": ["chunk-1"]},
            {"group": "clues", "record_id": "clue-room",
             "problem": "core but no effect; source supports discovery",
             "source_block_ids": ["chunk-1"]},
        ], "supported_assumption_indices": [],
    }])

    review = asyncio.run(ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
        authored.candidate, (evidence(),)
    ))

    assert review.decision == "approve"
    assert review.issues == ()


def test_review_retries_structured_envelope_field_fragments() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="transport-fragment", source_version=1,
            ruleset_id="coc7", title="传输碎片", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = JsonLlm([
        {
            "decision": "reject", "findings": ["issues':[{"], "issues": [],
            "supported_assumption_indices": [],
        },
        {
            "decision": "approve", "findings": [], "issues": [],
            "supported_assumption_indices": [],
        },
    ])

    review = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
            authored.candidate, (evidence(),)
        )
    )

    assert review.decision == "approve"
    assert reviewer.calls == 2


def test_review_recovers_exact_record_id_from_finding_without_structured_issue() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),),
            contract_id="module-review-finding-id",
            source_version=1,
            ruleset_id="coc7",
            title="审核定位模组",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = JsonLlm(
        [
            {
                "decision": "reject",
                "findings": ["Operator inspect-room has an unsupported effect.", "none"],
                "issues": [],
                "supported_assumption_indices": [],
            }
        ]
    )

    review = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
            authored.candidate, (evidence(),)
        )
    )

    assert [(item.group, item.record_id) for item in review.issues] == [
        ("operators", "inspect-room")
    ]


def test_review_repair_retries_invalid_json_once_with_validation_feedback() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),),
            contract_id="module-review-repair-retry",
            source_version=1,
            ruleset_id="coc7",
            title="审核修复重试模组",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    review = ScenarioContractReview(
        decision="reject",
        findings=("inspect-room 不受支持。",),
        issues=(
            ScenarioContractReviewIssue(
                group="operators",
                record_id="inspect-room",
                problem="不受支持",
                source_block_ids=("chunk-1",),
            ),
        ),
    )
    llm = CapturingJsonLlm(
        [
            {"wrong": []},
            {
                "repairs": [
                    {
                        "group": "operators",
                        "record_id": "inspect-room",
                        "replacement": None,
                    }
                ]
            },
        ]
    )

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            authored.candidate, (evidence(),), review
        )
    )

    assert repaired is not None
    assert repaired.contract.operators == ()
    assert llm.calls == 2
    assert "未通过服务器校验" in llm.prompts[1]


def test_review_repair_fails_closed_after_invalid_transport() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="repair-removal", source_version=1,
            ruleset_id="coc7", title="确定性删除", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    review = ScenarioContractReview(
        decision="reject", findings=("inspect-room 不受支持",),
        issues=(ScenarioContractReviewIssue(
            group="operators", record_id="inspect-room", problem="不受支持",
            source_block_ids=("invented-review-source",),
        ),),
    )
    llm = JsonLlm([{"invalid": True}, {"still_invalid": True}])

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            authored.candidate, (evidence(),), review
        )
    )

    assert repaired is None
    assert llm.calls == 2


def test_review_removal_contracts_direct_operator_dependents() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["clues"] = [{
        "id": "core-room", "title": "核心线索", "importance": "core",
        "discovery_action_ids": ["inspect-room"], "fact_path": "clues.core",
        "source_block_ids": ["chunk-1"],
    }]
    payload["endings"] = [{
        "id": "ending-room", "title": "完成",
        "all_conditions": [{
            "path": operator_outcome_path("inspect-room"),
            "operator": "eq", "value": "success",
        }], "source_block_ids": ["chunk-1"],
    }]
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author(
            (evidence(),), contract_id="dependent-removal", source_version=1,
            ruleset_id="coc7", title="依赖收缩", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    review = ScenarioContractReview(
        decision="reject", findings=("inspect-room 不受支持",),
        issues=(ScenarioContractReviewIssue(
            group="operators", record_id="inspect-room", problem="不受支持",
            source_block_ids=("chunk-1",),
        ),),
    )
    llm = JsonLlm([{"repairs": [{
        "group": "operators", "record_id": "inspect-room", "replacement": None,
    }]}])

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            authored.candidate, (evidence(),), review
        )
    )

    assert repaired is not None
    assert repaired.contract.operators == ()
    assert repaired.contract.clues == ()
    assert repaired.contract.endings == ()


def test_deterministic_repair_retries_preserve_source_grounded_checkpoint() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["locations"].append({
        "id": "source-scene", "title": "来源场景",
        "source_block_ids": ["chunk-1"],
    })
    payload["clues"] = [{
        "id": "source-clue", "title": "来源线索", "importance": "supporting",
        "discovery_action_ids": ["inspect-room"], "fact_path": "clues.source",
        "source_block_ids": ["chunk-1"],
    }]
    payload["endings"] = [{
        "id": "source-ending", "title": "来源结局",
        "all_conditions": [{
            "path": operator_outcome_path("inspect-room"),
            "operator": "eq", "value": "success",
        }],
        "source_block_ids": ["chunk-1"],
    }]
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author(
            (evidence(),), contract_id="checkpoint-no-contraction", source_version=1,
            ruleset_id="coc7", title="检查点不收缩", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    checkpoint = authored.candidate
    adapter = ConstrainedScenarioContractAuthoringAdapter(JsonLlm([
        {"repairs": [{
            "group": "locations", "record_id": "source-scene",
            "replacement": None,
        }]},
        {"repairs": [{
            "group": "clues", "record_id": "source-clue",
            "replacement": None,
        }]},
    ]))
    reviews = (
        ScenarioContractReview(
            decision="reject", review_kind="deterministic_compiler",
            findings=("source-scene is unreachable",),
            issues=(ScenarioContractReviewIssue(
                group="locations", record_id="source-scene",
                problem="playability_scene_reachability_failed: unreachable",
            ),),
        ),
        ScenarioContractReview(
            decision="reject", review_kind="deterministic_compiler",
            findings=("source-clue route is incomplete",),
            issues=(ScenarioContractReviewIssue(
                group="clues", record_id="source-clue",
                problem="playability_source_content_delivery_failed",
            ),),
        ),
        ScenarioContractReview(
            decision="reject", review_kind="deterministic_compiler",
            findings=("No ending condition is reachable",), issues=(),
        ),
    )

    for review in reviews:
        repaired = asyncio.run(
            adapter.repair_review_rejection(checkpoint, (evidence(),), review)
        )
        if repaired is not None:
            checkpoint = repaired
        assert checkpoint.model_dump(mode="json") == (
            authored.candidate.model_dump(mode="json")
        )


def test_review_multi_target_accepts_dependent_removed_by_earlier_target() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["endings"] = [{
        "id": "ending-room", "title": "完成",
        "all_conditions": [{
            "path": operator_outcome_path("inspect-room"),
            "operator": "eq", "value": "success",
        }], "source_block_ids": ["chunk-1"],
    }]
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author(
            (evidence(),), contract_id="multi-dependent-removal", source_version=1,
            ruleset_id="coc7", title="多目标依赖收缩", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    review = ScenarioContractReview(
        decision="reject", findings=("两个记录不受支持",),
        issues=(
            ScenarioContractReviewIssue(
                group="operators", record_id="inspect-room", problem="不受支持",
                source_block_ids=("chunk-1",),
            ),
            ScenarioContractReviewIssue(
                group="endings", record_id="ending-room", problem="不受支持",
                source_block_ids=("chunk-1",),
            ),
        ),
    )
    llm = JsonLlm([
        {"repairs": [{
            "group": "operators", "record_id": "inspect-room", "replacement": None,
        }]},
        {"repairs": [{
            "group": "endings", "record_id": "ending-room", "replacement": None,
        }]},
    ])

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            authored.candidate, (evidence(),), review
        )
    )

    assert repaired is not None
    assert repaired.contract.operators == ()
    assert repaired.contract.endings == ()


def test_review_may_narrow_ending_conditions_but_cannot_change_effects() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["initial_facts"] = {"deadline": 1}
    payload["endings"] = [{
        "id": "ending-room", "title": "原结局", "priority": 4,
        "all_conditions": [{
            "path": operator_outcome_path("inspect-room"),
            "operator": "eq", "value": "success",
        }],
        "commands": [{"kind": "set_fact", "path": "ending.reward", "value": True}],
        "source_block_ids": ["chunk-1"],
    }]
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author(
            (evidence(),), contract_id="ending-condition-repair", source_version=1,
            ruleset_id="coc7", title="结局修复", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    original = authored.candidate.contract.endings[0].model_dump(mode="json")
    replacement = {
        **original,
        "title": "修正结局",
        "all_conditions": [{"path": "facts.deadline", "operator": "eq", "value": 1}],
        "commands": [{"kind": "set_fact", "path": "ending.injected", "value": True}],
        "priority": 999,
    }
    review = ScenarioContractReview(
        decision="reject", findings=("条件不完整",),
        issues=(ScenarioContractReviewIssue(
            group="endings", record_id="ending-room", problem="条件不完整",
            source_block_ids=("chunk-1",),
        ),),
    )
    llm = JsonLlm([{"repairs": [{
        "group": "endings", "record_id": "ending-room", "replacement": replacement,
    }]}])

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            authored.candidate, (evidence(),), review
        )
    )

    assert repaired is not None
    ending = repaired.contract.endings[0]
    assert ending.title == "修正结局"
    assert ending.all_conditions[0].path == "facts.deadline"
    assert ending.commands == authored.candidate.contract.endings[0].commands
    assert ending.priority == 4


def test_authority_reconciliation_repairs_review_owned_fields_and_contracts_orphans() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")]),
            effect_catalog=coc7_scenario_effect_catalog(),
        ).author(
            (evidence(),), contract_id="authority-reconciliation", source_version=1,
            ruleset_id="coc7", title="权威重绑", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    payload = authored.candidate.contract.model_dump(mode="json")
    operator = payload["operators"][0]
    operator["policy"] = "required_check"
    operator["skill_choices"] = [{
        "skill_key": "san", "difficulty": "regular", "reason": "目睹恐怖",
        "hidden": False,
    }]
    operator["success_commands"] = [{
        "kind": "apply_ruleset_effect", "event_type": "san_loss",
        "payload": {"loss": "1/1d6"},
    }]
    operator["failure_commands"] = []
    operator["source_refs"] = [{
        "source_block_id": "chunk-1", "document_id": "model-corrupted",
        "page": 99, "paragraph": 999,
    }]
    payload["operators"].append({
        **operator,
        "operator_id": "unsupported-orphan",
        "source_refs": [],
    })
    payload["clues"] = [{
        "clue_id": "orphan-clue", "title": "孤立线索", "importance": "core",
        "discovery_operator_ids": ["unsupported-orphan"],
        "fact_path": "clues.orphan", "fact_value": True, "recoverable": True,
        "source_refs": [evidence().source_ref().model_dump(mode="json")],
    }]
    recovered = authored.candidate.model_copy(update={
        "contract": type(authored.candidate.contract).model_validate(payload)
    })
    adapter = ConstrainedScenarioContractAuthoringAdapter(
        JsonLlm([]), effect_catalog=coc7_scenario_effect_catalog()
    )

    reconciled = adapter.reconcile_candidate_authority(recovered, (evidence(),))

    assert [item.operator_id for item in reconciled.contract.operators] == [
        "inspect-room"
    ]
    assert reconciled.contract.clues == ()
    repaired_ref = reconciled.contract.operators[0].source_refs[0]
    assert repaired_ref == evidence().source_ref()
    assert reconciled.contract.operators[0].success_commands[0].payload == {"loss": "1"}
    assert reconciled.contract.operators[0].failure_commands[0].payload == {
        "loss": "1d6"
    }


def test_authority_reconciliation_deduplicates_identical_operators_and_relinks() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="duplicate-relink", source_version=1,
            ruleset_id="coc7", title="重复动作重连", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    payload = authored.candidate.contract.model_dump(mode="json")
    duplicate = {**payload["operators"][0], "operator_id": "duplicate-inspect"}
    payload["operators"].append(duplicate)
    payload["clues"] = [{
        "clue_id": "duplicate-clue", "title": "重复线索", "importance": "core",
        "discovery_operator_ids": ["duplicate-inspect"],
        "fact_path": "clues.duplicate", "fact_value": True, "recoverable": True,
        "source_refs": [evidence().source_ref().model_dump(mode="json")],
    }]
    payload["endings"] = [{
        "ending_id": "duplicate-ending", "title": "重复动作结局",
        "all_conditions": [{
            "path": operator_outcome_path("duplicate-inspect"),
            "operator": "eq", "value": "success",
        }],
        "source_refs": [evidence().source_ref().model_dump(mode="json")],
    }]
    candidate = authored.candidate.model_copy(update={
        "contract": type(authored.candidate.contract).model_validate(payload)
    })

    reconciled = ConstrainedScenarioContractAuthoringAdapter(
        JsonLlm([])
    ).reconcile_candidate_authority(candidate, (evidence(),))

    assert [item.operator_id for item in reconciled.contract.operators] == [
        "duplicate-inspect"
    ]
    assert reconciled.contract.clues[0].discovery_operator_ids == (
        "duplicate-inspect",
    )
    assert reconciled.contract.endings[0].all_conditions[0].path == (
        operator_outcome_path("duplicate-inspect")
    )


def test_authority_reconciliation_contracts_provenance_less_checkpoint_record() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="lost-provenance", source_version=1,
            ruleset_id="coc7", title="旧检查点收缩", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    payload = authored.candidate.contract.model_dump(mode="json")
    payload["entities"].append({
        "entity_id": "unsupported-entity", "entity_type": "other",
        "title": "没有来源的旧实体", "source_refs": [],
    })
    candidate = authored.candidate.model_copy(update={
        "contract": type(authored.candidate.contract).model_validate(payload)
    })

    reconciled = ConstrainedScenarioContractAuthoringAdapter(
        JsonLlm([])
    ).reconcile_candidate_authority(candidate, (evidence(),))

    assert reconciled.contract.entities == ()


def test_authority_reconciliation_contracts_review_created_document_location() -> None:
    base_evidence = evidence()
    handout_evidence = ScenarioAuthoringEvidence(
        source_block_id="handout-6",
        document_id="document-1",
        title="文字材料 6",
        text="文字材料 6",
        semantic_kind="heading",
        section_path=("场景 3: 中央图书馆", "文字材料 6"),
        scene_key="中央图书馆",
    )
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (base_evidence,),
            contract_id="review-created-document-location",
            source_version=1,
            ruleset_id="coc7",
            title="审核地点角色门禁",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    payload = authored.candidate.contract.model_dump(mode="json")
    payload["locations"].append({
        "location_id": "handout-as-location",
        "title": "文字材料 6",
        "source_refs": [handout_evidence.source_ref().model_dump(mode="json")],
    })
    candidate = authored.candidate.model_copy(update={
        "contract": ScenarioContract.model_validate(payload),
        "evidence_blocks": (
            base_evidence.evidence_block(),
            handout_evidence.evidence_block(),
        ),
    })

    reconciled = ConstrainedScenarioContractAuthoringAdapter(
        JsonLlm([])
    ).reconcile_candidate_authority(candidate, (base_evidence, handout_evidence))

    assert [item.location_id for item in reconciled.contract.locations] == [
        "waiting-room"
    ]
    assert any(
        item.startswith(
            "Source-unproven playable location contracted: handout-as-location"
        )
        for item in reconciled.assumptions
    )


def test_review_replacement_source_locator_is_rebound_by_server() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="review-source-rebind", source_version=1,
            ruleset_id="coc7", title="审核来源重绑", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    replacement = authored.candidate.contract.operators[0].model_dump(mode="json")
    replacement["source_refs"] = [{
        "source_block_id": "chunk-1", "document_id": "forged-document",
        "page": 88, "paragraph": 777,
    }]
    review = ScenarioContractReview(
        decision="reject", findings=("需要更正记录",),
        issues=(ScenarioContractReviewIssue(
            group="operators", record_id="inspect-room", problem="需要更正记录",
            source_block_ids=("chunk-1",),
        ),),
    )
    llm = JsonLlm([{"repairs": [{
        "group": "operators", "record_id": "inspect-room",
        "replacement": replacement,
    }]}])

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            authored.candidate, (evidence(),), review
        )
    )

    assert repaired is not None
    assert repaired.contract.operators[0].source_refs == (evidence().source_ref(),)


def test_review_replacement_cannot_erase_existing_provenance() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="review-source-inherit", source_version=1,
            ruleset_id="coc7", title="审核来源继承", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    replacement = authored.candidate.contract.operators[0].model_dump(mode="json")
    replacement["title"] = "检查座椅下方"
    replacement["source_refs"] = []
    review = ScenarioContractReview(
        decision="reject", findings=("标题不够准确",),
        issues=(ScenarioContractReviewIssue(
            group="operators", record_id="inspect-room", problem="标题不够准确",
            source_block_ids=("chunk-1",),
        ),),
    )
    llm = JsonLlm([{"repairs": [{
        "group": "operators", "record_id": "inspect-room",
        "replacement": replacement,
    }]}])

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            authored.candidate, (evidence(),), review
        )
    )

    assert repaired is not None
    assert repaired.contract.operators[0].source_refs == (evidence().source_ref(),)


def test_review_knows_automatic_operator_produces_success_outcome_without_commands() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="automatic-outcome", source_version=1,
            ruleset_id="coc7", title="自动结果", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = JsonLlm([{
        "decision": "reject", "findings": ["成功命令缺失"],
        "issues": [{
            "group": "operators", "record_id": "inspect-room",
            "problem": "成功命令缺失", "source_block_ids": ["chunk-1"],
        }], "supported_assumption_indices": [],
    }])

    review = asyncio.run(ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
        authored.candidate, (evidence(),)
    ))

    assert review.decision == "approve"


def test_review_discards_positive_statements_misplaced_as_issues() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="positive-review", source_version=1,
            ruleset_id="coc7", title="正向审核", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = JsonLlm([{
        "decision": "reject",
        "findings": [
            "inspect-room 的 policy 与来源一致，来源支持。",
            "inspect-room 的内容直接来源于证据，未发现来源不支持的内容。",
        ],
        "issues": [{
            "group": "operators", "record_id": "inspect-room",
            "problem": "operators 与来源一致，来源支持。",
            "source_block_ids": ["chunk-1"],
        }, {
            "group": "operators", "record_id": "inspect-room",
            "problem": "内容直接来源于证据，未发现来源不支持的内容。",
            "source_block_ids": ["chunk-1"],
        }],
        "supported_assumption_indices": [],
    }])

    review = asyncio.run(ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
        authored.candidate, (evidence(),)
    ))

    assert review.decision == "approve"
    assert review.findings == ()
    assert review.issues == ()


def test_review_keeps_negated_source_support_as_a_real_issue() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="negative-review", source_version=1,
            ruleset_id="coc7", title="负向审核", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = JsonLlm([{
        "decision": "reject",
        "findings": ["inspect-room 的命令不受来源支持。"],
        "issues": [{
            "group": "operators", "record_id": "inspect-room",
            "problem": "inspect-room 的命令不受来源支持。",
            "source_block_ids": ["chunk-1"],
        }],
        "supported_assumption_indices": [],
    }])

    review = asyncio.run(ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
        authored.candidate, (evidence(),)
    ))

    assert review.decision == "reject"
    assert review.issues[0].record_id == "inspect-room"


def test_review_cannot_remove_server_materialized_coverage_operator() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="protected-coverage", source_version=1,
            ruleset_id="coc7", title="服务器补全", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    candidate = authored.candidate.model_copy(update={
        "assumptions": (
            f"{SERVER_COVERAGE_ACTION_ASSUMPTION_PREFIX}inspect-room",
        )
    })
    reviewer = JsonLlm([{
        "decision": "reject", "findings": ["模型认为动作不受来源支持"],
        "issues": [{
            "group": "operators", "record_id": "inspect-room",
            "problem": "模型认为动作不受来源支持",
            "source_block_ids": ["chunk-1"],
        }], "supported_assumption_indices": [],
    }])

    review = asyncio.run(ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
        candidate, (evidence(),)
    ))

    assert review.decision == "approve"


def test_review_applies_server_authority_to_record_named_only_in_findings() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="protected-finding", source_version=1,
            ruleset_id="coc7", title="弱模型审核", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    candidate = authored.candidate.model_copy(update={
        "assumptions": (
            f"{SERVER_COVERAGE_ACTION_ASSUMPTION_PREFIX}inspect-room",
        )
    })
    reviewer = JsonLlm([{
        "decision": "reject",
        "findings": ["Operator inspect-room 的技能与标题不一致"],
        "issues": [],
        "supported_assumption_indices": [],
    }])

    review = asyncio.run(ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
        candidate, (evidence(),)
    ))

    assert review.decision == "approve"
    assert review.findings == ()


def test_review_cannot_overrule_reserved_supplement_operator_without_diagnostic() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["actions"][0]["id"] = "supp20_115_action_01"
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author(
            (evidence(),), contract_id="late-supplement", source_version=1,
            ruleset_id="coc7", title="补写命名空间", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = JsonLlm([{
        "decision": "reject",
        "findings": ["supp20_115_action_01 的 spot_hidden 不受来源支持"],
        "issues": [{
            "group": "operators",
            "record_id": "supp20_115_action_01",
            "problem": "supp20_115_action_01 的 spot_hidden 不受来源支持",
            "source_block_ids": ["chunk-1"],
        }],
        "supported_assumption_indices": [],
    }])

    review = asyncio.run(ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
        authored.candidate, (evidence(),)
    ))

    assert review.decision == "approve"


def test_review_recovers_unique_long_record_title_from_finding() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["actions"][0]["title"] = "Inspect the unusually detailed room"
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([payload])
        ).author(
            (evidence(),), contract_id="title-anchor", source_version=1,
            ruleset_id="coc7", title="标题锚点", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    candidate = authored.candidate.model_copy(update={
        "assumptions": (
            f"{SERVER_COVERAGE_ACTION_ASSUMPTION_PREFIX}inspect-room",
        )
    })
    operator = candidate.contract.operators[0]
    finding = f"{operator.title} 的 skill_key 与源文本不匹配"
    reviewer = JsonLlm([{
        "decision": "reject", "findings": [finding], "issues": [],
        "supported_assumption_indices": [],
    }])

    review = asyncio.run(ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
        candidate, (evidence(),)
    ))

    assert review.decision == "approve"


def test_review_repair_deduplicates_issues_and_batches_weak_model_context() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["actions"] = [
        {**payload["actions"][0], "id": f"inspect-{index}", "title": f"Inspect {index}"}
        for index in range(5)
    ]
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author(
            (evidence(),),
            contract_id="module-review-batches",
            source_version=1,
            ruleset_id="coc7",
            title="审核批次模组",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    issues = tuple(
        ScenarioContractReviewIssue(
            group="operators",
            record_id=f"inspect-{index}",
            problem="不受支持",
            source_block_ids=("chunk-1",),
        )
        for index in range(5)
    )
    review = ScenarioContractReview(
        decision="reject",
        findings=("记录不受支持。",),
        issues=(*issues, issues[0]),
    )
    llm = BatchedRemovalLlm()

    repaired = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).repair_review_rejection(
            authored.candidate, (evidence(),), review
        )
    )

    assert repaired is not None
    assert repaired.contract.operators == ()
    assert llm.batch_sizes == [1, 1, 1, 1, 1]


def test_review_repair_treats_unchanged_replacement_as_no_progress() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="unchanged-repair", source_version=1,
            ruleset_id="coc7", title="无效回声修复", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    original = authored.candidate.contract.operators[0].model_dump(mode="json")
    review = ScenarioContractReview(
        decision="reject",
        findings=("记录未解决问题",),
        issues=(ScenarioContractReviewIssue(
            group="operators", record_id="inspect-room",
            problem="记录未解决问题", source_block_ids=("chunk-1",),
        ),),
    )
    repairer = ConstrainedScenarioContractAuthoringAdapter(JsonLlm([{
        "repairs": [{
            "group": "operators",
            "record_id": "inspect-room",
            "replacement": original,
        }]
    }]))

    repaired = asyncio.run(repairer.repair_review_rejection(
        authored.candidate, (evidence(),), review
    ))

    assert repaired is None
    assert authored.candidate.contract.operators[0].model_dump(mode="json") == original


def test_review_retries_rejection_anchored_only_to_unknown_record() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),), contract_id="unknown-review-anchor", source_version=1,
            ruleset_id="coc7", title="审核幻觉锚点", corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = JsonLlm([
        {
            "decision": "reject",
            "findings": ["missing_operator_99 不受来源支持"],
            "issues": [],
            "supported_assumption_indices": [],
        },
        {
            "decision": "approve",
            "findings": [],
            "issues": [],
            "supported_assumption_indices": [],
        },
    ])

    review = asyncio.run(ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
        authored.candidate, (evidence(),)
    ))

    assert review.decision == "approve"


def test_independent_review_discards_inert_unknown_assumption_indices() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),),
            contract_id="module-review-index",
            source_version=1,
            ruleset_id="coc7",
            title="审核索引模组",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = JsonLlm([{
        "decision": "approve",
        "findings": [],
        "supported_assumption_indices": [0],
    }])

    review = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
            authored.candidate,
            (evidence(),),
        )
    )

    assert review.decision == "approve"
    assert review.assumptions_resolved is True


def test_independent_review_retries_json_field_leaked_into_findings() -> None:
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([authored_payload(document_id="document-1", block_id="chunk-1")])
        ).author(
            (evidence(),),
            contract_id="module-review-transport",
            source_version=1,
            ruleset_id="coc7",
            title="审核传输模组",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = JsonLlm([
        {
            "decision": "reject",
            "findings": ["supported_assumption_indices:[0,1] }"],
            "supported_assumption_indices": [],
        },
        {
            "decision": "approve",
            "findings": [],
            "supported_assumption_indices": [],
        },
    ])

    review = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
            authored.candidate,
            (evidence(),),
        )
    )

    assert reviewer.calls == 2
    assert review.decision == "approve"


def test_independent_review_explains_hashed_operator_outcome_paths() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["endings"] = [{
        "id": "ending-inspected",
        "title": "完成检查",
        "all_conditions": [{
            "path": operator_outcome_path("inspect-room"),
            "operator": "eq",
            "value": "success",
        }],
        "source_block_ids": ["chunk-1"],
    }]
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author(
            (evidence(),),
            contract_id="module-review-outcome-path",
            source_version=1,
            ruleset_id="coc7",
            title="审核事件路径模组",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    reviewer = CapturingJsonLlm([{
        "decision": "approve",
        "findings": [],
        "supported_assumption_indices": [],
    }])

    review = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
            authored.candidate,
            (evidence(),),
        )
    )

    assert review.decision == "approve"
    assert operator_outcome_path("inspect-room") in reviewer.prompts[0]
    assert '"operator_id": "inspect-room"' in reviewer.prompts[0]


def test_independent_review_aggregates_bounded_batches_and_assumption_support() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["confidence"] = "medium"
    payload["assumptions"] = ["末段证据支持这项假设"]
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author(
            (evidence(),),
            contract_id="module-batched-review",
            source_version=1,
            ruleset_id="coc7",
            title="分批审核模组",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    sources = tuple(
        evidence().model_copy(
            update={
                "source_block_id": f"chunk-{index + 1}",
                "text": (
                    "末段证据支持这项假设" if index == 16 else f"证据段落 {index + 1}"
                ),
            }
        )
        for index in range(17)
    )
    reviewer = CapturingJsonLlm([
        {
            "decision": "approve",
            "findings": [],
            "supported_assumption_indices": [],
        },
        {
            "decision": "approve",
            "findings": [],
            "supported_assumption_indices": [0],
        },
    ])

    review = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
            authored.candidate,
            sources,
        )
    )

    assert reviewer.calls == 2
    assert "审核分区：1/2" in reviewer.prompts[0]
    assert "审核分区：2/2" in reviewer.prompts[1]
    assert review.decision == "approve"
    assert review.assumptions_resolved is True


def test_authoring_repairs_invalid_contract_then_fails_closed() -> None:
    empty = {"confidence": "high", "assumptions": []}
    llm = JsonLlm([empty, empty, empty])

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).author(
            (evidence(),),
            contract_id="module-server-bound",
            source_version=1,
            ruleset_id="coc7",
            title="真实模组名",
            corpus_truncated=False,
        )
    )

    assert result.candidate is None
    assert result.attempt_count == 3
    assert len(result.validation_errors) == 3


def test_partition_repairs_only_a_schema_invalid_record() -> None:
    initial = authored_payload(document_id="document-1", block_id="chunk-1")
    initial["actions"].append({
        "id": "listen",
        "title": "倾听房间",
        "policy": "roll_magic",
        "source_block_ids": ["chunk-1"],
    })
    replacement = {
        "replacements": [{
            "group": "actions",
            "record_index": 1,
            "record": {
                "id": "listen",
                "title": "倾听房间",
                "policy": "automatic",
                "source_block_ids": ["chunk-1"],
            },
        }]
    }
    llm = CapturingJsonLlm([initial, replacement])

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=0,
        )
    )

    assert result.batch is not None
    assert [item.id for item in result.batch.actions] == ["inspect-room", "listen"]
    assert result.attempt_count == 2
    assert result.repair_diagnostics[0].group == "actions"
    assert result.repair_diagnostics[0].record_index == 1
    assert result.repair_diagnostics[0].status == "applied"
    assert "inspect-room" not in llm.prompts[1]


def test_partition_discards_a_record_when_its_only_citation_is_unknown() -> None:
    initial = authored_payload(document_id="document-1", block_id="chunk-1")
    initial["actions"][0]["source_block_ids"] = ["invented-source"]
    llm = CapturingJsonLlm([initial])

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=4,
        )
    )

    assert result.batch is not None
    assert result.batch.actions == ()
    assert result.attempt_count == 1
    assert llm.calls == 1


def test_partition_repairs_source_overflow_without_exposing_record_body() -> None:
    sources = tuple(
        evidence().model_copy(
            update={"source_block_id": f"chunk-{index}", "paragraph": index}
        )
        for index in range(1, 10)
    )
    initial = authored_payload(document_id="document-1", block_id="chunk-1")
    initial["actions"][0]["source_block_ids"] = [
        source.source_block_id for source in sources
    ]
    repair = {
        "replacements": [{
            "group": "actions",
            "record_index": 0,
            "source_block_ids": ["chunk-2", "chunk-7"],
        }]
    }
    llm = CapturingJsonLlm([initial, repair])

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).author_partition(
            sources,
            ruleset_id="coc7",
            partition_index=4,
        )
    )

    assert result.batch is not None
    action = result.batch.actions[0]
    assert action.id == "inspect-room"
    assert action.title == "检查候车室"
    assert action.source_block_ids == ("chunk-2", "chunk-7")
    assert '"maxItems":8' in llm.prompts[1]
    assert "不提供也不接受记录正文" in llm.prompts[1]


def test_partition_discards_overflow_record_after_unknown_citation_repair() -> None:
    sources = tuple(
        evidence().model_copy(
            update={"source_block_id": f"chunk-{index}", "paragraph": index}
        )
        for index in range(1, 10)
    )
    initial = authored_payload(document_id="document-1", block_id="chunk-1")
    initial["actions"][0]["source_block_ids"] = [
        source.source_block_id for source in sources
    ]
    invalid_repair = {
        "replacements": [{
            "group": "actions",
            "record_index": 0,
            "source_block_ids": ["invented-source"],
        }]
    }

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            CapturingJsonLlm([initial, invalid_repair])
        ).author_partition(
            sources,
            ruleset_id="coc7",
            partition_index=4,
            max_attempts=2,
        )
    )

    assert result.batch is not None
    assert result.batch.actions == ()
    assert result.repair_diagnostics[-1].status == "discarded"


def test_partition_rebinds_an_unambiguous_paragraph_source_alias() -> None:
    source = evidence().model_copy(
        update={"source_block_id": "chunk-real-250", "paragraph": 250}
    )
    initial = authored_payload(document_id="document-1", block_id="chunk_250")

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([initial])).author_partition(
            (source,),
            ruleset_id="coc7",
            partition_index=4,
        )
    )

    assert result.batch is not None
    assert result.batch.actions[0].source_block_ids == ("chunk-real-250",)
    assert result.attempt_count == 1
    assert "paragraph source aliases rebound" in result.batch.assumptions[0]


def test_partition_repairs_an_ending_before_cross_partition_assembly() -> None:
    initial = authored_payload(document_id="document-1", block_id="chunk-1")
    initial["endings"] = [{
        "id": "leave-station",
        "title": "离开车站",
        "source_block_ids": ["chunk-1"],
    }]
    replacement = {
        "replacements": [{
            "group": "endings",
            "record_index": 0,
            "record": {
                **initial["endings"][0],
                "all_conditions": [{
                    "path": "facts.departure_ready",
                    "operator": "eq",
                    "value": True,
                }],
            },
        }]
    }
    llm = CapturingJsonLlm([initial, replacement])

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=2,
        )
    )

    assert result.batch is not None
    assert result.batch.endings[0].all_conditions[0].path == "facts.departure_ready"
    assert result.repair_diagnostics[0].group == "endings"
    assert result.repair_diagnostics[0].record_index == 0
    assert result.repair_diagnostics[0].status == "applied"


def test_partition_discards_only_an_irreparable_record_after_bounded_repairs() -> None:
    initial = authored_payload(document_id="document-1", block_id="chunk-1")
    initial["actions"].append({
        "id": "misclassified-clue",
        "title": "乘务员勇气",
        "source_block_ids": ["chunk-1"],
    })
    invalid_replacement = {
        "replacements": [{
            "group": "actions",
            "record_index": 1,
            "record": {
                **initial["actions"][1],
                "policy": "supporting",
            },
        }]
    }
    llm = JsonLlm([initial, invalid_replacement, invalid_replacement])

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=3,
        )
    )

    assert result.batch is not None
    assert [item.id for item in result.batch.actions] == ["inspect-room"]
    assert result.attempt_count == 3
    assert result.repair_diagnostics[-1].status == "discarded"
    assert result.repair_diagnostics[-1].record_index == 1


def test_partition_contracts_invalid_effect_command_and_unknown_source_record() -> None:
    initial = authored_payload(document_id="document-1", block_id="chunk-1")
    initial["actions"][0]["on_success"].append({
        "kind": "apply_ruleset_effect",
        "event_type": "san_loss",
        "value": {"loss": "1d4"},
        "payload": {},
    })
    initial["actions"].append({
        "id": "unknown-source-action",
        "title": "错误来源记录",
        "policy": "automatic",
        "source_block_ids": ["invented-source"],
    })
    invalid_replacement = {
        "replacements": [{
            "group": "actions",
            "record_index": 1,
            "record": initial["actions"][1],
        }]
    }
    llm = JsonLlm([initial, invalid_replacement, invalid_replacement])

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            llm,
            effect_catalog=coc7_scenario_effect_catalog(),
        ).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=7,
        )
    )

    assert result.batch is not None
    assert [
        command.kind for command in result.batch.actions[0].on_success
    ] == ["set_fact"]
    assert [item.id for item in result.batch.actions] == ["inspect-room"]
    assert "contracted locally" in result.batch.assumptions[0]


def test_check_coverage_supplement_is_materialized_without_a_model_call() -> None:
    empty = {"confidence": "high"}
    supplement = {
        "confidence": "high",
        "actions": [{
            "title": "侦查旧车票",
        }],
    }
    llm = CapturingJsonLlm([empty, supplement])
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="explicit_checks",
        acceptable_record_kinds=("operators",),
        required_additional_count=1,
        reason="来源明确要求检定。",
    )
    check_source = evidence().model_copy(update={"text": "通过侦查检定发现旧车票。"})

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            llm,
            check_catalog=coc7_scenario_check_catalog(),
        ).author_partition(
            (check_source,),
            ruleset_id="coc7",
            partition_index=9,
            coverage_targets=(target,),
        )
    )

    assert result.batch is not None
    assert result.attempt_count == 1
    assert result.batch.actions[0].abstract_checks[0].term == "侦查"
    assert result.batch.actions[0].abstract_checks[0].difficulty == "regular"
    assert llm.calls == 0


def test_action_supplement_materializer_owns_identity_and_provenance() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="explicit_checks",
        acceptable_record_kinds=("operators",),
        required_additional_count=1,
        reason="来源明确要求检定。",
    )
    proposal = {
        "actions": [{
            "title": "侦查旧车票",
        }]
    }
    llm = CapturingJsonLlm([proposal])
    check_source = evidence().model_copy(update={"text": "通过侦查检定发现旧车票。"})

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            llm,
            check_catalog=coc7_scenario_check_catalog(),
        ).author_partition(
            (check_source,),
            ruleset_id="coc7",
            partition_index=9,
            coverage_targets=(target,),
            record_id_prefix="supp9_",
        )
    )

    assert result.batch is not None
    assert result.batch.actions[0].id == "supp9_action_01"
    assert result.batch.actions[0].source_block_ids == ("chunk-1",)
    assert result.validation_errors == ()
    assert llm.calls == 0


def test_action_supplement_truncates_only_retrieval_hint_for_long_source() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="explicit_checks",
        acceptable_record_kinds=("operators",),
        required_additional_count=1,
        reason="来源明确要求检定。",
    )
    long_text = "进行 SAN 检定，损失 1/1d6。" + "恐怖景象" * 60
    source = evidence().model_copy(update={"text": long_text})

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([]),
            check_catalog=coc7_scenario_check_catalog(),
        ).author_partition(
            (source,),
            ruleset_id="coc7",
            partition_index=11,
            coverage_targets=(target,),
            record_id_prefix="supp11_",
        )
    )

    assert result.batch is not None
    action = result.batch.actions[0]
    assert len(action.title) == 240
    assert len(action.intent_hints[0]) == 160


def test_check_only_materializer_never_reads_model_owned_effect_slots() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="explicit_checks",
        acceptable_record_kinds=("operators",),
        required_additional_count=1,
        reason="来源明确要求检定。",
    )
    proposal = {
        "actions": [{
            "title": "侦查旧车票",
            "effect_key": "san_loss",
            "success_payload": {"loss": "1"},
        }]
    }
    llm = CapturingJsonLlm([proposal])
    source = evidence().model_copy(update={"text": "通过侦查检定发现旧车票。"})

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            llm,
            check_catalog=coc7_scenario_check_catalog(),
            effect_catalog=coc7_scenario_effect_catalog(),
        ).author_partition(
            (source,),
            ruleset_id="coc7",
            partition_index=10,
            coverage_targets=(target,),
            record_id_prefix="supp10_",
        )
    )

    assert result.batch is not None
    action = result.batch.actions[0]
    assert action.always == ()
    assert action.on_success[0].kind == action.on_failure[0].kind == "set_fact"
    assert action.on_success[0].path == action.on_failure[0].path == (
        "action_goals.supp10_action_01.achieved"
    )
    assert action.on_success[0].value is True
    assert action.on_failure[0].value is False
    assert all(command.kind != "apply_ruleset_effect" for command in (
        *action.on_success,
        *action.on_failure,
    ))
    assert result.validation_errors == ()
    assert llm.calls == 0


def test_check_without_source_outcomes_gets_honest_goal_boundary_cross_layer() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="explicit_checks",
        acceptable_record_kinds=("operators",),
        required_additional_count=1,
        reason="来源明确要求检定。",
    )
    source = evidence().model_copy(
        update={"text": "进行信用评级检定以确定额外报酬。"}
    )
    adapter = ConstrainedScenarioContractAuthoringAdapter(
        JsonLlm([]),
        check_catalog=coc7_scenario_check_catalog(),
    )

    authored = asyncio.run(
        adapter.author_partition(
            (source,),
            ruleset_id="coc7",
            partition_index=41,
            coverage_targets=(target,),
            record_id_prefix="supp41_",
        )
    )

    assert authored.batch is not None
    action = authored.batch.actions[0]
    assert action.policy == "required_check"
    assert action.on_success[0].path == action.on_failure[0].path == (
        "action_goals.supp41_action_01.achieved"
    )
    assert (action.on_success[0].value, action.on_failure[0].value) == (True, False)
    assert "未规定更具体的奖励" in action.narrative_cues[0].public_summary
    assert "未规定其他普通失败后果" in (
        action.abstract_checks[0].failure_stakes
    )
    assembled = adapter.assemble(
        (source,),
        (authored.batch,),
        contract_id="round41-generic",
        source_version=1,
        ruleset_id="coc7",
        title="Generic reward check",
        corpus_truncated=False,
        attempt_count=authored.attempt_count,
    )
    assert assembled.candidate is not None
    operator = assembled.candidate.contract.operators[0]
    assert operator.success_commands[0].value is True
    assert operator.failure_commands[0].value is False
    assert operator.skill_choices[0].failure_stakes == (
        action.abstract_checks[0].failure_stakes
    )
    assert (
        ScenarioPlayabilityAnalyzer()
        .analyze(assembled.candidate.contract)
        .proof("branch_consequences")
        .status
        == "passed"
    )
    payload = assembled.candidate.contract.model_dump(mode="json")
    payload["operators"][0]["success_commands"].append({
        "kind": "set_fact",
        "path": "reward_terms_understood",
        "value": True,
    })
    payload["operators"][0]["automatic_information"] = [
        "你已理解额外报酬的计算方式。"
    ]
    payload["clues"] = [{
        "clue_id": "reward-terms",
        "title": "Reward terms",
        "importance": "core",
        "discovery_operator_ids": ["supp41_action_01"],
        "fact_path": "reward_terms_understood",
        "fact_value": True,
        "recoverable": True,
        "public_content": ["你已理解额外报酬的计算方式。"],
    }]
    clue_report = ScenarioPlayabilityAnalyzer().analyze(
        ScenarioContract.model_validate(payload)
    )
    assert clue_report.proof("core_clue_discoverability").status == "passed"
    assert clue_report.proof("failure_recovery").status == "passed"


def test_base_authored_check_gets_missing_goal_boundaries_without_regeneration() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["actions"][0].update({
        "policy": "required_check",
        "abstract_checks": [{
            "term": "灵感",
            "reason": "来源要求灵感检定",
        }],
        "on_success": [],
        "on_failure": [],
    })
    llm = CapturingJsonLlm([payload])

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).author_partition(
            (evidence().model_copy(update={"text": "进行灵感检定。"}),),
            ruleset_id="coc7",
            partition_index=42,
        )
    )

    assert result.batch is not None
    action = result.batch.actions[0]
    assert action.on_success[0].path == action.on_failure[0].path == (
        "action_goals.inspect-room.achieved"
    )
    assert (action.on_success[0].value, action.on_failure[0].value) == (True, False)
    assert "未规定其他普通失败后果" in action.abstract_checks[0].failure_stakes
    assert llm.calls == 1


def test_base_goal_boundary_never_overwrites_source_authored_branch() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["actions"][0].update({
        "policy": "required_check",
        "abstract_checks": [{
            "term": "侦查",
            "reason": "来源要求侦查检定",
        }],
        "on_failure": [],
    })

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author_partition(
            (evidence().model_copy(update={"text": "进行侦查检定。"}),),
            ruleset_id="coc7",
            partition_index=43,
        )
    )

    assert result.batch is not None
    action = result.batch.actions[0]
    assert len(action.on_success) == 1
    assert (
        action.on_success[0].kind,
        action.on_success[0].path,
        action.on_success[0].value,
    ) == ("set_fact", "waiting_room.inspected", True)
    assert action.on_failure[0].path == "action_goals.inspect-room.achieved"
    assert action.automatic_information == ()


def test_action_supplement_materializes_ruleset_outcome_pairs() -> None:
    targets = (
        SourceCoverageSupplementTarget(
            source_block_id="chunk-1",
            requirement_key="explicit_checks",
            acceptable_record_kinds=("operators",),
            required_additional_count=1,
            reason="来源明确要求理智检定。",
        ),
        SourceCoverageSupplementTarget(
            source_block_id="chunk-1",
            requirement_key="explicit_ruleset_effect",
            acceptable_record_kinds=("operators",),
            required_additional_count=1,
            reason="来源明确给出理智损失。",
        ),
    )
    proposal = {
        "confidence": "high",
        "actions": [{
            "title": "目睹尸体时进行 SAN 检定，损失 1/1d6",
        }],
    }
    san_source = evidence().model_copy(update={"text": "目睹尸体进行 SAN 检定，损失 1/1d6。"})

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([proposal]),
            check_catalog=coc7_scenario_check_catalog(),
            effect_catalog=coc7_scenario_effect_catalog(),
        ).author_partition(
            (san_source,),
            ruleset_id="coc7",
            partition_index=12,
            coverage_targets=targets,
            record_id_prefix="supp12_",
        )
    )

    assert result.batch is not None
    action = result.batch.actions[0]
    assert action.on_success[0].payload == {"loss": "1"}
    assert action.on_failure[0].payload == {"loss": "1d6"}
    assert action.id == "supp12_action_01"


def test_single_action_supplement_title_keeps_late_source_check_term() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="explicit_ruleset_effect",
        acceptable_record_kinds=("operators",),
        required_additional_count=1,
        reason="来源明确给出理智损失。",
    )
    source = evidence().model_copy(update={
        "text": (
            "A detailed description of the unsettling object follows. " * 8
            + "Ask for a SAN roll (0/1D4 loss)."
        ),
    })

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([]),
            check_catalog=coc7_scenario_check_catalog(),
            effect_catalog=coc7_scenario_effect_catalog(),
        ).author_partition(
            (source,),
            ruleset_id="coc7",
            partition_index=120,
            coverage_targets=(target,),
            record_id_prefix="supp120_",
        )
    )

    assert result.batch is not None
    assert "SAN" in result.batch.actions[0].title.upper()
    assert result.batch.actions[0].on_success[0].payload == {"loss": "0"}
    assert result.batch.actions[0].on_failure[0].payload == {"loss": "1d4"}


@pytest.mark.parametrize(
    ("source_text", "expected_damage"),
    (
        ("攻击造成 1D4 + 2 / 6 + 1D4 + 2", "1d4+2"),
        ("伤害 1D3 + 伤害加值(1D4) + 感染", "1d3+1d4"),
    ),
)
def test_action_supplement_recovers_one_catalog_normalized_damage(
    source_text: str,
    expected_damage: str,
) -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="explicit_ruleset_effect",
        acceptable_record_kinds=("operators",),
        required_additional_count=1,
        reason="来源明确给出伤害。",
    )

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([]),
            effect_catalog=coc7_scenario_effect_catalog(),
        ).author_partition(
            (evidence().model_copy(update={"text": source_text}),),
            ruleset_id="coc7",
            partition_index=44,
            coverage_targets=(target,),
            record_id_prefix="supp44_",
        )
    )

    assert result.batch is not None
    assert result.validation_errors == ()
    assert result.batch.actions[0].always[0].kind == "apply_ruleset_effect"
    assert result.batch.actions[0].always[0].event_type == "damage"
    assert result.batch.actions[0].always[0].payload == {
        "damage": expected_damage,
    }


def test_action_supplement_ignores_model_action_count_and_uses_target_count() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="explicit_checks",
        acceptable_record_kinds=("operators",),
        required_additional_count=1,
        reason="来源明确要求一项检定。",
    )
    overflow = {"actions": [{"title": "敏捷检定"}, {"title": "力量检定"}]}
    corrected = {"actions": [{"title": "敏捷检定"}]}
    source = evidence().model_copy(update={"text": "进行《敏捷》检定。"})

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([overflow, corrected]),
            check_catalog=coc7_scenario_check_catalog(),
        ).author_partition(
            (source,),
            ruleset_id="coc7",
            partition_index=13,
            coverage_targets=(target,),
            record_id_prefix="supp13_",
        )
    )

    assert result.batch is not None
    assert result.attempt_count == 1
    assert len(result.batch.actions) == 1
    assert result.validation_errors == ()


def test_action_supplement_keeps_source_local_alternative_checks_together() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="explicit_checks",
        acceptable_record_kinds=("operators",),
        required_additional_count=1,
        reason="来源明确要求一项检定。",
    )
    source = evidence().model_copy(
        update={"text": "敏捷对抗失败后，进入力量对抗。"}
    )

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([{"actions": [{"title": "力量对抗"}]}]),
            check_catalog=coc7_scenario_check_catalog(),
        ).author_partition(
            (source,),
            ruleset_id="coc7",
            partition_index=16,
            coverage_targets=(target,),
            record_id_prefix="supp16_",
        )
    )

    assert result.batch is not None
    assert tuple(
        item.term for item in result.batch.actions[0].abstract_checks
    ) == ("敏捷", "力量")


def test_action_supplement_rejects_source_without_a_catalog_match() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="explicit_checks",
        acceptable_record_kinds=("operators",),
        required_additional_count=1,
        reason="来源明确要求检定。",
    )
    unknown = {
        "actions": [{"title": "感应车厢"}],
    }
    source = evidence().model_copy(update={"text": "通过超感检定感应车厢。"})

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([unknown, unknown, unknown]),
            check_catalog=coc7_scenario_check_catalog(),
        ).author_partition(
            (source,),
            ruleset_id="coc7",
            partition_index=14,
            coverage_targets=(target,),
            record_id_prefix="supp14_",
        )
    )

    assert result.batch is None
    assert result.attempt_count == 1
    assert "no ruleset-resolvable check terms" in result.validation_errors[0]


def test_action_supplement_rejects_unprovable_repeated_check_count() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="explicit_checks",
        acceptable_record_kinds=("operators",),
        required_additional_count=2,
        reason="来源明确要求两项检定。",
    )
    action = {"title": "重复侦查"}
    duplicated = {"actions": [action, action]}
    source = evidence().model_copy(update={"text": "先后进行两次侦查检定。"})

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([duplicated, duplicated, duplicated]),
            check_catalog=coc7_scenario_check_catalog(),
        ).author_partition(
            (source,),
            ruleset_id="coc7",
            partition_index=15,
            coverage_targets=(target,),
            record_id_prefix="supp15_",
        )
    )

    assert result.batch is None
    assert "fewer distinct checks" in result.validation_errors[0]


def test_clue_supplement_builds_server_owned_discovery_route() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="clue_path",
        acceptable_record_kinds=("clues", "operators"),
        required_additional_count=1,
        reason="来源包含可发现线索。",
    )
    llm = CapturingJsonLlm([{
        "clues": [{
            "title": "报纸上的失踪报道",
            "public_content": "报道记载了失踪者的姓名、失踪日期与最后出现地点。",
        }]
    }])

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=19,
            coverage_targets=(target,),
            record_id_prefix="supp19_",
        )
    )

    assert result.batch is not None
    action = result.batch.actions[0]
    clue = result.batch.clues[0]
    assert action.id == "supp19_discover_01"
    assert action.policy == "automatic"
    assert action.always[0].path == clue.fact_path
    assert action.always[0].value is True
    assert clue.discovery_action_ids == (action.id,)
    assert clue.importance == "supporting"
    assert clue.public_content == (
        "报道记载了失踪者的姓名、失踪日期与最后出现地点。",
    )
    assert action.automatic_information == clue.public_content
    assert "fact_path" not in llm.prompts[0]


def test_ending_supplement_links_only_server_advertised_operator_outcomes() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="来源明确给出结局条件。",
    )
    source = evidence().model_copy(
        update={"text": "如果未能逃离追逐，则进入坏结局。"}
    )
    proposal = {
        "endings": [{
            "title": "坏结局",
            "any_of": [{"operator_id": "escape-chase", "outcome": "failure"}],
        }]
    }
    llm = CapturingJsonLlm([proposal])

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).author_partition(
            (source,),
            ruleset_id="coc7",
            partition_index=20,
            coverage_targets=(target,),
            record_id_prefix="supp20_",
            ending_candidates=(
                EndingOperatorCandidate(
                    operator_id="escape-chase",
                    title="逃离追逐",
                    allowed_outcomes=("success", "failure"),
                ),
            ),
        )
    )

    assert result.batch is not None
    ending = result.batch.endings[0]
    assert ending.id == "supp20_source_ending_01"
    assert ending.source_block_ids == ("chunk-1",)
    assert ending.all_conditions[0].path == operator_outcome_path("escape-chase")
    assert ending.all_conditions[0].value == "failure"
    assert llm.calls == 0
    expected_assumption = (
        "Deterministic source ending materialized by the server: "
        "supp20_source_ending_01"
    )
    assert result.batch.assumptions == (expected_assumption,)


def test_independent_review_does_not_reopen_server_materialized_ending() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    authored = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author(
            (evidence(),),
            contract_id="module-server-ending",
            source_version=1,
            ruleset_id="coc7",
            title="Server ending",
            corpus_truncated=False,
        )
    )
    assert authored.candidate is not None
    candidate = authored.candidate.model_copy(update={
        "assumptions": (
            f"{SERVER_COVERAGE_ENDING_ASSUMPTION_PREFIX}supp20_source_ending_01",
        ),
    })
    reviewer = CapturingJsonLlm([{
        "decision": "approve",
        "findings": [],
        "issues": [],
        "supported_assumption_indices": [],
    }])

    review = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(reviewer).review(
            candidate,
            (evidence(),),
        )
    )

    assert review.decision == "approve"
    assert review.assumptions_resolved is True
    assert review.unsupported_assumption_indices == ()
    assert "authoring_assumptions：{}" in reviewer.prompts[-1]


@pytest.mark.parametrize(
    ("source_text", "expected_outcome"),
    [
        ("如果恢复灯光，则进入好结局。", "success"),
        ("如果未能恢复灯光，则进入坏结局。", "failure"),
        ("If not restore light, the station is lost.", "failure"),
    ],
)
def test_deterministic_ending_matches_server_operator_aliases(
    source_text: str,
    expected_outcome: str,
) -> None:
    envelope = deterministic_ending_envelope(
        source_text,
        candidates=(
            EndingOperatorCandidate(
                operator_id="repair-lamp",
                title="修复灯室设备",
                aliases=("恢复灯光", "restore light"),
                allowed_outcomes=("success", "failure"),
            ),
        ),
    )

    assert envelope is not None
    assert envelope.endings[0].all_of[0].operator_id == "repair-lamp"
    assert envelope.endings[0].all_of[0].outcome == expected_outcome


def test_deterministic_ending_prefers_longest_overlapping_server_alias() -> None:
    envelope = deterministic_ending_envelope(
        "若紧急恢复灯光，则所有人都能获救。",
        candidates=(
            EndingOperatorCandidate(
                operator_id="restore-light",
                title="恢复灯光",
                allowed_outcomes=("success", "failure"),
            ),
            EndingOperatorCandidate(
                operator_id="emergency-restore",
                title="启动应急修复",
                aliases=("紧急恢复灯光",),
                allowed_outcomes=("success", "failure"),
            ),
        ),
    )

    assert envelope is not None
    assert [item.operator_id for item in envelope.endings[0].all_of] == [
        "emergency-restore"
    ]


def test_deterministic_ending_does_not_match_partial_english_alias() -> None:
    envelope = deterministic_ending_envelope(
        "When the crew restore lighthouse power, the harbor reopens.",
        candidates=(
            EndingOperatorCandidate(
                operator_id="restore-light",
                title="恢复灯光",
                aliases=("restore light",),
                allowed_outcomes=("success", "failure"),
            ),
        ),
    )

    assert envelope is None


def test_ending_catalog_excludes_operator_without_a_reachable_outcome() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "fresh-ending-catalog",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Current merged contract",
        "initial_facts": {"ready": False},
        "operators": [{
            "operator_id": "resolve-current",
            "title": "Resolve current threat",
            "policy": "automatic",
            "preconditions": [{
                "path": "facts.ready", "operator": "eq", "value": False,
            }],
            "success_commands": [{
                "kind": "set_fact", "path": "ready", "value": True,
            }],
        }, {
            "operator_id": "stale-removed-producer",
            "title": "Use removed producer",
            "policy": "automatic",
            "preconditions": [{
                "path": "facts.never_produced", "operator": "eq", "value": True,
            }],
            "success_commands": [{
                "kind": "set_fact", "path": "stale_done", "value": True,
            }],
        }],
    })

    candidates = ConstrainedScenarioContractAuthoringAdapter.ending_operator_candidates(
        contract,
        (evidence().model_copy(update={
            "text": "If resolve current threat, the story ends."
        }),),
        (SourceCoverageSupplementTarget(
            source_block_id="chunk-1",
            requirement_key="ending_rule",
            acceptable_record_kinds=("endings",),
            required_additional_count=1,
            reason="source ending",
        ),),
    )

    assert [item.operator_id for item in candidates] == ["resolve-current"]


def test_ending_catalog_excludes_noncausal_and_ungated_automatic_outcomes() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "safe-ending-catalog",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Safe ending catalog",
        "initial_facts": {"ready": False},
        "operators": [{
            "operator_id": "terminal-prose",
            "title": "The antagonist reaches zero HP and turns to dust",
            "policy": "automatic",
        }, {
            "operator_id": "ungated-terminal-effect",
            "title": "Resolve the threat",
            "policy": "automatic",
            "success_commands": [{
                "kind": "set_fact", "path": "resolved", "value": True,
            }],
        }, {
            "operator_id": "gated-terminal-effect",
            "title": "Confirm the secured escape route",
            "policy": "automatic",
            "preconditions": [{
                "path": "facts.ready", "operator": "eq", "value": False,
            }],
            "success_commands": [{
                "kind": "set_fact", "path": "ready", "value": True,
            }],
        }],
    })

    candidates = build_ending_catalogs(
        contract,
        "When the threat is resolved and the escape route is secured, the story ends.",
    )[0]

    assert [item.operator_id for item in candidates] == ["gated-terminal-effect"]


def test_ending_catalog_excludes_weak_semantic_overlap() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "relevant-ending-catalog",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Relevant ending catalog",
        "operators": [{
            "operator_id": "unrelated-check",
            "title": "If the investigator persuades the editor",
            "policy": "required_check",
            "skill_choices": [{
                "skill_key": "coc7.persuade",
                "reason": "Persuade the editor.",
                "failure_stakes": "The editor refuses.",
            }],
            "success_commands": [{
                "kind": "set_fact", "path": "editor_helped", "value": True,
            }],
        }],
    })

    candidates = build_ending_catalogs(
        contract,
        "If the antagonist is defeated, the investigators receive their reward.",
    )[0]

    assert candidates == ()


def test_terminal_method_supplement_materializes_only_server_owned_entity_status() -> None:
    source_text = (
        "在礼拜堂中，若调查员成功夺取银刃并反过来用它刺中吸血鬼，"
        "则吸血鬼的躯体会立刻化为尘埃。"
    )
    evidence = (
        ScenarioAuthoringEvidence(
            source_block_id="terminal-source",
            document_id="doc",
            title="礼拜堂",
            text=source_text,
            coverage_requirements=(SourceCoverageRequirement(
                requirement_key="explicit_terminal_method",
                acceptable_record_kinds=("operators",),
                blocking=True,
                reason="terminal source",
            ),),
        ),
    )
    contract = ScenarioContract.model_validate({
        "contract_id": "terminal-method",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Terminal method",
        "initial_scene_id": "chapel",
        "locations": [{
            "location_id": "chapel",
            "title": "礼拜堂",
            "initial_visibility": "visited",
        }],
        "entities": [{
            "entity_id": "vampire",
            "entity_type": "creature",
            "title": "吸血鬼",
            "initial_status": "active",
            "initial_location_id": "chapel",
        }],
    })
    target = SourceCoverageSupplementTarget(
        source_block_id="terminal-source",
        requirement_key="explicit_terminal_method",
        acceptable_record_kinds=("operators",),
        required_additional_count=1,
        reason="terminal source",
    )
    llm = CapturingJsonLlm([{
        "selections": [{"source_method_slot": 0, "target_entity_slot": 0}],
    }])
    adapter = ConstrainedScenarioContractAuthoringAdapter(llm)
    entity_candidates = adapter.terminal_entity_candidates(
        contract, evidence, (target,)
    )

    authored = asyncio.run(adapter.author_partition(
        evidence,
        ruleset_id="coc7",
        partition_index=0,
        coverage_targets=(target,),
        record_id_prefix="supp_",
        terminal_entity_candidates=entity_candidates,
    ))

    assert authored.batch is not None
    assert llm.calls == 1
    action = authored.batch.actions[0]
    assert action.id == "supp_terminal_01"
    assert action.on_success[0].model_dump(mode="json") == {
        "kind": "set_entity_status",
        "path": None,
        "value": "destroyed",
        "entity_id": "vampire",
        "actor_id": None,
        "clock_id": None,
        "delta": None,
        "event_type": None,
        "payload": {},
    }
    assert '"commands"' not in llm.prompts[0]
    candidate = EvidenceBoundContractCandidate(
        contract=contract,
        confidence="high",
        evidence_blocks=tuple(item.evidence_block() for item in evidence),
    )
    merged = adapter.merge_supplement_batches(
        candidate, evidence, (authored.batch,)
    )
    assert merged.contract.operators, merged.assumptions
    operator = merged.contract.operators[0]
    assert operator.preconditions[0] == StateCondition(
        path="scene_id", operator="eq", value="chapel"
    )
    assert operator.success_commands[0].kind == "set_entity_status"
    coverage = EvidenceBoundScenarioCompiler().compile(merged).coverage
    terminal_item = next(
        item for item in coverage.items
        if item.requirement_key == "explicit_terminal_method"
    )
    assert terminal_item.covered is True


def test_terminal_method_materializer_rejects_entity_not_named_in_outcome() -> None:
    methods = extract_source_terminal_methods(
        "若玩家成功烧毁巫妖的命匣，则巫妖会立刻死亡。"
    )
    contract = ScenarioContract.model_validate({
        "contract_id": "wrong-terminal-target",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Wrong target",
        "locations": [{
            "location_id": "crypt", "title": "墓穴",
        }],
        "entities": [{
            "entity_id": "lich",
            "entity_type": "creature",
            "title": "巫妖",
            "initial_location_id": "crypt",
        }, {
            "entity_id": "guard",
            "entity_type": "npc",
            "title": "守卫",
            "initial_location_id": "crypt",
        }],
    })
    candidates = build_terminal_entity_candidates(contract, "巫妖与守卫都在场。")
    guard = next(item for item in candidates if item.entity_id == "guard")

    with pytest.raises(ValueError, match="not named"):
        materialize_terminal_method_envelope(
            TerminalMethodEnvelope.model_validate({
                "selections": [{
                    "source_method_slot": 0,
                    "target_entity_slot": guard.entity_slot,
                }],
            }),
            methods=methods,
            entity_candidates=candidates,
            source_block_id="terminal",
            record_id_prefix="supp_",
        )


def test_terminal_entity_catalog_accepts_only_unique_exact_title_token() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "terminal-alias",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Terminal alias",
        "locations": [{"location_id": "crypt", "title": "墓穴"}],
        "entities": [{
            "entity_id": "corbitt",
            "entity_type": "creature",
            "title": "沃尔特·科比特",
            "initial_location_id": "crypt",
        }, {
            "entity_id": "keeper",
            "entity_type": "npc",
            "title": "墓穴守卫",
            "initial_location_id": "crypt",
        }],
    })

    candidates = build_terminal_entity_candidates(
        contract, "若调查员刺中科比特，则科比特化为尘埃。"
    )

    assert len(candidates) == 1
    assert candidates[0].entity_id == "corbitt"
    assert candidates[0].model_dump(mode="json") == {
        "entity_slot": 0,
        "title": "沃尔特·科比特",
    }


def test_terminal_entity_catalog_rejects_ambiguous_exact_title_token() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "ambiguous-terminal-alias",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Ambiguous terminal alias",
        "locations": [{"location_id": "crypt", "title": "墓穴"}],
        "entities": [{
            "entity_id": "corbitt",
            "entity_type": "creature",
            "title": "沃尔特·科比特",
            "initial_location_id": "crypt",
        }, {
            "entity_id": "corbitt-corpse",
            "entity_type": "creature",
            "title": "科比特·尸体",
            "initial_location_id": "crypt",
        }],
    })

    assert build_terminal_entity_candidates(
        contract, "若调查员刺中科比特，则科比特化为尘埃。"
    ) == ()


def test_body_manifestation_terminal_method_is_scene_scoped_and_reaches_ending() -> None:
    terminal_text = (
        "在地下藏身处，若调查员成功夺取匕首并反过来刺中科比特，"
        "则科比特的躯体会立刻化为尘埃。"
    )
    terminal_evidence = ScenarioAuthoringEvidence(
        source_block_id="terminal-body-source",
        document_id="doc",
        title="地下藏身处",
        text=terminal_text,
    )
    ending_evidence = ScenarioAuthoringEvidence(
        source_block_id="body-ending-source",
        document_id="doc",
        title="结局",
        text="如果科比特的尸体被摧毁，调查员便进入胜利结局。",
    )
    evidence = (terminal_evidence, ending_evidence)
    contract = ScenarioContract.model_validate({
        "contract_id": "body-terminal-method",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Body terminal method",
        "initial_scene_id": "hideout",
        "locations": [{
            "location_id": "hideout",
            "title": "地下藏身处",
            "initial_visibility": "visited",
        }],
        "entities": [{
            "entity_id": "corbitt-corpse",
            "entity_type": "creature",
            "title": "科比特的尸体",
            "initial_status": "active",
            "initial_location_id": "hideout",
        }, {
            "entity_id": "corbitt-diary",
            "entity_type": "item",
            "title": "科比特的日记",
            "initial_location_id": "hideout",
        }, {
            "entity_id": "corbitt-dagger",
            "entity_type": "item",
            "title": "科比特·匕首",
            "initial_location_id": "hideout",
        }, {
            "entity_id": "corbitt-spirit",
            "entity_type": "creature",
            "title": "科比特（灵体）",
            "initial_location_id": "hideout",
        }],
    })

    candidates = build_terminal_entity_candidates(contract, terminal_text)

    assert [item.entity_id for item in candidates] == ["corbitt-corpse"]
    methods = extract_source_terminal_methods(terminal_text)
    terminal_batch = materialize_terminal_method_envelope(
        TerminalMethodEnvelope.model_validate({
            "selections": [{
                "source_method_slot": methods[0].source_slot,
                "target_entity_slot": candidates[0].entity_slot,
            }],
        }),
        methods=methods,
        entity_candidates=candidates,
        source_block_id=terminal_evidence.source_block_id,
        record_id_prefix="body_",
    )
    candidate = EvidenceBoundContractCandidate(
        contract=contract,
        confidence="high",
        evidence_blocks=tuple(item.evidence_block() for item in evidence),
    )
    adapter = ConstrainedScenarioContractAuthoringAdapter(JsonLlm([]))
    with_terminal = adapter.merge_supplement_batches(
        candidate, evidence, (terminal_batch,)
    )
    operator = with_terminal.contract.operators[0]
    assert operator.preconditions[0] == StateCondition(
        path="scene_id", operator="eq", value="hideout"
    )
    assert operator.success_commands[0].entity_id == "corbitt-corpse"
    assert operator.success_commands[0].value == "destroyed"

    ending_candidates, _ = build_ending_catalogs(
        with_terminal.contract, ending_evidence.text
    )
    producer = next(
        item for item in ending_candidates
        if item.operator_id == operator.operator_id
    )
    ending_batch = materialize_ending_envelope(
        CoverageEndingEnvelope.model_validate({
            "endings": [{
                "title": "摧毁科比特的尸体",
                "all_of": [{
                    "operator_id": producer.operator_id,
                    "outcome": "success",
                }],
            }],
        }),
        candidates=ending_candidates,
        source_block_id=ending_evidence.source_block_id,
        record_id_prefix="ending_",
    )
    final = adapter.merge_supplement_batches(
        with_terminal, evidence, (ending_batch,)
    )
    compilation = EvidenceBoundScenarioCompiler().compile(final)
    assert not any(
        "No ending condition is reachable" in issue.message
        for issue in compilation.report.issues
    )


def test_body_manifestation_terminal_catalog_rejects_ambiguity_and_fuzzy_names() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "ambiguous-body-terminal",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Ambiguous body terminal",
        "locations": [{"location_id": "hideout", "title": "地下藏身处"}],
        "entities": [{
            "entity_id": "corpse-a",
            "entity_type": "creature",
            "title": "科比特的尸体",
            "initial_location_id": "hideout",
        }, {
            "entity_id": "corpse-b",
            "entity_type": "creature",
            "title": "科比特（肉身）",
            "initial_location_id": "hideout",
        }, {
            "entity_id": "fuzzy-corpse",
            "entity_type": "creature",
            "title": "科比特森的尸体",
            "initial_location_id": "hideout",
        }],
    })
    source_text = (
        "若调查员成功用匕首刺中科比特，"
        "则科比特的身体会立刻化为尘埃。"
    )

    assert build_terminal_entity_candidates(contract, source_text) == ()
    fuzzy_only = contract.model_copy(update={
        "entities": tuple(
            entity
            for entity in contract.entities
            if entity.entity_id == "fuzzy-corpse"
        ),
    })
    assert build_terminal_entity_candidates(fuzzy_only, source_text) == ()


def test_ambiguous_terminal_entity_falls_back_to_reachable_source_method_fact() -> None:
    terminal_text = (
        "在礼拜堂中，若调查员成功夺取匕首并反过来刺中科比特，"
        "则科比特的躯体会立刻化为尘埃。"
    )
    terminal_requirement = SourceCoverageRequirement(
        requirement_key="explicit_terminal_method",
        acceptable_record_kinds=("operators",),
        blocking=True,
        reason="terminal source",
    )
    terminal_evidence = ScenarioAuthoringEvidence(
        source_block_id="terminal-source",
        document_id="doc",
        title="礼拜堂",
        text=terminal_text,
        coverage_requirements=(terminal_requirement,),
    )
    ending_evidence = ScenarioAuthoringEvidence(
        source_block_id="ending-source",
        document_id="doc",
        title="结局",
        text="如果调查员击败科比特，则进入胜利结局。",
    )
    evidence = (terminal_evidence, ending_evidence)
    contract = ScenarioContract.model_validate({
        "contract_id": "ambiguous-terminal-fallback",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Ambiguous terminal fallback",
        "initial_scene_id": "chapel",
        "locations": [{
            "location_id": "chapel",
            "title": "礼拜堂",
            "initial_visibility": "visited",
        }],
        "entities": [{
            "entity_id": "corbitt",
            "entity_type": "creature",
            "title": "沃尔特·科比特",
            "initial_location_id": "chapel",
        }, {
            "entity_id": "corbitt-spirit",
            "entity_type": "creature",
            "title": "科比特（灵体）",
            "initial_location_id": "chapel",
        }],
    })
    target = SourceCoverageSupplementTarget(
        source_block_id="terminal-source",
        requirement_key="explicit_terminal_method",
        acceptable_record_kinds=("operators",),
        required_additional_count=1,
        reason="terminal source",
    )
    llm = JsonLlm([])
    adapter = ConstrainedScenarioContractAuthoringAdapter(llm)
    candidates = adapter.terminal_entity_candidates(
        contract, evidence, (target,)
    )
    assert candidates == ()

    authored = asyncio.run(adapter.author_partition(
        evidence,
        ruleset_id="coc7",
        partition_index=0,
        coverage_targets=(target,),
        record_id_prefix="supp_",
        terminal_entity_candidates=candidates,
    ))

    assert authored.batch is not None
    assert llm.calls == 0
    fallback = authored.batch.actions[0]
    assert is_terminal_method_fact_command(fallback.on_success[0])
    assert fallback.global_action is False
    candidate = EvidenceBoundContractCandidate(
        contract=contract,
        confidence="high",
        evidence_blocks=tuple(item.evidence_block() for item in evidence),
    )
    with_fallback = adapter.merge_supplement_batches(
        candidate, evidence, (authored.batch,)
    )
    fallback_operator = with_fallback.contract.operators[0]
    assert fallback_operator.preconditions[0] == StateCondition(
        path="scene_id", operator="eq", value="chapel"
    )
    assert EvidenceBoundScenarioCompiler().compile(
        with_fallback
    ).coverage.blocking_complete is True
    ending_candidates, _ = build_ending_catalogs(
        with_fallback.contract, ending_evidence.text
    )
    producer = next(
        item for item in ending_candidates
        if item.operator_id == fallback_operator.operator_id
    )
    ending_batch = materialize_ending_envelope(
        CoverageEndingEnvelope.model_validate({
            "endings": [{
                "title": "击败科比特",
                "all_of": [{
                    "operator_id": producer.operator_id,
                    "outcome": "success",
                }],
            }],
        }),
        candidates=ending_candidates,
        source_block_id="ending-source",
        record_id_prefix="ending_",
    )
    final = adapter.merge_supplement_batches(
        with_fallback, evidence, (ending_batch,)
    )
    compilation = EvidenceBoundScenarioCompiler().compile(final)
    assert not any(
        "No ending condition is reachable" in issue.message
        for issue in compilation.report.issues
    )


def test_terminal_coverage_rejects_non_reserved_fact_shape() -> None:
    action = ScenarioIrBatch.model_validate({
        "actions": [{
            "id": "fake-terminal",
            "title": "Fake terminal",
            "policy": "automatic",
            "on_success": [{
                "kind": "set_fact",
                "path": "source_terminal_methods.not-a-valid-digest.achieved",
                "value": True,
            }],
            "source_block_ids": ["source"],
        }],
    }).actions[0]

    assert ConstrainedScenarioContractAuthoringAdapter._ir_record_satisfies_requirement(
        "explicit_terminal_method", "operators", action
    ) is False


def test_observable_terminal_condition_materializes_before_reachable_ending() -> None:
    source_text = (
        "If the investigators all manage to get away, that's just as good "
        "a resolution as any other."
    )
    observation_requirement = SourceCoverageRequirement(
        requirement_key="explicit_terminal_observation",
        acceptable_record_kinds=("operators",),
        blocking=True,
        reason="source terminal observation",
    )
    ending_requirement = SourceCoverageRequirement(
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        blocking=True,
        reason="source ending",
    )
    source = ScenarioAuthoringEvidence(
        source_block_id="conclusion-source",
        document_id="doc",
        title="Conclusion",
        text=source_text,
        semantic_kind="ending",
        classification_confidence=0.95,
        coverage_requirements=(ending_requirement, observation_requirement),
    )
    observation_target = SourceCoverageSupplementTarget(
        source_block_id=source.source_block_id,
        requirement_key="explicit_terminal_observation",
        acceptable_record_kinds=("operators",),
        required_additional_count=1,
        reason="source terminal observation",
    )
    ending_target = SourceCoverageSupplementTarget(
        source_block_id=source.source_block_id,
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="source ending",
    )
    llm = JsonLlm([])
    adapter = ConstrainedScenarioContractAuthoringAdapter(llm)

    observed = asyncio.run(adapter.author_partition(
        (source,),
        ruleset_id="coc7",
        partition_index=0,
        coverage_targets=(observation_target,),
        record_id_prefix="observation_",
    ))

    assert observed.batch is not None
    assert llm.calls == 0
    assert observed.batch.actions[0].policy == "choice"
    assert observed.batch.actions[0].global_action is True
    candidate = EvidenceBoundContractCandidate(
        contract=ScenarioContract(
            contract_id="observable-ending",
            source_version=1,
            ruleset_id="coc7",
            title="Observable ending",
        ),
        confidence="high",
        evidence_blocks=(source.evidence_block(),),
    )
    with_observation = adapter.merge_supplement_batches(
        candidate, (source,), (observed.batch,)
    )
    observation_operator = with_observation.contract.operators[0]
    assert observation_operator.policy == "choice"
    assert observation_operator.preconditions == ()
    candidates = adapter.ending_operator_candidates(
        with_observation.contract, (source,), (ending_target,)
    )
    assert [item.operator_id for item in candidates] == [
        observation_operator.operator_id
    ]

    authored_ending = asyncio.run(adapter.author_partition(
        (source,),
        ruleset_id="coc7",
        partition_index=1,
        coverage_targets=(ending_target,),
        record_id_prefix="ending_",
        ending_candidates=candidates,
    ))

    assert authored_ending.batch is not None
    assert llm.calls == 0
    final = adapter.merge_supplement_batches(
        with_observation, (source,), (authored_ending.batch,)
    )
    compilation = EvidenceBoundScenarioCompiler().compile(final)
    coverage = {
        item.requirement_key: item for item in compilation.coverage.items
    }
    assert coverage["explicit_terminal_observation"].covered is True
    assert coverage["ending_rule"].covered is True
    assert compilation.report.playability.proof("ending_reachability").status == "passed"


def test_tampered_terminal_observation_is_discarded_during_merge() -> None:
    source = ScenarioAuthoringEvidence(
        source_block_id="conclusion-source",
        document_id="doc",
        title="Conclusion",
        text=(
            "If the investigators all manage to get away, that's just as good "
            "a resolution as any other."
        ),
    )
    target = SourceCoverageSupplementTarget(
        source_block_id=source.source_block_id,
        requirement_key="explicit_terminal_observation",
        acceptable_record_kinds=("operators",),
        required_additional_count=1,
        reason="source terminal observation",
    )
    adapter = ConstrainedScenarioContractAuthoringAdapter(JsonLlm([]))
    authored = asyncio.run(adapter.author_partition(
        (source,),
        ruleset_id="coc7",
        partition_index=0,
        coverage_targets=(target,),
        record_id_prefix="observation_",
    ))
    assert authored.batch is not None
    action = authored.batch.actions[0].model_copy(update={
        "title": "The investigators consider getting away",
        "global_action": False,
    })
    tampered = authored.batch.model_copy(update={"actions": (action,)})
    candidate = EvidenceBoundContractCandidate(
        contract=ScenarioContract(
            contract_id="tampered-observation",
            source_version=1,
            ruleset_id="coc7",
            title="Tampered observation",
        ),
        confidence="high",
        evidence_blocks=(source.evidence_block(),),
    )

    merged = adapter.merge_supplement_batches(candidate, (source,), (tampered,))

    assert merged.contract.operators == ()
    assert any(
        "Unauthenticated terminal observation action was discarded" in item
        for item in merged.assumptions
    )


def test_ending_catalog_exposes_source_named_produced_entity_status() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "entity-ending-state",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Entity ending state",
        "entities": [{
            "entity_id": "antagonist",
            "entity_type": "creature",
            "title": "Antagonist",
        }],
        "operators": [{
            "operator_id": "defeat-antagonist",
            "title": "Defeat Antagonist with its own blade",
            "policy": "choice",
            "preconditions": [{
                "path": "entities.antagonist", "operator": "eq", "value": "active",
            }],
            "success_commands": [{
                "kind": "set_entity_status",
                "entity_id": "antagonist",
                "value": "defeated",
            }],
        }],
    })

    _, states = build_ending_catalogs(
        contract,
        "If the Antagonist is defeated, the investigators receive the reward.",
    )

    state = next(item for item in states if item.path == "entities.antagonist")
    assert state.allowed_values == ("active", "defeated")
    assert state.producers[0].operator_id == "defeat-antagonist"


def test_ending_catalog_exposes_only_source_named_produced_state() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "relevant-ending-state-catalog",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Relevant ending state catalog",
        "initial_facts": {"lighthouse_restored": False, "knife_grabbed": False},
        "operators": [{
            "operator_id": "restore-light",
            "title": "Restore lighthouse light",
            "policy": "required_check",
            "skill_choices": [{
                "skill_key": "coc7.electrical_repair",
                "reason": "Repair the light.",
                "failure_stakes": "The light remains dark.",
            }],
            "success_commands": [{
                "kind": "set_fact", "path": "lighthouse_restored", "value": True,
            }, {
                "kind": "set_fact", "path": "knife_grabbed", "value": True,
            }],
        }],
    })

    _, states = build_ending_catalogs(
        contract,
        "When lighthouse_restored is true, the ship enters the harbor.",
    )

    assert [item.path for item in states] == ["facts.lighthouse_restored"]


def test_ending_catalog_uses_only_witnessed_outcomes_when_state_bound_is_exhausted() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "bounded-ending-catalog",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Bounded catalog",
        "initial_facts": {"done": False},
        "operators": [{
            "operator_id": "finish",
            "title": "Finish",
            "policy": "automatic",
            "preconditions": [{
                "path": "facts.done", "operator": "eq", "value": False,
            }],
            "success_commands": [{
                "kind": "set_fact", "path": "done", "value": True,
            }],
        }],
    })
    clear_ending_catalog_cache()

    operators, states = build_ending_catalogs(
        contract, "finish", maximum_states=1
    )

    assert operators == ()
    assert states == ()


def test_ending_catalog_reuses_exploration_for_same_contract(monkeypatch) -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "cached-ending-catalog",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Cached catalog",
    })
    original = ScenarioPlayabilityAnalyzer.reachable_operator_outcome_exploration
    calls = 0

    def counted(analyzer, current_contract):
        nonlocal calls
        calls += 1
        return original(analyzer, current_contract)

    clear_ending_catalog_cache()
    monkeypatch.setattr(
        ScenarioPlayabilityAnalyzer,
        "reachable_operator_outcome_exploration",
        counted,
    )
    build_ending_catalogs(contract, "first ending source")
    build_ending_catalogs(contract, "second ending source")

    assert calls == 1


def test_incremental_supplement_merge_unifies_provenance_and_rejects_conflicts() -> None:
    first = ScenarioAuthoringEvidence(
        source_block_id="first", document_id="doc", text="A waiting room."
    )
    second = ScenarioAuthoringEvidence(
        source_block_id="second", document_id="doc", text="A waiting room."
    )
    base = ScenarioContract.model_validate({
        "contract_id": "incremental-identity",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Incremental identity",
        "initial_scene_id": "room",
        "locations": [{
            "location_id": "room",
            "title": "Waiting room",
            "initial_visibility": "visited",
            "source_refs": [first.source_ref().model_dump(mode="json")],
        }],
    })
    candidate = EvidenceBoundContractCandidate(
        contract=base,
        confidence="high",
        evidence_blocks=(first.evidence_block(), second.evidence_block()),
    )
    adapter = ConstrainedScenarioContractAuthoringAdapter(llm=JsonLlm([]))
    equivalent = ScenarioIrBatch(locations=({
        "id": "room",
        "title": "Waiting room",
        "source_block_ids": ["second"],
    },))

    merged = adapter.merge_supplement_batches(
        candidate, (first, second), (equivalent,)
    )
    assert [item.source_block_id for item in merged.contract.locations[0].source_refs] == [
        "first", "second"
    ]

    conflicting = ScenarioIrBatch(locations=({
        "id": "room",
        "title": "Different room",
        "source_block_ids": ["second"],
    },))
    with pytest.raises(ValueError, match="locations/room"):
        adapter.merge_supplement_batches(candidate, (first, second), (conflicting,))


def test_deterministic_ending_binds_exact_authoritative_state_to_its_producer() -> None:
    envelope = deterministic_ending_envelope(
        "若 lighthouse_restored 为真，渡轮安全入港。",
        state_candidates=(
            EndingStateCandidate(
                path="facts.lighthouse_restored",
                title="lighthouse_restored",
                allowed_operators=("eq", "ne"),
                value_kind="scalar",
                allowed_values=(False, True),
                producers=(
                    EndingStateProducer(
                        operator_id="repair-light",
                        outcome="success",
                        value=True,
                    ),
                ),
            ),
        ),
    )

    assert envelope is not None
    proposal = envelope.endings[0]
    assert [(item.operator_id, item.outcome) for item in proposal.all_of] == [
        ("repair-light", "success")
    ]
    assert [(item.path, item.operator, item.value) for item in proposal.state_all_of] == [
        ("facts.lighthouse_restored", "eq", True)
    ]


def test_deterministic_ending_rejects_partially_bound_compound_condition() -> None:
    envelope = deterministic_ending_envelope(
        "若时间耗尽且未能修复设施，则进入代价结局。",
        candidates=(
            EndingOperatorCandidate(
                operator_id="restore-site",
                title="修复设施",
                allowed_outcomes=("success", "failure"),
            ),
        ),
    )

    assert envelope is None


def test_deterministic_ending_does_not_bind_initial_or_ungated_state() -> None:
    envelope = deterministic_ending_envelope(
        "若 lighthouse_restored 为假，则进入坏结局。",
        state_candidates=(
            EndingStateCandidate(
                path="facts.lighthouse_restored",
                title="lighthouse_restored",
                allowed_operators=("eq", "ne"),
                value_kind="scalar",
                allowed_values=(False, True),
                producers=(
                    EndingStateProducer(
                        operator_id="repair-light",
                        outcome="success",
                        value=True,
                    ),
                ),
            ),
        ),
    )

    assert envelope is None


def test_ending_supplement_materializes_source_state_from_advertised_producer() -> None:
    source = evidence().model_copy(update={
        "text": "若 case_resolved 为真，则事件结束。",
    })
    target = SourceCoverageSupplementTarget(
        source_block_id=source.source_block_id,
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="来源明确给出结局条件。",
    )
    llm = CapturingJsonLlm([])

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).author_partition(
            (source,),
            ruleset_id="coc7",
            partition_index=28,
            coverage_targets=(target,),
            record_id_prefix="supp28_",
            ending_state_candidates=(
                EndingStateCandidate(
                    path="facts.case_resolved",
                    title="case_resolved",
                    allowed_operators=("eq", "ne"),
                    value_kind="scalar",
                    allowed_values=(False, True),
                    producers=(
                        EndingStateProducer(
                            operator_id="resolve-case",
                            outcome="success",
                            value=True,
                        ),
                    ),
                ),
            ),
        )
    )

    assert result.batch is not None
    assert llm.calls == 0
    ending = result.batch.endings[0]
    assert [(item.path, item.value) for item in ending.all_conditions] == [
        (operator_outcome_path("resolve-case"), "success"),
        ("facts.case_resolved", True),
    ]


def test_ending_operator_catalog_reuses_existing_intent_hints_as_aliases() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "ending-alias-catalog",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "结局别名目录",
        "initial_facts": {"light_restored": False},
        "operators": [{
            "operator_id": "repair-lamp",
            "title": "修复灯室设备",
            "intent_hints": ["恢复灯光", "restore light"],
            "policy": "automatic",
            "preconditions": [{
                "path": "facts.light_restored", "operator": "ne", "value": True,
            }],
            "success_commands": [{
                "kind": "set_fact", "path": "light_restored", "value": True,
            }],
        }],
    })
    source = evidence().model_copy(
        update={"text": "如果恢复灯光，则进入好结局。"}
    )
    target = SourceCoverageSupplementTarget(
        source_block_id=source.source_block_id,
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="来源明确给出结局条件。",
    )

    candidates = (
        ConstrainedScenarioContractAuthoringAdapter.ending_operator_candidates(
            contract,
            (source,),
            (target,),
        )
    )

    assert candidates[0].operator_id == "repair-lamp"
    assert candidates[0].aliases == ("恢复灯光", "restore light")


def test_ending_supplement_rejects_source_trigger_without_causal_operator() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="来源明确给出结局条件。",
    )
    proposal = {
        "endings": [{
            "title": "牺牲结局",
            "any_triggers": [{"title": "主动留下牺牲自己"}],
        }]
    }

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([proposal, proposal, proposal])
        ).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=22,
            coverage_targets=(target,),
            record_id_prefix="supp22_",
            ending_candidates=(),
        )
    )

    assert result.batch is None
    assert result.validation_errors
    assert "extra_forbidden" in result.validation_errors[-1]


def test_ending_supplement_does_not_wrap_deprecated_trigger_proposal() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="来源明确给出结局条件。",
    )
    flattened = {
        "title": "被吞噬",
        "any_triggers": [{"title": "被怪物吞噬"}],
    }

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
                JsonLlm([flattened, flattened, flattened])
        ).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=23,
            coverage_targets=(target,),
            record_id_prefix="supp23_",
        )
    )

    assert result.batch is None
    assert result.validation_errors


def test_ending_supplement_rejects_outcomes_outside_the_server_catalog() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="来源明确给出结局条件。",
    )
    invalid = {
        "endings": [{
            "title": "伪造结局",
            "all_of": [{"operator_id": "escape-chase", "outcome": "critical"}],
        }]
    }

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([invalid, invalid, invalid])
        ).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=21,
            coverage_targets=(target,),
            record_id_prefix="supp21_",
            ending_candidates=(
                EndingOperatorCandidate(
                    operator_id="escape-chase",
                    title="逃离追逐",
                    allowed_outcomes=("success", "failure"),
                ),
            ),
        )
    )

    assert result.batch is None
    assert result.attempt_count == 3
    assert all("outside the server operator" in item for item in result.validation_errors)


def test_ending_supplement_combines_operator_and_server_advertised_state() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="来源明确给出复合结局条件。",
    )
    proposal = {
        "endings": [{
            "title": "及时修复",
            "all_of": [{"operator_id": "restore-site", "outcome": "success"}],
            "state_all_of": [{
                "path": "clocks.deadline",
                "operator": "lt",
                "value": 4,
            }],
        }]
    }
    llm = CapturingJsonLlm([proposal])

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=24,
            coverage_targets=(target,),
            record_id_prefix="supp24_",
            ending_candidates=(
                EndingOperatorCandidate(
                    operator_id="restore-site",
                    title="修复设施",
                    allowed_outcomes=("success", "failure"),
                ),
            ),
            ending_state_candidates=(
                EndingStateCandidate(
                    path="clocks.deadline",
                    title="最后期限",
                    allowed_operators=("lt", "lte", "eq"),
                    value_kind="number",
                    minimum_value=0,
                    maximum_value=6,
                ),
            ),
        )
    )

    assert result.batch is not None
    ending = result.batch.endings[0]
    assert [(item.path, item.operator, item.value) for item in ending.all_conditions] == [
        (operator_outcome_path("restore-site"), "eq", "success"),
        ("clocks.deadline", "lt", 4),
    ]
    assert "允许的既有状态条件目录" in llm.prompts[0]


def test_ending_supplement_retries_numeric_state_incompatible_with_outcome() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="来源明确给出复合结局条件。",
    )
    incompatible = {
        "endings": [{
            "title": "修复后时间已推进",
            "all_of": [{"operator_id": "restore-site", "outcome": "success"}],
            "state_all_of": [{
                "path": "clocks.deadline",
                "operator": "gt",
                "value": 0,
            }],
        }]
    }
    compatible = {
        "endings": [{
            "title": "在最后期限前修复",
            "all_of": [{"operator_id": "restore-site", "outcome": "success"}],
            "state_all_of": [{
                "path": "clocks.deadline",
                "operator": "lt",
                "value": 4,
            }],
        }]
    }

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([incompatible, compatible])
        ).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=29,
            coverage_targets=(target,),
            record_id_prefix="supp29_",
            ending_candidates=(
                EndingOperatorCandidate(
                    operator_id="restore-site",
                    title="修复设施",
                    allowed_outcomes=("success", "failure"),
                ),
            ),
            ending_state_candidates=(
                EndingStateCandidate(
                    path="clocks.deadline",
                    title="最后期限",
                    allowed_operators=("lt", "gt"),
                    value_kind="number",
                    initial_value=0,
                    minimum_value=0,
                    maximum_value=4,
                ),
            ),
        )
    )

    assert result.batch is not None
    assert result.attempt_count == 2
    assert "incompatible with its selected operator outcomes" in result.validation_errors[0]


@pytest.mark.parametrize(
    ("initial", "minimum", "maximum", "delta", "operator", "threshold", "accepted"),
    [
        (0, 0, 4, 1, "gte", 4, True),
        (4, 0, 4, -1, "lte", 0, True),
        (2, 0, 4, -1, "gte", 4, False),
    ],
)
def test_ending_numeric_compatibility_supports_repeatable_monotonic_clock_producer(
    initial: int,
    minimum: int,
    maximum: int,
    delta: int,
    operator: str,
    threshold: int,
    accepted: bool,
) -> None:
    envelope = CoverageEndingEnvelope.model_validate({
        "endings": [{
            "title": "Clock threshold ending",
            "all_of": [{"operator_id": "tick", "outcome": "failure"}],
            "state_all_of": [{
                "path": "clocks.deadline",
                "operator": operator,
                "value": threshold,
            }],
        }]
    })
    kwargs = {
        "envelope": envelope,
        "candidates": (
            EndingOperatorCandidate(
                operator_id="tick",
                title="Advance clock",
                allowed_outcomes=("failure",),
            ),
        ),
        "state_candidates": (
            EndingStateCandidate(
                path="clocks.deadline",
                title="Deadline",
                allowed_operators=("gte", "lte"),
                value_kind="number",
                initial_value=initial,
                minimum_value=minimum,
                maximum_value=maximum,
                producers=(
                    EndingStateProducer(
                        operator_id="tick",
                        outcome="failure",
                        value=max(minimum, min(maximum, initial + delta)),
                        repeatable_delta=delta,
                    ),
                ),
            ),
        ),
        "source_block_id": "chunk-1",
        "record_id_prefix": "repeatable_",
    }

    if accepted:
        assert materialize_ending_envelope(**kwargs).endings
    else:
        with pytest.raises(ValueError, match="incompatible"):
            materialize_ending_envelope(**kwargs)


def test_ending_numeric_one_shot_producer_is_not_assumed_repeatable() -> None:
    envelope = CoverageEndingEnvelope.model_validate({
        "endings": [{
            "title": "Invented repeated threshold",
            "all_of": [{"operator_id": "set-once", "outcome": "success"}],
            "state_all_of": [{
                "path": "clocks.deadline",
                "operator": "gte",
                "value": 4,
            }],
        }]
    })

    with pytest.raises(ValueError, match="incompatible"):
        materialize_ending_envelope(
            envelope,
            candidates=(
                EndingOperatorCandidate(
                    operator_id="set-once",
                    title="Set clock once",
                    allowed_outcomes=("success",),
                ),
            ),
            state_candidates=(
                EndingStateCandidate(
                    path="clocks.deadline",
                    title="Deadline",
                    allowed_operators=("gte",),
                    value_kind="number",
                    initial_value=0,
                    minimum_value=0,
                    maximum_value=4,
                    producers=(
                        EndingStateProducer(
                            operator_id="set-once",
                            outcome="success",
                            value=1,
                        ),
                    ),
                ),
            ),
            source_block_id="chunk-1",
            record_id_prefix="one_shot_",
        )


def test_equivalent_ending_supplements_ignore_transport_ids_and_titles() -> None:
    first = EndingRule(
        ending_id="supp1-ending",
        title="First wording",
        all_conditions=(
            StateCondition(path="clocks.deadline", operator="lt", value=4),
            StateCondition(
                path=operator_outcome_path("restore-site"),
                operator="eq",
                value="success",
            ),
        ),
    )
    repeated = EndingRule(
        ending_id="supp2-ending",
        title="Different wording",
        all_conditions=tuple(reversed(first.all_conditions)),
    )

    assert merge_equivalent_endings((first,), (repeated,)) == (first,)


def test_ending_supplement_rejects_state_outside_server_catalog() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="来源明确给出结局条件。",
    )
    invalid = {
        "endings": [{
            "title": "伪造期限",
            "state_all_of": [{
                "path": "facts.unadvertised",
                "operator": "eq",
                "value": True,
            }],
        }]
    }

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([invalid, invalid, invalid])
        ).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=25,
            coverage_targets=(target,),
            record_id_prefix="supp25_",
            ending_state_candidates=(
                EndingStateCandidate(
                    path="clocks.deadline",
                    title="最后期限",
                    allowed_operators=("lt",),
                    value_kind="number",
                    minimum_value=0,
                    maximum_value=6,
                ),
            ),
        )
    )

    assert result.batch is None
    assert result.attempt_count == 3
    assert all("outside the server condition" in item for item in result.validation_errors)


def test_ending_state_catalog_flattens_only_authoritative_produced_values() -> None:
    contract = ScenarioContract.model_validate({
        "contract_id": "state-catalog",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "状态目录",
        "initial_facts": {"case": {"ready": False}},
        "clocks": [{
            "clock_id": "deadline",
            "title": "最后期限",
            "maximum_value": 6,
        }],
        "resources": [{
            "resource_id": "supplies",
            "title": "补给",
            "initial_value": 2,
            "minimum_value": 0,
            "maximum_value": 4,
        }],
        "operators": [{
            "operator_id": "finish-case",
            "title": "完成事件",
            "policy": "automatic",
            "preconditions": [{
                "path": "facts.case.ready",
                "operator": "eq",
                "value": False,
            }],
            "success_commands": [{
                "kind": "set_fact",
                "path": "case.ready",
                "value": True,
            }],
            "failure_commands": [{
                "kind": "advance_clock",
                "clock_id": "deadline",
                "delta": 1,
            }],
        }],
        "clues": [{
            "clue_id": "seal",
            "title": "封印状态",
            "fact_path": "facts.seal.broken",
            "fact_value": True,
        }],
    })

    candidates = {
        item.path: item
        for item in ConstrainedScenarioContractAuthoringAdapter.ending_state_candidates(
            contract
        )
    }

    assert candidates["clocks.deadline"].allowed_operators == (
        "eq", "ne", "lt", "lte", "gt", "gte"
    )
    assert candidates["clocks.deadline"].maximum_value == 6
    assert candidates["clocks.deadline"].initial_value == 0
    assert [
        (item.operator_id, item.outcome, item.value)
        for item in candidates["clocks.deadline"].producers
    ] == []
    assert candidates["resources.supplies"].minimum_value == 0
    assert candidates["resources.supplies"].maximum_value == 4
    assert candidates["facts.case.ready"].allowed_values == (False, True)
    assert [
        (item.operator_id, item.outcome, item.value)
        for item in candidates["facts.case.ready"].producers
    ] == [("finish-case", "success", True)]
    assert candidates["facts.seal.broken"].allowed_values == (True,)
    assert "facts.facts.seal.broken" not in candidates


def test_ending_supplement_rejects_out_of_bounds_state_value() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="来源明确给出结局条件。",
    )
    invalid = {
        "endings": [{
            "title": "越界期限",
            "state_all_of": [{
                "path": "clocks.deadline",
                "operator": "gte",
                "value": 99,
            }],
        }]
    }

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([invalid, invalid, invalid])
        ).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=26,
            coverage_targets=(target,),
            record_id_prefix="supp26_",
            ending_state_candidates=(
                EndingStateCandidate(
                    path="clocks.deadline",
                    title="最后期限",
                    allowed_operators=("gte",),
                    value_kind="number",
                    minimum_value=0,
                    maximum_value=6,
                ),
            ),
        )
    )

    assert result.batch is None
    assert all("exceeds the server maximum" in item for item in result.validation_errors)


def test_ending_supplement_normalizes_absent_boolean_fact_to_open_world_negation() -> None:
    target = SourceCoverageSupplementTarget(
        source_block_id="chunk-1",
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="来源明确给出未取得线索的结局条件。",
    )
    proposal = {
        "endings": [{
            "title": "尚未取得证明",
            "state_all_of": [{
                "path": "facts.clue.proof_found",
                "operator": "eq",
                "value": False,
            }],
        }]
    }

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([proposal])).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=27,
            coverage_targets=(target,),
            record_id_prefix="supp27_",
            ending_state_candidates=(
                EndingStateCandidate(
                    path="facts.clue.proof_found",
                    title="证明",
                    allowed_operators=("eq", "ne"),
                    value_kind="scalar",
                    allowed_values=(True,),
                ),
            ),
        )
    )

    assert result.batch is not None
    condition = result.batch.endings[0].all_conditions[0]
    assert (condition.operator, condition.value) == ("ne", True)


def test_partition_removes_catalog_invalid_effect_without_regenerating_partition() -> None:
    invalid = authored_payload(document_id="document-1", block_id="chunk-1")
    invalid["actions"][0]["on_success"] = [
        {
            "kind": "apply_ruleset_effect",
            "event_type": "invented_damage",
            "payload": {"amount": "99d99"},
        }
    ]
    llm = CapturingJsonLlm([invalid])

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            llm, effect_catalog=coc7_scenario_effect_catalog()
        ).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=0,
        )
    )

    assert result.batch is not None
    assert result.attempt_count == 1
    assert result.batch.actions[0].on_success == ()
    assert "可执行规则效果目录" in llm.prompts[0]
    assert llm.calls == 1


def test_partition_replaces_invalid_effect_from_one_source_catalog_match() -> None:
    invalid = authored_payload(document_id="document-1", block_id="chunk-1")
    invalid["actions"][0]["on_success"] = [{
        "kind": "apply_ruleset_effect",
        "event_type": "invented_effect",
        "payload": {"amount": "99d99"},
    }]
    source = evidence().model_copy(update={"text": "成功时 SAN 损失 1d6。"})
    llm = CapturingJsonLlm([invalid])

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            llm, effect_catalog=coc7_scenario_effect_catalog()
        ).author_partition(
            (source,), ruleset_id="coc7", partition_index=0
        )
    )

    assert result.batch is not None
    effect = result.batch.actions[0].on_success[0]
    assert (effect.event_type, effect.payload) == ("san_loss", {"loss": "1d6"})
    assert result.attempt_count == 1
    assert "unique source effect" in result.batch.assumptions[0]


def test_partition_contracts_only_the_invalid_reactive_child() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["entities"] = [{
        "id": "witness",
        "type": "npc",
        "title": "目击者",
        "source_block_ids": ["chunk-1"],
    }]
    payload["reactive_policies"] = [{
        "id": "witness-reactions",
        "entity_id": "witness",
        "source_block_ids": ["chunk-1"],
        "rules": [{
            "rule_id": "valid-background",
            "trigger": "background_tick",
            "commands": [{"kind": "emit_event", "event_type": "npc_waits"}],
        }, {
            "rule_id": "invalid-semantic",
            "trigger": "semantic_event",
            "commands": [{"kind": "emit_event", "event_type": "npc_reacts"}],
        }],
    }]
    llm = CapturingJsonLlm([payload])

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).author_partition(
            (evidence(),), ruleset_id="coc7", partition_index=0
        )
    )

    assert result.batch is not None
    assert [
        rule.rule_id for rule in result.batch.reactive_policies[0].rules
    ] == ["valid-background"]
    assert "contracted locally: 1 rules" in result.batch.assumptions[0]
    assert llm.calls == 1


def test_partition_splits_ruleset_declared_outcome_pair_notation() -> None:
    paired = authored_payload(document_id="document-1", block_id="chunk-1")
    paired["actions"][0]["policy"] = "required_check"
    paired["actions"][0]["checks"] = [{
        "skill_key": "san",
        "difficulty": "regular",
        "reason": "来源要求 SAN 1/1d6",
    }]
    paired["actions"][0]["on_success"] = [{
        "kind": "apply_ruleset_effect",
        "event_type": "san_loss",
        "payload": {"loss": "1/1d6"},
    }]

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([paired]),
            effect_catalog=coc7_scenario_effect_catalog(),
        ).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=8,
        )
    )

    assert result.batch is not None
    action = result.batch.actions[0]
    assert action.on_success[0].payload == {"loss": "1"}
    assert action.on_failure[0].payload == {"loss": "1d6"}
    assert "outcome-pair notation" in result.batch.assumptions[-1]


def test_partition_moves_always_outcome_pair_to_check_branches() -> None:
    paired = authored_payload(document_id="document-1", block_id="chunk-1")
    paired["actions"][0]["policy"] = "required_check"
    paired["actions"][0]["checks"] = [{
        "skill_key": "san",
        "difficulty": "regular",
        "reason": "来源要求 SAN 1d4/1d10",
    }]
    paired["actions"][0]["always"] = [{
        "kind": "apply_ruleset_effect",
        "event_type": "san_loss",
        "payload": {"loss": "1d4/1d10"},
    }]

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([paired]),
            effect_catalog=coc7_scenario_effect_catalog(),
        ).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=8,
        )
    )

    assert result.batch is not None
    action = result.batch.actions[0]
    assert action.always == ()
    success_effect = next(
        command for command in action.on_success if command.kind == "apply_ruleset_effect"
    )
    assert success_effect.payload == {"loss": "1d4"}
    assert action.on_failure[0].payload == {"loss": "1d10"}


def test_partition_normalizes_unambiguous_world_command_transport_aliases() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["actions"][0]["on_success"] = [
        {"kind": "set_fact", "fact_path": "ticket.found", "fact_value": True},
        {"kind": "adjust_resource", "resource_id": "san", "amount": -1},
    ]
    payload["endings"] = [{
        "id": "escape",
        "title": "逃离",
        "all_conditions": [{
            "path": "facts.ticket.found",
            "operator": "eq",
            "value": True,
        }],
        "commands": [
            {"kind": "set_scene", "scene_id": "outside"},
            {"kind": "complete_run"},
        ],
        "source_block_ids": ["chunk-1"],
    }]

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(JsonLlm([payload])).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=3,
        )
    )

    assert result.batch is not None
    assert result.batch.actions[0].on_success[0].path == "ticket.found"
    assert result.batch.actions[0].on_success[1].path == "san"
    assert result.batch.actions[0].on_success[1].delta == -1
    assert result.batch.endings[0].commands[0].value == "outside"
    assert len(result.batch.endings[0].commands) == 1
    assert "5 command alias fields rewritten" in result.batch.assumptions[0]
    assert "1 redundant ending completions removed" in result.batch.assumptions[0]


def test_partition_rejects_conflicting_world_command_aliases() -> None:
    payload = authored_payload(document_id="document-1", block_id="chunk-1")
    payload["actions"][0]["on_success"] = [{
        "kind": "set_scene",
        "value": "outside",
        "scene_id": "invented-scene",
    }]

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(
            JsonLlm([payload, payload, payload])
        ).author_partition(
            (evidence(),),
            ruleset_id="coc7",
            partition_index=4,
        )
    )

    assert result.batch is not None
    assert result.batch.actions == ()
    assert any("scene_id" in error for error in result.validation_errors)


def test_truncated_corpus_can_never_be_auto_publishable() -> None:
    llm = JsonLlm(
        [authored_payload(document_id="document-1", block_id="chunk-1")]
    )

    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).author(
            (evidence(),),
            contract_id="module-server-bound",
            source_version=1,
            ruleset_id="coc7",
            title="真实模组名",
            corpus_truncated=True,
        )
    )

    assert result.candidate is not None
    assert "bounded authoring window" in result.candidate.assumptions[-1]


def test_authoring_partitions_source_and_merges_local_ir_records() -> None:
    class PartitionAwareLlm:
        calls = 0

        async def complete(self, messages, temperature: float = 0.7) -> str:
            self.calls += 1
            prompt = messages[-1].content
            catalog = json.loads(
                prompt.split("证据目录：", 1)[1].split("\n返回", 1)[0]
            )
            source_id = catalog[0]["source_block_id"]
            return json.dumps(
                {
                    "confidence": "high",
                    "actions": [
                        {
                            "id": f"inspect-{source_id}",
                            "title": f"检查 {source_id}",
                            "policy": "automatic",
                            "on_success": [
                                {
                                    "kind": "set_fact",
                                    "path": f"inspected.{source_id}",
                                    "value": True,
                                }
                            ],
                            "source_block_ids": [source_id],
                        }
                    ],
                },
                ensure_ascii=False,
            )

    sources = tuple(
        ScenarioAuthoringEvidence(
            source_block_id=f"chunk-{index}",
            document_id="document-1",
            title=f"Section {index}",
            text=f"Evidence {index}",
        )
        for index in range(17)
    )
    llm = PartitionAwareLlm()
    result = asyncio.run(
        ConstrainedScenarioContractAuthoringAdapter(llm).author(
            sources,
            contract_id="partitioned-contract",
            source_version=1,
            ruleset_id="coc7",
            title="Partition fixture",
            corpus_truncated=False,
        )
    )

    assert result.candidate is not None
    assert result.partition_count == 2
    assert result.completed_partition_count == 2
    assert result.attempt_count == 2
    assert {item.operator_id for item in result.candidate.contract.operators} == {
        "inspect-chunk-0",
        "inspect-chunk-16",
    }


def test_authoring_evidence_keeps_many_tiny_document_paragraphs() -> None:
    chunks = [
        {
            "id": f"chunk-{index}",
            "title": f"Paragraph {index}",
            "text": "短段落证据。",
            "order_index": index,
            "semantic_kind": "text",
            "classification_confidence": 0.5,
        }
        for index in range(243)
    ]

    evidence, truncated = ScenarioContractService.authoring_evidence(
        {"id": "module-1", "source_hash": "document-1"},
        chunks,
    )

    assert len(evidence) == 243
    assert truncated is False
    assert len(ConstrainedScenarioContractAuthoringAdapter.partitions(evidence)) == 16


def test_service_revalidates_all_module_source_before_persisting(tmp_path: Path) -> None:
    with db_session(tmp_path / "scenario-authoring-race.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Scenario authoring race")
        module = repo.create_module(
            campaign["id"],
            "Mutable source fixture",
            [
                ModuleChunk(
                    title="候车室",
                    text="调查员在候车室可以检查座椅下方。",
                    visibility="kp",
                    spoiler_tag=None,
                    scene_key="waiting-room",
                    order_index=0,
                )
            ],
        )
        chunk = repo.list_module_chunks(
            module["id"],
            allowed_visibility=("kp",),
            spoiler_tags=None,
        )[0]

        class MutatingLlm:
            async def complete(self, messages, temperature: float = 0.7) -> str:
                connection.execute(
                    "UPDATE module_chunks SET text = ? WHERE id = ?",
                    ("并发修改后的来源。", chunk["id"]),
                )
                connection.commit()
                return json.dumps(
                    authored_payload(
                        document_id=module["id"],
                        block_id=chunk["id"],
                        page=None,
                        paragraph=None,
                    ),
                    ensure_ascii=False,
                )

        with pytest.raises(ConflictError, match="source changed"):
            asyncio.run(
                ScenarioContractService(repo).generate_from_module(
                    module["id"],
                    MutatingLlm(),
                    ruleset_id="coc7",
                    automation_level="ai_kp",
                    created_by_member_id=None,
                )
            )

        assert repo.list_module_scenario_contract_versions(module["id"]) == []


def test_full_ai_requires_independent_semantic_review_before_publish(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "scenario-review.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Scenario semantic review")
        module = repo.create_module(
            campaign["id"],
            "Review fixture",
            [
                ModuleChunk(
                    title="候车室",
                    text="调查员可以检查候车室座椅。",
                    visibility="kp",
                    spoiler_tag=None,
                    scene_key="waiting-room",
                    order_index=0,
                )
            ],
        )
        chunk = repo.list_module_chunks(
            module["id"], allowed_visibility=("kp",), spoiler_tags=None
        )[0]
        authoring = authored_payload(
            document_id=module["id"],
            block_id=chunk["id"],
            page=None,
            paragraph=None,
        )

        class RejectingReviewerLlm:
            calls = 0

            async def complete(self, messages, temperature: float = 0.7) -> str:
                self.calls += 1
                if self.calls == 1:
                    return json.dumps(authoring, ensure_ascii=False)
                return json.dumps(
                    {
                        "decision": "reject",
                        "findings": ["命令效果没有被来源直接支持"],
                        "issues": [{
                            "group": "operators",
                            "record_id": "inspect-room",
                            "problem": "命令效果没有被来源直接支持",
                            "source_block_ids": [chunk["id"]],
                        }],
                        "supported_assumption_indices": [],
                    },
                    ensure_ascii=False,
                )

        result = asyncio.run(
            ScenarioContractService(repo).generate_from_module(
                module["id"],
                RejectingReviewerLlm(),
                ruleset_id="coc7",
                automation_level="ai_kp",
                created_by_member_id=None,
            )
        )

        assert result.authoring["review"]["decision"] == "reject"
        assert result.auto_published is False
        assert result.version is not None
        assert result.version["status"] == "draft"
        assert any(
            issue.code == "unresolved_authoring_assumption"
            and "命令效果" in issue.message
            for issue in result.version["validation"].issues
        )

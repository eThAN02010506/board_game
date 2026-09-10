import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai_kp.application.errors import ConflictError
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.bootstrap.settings import Settings
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.infrastructure.scenario_contract_worker import (
    _assert_job_source_unchanged,
    _ending_last_coverage_groups,
    _JobCompilationMemo,
    _run_candidate_coverage_supplements,
    _settled_coverage_supplement_summary,
    process_claimed_scenario_contract_job,
)
from ai_kp.infrastructure.scenario_coverage_progress import (
    coverage_attempt_fingerprint,
    restore_coverage_no_progress,
)
from ai_kp.platform.modules.ingestion import ModuleChunk
from ai_kp.platform.resolution.contracts import ScenarioContract
from ai_kp.platform.resolution.evidence_compiler import (
    EvidenceBoundContractCandidate,
    EvidenceBoundScenarioCompiler,
)
from ai_kp.platform.resolution.kernel import operator_outcome_path
from ai_kp.platform.resolution.scenario_authoring import (
    ConstrainedScenarioContractAuthoringAdapter,
    ScenarioAuthoringEvidence,
    ScenarioContractReview,
    ScenarioPartitionAuthoringResult,
)
from ai_kp.platform.resolution.scenario_ir_models import ScenarioIrBatch
from ai_kp.platform.resolution.scenario_ir_repair import ScenarioIrRepairDiagnostic
from ai_kp.platform.resolution.source_coverage import SourceCoverageSupplementTarget


class _ValidPartitionLlm:
    async def complete(self, messages, temperature: float = 0.7) -> str:
        prompt = messages[-1].content
        source = json.loads(prompt.split("证据目录：", 1)[1].split("\n返回", 1)[0])[0]
        return json.dumps(
            {
                "confidence": "high",
                "initial_scene_id": "room",
                "locations": [
                    {
                        "id": "room",
                        "title": "Room",
                        "visibility": "visited",
                        "source_block_ids": [source["source_block_id"]],
                    }
                ],
                "actions": [
                    {
                        "id": "inspect",
                        "title": "Inspect",
                        "location_slot": 0,
                        "intent_hints": ["inspect"],
                        "policy": "automatic",
                        "on_success": [
                            {"kind": "set_fact", "path": "room.seen", "value": True}
                        ],
                        "source_block_ids": [source["source_block_id"]],
                    }
                ],
            }
        )


class _SwitchModelDuringPartitionLlm(_ValidPartitionLlm):
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.switched = False

    async def complete(self, messages, temperature: float = 0.7) -> str:
        if not self.switched:
            self.switched = True
            with db_session(self.db_path) as other_connection:
                Repository(other_connection).save_model_configuration(
                    provider_type="openai_compatible",
                    base_url="http://model-b.test/v1",
                    api_key="b",
                    model="model-b",
                    local_model_path=None,
                    local_port=8011,
                    semantic_profile="small",
                )
        return await super().complete(messages, temperature)


class _CoverageSupplementLlm:
    def __init__(self, *, fail_supplements: bool = False):
        self.fail_supplements = fail_supplements

    async def complete(self, messages, temperature: float = 0.7) -> str:
        prompt = messages[-1].content
        if "补写目标：" in prompt:
            if self.fail_supplements:
                return json.dumps({"confidence": "high"})
            return json.dumps({
                "confidence": "high",
                "actions": [{
                    "title": "侦查旧车票",
                }],
            }, ensure_ascii=False)
        source = json.loads(
            prompt.split("证据目录：", 1)[1].split("\n返回", 1)[0]
        )[0]
        return json.dumps({
            "confidence": "high",
            "initial_scene_id": "station",
            "locations": [{
                "id": "station",
                "title": source["title"],
                "visibility": "visited",
                "source_block_ids": [source["source_block_id"]],
            }],
            "actions": [{
                "id": "look-around",
                "title": "环顾车站",
                "location_slot": 0,
                "intent_hints": ["环顾"],
                "policy": "automatic",
                "source_block_ids": [source["source_block_id"]],
            }],
        }, ensure_ascii=False)


class _ReviewRepairLlm:
    def __init__(self):
        self.review_calls = 0

    async def complete(self, messages, temperature: float = 0.7) -> str:
        prompt = messages[-1].content
        if "审核问题：" in prompt:
            targets = json.loads(
                prompt.split("当前目标记录：", 1)[1].split("\n可用证据：", 1)[0]
            )
            replacement = targets[0]["record"]
            replacement["success_commands"] = []
            return json.dumps({
                "repairs": [{
                    "group": "operators",
                    "record_id": "inspect",
                    "replacement": replacement,
                }],
            })
        if "本批待审核契约记录：" in prompt:
            self.review_calls += 1
            if self.review_calls == 1:
                records = json.loads(
                    prompt.split("本批待审核契约记录：", 1)[1].split(
                        "\noperator_outcome_path_legend：", 1
                    )[0]
                )
                source_id = records["operators"][0]["source_refs"][0][
                    "source_block_id"
                ]
                return json.dumps({
                    "decision": "reject",
                    "findings": ["inspect 的成功效果没有来源支持。"],
                    "issues": [{
                        "group": "operators",
                        "record_id": "inspect",
                        "problem": "成功效果没有来源支持。",
                        "source_block_ids": [source_id],
                    }],
                    "supported_assumption_indices": [],
                }, ensure_ascii=False)
            return json.dumps({
                "decision": "approve",
                "findings": [],
                "issues": [],
                "supported_assumption_indices": [],
            })
        source = json.loads(
            prompt.split("证据目录：", 1)[1].split("\n返回", 1)[0]
        )[0]
        return json.dumps({
            "confidence": "high",
            "initial_scene_id": "room",
            "locations": [{
                "id": "room",
                "title": "Room",
                "visibility": "visited",
                "source_block_ids": [source["source_block_id"]],
            }],
            "actions": [{
                "id": "inspect",
                "title": "Inspect",
                "location_slot": 0,
                "intent_hints": ["inspect"],
                "policy": "automatic",
                "on_success": [{
                    "kind": "set_fact",
                    "path": "room.unsupported",
                    "value": True,
                }],
                "source_block_ids": [source["source_block_id"]],
            }, {
                "id": "leave-room",
                "title": "Leave the room and end the visit",
                "location_slot": 0,
                "intent_hints": ["leave"],
                "policy": "automatic",
                "on_success": [{
                    "kind": "set_fact",
                    "path": "visit.finished",
                    "value": True,
                }],
                "source_block_ids": [source["source_block_id"]],
            }],
            "endings": [{
                "id": "visit-finished",
                "title": "Visit finished",
                "all_conditions": [{
                    "path": "facts.visit.finished",
                    "operator": "eq",
                    "value": True,
                }],
                "source_block_ids": [source["source_block_id"]],
            }],
        })


class _AlwaysRejectReviewLlm(_ReviewRepairLlm):
    async def complete(self, messages, temperature: float = 0.7) -> str:
        prompt = messages[-1].content
        if "本批待审核契约记录：" in prompt:
            self.review_calls += 1
            records = json.loads(
                prompt.split("本批待审核契约记录：", 1)[1].split(
                    "\noperator_outcome_path_legend：", 1
                )[0]
            )
            operator = records["operators"][0]
            source_id = operator["source_refs"][0]["source_block_id"]
            return json.dumps({
                "decision": "reject",
                "findings": [f"Operator {operator['operator_id']} remains unsupported."],
                "issues": [{
                    "group": "operators",
                    "record_id": operator["operator_id"],
                    "problem": "remains unsupported",
                    "source_block_ids": [source_id],
                }],
                "supported_assumption_indices": [],
            })
        if "审核问题：" in prompt:
            targets = json.loads(
                prompt.split("当前目标记录：", 1)[1].split("\n可用证据：", 1)[0]
            )
            replacement = targets[0]["record"]
            replacement["title"] = (
                f"{replacement.get('title', 'Record')} review {self.review_calls}"
            )
            return json.dumps({
                "repairs": [{
                    "group": targets[0]["group"],
                    "record_id": targets[0]["record_id"],
                    "replacement": replacement,
                }],
            })
        return await super().complete(messages, temperature)


class _UnsupportedAssumptionReviewLlm(_ReviewRepairLlm):
    async def complete(self, messages, temperature: float = 0.7) -> str:
        prompt = messages[-1].content
        if "本批待审核契约记录：" in prompt:
            self.review_calls += 1
            return json.dumps({
                "decision": "approve",
                "findings": [],
                "issues": [],
                "supported_assumption_indices": [],
            })
        raw = await super().complete(messages, temperature)
        if "证据目录：" in prompt:
            payload = json.loads(raw)
            payload["assumptions"] = ["来源未说明所有移动都固定耗时一小时"]
            return json.dumps(payload, ensure_ascii=False)
        return raw


class _ReviewDeletesCoverageLlm:
    """Try to reject a deterministic coverage record owned by the server."""

    def __init__(self):
        self.review_calls = 0

    async def complete(self, messages, temperature: float = 0.7) -> str:
        prompt = messages[-1].content
        if "审核问题：" in prompt:
            targets = json.loads(
                prompt.split("当前目标记录：", 1)[1].split("\n可用证据：", 1)[0]
            )
            return json.dumps({
                "repairs": [{
                    "group": targets[0]["group"],
                    "record_id": targets[0]["record_id"],
                    "replacement": None,
                }],
            })
        if "本批待审核契约记录：" in prompt:
            self.review_calls += 1
            records = json.loads(
                prompt.split("本批待审核契约记录：", 1)[1].split(
                    "\noperator_outcome_path_legend：", 1
                )[0]
            )
            supplemented = [
                item for item in records["operators"]
                if item["operator_id"].startswith("supp")
            ]
            if self.review_calls <= 3:
                operator = supplemented[-1]
                source_id = operator["source_refs"][0]["source_block_id"]
                return json.dumps({
                    "decision": "reject",
                    "findings": ["首轮补写记录需要移除后重新闭合。"],
                    "issues": [{
                        "group": "operators",
                        "record_id": operator["operator_id"],
                        "problem": "首轮补写记录需要移除后重新闭合。",
                        "source_block_ids": [source_id],
                    }],
                    "supported_assumption_indices": [],
                }, ensure_ascii=False)
            assert any(
                item["operator_id"].startswith("supp3_")
                for item in supplemented
            )
            return json.dumps({
                "decision": "approve",
                "findings": [],
                "issues": [],
                "supported_assumption_indices": [],
            })
        if "补写目标：" in prompt:
            return json.dumps({
                "confidence": "high",
                "actions": [{"title": "侦查旧车票"}],
            }, ensure_ascii=False)
        source = json.loads(
            prompt.split("证据目录：", 1)[1].split("\n返回", 1)[0]
        )[0]
        return json.dumps({
            "confidence": "high",
            "initial_scene_id": "station",
            "locations": [{
                "id": "station", "title": source["title"], "visibility": "visited",
                "source_block_ids": [source["source_block_id"]],
            }],
            "actions": [{
                "id": "wait", "title": "等待", "policy": "automatic",
                "location_slot": 0,
                "source_block_ids": [source["source_block_id"]],
            }],
        }, ensure_ascii=False)


def _create_job(repo: Repository) -> dict:
    campaign = repo.create_campaign("Durable contract queue")
    module = repo.create_module(
        campaign["id"],
        "Long generic module",
        [
            ModuleChunk(
                title="Source",
                text="A location and an actionable clue.",
                visibility="kp",
                spoiler_tag=None,
                scene_key=None,
                order_index=0,
            )
        ],
    )
    partitions = (
        (
            ScenarioAuthoringEvidence(
                source_block_id="block-1",
                document_id="document-1",
                text="First bounded source block.",
            ),
        ),
        (
            ScenarioAuthoringEvidence(
                source_block_id="block-2",
                document_id="document-1",
                text="Second bounded source block.",
            ),
        ),
    )
    return repo.create_scenario_contract_job(
        campaign_id=campaign["id"],
        module_id=module["id"],
        run_id=None,
        ruleset_id="coc7",
        automation_level="balanced",
        created_by_member_id=None,
        source_fingerprint="a" * 64,
        contract_key="module-a",
        title=module["title"],
        evidence_partitions=partitions,
        corpus_total_block_count=2,
        corpus_truncated=False,
    )


def _coverage_supplement_input() -> tuple[
    tuple[ScenarioAuthoringEvidence, ...],
    SourceCoverageSupplementTarget,
]:
    return (
        (
            ScenarioAuthoringEvidence(
                source_block_id="block-1",
                document_id="document-1",
                title="车站",
                text="通过侦查检定发现车票。",
            ),
        ),
        SourceCoverageSupplementTarget(
            source_block_id="block-1",
            requirement_key="explicit_checks",
            acceptable_record_kinds=("operators",),
            required_additional_count=1,
            reason="来源明确要求检定。",
        ),
    )


def test_recovered_job_rehydrates_server_owned_evidence_ancestry(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "scenario-evidence-ancestry.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Recovered ancestry")
        module = repo.create_module(
            campaign["id"],
            "Scenario",
            [ModuleChunk(
                title="床架攻击",
                text="床架可能会攻击调查员。",
                visibility="kp",
                order_index=0,
            )],
        )
        chunk_id = repo.list_module_chunks(module["id"])[0]["id"]
        job = repo.create_scenario_contract_job(
            campaign_id=campaign["id"],
            module_id=module["id"],
            run_id=None,
            ruleset_id="coc7",
            automation_level="ai_kp",
            created_by_member_id=None,
            source_fingerprint="a" * 64,
            contract_key="module-ancestry",
            title="Scenario",
            evidence_partitions=((ScenarioAuthoringEvidence(
                source_block_id=chunk_id,
                document_id="document-1",
                title="床架攻击",
                text="床架可能会攻击调查员。",
            ),),),
            corpus_total_block_count=1,
            corpus_truncated=False,
        )
        claimed = repo.claim_next_scenario_contract_job(worker_id="worker")
        assert claimed is not None
        partition = repo.next_scenario_contract_partition(
            job["id"], expected_attempt=claimed["attempt_count"]
        )
        assert partition is not None
        repo.complete_scenario_contract_partition(
            job["id"],
            0,
            expected_attempt=claimed["attempt_count"],
            batch=ScenarioIrBatch(),
            model_attempt_count=1,
            validation_errors=(),
        )
        connection.execute(
            "UPDATE module_chunks SET heading_level = 4, section_path_json = ?, "
            "scene_key = 'empty_room' WHERE id = ?",
            (json.dumps(["场景 2", "3号房间：空卧室", "床架攻击"]), chunk_id),
        )

        evidence, _ = repo.load_scenario_contract_job_inputs(job["id"])

        assert evidence[0].heading_level == 4
        assert evidence[0].section_path[-2:] == ("3号房间：空卧室", "床架攻击")
        assert evidence[0].scene_key == "empty_room"


def test_review_coverage_splits_producers_before_ending_links() -> None:
    evidence, check_target = _coverage_supplement_input()
    ending_target = SourceCoverageSupplementTarget(
        source_block_id="block-1",
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="来源明确给出结局。",
    )

    ordered = _ending_last_coverage_groups(
        ((evidence, (ending_target, check_target)),)
    )

    assert len(ordered) == 2
    assert ordered[0][1] == (check_target,)
    assert ordered[1][1] == (ending_target,)


def test_review_coverage_isolates_terminal_observation_before_ending() -> None:
    evidence, check_target = _coverage_supplement_input()
    observation_target = SourceCoverageSupplementTarget(
        source_block_id="block-1",
        requirement_key="explicit_terminal_observation",
        acceptable_record_kinds=("operators",),
        required_additional_count=1,
        reason="source terminal observation",
    )
    ending_target = SourceCoverageSupplementTarget(
        source_block_id="block-1",
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="source ending",
    )

    ordered = _ending_last_coverage_groups(
        ((evidence, (ending_target, check_target, observation_target)),)
    )

    assert [targets for _, targets in ordered] == [
        (check_target,),
        (observation_target,),
        (ending_target,),
    ]


def test_recovered_legacy_mixed_coverage_plan_matches_split_plan(tmp_path: Path) -> None:
    evidence, check_target = _coverage_supplement_input()
    ending_target = SourceCoverageSupplementTarget(
        source_block_id="block-1",
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="source ending",
    )
    with db_session(tmp_path / "legacy-mixed-plan.sqlite3") as connection:
        repo = Repository(connection)
        job = _create_job(repo)
        claimed = repo.claim_next_scenario_contract_job(worker_id="legacy-worker")
        assert claimed is not None
        assert repo.ensure_scenario_contract_supplements(
            job["id"],
            expected_attempt=claimed["attempt_count"],
            groups=((evidence, (check_target, ending_target)),),
            supplement_cycle=1,
        ) == 1

        assert repo.ensure_scenario_contract_supplements(
            job["id"],
            expected_attempt=claimed["attempt_count"],
            groups=((evidence, (check_target,)), (evidence, (ending_target,))),
            supplement_cycle=1,
        ) == 1


class _FixedCoverageCompiler:
    def __init__(self, targets: tuple[SourceCoverageSupplementTarget, ...]):
        self.targets = targets

    def compile(self, candidate):
        return SimpleNamespace(
            coverage=SimpleNamespace(
                supplement_targets=lambda *, blocking_only: self.targets
            )
        )


class _ProducerThenEndingAdapter:
    def __init__(self):
        self.delegate = ConstrainedScenarioContractAuthoringAdapter(_ValidPartitionLlm())
        self.ending_catalog_operator_ids: list[tuple[str, ...]] = []

    async def author_partition(self, evidence, **kwargs):
        targets = kwargs["coverage_targets"]
        if targets[0].requirement_key != "ending_rule":
            assert kwargs["ending_candidates"] == ()
            assert kwargs["ending_state_candidates"] == ()
            return ScenarioPartitionAuthoringResult(
                batch=ScenarioIrBatch.model_validate({
                    "actions": [{
                        "id": "finish-current-threat",
                        "title": "结束当前威胁",
                        "policy": "automatic",
                        "preconditions": [{
                            "path": "facts.ready", "operator": "eq", "value": False,
                        }],
                        "on_success": [{
                            "kind": "set_fact", "path": "threat_finished", "value": True,
                        }],
                        "source_block_ids": ["block-1"],
                    }],
                }),
                attempt_count=1,
            )
        candidates = kwargs["ending_candidates"]
        self.ending_catalog_operator_ids.append(
            tuple(item.operator_id for item in candidates)
        )
        assert "finish-current-threat" in self.ending_catalog_operator_ids[-1]
        return ScenarioPartitionAuthoringResult(
            batch=ScenarioIrBatch.model_validate({
                "endings": [{
                    "id": "ending-current-threat",
                    "title": "Threat finished",
                    "all_conditions": [{
                        "path": operator_outcome_path("finish-current-threat"),
                        "operator": "eq",
                        "value": "success",
                    }],
                    "source_block_ids": ["block-1"],
                }],
            }),
            attempt_count=1,
        )

    def ending_catalogs(self, contract, evidence, targets):
        return self.delegate.ending_catalogs(contract, evidence, targets)

    def merge_supplement_batches(self, candidate, evidence, batches):
        return self.delegate.merge_supplement_batches(candidate, evidence, batches)

    def reconcile_candidate_authority(self, candidate, evidence):
        return self.delegate.reconcile_candidate_authority(candidate, evidence)


@pytest.mark.parametrize("recover_producer", [False, True])
def test_review_coverage_rebuilds_ending_catalog_after_current_producer(
    tmp_path: Path,
    recover_producer: bool,
) -> None:
    with db_session(tmp_path / f"ending-refresh-{recover_producer}.sqlite3") as connection:
        repo = Repository(connection)
        job = _create_job(repo)
        claimed = repo.claim_next_scenario_contract_job(worker_id="coverage-worker")
        assert claimed is not None
        evidence, check_target = _coverage_supplement_input()
        evidence = (
            evidence[0].model_copy(
                update={
                    "text": (
                        "通过侦查检定结束当前威胁；当当前威胁结束时，故事终结。"
                    )
                }
            ),
        )
        ending_target = SourceCoverageSupplementTarget(
            source_block_id="block-1",
            requirement_key="ending_rule",
            acceptable_record_kinds=("endings",),
            required_additional_count=1,
            reason="source ending",
        )
        targets = (ending_target, check_target)
        service = SimpleNamespace(
            evidence_compiler=_FixedCoverageCompiler(targets),
            coverage_supplement_groups=lambda evidence, targets: (
                (evidence, targets),
            ),
        )
        adapter = _ProducerThenEndingAdapter()
        candidate = EvidenceBoundContractCandidate(
            contract=ScenarioContract(
                contract_id="review-current",
                source_version=1,
                ruleset_id="coc7",
                title="Current candidate",
                initial_facts={"ready": False},
            ),
            confidence="high",
            evidence_blocks=tuple(item.evidence_block() for item in evidence),
        )
        if recover_producer:
            groups = _ending_last_coverage_groups(
                service.coverage_supplement_groups(evidence, targets)
            )
            repo.ensure_scenario_contract_supplements(
                job["id"],
                expected_attempt=claimed["attempt_count"],
                groups=groups,
                supplement_cycle=1,
            )
            producer = repo.next_scenario_contract_supplement(
                job["id"],
                expected_attempt=claimed["attempt_count"],
                supplement_cycle=1,
            )
            assert producer is not None
            producer_batch = ScenarioIrBatch.model_validate({
                "actions": [{
                    "id": "finish-current-threat",
                    "title": "结束当前威胁",
                    "policy": "automatic",
                    "preconditions": [{
                        "path": "facts.ready", "operator": "eq", "value": False,
                    }],
                    "on_success": [{
                        "kind": "set_fact", "path": "threat_finished", "value": True,
                    }],
                    "source_block_ids": ["block-1"],
                }],
            })
            repo.complete_scenario_contract_supplement(
                job["id"],
                producer["supplement_index"],
                expected_attempt=claimed["attempt_count"],
                batch=producer_batch,
                model_attempt_count=1,
            )

        refreshed, _ = _run_candidate_coverage_supplements(
            connection,
            repo,
            service,
            adapter,
            claimed,
            expected_attempt=claimed["attempt_count"],
            evidence=evidence,
            candidate=candidate,
            supplement_cycle=1,
            repair_observer=lambda plan, attempt: None,
            revalidate_model=lambda: None,
            compile_candidate=service.evidence_compiler.compile,
        )

        assert [item.operator_id for item in refreshed.contract.operators] == [
            "finish-current-threat"
        ]
        assert [item.ending_id for item in refreshed.contract.endings] == [
            "ending-current-threat"
        ]
        assert adapter.ending_catalog_operator_ids == [("finish-current-threat",)]


def test_job_compilation_memo_uses_the_complete_candidate_identity() -> None:
    evidence = _coverage_supplement_input()[0]
    base = EvidenceBoundContractCandidate(
        contract=ScenarioContract(
            contract_id="memo-candidate",
            source_version=1,
            ruleset_id="coc7",
            title="Memo candidate",
        ),
        confidence="high",
        evidence_blocks=tuple(item.evidence_block() for item in evidence),
    )
    calls: list[EvidenceBoundContractCandidate] = []

    def compile_candidate(candidate):
        calls.append(candidate)
        return SimpleNamespace(candidate=candidate)

    memo = _JobCompilationMemo(compile_candidate)
    first = memo.compile(base)
    assert memo.compile(base) is first
    changed_assumption = base.model_copy(update={"assumptions": ("review changed",)})
    second = memo.compile(changed_assumption)
    assert second is not first
    assert memo.compile(changed_assumption) is second
    assert calls == [base, changed_assumption]


def _ending_no_progress_fixture():
    evidence, _ = _coverage_supplement_input()
    target = SourceCoverageSupplementTarget(
        source_block_id="block-1",
        requirement_key="ending_rule",
        acceptable_record_kinds=("endings",),
        required_additional_count=1,
        reason="source ending",
    )
    adapter = ConstrainedScenarioContractAuthoringAdapter(_ValidPartitionLlm())
    candidate = EvidenceBoundContractCandidate(
        contract=ScenarioContract(
            contract_id="stalled-ending",
            source_version=1,
            ruleset_id="coc7",
            title="Stalled ending",
        ),
        confidence="high",
        evidence_blocks=tuple(item.evidence_block() for item in evidence),
    )
    review = ScenarioContractReview(
        decision="reject",
        review_kind="deterministic_compiler",
        findings=("Coverage cycle made no progress.",),
    )
    guard = {
        "fingerprint": coverage_attempt_fingerprint(
            adapter, candidate, evidence, (target,)
        ),
        "blocking_targets": [target.model_dump(mode="json")],
        "review": review.model_dump(mode="json"),
        "compilation": EvidenceBoundScenarioCompiler()
        .compile(candidate)
        .model_dump(mode="json"),
    }
    return adapter, candidate, evidence, guard, review


def test_unchanged_coverage_authority_skips_another_bounded_attempt() -> None:
    adapter, candidate, evidence, guard, review = _ending_no_progress_fixture()

    restored = restore_coverage_no_progress(
        guard,
        adapter=adapter,
        candidate=candidate,
        evidence=evidence,
    )
    assert restored is not None
    restored_review, compilation = restored
    assert restored_review == review
    compile_calls = 0

    def compile_candidate(_candidate):
        nonlocal compile_calls
        compile_calls += 1
        return compilation

    memo = _JobCompilationMemo(compile_candidate)
    memo.seed(candidate, compilation)
    assert memo.compile(candidate) == compilation
    assert compile_calls == 0


def test_coverage_no_progress_guard_retries_after_a_producer_is_added() -> None:
    adapter, candidate, evidence, guard, _ = _ending_no_progress_fixture()
    producer = ScenarioIrBatch.model_validate(
        {
            "actions": [
                {
                    "id": "finish-threat",
                    "title": "Finish threat",
                    "policy": "automatic",
                    "on_success": [
                        {
                            "kind": "set_fact",
                            "path": "threat_finished",
                            "value": True,
                        }
                    ],
                    "source_block_ids": ["block-1"],
                }
            ]
        }
    )
    repaired = adapter.merge_supplement_batches(candidate, evidence, (producer,))

    assert restore_coverage_no_progress(
        guard,
        adapter=adapter,
        candidate=repaired,
        evidence=evidence,
    ) is None


def test_recovery_resumes_after_the_last_completed_partition(tmp_path: Path) -> None:
    db_path = tmp_path / "scenario-contract-resume.sqlite3"
    with db_session(db_path) as connection:
        repo = Repository(connection)
        queued = _create_job(repo)
        claimed = repo.claim_next_scenario_contract_job(worker_id="worker-one")
        assert claimed is not None
        first = repo.next_scenario_contract_partition(
            queued["id"], expected_attempt=claimed["attempt_count"]
        )
        assert first is not None
        assert first["partition_index"] == 0
        repo.complete_scenario_contract_partition(
            queued["id"],
            0,
            expected_attempt=claimed["attempt_count"],
            batch=ScenarioIrBatch(confidence="high"),
        )

    with db_session(db_path) as connection:
        repo = Repository(connection)
        assert repo.recover_interrupted_scenario_contract_jobs() == 1
        resumed = repo.claim_next_scenario_contract_job(worker_id="worker-two")
        assert resumed is not None
        assert resumed["progress_current"] == 1
        second = repo.next_scenario_contract_partition(
            queued["id"], expected_attempt=resumed["attempt_count"]
        )
        assert second is not None
        assert second["partition_index"] == 1


def test_model_generation_change_discards_all_authored_intermediate_state(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "scenario-contract-model-generation.sqlite3") as connection:
        repo = Repository(connection)
        job = _create_job(repo)
        first_model = repo.save_model_configuration(
            provider_type="openai_compatible",
            base_url="http://model-a.test/v1",
            api_key="a",
            model="small-a",
            local_model_path=None,
            local_port=8011,
            semantic_profile="small",
        )
        claimed = repo.claim_next_scenario_contract_job(worker_id="model-worker")
        assert claimed is not None
        bound = repo.bind_scenario_contract_job_model_generation(
            job["id"],
            expected_attempt=claimed["attempt_count"],
            model_configuration_version=first_model["version"],
        )
        partition = repo.next_scenario_contract_partition(
            job["id"], expected_attempt=bound["attempt_count"]
        )
        assert partition is not None
        repo.complete_scenario_contract_partition(
            job["id"],
            int(partition["partition_index"]),
            expected_attempt=bound["attempt_count"],
            batch=ScenarioIrBatch(confidence="high"),
        )
        assert repo.get_scenario_contract_job(job["id"])["progress_current"] == 1

        second_model = repo.save_model_configuration(
            provider_type="openai_compatible",
            base_url="http://model-b.test/v1",
            api_key="b",
            model="small-b",
            local_model_path=None,
            local_port=8011,
            semantic_profile="small",
        )
        rebound = repo.bind_scenario_contract_job_model_generation(
            job["id"],
            expected_attempt=bound["attempt_count"],
            model_configuration_version=second_model["version"],
        )
        assert rebound["model_configuration_version"] == second_model["version"]
        assert rebound["attempt_count"] == 1
        assert rebound["progress_current"] == 0
        rows = connection.execute(
            "SELECT status, result_json FROM scenario_contract_job_partitions "
            "WHERE job_id = ? ORDER BY partition_index",
            (job["id"],),
        ).fetchall()
        assert [(row["status"], row["result_json"]) for row in rows] == [
            ("queued", None),
            ("queued", None),
        ]


def test_worker_restarts_contract_from_source_after_inflight_model_switch(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "scenario-contract-inflight-model-switch.sqlite3"
    settings = Settings(db_path=db_path)
    with db_session(db_path) as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Model switch")
        module = repo.create_module(
            campaign["id"],
            "Generic room",
            [
                ModuleChunk(
                    title="Room",
                    text="The room contains something inspectable.",
                    visibility="kp",
                    spoiler_tag=None,
                    scene_key="room",
                    order_index=0,
                )
            ],
        )
        model_a = repo.save_model_configuration(
            provider_type="openai_compatible",
            base_url="http://model-a.test/v1",
            api_key="a",
            model="model-a",
            local_model_path=None,
            local_port=8011,
            semantic_profile="small",
        )
        prepared = ScenarioContractService(repo).prepare_generation(module["id"])
        job = repo.create_scenario_contract_job(
            campaign_id=campaign["id"],
            module_id=module["id"],
            run_id=None,
            ruleset_id="coc7",
            automation_level="balanced",
            created_by_member_id=None,
            source_fingerprint=prepared.source_fingerprint,
            contract_key="module-model-switch",
            title=module["title"],
            evidence_partitions=ConstrainedScenarioContractAuthoringAdapter.partitions(
                prepared.evidence
            ),
            corpus_total_block_count=prepared.corpus_total_block_count,
            corpus_truncated=prepared.corpus_truncated,
        )
        first_claim = repo.claim_next_scenario_contract_job(worker_id="worker-a")
        assert first_claim is not None
        connection.commit()

        with patch(
            "ai_kp.infrastructure.scenario_contract_worker.OpenAICompatibleClient",
            return_value=_SwitchModelDuringPartitionLlm(db_path),
        ):
            process_claimed_scenario_contract_job(
                connection,
                first_claim,
                settings=settings,
            )

        interrupted = repo.get_scenario_contract_job(job["id"])
        assert interrupted["status"] == "retry_wait"
        assert interrupted["model_configuration_version"] == model_a["version"]
        assert "Model configuration changed" in interrupted["last_error"]
        connection.execute(
            "UPDATE scenario_contract_jobs SET next_run_at = NULL WHERE id = ?",
            (job["id"],),
        )
        second_claim = repo.claim_next_scenario_contract_job(worker_id="worker-b")
        assert second_claim is not None
        connection.commit()

        with patch(
            "ai_kp.infrastructure.scenario_contract_worker.OpenAICompatibleClient",
            return_value=_ValidPartitionLlm(),
        ):
            process_claimed_scenario_contract_job(
                connection,
                second_claim,
                settings=settings,
            )

        completed = repo.get_scenario_contract_job(job["id"])
        assert completed["status"] == "succeeded"
        assert completed["model_configuration_version"] == model_a["version"] + 1
        assert completed["attempt_count"] == 1
        assert completed["result"]["model"] == "model-b"


def test_duplicate_enqueue_returns_the_existing_active_job(tmp_path: Path) -> None:
    with db_session(tmp_path / "scenario-contract-deduplicate.sqlite3") as connection:
        repo = Repository(connection)
        first = _create_job(repo)
        active = repo.create_scenario_contract_job(
            campaign_id=first["campaign_id"],
            module_id=first["module_id"],
            run_id=None,
            ruleset_id="different-ruleset",
            automation_level="ai_kp",
            created_by_member_id=None,
            source_fingerprint="b" * 64,
            contract_key="module-b",
            title="Ignored duplicate",
            evidence_partitions=(
                (
                    ScenarioAuthoringEvidence(
                        source_block_id="other",
                        document_id="other",
                        text="Other evidence.",
                    ),
                ),
            ),
            corpus_total_block_count=1,
            corpus_truncated=False,
        )
        assert active["id"] == first["id"]


def test_rejected_full_ai_review_can_resume_without_requeueing_partitions(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "scenario-contract-review-resume.sqlite3") as connection:
        repo = Repository(connection)
        job = _create_job(repo)
        connection.execute(
            "UPDATE scenario_contract_jobs SET automation_level = 'ai_kp' WHERE id = ?",
            (job["id"],),
        )
        claimed = repo.claim_next_scenario_contract_job(worker_id="review-worker")
        assert claimed is not None
        for index in range(2):
            assert repo.next_scenario_contract_partition(
                job["id"], expected_attempt=claimed["attempt_count"]
            ) is not None
            repo.complete_scenario_contract_partition(
                job["id"],
                index,
                expected_attempt=claimed["attempt_count"],
                batch=ScenarioIrBatch(confidence="high"),
            )
        checkpoint = {
            "contract": {
                "contract_id": "checkpoint", "source_version": 1,
                "ruleset_id": "coc7", "title": "Checkpoint",
                "locations": [], "location_links": [], "entities": [],
                "clocks": [], "resources": [], "clues": [], "operators": [],
                "task_methods": [], "reactive_policies": [],
                "consequence_signals": [], "endings": [],
            },
            "confidence": "high", "assumptions": [],
            "evidence_blocks": [{
                "source_block_id": "block-1", "document_id": "document-1",
                "text_hash": "a" * 64, "semantic_kind": "text",
                "classification_confidence": 0.5, "coverage_requirements": [],
            }],
        }
        repo.complete_scenario_contract_job(
            job["id"],
            expected_attempt=claimed["attempt_count"],
            result={
                "authoring": {"review": {"decision": "reject"}},
                "auto_published": False,
                "review_checkpoint": checkpoint,
            },
        )

        resumed = repo.resume_rejected_scenario_contract_review(
            job["module_id"], job["source_fingerprint"]
        )

        assert resumed is not None
        assert resumed["id"] != job["id"]
        assert resumed["status"] == "queued"
        assert resumed["progress_current"] == resumed["progress_total"] == 2
        assert resumed["result"] == {
            "review_checkpoint": checkpoint,
            "review_progress": {
                "history": [],
                "completed_coverage_cycle": 0,
                "pending_coverage_cycle": None,
                "coverage_cycle_limit": 3,
                "review_epoch_start": 0,
            },
        }
        assert repo.get_scenario_contract_job(job["id"])["status"] == "succeeded"
        assert (
            repo.resume_rejected_scenario_contract_review(
                job["module_id"], job["source_fingerprint"]
            )["id"]
            == resumed["id"]
        )
        reclaimed = repo.claim_next_scenario_contract_job(worker_id="resume-worker")
        assert reclaimed is not None
        assert repo.next_scenario_contract_partition(
            resumed["id"], expected_attempt=reclaimed["attempt_count"]
        ) is None


def test_review_resume_recovers_latest_completed_supplement_cycle(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "scenario-review-cycle-resume.sqlite3") as connection:
        repo = Repository(connection)
        job = _create_job(repo)
        connection.execute(
            "UPDATE scenario_contract_jobs SET automation_level = 'ai_kp' WHERE id = ?",
            (job["id"],),
        )
        claimed = repo.claim_next_scenario_contract_job(worker_id="review-worker")
        assert claimed is not None
        evidence, target = _coverage_supplement_input()
        for cycle in (1, 2):
            repo.ensure_scenario_contract_supplements(
                job["id"], expected_attempt=claimed["attempt_count"],
                groups=((evidence, (target,)),), supplement_cycle=cycle,
            )
        checkpoint = {
            "contract": {
                "contract_id": "checkpoint", "source_version": 1,
                "ruleset_id": "coc7", "title": "Checkpoint",
                "locations": [], "location_links": [], "entities": [],
                "clocks": [], "resources": [], "clues": [], "operators": [],
                "task_methods": [], "reactive_policies": [],
                "consequence_signals": [], "endings": [],
            },
            "confidence": "high", "assumptions": [],
            "evidence_blocks": [{
                "source_block_id": "block-1", "document_id": "document-1",
                "text_hash": "a" * 64, "semantic_kind": "text",
                "classification_confidence": 0.5, "coverage_requirements": [],
            }],
        }
        repo.complete_scenario_contract_job(
            job["id"], expected_attempt=claimed["attempt_count"],
            result={
                "authoring": {
                    "review": {"decision": "reject"},
                    "review_history": [{"decision": "reject"}],
                },
                "auto_published": False,
                "review_checkpoint": checkpoint,
            },
        )

        resumed = repo.resume_rejected_scenario_contract_review(
            job["module_id"], job["source_fingerprint"]
        )

        assert resumed is not None
        assert resumed["id"] != job["id"]
        assert resumed["result"]["review_progress"] == {
            "history": [{"decision": "reject"}],
            "completed_coverage_cycle": 2,
            "pending_coverage_cycle": None,
            "coverage_cycle_limit": 5,
            "review_epoch_start": 1,
        }


def test_startup_auto_resumes_unfinished_full_ai_review_with_fixed_budget(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "scenario-auto-review-resume.sqlite3") as connection:
        repo = Repository(connection)
        job = _create_job(repo)
        connection.execute(
            "UPDATE scenario_contract_jobs SET automation_level = 'ai_kp' WHERE id = ?",
            (job["id"],),
        )
        claimed = repo.claim_next_scenario_contract_job(worker_id="review-worker")
        assert claimed is not None
        evidence, target = _coverage_supplement_input()
        repo.ensure_scenario_contract_supplements(
            job["id"],
            expected_attempt=claimed["attempt_count"],
            groups=((evidence, (target,)),),
            supplement_cycle=20,
        )
        checkpoint = {
            "contract": {
                "contract_id": "checkpoint", "source_version": 1,
                "ruleset_id": "coc7", "title": "Checkpoint",
                "locations": [], "location_links": [], "entities": [],
                "clocks": [], "resources": [], "clues": [], "operators": [],
                "task_methods": [], "reactive_policies": [],
                "consequence_signals": [], "endings": [],
            },
            "confidence": "high", "assumptions": [],
            "evidence_blocks": [{
                "source_block_id": "block-1", "document_id": "document-1",
                "text_hash": "a" * 64, "semantic_kind": "text",
                "classification_confidence": 0.5, "coverage_requirements": [],
            }],
        }
        repo.complete_scenario_contract_job(
            job["id"], expected_attempt=claimed["attempt_count"],
            result={
                "authoring": {
                    "review": {"decision": "reject"},
                    "review_history": [{"decision": "reject"}] * 4,
                },
                "auto_published": False,
                "review_checkpoint": checkpoint,
            },
        )

        assert repo.resume_unfinished_full_ai_reviews(
            max_review_count=16, max_coverage_cycle=12
        ) == 1
        resumed = repo.get_scenario_contract_job(job["id"])
        assert resumed["status"] == "queued"
        assert resumed["result"]["review_progress"] == {
            "history": [{"decision": "reject"}] * 4,
            "completed_coverage_cycle": 20,
            "pending_coverage_cycle": None,
            "coverage_cycle_limit": 12,
            "authority_revision": 0,
            "review_epoch_start": 0,
        }

        connection.execute(
            "UPDATE scenario_contract_jobs SET status = 'succeeded', "
            "result_json = json_set(result_json, '$.review_progress.history', "
            "json(?)) WHERE id = ?",
            (json.dumps([{"decision": "reject"}] * 16), job["id"]),
        )
        assert repo.resume_unfinished_full_ai_reviews(
            max_review_count=16, max_coverage_cycle=12
        ) == 0
        assert repo.get_scenario_contract_job(job["id"])["status"] == "succeeded"

        assert repo.resume_unfinished_full_ai_reviews(
            max_review_count=16,
            max_coverage_cycle=12,
            authority_revision=1,
        ) == 1
        migrated = repo.get_scenario_contract_job(job["id"])
        assert migrated["result"]["review_progress"]["coverage_cycle_limit"] == 23
        assert migrated["result"]["review_progress"]["rebuild_required"] is True
        assert migrated["result"]["review_checkpoint"] is None

        connection.execute(
            "UPDATE scenario_contract_jobs SET status = 'failed' WHERE id = ?",
            (job["id"],),
        )
        assert repo.resume_unfinished_full_ai_reviews(
            max_review_count=16,
            max_coverage_cycle=12,
            authority_revision=1,
        ) == 0
        assert repo.resume_unfinished_full_ai_reviews(
            max_review_count=16,
            max_coverage_cycle=12,
            authority_revision=2,
        ) == 1
        failed_migration = repo.get_scenario_contract_job(job["id"])
        assert failed_migration["status"] == "queued"
        assert failed_migration["result"]["review_progress"]["authority_revision"] == 2
        assert failed_migration["result"]["review_progress"]["review_epoch_start"] == 16
        assert failed_migration["result"]["review_checkpoint"] is None
        connection.execute(
            "UPDATE scenario_contract_jobs SET status = 'succeeded' WHERE id = ?",
            (job["id"],),
        )
        assert repo.resume_unfinished_full_ai_reviews(
            max_review_count=16,
            max_coverage_cycle=12,
            authority_revision=2,
        ) == 1


def test_completed_partition_persists_record_repair_diagnostics(tmp_path: Path) -> None:
    with db_session(tmp_path / "scenario-contract-repairs.sqlite3") as connection:
        repo = Repository(connection)
        job = _create_job(repo)
        claimed = repo.claim_next_scenario_contract_job(worker_id="repair-worker")
        assert claimed is not None
        partition = repo.next_scenario_contract_partition(
            job["id"], expected_attempt=claimed["attempt_count"]
        )
        assert partition is not None
        diagnostic = ScenarioIrRepairDiagnostic(
            partition_index=0,
            model_attempt=2,
            group="actions",
            record_index=1,
            status="applied",
            validation_errors=("policy: invalid value",),
        )
        repo.complete_scenario_contract_partition(
            job["id"],
            0,
            expected_attempt=claimed["attempt_count"],
            batch=ScenarioIrBatch(confidence="high"),
            model_attempt_count=2,
            repair_diagnostics=(diagnostic,),
        )

        attempts, errors, repairs = repo.get_scenario_contract_job_authoring_stats(
            job["id"]
        )

        assert attempts == 2
        assert errors == ()
        assert repairs == (diagnostic,)


def test_coverage_supplement_plan_is_immutable_persisted_and_counted(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "scenario-contract-supplements.sqlite3") as connection:
        repo = Repository(connection)
        job = _create_job(repo)
        claimed = repo.claim_next_scenario_contract_job(worker_id="coverage-worker")
        assert claimed is not None
        evidence, target = _coverage_supplement_input()

        assert repo.ensure_scenario_contract_supplements(
            job["id"],
            expected_attempt=claimed["attempt_count"],
            groups=((evidence, (target,)),),
        ) == 1
        assert repo.ensure_scenario_contract_supplements(
            job["id"],
            expected_attempt=claimed["attempt_count"],
            groups=((evidence, (target,)),),
        ) == 1
        changed_target = target.model_copy(
            update={"required_additional_count": 2}
        )
        with pytest.raises(
            ValueError, match="supplement plan no longer matches"
        ):
            repo.ensure_scenario_contract_supplements(
                job["id"],
                expected_attempt=claimed["attempt_count"],
                groups=((evidence, (changed_target,)),),
            )
        supplement = repo.next_scenario_contract_supplement(
            job["id"], expected_attempt=claimed["attempt_count"]
        )
        assert supplement is not None
        assert supplement["coverage_targets"] == (target,)
        repo.complete_scenario_contract_supplement(
            job["id"],
            0,
            expected_attempt=claimed["attempt_count"],
            batch=ScenarioIrBatch(
                actions=({
                    "id": "spot-ticket",
                    "title": "Spot ticket",
                    "policy": "required_check",
                    "abstract_checks": [{"term": "侦查", "reason": "source"}],
                    "source_block_ids": ["block-1"],
                },)
            ),
            model_attempt_count=1,
        )

        current = repo.get_scenario_contract_job(job["id"])
        assert current["progress_total"] == 3
        assert repo.load_scenario_contract_supplement_batches(job["id"])[0].actions
        connection.execute(
            """
            UPDATE scenario_contract_job_supplements
            SET payload_hash = ? WHERE job_id = ? AND supplement_index = 0
            """,
            ("0" * 64, job["id"]),
        )
        with pytest.raises(ValueError, match="payload hash mismatch"):
            repo.load_scenario_contract_supplement_batches(job["id"])


def test_coverage_supplement_cycles_are_independently_resumable(tmp_path: Path) -> None:
    with db_session(tmp_path / "scenario-contract-supplement-cycles.sqlite3") as connection:
        repo = Repository(connection)
        job = _create_job(repo)
        claimed = repo.claim_next_scenario_contract_job(worker_id="coverage-worker")
        assert claimed is not None
        evidence, target = _coverage_supplement_input()
        for cycle in (0, 1):
            assert repo.ensure_scenario_contract_supplements(
                job["id"],
                expected_attempt=claimed["attempt_count"],
                groups=((evidence, (target,)),),
                supplement_cycle=cycle,
            ) == 1
            supplement = repo.next_scenario_contract_supplement(
                job["id"],
                expected_attempt=claimed["attempt_count"],
                supplement_cycle=cycle,
            )
            assert supplement is not None
            assert supplement["supplement_cycle"] == cycle
            repo.complete_scenario_contract_supplement(
                job["id"],
                supplement["supplement_index"],
                expected_attempt=claimed["attempt_count"],
                batch=ScenarioIrBatch(confidence="high"),
                model_attempt_count=1,
            )

        rows = connection.execute(
            "SELECT supplement_cycle, supplement_index FROM "
            "scenario_contract_job_supplements WHERE job_id = ? "
            "ORDER BY supplement_index",
            (job["id"],),
        ).fetchall()
        assert [tuple(row) for row in rows] == [(0, 0), (1, 1)]
        assert len(repo.load_scenario_contract_supplement_batches(
            job["id"], supplement_cycle=1
        )) == 1
        assert _settled_coverage_supplement_summary(
            repo,
            job["id"],
            supplement_cycle=0,
            target_count=1,
            expected_partition_count=1,
        ) == {
            "target_count": 1,
            "partition_count": 1,
            "completed_partition_count": 1,
            "failed_partition_count": 0,
        }
        assert _settled_coverage_supplement_summary(
            repo,
            job["id"],
            supplement_cycle=1,
            target_count=1,
            expected_partition_count=1,
        ) == {
            "target_count": 1,
            "partition_count": 1,
            "completed_partition_count": 1,
            "failed_partition_count": 0,
        }


def test_recovery_requeues_only_an_interrupted_coverage_supplement(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "scenario-contract-supplement-recovery.sqlite3"
    with db_session(db_path) as connection:
        repo = Repository(connection)
        job = _create_job(repo)
        claimed = repo.claim_next_scenario_contract_job(worker_id="coverage-worker")
        assert claimed is not None
        evidence, target = _coverage_supplement_input()
        repo.ensure_scenario_contract_supplements(
            job["id"],
            expected_attempt=claimed["attempt_count"],
            groups=((evidence, (target,)),),
        )
        running = repo.next_scenario_contract_supplement(
            job["id"], expected_attempt=claimed["attempt_count"]
        )
        assert running is not None

    with db_session(db_path) as connection:
        repo = Repository(connection)
        assert repo.recover_interrupted_scenario_contract_jobs() == 1
        resumed = repo.claim_next_scenario_contract_job(worker_id="recovered-worker")
        assert resumed is not None
        supplement = repo.next_scenario_contract_supplement(
            job["id"], expected_attempt=resumed["attempt_count"]
        )
        assert supplement is not None
        assert supplement["attempt_count"] == 2
        with pytest.raises(RuntimeError, match="unsettled partitions"):
            _settled_coverage_supplement_summary(
                repo,
                job["id"],
                supplement_cycle=0,
                target_count=1,
                expected_partition_count=1,
            )
        repo.complete_scenario_contract_supplement(
            job["id"],
            supplement["supplement_index"],
            expected_attempt=resumed["attempt_count"],
            batch=ScenarioIrBatch(confidence="high"),
            model_attempt_count=1,
        )
        summary = _settled_coverage_supplement_summary(
            repo,
            job["id"],
            supplement_cycle=0,
            target_count=1,
            expected_partition_count=1,
        )
        assert summary["completed_partition_count"] == 1
        assert summary["failed_partition_count"] == 0


def test_failed_coverage_supplement_advances_terminal_progress(tmp_path: Path) -> None:
    with db_session(tmp_path / "scenario-contract-supplement-progress.sqlite3") as connection:
        repo = Repository(connection)
        job = _create_job(repo)
        claimed = repo.claim_next_scenario_contract_job(worker_id="coverage-worker")
        assert claimed is not None
        evidence, target = _coverage_supplement_input()
        repo.ensure_scenario_contract_supplements(
            job["id"],
            expected_attempt=claimed["attempt_count"],
            groups=((evidence, (target,)),),
        )
        supplement = repo.next_scenario_contract_supplement(
            job["id"], expected_attempt=claimed["attempt_count"]
        )
        assert supplement is not None

        repo.fail_scenario_contract_supplement(
            job["id"],
            0,
            expected_attempt=claimed["attempt_count"],
            error="bounded semantic failure",
            model_attempt_count=3,
        )

        current = repo.get_scenario_contract_job(job["id"])
        assert current["progress_current"] == 1
        assert current["progress_total"] == 3


def test_automatic_job_retry_preserves_semantically_failed_supplements(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "scenario-contract-supplement-auto-retry.sqlite3") as connection:
        repo = Repository(connection)
        job = _create_job(repo)
        claimed = repo.claim_next_scenario_contract_job(worker_id="coverage-worker")
        assert claimed is not None
        evidence, target = _coverage_supplement_input()
        repo.ensure_scenario_contract_supplements(
            job["id"],
            expected_attempt=claimed["attempt_count"],
            groups=((evidence, (target,)),),
        )
        supplement = repo.next_scenario_contract_supplement(
            job["id"], expected_attempt=claimed["attempt_count"]
        )
        assert supplement is not None
        repo.fail_scenario_contract_supplement(
            job["id"],
            0,
            expected_attempt=claimed["attempt_count"],
            error="bounded semantic failure",
            model_attempt_count=3,
        )
        repo.fail_scenario_contract_job(
            job["id"],
            expected_attempt=claimed["attempt_count"],
            error="transient independent review failure",
        )
        connection.execute(
            "UPDATE scenario_contract_jobs SET next_run_at = CURRENT_TIMESTAMP WHERE id = ?",
            (job["id"],),
        )

        retried = repo.claim_next_scenario_contract_job(worker_id="retry-worker")
        assert retried is not None
        assert retried["attempt_count"] == 2
        assert retried["progress_current"] == 1
        assert repo.next_scenario_contract_supplement(
            job["id"], expected_attempt=retried["attempt_count"]
        ) is None
        status = connection.execute(
            "SELECT status FROM scenario_contract_job_supplements WHERE job_id = ?",
            (job["id"],),
        ).fetchone()["status"]
        assert status == "failed"


def test_terminal_job_failure_closes_a_running_supplement(tmp_path: Path) -> None:
    with db_session(tmp_path / "scenario-contract-terminal-child.sqlite3") as connection:
        repo = Repository(connection)
        job = _create_job(repo)
        connection.execute(
            "UPDATE scenario_contract_jobs SET max_attempts = 1 WHERE id = ?",
            (job["id"],),
        )
        claimed = repo.claim_next_scenario_contract_job(worker_id="coverage-worker")
        assert claimed is not None
        evidence, target = _coverage_supplement_input()
        repo.ensure_scenario_contract_supplements(
            job["id"],
            expected_attempt=claimed["attempt_count"],
            groups=((evidence, (target,)),),
        )
        assert repo.next_scenario_contract_supplement(
            job["id"], expected_attempt=claimed["attempt_count"]
        ) is not None

        failed = repo.fail_scenario_contract_job(
            job["id"],
            expected_attempt=claimed["attempt_count"],
            error="LLM HTTP 502",
        )

        assert failed["status"] == "failed"
        assert failed["progress_current"] == 1
        child = connection.execute(
            "SELECT status, last_error FROM scenario_contract_job_supplements "
            "WHERE job_id = ?",
            (job["id"],),
        ).fetchone()
        assert child["status"] == "failed"
        assert child["last_error"] == "LLM HTTP 502"

        retried = repo.retry_scenario_contract_job(job["id"])

        assert retried["status"] == "queued"
        assert retried["progress_current"] == 0
        child_status = connection.execute(
            "SELECT status FROM scenario_contract_job_supplements WHERE job_id = ?",
            (job["id"],),
        ).fetchone()["status"]
        assert child_status == "queued"


def test_failed_partition_keeps_repair_diagnostics_for_manual_retry(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "scenario-contract-failed-repairs.sqlite3") as connection:
        repo = Repository(connection)
        job = _create_job(repo)
        connection.execute(
            "UPDATE scenario_contract_jobs SET max_attempts = 1 WHERE id = ?",
            (job["id"],),
        )
        claimed = repo.claim_next_scenario_contract_job(worker_id="repair-worker")
        assert claimed is not None
        partition = repo.next_scenario_contract_partition(
            job["id"], expected_attempt=claimed["attempt_count"]
        )
        assert partition is not None
        diagnostic = ScenarioIrRepairDiagnostic(
            partition_index=0,
            model_attempt=2,
            group="actions",
            record_index=0,
            status="failed",
            validation_errors=("policy: invalid value",),
        )
        repo.fail_scenario_contract_partition(
            job["id"],
            0,
            expected_attempt=claimed["attempt_count"],
            error="replacement remained invalid",
            model_attempt_count=3,
            validation_errors=("replacement remained invalid",),
            repair_diagnostics=(diagnostic,),
        )
        repo.fail_scenario_contract_job(
            job["id"],
            expected_attempt=claimed["attempt_count"],
            error="replacement remained invalid",
        )
        repo.retry_scenario_contract_job(job["id"])
        retried = repo.claim_next_scenario_contract_job(worker_id="retry-worker")
        assert retried is not None

        resumed = repo.next_scenario_contract_partition(
            job["id"], expected_attempt=retried["attempt_count"]
        )

        assert resumed is not None
        assert resumed["repair_diagnostics"] == (diagnostic,)
        assert resumed["validation_errors"] == ("replacement remained invalid",)


def test_source_change_terminally_rejects_a_completed_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "scenario-contract-stale.sqlite3"
    settings = Settings(db_path=db_path)
    with db_session(db_path) as connection:
        repo = Repository(connection)
        repo.save_model_configuration(
            provider_type="openai_compatible",
            base_url="http://selected-worker-model.test/v1",
            api_key="selected-key",
            model="selected-worker-model",
            local_model_path=None,
            local_port=8011,
            semantic_profile="small",
        )
        campaign = repo.create_campaign("Stale source")
        module = repo.create_module(
            campaign["id"],
            "Mutable module",
            [
                ModuleChunk(
                    title="Room",
                    text="The room contains something inspectable.",
                    visibility="kp",
                    spoiler_tag=None,
                    scene_key=None,
                    order_index=0,
                )
            ],
        )
        prepared = ScenarioContractService(repo).prepare_generation(module["id"])
        job = repo.create_scenario_contract_job(
            campaign_id=campaign["id"],
            module_id=module["id"],
            run_id=None,
            ruleset_id="coc7",
            automation_level="balanced",
            created_by_member_id=None,
            source_fingerprint=prepared.source_fingerprint,
            contract_key="module-stale",
            title=module["title"],
            evidence_partitions=ConstrainedScenarioContractAuthoringAdapter.partitions(
                prepared.evidence
            ),
            corpus_total_block_count=prepared.corpus_total_block_count,
            corpus_truncated=prepared.corpus_truncated,
        )
        connection.execute(
            "UPDATE module_chunks SET text = 'The source changed.' WHERE module_id = ?",
            (module["id"],),
        )
        claimed = repo.claim_next_scenario_contract_job(worker_id="stale-worker")
        assert claimed is not None
        connection.commit()

        captured_client: dict[str, str] = {}

        def selected_client(base_url: str, api_key: str, model: str, **_kwargs):
            captured_client.update(
                base_url=base_url,
                api_key=api_key,
                model=model,
            )
            return _ValidPartitionLlm()

        with patch(
            "ai_kp.infrastructure.scenario_contract_worker.OpenAICompatibleClient",
            side_effect=selected_client,
        ):
            process_claimed_scenario_contract_job(connection, claimed, settings=settings)

        failed = repo.get_scenario_contract_job(job["id"])
        assert captured_client == {
            "base_url": "http://selected-worker-model.test/v1",
            "api_key": "selected-key",
            "model": "selected-worker-model",
        }
        assert failed["status"] == "failed"
        assert failed["retryable"] is False
        assert "source changed" in failed["last_error"]


def test_final_source_recheck_detects_changes_during_deterministic_compile(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "scenario-contract-compile-toctou.sqlite3"
    with db_session(db_path) as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Compile TOCTOU")
        module = repo.create_module(
            campaign["id"],
            "Mutable source",
            [
                ModuleChunk(
                    title="Room",
                    text="The room is initially unchanged.",
                    visibility="kp",
                    spoiler_tag=None,
                    scene_key="room",
                    order_index=0,
                )
            ],
        )
        service = ScenarioContractService(repo)
        prepared = service.prepare_generation(module["id"])
        job = {
            "module_id": module["id"],
            "source_fingerprint": prepared.source_fingerprint,
        }
        connection.commit()

        # Simulate a source edit committed by another request while the worker
        # performs its lock-free deterministic compilation.
        with db_session(db_path) as other_connection:
            other_connection.execute(
                "UPDATE module_chunks SET text = ? WHERE module_id = ?",
                ("The room changed during compilation.", module["id"]),
            )

        repo.begin_immediate()
        with pytest.raises(ConflictError, match="source changed"):
            _assert_job_source_unchanged(service, job, prepared.evidence)
        connection.rollback()


def test_full_ai_repairs_one_review_rejection_then_rechecks_before_publish(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "scenario-contract-review-repair.sqlite3"
    settings = Settings(db_path=db_path)
    with db_session(db_path) as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Review repair")
        module = repo.create_module(
            campaign["id"],
            "Bounded repair module",
            [
                ModuleChunk(
                    title="Room",
                    text="The investigator may inspect this room.",
                    visibility="kp",
                    spoiler_tag=None,
                    scene_key="room",
                    order_index=0,
                )
            ],
        )
        service = ScenarioContractService(repo)
        prepared = service.prepare_generation(module["id"])
        job = repo.create_scenario_contract_job(
            campaign_id=campaign["id"],
            module_id=module["id"],
            run_id=None,
            ruleset_id="coc7",
            automation_level="ai_kp",
            created_by_member_id=None,
            source_fingerprint=prepared.source_fingerprint,
            contract_key="module-review-repair",
            title=module["title"],
            evidence_partitions=ConstrainedScenarioContractAuthoringAdapter.partitions(
                prepared.evidence
            ),
            corpus_total_block_count=prepared.corpus_total_block_count,
            corpus_truncated=prepared.corpus_truncated,
        )
        claimed = repo.claim_next_scenario_contract_job(worker_id="review-worker")
        assert claimed is not None
        connection.commit()
        llm = _ReviewRepairLlm()

        with patch(
            "ai_kp.infrastructure.scenario_contract_worker.OpenAICompatibleClient",
            return_value=llm,
        ):
            process_claimed_scenario_contract_job(connection, claimed, settings=settings)

        completed = repo.get_scenario_contract_job(job["id"])
        assert completed["status"] == "succeeded"
        history = completed["result"]["authoring"]["review_history"]
        assert [item["decision"] for item in history] == ["reject", "approve"], history
        assert completed["result"]["auto_published"] is True, completed["result"]
        assert completed["result"]["version"]["status"] == "published"
        operator = completed["result"]["version"]["contract"]["operators"][0]
        assert operator["success_commands"] == []
        assert llm.review_calls == 2


def test_full_ai_shrinks_unsupported_assumption_then_rechecks_before_publish(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "scenario-contract-assumption-shrink.sqlite3"
    settings = Settings(db_path=db_path)
    with db_session(db_path) as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Assumption shrink")
        module = repo.create_module(
            campaign["id"], "Bounded assumption module",
            [ModuleChunk(
                title="Room", text="The investigator may inspect and leave this room.",
                visibility="kp", spoiler_tag=None, scene_key="room", order_index=0,
            )],
        )
        service = ScenarioContractService(repo)
        prepared = service.prepare_generation(module["id"])
        job = repo.create_scenario_contract_job(
            campaign_id=campaign["id"], module_id=module["id"], run_id=None,
            ruleset_id="coc7", automation_level="ai_kp",
            created_by_member_id=None,
            source_fingerprint=prepared.source_fingerprint,
            contract_key="module-assumption-shrink", title=module["title"],
            evidence_partitions=ConstrainedScenarioContractAuthoringAdapter.partitions(
                prepared.evidence
            ),
            corpus_total_block_count=prepared.corpus_total_block_count,
            corpus_truncated=prepared.corpus_truncated,
        )
        claimed = repo.claim_next_scenario_contract_job(worker_id="review-worker")
        assert claimed is not None
        connection.commit()
        llm = _UnsupportedAssumptionReviewLlm()

        with patch(
            "ai_kp.infrastructure.scenario_contract_worker.OpenAICompatibleClient",
            return_value=llm,
        ):
            process_claimed_scenario_contract_job(connection, claimed, settings=settings)

        completed = repo.get_scenario_contract_job(job["id"])
        assert completed["status"] == "succeeded"
        history = completed["result"]["authoring"]["review_history"]
        assert [item["decision"] for item in history] == ["reject", "approve"]
        assert history[0]["unsupported_assumption_indices"] == [0]
        assert completed["result"]["auto_published"] is True
        assert llm.review_calls == 2


def test_full_ai_keeps_ending_incomplete_goal_bounded_supplement_as_draft(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "scenario-contract-post-review-coverage.sqlite3"
    settings = Settings(db_path=db_path)
    with db_session(db_path) as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Post-review coverage")
        module = repo.create_module(
            campaign["id"],
            "Explicit source check",
            [ModuleChunk(
                title="Ticket",
                text="通过侦查检定发现旧车票。",
                visibility="kp",
                spoiler_tag=None,
                scene_key="station",
                order_index=0,
            )],
        )
        prepared = ScenarioContractService(repo).prepare_generation(module["id"])
        job = repo.create_scenario_contract_job(
            campaign_id=campaign["id"], module_id=module["id"], run_id=None,
            ruleset_id="coc7", automation_level="ai_kp",
            created_by_member_id=None,
            source_fingerprint=prepared.source_fingerprint,
            contract_key="module-post-review-coverage", title=module["title"],
            evidence_partitions=ConstrainedScenarioContractAuthoringAdapter.partitions(
                prepared.evidence
            ),
            corpus_total_block_count=prepared.corpus_total_block_count,
            corpus_truncated=False,
        )
        claimed = repo.claim_next_scenario_contract_job(worker_id="review-worker")
        assert claimed is not None
        connection.commit()
        llm = _ReviewDeletesCoverageLlm()

        with patch(
            "ai_kp.infrastructure.scenario_contract_worker.OpenAICompatibleClient",
            return_value=llm,
        ):
            process_claimed_scenario_contract_job(connection, claimed, settings=settings)

        completed = repo.get_scenario_contract_job(job["id"])
        assert completed["result"]["auto_published"] is False
        assert completed["result"]["version"]["status"] == "draft"
        # The server can safely distinguish whether the named action goal was
        # achieved without inventing what the ticket contains.  That closes the
        # check branch, while the missing ending still keeps this version draft.
        assert completed["result"]["compilation"]["coverage"]["coverage_ratio"] == 1
        assert completed["result"]["authoring"]["post_review_coverage"] == []
        operators = completed["result"]["version"]["contract"]["operators"]
        assert any(item["operator_id"].startswith("supp1_") for item in operators)
        assert not any(
            item["operator_id"].startswith("supp2_")
            or item["operator_id"].startswith("supp3_")
            for item in operators
        )
        # The deterministic compiler blocks the structurally incomplete,
        # server-owned supplement before spending an independent-review call.
        assert llm.review_calls == 0
        cycles = connection.execute(
            "SELECT supplement_cycle, status FROM scenario_contract_job_supplements "
            "WHERE job_id = ? ORDER BY supplement_index",
            (job["id"],),
        ).fetchall()
        assert [tuple(item) for item in cycles] == [(0, "succeeded")]


def test_full_ai_stops_at_the_total_review_budget(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "scenario-contract-review-limit.sqlite3"
    settings = Settings(db_path=db_path)
    with db_session(db_path) as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Review limit")
        module = repo.create_module(
            campaign["id"],
            "Bounded review limit",
            [ModuleChunk(
                title="Room",
                text="The investigator may inspect this room.",
                visibility="kp",
                spoiler_tag=None,
                scene_key="room",
                order_index=0,
            )],
        )
        prepared = ScenarioContractService(repo).prepare_generation(module["id"])
        job = repo.create_scenario_contract_job(
            campaign_id=campaign["id"], module_id=module["id"], run_id=None,
            ruleset_id="coc7", automation_level="ai_kp",
            created_by_member_id=None, source_fingerprint=prepared.source_fingerprint,
            contract_key="module-review-limit", title=module["title"],
            evidence_partitions=ConstrainedScenarioContractAuthoringAdapter.partitions(
                prepared.evidence
            ),
            corpus_total_block_count=prepared.corpus_total_block_count,
            corpus_truncated=False,
        )
        claimed = repo.claim_next_scenario_contract_job(worker_id="review-worker")
        assert claimed is not None
        connection.commit()
        llm = _AlwaysRejectReviewLlm()

        with patch(
            "ai_kp.infrastructure.scenario_contract_worker.OpenAICompatibleClient",
            return_value=llm,
        ):
            process_claimed_scenario_contract_job(connection, claimed, settings=settings)

        completed = repo.get_scenario_contract_job(job["id"])
        history = completed["result"]["authoring"]["review_history"]
        assert [item["decision"] for item in history] == ["reject"] * 16
        assert completed["result"]["auto_published"] is False
        assert completed["result"]["version"]["status"] == "draft"
        assert llm.review_calls == 16


def test_worker_goal_bounds_source_named_check_without_inventing_an_effect(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "scenario-contract-coverage-worker.sqlite3"
    settings = Settings(db_path=db_path)
    with db_session(db_path) as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Coverage supplement")
        module = repo.create_module(
            campaign["id"],
            "Explicit check module",
            [
                ModuleChunk(
                    title="Ticket",
                    text="通过侦查检定可以发现旧车票。",
                    visibility="kp",
                    spoiler_tag=None,
                    scene_key="station",
                    order_index=0,
                )
            ],
        )
        prepared = ScenarioContractService(repo).prepare_generation(module["id"])
        assert prepared.evidence[0].coverage_requirements[0].requirement_key == (
            "explicit_checks"
        )
        job = repo.create_scenario_contract_job(
            campaign_id=campaign["id"],
            module_id=module["id"],
            run_id=None,
            ruleset_id="coc7",
            automation_level="balanced",
            created_by_member_id=None,
            source_fingerprint=prepared.source_fingerprint,
            contract_key="module-coverage",
            title=module["title"],
            evidence_partitions=ConstrainedScenarioContractAuthoringAdapter.partitions(
                prepared.evidence
            ),
            corpus_total_block_count=prepared.corpus_total_block_count,
            corpus_truncated=prepared.corpus_truncated,
        )
        claimed = repo.claim_next_scenario_contract_job(worker_id="coverage-worker")
        assert claimed is not None
        connection.commit()

        with patch(
            "ai_kp.infrastructure.scenario_contract_worker.OpenAICompatibleClient",
            return_value=_CoverageSupplementLlm(),
        ):
            process_claimed_scenario_contract_job(connection, claimed, settings=settings)

        completed = repo.get_scenario_contract_job(job["id"])
        assert completed["status"] == "succeeded"
        coverage = completed["result"]["compilation"]["coverage"]
        assert coverage["required_item_count"] == 1
        # A source-named roll without concrete outcome text is executable via
        # the server-owned action-goal boundary.  It does not claim that the
        # ticket was found or reveal any content absent from the source.
        assert coverage["covered_item_count"] == 1
        assert coverage["coverage_ratio"] == 1.0
        summary = completed["result"]["authoring"]["coverage_supplement"]
        assert summary == {
            "target_count": 1,
            "partition_count": 1,
            "completed_partition_count": 1,
            "failed_partition_count": 0,
        }
        assert completed["result"]["version"]["status"] == "draft"
        operator_ids = {
            item["operator_id"]
            for item in completed["result"]["version"]["contract"]["operators"]
        }
        assert operator_ids == {"supp1_action_01"}


def test_worker_goal_bounds_check_when_model_supplement_fails(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "scenario-contract-failed-supplement.sqlite3"
    settings = Settings(db_path=db_path)
    with db_session(db_path) as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Failed supplement remains a draft")
        module = repo.create_module(
            campaign["id"],
            "Explicit check module",
            [
                ModuleChunk(
                    title="Ticket",
                    text="通过侦查检定可以发现旧车票。",
                    visibility="kp",
                    spoiler_tag=None,
                    scene_key="station",
                    order_index=0,
                )
            ],
        )
        prepared = ScenarioContractService(repo).prepare_generation(module["id"])
        job = repo.create_scenario_contract_job(
            campaign_id=campaign["id"],
            module_id=module["id"],
            run_id=None,
            ruleset_id="coc7",
            automation_level="balanced",
            created_by_member_id=None,
            source_fingerprint=prepared.source_fingerprint,
            contract_key="module-failed-supplement",
            title=module["title"],
            evidence_partitions=ConstrainedScenarioContractAuthoringAdapter.partitions(
                prepared.evidence
            ),
            corpus_total_block_count=prepared.corpus_total_block_count,
            corpus_truncated=prepared.corpus_truncated,
        )
        claimed = repo.claim_next_scenario_contract_job(worker_id="coverage-worker")
        assert claimed is not None
        connection.commit()

        with patch(
            "ai_kp.infrastructure.scenario_contract_worker.OpenAICompatibleClient",
            return_value=_CoverageSupplementLlm(fail_supplements=True),
        ):
            process_claimed_scenario_contract_job(connection, claimed, settings=settings)

        completed = repo.get_scenario_contract_job(job["id"])
        assert completed["status"] == "succeeded"
        assert completed["attempt_count"] == 1
        assert completed["result"]["compilation"]["coverage"]["covered_item_count"] == 1
        assert completed["result"]["version"]["status"] == "draft"
        assert completed["result"]["authoring"]["coverage_supplement"] == {
            "target_count": 1,
            "partition_count": 1,
            "completed_partition_count": 1,
            "failed_partition_count": 0,
        }
        assert completed["result"]["authoring"]["validation_errors"] == []
        supplements = connection.execute(
            "SELECT status, model_attempt_count FROM scenario_contract_job_supplements "
            "WHERE job_id = ?",
            (job["id"],),
        ).fetchall()
        assert [(row["status"], row["model_attempt_count"]) for row in supplements] == [
            ("succeeded", 1)
        ]

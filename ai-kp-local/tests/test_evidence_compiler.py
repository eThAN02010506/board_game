from __future__ import annotations

from ai_kp.platform.resolution.check_catalog import ScenarioSourceCheck
from ai_kp.platform.resolution.evidence_compiler import (
    EvidenceBlock,
    EvidenceBoundContractCandidate,
    EvidenceBoundScenarioCompiler,
)
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler
from ai_kp.platform.resolution.scenario_ir import ScenarioIrAssembler
from ai_kp.platform.resolution.scenario_supplement import (
    CoverageActionEnvelope,
    CoverageActionProposal,
    materialize_action_envelope,
)
from ai_kp.platform.resolution.source_coverage import SourceCoverageRequirement
from ai_kp.rulesets.coc7.scenario_checks import coc7_scenario_check_catalog
from ai_kp.rulesets.coc7.scenario_effects import coc7_scenario_effect_catalog
from tests.test_parallel_action_kernel import parallel_contract


def evidence_block() -> EvidenceBlock:
    return EvidenceBlock(
        source_block_id="block-1",
        document_id="document-1",
        page=3,
        paragraph=7,
        text_hash="a" * 64,
    )


def sourced_contract():
    payload = parallel_contract().model_dump(mode="json")
    source_refs = [
        {
            "source_block_id": "block-1",
            "document_id": "document-1",
            "page": 3,
            "paragraph": 7,
        }
    ]
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
        "endings",
    ):
        for item in payload[group]:
            item["source_refs"] = source_refs
    result = ScenarioContractCompiler().compile(payload)
    assert result.contract is not None
    return result.contract


def candidate(**changes: object) -> EvidenceBoundContractCandidate:
    values = {
        "contract": sourced_contract(),
        "confidence": "high",
        "assumptions": (),
        "evidence_blocks": (evidence_block(),),
    }
    values.update(changes)
    return EvidenceBoundContractCandidate.model_validate(values)


def test_high_confidence_exactly_sourced_contract_is_auto_publishable() -> None:
    result = EvidenceBoundScenarioCompiler().compile(candidate())

    assert result.decision == "auto_publishable"
    assert result.report.valid is True
    assert result.contract is not None


def test_ai_authored_location_graph_without_entrypoint_is_rejected() -> None:
    payload = sourced_contract().model_dump(mode="json")
    payload["locations"] = [{
        "location_id": "entry",
        "title": "Entry",
        "source_refs": [{
            "source_block_id": "block-1",
            "document_id": "document-1",
            "page": 3,
            "paragraph": 7,
        }],
    }]
    payload["initial_scene_id"] = None
    contract = ScenarioContractCompiler().compile(payload).contract
    assert contract is not None

    result = EvidenceBoundScenarioCompiler().compile(candidate(contract=contract))

    assert result.decision == "rejected"
    assert "authored_initial_scene_missing" in {
        item.code for item in result.report.issues
    }


def test_assumptions_or_lower_confidence_require_review() -> None:
    result = EvidenceBoundScenarioCompiler().compile(
        candidate(confidence="medium", assumptions=("An ambiguous passage remains.",))
    )

    assert result.decision == "review_required"
    assert result.report.valid is True


def test_missing_or_unknown_executable_provenance_is_rejected() -> None:
    payload = sourced_contract().model_dump(mode="json")
    payload["operators"][0]["source_refs"] = []
    missing_contract = ScenarioContractCompiler().compile(payload).contract
    assert missing_contract is not None

    missing = EvidenceBoundScenarioCompiler().compile(
        candidate(contract=missing_contract)
    )
    assert missing.decision == "rejected"
    assert "missing_executable_provenance" in {
        item.code for item in missing.report.issues
    }

    payload = sourced_contract().model_dump(mode="json")
    payload["operators"][0]["source_refs"][0]["source_block_id"] = "invented"
    unknown_contract = ScenarioContractCompiler().compile(payload).contract
    assert unknown_contract is not None
    unknown = EvidenceBoundScenarioCompiler().compile(
        candidate(contract=unknown_contract)
    )
    assert unknown.decision == "rejected"
    assert "unknown_source_reference" in {item.code for item in unknown.report.issues}


def test_uncovered_source_obligation_prevents_automatic_publication() -> None:
    required = evidence_block().model_copy(
        update={
            "semantic_kind": "ending",
            "classification_confidence": 0.9,
            "coverage_requirements": (
                SourceCoverageRequirement(
                    requirement_key="ending_rule",
                    acceptable_record_kinds=("endings",),
                    reason="Explicit source ending must be executable.",
                ),
            ),
        }
    )
    payload = sourced_contract().model_dump(mode="json")
    payload["endings"] = []
    without_ending = ScenarioContractCompiler().compile(payload).contract
    assert without_ending is not None

    result = EvidenceBoundScenarioCompiler().compile(
        candidate(contract=without_ending, evidence_blocks=(required,))
    )

    assert result.decision == "review_required"
    assert result.report.valid is True
    assert result.coverage.required_item_count == 1
    assert result.coverage.covered_item_count == 0
    assert result.coverage.coverage_ratio == 0
    assert "uncovered_source_mechanic" in {
        item.code for item in result.report.issues
    }


def test_uncovered_advisory_world_materialization_does_not_block_publication() -> None:
    advisory = evidence_block().model_copy(
        update={
            "source_block_id": "block-advisory",
            "semantic_kind": "npc",
            "coverage_requirements": (
                SourceCoverageRequirement(
                    requirement_key="npc_presence",
                    acceptable_record_kinds=("entities", "operators"),
                    blocking=False,
                    reason="Try to materialize a supported NPC.",
                ),
            ),
        }
    )

    result = EvidenceBoundScenarioCompiler().compile(
        candidate(evidence_blocks=(evidence_block(), advisory))
    )

    assert result.coverage.coverage_ratio == 0
    assert result.coverage.blocking_complete is True
    assert result.coverage.supplement_targets()
    assert result.coverage.supplement_targets(blocking_only=True) == ()
    assert result.decision == "auto_publishable"


def test_coverage_counts_distinct_cited_records_of_an_acceptable_kind() -> None:
    required = evidence_block().model_copy(
        update={
            "coverage_requirements": (
                SourceCoverageRequirement(
                    requirement_key="explicit_checks",
                    acceptable_record_kinds=("operators",),
                    minimum_record_count=2,
                    reason="Two explicit checks were found.",
                ),
            ),
        }
    )

    payload = sourced_contract().model_dump(mode="json")
    payload["operators"][1]["policy"] = "required_check"
    payload["operators"][1]["skill_choices"] = payload["operators"][0][
        "skill_choices"
    ]
    payload["operators"][1]["failure_commands"] = [{
        "kind": "set_fact",
        "path": "batch.panel_delayed",
        "value": True,
    }]
    contract = ScenarioContractCompiler().compile(payload).contract
    assert contract is not None
    result = EvidenceBoundScenarioCompiler().compile(
        candidate(contract=contract, evidence_blocks=(required,))
    )

    item = result.coverage.items[0]
    assert item.matched_record_count >= 2
    assert item.covered is True
    assert result.decision == "auto_publishable"


def test_automatic_action_cannot_masquerade_as_an_explicit_check() -> None:
    required = evidence_block().model_copy(
        update={
            "coverage_requirements": (
                SourceCoverageRequirement(
                    requirement_key="explicit_checks",
                    acceptable_record_kinds=("operators",),
                    reason="The source requires an actual check.",
                ),
            ),
        }
    )
    payload = sourced_contract().model_dump(mode="json")
    payload["operators"] = [{
        **payload["operators"][0],
        "policy": "automatic",
        "skill_choices": [],
        "preconditions": [{
            "path": "resources.shared_tokens",
            "operator": "gte",
            "value": 0,
        }],
    }]
    payload["endings"] = []
    contract = ScenarioContractCompiler().compile(payload).contract
    assert contract is not None

    result = EvidenceBoundScenarioCompiler().compile(
        candidate(contract=contract, evidence_blocks=(required,))
    )

    assert result.coverage.items[0].matched_record_count == 0
    assert result.decision == "review_required"


def test_ruleset_effect_obligation_requires_an_executable_effect_command() -> None:
    required = evidence_block().model_copy(
        update={
            "coverage_requirements": (
                SourceCoverageRequirement(
                    requirement_key="explicit_ruleset_effect",
                    acceptable_record_kinds=("operators",),
                    reason="The source declares SAN loss.",
                ),
            ),
        }
    )
    payload = sourced_contract().model_dump(mode="json")
    payload["operators"][0]["success_commands"].append(
        {
            "kind": "apply_ruleset_effect",
            "event_type": "san_loss",
            "payload": {"loss": "1"},
        }
    )
    compiler = ScenarioContractCompiler(coc7_scenario_effect_catalog())
    contract = compiler.compile(payload).contract
    assert contract is not None

    covered = EvidenceBoundScenarioCompiler(compiler).compile(
        candidate(contract=contract, evidence_blocks=(required,))
    )
    assert covered.coverage.items[0].covered is True

    payload["operators"][0]["success_commands"] = [
        command
        for command in payload["operators"][0]["success_commands"]
        if command["kind"] != "apply_ruleset_effect"
    ]
    inert = ScenarioContractCompiler().compile(payload).contract
    assert inert is not None
    uncovered = EvidenceBoundScenarioCompiler().compile(
        candidate(contract=inert, evidence_blocks=(required,))
    )
    assert uncovered.coverage.items[0].covered is False


def test_source_grounded_outcome_cues_cover_an_explicit_check() -> None:
    text = (
        "进行侦查检定。成功：找到完整巡灯记录；"
        "失败：今日无法再查阅档案。"
    )
    batch = materialize_action_envelope(
        CoverageActionEnvelope(
            actions=(CoverageActionProposal(title="进行侦查检定"),)
        ),
        source_block_id="block-1",
        record_id_prefix="coverage_",
        source_checks=(ScenarioSourceCheck(term="侦查"),),
        source_text=text,
    )
    assembled = ScenarioIrAssembler().assemble(
        (batch,),
        source_refs={
            "block-1": sourced_contract().operators[0].source_refs[0]
        },
        source_texts={"block-1": text},
        contract_id="coverage-contract",
        source_version=1,
        ruleset_id="coc7",
        title="Coverage",
        corpus_truncated=False,
        check_catalog=coc7_scenario_check_catalog(),
    )
    required = evidence_block().model_copy(
        update={
            "coverage_requirements": (
                SourceCoverageRequirement(
                    requirement_key="explicit_checks",
                    acceptable_record_kinds=("operators",),
                    reason="The source declares a check and both outcomes.",
                ),
            )
        }
    )

    result = EvidenceBoundScenarioCompiler().compile(
        candidate(contract=assembled.contract, evidence_blocks=(required,))
    )

    operator = assembled.contract.operators[0]
    assert operator.skill_choices[0].allow_push is False
    assert operator.skill_choices[0].failure_stakes == "失败：今日无法再查阅档案"
    assert operator.failure_commands[0].path.endswith("failure_observed")
    assert result.coverage.items[0].covered is True


def test_ruleset_effect_and_pressure_in_exact_outcome_branch_are_covered() -> None:
    required = evidence_block().model_copy(update={
        "coverage_requirements": (
            SourceCoverageRequirement(
                requirement_key="explicit_ruleset_effect",
                acceptable_record_kinds=("operators",),
                reason="The source declares damage.",
            ),
            SourceCoverageRequirement(
                requirement_key="explicit_pressure",
                acceptable_record_kinds=("operators",),
                reason="The source advances a clock.",
            ),
        ),
    })
    payload = sourced_contract().model_dump(mode="json")
    payload["clocks"] = [{
        "clock_id": "danger",
        "title": "Danger",
        "clock_kind": "soft",
        "initial_value": 0,
        "maximum_value": 4,
        "source_refs": [{
            "source_block_id": "block-1",
            "document_id": "document-1",
            "page": 3,
            "paragraph": 7,
        }],
    }]
    payload["operators"][0]["outcome_branches"] = [{
        "outcome_key": "pushed_failure",
        "commands": [
            {
                "kind": "apply_ruleset_effect",
                "event_type": "damage",
                "payload": {"damage": "1"},
            },
            {"kind": "advance_clock", "clock_id": "danger", "delta": 1},
        ],
    }]
    compiler = ScenarioContractCompiler(coc7_scenario_effect_catalog())
    contract = compiler.compile(payload).contract
    assert contract is not None

    result = EvidenceBoundScenarioCompiler(compiler).compile(
        candidate(contract=contract, evidence_blocks=(required,))
    )

    assert all(item.covered for item in result.coverage.items)


def test_coverage_report_creates_only_the_missing_supplement_delta() -> None:
    required = evidence_block().model_copy(
        update={
            "coverage_requirements": (
                SourceCoverageRequirement(
                    requirement_key="explicit_checks",
                    acceptable_record_kinds=("operators",),
                    minimum_record_count=3,
                    reason="Three checks are explicit.",
                ),
            ),
        }
    )

    result = EvidenceBoundScenarioCompiler().compile(
        candidate(evidence_blocks=(required,))
    )

    item = result.coverage.items[0]
    target = result.coverage.supplement_targets()[0]
    assert item.matched_record_count == 1
    assert target.required_additional_count == 2
    assert target.source_block_id == "block-1"


def test_duplicate_source_refs_do_not_inflate_coverage() -> None:
    required = evidence_block().model_copy(
        update={
            "coverage_requirements": (
                SourceCoverageRequirement(
                    requirement_key="two_endings",
                    acceptable_record_kinds=("endings",),
                    minimum_record_count=2,
                    reason="Two independently represented endings are required.",
                ),
            ),
        }
    )
    payload = sourced_contract().model_dump(mode="json")
    assert len(payload["endings"]) == 1
    payload["endings"][0]["source_refs"] *= 2
    contract = ScenarioContractCompiler().compile(payload).contract
    assert contract is not None

    result = EvidenceBoundScenarioCompiler().compile(
        candidate(contract=contract, evidence_blocks=(required,))
    )

    assert result.coverage.items[0].matched_record_count == 1
    assert result.decision == "review_required"

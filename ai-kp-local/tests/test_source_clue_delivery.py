from __future__ import annotations

from ai_kp.platform.resolution.contracts import (
    ActionIntent,
    ActionOperator,
    ClueSpec,
    LocationSpec,
    OutcomeNarrativeCue,
    ScenarioContract,
    ScenarioSnapshot,
    SourceRef,
    StateCondition,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.playability import ScenarioPlayabilityAnalyzer
from ai_kp.platform.resolution.scenario_ir import ScenarioIrAssembler, ScenarioIrBatch
from ai_kp.platform.resolution.source_clue_delivery import (
    materialize_source_declared_clue_delivery,
)


def _ref(source_id: str = "source-1") -> SourceRef:
    return SourceRef(source_block_id=source_id, document_id="doc", paragraph=1)


def _contract(*, preconditions: tuple[StateCondition, ...] = ()) -> ScenarioContract:
    source_ref = _ref()
    return ScenarioContract(
        contract_id="clue-recovery",
        source_version=1,
        ruleset_id="coc7",
        title="Clue recovery",
        initial_scene_id="archive",
        locations=(
            LocationSpec(
                location_id="archive",
                title="Archive",
                initial_visibility="visited",
                source_refs=(source_ref,),
            ),
        ),
        clues=(
            ClueSpec(
                clue_id="ledger",
                title="Ledger",
                importance="core",
                discovery_operator_ids=("search-ledger",),
                fact_path="clues.ledger",
                public_content=("The ledger names Corbitt.",),
                source_refs=(source_ref,),
            ),
        ),
        operators=(
            ActionOperator(
                operator_id="search-ledger",
                title="Search the ledger",
                policy="automatic",
                preconditions=preconditions,
                source_refs=(source_ref,),
            ),
        ),
    )


def test_reachable_exact_source_route_gets_fact_and_public_success_payload() -> None:
    contract = _contract(
        preconditions=(StateCondition(path="scene_id", operator="eq", value="archive"),)
    )
    contract = contract.model_copy(update={
        "operators": (
            contract.operators[0].model_copy(update={
                "success_commands": (
                    WorldCommand(kind="set_fact", path="clues.ledger", value=True),
                ),
            }),
        ),
    })

    repaired = materialize_source_declared_clue_delivery(
        contract,
        source_texts={"source-1": "On a successful search: The ledger names Corbitt."},
    )

    operator = repaired.operators[0]
    assert operator.success_commands[0].path == "clues.ledger"
    assert operator.narrative_cues[0].public_summary == "The ledger names Corbitt."
    report = ScenarioPlayabilityAnalyzer().analyze(repaired)
    assert report.proof("source_content_delivery").status == "passed"
    assert report.proof("core_clue_discoverability").status == "passed"
    assert materialize_source_declared_clue_delivery(
        repaired,
        source_texts={"source-1": "On a successful search: The ledger names Corbitt."},
    ) == repaired


def test_unreachable_declared_route_is_not_made_into_a_false_discovery() -> None:
    contract = _contract(
        preconditions=(StateCondition(path="facts.locked", operator="eq", value=False),)
    )

    repaired = materialize_source_declared_clue_delivery(
        contract,
        source_texts={"source-1": "The ledger names Corbitt."},
    )

    assert repaired == contract
    report = ScenarioPlayabilityAnalyzer().analyze(repaired)
    assert report.proof("source_content_delivery").status == "failed"
    assert report.proof("core_clue_discoverability").status == "failed"


def test_existing_public_success_delivery_cannot_create_missing_fact_commit() -> None:
    contract = _contract()
    operator = contract.operators[0].model_copy(update={
        "public_setup": "A search may expose the ledger entry.",
        "narrative_cues": (
            OutcomeNarrativeCue(
                outcome_key="success",
                public_summary="The ledger names Corbitt.",
            ),
        ),
    })
    contract = contract.model_copy(update={"operators": (operator,)})

    repaired = materialize_source_declared_clue_delivery(
        contract,
        source_texts={"source-1": "The ledger names Corbitt."},
    )

    assert repaired == contract


def test_shared_source_without_existing_causal_half_does_not_bind_clue() -> None:
    contract = _contract()

    repaired = materialize_source_declared_clue_delivery(
        contract,
        source_texts={"source-1": "The ledger names Corbitt."},
    )

    assert repaired == contract


def test_assembler_does_not_turn_model_discovery_id_into_causal_authority() -> None:
    source_ref = _ref()
    batch = ScenarioIrBatch.model_validate({
        "clues": [{
            "id": "ledger",
            "title": "Ledger",
            "importance": "supporting",
            "discovery_action_ids": ["search-ledger"],
            "fact_path": "clues.ledger",
            "fact_value": True,
            "public_content": ["The ledger names Corbitt."],
            "source_block_ids": ["source-1"],
        }],
        "actions": [{
            "id": "search-ledger",
            "title": "Search the ledger",
            "policy": "automatic",
            "source_block_ids": ["source-1"],
        }],
    })

    assembled = ScenarioIrAssembler().assemble(
        (batch,),
        source_refs={"source-1": source_ref},
        source_texts={"source-1": "The ledger names Corbitt."},
        source_titles={"source-1": "Ledger"},
        contract_id="no-model-causal-authority",
        source_version=1,
        ruleset_id="coc7",
        title="No causal authority",
        corpus_truncated=False,
    )

    operator = assembled.contract.operators[0]
    assert operator.automatic_information == ()
    assert operator.success_commands == ()


def test_success_only_clue_is_not_disclosed_in_preview_before_outcome() -> None:
    source_ref = _ref()
    batch = ScenarioIrBatch.model_validate({
        "clues": [{
            "id": "ledger",
            "title": "Ledger",
            "importance": "core",
            "discovery_action_ids": ["search-ledger"],
            "fact_path": "clues.ledger",
            "fact_value": True,
            "public_content": ["The ledger names Corbitt."],
            "source_block_ids": ["source-1"],
        }],
        "actions": [{
            "id": "search-ledger",
            "title": "Search the ledger",
            "policy": "automatic",
            "on_success": [{
                "kind": "set_fact",
                "path": "clues.ledger",
                "value": True,
            }],
            "source_block_ids": ["source-1"],
        }],
    })
    assembled = ScenarioIrAssembler().assemble(
        (batch,),
        source_refs={"source-1": source_ref},
        source_texts={"source-1": "The ledger names Corbitt."},
        source_titles={"source-1": "Ledger"},
        contract_id="success-only-clue",
        source_version=1,
        ruleset_id="coc7",
        title="Success-only clue",
        corpus_truncated=False,
    )
    operator = assembled.contract.operators[0]
    preview = ActionResolutionKernel.from_contract(assembled.contract).preview(
        ScenarioSnapshot(
            run_id="run",
            contract_id=assembled.contract.contract_id,
            scenario_version=1,
            run_version=1,
        ),
        ActionIntent(
            action_id="action",
            actor_id="investigator",
            goal="Search",
            operator_id=operator.operator_id,
        ),
    )

    assert operator.automatic_information == ()
    assert preview.automatic_information == ()
    assert operator.narrative_cues == (
        OutcomeNarrativeCue(
            outcome_key="success",
            public_summary="The ledger names Corbitt.",
        ),
    )


def test_private_source_payload_is_not_published() -> None:
    contract = _contract()

    repaired = materialize_source_declared_clue_delivery(
        contract,
        source_texts={"source-1": "Keeper only: The ledger names Corbitt."},
    )

    assert repaired == contract


def test_multiple_exact_payloads_reuse_existing_route_delivery() -> None:
    contract = _contract()
    clue = contract.clues[0].model_copy(
        update={"public_content": ("First fact.", "Second fact.")}
    )
    operator = contract.operators[0].model_copy(
        update={
            "automatic_information": ("First fact.", "Second fact."),
            "success_commands": (
                WorldCommand(kind="set_fact", path="clues.ledger", value=True),
            ),
        }
    )
    contract = contract.model_copy(update={"clues": (clue,), "operators": (operator,)})

    repaired = materialize_source_declared_clue_delivery(
        contract,
        source_texts={"source-1": "First fact. Second fact."},
    )

    assert repaired.operators[0].success_commands[0].path == "clues.ledger"
    assert ScenarioPlayabilityAnalyzer().analyze(repaired).proof(
        "source_content_delivery"
    ).status == "passed"


def test_ambiguous_declared_routes_are_not_selected() -> None:
    contract = _contract()
    duplicate = contract.operators[0].model_copy(update={"operator_id": "read-ledger"})
    clue = contract.clues[0].model_copy(
        update={"discovery_operator_ids": ("search-ledger", "read-ledger")}
    )
    contract = contract.model_copy(
        update={"clues": (clue,), "operators": (*contract.operators, duplicate)}
    )

    repaired = materialize_source_declared_clue_delivery(
        contract,
        source_texts={
            "source-1": (
                "In the Archive, search the ledger. A completed search is recorded. "
                "The ledger names Corbitt."
            )
        },
    )

    assert repaired == contract


def test_recovery_does_not_run_playability_graph_exploration(
    monkeypatch,
) -> None:
    contract = _contract()
    monkeypatch.setattr(
        ScenarioPlayabilityAnalyzer,
        "reachable_operator_outcomes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("clue recovery must not explore the state graph")
        ),
    )
    materialize_source_declared_clue_delivery(
        contract,
        source_texts={"source-1": "The ledger names Corbitt."},
    )


def test_non_clue_state_handle_is_not_written_by_recovery() -> None:
    contract = _contract()
    clue = contract.clues[0].model_copy(update={"fact_path": "ending.unlocked"})
    contract = contract.model_copy(update={"clues": (clue,)})

    repaired = materialize_source_declared_clue_delivery(
        contract,
        source_texts={"source-1": "The ledger names Corbitt."},
    )

    assert repaired == contract

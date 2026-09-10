from __future__ import annotations

import pytest

from ai_kp.platform.resolution.contracts import (
    ActionIntent,
    ActionOperator,
    ClueSpec,
    LocationSpec,
    ScenarioContract,
    ScenarioSnapshot,
    SourceRef,
    StateCondition,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.playability import ScenarioPlayabilityAnalyzer
from ai_kp.platform.resolution.scenario_ir import ScenarioIrAssembler, ScenarioIrBatch
from ai_kp.platform.resolution.source_scene_opportunities import (
    materialize_source_scene_opportunities,
)


def ref(source_id: str) -> SourceRef:
    return SourceRef(source_block_id=source_id, document_id="document")


def producer(
    operator_id: str,
    source_id: str,
    fact_path: str,
    *,
    scene_id: str | None = None,
) -> ActionOperator:
    return ActionOperator(
        operator_id=operator_id,
        title=operator_id,
        policy="automatic",
        preconditions=(
            (StateCondition(path="scene_id", operator="eq", value=scene_id),)
            if scene_id is not None
            else ()
        ),
        success_commands=(WorldCommand(kind="set_fact", path=fact_path, value=True),),
        source_refs=(ref(source_id),),
    )


def base_contract(
    *,
    locations: tuple[LocationSpec, ...],
    operators: tuple[ActionOperator, ...],
    clues: tuple[ClueSpec, ...] = (),
    initial_scene_id: str = "archive",
) -> ScenarioContract:
    return ScenarioContract(
        contract_id="contract",
        source_version=1,
        ruleset_id="coc7",
        title="Investigation",
        initial_scene_id=initial_scene_id,
        locations=locations,
        operators=operators,
        clues=clues,
    )


def test_one_archive_fact_unlocks_court_and_police_destinations() -> None:
    source_id = "archive-records"
    contract = base_contract(
        locations=(
            LocationSpec(location_id="archive", title="档案馆"),
            LocationSpec(location_id="court", title="高等法院"),
            LocationSpec(location_id="police", title="中央警察局"),
        ),
        operators=(
            producer("read-records", source_id, "investigation.records_read"),
        ),
    )

    repaired = materialize_source_scene_opportunities(
        contract,
        source_refs={source_id: ref(source_id)},
        source_texts={
            source_id: "重大案件的记录位于高等法院；城市警官的记录由中央警察局保存。"
        },
        source_section_paths={source_id: ("场景 4: 档案馆", "查阅记录")},
        source_scene_keys={source_id: "档案馆"},
    )

    generated = repaired.operators[1:]
    assert len(generated) == 2
    assert {item.success_commands[0].value for item in generated} == {
        "court",
        "police",
    }
    assert all(
        item.preconditions
        == (
            StateCondition(
                path="facts.investigation.records_read",
                operator="eq",
                value=True,
            ),
        )
        for item in generated
    )
    assert materialize_source_scene_opportunities(
        repaired,
        source_refs={source_id: ref(source_id)},
        source_texts={
            source_id: "重大案件的记录位于高等法院；城市警官的记录由中央警察局保存。"
        },
        source_section_paths={source_id: ("场景 4: 档案馆", "查阅记录")},
        source_scene_keys={source_id: "档案馆"},
    ) == repaired


def test_dooley_fact_unlocks_two_separately_asserted_destinations() -> None:
    producer_source_id = "dooley-action"
    evidence_source_id = "dooley-statement"
    contract = base_contract(
        locations=(
            LocationSpec(location_id="archive", title="档案馆"),
            LocationSpec(location_id="chapel", title="沉思礼拜堂"),
            LocationSpec(location_id="sanitarium", title="罗克斯伯里疗养院"),
        ),
        operators=(
            producer(
                "speak-dooley",
                evidence_source_id,
                "contacts.dooley_spoke",
            ),
        ),
    )

    repaired = materialize_source_scene_opportunities(
        contract,
        source_refs={evidence_source_id: ref(evidence_source_id)},
        source_texts={
            evidence_source_id: (
                "如果调查员成功让杜利开口，他会指出沉思礼拜堂所在的地方；"
                "马卡里奥夫妇现在在罗克斯伯里疗养院。"
            )
        },
        source_section_paths={
            producer_source_id: ("访问杜利",),
            evidence_source_id: ("访问杜利",),
        },
    )

    assert {item.success_commands[0].value for item in repaired.operators[1:]} == {
        "chapel",
        "sanitarium",
    }


def test_conditional_success_relation_requires_direct_source_producer() -> None:
    producer_source_id = "nearby-action"
    evidence_source_id = "conditional-destination"
    contract = base_contract(
        locations=(
            LocationSpec(location_id="archive", title="档案馆"),
            LocationSpec(location_id="chapel", title="沉思礼拜堂"),
        ),
        operators=(
            producer("speak", producer_source_id, "contacts.spoke"),
        ),
    )

    repaired = materialize_source_scene_opportunities(
        contract,
        source_refs={evidence_source_id: ref(evidence_source_id)},
        source_texts={
            evidence_source_id: "如果调查员成功让他开口，他会指出沉思礼拜堂所在的地方。"
        },
        source_section_paths={
            producer_source_id: ("访问证人",),
            evidence_source_id: ("访问证人",),
        },
    )

    assert repaired == contract


def test_unconditional_same_section_fact_without_clue_does_not_unlock_travel() -> None:
    producer_source_id = "nearby-action"
    evidence_source_id = "destination-statement"
    contract = base_contract(
        locations=(
            LocationSpec(location_id="archive", title="Archive"),
            LocationSpec(location_id="court", title="Court"),
        ),
        operators=(producer("unrelated", producer_source_id, "door.open"),),
    )

    repaired = materialize_source_scene_opportunities(
        contract,
        source_refs={evidence_source_id: ref(evidence_source_id)},
        source_texts={evidence_source_id: "The records are kept at Court."},
        source_section_paths={
            producer_source_id: ("Archive",),
            evidence_source_id: ("Archive",),
        },
    )

    assert repaired == contract


def test_clue_mapping_selects_one_fact_from_a_multi_fact_producer() -> None:
    source_id = "records"
    operator = ActionOperator(
        operator_id="read",
        title="Read",
        policy="automatic",
        success_commands=(
            WorldCommand(kind="set_fact", path="records.read", value=True),
            WorldCommand(kind="set_fact", path="time.passed", value=True),
        ),
        source_refs=(ref(source_id),),
    )
    contract = base_contract(
        locations=(
            LocationSpec(location_id="archive", title="Archive"),
            LocationSpec(location_id="court", title="Court"),
        ),
        operators=(operator,),
        clues=(
            ClueSpec(
                clue_id="record-clue",
                title="Record",
                importance="supporting",
                discovery_operator_ids=("read",),
                fact_path="records.read",
                fact_value=True,
                public_content=("The records are kept at Court.",),
                source_refs=(ref(source_id),),
            ),
        ),
    )

    repaired = materialize_source_scene_opportunities(
        contract,
        source_refs={source_id: ref(source_id)},
        source_texts={source_id: "The records are kept at Court."},
        source_section_paths={source_id: ("Archive records",)},
    )

    assert repaired.operators[-1].preconditions[0].path == "facts.records.read"


def test_generated_opportunity_executes_after_fact_and_proves_reachability() -> None:
    source_id = "record"
    contract = base_contract(
        locations=(
            LocationSpec(location_id="archive", title="Archive"),
            LocationSpec(location_id="court", title="Court"),
        ),
        operators=(
            producer(
                "read-record",
                source_id,
                "record.read",
                scene_id="archive",
            ),
        ),
    )
    repaired = materialize_source_scene_opportunities(
        contract,
        source_refs={source_id: ref(source_id)},
        source_texts={source_id: "The records are kept at Court."},
        source_section_paths={source_id: ("Archive", "Record")},
    )
    opportunity = repaired.operators[-1]
    kernel = ActionResolutionKernel.from_contract(repaired)
    snapshot = ScenarioSnapshot(
        run_id="run",
        contract_id="contract",
        scenario_version=1,
        run_version=1,
        scene_id="archive",
    )

    blocked = kernel.preview(
        snapshot,
        ActionIntent(
            action_id="travel-before",
            actor_id="investigator",
            goal="Travel",
            method="Follow record",
            operator_id=opportunity.operator_id,
        ),
    )
    assert blocked.allowed is False
    producer_preview = kernel.preview(
        snapshot,
        ActionIntent(
            action_id="read",
            actor_id="investigator",
            goal="Read",
            method="Research",
            operator_id="read-record",
        ),
    )
    learned = kernel.preflight(snapshot, producer_preview.success_commands)
    travel = kernel.preview(
        learned,
        ActionIntent(
            action_id="travel-after",
            actor_id="investigator",
            goal="Travel",
            method="Follow record",
            operator_id=opportunity.operator_id,
        ),
    )
    assert travel.allowed is True
    assert kernel.preflight(learned, travel.success_commands).scene_id == "court"
    assert (
        ScenarioPlayabilityAnalyzer()
        .analyze(repaired)
        .proof("scene_reachability")
        .status
        == "passed"
    )


def test_reserved_opportunity_id_conflict_fails_closed() -> None:
    source_id = "record"
    base = base_contract(
        locations=(
            LocationSpec(location_id="archive", title="Archive"),
            LocationSpec(location_id="court", title="Court"),
        ),
        operators=(producer("read", source_id, "record.read"),),
    )
    kwargs = {
        "source_refs": {source_id: ref(source_id)},
        "source_texts": {source_id: "The records are kept at Court."},
        "source_section_paths": {source_id: ("Archive", "Record")},
    }
    generated = materialize_source_scene_opportunities(base, **kwargs).operators[-1]
    poisoned = base.model_copy(update={
        "operators": (
            *base.operators,
            generated.model_copy(update={"title": "Model-owned collision"}),
        )
    })

    with pytest.raises(ValueError, match="reserved id conflicts"):
        materialize_source_scene_opportunities(poisoned, **kwargs)


def test_ambiguous_or_unsupported_destination_evidence_fails_closed() -> None:
    source_id = "source"
    base_locations = (
        LocationSpec(location_id="archive", title="Archive"),
        LocationSpec(location_id="court-a", title="Court"),
        LocationSpec(location_id="court-b", title="Court"),
        LocationSpec(location_id="combined", title="Court / Police"),
    )
    operator = producer("read", source_id, "record.read")
    cases = (
        "Court",
        "You may go to Court or Archive; you decide where to go.",
        "If the secret door is unlocked, the passage is located at Court.",
        "The records are kept at Court.",
    )
    for text in cases:
        contract = base_contract(locations=base_locations, operators=(operator,))
        repaired = materialize_source_scene_opportunities(
            contract,
            source_refs={source_id: ref(source_id)},
            source_texts={source_id: text},
            source_section_paths={source_id: ("Records",)},
        )
        assert repaired == contract


def test_exact_component_can_unlock_a_server_combined_scene() -> None:
    source_id = "records"
    contract = base_contract(
        locations=(
            LocationSpec(location_id="archive", title="Archive"),
            LocationSpec(location_id="records-office", title="Court / Police"),
        ),
        operators=(producer("read", source_id, "record.read"),),
    )

    repaired = materialize_source_scene_opportunities(
        contract,
        source_refs={source_id: ref(source_id)},
        source_texts={source_id: "The arrest records are kept at Police."},
        source_section_paths={source_id: ("Archive records",)},
    )

    assert repaired.operators[-1].success_commands == (
        WorldCommand(kind="set_scene", value="records-office"),
    )


def test_non_unique_fact_producer_fails_closed() -> None:
    source_id = "records"
    contract = base_contract(
        locations=(
            LocationSpec(location_id="archive", title="Archive"),
            LocationSpec(location_id="court", title="Court"),
        ),
        operators=(
            producer("read-a", source_id, "record.read"),
            producer("read-b", source_id, "record.read"),
        ),
    )

    repaired = materialize_source_scene_opportunities(
        contract,
        source_refs={source_id: ref(source_id)},
        source_texts={source_id: "The records are kept at Court."},
        source_section_paths={source_id: ("Records",)},
    )

    assert repaired == contract


def test_unique_source_scene_alias_can_name_incrementally_added_destination() -> None:
    source_id = "records"
    destination_source_id = "court-location"
    contract = base_contract(
        locations=(LocationSpec(location_id="archive", title="Archive"),),
        operators=(producer("read", source_id, "record.read"),),
    )
    kwargs = {
        "source_refs": {source_id: ref(source_id)},
        "source_texts": {source_id: "The records are kept at Court Registry."},
        "source_section_paths": {source_id: ("Records",)},
        "source_scene_keys": {destination_source_id: "Court Registry"},
    }

    assert materialize_source_scene_opportunities(contract, **kwargs) == contract
    extended = contract.model_copy(
        update={
            "locations": (
                *contract.locations,
                LocationSpec(
                    location_id="court",
                    title="Judicial Building",
                    source_refs=(ref(destination_source_id),),
                ),
            )
        }
    )
    repaired = materialize_source_scene_opportunities(extended, **kwargs)

    assert repaired.operators[-1].success_commands == (
        WorldCommand(kind="set_scene", value="court"),
    )


def test_secret_or_conditional_section_ancestry_fails_closed() -> None:
    source_id = "records"
    contract = base_contract(
        locations=(
            LocationSpec(location_id="archive", title="Archive"),
            LocationSpec(location_id="court", title="Court"),
        ),
        operators=(producer("read", source_id, "record.read"),),
    )

    for section_path in (
        ("Archive", "Secret passage"),
        ("Archive", "如果解锁暗门"),
    ):
        repaired = materialize_source_scene_opportunities(
            contract,
            source_refs={source_id: ref(source_id)},
            source_texts={source_id: "The records are kept at Court."},
            source_section_paths={source_id: section_path},
        )
        assert repaired == contract


def test_assembler_materializes_opportunity_before_canonical_travel() -> None:
    action_source_id = "record-action"
    evidence_source_id = "record-evidence"
    court_source_id = "court-location"
    batch = ScenarioIrBatch.model_validate(
        {
            "initial_scene_id": "archive",
            "locations": [
                {
                    "id": "archive",
                    "title": "Archive",
                    "source_block_ids": [action_source_id],
                },
                {
                    "id": "court",
                    "title": "Court",
                    "source_block_ids": [court_source_id],
                },
            ],
            "actions": [
                {
                    "id": "read-record",
                    "title": "Read record",
                    "policy": "automatic",
                    "location_slot": 0,
                    "on_success": [
                        {
                            "kind": "set_fact",
                            "path": "facts.record.read",
                            "value": True,
                        }
                    ],
                    "source_block_ids": [action_source_id],
                }
            ],
        }
    )

    result = ScenarioIrAssembler().assemble(
        (batch,),
        source_refs={
            action_source_id: ref(action_source_id),
            evidence_source_id: ref(evidence_source_id),
            court_source_id: ref(court_source_id),
        },
        source_texts={
            action_source_id: "Read the record. The records are kept at Court."
        },
        source_section_paths={
            action_source_id: ("Archive", "Records"),
            evidence_source_id: ("Archive", "Records"),
            court_source_id: ("Court",),
        },
        contract_id="assembled",
        source_version=1,
        ruleset_id="coc7",
        title="Investigation",
        corpus_truncated=False,
    )

    opportunity = next(
        item
        for item in result.contract.operators
        if item.operator_id.startswith("source_scene_opportunity_")
    )
    assert opportunity.preconditions[0].path == "facts.record.read"
    assert opportunity.success_commands == (
        WorldCommand(kind="set_scene", value="court"),
    )

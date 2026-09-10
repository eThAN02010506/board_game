from ai_kp.platform.resolution.contracts import (
    ActionIntent,
    LocationSpec,
    ScenarioContract,
    SourceRef,
)
from ai_kp.platform.resolution.investigation_hub import (
    InvestigationEvidence,
    materialize_investigation_hub,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.scenario_ir import ScenarioIrAssembler
from ai_kp.platform.resolution.scenario_ir_models import (
    IrLocation,
    IrLocationLink,
    ScenarioIrBatch,
)


def _ref(block_id: str) -> SourceRef:
    return SourceRef(source_block_id=block_id, document_id="doc")


def _contract(*, duplicate_archive: bool = False) -> ScenarioContract:
    locations = [
        LocationSpec(location_id="globe", title="波士顿环球报社"),
        LocationSpec(location_id="library", title="中央图书馆"),
        LocationSpec(location_id="archives", title="档案馆"),
        LocationSpec(location_id="house", title="科比特的老房子"),
    ]
    if duplicate_archive:
        locations.append(LocationSpec(location_id="archives_2", title="档案馆"))
    return ScenarioContract(
        contract_id="ghost-house",
        source_version=1,
        ruleset_id="coc7",
        title="鬼屋",
        locations=tuple(locations),
    )


def _evidence() -> tuple[InvestigationEvidence, ...]:
    return (
        InvestigationEvidence(
            source_ref=_ref("choice"),
            title="文字材料1",
            section_path=("场景1", "文字材料1"),
            text=(
                "接到委托后，你们可以去波士顿环球报社，也可以前往中央图书馆"
                "或档案馆。这都由你们决定。"
            ),
        ),
        InvestigationEvidence(
            source_ref=_ref("house"),
            title="文字材料1",
            section_path=("场景1", "文字材料1"),
            text="有些玩家会想要直接进入科比特的老房子。",
        ),
        InvestigationEvidence(
            source_ref=_ref("movement"),
            title="文字材料1",
            section_path=("场景1", "文字材料1"),
            text="你不需要详述场景之间的移动，直接让调查员们抵达新场景就好。",
        ),
    )


def test_materializes_source_declared_choice_as_executable_phase() -> None:
    result = materialize_investigation_hub(_contract(), _evidence())

    assert result.initial_scene_id is not None
    assert result.initial_scene_id.startswith("system_investigation_phase_")
    assert result.locations[0].initial_visibility == "visited"
    visibility = {item.location_id: item.initial_visibility for item in result.locations}
    assert visibility["globe"] == "known"
    assert visibility["library"] == "known"
    assert visibility["archives"] == "known"
    assert visibility["house"] == "known"

    to_library = next(
        item
        for item in result.operators
        if item.success_commands[0].value == "library"
        and item.preconditions[0].value == result.initial_scene_id
    )
    kernel = ActionResolutionKernel.from_contract(result)
    preview = kernel.preview(
        result.initial_snapshot("run"),
        ActionIntent(
            action_id="action-1",
            actor_id="pc-1",
            goal=to_library.title,
            method=to_library.title,
            operator_id=to_library.operator_id,
        ),
    )
    settled = kernel.preflight(
        result.initial_snapshot("run"), preview.commands_for_outcome("success")
    )
    assert settled.scene_id == "library"
    assert any(
        item.success_commands[0].value == result.initial_scene_id
        and item.preconditions[0].value == "library"
        for item in result.operators
    )

    # Deterministic materialization is restart/idempotency safe.
    assert materialize_investigation_hub(result, _evidence()) == result


def test_reuses_exact_source_scene_as_investigation_phase() -> None:
    base = _contract().model_copy(update={
        "locations": (
            LocationSpec(location_id="intro", title="介绍", source_refs=(_ref("heading"),)),
            *_contract().locations,
        )
    })
    evidence = tuple(
        InvestigationEvidence(
            source_ref=item.source_ref,
            title=item.title,
            text=item.text,
            section_path=item.section_path,
            scene_key="介绍",
        )
        for item in _evidence()
    )

    result = materialize_investigation_hub(base, evidence)

    assert result.initial_scene_id == "intro"
    assert len(result.locations) == len(base.locations)
    assert result.locations[0].initial_visibility == "visited"
    assert not any("system_phase" in item.tags for item in result.locations)
    assert any(
        item.preconditions[0].value == "intro"
        and item.success_commands[0].value == "library"
        for item in result.operators
    )


def test_exact_duplicate_choice_evidence_merges_provenance() -> None:
    original = InvestigationEvidence(
        source_ref=SourceRef(
            source_block_id="choice",
            document_id="doc",
            paragraph=10,
        ),
        title=_evidence()[0].title,
        text=_evidence()[0].text,
        section_path=("场景1", "文字材料1"),
    )
    duplicate = InvestigationEvidence(
        source_ref=SourceRef(
            source_block_id="choice_appendix",
            document_id="doc",
            paragraph=100,
        ),
        title=original.title,
        text=original.text,
        section_path=("结局", "文字材料1"),
    )
    appendix_exit = InvestigationEvidence(
        source_ref=SourceRef(
            source_block_id="appendix_exit",
            document_id="doc",
            paragraph=101,
        ),
        title="文字材料1",
        text="调查员可以直接进入科比特的老房子。",
        section_path=("结局", "文字材料1"),
    )
    movement = InvestigationEvidence(
        source_ref=SourceRef(
            source_block_id="movement",
            document_id="doc",
            paragraph=11,
        ),
        title="文字材料1",
        text="你不需要详述场景之间的移动，直接让调查员们抵达新场景就好。",
        section_path=("场景1", "文字材料1"),
    )

    result = materialize_investigation_hub(
        _contract(),
        (original, duplicate, appendix_exit, movement),
    )

    assert result.initial_scene_id is not None
    phase = next(
        item for item in result.locations if item.location_id == result.initial_scene_id
    )
    assert {item.source_block_id for item in phase.source_refs} == {
        "choice",
        "choice_appendix",
    }
    selectable = tuple(
        item
        for item in result.operators
        if item.preconditions[0].value == result.initial_scene_id
        and item.success_commands[0].value in {"globe", "library", "archives"}
    )
    assert len(selectable) == 3
    assert all(
        {ref.source_block_id for ref in item.source_refs}
        == {"choice", "choice_appendix"}
        for item in selectable
    )
    assert next(
        item for item in result.locations if item.location_id == "house"
    ).initial_visibility == "hidden"
    assert not any(
        command.value == "house"
        for item in result.operators
        for command in item.success_commands
    )


def test_objective_mention_without_choice_never_becomes_initial_scene() -> None:
    evidence = (
        InvestigationEvidence(
            source_ref=_ref("objective"),
            title="委托",
            text="调查员受聘调查科比特的老房子，并拿到了地址与钥匙。",
        ),
    )
    assert materialize_investigation_hub(_contract(), evidence) == _contract()


def test_ambiguous_location_title_is_not_selected() -> None:
    result = materialize_investigation_hub(
        _contract(duplicate_archive=True), _evidence()
    )
    destinations = {
        command.value
        for operator in result.operators
        for command in operator.success_commands
    }
    assert "archives" not in destinations
    assert "archives_2" not in destinations
    assert {"globe", "library"} <= destinations


def test_conditional_or_secret_choice_fails_closed() -> None:
    evidence = (
        InvestigationEvidence(
            source_ref=_ref("secret"),
            title="隐藏路线",
            text=(
                "如果检定成功并发现隐藏入口，你们可以去波士顿环球报社，"
                "也可以前往中央图书馆，这都由你们决定。"
            ),
        ),
    )
    assert materialize_investigation_hub(_contract(), evidence) == _contract()


def test_ir_assembly_materializes_hub_and_rejects_ungrounded_base_link() -> None:
    batch = ScenarioIrBatch(
        confidence="high",
        locations=(
            IrLocation(id="globe", title="波士顿环球报社", source_block_ids=("choice",)),
            IrLocation(id="library", title="中央图书馆", source_block_ids=("choice",)),
            IrLocation(id="archives", title="档案馆", source_block_ids=("choice",)),
            IrLocation(id="house", title="科比特的老房子", source_block_ids=("house",)),
        ),
        location_links=(
            IrLocationLink(
                from_id="globe",
                to_id="house",
                source_block_ids=("objective",),
            ),
        ),
    )
    source_refs = {
        key: _ref(key) for key in ("choice", "house", "movement", "objective")
    }
    evidence = {item.source_ref.source_block_id: item for item in _evidence()}
    source_texts = {
        key: item.text for key, item in evidence.items()
    } | {"objective": "房东委托调查员调查波士顿环球报社与科比特的老房子。"}
    source_titles = {
        key: item.title for key, item in evidence.items()
    } | {"objective": "委托"}
    section_paths = {
        key: item.section_path for key, item in evidence.items()
    } | {"objective": ("场景1", "文字材料1")}

    assembly = ScenarioIrAssembler().assemble(
        (batch,),
        source_refs=source_refs,
        contract_id="ghost-house",
        source_version=1,
        ruleset_id="coc7",
        title="鬼屋",
        corpus_truncated=False,
        source_texts=source_texts,
        source_titles=source_titles,
        source_section_paths=section_paths,
    )

    assert assembly.contract.initial_scene_id is not None
    assert assembly.contract.location_links == ()
    assert any(
        command.kind == "set_scene" and command.value == "library"
        for operator in assembly.contract.operators
        for command in operator.success_commands
    )


def test_incremental_assembly_retains_external_initial_phase_without_duplication() -> None:
    base = materialize_investigation_hub(_contract(), _evidence())
    source_refs = {
        item.source_ref.source_block_id: item.source_ref for item in _evidence()
    }

    assembly = ScenarioIrAssembler().assemble(
        (ScenarioIrBatch(confidence="high"),),
        source_refs=source_refs,
        contract_id=base.contract_id,
        source_version=base.source_version,
        ruleset_id=base.ruleset_id,
        title=base.title,
        corpus_truncated=False,
        source_texts={
            item.source_ref.source_block_id: item.text for item in _evidence()
        },
        source_titles={
            item.source_ref.source_block_id: item.title for item in _evidence()
        },
        source_section_paths={
            item.source_ref.source_block_id: item.section_path for item in _evidence()
        },
        external_initial_scene_id=base.initial_scene_id,
        external_locations=base.locations,
        allow_empty_actions=True,
    )

    assert assembly.contract.initial_scene_id == base.initial_scene_id
    assert sum(
        item.location_id == base.initial_scene_id
        for item in assembly.contract.locations
    ) == 1

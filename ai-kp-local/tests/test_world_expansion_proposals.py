import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_kp.application.auto_world_expansion_service import AutoWorldExpansionService
from ai_kp.application.errors import ConflictError
from ai_kp.application.fact_service import AssertWorldFactCommand, FactService
from ai_kp.application.module_graph_service import ModuleGraphService
from ai_kp.application.module_knowledge_service import ModuleKnowledgeService
from ai_kp.application.module_setting_analysis_service import (
    CreateSettingProfileCommand,
    ModuleSettingAnalysisService,
    SelectRunSettingCommand,
    UpdateSettingProfileCommand,
)
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import (
    TurnService,
    WorldExpansionCommand,
)
from ai_kp.application.world_expansion_materialization_service import (
    EncounterEntityRealization,
    EncounterFact,
    MaterializeWorldExpansionCommand,
    WorldExpansionMaterializationService,
)
from ai_kp.director.context_builder import ContextAssembly, ContextBuilder
from ai_kp.director.orchestrator import KpOrchestrator
from ai_kp.director.turn_output import StructuredOutputError
from ai_kp.director.world_expansion import (
    WorldExpansionCandidate,
    WorldExpansionOutput,
    normalize_world_expansion_transport,
    parse_world_expansion_output,
    validate_world_expansion_plan,
)
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.modules.graph import ModuleEntityCreate
from ai_kp.platform.modules.ingestion import ModuleChunk
from ai_kp.platform.scenes.setting_profiles import (
    ConfiguredEntityBinding,
    ConfiguredRegion,
    SettingProfileDocument,
)


class FakeWorldExpansionDirector:
    def __init__(
        self,
        *,
        confidence: str = "medium",
        conflicts: list[str] | None = None,
        include_entities: bool = True,
    ) -> None:
        self.calls = 0
        self.confidence = confidence
        self.conflicts = conflicts or []
        self.include_entities = include_entities
        self.last_analysis_snapshot = None

    async def handle_world_expansion(self, **values):
        self.calls += 1
        self.last_analysis_snapshot = values["analysis_snapshot"]
        template = values["analysis_snapshot"].get("settlement_template")
        template_binding = None
        if template is not None:
            slot = next(
                item for item in template["scene_slots"] if item["slot_id"] == "law_enforcement"
            )
            template_binding = {
                "setting_pack_id": template["setting_pack_id"],
                "settlement_kind": template["settlement_kind"],
                "slot_id": slot["slot_id"],
                "building_variant": slot["building_candidates"][0],
                "profession_ids": [
                    item["profession_id"] for item in slot["profession_candidates"][:1]
                ],
                "source_entity_ids": [
                    item["module_entity_id"]
                    for item in template.get("source_entity_bindings", [])
                ],
                "entity_bindings": [
                    {
                        "local_ref": "duty_officer",
                        "archetype_id": "public_safety_officer",
                        "entity_kind": "npc",
                        "label_variant": "巡警",
                        "profession_ids": ["law_officer"],
                        "relation_bindings": [{
                            "relation_slot_id": "agency",
                            "target_source": "candidate",
                            "target_ref": "local_agency",
                        }],
                    },
                    {
                        "local_ref": "local_agency",
                        "archetype_id": "public_safety_agency",
                        "entity_kind": "organization",
                        "label_variant": "警长办公室",
                        "profession_ids": [],
                        "relation_bindings": [],
                    },
                ] if self.include_entities else [],
            }
        output = WorldExpansionOutput.model_validate(
            {
                "public_narration": "镇中心没有现代警察局招牌，但可以找到负责治安的办公室。",
                "kp_notes": "候选尚未成为事实；批准后仅叙述当前接触。",
                "candidate": {
                    "expansion_kind": "environment",
                    "subject": "小镇警务设施",
                    "proposal": "设置一间由治安官与兼职书记员使用的小型办公室。",
                    "rationale": "符合 1920 年代小型聚落的规模和当前调查需求。",
                    "confidence": self.confidence,
                    "assumptions": ["小镇位于采用治安官制度的辖区"],
                    "conflicts": self.conflicts,
                    "alternatives": [
                        {
                            "title": "邻镇辖区",
                            "description": "本镇没有常驻警力，由邻镇负责。",
                            "tradeoff": "求助需要额外旅行时间。",
                        },
                        {
                            "title": "临时巡警驻点",
                            "description": "车站旁只有一处临时值守点。",
                            "tradeoff": "可用档案和人手较少。",
                        },
                    ],
                    "template_binding": template_binding,
                },
            }
        )
        context = ContextAssembly(
            messages=[
                {"role": "system", "content": "source-bound"},
                {"role": "user", "content": values["player_intent"]},
            ],
            included_sources=[
                {
                    "kind": "scene_director_analysis",
                    "id": values["analysis_snapshot"]["fingerprint"],
                    "label": "gap",
                    "content": "world gap",
                    "visibility": "kp",
                }
            ],
            excluded_sources=[],
            token_estimate=42,
            visibility_scope="kp",
        )
        return SimpleNamespace(output=output, context=context)


class SequenceWorldExpansionLlm:
    def __init__(self, outputs: list[dict]) -> None:
        self.outputs = outputs
        self.calls: list[list] = []

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls.append(list(messages))
        return json.dumps(self.outputs[len(self.calls) - 1], ensure_ascii=False)


def _world_expansion_output(relation_slot_id: str) -> dict:
    return {
        "public_narration": "镇中心可以找到一间治安官办公室。",
        "kp_notes": "候选仍需 KP 批准。",
        "candidate": {
            "expansion_kind": "environment",
            "subject": "小镇治安设施",
            "proposal": "设置治安官办公室与一名值班巡警。",
            "rationale": "符合当前聚落的时代和规模。",
            "confidence": "medium",
            "assumptions": [],
            "conflicts": [],
            "alternatives": [
                {"title": "邻镇管辖", "description": "由邻镇负责。", "tradeoff": "响应较慢。"},
                {"title": "兼职治安", "description": "由居民兼任。", "tradeoff": "资源有限。"},
            ],
            "branch_plan": None,
            "template_binding": {
                "setting_pack_id": "us.1920s",
                "settlement_kind": "town",
                "slot_id": "law_enforcement",
                "building_variant": "治安官办公室",
                "profession_ids": ["law_officer"],
                "source_entity_ids": [],
                "entity_bindings": [
                    {
                        "local_ref": "local_agency",
                        "archetype_id": "public_safety_agency",
                        "entity_kind": "organization",
                        "label_variant": "警长办公室",
                        "profession_ids": [],
                        "relation_bindings": [],
                    },
                    {
                        "local_ref": "duty_officer",
                        "archetype_id": "public_safety_officer",
                        "entity_kind": "npc",
                        "label_variant": "巡警",
                        "profession_ids": ["law_officer"],
                        "relation_bindings": [
                            {
                                "relation_slot_id": relation_slot_id,
                                "target_source": "candidate",
                                "target_ref": "local_agency",
                            }
                        ],
                    },
                ],
            },
        },
    }


def setup_world_gap(tmp_path: Path) -> tuple:
    connection = connect(tmp_path / "world-expansion.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    campaign = repo.create_campaign("世界补全测试", current_time="1928-10-03")
    session = SessionService(repo).create(
        campaign["id"],
        kp_display_name="OldOnes",
    )
    identity = repo.authenticate_access_token(session["access_token"])
    assert identity is not None
    module = repo.create_module(
        campaign["id"],
        "只描述钟楼的模组",
        [
            ModuleChunk(
                title="钟楼",
                text="钟楼门前散落着破碎的玻璃。",
                visibility="kp",
                spoiler_tag="act-1",
                scene_key="town",
                order_index=0,
            )
        ],
    )
    run = repo.start_campaign_module_run(
        campaign_id=campaign["id"],
        module_id=module["id"],
        current_scene_key="town",
        active_spoiler_tags=["act-1"],
        state={},
        started_by_member_id=identity.member_id,
    )
    return connection, repo, campaign, identity, run


def _minimal_world_expansion_analysis() -> dict:
    return {
        "fingerprint": "world-gap-semantic-repair",
        "entity_states": [],
        "anchors": [],
        "world_fact_heads": [],
        "settlement_template": {
            "setting_pack_id": "us.1920s",
            "settlement_kind": "town",
            "source_entity_bindings": [],
            "scene_slots": [
                {
                    "slot_id": "law_enforcement",
                    "selected": True,
                    "building_candidates": ["治安官办公室"],
                    "profession_candidates": [{"profession_id": "law_officer"}],
                    "entity_archetype_candidates": [
                        {
                            "archetype_id": "public_safety_agency",
                            "entity_kind": "organization",
                            "label_variants": ["警长办公室"],
                            "profession_ids": [],
                            "relation_slots": [],
                        },
                        {
                            "archetype_id": "public_safety_officer",
                            "entity_kind": "npc",
                            "label_variants": ["巡警"],
                            "profession_ids": ["law_officer"],
                            "relation_slots": [
                                {
                                    "relation_slot_id": "agency",
                                    "target_kinds": ["organization"],
                                    "target_archetype_ids": ["public_safety_agency"],
                                    "minimum_count": 1,
                                    "maximum_count": 1,
                                }
                            ],
                        },
                    ],
                }
            ],
        },
    }


def test_orchestrator_repairs_semantically_invalid_world_binding_once(
    tmp_path: Path,
) -> None:
    connection, _repo, campaign, _identity, _run = setup_world_gap(tmp_path)
    llm = SequenceWorldExpansionLlm(
        [
            _world_expansion_output("custodian"),
            _world_expansion_output("agency"),
        ]
    )
    try:
        result = asyncio.run(
            KpOrchestrator(connection, llm).handle_world_expansion(
                campaign_id=campaign["id"],
                player_intent="寻找镇上的治安机构",
                analysis_snapshot=_minimal_world_expansion_analysis(),
            )
        )
    finally:
        connection.close()

    assert result.repaired is True
    assert len(llm.calls) == 2
    repair_message = llm.calls[1][-1].content
    assert "custodian" in repair_message
    assert "allowed=agency" in repair_message
    assert (
        result.output.candidate.template_binding.entity_bindings[1]
        .relation_bindings[0]
        .relation_slot_id
        == "agency"
    )


def test_world_expansion_transport_normalizes_only_authority_neutral_fields() -> None:
    payload = _world_expansion_output("agency")
    payload["public_narration"] = ""
    binding = payload["candidate"]["template_binding"]
    binding["source_entity_ids"] = ["invented-source"]
    agency, officer = binding["entity_bindings"]
    agency["local_ref"] = "治安机构"
    officer["local_ref"] = "值班警员"
    officer["relation_bindings"][0].update(
        {"target_source": "existing_entity", "target_ref": "治安机构"}
    )

    normalized_raw, changed = normalize_world_expansion_transport(
        json.dumps(payload, ensure_ascii=False),
        _minimal_world_expansion_analysis(),
    )
    normalized = parse_world_expansion_output(normalized_raw)

    assert changed is True
    assert normalized.public_narration == normalized.candidate.proposal
    assert normalized.candidate.template_binding is not None
    assert normalized.candidate.template_binding.source_entity_ids == []
    normalized_entities = normalized.candidate.template_binding.entity_bindings
    assert [item.local_ref for item in normalized_entities] == ["entity_1", "entity_2"]
    assert normalized_entities[1].relation_bindings[0].target_source == "candidate"
    assert normalized_entities[1].relation_bindings[0].target_ref == "entity_1"


def test_world_expansion_output_requires_real_alternatives() -> None:
    with pytest.raises(ValueError):
        parse_world_expansion_output(
            """{
              "public_narration":"候选",
              "kp_notes":"",
              "candidate":{
                "expansion_kind":"environment",
                "subject":"警务设施",
                "proposal":"治安官办公室",
                "rationale":"符合时代",
                "confidence":"medium",
                "assumptions":[],
                "conflicts":[],
                "alternatives":[
                  {"title":"一个方案","description":"内容","tradeoff":"取舍"}
                ]
              }
            }"""
        )


def test_world_template_binding_rejects_duplicate_relation_before_persistence() -> None:
    payload = _world_expansion_output("agency")
    officer = payload["candidate"]["template_binding"]["entity_bindings"][1]
    officer["relation_bindings"].append(dict(officer["relation_bindings"][0]))

    with pytest.raises(ValueError, match="relations must be unique"):
        WorldExpansionOutput.model_validate(payload)


def test_dynamic_branch_requires_causal_plan_and_known_references() -> None:
    base = {
        "expansion_kind": "reactive_branch",
        "subject": "治安官的调查",
        "proposal": "治安官先核实访客身份，再决定是否提供旧档案。",
        "rationale": "给出有动机且可中止的调查支线。",
        "confidence": "medium",
        "assumptions": [],
        "conflicts": [],
        "alternatives": [
            {"title": "拒绝", "description": "不提供档案", "tradeoff": "线索较慢"},
            {"title": "陪同", "description": "陪同查阅", "tradeoff": "行动受监督"},
        ],
    }
    with pytest.raises(ValueError, match="branch_plan"):
        WorldExpansionCandidate.model_validate(base)

    planned = {
        **base,
        "branch_plan": {
            "goal": "在不破坏钟楼锚点的情况下回应调查。",
            "entry_conditions": [
                {
                    "condition_type": "entity_state",
                    "reference": "npc_sheriff",
                    "operator": "equals",
                    "expected": "available",
                    "rationale": "治安官必须可以行动。",
                }
            ],
            "beats": [
                {
                    "beat_id": "verify_visitors",
                    "title": "核实访客",
                    "character_intent": "治安官想避免泄露敏感档案。",
                    "action": "询问来意并检查介绍信。",
                    "preconditions": [],
                    "expected_effects": [
                        {
                            "effect_type": "narrative_only",
                            "reference": None,
                            "description": "治安官形成初步态度。",
                            "requires_contact": True,
                        }
                    ],
                    "failure_policy": "pause_for_kp",
                }
            ],
            "anchor_guards": [],
            "completion_conditions": [
                {
                    "condition_type": "scene",
                    "reference": "current_scene_key",
                    "operator": "equals",
                    "expected": "town",
                    "rationale": "支线只在当前小镇场景内结算。",
                }
            ],
        },
    }
    candidate = WorldExpansionCandidate.model_validate(planned)
    validate_world_expansion_plan(
        candidate,
        {
            "requested_expansion_kind": "reactive_branch",
            "entity_states": [
                {
                    "entity_id": "npc_sheriff",
                    "entity_type": "npc",
                    "name": "治安官",
                    "status": "available",
                }
            ],
            "anchors": [],
            "world_fact_heads": [],
        },
    )
    broken = WorldExpansionCandidate.model_validate(
        {
            **planned,
            "branch_plan": {
                **planned["branch_plan"],
                "entry_conditions": [
                    {
                        "condition_type": "entity_state",
                        "reference": "invented_npc",
                        "operator": "exists",
                        "expected": None,
                        "rationale": "模型编造的引用。",
                    }
                ],
            },
        }
    )
    with pytest.raises(ValueError, match="unknown entity"):
        validate_world_expansion_plan(
            broken,
            {
                "requested_expansion_kind": "reactive_branch",
                "entity_states": [],
                "anchors": [],
                "world_fact_heads": [],
            },
        )

    with pytest.raises(StructuredOutputError, match="server-selected workflow"):
        validate_world_expansion_plan(
            candidate,
            {
                "requested_expansion_kind": "environment",
                "entity_states": [],
                "anchors": [],
                "world_fact_heads": [],
            },
        )


def test_world_gap_creates_one_source_bound_draft_and_reuses_it(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, run = setup_world_gap(tmp_path)
    try:
        service = TurnService(repo)
        director = FakeWorldExpansionDirector()
        command = WorldExpansionCommand(
            run_id=run["id"],
            player_intent="我去寻找镇上的警察局",
        )
        proposal = asyncio.run(
            service.create_world_expansion_proposal(
                command,
                identity,
                director,
                source_model="fake-local-model",
            )
        )
        repeated = asyncio.run(
            service.create_world_expansion_proposal(
                command,
                identity,
                director,
                source_model="fake-local-model",
            )
        )

        assert proposal["id"] == repeated["id"]
        assert director.calls == 1
        assert proposal["proposal_kind"] == "world_expansion"
        assert proposal["world_expansion"]["module_run_id"] == run["id"]
        assert proposal["world_expansion"]["module_run_version"] == run["version"]
        assert proposal["world_expansion"]["analysis"]["decision"] == "world_gap"
        assert len(proposal["world_expansion"]["candidate"]["alternatives"]) == 2
        assert proposal["proposed_events"] == []
        assert proposal["proposed_memories"] == []
        assert repo.get_context_assembly(proposal["id"]) is not None

        approved = service.approve(
            proposal["id"],
            campaign["id"],
            identity,
            note="采用治安官办公室方案。",
        )
        assert approved["status"] == "approved"
        assert approved["proposal_kind"] == "world_expansion"
    finally:
        connection.close()


def test_world_gap_freezes_selected_settlement_template_for_shared_director(
    tmp_path: Path,
) -> None:
    connection, repo, _campaign, identity, run = setup_world_gap(tmp_path)
    try:
        director = FakeWorldExpansionDirector()
        proposal = asyncio.run(
            TurnService(repo).create_world_expansion_proposal(
                WorldExpansionCommand(
                    run_id=run["id"],
                    player_intent="我去寻找镇上的警察局",
                    setting_pack_id="us.1920s",
                    settlement_kind="town",
                ),
                identity,
                director,
                source_model="fake-local-model",
            )
        )

        template = proposal["world_expansion"]["analysis"]["settlement_template"]
        assert template == director.last_analysis_snapshot["settlement_template"]
        assert template["setting_pack_id"] == "us.1920s"
        assert template["settlement_kind"] == "town"
        assert {item["slot_id"] for item in template["scene_slots"]} == {
            "law_enforcement"
        }
        law_enforcement = template["scene_slots"][0]
        assert law_enforcement["selected"] is True
        assert "小镇警察局" in law_enforcement["building_candidates"]
        assert "agent_workflow" not in template
        candidate = WorldExpansionCandidate.model_validate(
            proposal["world_expansion"]["candidate"]
        )
        assert candidate.template_binding is not None
        assert candidate.template_binding.slot_id == "law_enforcement"
        assert candidate.template_binding.entity_bindings[0].local_ref == "duty_officer"

        invented_building = candidate.model_copy(
            update={
                "template_binding": candidate.template_binding.model_copy(
                    update={"building_variant": "现代数字取证中心"}
                )
            }
        )
        with pytest.raises(StructuredOutputError, match="invented a building"):
            validate_world_expansion_plan(
                invented_building, {"settlement_template": template}
            )

        invented_profession = candidate.model_copy(
            update={
                "template_binding": candidate.template_binding.model_copy(
                    update={"profession_ids": ["cybercrime_specialist"]}
                )
            }
        )
        with pytest.raises(StructuredOutputError, match="invented professions"):
            validate_world_expansion_plan(
                invented_profession, {"settlement_template": template}
            )

        invented_source_entity = candidate.model_copy(
            update={
                "template_binding": candidate.template_binding.model_copy(
                    update={"source_entity_ids": ["not-in-source-profile"]}
                )
            }
        )
        with pytest.raises(StructuredOutputError, match="invented source entities"):
            validate_world_expansion_plan(
                invented_source_entity, {"settlement_template": template}
            )

        invented_archetype = candidate.model_copy(
            update={
                "template_binding": candidate.template_binding.model_copy(
                    update={
                        "entity_bindings": [
                            candidate.template_binding.entity_bindings[0].model_copy(
                                update={"archetype_id": "modern_forensics_ai"}
                            )
                        ]
                    }
                )
            }
        )
        with pytest.raises(StructuredOutputError, match="invented entity archetype"):
            validate_world_expansion_plan(
                invented_archetype, {"settlement_template": template}
            )

        wrong_entity_kind = candidate.model_copy(
            update={
                "template_binding": candidate.template_binding.model_copy(
                    update={
                        "entity_bindings": [
                            candidate.template_binding.entity_bindings[0].model_copy(
                                update={"entity_kind": "organization"}
                            )
                        ]
                    }
                )
            }
        )
        with pytest.raises(StructuredOutputError, match="changed entity kind"):
            validate_world_expansion_plan(
                wrong_entity_kind, {"settlement_template": template}
            )

        missing_required_relation = candidate.model_copy(
            update={
                "template_binding": candidate.template_binding.model_copy(
                    update={
                        "entity_bindings": [
                            candidate.template_binding.entity_bindings[0].model_copy(
                                update={"relation_bindings": []}
                            ),
                            candidate.template_binding.entity_bindings[1],
                        ]
                    }
                )
            }
        )
        with pytest.raises(StructuredOutputError, match="relation cardinality failed"):
            validate_world_expansion_plan(
                missing_required_relation, {"settlement_template": template}
            )
    finally:
        connection.close()


def test_world_gap_inherits_the_run_pinned_setting_profile_without_ui_parameters(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, run = setup_world_gap(tmp_path)
    try:
        settings = ModuleSettingAnalysisService(repo)
        profile = settings.create_profile(
            CreateSettingProfileCommand(
                module_id=run["module_id"],
                title="运行固定时代",
                setting_pack_id="us.1920s",
                regions=(
                    ConfiguredRegion(
                        region_id="run-region",
                        title="运行区域",
                        pattern_id="county_town_network",
                        role_counts={},
                    ),
                ),
            ),
            member_id=identity.member_id,
        )
        settlement_id = profile["document"]["settlements"][0]["settlement_id"]
        chunk_id = repo.list_module_chunks(run["module_id"])[0]["id"]
        candidate = ModuleKnowledgeService(repo).create_manual_candidate(
            run["module_id"],
            {
                "kind": "reference",
                "title": "破碎玻璃",
                "statement": "钟楼门前散落着破碎的玻璃。",
                "visibility": "kp",
                "spoiler_tag": "act-1",
                "entity_name": "破碎的玻璃",
                "entity_type": "item",
                "citations": [{
                    "chunk_id": chunk_id,
                    "evidence_text": "钟楼门前散落着破碎的玻璃。",
                }],
            },
        )
        repo.review_module_knowledge_candidate(
            candidate["id"],
            decision="approved",
            member_id=identity.member_id,
            note="测试来源实体绑定",
        )
        source_item = ModuleGraphService(repo).create_entity(
            run["module_id"],
            ModuleEntityCreate(
                entity_type="item",
                name="破碎的玻璃",
                description="钟楼门前的物证",
                spoiler_tag="act-1",
                source_candidate_id=candidate["id"],
            ),
            member_id=identity.member_id,
        )
        initial_document = SettingProfileDocument.model_validate(profile["document"])
        with_entity = initial_document.model_copy(
            update={
                "entity_bindings": (
                    ConfiguredEntityBinding(
                        module_entity_id=source_item["id"],
                        archetype_id="portable_evidence_object",
                        settlement_id=settlement_id,
                        slot_id="law_enforcement",
                    ),
                )
            }
        )
        profile = settings.update_profile(
            UpdateSettingProfileCommand(
                profile_id=profile["id"],
                expected_version=1,
                title=profile["title"],
                document=with_entity,
            ),
            member_id=identity.member_id,
        )
        selected = settings.select_for_run(
            SelectRunSettingCommand(
                run_id=run["id"],
                expected_run_version=run["version"],
                profile_id=profile["id"],
                profile_version=profile["version"],
                settlement_id=settlement_id,
                reason="测试 Full AI 继承",
            ),
            member_id=identity.member_id,
        )
        document = SettingProfileDocument.model_validate(profile["document"])
        revised = document.model_copy(
            update={
                "settlements": (
                    document.settlements[0].model_copy(update={"title": "更新后的名称"}),
                    *document.settlements[1:],
                )
            }
        )
        latest = settings.update_profile(
            UpdateSettingProfileCommand(
                profile_id=profile["id"],
                expected_version=2,
                title=profile["title"],
                document=revised,
            ),
            member_id=identity.member_id,
        )
        director = FakeWorldExpansionDirector()

        proposal = asyncio.run(
            TurnService(repo).create_world_expansion_proposal(
                WorldExpansionCommand(
                    run_id=run["id"],
                    player_intent="我去寻找镇上的警察局",
                ),
                identity,
                director,
                source_model="fake-local-model",
            )
        )

        template = proposal["world_expansion"]["analysis"]["settlement_template"]
        assert selected["run"]["version"] == run["version"] + 1
        assert latest["version"] == 3
        assert template["setting_pack_id"] == "us.1920s"
        assert template["setting_profile"] == {
            "profile_id": profile["id"],
            "profile_version": 2,
            "profile_content_hash": profile["content_hash"],
            "settlement_id": settlement_id,
            "selection_version": 1,
            "run_version": selected["run"]["version"],
        }
        assert template["source_entity_bindings"][0]["entity"] == {
            "entity_id": source_item["id"],
            "entity_type": "item",
            "name": "破碎的玻璃",
            "description": "钟楼门前的物证",
        }
        assert template["source_entity_bindings"][0]["archetype"]["archetype_id"] == (
            "portable_evidence_object"
        )
        candidate = proposal["world_expansion"]["candidate"]
        assert candidate["template_binding"]["source_entity_ids"] == [source_item["id"]]
        assert director.last_analysis_snapshot["settlement_template"] == template
        approved = TurnService(repo).approve(
            proposal["id"], campaign["id"], identity
        )
        materialized = WorldExpansionMaterializationService(repo).materialize(
            approved["id"],
            identity,
            MaterializeWorldExpansionCommand(
                idempotency_key="contact:pinned-source-entity",
                summary="调查员接触警长办公室并核对钟楼物证。",
                happened_at="1928-10-03 22:15",
                facts=(
                    EncounterFact(
                        fact_type="canonical_fact",
                        subject="钟楼物证",
                        predicate="已经核对",
                        object_text="调查员核对了破碎玻璃。",
                    ),
                ),
                entities=(
                    EncounterEntityRealization(
                        local_ref="duty_officer",
                        name="值班巡警",
                    ),
                    EncounterEntityRealization(
                        local_ref="local_agency",
                        name="警长办公室",
                    ),
                ),
            ),
        )
        receipt = materialized["world_expansion_materialization"]
        assert len(receipt["world_entity_ids"]) == 3
        source_world_entity = repo.find_campaign_world_entity_by_origin(
            campaign["id"], "module_source", source_item["id"]
        )
        assert source_world_entity is not None
        assert source_world_entity["entity_kind"] == "item"
        assert source_world_entity["archetype_id"] == "portable_evidence_object"
        assert source_world_entity["visibility"] == "kp"
        context = ContextBuilder(connection).build(
            campaign_id=campaign["id"],
            player_action="我重新检查钟楼门前的破碎玻璃。",
            active_spoiler_tags=("act-1",),
        )
        assert source_world_entity["id"] in {
            item["id"]
            for item in context.included_sources
            if item["kind"] == "campaign_world_entity"
        }
    finally:
        connection.close()


def test_balanced_world_gap_can_auto_materialize_environment_candidate(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, run = setup_world_gap(tmp_path)
    try:
        repo.set_module_run_automation_level(
            run["id"],
            expected_version=run["version"],
            level="balanced",
            reason="允许低副作用环境补全自动落地。",
            member_id=identity.member_id,
        )
        result = asyncio.run(
            AutoWorldExpansionService(repo).create_and_maybe_materialize(
                WorldExpansionCommand(
                    run_id=run["id"],
                    player_intent="我去寻找镇上的警察局",
                ),
                identity,
                FakeWorldExpansionDirector(),
                source_model="fake-local-model",
            )
        )

        assert result.status == "materialized"
        assert result.materialization is not None
        assert result.proposal is not None
        assert result.proposal["status"] == "approved"
        assert result.policy is not None
        assert result.policy["allowed"] is True
        heads = repo.list_fact_heads(campaign["id"])
        assert [(item.fact.subject, item.fact.predicate) for item in heads] == [
            ("小镇警务设施", "存在或成立")
        ]
        actions = repo.list_proposal_actions(result.proposal["id"])
        assert "auto_world_expansion_policy" in [item["action_type"] for item in actions]
        assert "world_expansion_materialized" in [item["action_type"] for item in actions]
    finally:
        connection.close()


def test_full_ai_materializes_typed_entities_through_the_shared_contact_kernel(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, run = setup_world_gap(tmp_path)
    try:
        repo.set_module_run_automation_level(
            run["id"],
            expected_version=run["version"],
            level="ai_kp",
            reason="测试 Full AI 类型化实体落地。",
            member_id=identity.member_id,
        )
        result = asyncio.run(
            AutoWorldExpansionService(repo).create_and_maybe_materialize(
                WorldExpansionCommand(
                    run_id=run["id"],
                    player_intent="我去寻找镇上的警察局",
                    setting_pack_id="us.1920s",
                    settlement_kind="town",
                ),
                identity,
                FakeWorldExpansionDirector(),
                source_model="fake-local-model",
            )
        )

        assert result.status == "materialized"
        assert result.materialization is not None
        assert len(result.materialization["world_entity_ids"]) == 2
        assert {(item["entity_kind"], item["name"]) for item in repo.list_campaign_world_entities(campaign["id"])} == {
            ("npc", "巡警"),
            ("organization", "警长办公室"),
        }
        assert len(repo.list_campaign_world_entity_relations(campaign["id"])) == 1
    finally:
        connection.close()


def test_conservative_world_gap_stays_reviewable_without_materialization(
    tmp_path: Path,
) -> None:
    connection, repo, _campaign, identity, run = setup_world_gap(tmp_path)
    try:
        result = asyncio.run(
            AutoWorldExpansionService(repo).create_and_maybe_materialize(
                WorldExpansionCommand(
                    run_id=run["id"],
                    player_intent="我去寻找镇上的警察局",
                ),
                identity,
                FakeWorldExpansionDirector(),
                source_model="fake-local-model",
            )
        )

        assert result.status == "needs_attention"
        assert result.proposal is not None
        assert result.proposal["status"] == "draft"
        assert result.materialization is None
        assert result.policy is not None
        assert result.policy["blockers"] == ["automation_level:conservative"]
    finally:
        connection.close()


def test_ai_kp_world_gap_does_not_materialize_low_confidence_candidate(
    tmp_path: Path,
) -> None:
    connection, repo, _campaign, identity, run = setup_world_gap(tmp_path)
    try:
        repo.set_module_run_automation_level(
            run["id"],
            expected_version=run["version"],
            level="ai_kp",
            reason="测试低置信安全阀。",
            member_id=identity.member_id,
        )
        result = asyncio.run(
            AutoWorldExpansionService(repo).create_and_maybe_materialize(
                WorldExpansionCommand(
                    run_id=run["id"],
                    player_intent="我去寻找镇上的警察局",
                ),
                identity,
                FakeWorldExpansionDirector(confidence="low"),
                source_model="fake-local-model",
            )
        )

        assert result.status == "needs_attention"
        assert result.proposal is not None
        assert result.proposal["status"] == "draft"
        assert result.policy is not None
        assert result.policy["blockers"] == ["low_confidence"]
        assert repo.get_world_expansion_materialization(result.proposal["id"]) is None
    finally:
        connection.close()


def test_world_expansion_approval_rejects_a_stale_scene_basis(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, run = setup_world_gap(tmp_path)
    try:
        service = TurnService(repo)
        proposal = asyncio.run(
            service.create_world_expansion_proposal(
                WorldExpansionCommand(
                    run_id=run["id"],
                    player_intent="我去寻找镇上的警察局",
                ),
                identity,
                FakeWorldExpansionDirector(),
                source_model="fake-local-model",
            )
        )
        repo.transition_module_run_scene(
            run["id"],
            expected_version=run["version"],
            scene_key="clocktower",
            scene_title="钟楼内部",
            play_pace="freeform",
            location_entity_id=None,
            world_time="1928-10-03 22:00",
            note="玩家先去了钟楼。",
            member_id=identity.member_id,
        )

        with pytest.raises(ConflictError, match="stale"):
            service.approve(
                proposal["id"],
                campaign["id"],
                identity,
            )
        assert repo.get_turn_proposal(proposal["id"])["status"] == "draft"
        event_count = connection.execute(
            "SELECT COUNT(*) AS total FROM events WHERE campaign_id = ?",
            (campaign["id"],),
        ).fetchone()["total"]
        assert event_count == 0
    finally:
        connection.close()


def test_world_expansion_approval_rejects_changed_world_facts(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, run = setup_world_gap(tmp_path)
    try:
        service = TurnService(repo)
        proposal = asyncio.run(
            service.create_world_expansion_proposal(
                WorldExpansionCommand(
                    run_id=run["id"],
                    player_intent="我去寻找镇上的警察局",
                ),
                identity,
                FakeWorldExpansionDirector(),
                source_model="fake-local-model",
            )
        )
        FactService(repo).assert_fact(
            campaign["id"],
            identity,
            AssertWorldFactCommand(
                fact_type="canonical_fact",
                subject="小镇",
                predicate="治安辖区",
                object_text="本镇由邻镇治安官负责，没有常驻警力。",
            ),
        )

        with pytest.raises(ConflictError, match="stale"):
            service.approve(
                proposal["id"],
                campaign["id"],
                identity,
            )
        assert repo.get_turn_proposal(proposal["id"])["status"] == "draft"
    finally:
        connection.close()

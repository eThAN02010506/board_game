import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_kp.application.dynamic_branch_service import (
    DynamicBranchService,
    ResolveBranchBeatCommand,
    ResumeDynamicBranchCommand,
)
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import TurnService, WorldExpansionCommand
from ai_kp.application.world_expansion_materialization_service import (
    EncounterFact,
    MaterializeWorldExpansionCommand,
    WorldExpansionMaterializationService,
)
from ai_kp.director.context_builder import ContextAssembly
from ai_kp.director.world_expansion import WorldExpansionOutput
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.modules.ingestion import ModuleChunk


class DynamicBranchDirector:
    async def handle_world_expansion(self, **values):
        return SimpleNamespace(
            output=WorldExpansionOutput.model_validate(
                {
                    "public_narration": "治安官愿意先听取调查员的来意。",
                    "kp_notes": "支线在实际接触后才激活。",
                    "candidate": {
                        "expansion_kind": "reactive_branch",
                        "subject": "治安官的调查",
                        "proposal": "先核实访客身份，再决定是否开放档案。",
                        "rationale": "NPC 行动有明确意图，且不修改模组锚点。",
                        "confidence": "medium",
                        "assumptions": [],
                        "conflicts": [],
                        "alternatives": [
                            {
                                "title": "拒绝",
                                "description": "治安官拒绝开放档案。",
                                "tradeoff": "线索获取更慢。",
                            },
                            {
                                "title": "陪同",
                                "description": "由书记员全程陪同。",
                                "tradeoff": "调查行动受监督。",
                            },
                        ],
                        "branch_plan": {
                            "goal": "确定调查员是否可以查阅旧档案。",
                            "entry_conditions": [
                                {
                                    "condition_type": "scene",
                                    "reference": "current_scene_key",
                                    "operator": "equals",
                                    "expected": "town",
                                    "rationale": "只在镇内开始支线。",
                                }
                            ],
                            "beats": [
                                {
                                    "beat_id": "verify",
                                    "title": "核实身份",
                                    "character_intent": "治安官避免泄露敏感信息。",
                                    "action": "询问来意并检查介绍信。",
                                    "preconditions": [],
                                    "expected_effects": [
                                        {
                                            "effect_type": "narrative_only",
                                            "reference": None,
                                            "description": "形成初步态度。",
                                            "requires_contact": True,
                                        }
                                    ],
                                    "failure_policy": "skip",
                                },
                                {
                                    "beat_id": "review",
                                    "title": "审阅档案",
                                    "character_intent": "书记员保护档案完整性。",
                                    "action": "在陪同下查阅登记册。",
                                    "preconditions": [],
                                    "expected_effects": [
                                        {
                                            "effect_type": "fact_candidate",
                                            "reference": None,
                                            "description": "可能发现一条新线索。",
                                            "requires_contact": True,
                                        }
                                    ],
                                    "failure_policy": "pause_for_kp",
                                },
                            ],
                            "anchor_guards": [],
                            "completion_conditions": [
                                {
                                    "condition_type": "scene",
                                    "reference": "current_scene_key",
                                    "operator": "equals",
                                    "expected": "town",
                                    "rationale": "离开小镇后不自动结算。",
                                }
                            ],
                        },
                    },
                }
            ),
            context=ContextAssembly(
                messages=[{"role": "system", "content": "source-bound"}],
                included_sources=[],
                excluded_sources=[],
                token_estimate=10,
                visibility_scope="kp",
            ),
        )


def _setup(tmp_path: Path):
    connection = connect(tmp_path / "dynamic-branches.sqlite3")
    init_db(connection)
    repo = Repository(connection)
    campaign = repo.create_campaign("动态支线测试", current_time="1928-10-03")
    session = SessionService(repo).create(
        campaign["id"],
        kp_display_name="OldOnes",
    )
    identity = repo.authenticate_access_token(session["access_token"])
    assert identity is not None
    module = repo.create_module(
        campaign["id"],
        "钟楼大纲",
        [
            ModuleChunk(
                title="钟楼",
                text="钟楼门前散落着玻璃。",
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


def _approved_branch(tmp_path: Path):
    connection, repo, campaign, identity, run = _setup(tmp_path)
    proposal = asyncio.run(
        TurnService(repo).create_world_expansion_proposal(
            WorldExpansionCommand(
                run_id=run["id"],
                player_intent="我去找治安官查旧档案",
                requested_expansion_kind="reactive_branch",
            ),
            identity,
            DynamicBranchDirector(),
            source_model="fake-local-model",
        )
    )
    TurnService(repo).approve(
        proposal["id"],
        campaign["id"],
        identity,
        note="批准候选支线。",
    )
    branch = repo.get_dynamic_branch_for_proposal(proposal["id"])
    assert branch is not None
    return connection, repo, campaign, identity, proposal, branch


def test_branch_is_approved_then_activated_only_after_actual_contact(
    tmp_path: Path,
) -> None:
    connection, repo, _campaign, identity, proposal, branch = _approved_branch(
        tmp_path
    )
    try:
        assert branch["status"] == "approved"
        materialized = WorldExpansionMaterializationService(repo).materialize(
            proposal["id"],
            identity,
            MaterializeWorldExpansionCommand(
                idempotency_key="branch-contact-001",
                summary="调查员在治安官办公室进行了当面交涉。",
                happened_at="1928-10-03 14:00",
                facts=(
                    EncounterFact(
                        fact_type="canonical_fact",
                        subject="治安官办公室",
                        predicate="已接触",
                        object_text="调查员已与治安官当面交涉。",
                    ),
                ),
            ),
        )
        activated = repo.get_dynamic_branch_for_proposal(proposal["id"])
        assert activated is not None
        assert activated["status"] == "active"
        assert activated["version"] == 1
        action = materialized["actions"][-1]
        assert action["payload"]["dynamic_branch_id"] == activated["id"]
    finally:
        connection.close()


def test_branch_progress_is_replayable_and_never_writes_world_facts(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, proposal, _branch = _approved_branch(tmp_path)
    try:
        WorldExpansionMaterializationService(repo).materialize(
            proposal["id"],
            identity,
            MaterializeWorldExpansionCommand(
                idempotency_key="branch-contact-002",
                summary="调查员与治安官开始交涉。",
                happened_at=None,
                facts=(
                    EncounterFact(
                        fact_type="canonical_fact",
                        subject="治安官",
                        predicate="已接触",
                        object_text="调查员已经见到治安官。",
                    ),
                ),
            ),
        )
        branch = repo.get_dynamic_branch_for_proposal(proposal["id"])
        assert branch is not None
        fact_count = len(repo.list_fact_heads(campaign["id"]))
        service = DynamicBranchService(repo)
        first = service.resolve_beat(
            branch["id"],
            identity,
            ResolveBranchBeatCommand(
                expected_version=branch["version"],
                command_id="resolve-branch-001",
                outcome="succeeded",
                observed_effects=("治安官确认介绍信格式无误。",),
            ),
        )
        assert first["branch"]["current_beat_index"] == 1
        paused = service.resolve_beat(
            branch["id"],
            identity,
            ResolveBranchBeatCommand(
                expected_version=first["branch"]["version"],
                command_id="resolve-branch-002",
                outcome="failed",
                note="书记员发现登记册缺页。",
            ),
        )
        assert paused["branch"]["status"] == "paused"
        resumed = service.resume(
            branch["id"],
            identity,
            ResumeDynamicBranchCommand(
                expected_version=paused["branch"]["version"],
                command_id="resume-branch-001",
                note="人类 KP 决定由治安官提供备份登记。",
            ),
        )
        completed = service.resolve_beat(
            branch["id"],
            identity,
            ResolveBranchBeatCommand(
                expected_version=resumed["branch"]["version"],
                command_id="resolve-branch-003",
                outcome="succeeded",
            ),
        )
        assert completed["branch"]["status"] == "completed"
        replay = service.resolve_beat(
            branch["id"],
            identity,
            ResolveBranchBeatCommand(
                expected_version=0,
                command_id="resolve-branch-003",
                outcome="succeeded",
            ),
        )
        assert replay["idempotent_replay"] is True
        assert len(repo.list_fact_heads(campaign["id"])) == fact_count
        assert completed["event"]["payload"]["world_writes_performed"] is False
    finally:
        connection.close()


def test_branch_service_denies_player_and_cross_campaign_identity(
    tmp_path: Path,
) -> None:
    connection, _repo, _campaign, identity, _proposal, branch = _approved_branch(
        tmp_path
    )
    try:
        service = DynamicBranchService(_repo)
        with pytest.raises(PermissionError):
            service.get(branch["id"], replace(identity, role="player"))
        with pytest.raises(KeyError):
            service.get(
                branch["id"],
                replace(identity, campaign_id="campaign_other"),
            )
    finally:
        connection.close()


def test_branch_pauses_when_completion_condition_no_longer_holds(
    tmp_path: Path,
) -> None:
    connection, repo, _campaign, identity, proposal, _branch = _approved_branch(
        tmp_path
    )
    try:
        WorldExpansionMaterializationService(repo).materialize(
            proposal["id"],
            identity,
            MaterializeWorldExpansionCommand(
                idempotency_key="branch-contact-003",
                summary="调查员进入治安官办公室。",
                happened_at=None,
                facts=(
                    EncounterFact(
                        fact_type="canonical_fact",
                        subject="治安官办公室",
                        predicate="已进入",
                        object_text="调查员已经进入治安官办公室。",
                    ),
                ),
            ),
        )
        branch = repo.get_dynamic_branch_for_proposal(proposal["id"])
        assert branch is not None
        service = DynamicBranchService(repo)
        first = service.resolve_beat(
            branch["id"],
            identity,
            ResolveBranchBeatCommand(
                expected_version=branch["version"],
                command_id="resolve-branch-004",
                outcome="succeeded",
            ),
        )
        run = repo.get_campaign_module_run(branch["module_run_id"])
        repo.transition_module_run_scene(
            run["id"],
            expected_version=run["version"],
            scene_key="railway",
            scene_title="离镇列车",
            play_pace="freeform",
            location_entity_id=None,
            world_time=None,
            note="调查员离开了支线适用场景。",
            member_id=identity.member_id,
        )
        result = service.resolve_beat(
            branch["id"],
            identity,
            ResolveBranchBeatCommand(
                expected_version=first["branch"]["version"],
                command_id="resolve-branch-005",
                outcome="succeeded",
            ),
        )
        assert result["branch"]["status"] == "paused"
        assert result["event"]["payload"]["completion_condition_failures"]
    finally:
        connection.close()

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_kp.application.errors import ConflictError
from ai_kp.application.fact_service import AssertWorldFactCommand, FactService
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import (
    TurnService,
    WorldExpansionCommand,
)
from ai_kp.director.context_builder import ContextAssembly
from ai_kp.director.world_expansion import (
    WorldExpansionOutput,
    parse_world_expansion_output,
)
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.modules.ingestion import ModuleChunk


class FakeWorldExpansionDirector:
    def __init__(self) -> None:
        self.calls = 0

    async def handle_world_expansion(self, **values):
        self.calls += 1
        output = WorldExpansionOutput.model_validate(
            {
                "public_narration": "镇中心没有现代警察局招牌，但可以找到负责治安的办公室。",
                "kp_notes": "候选尚未成为事实；批准后仅叙述当前接触。",
                "candidate": {
                    "expansion_kind": "environment",
                    "subject": "小镇警务设施",
                    "proposal": "设置一间由治安官与兼职书记员使用的小型办公室。",
                    "rationale": "符合 1920 年代小型聚落的规模和当前调查需求。",
                    "confidence": "medium",
                    "assumptions": ["小镇位于采用治安官制度的辖区"],
                    "conflicts": [],
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

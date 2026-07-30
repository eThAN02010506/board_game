import asyncio
from pathlib import Path

import pytest

from ai_kp.application.errors import ConflictError, InvalidInputError
from ai_kp.application.npc_reappearance_service import NpcReappearanceService
from ai_kp.application.turn_service import TurnService, WorldExpansionCommand
from ai_kp.application.world_expansion_materialization_service import (
    EncounterFact,
    EncounterMapPlacement,
    EncounterNpc,
    MaterializeWorldExpansionCommand,
    WorldExpansionMaterializationService,
)
from ai_kp.platform.scenes.map_generation import generate_map
from tests.test_world_expansion_proposals import (
    FakeWorldExpansionDirector,
    setup_world_gap,
)


def approved_world_expansion(tmp_path: Path) -> tuple:
    connection, repo, campaign, identity, run = setup_world_gap(tmp_path)
    proposal = asyncio.run(
        TurnService(repo).create_world_expansion_proposal(
            WorldExpansionCommand(
                run_id=run["id"],
                player_intent="我去寻找镇上的警察局",
            ),
            identity,
            FakeWorldExpansionDirector(),
            source_model="fake-local-model",
        )
    )
    approved = TurnService(repo).approve(
        proposal["id"],
        campaign["id"],
        identity,
    )
    return connection, repo, campaign, identity, approved


def materialization_command(
    map_id: str,
    *,
    idempotency_key: str = "contact:test-police-station",
    location_name: str = "镇中心",
) -> MaterializeWorldExpansionCommand:
    return MaterializeWorldExpansionCommand(
        idempotency_key=idempotency_key,
        summary="调查员抵达治安官办公室，并与值班治安官交谈。",
        happened_at="1928-10-03 22:15",
        facts=(
            EncounterFact(
                fact_type="canonical_fact",
                subject="小镇警务设施",
                predicate="实际存在",
                object_text="镇中心有一间由治安官使用的小型办公室。",
            ),
        ),
        npc=EncounterNpc(
            name="艾萨克·霍尔",
            home_location="小镇",
            profession="治安官",
            public_notes="负责本镇日常治安。",
        ),
        map_placement=EncounterMapPlacement(
            map_id=map_id,
            location_name=location_name,
        ),
    )


def approve_stable_investigator(
    repo,
    campaign_id: str,
    *,
    name: str,
    owner_profile_id: str | None = None,
) -> dict:
    if owner_profile_id is None:
        owner_profile_id = repo.create_player_profile(f"{name}的玩家")["profile"]["id"]
    investigator = repo.create_investigator(
        owner_profile_id,
        {
            "schema_version": "coc7-investigator-v1",
            "ruleset_id": "coc7-keeper-cn-2002c",
            "identity": {"name": name},
            "characteristics": {},
            "skills": [],
        },
        source_type="manual",
    )
    pc = repo.create_pc(campaign_id, name)
    repo.connection.execute(
        """
        INSERT INTO campaign_investigators
          (campaign_id, investigator_id, owner_profile_id,
           submitted_revision_id, approved_revision_id, legacy_pc_id, status)
        VALUES (?, ?, ?, ?, ?, ?, 'approved')
        """,
        (
            campaign_id,
            investigator["id"],
            owner_profile_id,
            investigator["current_revision_id"],
            investigator["current_revision_id"],
            pc["id"],
        ),
    )
    return investigator


def approve_existing_investigator(repo, campaign_id: str, investigator: dict) -> None:
    pc = repo.create_pc(campaign_id, investigator["name"])
    repo.connection.execute(
        """
        INSERT INTO campaign_investigators
          (campaign_id, investigator_id, owner_profile_id,
           submitted_revision_id, approved_revision_id, legacy_pc_id, status)
        VALUES (?, ?, ?, ?, ?, ?, 'approved')
        """,
        (
            campaign_id,
            investigator["id"],
            investigator["owner_profile_id"],
            investigator["current_revision_id"],
            investigator["current_revision_id"],
            pc["id"],
        ),
    )


def record_prior_encounter(
    repo,
    *,
    campaign_id: str,
    investigator_id: str,
    npc_id: str,
) -> dict:
    event = repo.append_event(
        campaign_id=campaign_id,
        actor_type="kp",
        visibility="table",
        event_type="npc.encountered",
        happened_at="1927-06-11 16:00",
        summary="调查员与报社线人一起查阅了旧档案。",
    )
    return repo.record_investigator_npc_encounter(
        investigator_id=investigator_id,
        npc_id=npc_id,
        campaign_id=campaign_id,
        source_event_id=event["id"],
        materialization_id=None,
        interaction_summary="一起查阅报社旧档案并交换联系方式。",
        happened_at="1927-06-11 16:00",
    )


def test_confirmed_contact_atomically_writes_fact_npc_and_map_token(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, proposal = approved_world_expansion(
        tmp_path
    )
    try:
        saved_map = repo.create_map(
            campaign["id"],
            generate_map(
                title="小镇",
                prompt="小镇调查地图",
                location_names=["镇中心", "钟楼"],
            ),
            created_by="human_kp",
        )
        service = WorldExpansionMaterializationService(repo)
        command = materialization_command(saved_map["id"])

        materialized = service.materialize(proposal["id"], identity, command)
        receipt = materialized["world_expansion_materialization"]
        assert receipt["materialization_id"].startswith("worldmat_")
        assert len(receipt["fact_event_ids"]) == 1
        assert receipt["npc_id"].startswith("npc_")
        assert receipt["map_token_id"].startswith("token_")

        fact = repo.list_fact_heads(campaign["id"])[0]
        assert fact.fact.subject == "小镇警务设施"
        assert fact.evidence_event_ids == (receipt["encounter_event_id"],)
        assert fact.source_reference["proposal_id"] == proposal["id"]
        token = repo.get_map_token(receipt["map_token_id"])
        assert token["actor_type"] == "npc"
        assert token["actor_id"] == receipt["npc_id"]
        assert token["location_name"] == "镇中心"
        campaign_npc = connection.execute(
            """
            SELECT * FROM campaign_npcs
            WHERE campaign_id = ? AND npc_id = ?
            """,
            (campaign["id"], receipt["npc_id"]),
        ).fetchone()
        assert campaign_npc["first_seen_time"] == "1928-10-03 22:15"

        event_count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE campaign_id = ?",
            (campaign["id"],),
        ).fetchone()[0]
        repeated = service.materialize(proposal["id"], identity, command)
        assert (
            repeated["world_expansion_materialization"]["materialization_id"]
            == receipt["materialization_id"]
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE campaign_id = ?",
                (campaign["id"],),
            ).fetchone()[0]
            == event_count
        )

        with pytest.raises(ConflictError, match="already materialized"):
            service.materialize(
                proposal["id"],
                identity,
                materialization_command(
                    saved_map["id"],
                    idempotency_key="contact:different-retry-key",
                ),
            )
    finally:
        connection.close()


def test_invalid_map_projection_rolls_back_every_contact_write(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, proposal = approved_world_expansion(
        tmp_path
    )
    try:
        saved_map = repo.create_map(
            campaign["id"],
            generate_map(
                title="小镇",
                prompt="小镇调查地图",
                location_names=["钟楼"],
            ),
            created_by="human_kp",
        )
        with pytest.raises(
            InvalidInputError,
            match="existing reviewed map location",
        ):
            WorldExpansionMaterializationService(repo).materialize(
                proposal["id"],
                identity,
                materialization_command(saved_map["id"]),
            )

        assert repo.get_world_expansion_materialization(proposal["id"]) is None
        assert connection.execute("SELECT COUNT(*) FROM npcs").fetchone()[0] == 0
        assert (
            connection.execute(
                """
                SELECT COUNT(*) FROM events
                WHERE event_type = 'world_expansion.encountered'
                """
            ).fetchone()[0]
            == 0
        )
        assert repo.list_fact_heads(campaign["id"]) == []
    finally:
        connection.close()


def test_existing_npc_from_another_campaign_cannot_cross_the_contact_boundary(
    tmp_path: Path,
) -> None:
    connection, repo, _campaign, identity, proposal = approved_world_expansion(
        tmp_path
    )
    try:
        other_campaign = repo.create_campaign("另一张互相隔离的桌")
        private_npc = repo.create_npc(
            name="另一团的秘密人物",
            secret_notes="不得被当前 KP 猜 ID 引用。",
        )
        repo.link_npc_to_campaign(other_campaign["id"], private_npc["id"])
        command = MaterializeWorldExpansionCommand(
            idempotency_key="contact:foreign-npc",
            summary="玩家实际接触了某人。",
            happened_at="1928-10-03 22:20",
            facts=(
                EncounterFact(
                    fact_type="canonical_fact",
                    subject="陌生人",
                    predicate="出现于",
                    object_text="镇中心",
                ),
            ),
            npc=EncounterNpc(npc_id=private_npc["id"]),
        )

        with pytest.raises(
            InvalidInputError,
            match="qualifying prior encounter",
        ):
            WorldExpansionMaterializationService(repo).materialize(
                proposal["id"],
                identity,
                command,
            )

        assert repo.get_world_expansion_materialization(proposal["id"]) is None
        assert repo.list_fact_heads(identity.campaign_id) == []
    finally:
        connection.close()


def test_stable_investigator_history_authorizes_npc_reappearance(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, proposal = approved_world_expansion(
        tmp_path
    )
    try:
        prior_campaign = repo.create_campaign("上一部模组")
        investigator = approve_stable_investigator(
            repo,
            prior_campaign["id"],
            name="林若川",
        )
        approve_existing_investigator(repo, campaign["id"], investigator)
        npc = repo.create_npc(
            "周怀民",
            home_location="雾港旧码头",
            profession="报社线人",
            public_notes="曾帮助林若川调查旧档案。",
            secret_notes="另一团的秘密不会出现在候选响应。",
        )
        repo.link_npc_to_campaign(prior_campaign["id"], npc["id"])
        record_prior_encounter(
            repo,
            campaign_id=prior_campaign["id"],
            investigator_id=investigator["id"],
            npc_id=npc["id"],
        )
        candidates = NpcReappearanceService(repo).list_candidates(campaign["id"])
        assert len(candidates) == 1
        assert candidates[0]["npc_id"] == npc["id"]
        assert candidates[0]["qualifying_investigators"] == [
            {
                "investigator_id": investigator["id"],
                "investigator_name": "林若川",
                "interaction_summary": "一起查阅报社旧档案并交换联系方式。",
                "happened_at": "1927-06-11 16:00",
            }
        ]
        assert candidates[0]["appearance_gate"]["decision"] == "needs_review"
        assert candidates[0]["appearance_gate"]["remaining_campaign_budget"] == 1
        assert "secret" not in candidates[0]

        saved_map = repo.create_map(
            campaign["id"],
            generate_map(
                title="小镇",
                prompt="小镇调查地图",
                location_names=["镇中心"],
            ),
            created_by="human_kp",
        )
        command = MaterializeWorldExpansionCommand(
            idempotency_key="contact:known-informant",
            summary="林若川在镇中心再次遇见周怀民。",
            happened_at="1928-10-03 22:30",
            facts=(
                EncounterFact(
                    fact_type="canonical_fact",
                    subject="周怀民",
                    predicate="当前位于",
                    object_text="镇中心",
                ),
            ),
            npc=EncounterNpc(npc_id=npc["id"]),
            map_placement=EncounterMapPlacement(
                map_id=saved_map["id"],
                location_name="镇中心",
            ),
            participant_investigator_ids=(investigator["id"],),
            interaction_summary="重逢后一起讨论了镇上的失踪案。",
        )
        result = WorldExpansionMaterializationService(repo).materialize(
            proposal["id"],
            identity,
            command,
        )
        receipt = result["world_expansion_materialization"]
        encounter_rows = connection.execute(
            """
            SELECT * FROM investigator_npc_encounters
            WHERE campaign_id = ? AND npc_id = ?
            """,
            (campaign["id"], npc["id"]),
        ).fetchall()
        assert len(encounter_rows) == 1
        assert encounter_rows[0]["interaction_summary"] == (
            "重逢后一起讨论了镇上的失踪案。"
        )
        assert repo.npc_is_linked_to_campaign(campaign["id"], npc["id"])
        assert repo.get_map_token(receipt["map_token_id"])["actor_id"] == npc["id"]
        assert receipt["npc_reappearance_id"].startswith("npcapp_")
        assert repo.count_npc_reappearances(campaign["id"]) == 1

        retried = WorldExpansionMaterializationService(repo).materialize(
            proposal["id"],
            identity,
            command,
        )
        assert (
            retried["world_expansion_materialization"]["materialization_id"]
            == receipt["materialization_id"]
        )
        assert (
            connection.execute(
                """
                SELECT COUNT(*) FROM investigator_npc_encounters
                WHERE campaign_id = ? AND npc_id = ?
                """,
                (campaign["id"], npc["id"]),
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_impossible_npc_year_is_hidden_and_rejected_inside_write_transaction(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, proposal = approved_world_expansion(tmp_path)
    try:
        prior_campaign = repo.create_campaign("旧团", current_time="1970-01-01")
        investigator = approve_stable_investigator(
            repo, prior_campaign["id"], name="林若川"
        )
        approve_existing_investigator(repo, campaign["id"], investigator)
        npc = repo.create_npc("尚未出生的人")
        repo.link_npc_to_campaign(prior_campaign["id"], npc["id"])
        record_prior_encounter(
            repo,
            campaign_id=prior_campaign["id"],
            investigator_id=investigator["id"],
            npc_id=npc["id"],
        )
        repo.save_npc_availability_profile(
            npc["id"],
            lifecycle_state="active",
            born_year=1950,
            died_year=None,
            active_from_year=1970,
            active_until_year=1990,
            location_tags=[],
            profession_tags=[],
            kp_notes="年代测试",
        )

        assert NpcReappearanceService(repo).list_candidates(campaign["id"]) == []
        command = MaterializeWorldExpansionCommand(
            idempotency_key="contact:impossible-year",
            summary="不可能的相遇。",
            happened_at="1928-10-03",
            facts=(
                EncounterFact(
                    fact_type="canonical_fact",
                    subject="尚未出生的人",
                    predicate="出现",
                    object_text="小镇",
                ),
            ),
            npc=EncounterNpc(npc_id=npc["id"]),
            participant_investigator_ids=(investigator["id"],),
        )
        with pytest.raises(ConflictError, match="appearance is impossible"):
            WorldExpansionMaterializationService(repo).materialize(
                proposal["id"], identity, command
            )
        assert not repo.npc_is_linked_to_campaign(campaign["id"], npc["id"])
        assert repo.get_world_expansion_materialization(proposal["id"]) is None
        assert repo.count_npc_reappearances(campaign["id"]) == 0
    finally:
        connection.close()


def test_strict_location_and_zero_budget_fail_closed(tmp_path: Path) -> None:
    connection, repo, campaign, identity, proposal = approved_world_expansion(tmp_path)
    try:
        prior_campaign = repo.create_campaign("旧团")
        investigator = approve_stable_investigator(
            repo, prior_campaign["id"], name="林若川"
        )
        approve_existing_investigator(repo, campaign["id"], investigator)
        npc = repo.create_npc("码头线人")
        repo.link_npc_to_campaign(prior_campaign["id"], npc["id"])
        record_prior_encounter(
            repo,
            campaign_id=prior_campaign["id"],
            investigator_id=investigator["id"],
            npc_id=npc["id"],
        )
        repo.save_npc_availability_profile(
            npc["id"],
            lifecycle_state="active",
            born_year=1880,
            died_year=1940,
            active_from_year=1910,
            active_until_year=1935,
            location_tags=["旧码头"],
            profession_tags=["线人"],
            kp_notes="",
        )
        repo.save_campaign_npc_reappearance_policy(
            campaign["id"],
            max_returning_npcs=1,
            require_location_match=True,
            require_profession_match=True,
        )
        discovery = NpcReappearanceService(repo).list_candidates(campaign["id"])
        assert discovery[0]["appearance_gate"]["decision"] == "needs_review"
        assert (
            NpcReappearanceService(repo).list_candidates(
                campaign["id"],
                context_location="市政厅",
                profession_hint="警察",
            )
            == []
        )

        command = MaterializeWorldExpansionCommand(
            idempotency_key="contact:strict-location",
            summary="地点不合理的相遇。",
            happened_at="1928-10-03",
            facts=(
                EncounterFact(
                    fact_type="canonical_fact",
                    subject="码头线人",
                    predicate="出现",
                    object_text="未知地点",
                ),
            ),
            npc=EncounterNpc(npc_id=npc["id"]),
            participant_investigator_ids=(investigator["id"],),
            profession_context="线人",
        )
        with pytest.raises(ConflictError, match="地点上下文缺失"):
            WorldExpansionMaterializationService(repo).materialize(
                proposal["id"], identity, command
            )

        repo.save_campaign_npc_reappearance_policy(
            campaign["id"],
            max_returning_npcs=0,
            require_location_match=False,
            require_profession_match=False,
        )
        with pytest.raises(ConflictError, match="budget is exhausted"):
            WorldExpansionMaterializationService(repo).materialize(
                proposal["id"], identity, command
            )
    finally:
        connection.close()


def test_same_player_different_investigator_does_not_authorize_reappearance(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, proposal = approved_world_expansion(
        tmp_path
    )
    try:
        prior_campaign = repo.create_campaign("旧团")
        first = approve_stable_investigator(
            repo,
            prior_campaign["id"],
            name="林若川",
        )
        second = approve_stable_investigator(
            repo,
            campaign["id"],
            name="顾清",
            owner_profile_id=first["owner_profile_id"],
        )
        npc = repo.create_npc("周怀民")
        repo.link_npc_to_campaign(prior_campaign["id"], npc["id"])
        record_prior_encounter(
            repo,
            campaign_id=prior_campaign["id"],
            investigator_id=first["id"],
            npc_id=npc["id"],
        )
        command = MaterializeWorldExpansionCommand(
            idempotency_key="contact:wrong-investigator",
            summary="顾清声称认识周怀民。",
            happened_at=None,
            facts=(
                EncounterFact(
                    fact_type="canonical_fact",
                    subject="周怀民",
                    predicate="认识",
                    object_text="顾清",
                ),
            ),
            npc=EncounterNpc(npc_id=npc["id"]),
            participant_investigator_ids=(second["id"],),
        )

        with pytest.raises(InvalidInputError, match="qualifying prior encounter"):
            WorldExpansionMaterializationService(repo).materialize(
                proposal["id"],
                identity,
                command,
            )

        assert not repo.npc_is_linked_to_campaign(campaign["id"], npc["id"])
        assert repo.get_world_expansion_materialization(proposal["id"]) is None
    finally:
        connection.close()


def test_invalid_map_rolls_back_reappearance_link_and_new_history(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, proposal = approved_world_expansion(
        tmp_path
    )
    try:
        prior_campaign = repo.create_campaign("旧团")
        investigator = approve_stable_investigator(
            repo,
            prior_campaign["id"],
            name="林若川",
        )
        approve_existing_investigator(repo, campaign["id"], investigator)
        npc = repo.create_npc("周怀民")
        repo.link_npc_to_campaign(prior_campaign["id"], npc["id"])
        record_prior_encounter(
            repo,
            campaign_id=prior_campaign["id"],
            investigator_id=investigator["id"],
            npc_id=npc["id"],
        )
        saved_map = repo.create_map(
            campaign["id"],
            generate_map(
                title="小镇",
                prompt="小镇调查地图",
                location_names=["钟楼"],
            ),
            created_by="human_kp",
        )
        event_count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE campaign_id = ?",
            (campaign["id"],),
        ).fetchone()[0]
        command = MaterializeWorldExpansionCommand(
            idempotency_key="contact:rollback-known-npc",
            summary="林若川再次遇见周怀民。",
            happened_at="1928-10-03 22:30",
            facts=(
                EncounterFact(
                    fact_type="canonical_fact",
                    subject="周怀民",
                    predicate="当前位于",
                    object_text="不存在的镇中心",
                ),
            ),
            npc=EncounterNpc(npc_id=npc["id"]),
            map_placement=EncounterMapPlacement(
                map_id=saved_map["id"],
                location_name="镇中心",
            ),
            participant_investigator_ids=(investigator["id"],),
        )

        with pytest.raises(
            InvalidInputError,
            match="existing reviewed map location",
        ):
            WorldExpansionMaterializationService(repo).materialize(
                proposal["id"],
                identity,
                command,
            )

        assert not repo.npc_is_linked_to_campaign(campaign["id"], npc["id"])
        assert repo.get_world_expansion_materialization(proposal["id"]) is None
        assert repo.list_fact_heads(campaign["id"]) == []
        assert connection.execute(
            """
            SELECT COUNT(*) FROM investigator_npc_encounters
            WHERE campaign_id = ?
            """,
            (campaign["id"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE campaign_id = ?",
            (campaign["id"],),
        ).fetchone()[0] == event_count
        assert connection.execute(
            "SELECT COUNT(*) FROM map_tokens WHERE map_id = ?",
            (saved_map["id"],),
        ).fetchone()[0] == 0
    finally:
        connection.close()

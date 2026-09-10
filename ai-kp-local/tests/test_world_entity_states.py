import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.application.errors import ConflictError, InvalidInputError
from ai_kp.application.turn_service import TurnService
from ai_kp.application.world_entity_state_candidates import (
    validate_world_entity_state_candidates,
)
from ai_kp.application.world_entity_state_service import (
    SetWorldEntityStateCommand,
    WorldEntityStateService,
)
from ai_kp.application.world_expansion_materialization_service import (
    EncounterEntityRealization,
    EncounterFact,
    MaterializeWorldExpansionCommand,
    WorldExpansionMaterializationService,
)
from ai_kp.application.world_service import WorldService
from ai_kp.bootstrap.settings import Settings
from ai_kp.director.context_builder import ContextBuilder
from ai_kp.director.turn_output import KpTurnOutput
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from tests.test_world_expansion_materialization import approved_world_expansion


def materialized_typed_entities(tmp_path: Path):
    connection, repo, campaign, identity, proposal = approved_world_expansion(
        tmp_path, typed_entities=True
    )
    WorldExpansionMaterializationService(repo).materialize(
        proposal["id"],
        identity,
        MaterializeWorldExpansionCommand(
            idempotency_key="contact:stateful-entities",
            summary="调查员进入警长办公室并见到值班巡警。",
            happened_at="1928-10-03 22:15",
            facts=(
                EncounterFact(
                    fact_type="canonical_fact",
                    subject="警长办公室",
                    predicate="实际存在",
                    object_text="镇中心存在一间警长办公室。",
                ),
            ),
            entities=(
                EncounterEntityRealization(
                    local_ref="local_agency",
                    name="黑溪镇警长办公室",
                    visibility="kp",
                ),
                EncounterEntityRealization(
                    local_ref="duty_officer",
                    name="约瑟夫·贝尔",
                    description="今晚的值班巡警。",
                ),
            ),
        ),
    )
    repo.commit()
    entities = repo.list_campaign_world_entities(campaign["id"])
    by_archetype = {item["archetype_id"]: item for item in entities}
    return connection, repo, campaign, identity, by_archetype


def test_human_and_ai_kp_share_versioned_world_entity_state_kernel(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, entities = materialized_typed_entities(
        tmp_path
    )
    try:
        officer = entities["public_safety_officer"]
        service = WorldEntityStateService(repo)
        human_command = SetWorldEntityStateCommand(
            expected_version=0,
            dimension="on_duty",
            value=True,
            visibility="table",
            idempotency_key="world-state:officer:on-duty:1",
            note="调查员抵达时亲眼见到警员值班。",
            happened_at="1928-10-03 22:15",
        )
        human = service.set_state(
            campaign["id"], officer["id"], identity, human_command
        )
        assert human["entity"]["state_version"] == 1
        assert human["change"]["source_kind"] == "human_kp"
        assert human["change"]["from_value"] is None
        assert human["change"]["to_value"] is True

        replay = service.set_state(
            campaign["id"], officer["id"], identity, human_command
        )
        assert replay["change"]["id"] == human["change"]["id"]

        ai = service.set_state(
            campaign["id"],
            officer["id"],
            identity,
            SetWorldEntityStateCommand(
                expected_version=1,
                dimension="cooperation",
                value="guarded",
                visibility="secret",
                idempotency_key="world-state:officer:cooperation:1",
                note="AI KP 根据已结算交互提交，不直接修改模型记忆。",
                source_kind="ai_kp",
            ),
        )
        assert ai["entity"]["state_version"] == 2
        assert ai["change"]["source_kind"] == "ai_kp"

        kp_graph = WorldService(repo).list_world_entities(campaign["id"], view="kp")
        player_graph = WorldService(repo).list_world_entities(
            campaign["id"], view="player"
        )
        kp_officer = next(item for item in kp_graph["entities"] if item["id"] == officer["id"])
        player_officer = next(
            item for item in player_graph["entities"] if item["id"] == officer["id"]
        )
        assert {item["dimension"] for item in kp_officer["states"]} == {
            "on_duty",
            "cooperation",
        }
        assert [item["dimension"] for item in player_officer["states"]] == ["on_duty"]
        assert len(kp_graph["state_changes"]) == 2
        assert len(player_graph["state_changes"]) == 1
        assert "note" not in player_graph["state_changes"][0]
        assert "source_kind" not in player_graph["state_changes"][0]

        context = ContextBuilder(connection).build(
            campaign_id=campaign["id"],
            player_action="约瑟夫·贝尔现在是否值班，他愿意合作吗？",
        )
        source = next(
            item
            for item in context.included_sources
            if item["kind"] == "campaign_world_entity" and item["id"] == officer["id"]
        )
        decoded = json.loads(source["content"])
        assert decoded["state_version"] == 2
        assert {"on_duty", "cooperation"} <= set(decoded["state_dimensions"])
        assert {item["dimension"] for item in decoded["states"]} == {
            "on_duty",
            "cooperation",
        }
        revealed = service.set_state(
            campaign["id"], officer["id"], identity,
            SetWorldEntityStateCommand(
                expected_version=2, dimension="cooperation", value="helpful",
                visibility="table", idempotency_key="state-public-new-value",
            ),
        )
        assert revealed["change"]["visibility"] == "secret"
        public = WorldService(repo).list_world_entities(campaign["id"], view="player")
        assert "guarded" not in json.dumps(public)
        assert "helpful" in json.dumps(public)
    finally:
        connection.close()


def test_ai_proposal_state_candidate_uses_context_contract_and_shared_commit_kernel(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, entities = materialized_typed_entities(
        tmp_path
    )
    try:
        officer = entities["public_safety_officer"]
        context = ContextBuilder(connection).build(
            campaign_id=campaign["id"],
            player_action="约瑟夫·贝尔接受调查员请求并开始合作。",
        )
        payload = {
            "public_narration": "贝尔警员接受了请求，开始翻找当晚的值班记录。",
            "kp_notes": "合作变化由已成立的对话结果支持。",
            "action_ruling": {
                "goal": "请贝尔警员协助查阅记录",
                "method": "直接提出明确请求",
                "target": "约瑟夫·贝尔",
                "feasibility": "possible",
                "resolution": "automatic",
                "reason": "当前已确认状态允许直接合作。",
                "maximum_effect": "贝尔开始提供职权范围内的协助。",
                "alternative": "",
            },
            "proposed_checks": [],
            "proposed_events": [],
            "proposed_memories": [],
            "proposed_npc_updates": [],
            "proposed_map_moves": [],
            "proposed_facts": [],
            "proposed_world_entity_states": [
                {
                    "entity_id": officer["id"],
                    "expected_version": 0,
                    "dimension": "cooperation",
                    "value": "helpful",
                    "visibility": "table",
                    "note": "贝尔已明确开始协助。",
                }
            ],
        }
        output = KpTurnOutput.model_validate(payload)
        validate_world_entity_state_candidates(output, context.included_sources)

        proposal = repo.create_turn_proposal(
            campaign_id=campaign["id"],
            player_action="请贝尔协助查阅记录",
            public_narration=output.public_narration,
            kp_notes=output.kp_notes,
            proposed_world_entity_states=output.proposed_world_entity_states,
            source_model="local-test-model",
        )
        repo.add_proposal_action(
            proposal["id"],
            "action_ruling",
            actor="system",
            payload=output.action_ruling.model_dump(mode="json"),
        )
        approved = TurnService(repo).approve(
            proposal["id"], campaign["id"], identity, note="approve AI state"
        )

        assert approved["status"] == "approved"
        change = repo.list_campaign_world_entity_state_changes(campaign["id"])[0]
        assert change["entity_id"] == officer["id"]
        assert change["dimension"] == "cooperation"
        assert change["to_value"] == "helpful"
        assert change["source_kind"] == "ai_kp"
        applied = next(
            action
            for action in approved["actions"]
            if action["action_type"] == "proposed_world_entity_states_applied"
        )
        assert applied["payload"]["changes"][0]["change_id"] == change["id"]

        stale = output.model_copy(
            update={
                "proposed_world_entity_states": [
                    output.proposed_world_entity_states[0].model_copy(
                        update={"expected_version": 1}
                    )
                ]
            }
        )
        bound = validate_world_entity_state_candidates(stale, context.included_sources)
        assert bound.proposed_world_entity_states[0].expected_version == 0
        assert stale.proposed_world_entity_states[0].expected_version == 1
        # Binding must use the old prompt snapshot, never the live database's v1.
        with pytest.raises(ConflictError, match="refresh"):
            WorldEntityStateService(repo).set_state(
                campaign["id"], officer["id"], identity,
                SetWorldEntityStateCommand(
                    expected_version=bound.proposed_world_entity_states[0].expected_version,
                    dimension="cooperation", value="guarded", visibility="table",
                    idempotency_key="stale-bound-candidate",
                ),
            )
        untrusted = repo.create_turn_proposal(
            campaign_id=campaign["id"], player_action="玩家猜测警员会合作",
            public_narration="等待确认。",
            proposed_world_entity_states=output.proposed_world_entity_states,
        )
        cleared = repo.clear_draft_world_effects(untrusted["id"])
        assert cleared["proposed_world_entity_states"] == []
        conflicting = repo.create_turn_proposal(
            campaign_id=campaign["id"], player_action="过期提案",
            public_narration="这段叙事必须随冲突回滚。",
            proposed_world_entity_states=[{
                "entity_id": officer["id"], "expected_version": 0,
                "dimension": "cooperation", "value": "guarded", "visibility": "table",
            }],
        )
        repo.commit()
        with pytest.raises(ConflictError, match="refresh"):
            TurnService(repo).approve(conflicting["id"], campaign["id"], identity)
        repo.commit()  # Simulate a worker catching the error and committing its job.
        assert repo.get_turn_proposal(conflicting["id"])["status"] == "draft"
        assert connection.execute(
            "SELECT count(*) FROM events WHERE summary = ?",
            ("这段叙事必须随冲突回滚。",),
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_world_entity_state_rejects_unknown_dimensions_stale_writes_and_broad_visibility(
    tmp_path: Path,
) -> None:
    connection, repo, campaign, identity, entities = materialized_typed_entities(
        tmp_path
    )
    try:
        officer = entities["public_safety_officer"]
        agency = entities["public_safety_agency"]
        service = WorldEntityStateService(repo)
        with pytest.raises(InvalidInputError, match="does not declare"):
            service.set_state(
                campaign["id"],
                officer["id"],
                identity,
                SetWorldEntityStateCommand(
                    expected_version=0,
                    dimension="hit_points",
                    value=10,
                    visibility="table",
                    idempotency_key="world-state:invalid-dimension",
                ),
            )
        with pytest.raises(InvalidInputError, match="cannot be broader"):
            service.set_state(
                campaign["id"],
                agency["id"],
                identity,
                SetWorldEntityStateCommand(
                    expected_version=0,
                    dimension="staffing",
                    value="reduced",
                    visibility="table",
                    idempotency_key="world-state:secret-agency",
                ),
            )
        first = service.set_state(
            campaign["id"],
            officer["id"],
            identity,
            SetWorldEntityStateCommand(
                expected_version=0,
                dimension="on_duty",
                value=True,
                visibility="table",
                idempotency_key="world-state:optimistic:one",
            ),
        )
        with pytest.raises(ConflictError, match="refresh"):
            service.set_state(
                campaign["id"],
                officer["id"],
                identity,
                SetWorldEntityStateCommand(
                    expected_version=0,
                    dimension="cooperation",
                    value="helpful",
                    visibility="table",
                    idempotency_key="world-state:optimistic:stale",
                ),
            )
        with pytest.raises(ConflictError, match="another command"):
            service.set_state(
                campaign["id"],
                officer["id"],
                identity,
                SetWorldEntityStateCommand(
                    expected_version=1,
                    dimension="on_duty",
                    value=False,
                    visibility="table",
                    idempotency_key="world-state:optimistic:one",
                ),
            )
        assert first["entity"]["state_version"] == 1
        assert repo.get_campaign_world_entity(officer["id"])["state_version"] == 1
        assert len(repo.list_campaign_world_entity_state_changes(campaign["id"])) == 1
    finally:
        connection.close()


def test_world_entity_state_api_is_kp_only_idempotent_and_player_safe(tmp_path: Path) -> None:
    settings = Settings(
        db_path=tmp_path / "world-state-api.sqlite3",
        admin_token="world-state-admin",
        local_admin_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        admin = {"X-AI-KP-Admin-Token": "world-state-admin"}
        campaign = client.post(
            "/campaigns", headers=admin, json={"title": "实体状态 API"}
        ).json()
        session = client.post(
            f"/campaigns/{campaign['id']}/sessions",
            headers=admin,
            json={"kp_display_name": "KP"},
        ).json()
        kp_headers = {"Authorization": f"Bearer {session['access_token']}"}
        player = client.post(
            "/sessions/join",
            json={"join_code": session["join_code"], "display_name": "玩家"},
        ).json()
        player_headers = {"Authorization": f"Bearer {player['access_token']}"}

        connection = connect(settings.db_path)
        try:
            init_db(connection)
            repo = Repository(connection)
            source_event = repo.append_event(
                campaign_id=campaign["id"],
                actor_type="system",
                event_type="world_expansion.encountered",
                summary="玩家实际见到巡警。",
            )
            entity = repo.create_campaign_world_entity(
                campaign_id=campaign["id"],
                entity_kind="npc",
                archetype_id="public_safety_officer",
                name="值班巡警",
                visibility="table",
                origin_kind="world_expansion",
                origin_ref="api-fixture:officer",
                created_from_event_id=source_event["id"],
                data={"state_dimensions": ["on_duty", "cooperation"]},
            )
            connection.commit()
        finally:
            connection.close()

        payload = {
            "expected_version": 0,
            "dimension": "on_duty",
            "value": True,
            "visibility": "table",
            "idempotency_key": "world-state:api:on-duty:1",
            "note": "KP 私有依据不会投影给玩家",
        }
        denied = client.post(
            f"/campaigns/{campaign['id']}/world-entities/{entity['id']}/states",
            headers=player_headers,
            json=payload,
        )
        assert denied.status_code == 403

        updated = client.post(
            f"/campaigns/{campaign['id']}/world-entities/{entity['id']}/states",
            headers=kp_headers,
            json=payload,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["entity"]["state_version"] == 1
        replay = client.post(
            f"/campaigns/{campaign['id']}/world-entities/{entity['id']}/states",
            headers=kp_headers,
            json=payload,
        )
        assert replay.status_code == 200
        assert replay.json()["change"]["id"] == updated.json()["change"]["id"]

        player_graph = client.get(
            f"/campaigns/{campaign['id']}/world-entities",
            headers=player_headers,
        ).json()
        assert player_graph["entities"][0]["states"][0]["value"] is True
        assert "KP 私有依据" not in json.dumps(player_graph, ensure_ascii=False)
        assert "command_hash" not in player_graph
        assert "idempotency_key" not in player_graph

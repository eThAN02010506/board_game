import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from ai_kp.api.main import create_app
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.core.config import Settings, get_settings
from ai_kp.core.repository import Repository
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.modules.ingestion import ModuleChunk
from tests.scenario_contract_testkit import bind_payload_to_module, source_bound_payload
from tests.support_investigators import (
    coc7_sheet,
    confirm_current_session_zero,
    create_approved_player,
)


def _is_tabletop_frame_request(messages: object) -> bool:
    return any(
        "只负责判断玩家这句话在桌面角色扮演对话中属于什么"
        in getattr(message, "content", "")
        for message in messages
    )


class FakeStructuredLlm:
    async def complete(self, messages, temperature: float = 0.7) -> str:
        if _is_tabletop_frame_request(messages):
            return json.dumps(
                {
                    "kind": "action",
                    "goal": "检查仓库门",
                    "method": "近距离观察",
                    "target_entity_ids": [],
                    "dialogue": "",
                    "steps": [],
                    "time_span": "",
                    "ambiguity": None,
                    "confidence": "high",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "public_narration": "仓库门上有一道新鲜刮痕。",
                "kp_notes": "刮痕来自柜子。",
                "action_ruling": {
                    "goal": "检查仓库门",
                    "method": "近距离观察",
                    "target": "仓库门",
                    "feasibility": "possible",
                    "resolution": "automatic",
                    "reason": "刮痕无需专业能力即可看见。",
                    "maximum_effect": "看到门上的明显刮痕。",
                    "alternative": "",
                },
                "proposed_checks": [],
                "proposed_events": [
                    {
                        "event_type": "clue_seen",
                        "summary": "玩家看到仓库门上的刮痕。",
                        "actor_type": "system",
                        "actor_id": None,
                        "visibility": "table",
                        "happened_at": None,
                        "payload": {},
                    }
                ],
                "proposed_memories": [
                    {
                        "text": "仓库门有新鲜刮痕。",
                        "scope": "clue",
                        "importance": 2,
                        "visibility": "table",
                        "pc_id": None,
                        "npc_id": None,
                        "happened_at": None,
                    }
                ],
                "proposed_npc_updates": [],
                "proposed_map_moves": [],
            },
            ensure_ascii=False,
        )


class FakeJumpLlm:
    async def complete(self, messages, temperature: float = 0.7) -> str:
        prompt = "\n".join(message.content for message in messages)
        if _is_tabletop_frame_request(messages):
            return json.dumps(
                {
                    "kind": "multi_step_action",
                    "goal": "确认列车长身份后从行驶列车跳下",
                    "method": "先确认身份，再执行危险跳跃",
                    "target_entity_ids": [],
                    "dialogue": "",
                    "steps": ["确认列车长是亲属", "从行驶列车跳下"],
                    "time_span": "",
                    "ambiguity": None,
                    "confidence": "high",
                },
                ensure_ascii=False,
            )
        if "智能手机" in prompt:
            candidate_id = "reject-anachronism"
            skill = None
        elif "失散亲属" in prompt:
            candidate_id = "attempt-deception"
            skill = "话术"
        elif "理解风险" in prompt:
            candidate_id = "dangerous-jump"
            skill = "跳跃"
        else:
            candidate_id = "recognize-risk"
            skill = "INT"
        return json.dumps(
            {
                "kind": "operator",
                "candidate_id": candidate_id,
                "requested_skill_key": skill,
                "confidence": "high",
                "clarification": None,
            },
            ensure_ascii=False,
        )


class FakeCheckedPlanLlm:
    async def complete(self, messages, temperature: float = 0.7) -> str:
        if _is_tabletop_frame_request(messages):
            return json.dumps(
                {
                    "kind": "multi_step_action",
                    "goal": "检查门锁的弱点后进入房间",
                    "method": "先检查，再进入",
                    "target_entity_ids": [],
                    "dialogue": "",
                    "steps": ["检查门锁的弱点", "进入房间"],
                    "time_span": "",
                    "ambiguity": None,
                    "confidence": "high",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "kind": "task_method",
                "candidate_id": "inspect-and-enter",
                "requested_skill_key": None,
                "confidence": "high",
                "clarification": None,
            },
            ensure_ascii=False,
        )


def install_risk_contract(db_path: Path, campaign_id: str, kp_member_id: str) -> None:
    connection = connect(db_path)
    try:
        init_db(connection)
        repo = Repository(connection)
        module = repo.create_module(
            campaign_id,
            "Generic risk fixture",
            [
                ModuleChunk(
                    title="Risk",
                    text="Generic risk evidence.",
                    visibility="kp",
                    order_index=0,
                )
            ],
        )
        payload = bind_payload_to_module(repo, module["id"], source_bound_payload({
            "contract_id": "risk-and-deception",
            "source_version": 1,
            "ruleset_id": "coc7",
            "title": "Generic risk and deception",
            "operators": [
                {
                    "operator_id": "recognize-risk",
                    "title": "Recognize a severe physical risk",
                    "intent_hints": ["从行驶列车跳下", "确认跳车风险"],
                    "policy": "required_check",
                    "skill_choices": [
                        {
                            "skill_key": "INT",
                            "reason": "成功只代表意识到风险，不保证安全。",
                            "allow_push": False,
                            "failure_stakes": "未能充分认识危险，但危险行动仍需单独确认。",
                        }
                    ],
                    "success_commands": [
                        {"kind": "set_fact", "path": "risk.considered", "value": True}
                    ],
                    "failure_commands": [
                        {"kind": "set_fact", "path": "risk.considered", "value": False}
                    ],
                    "maximum_effect": "只意识到严重风险，不保证安全。",
                },
                {
                    "operator_id": "dangerous-jump",
                    "title": "Proceed despite the severe risk",
                    "intent_hints": ["理解风险仍坚持跳下"],
                    "policy": "required_check",
                    "preconditions": [
                        {"path": "facts.risk.considered", "operator": "exists"}
                    ],
                    "skill_choices": [
                        {
                            "skill_key": "跳跃",
                            "reason": "检定只决定伤害和落点有多糟。",
                            "allow_push": False,
                            "failure_stakes": "落点失控并造成危重伤害。",
                        }
                    ],
                    "success_commands": [
                        {"kind": "set_fact", "path": "risk.injury", "value": "serious"}
                    ],
                    "failure_commands": [
                        {"kind": "set_fact", "path": "risk.injury", "value": "critical"}
                    ],
                    "maximum_effect": "即使成功也必然受伤，只能减轻伤害；绝不安全落地。",
                },
                {
                    "operator_id": "reject-anachronism",
                    "title": "Reject unavailable technology",
                    "intent_hints": ["1928 年的智能手机 GPS 和微信"],
                    "policy": "clarification",
                    "clarification_prompt": "当前时代没有这种技术；请改用已存在的手段。",
                },
                {
                    "operator_id": "attempt-deception",
                    "title": "Attempt a bounded deception",
                    "intent_hints": ["声称是失散亲属", "骗取钥匙"],
                    "policy": "required_check",
                    "skill_choices": [
                        {
                            "skill_key": "话术",
                            "reason": "说法只能造成暂时相信，不能确认亲属关系为真。",
                            "allow_push": False,
                            "failure_stakes": "对方不接受说法并提高警惕。",
                        },
                        {
                            "skill_key": "说服",
                            "reason": "以可核实的理由请求对方让步。",
                            "allow_push": False,
                            "failure_stakes": "对方拒绝请求并中止当前交涉。",
                        },
                    ],
                    "success_commands": [
                        {"kind": "set_fact", "path": "social.temporary_trust", "value": True}
                    ],
                    "failure_commands": [
                        {"kind": "set_fact", "path": "social.temporary_trust", "value": False}
                    ],
                    "rationale": "这是受限的欺骗尝试，成功也不能确认亲属关系为真。",
                    "maximum_effect": "目标暂时相信或继续交涉；不确认亲属关系，也不保证交出钥匙。",
                },
            ],
            "endings": [{
                "ending_id": "jump-resolved",
                "title": "Dangerous jump resolved",
                "all_conditions": [{
                    "path": "facts.risk.injury",
                    "operator": "exists",
                }],
            }],
        }, source_block_id="risk-contract-source"))
        service = ScenarioContractService(repo)
        _, draft = service.compile_draft(
            module["id"], payload, created_by_member_id=kp_member_id
        )
        assert draft is not None
        published = service.publish(
            draft["id"], expected_row_version=1, published_by_member_id=kp_member_id
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign_id,
            module_id=module["id"],
            current_scene_key=None,
            active_spoiler_tags=[],
            state={},
            started_by_member_id=kp_member_id,
        )
        run = repo.set_module_run_automation_level(
            run["id"],
            expected_version=run["version"],
            level="ai_kp",
            reason="Contract-first API test",
            member_id=kp_member_id,
        )
        service.bind_run(run["id"], published["id"])
        connection.commit()
    finally:
        connection.close()


def install_checked_plan_contract(
    db_path: Path, campaign_id: str, kp_member_id: str
) -> None:
    """Install a two-step method whose first primitive requires a player roll."""

    connection = connect(db_path)
    try:
        init_db(connection)
        repo = Repository(connection)
        module = repo.create_module(
            campaign_id,
            "Checked durable plan fixture",
            [
                ModuleChunk(
                    title="Locked room",
                    text="Careful inspection reveals how to enter the locked room.",
                    visibility="kp",
                    order_index=0,
                )
            ],
        )
        payload = bind_payload_to_module(repo, module["id"], source_bound_payload({
            "contract_id": "checked-durable-plan",
            "source_version": 1,
            "ruleset_id": "coc7",
            "title": "Checked durable plan",
            "operators": [
                {
                    "operator_id": "inspect-lock",
                    "title": "Inspect the lock",
                    "policy": "required_check",
                    "skill_choices": [
                        {
                            "skill_key": "侦查",
                            "reason": "发现锁具上可利用的磨损痕迹。",
                            "allow_push": False,
                            "failure_stakes": "这次检查未能确认锁具弱点。",
                        }
                    ],
                    "success_commands": [
                        {
                            "kind": "set_fact",
                            "path": "lock.weakness_found",
                            "value": True,
                        }
                    ],
                    "failure_commands": [
                        {
                            "kind": "set_fact",
                            "path": "lock.weakness_found",
                            "value": False,
                        }
                    ],
                    "maximum_effect": "找到锁具弱点，但尚未进入房间。",
                },
                {
                    "operator_id": "enter-room",
                    "title": "Use the weakness and enter",
                    "policy": "automatic",
                    "preconditions": [
                        {
                            "path": "facts.lock.weakness_found",
                            "operator": "eq",
                            "value": True,
                        }
                    ],
                    "success_commands": [
                        {
                            "kind": "set_fact",
                            "path": "room.entered",
                            "value": True,
                        }
                    ],
                },
            ],
            "task_methods": [
                {
                    "method_id": "inspect-and-enter",
                    "task_key": "enter-locked-room",
                    "title": "Inspect the lock, then enter",
                    "intent_hints": ["检查门锁后进入房间"],
                    "steps": [
                        {"step_id": "inspect", "operator_id": "inspect-lock"},
                        {
                            "step_id": "enter",
                            "operator_id": "enter-room",
                            "depends_on": ["inspect"],
                        },
                    ],
                }
            ],
            "endings": [
                {
                    "ending_id": "inside-room",
                    "title": "Inside the room",
                    "all_conditions": [
                        {
                            "path": "facts.room.entered",
                            "operator": "eq",
                            "value": True,
                        }
                    ],
                }
            ],
        }, source_block_id="checked-plan-source"))
        service = ScenarioContractService(repo)
        _, draft = service.compile_draft(
            module["id"], payload, created_by_member_id=kp_member_id
        )
        assert draft is not None
        published = service.publish(
            draft["id"], expected_row_version=1, published_by_member_id=kp_member_id
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign_id,
            module_id=module["id"],
            current_scene_key=None,
            active_spoiler_tags=[],
            state={},
            started_by_member_id=kp_member_id,
        )
        run = repo.set_module_run_automation_level(
            run["id"],
            expected_version=run["version"],
            level="ai_kp",
            reason="Checked plan recovery API test",
            member_id=kp_member_id,
        )
        service.bind_run(run["id"], published["id"])
        repo.save_model_configuration(
            provider_type="openai_compatible",
            base_url="http://unused.local/v1",
            api_key=None,
            model="large-plan-model",
            local_model_path=None,
            local_port=8001,
            semantic_profile="large",
        )
        connection.commit()
    finally:
        connection.close()


class KpApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_checked_kernel_plan_recovers_next_player_confirmation_after_restart(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                db_path=Path(tmpdir) / "checked-plan-restart.sqlite3",
                llm_base_url="http://unused.local/v1",
                llm_api_key="test",
                llm_model="large-plan-model",
            )
            app = create_app(settings)
            app.dependency_overrides[get_settings] = lambda: settings
            with patch(
                "ai_kp.api.main.OpenAICompatibleClient",
                return_value=FakeCheckedPlanLlm(),
            ):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://test.local"
                ) as client:
                    campaign = (await client.post(
                        "/campaigns", json={"title": "Recoverable checked plan"}
                    )).json()
                    session = (await client.post(
                        f"/campaigns/{campaign['id']}/sessions",
                        json={"kp_display_name": "Auto KP"},
                    )).json()
                    kp_headers = {
                        "Authorization": f"Bearer {session['access_token']}"
                    }
                    player = await create_approved_player(
                        client,
                        campaign=campaign,
                        session=session,
                        kp_headers=kp_headers,
                        display_name="Plan Player",
                        sheet=coc7_sheet("Inspector", skills={"侦查": 80}),
                    )
                    await confirm_current_session_zero(
                        client,
                        campaign_id=campaign["id"],
                        member_headers=(kp_headers, player["headers"]),
                    )
                    install_checked_plan_contract(
                        settings.db_path, campaign["id"], session["member"]["id"]
                    )

                    submitted = await client.post(
                        f"/campaigns/{campaign['id']}/actions",
                        headers=player["headers"],
                        json={
                            "action_text": "我检查门锁的弱点，然后进入房间。",
                            "client_action_id": "checked-plan-root",
                            "auto_advance": True,
                        },
                    )
                    self.assertEqual(submitted.status_code, 200, submitted.text)
                    first = submitted.json()
                    self.assertEqual(first["status"], "awaiting_confirmation")
                    self.assertEqual(first["adjudication"]["mode"], "skill_check")
                    self.assertEqual(first["adjudication"]["selected_skill"], "侦查")

                    confirmed = await client.post(
                        f"/player-actions/{first['player_action']['id']}"
                        "/adjudication/confirm",
                        headers=player["headers"],
                        json={
                            "expected_version": first["adjudication"]["version"],
                            "selected_skill": "侦查",
                        },
                    )
                    self.assertEqual(confirmed.status_code, 200, confirmed.text)
                    roll = confirmed.json()
                    self.assertEqual(roll["status"], "awaiting_roll")
                    self.assertEqual(roll["checks"][0]["target"], 80)

                    resolved = await client.post(
                        f"/checks/{roll['checks'][0]['id']}/resolve",
                        headers=player["headers"],
                        json={
                            "input_method": "physical",
                            "ones_digit": 1,
                            "tens_digits": [2],
                            "auto_advance": True,
                        },
                    )
                    self.assertEqual(resolved.status_code, 200, resolved.text)
                    advanced = resolved.json()
                    self.assertEqual(advanced["status"], "awaiting_confirmation")
                    self.assertEqual(
                        advanced["adjudication"]["mode"], "direct_resolution"
                    )
                    next_action_id = advanced["adjudication"]["action_id"]
                    self.assertNotEqual(next_action_id, first["player_action"]["id"])

            # Recreate the complete ASGI application over the same SQLite file.
            # This is the same discovery call the player UI performs on reload.
            restarted_app = create_app(settings)
            restarted_app.dependency_overrides[get_settings] = lambda: settings
            restarted_transport = httpx.ASGITransport(app=restarted_app)
            async with httpx.AsyncClient(
                transport=restarted_transport, base_url="http://test.local"
            ) as restarted_client:
                pending_response = await restarted_client.get(
                    f"/campaigns/{campaign['id']}/action-adjudications/pending",
                    headers=player["headers"],
                )
                self.assertEqual(
                    pending_response.status_code, 200, pending_response.text
                )
                pending = pending_response.json()
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0]["id"], advanced["adjudication"]["id"])
                self.assertEqual(pending[0]["action_id"], next_action_id)

                completed_response = await restarted_client.post(
                    f"/player-actions/{next_action_id}/adjudication/confirm",
                    headers=player["headers"],
                    json={
                        "expected_version": pending[0]["version"],
                        "selected_skill": None,
                    },
                )
                self.assertEqual(
                    completed_response.status_code, 200, completed_response.text
                )
                completed = completed_response.json()
                self.assertEqual(completed["status"], "completed")
                self.assertEqual(completed["player_action"]["status"], "resolved")
                self.assertEqual(
                    (await restarted_client.get(
                        f"/campaigns/{campaign['id']}/action-adjudications/pending",
                        headers=player["headers"],
                    )).json(),
                    [],
                )

            connection = connect(settings.db_path)
            try:
                init_db(connection)
                repo = Repository(connection)
                run = repo.list_campaign_module_runs(campaign["id"], limit=1)[0]
                self.assertEqual(run["status"], "completed")
                state = repo.get_scenario_run_state(run["id"])["snapshot"]
                self.assertTrue(state.facts["lock"]["weakness_found"])
                self.assertTrue(state.facts["room"]["entered"])
                self.assertEqual(state.ending_id, "inside-room")
                plan = repo.get_kernel_plan_for_action(first["player_action"]["id"])
                self.assertEqual(plan["status"], "completed")
                self.assertEqual(
                    [step["status"] for step in plan["steps"]],
                    ["committed", "committed"],
                )
            finally:
                connection.close()

    async def test_human_kp_selects_bound_kernel_operator_without_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                db_path=Path(tmpdir) / "manual-kernel-api.sqlite3",
                llm_base_url="http://must-not-be-called.local/v1",
                llm_model="must-not-be-called",
            )
            app = create_app(settings)
            app.dependency_overrides[get_settings] = lambda: settings
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test.local"
            ) as client:
                campaign = (await client.post(
                    "/campaigns", json={"title": "Human kernel"}
                )).json()
                session = (await client.post(
                    f"/campaigns/{campaign['id']}/sessions",
                    json={"kp_display_name": "Human KP"},
                )).json()
                kp_headers = {"Authorization": f"Bearer {session['access_token']}"}
                player = await create_approved_player(
                    client,
                    campaign=campaign,
                    session=session,
                    kp_headers=kp_headers,
                    display_name="Player",
                    sheet=coc7_sheet("Passenger", skills={"话术": 55, "说服": 60}),
                )
                await confirm_current_session_zero(
                    client,
                    campaign_id=campaign["id"],
                    member_headers=(kp_headers, player["headers"]),
                )
                install_risk_contract(
                    settings.db_path, campaign["id"], session["member"]["id"]
                )
                action = (await client.post(
                    f"/campaigns/{campaign['id']}/actions",
                    headers=player["headers"],
                    json={
                        "action_text": "我声称自己是列车长的失散亲属，请他交出钥匙。",
                        "client_action_id": "manual-kernel-001",
                        "auto_advance": False,
                    },
                )).json()

                catalog_response = await client.get(
                    f"/player-actions/{action['id']}/kernel-candidates",
                    headers=kp_headers,
                )
                self.assertEqual(catalog_response.status_code, 200)
                catalog = catalog_response.json()["candidates"]
                deception = next(
                    item for item in catalog
                    if item["candidate_id"] == "attempt-deception"
                )
                self.assertTrue(deception["available"])
                self.assertEqual(
                    [item["skill_key"] for item in deception["skill_choices"]],
                    ["话术", "说服"],
                )

                prepared_response = await client.post(
                    f"/player-actions/{action['id']}/kernel-selection",
                    headers=kp_headers,
                    json={
                        "operator_id": "attempt-deception",
                        "requested_skill_key": "说服",
                    },
                )
                self.assertEqual(prepared_response.status_code, 200)
                prepared = prepared_response.json()
                self.assertEqual(
                    prepared["adjudication"]["source_model"], "kernel:human-kp"
                )
                self.assertEqual(prepared["adjudication"]["selected_skill"], "说服")
                self.assertEqual(
                    [item["skill_name"] for item in prepared["adjudication"]["skill_options"]],
                    ["说服", "话术"],
                )
                self.assertEqual(prepared["proposal"]["status"], "draft")
                self.assertEqual(len(prepared["preview_hash"]), 64)
                kernel_payload = next(
                    item["payload"]
                    for item in prepared["proposal"]["actions"]
                    if item["action_type"] == "kernel_resolution"
                )
                authority_basis = kernel_payload["authority_basis"]
                self.assertEqual(
                    kernel_payload["schema_version"], "kernel-resolution.v3"
                )
                self.assertEqual(
                    authority_basis["schema_version"], "kernel-authority-basis.v1"
                )
                self.assertEqual(
                    authority_basis["preview_hash"], prepared["preview_hash"]
                )
                self.assertEqual(authority_basis["run_id"], kernel_payload["run_id"])
                self.assertEqual(len(authority_basis["contract_hash"]), 64)

                confirmed = (await client.post(
                    f"/player-actions/{action['id']}/adjudication/confirm",
                    headers=player["headers"],
                    json={
                        "expected_version": prepared["adjudication"]["version"],
                        "selected_skill": "话术",
                    },
                )).json()
                self.assertEqual(confirmed["status"], "awaiting_roll")
                self.assertEqual(confirmed["checks"][0]["skill_name"], "话术")
                self.assertEqual(confirmed["checks"][0]["target"], 55)

    async def test_dangerous_train_jump_requires_player_confirmed_idea_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                db_path=Path(tmpdir) / "jump-api.sqlite3",
                llm_base_url="http://unused.local/v1",
                llm_api_key="test",
                llm_model="fake-jump",
            )
            app = create_app(settings)
            app.dependency_overrides[get_settings] = lambda: settings
            with patch(
                "ai_kp.api.main.OpenAICompatibleClient", return_value=FakeJumpLlm()
            ):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://test.local"
                ) as client:
                    campaign = (await client.post("/campaigns", json={"title": "常暗之厢"})).json()
                    session = (
                        await client.post(
                            f"/campaigns/{campaign['id']}/sessions",
                            json={"kp_display_name": "Auto KP"},
                        )
                    ).json()
                    kp_headers = {"Authorization": f"Bearer {session['access_token']}"}
                    player = await create_approved_player(
                        client,
                        campaign=campaign,
                        session=session,
                        kp_headers=kp_headers,
                        display_name="Bold Player",
                        sheet=coc7_sheet(
                            "Passenger",
                            skills={"跳跃": 40, "话术": 55, "说服": 60},
                            characteristics={"int": 70},
                        ),
                    )
                    await confirm_current_session_zero(
                        client,
                        campaign_id=campaign["id"],
                        member_headers=(kp_headers, player["headers"]),
                    )
                    install_risk_contract(
                        settings.db_path,
                        campaign["id"],
                        session["member"]["id"],
                    )
                    result = (
                        await client.post(
                            f"/campaigns/{campaign['id']}/actions",
                            headers=player["headers"],
                            json={
                                "action_text": "我确认列车长是亲属，然后从行驶列车跳下。",
                                "client_action_id": "dangerous-jump-001",
                                "auto_advance": True,
                            },
                        )
                    ).json()

                    ruling = result["adjudication"]
                    self.assertEqual(ruling["mode"], "skill_check")
                    self.assertEqual(ruling["selected_skill"], "INT")
                    self.assertEqual(ruling["skill_options"][0]["target"], 70)
                    self.assertIn("不保证安全", ruling["skill_options"][0]["reason"])
                    self.assertEqual(result["proposal"]["status"], "draft")
                    self.assertEqual(result["proposal"]["proposed_events"], [])
                    self.assertIn(
                        "不保证安全",
                        result["proposal"]["action_ruling"]["maximum_effect"],
                    )
                    self.assertEqual(
                        ruling["ruling"], result["proposal"]["action_ruling"]
                    )
                    pending = (
                        await client.get(
                            f"/campaigns/{campaign['id']}/action-adjudications/pending",
                            headers=player["headers"],
                        )
                    ).json()
                    self.assertEqual([item["id"] for item in pending], [ruling["id"]])

                    confirmed = (
                        await client.post(
                            f"/player-actions/{result['player_action']['id']}"
                            "/adjudication/confirm",
                            headers=player["headers"],
                            json={
                                "expected_version": ruling["version"],
                                "selected_skill": "INT",
                            },
                        )
                    ).json()
                    self.assertEqual(confirmed["status"], "awaiting_roll")
                    self.assertEqual(confirmed["checks"][0]["skill_name"], "INT")
                    self.assertEqual(confirmed["checks"][0]["target"], 70)
                    pending = (
                        await client.get(
                            f"/campaigns/{campaign['id']}/action-adjudications/pending",
                            headers=player["headers"],
                        )
                    ).json()
                    self.assertEqual(pending, [])

                    resolved_idea = await client.post(
                        f"/checks/{confirmed['checks'][0]['id']}/resolve",
                        headers=player["headers"],
                        json={
                            "input_method": "physical",
                            "ones_digit": 5,
                            "tens_digits": [5],
                            "auto_advance": True,
                        },
                    )
                    self.assertEqual(resolved_idea.status_code, 200)
                    committed_jump = (
                        await client.post(
                            f"/campaigns/{campaign['id']}/actions",
                            headers=player["headers"],
                            json={
                                "action_text": "我已经理解风险，仍坚持从高速列车跳下。",
                                "client_action_id": "dangerous-jump-002",
                                "auto_advance": True,
                            },
                        )
                    ).json()
                    self.assertEqual(
                        committed_jump["adjudication"]["selected_skill"], "跳跃"
                    )
                    self.assertIn(
                        "必然受伤",
                        committed_jump["adjudication"]["ruling"]["maximum_effect"],
                    )
                    self.assertIn(
                        "绝不安全落地",
                        committed_jump["proposal"]["public_narration"]
                        + committed_jump["adjudication"]["ruling"]["maximum_effect"],
                    )

                    anachronism = (
                        await client.post(
                            f"/campaigns/{campaign['id']}/actions",
                            headers=player["headers"],
                            json={
                                "action_text": "我用 1928 年的智能手机 GPS 和微信定位列车长。",
                                "client_action_id": "anachronism-001",
                                "auto_advance": True,
                            },
                        )
                    ).json()
                    self.assertEqual(
                        anachronism["adjudication"]["mode"],
                        "roleplay_or_clarification",
                    )
                    self.assertIn("当前时代", anachronism["adjudication"]["reason"])
                    self.assertEqual(anachronism["proposal"]["status"], "draft")

                    deception = (
                        await client.post(
                            f"/campaigns/{campaign['id']}/actions",
                            headers=player["headers"],
                            json={
                                "action_text": "我声称列车长是失散亲属，请他直接交出万能钥匙。",
                                "client_action_id": "deception-001",
                                "auto_advance": True,
                            },
                        )
                    ).json()
                    deception_ruling = deception["adjudication"]
                    self.assertEqual(deception_ruling["mode"], "skill_check")
                    self.assertEqual(deception_ruling["selected_skill"], "话术")
                    self.assertEqual(deception_ruling["skill_options"][0]["target"], 55)
                    self.assertIn("不能确认亲属关系为真", deception_ruling["reason"])
                    self.assertEqual(deception["proposal"]["proposed_events"], [])
                    self.assertIn(
                        "不保证交出钥匙",
                        deception_ruling["ruling"]["maximum_effect"],
                    )
                    self.assertEqual(deception["proposal"]["status"], "draft")

    async def test_kp_turn_saves_structured_draft_context_and_approved_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                db_path=Path(tmpdir) / "api.sqlite3",
                llm_base_url="http://unused.local/v1",
                llm_api_key="test",
                llm_model="fake-structured",
            )
            app = create_app(settings)
            app.dependency_overrides[get_settings] = lambda: settings
            with patch(
                "ai_kp.api.main.OpenAICompatibleClient",
                return_value=FakeStructuredLlm(),
            ):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://test.local",
                ) as client:
                    campaign = (
                        await client.post(
                            "/campaigns",
                            json={"title": "雾港 1928", "current_time": "1928-10-03 19:30"},
                        )
                    ).json()
                    session_bundle = (
                        await client.post(
                            f"/campaigns/{campaign['id']}/sessions",
                            json={"kp_display_name": "Test KP"},
                        )
                    ).json()
                    auth_headers = {
                        "Authorization": f"Bearer {session_bundle['access_token']}"
                    }
                    draft_response = await client.post(
                        "/kp/turn",
                        json={
                            "campaign_id": campaign["id"],
                            "player_action": "我检查仓库门。",
                        },
                        headers=auth_headers,
                    )

                    self.assertEqual(draft_response.status_code, 200)
                    draft = draft_response.json()
                    self.assertEqual(draft["status"], "draft")
                    self.assertEqual(draft["proposed_checks"], [])
                    self.assertEqual(draft["proposed_memories"][0]["scope"], "clue")

                    context = (
                        await client.get(
                            f"/kp/proposals/{draft['id']}/context",
                            headers=auth_headers,
                        )
                    ).json()
                    self.assertEqual(context["proposal_id"], draft["id"])
                    self.assertTrue(context["included_sources"])

                    approved = (
                        await client.post(
                        f"/kp/proposals/{draft['id']}/approve",
                            json={"actor": "human_kp", "note": "API integration test"},
                            headers=auth_headers,
                        )
                    ).json()
                    self.assertEqual(approved["status"], "approved")

                    memories = (
                        await client.get(
                            f"/campaigns/{campaign['id']}/memory/search",
                            params={"q": "仓库刮痕"},
                            headers=auth_headers,
                        )
                    ).json()
                    self.assertTrue(any(item["scope"] == "clue" for item in memories))

    async def test_player_action_auto_advance_waits_for_player_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = Settings(
                db_path=Path(tmpdir) / "auto-api.sqlite3",
                llm_base_url="http://unused.local/v1",
                llm_api_key="test",
                llm_model="fake-structured",
            )
            app = create_app(settings)
            app.dependency_overrides[get_settings] = lambda: settings
            with patch(
                "ai_kp.api.main.OpenAICompatibleClient",
                return_value=FakeStructuredLlm(),
            ):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://test.local",
                ) as client:
                    campaign = (
                        await client.post(
                            "/campaigns",
                            json={"title": "雾港自动团"},
                        )
                    ).json()
                    session_bundle = (
                        await client.post(
                            f"/campaigns/{campaign['id']}/sessions",
                            json={"kp_display_name": "Test KP"},
                        )
                    ).json()
                    kp_headers = {
                        "Authorization": f"Bearer {session_bundle['access_token']}"
                    }
                    approved_player = await create_approved_player(
                        client,
                        campaign=campaign,
                        session=session_bundle,
                        kp_headers=kp_headers,
                        display_name="Player",
                        sheet=coc7_sheet("Investigator", skills={"侦查": 60}),
                    )
                    await confirm_current_session_zero(
                        client,
                        campaign_id=campaign["id"],
                        member_headers=(kp_headers, approved_player["headers"]),
                    )

                    response = await client.post(
                        f"/campaigns/{campaign['id']}/actions",
                        headers=approved_player["headers"],
                        json={
                            "action_text": "我检查仓库门。",
                            "client_action_id": "auto-api-action-001",
                            "auto_advance": True,
                        },
                    )

                    self.assertEqual(response.status_code, 200, response.text)
                    result = response.json()
                    self.assertEqual(result["status"], "awaiting_confirmation")
                    self.assertEqual(result["proposal"]["status"], "draft")
                    self.assertEqual(result["player_action"]["status"], "reviewed")
                    self.assertEqual(
                        result["adjudication"]["mode"], "direct_resolution"
                    )

                    confirmed = (
                        await client.post(
                            f"/player-actions/{result['player_action']['id']}"
                            "/adjudication/confirm",
                            headers=approved_player["headers"],
                            json={
                                "expected_version": result["adjudication"]["version"],
                                "selected_skill": None,
                            },
                        )
                    ).json()
                    self.assertEqual(confirmed["status"], "completed")
                    self.assertEqual(confirmed["proposal"]["status"], "approved")
                    self.assertEqual(confirmed["player_action"]["status"], "resolved")

                    public_turns_response = await client.get(
                        f"/campaigns/{campaign['id']}/public-turns",
                        headers=approved_player["headers"],
                    )
                    self.assertEqual(public_turns_response.status_code, 200)
                    public_turns = public_turns_response.json()
                    self.assertEqual(len(public_turns), 1)
                    self.assertEqual(
                        public_turns[0]["public_narration"],
                        "仓库门上有一道新鲜刮痕。",
                    )
                    self.assertNotIn("kp_notes", public_turns[0])
                    self.assertNotIn("proposed_events", public_turns[0])


if __name__ == "__main__":
    unittest.main()

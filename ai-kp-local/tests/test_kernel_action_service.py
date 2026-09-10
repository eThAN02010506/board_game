from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ai_kp.application.action_adjudication_service import ActionAdjudicationService
from ai_kp.application.auto_turn_service import AutoTurnService
from ai_kp.application.consequence_signal_service import ConsequenceSignalService
from ai_kp.application.errors import ConflictError
from ai_kp.application.kernel_action_service import KernelActionService
from ai_kp.application.scenario_contract_service import ScenarioContractService
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import TurnService
from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.director.orchestrator import KpOrchestrator
from ai_kp.infrastructure.database.schema import connect, init_db
from ai_kp.platform.resolution import exact_kernel_outcome
from ai_kp.platform.resolution.kernel import (
    ActionResolutionKernel,
    operator_outcome_path,
)
from tests.scenario_contract_testkit import bind_payload_to_module, source_bound_payload
from tests.test_bounded_planning import delegation_payload
from tests.test_check_consequence_fingerprint import _check
from tests.test_scenario_contract_service import create_module


class ForbiddenExplicitSelectionLlm:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls += 1
        raise AssertionError(
            "Explicit published operator selection must bypass the weak model"
        )


class TaskMethodSelectionLlm:
    async def complete(self, messages, temperature: float = 0.7) -> str:
        system_prompt = messages[0].content
        if "只负责判断玩家这句话在桌面角色扮演对话中属于什么" in system_prompt:
            return json.dumps(
                {
                    "kind": "multi_step_action",
                    "goal": "Enter the room",
                    "method": "Open the door, then enter",
                    "target_entity_ids": [],
                    "dialogue": "",
                    "steps": ["Open the door", "Enter the room"],
                    "time_span": "",
                    "ambiguity": None,
                    "confidence": "high",
                }
            )
        return json.dumps(
            {
                "kind": "task_method",
                "candidate_id": "open-and-enter",
                "requested_skill_key": None,
                "confidence": "high",
                "clarification": None,
            }
        )


class SequencedExpansionLlm:
    def __init__(self, responses: list[dict]):
        self.responses = responses

    async def complete(self, messages, temperature: float = 0.7) -> str:
        return json.dumps(self.responses.pop(0))


class NpcConversationLlm:
    def __init__(self):
        self.calls = 0

    async def complete(self, messages, temperature: float = 0.7) -> str:
        self.calls += 1
        if self.calls == 1:
            return json.dumps({
                "kind": "npc_dialogue",
                "goal": "询问警告的含义",
                "method": "当面提问",
                "target_entity_ids": ["witness"],
                "dialogue": "你为什么叫我离开？",
                "steps": [],
                "time_span": "",
                "ambiguity": None,
                "confidence": "high",
            }, ensure_ascii=False)
        if self.calls == 2:
            text = messages[-1].content
            basis = json.loads(text.split("受限依据：", 1)[1].split("\n返回", 1)[0])
            return json.dumps({
                "basis_hash": basis["basis_hash"],
                "public_narration": (
                    "维托里奥猛撞观察窗。他用指甲抓挠观察窗边缘。"
                    "他已经陷入恐慌。‘你必须离开这里。现在就走！’"
                ),
                "speaker_entity_ids": ["witness"],
            }, ensure_ascii=False)
        return json.dumps({
            "accepted": True,
            "missing": [],
            "contradictions": [],
            "patch_instruction": "",
        })


class StateDriftingNpcConversationLlm(NpcConversationLlm):
    """Advance the authoritative snapshot while the conversation model is running."""

    def __init__(self, repo: Repository, run_id: str):
        super().__init__()
        self.repo = repo
        self.run_id = run_id

    async def complete(self, messages, temperature: float = 0.7) -> str:
        if self.calls == 1:
            state = self.repo.get_scenario_run_state(self.run_id)
            next_version = int(state["state_version"]) + 1
            snapshot = state["snapshot"].model_copy(
                update={"run_version": next_version}
            )
            self.repo.connection.execute(
                """
                UPDATE scenario_run_states
                SET state_version = ?, snapshot_json = ?
                WHERE run_id = ?
                """,
                (
                    next_version,
                    json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False),
                    self.run_id,
                ),
            )
        return await super().complete(messages, temperature)


class UnboundWorldQuestionLlm:
    async def complete(self, messages, temperature: float = 0.7) -> str:
        return json.dumps({
            "kind": "world_question",
            "goal": "了解当前可感知环境",
            "method": "直接观察",
            "target_entity_ids": [],
            "dialogue": "",
            "steps": [],
            "time_span": "",
            "ambiguity": None,
            "confidence": "high",
        }, ensure_ascii=False)


def contract_payload() -> dict:
    return source_bound_payload({
        "contract_id": "kernel-action-fixture",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Kernel action fixture",
        "clocks": [
            {
                "clock_id": "pressure",
                "title": "Case pressure",
                "clock_kind": "hard",
                "maximum_value": 3,
            }
        ],
        "entities": [
            {
                "entity_id": "nightmare-pressure",
                "entity_type": "hazard",
                "title": "Nightmare pressure",
            }
        ],
        "operators": [
            {
                "operator_id": "open-unlocked-door",
                "title": "Open an unlocked door",
                "public_setup": "你把手放上门把，行动正在等待确认。",
                "narrative_cues": [
                    {
                        "outcome_key": "success",
                        "public_summary": "门已经敞开，通道现在可用。",
                    }
                ],
                "policy": "automatic",
                "always_commands": [
                    {"kind": "advance_clock", "clock_id": "pressure", "delta": 1}
                ],
                "success_commands": [
                    {"kind": "set_fact", "path": "door.open", "value": True}
                ],
                "rationale": "The door is unlocked and within reach.",
                "maximum_effect": "The door becomes open.",
            }
        ],
        "reactive_policies": [
            {
                "policy_id": "nightmare-at-pressure",
                "entity_id": "nightmare-pressure",
                "rules": [
                    {
                        "rule_id": "nightmare-warning",
                        "trigger": "clock_advanced",
                        "conditions": [
                            {
                                "path": "clocks.pressure",
                                "operator": "gte",
                                "value": 1,
                            }
                        ],
                        "commands": [
                            {
                                "kind": "set_fact",
                                "path": "pressure.nightmare_pending",
                                "value": True,
                            }
                        ],
                        "rationale": "Pressure produces a bounded nightmare warning.",
                    }
                ],
            }
        ],
        "consequence_signals": [
            {
                "signal_id": "case-pressure-warning",
                "title": "Internal case pressure",
                "visibility": "table",
                "display_mode": "stage",
                "public_title": "The situation is changing",
                "bands": [
                    {
                        "band_id": "quiet",
                        "priority": 0,
                        "player_visible": False,
                    },
                    {
                        "band_id": "warning",
                        "priority": 10,
                        "severity": 2,
                        "all_conditions": [
                            {
                                "path": "clocks.pressure",
                                "operator": "gte",
                                "value": 1,
                            }
                        ],
                        "player_visible": True,
                        "public_label": "Consequences are approaching",
                        "public_description": "The world is no longer waiting quietly.",
                    },
                ],
            }
        ],
        "endings": [
            {
                "ending_id": "door-opened",
                "title": "Door opened",
                "all_conditions": [
                    {"path": "facts.door.open", "operator": "eq", "value": True},
                    {
                        "path": "facts.pressure.nightmare_pending",
                        "operator": "eq",
                        "value": True,
                    },
                ],
            }
        ],
    }, source_block_id="kernel-action-source")


def conversation_contract_payload() -> dict:
    return source_bound_payload({
        "contract_id": "conversation-fixture",
        "source_version": 1,
        "ruleset_id": "coc7",
        "title": "Conversation fixture",
        "initial_scene_id": "cell",
        "locations": [{
            "location_id": "cell",
            "title": "观察室",
            "initial_visibility": "visited",
        }],
        "entities": [{
            "entity_id": "witness",
            "entity_type": "npc",
            "title": "维托里奥",
            "initial_location_id": "cell",
            "canonical_profile": {
                "summary": "精神状态明显异常。",
                "secrets": ["不可公开的真相。"],
                "behavioral_directives": ["动作急促而且会伤害自己。"],
            },
            "derived_profile": {
                "traits": ["偏执"],
                "speech_style": ["断裂的短句"],
            },
        }],
        "response_obligations": [{
            "obligation_id": "warning",
            "entity_id": "witness",
            "trigger_topics": ["离开"],
            "facts_to_convey": ["你必须离开这里。"],
            "state_to_express": ["他已经陷入恐慌。"],
            "physical_behaviors": ["他用指甲抓挠观察窗边缘。"],
            "boundaries": ["不可公开的真相。"],
        }],
        "initial_facts": {"conversation": {"can_end": True}},
        "operators": [{
            "operator_id": "end-conversation-scene",
            "title": "End the conversation scene",
            "policy": "choice",
            "preconditions": [{
                "path": "facts.conversation.can_end",
                "operator": "eq",
                "value": True,
            }],
            "success_commands": [{
                "kind": "set_fact",
                "path": "conversation.ended",
                "value": True,
            }],
        }],
        "endings": [{
            "ending_id": "conversation-ended",
            "title": "Conversation ended",
            "all_conditions": [{
                "path": "facts.conversation.ended",
                "operator": "eq",
                "value": True,
            }],
        }],
    }, source_block_id="conversation-source")


def test_npc_conversation_replies_without_matching_or_committing_an_operator(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "tabletop-conversation.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Conversation campaign")
        session = SessionService(repo).create(campaign["id"])
        player_bundle = SessionService(repo).join(
            session["join_code"], display_name="Player"
        )
        kp = repo.authenticate_access_token(session["access_token"])
        player = repo.authenticate_access_token(player_bundle["access_token"])
        assert kp is not None and player is not None
        module = create_module(repo, campaign["id"])
        contracts = ScenarioContractService(repo)
        _, draft = contracts.compile_draft(
            module["id"],
            bind_payload_to_module(
                repo, module["id"], conversation_contract_payload()
            ),
            created_by_member_id=kp.member_id,
        )
        assert draft is not None
        published = contracts.publish(
            draft["id"], expected_row_version=1,
            published_by_member_id=kp.member_id,
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"], module_id=module["id"],
            current_scene_key=None, active_spoiler_tags=[], state={},
            started_by_member_id=kp.member_id,
        )
        run = repo.set_module_run_automation_level(
            run["id"], expected_version=run["version"], level="ai_kp",
            reason="conversation test", member_id=kp.member_id,
        )
        contracts.bind_run(run["id"], published["id"])
        action = TurnService(repo).submit_player_action(
            player,
            action_text="我问维托里奥：你为什么叫我离开？",
            client_action_id="npc-conversation-turn",
        )

        result = asyncio.run(AutoTurnService(repo).advance_player_action(
            action["id"],
            director=KpOrchestrator(repo.connection, NpcConversationLlm()),
            source_model="weak-model",
        ))

        assert result.adjudication is not None
        assert result.adjudication["mode"] == "direct_resolution"
        assert result.adjudication["tabletop_turn"]["route"] == "roleplay"
        assert result.adjudication["tabletop_turn"]["response"]["actor_traces"] == [
            {
                "schema_version": "entity-actor-trace.v1",
                "entity_id": "witness",
                "entity_title": "维托里奥",
                "execution": "model",
                "generation_attempt_count": 1,
                "error_codes": [],
            }
        ]
        assert repo.get_action_adjudication(action["id"])["tabletop_turn"][
            "response"
        ]["actor_traces"][0]["entity_id"] == "witness"
        assert KernelActionService.kernel_payload(result.proposal) is None
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 0

        stale_action = TurnService(repo).submit_player_action(
            player,
            action_text="我继续追问：你看见了什么？",
            client_action_id="npc-conversation-stale-authority",
        )
        proposal_count = len(repo.list_turn_proposals(campaign["id"]))
        with pytest.raises(ConflictError, match="Scenario authority changed"):
            asyncio.run(
                KernelActionService(repo).prepare(
                    stale_action,
                    kp,
                    KpOrchestrator(
                        repo.connection,
                        StateDriftingNpcConversationLlm(repo, run["id"]),
                    ),
                    source_model="weak-model",
                )
            )
        assert len(repo.list_turn_proposals(campaign["id"])) == proposal_count
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 1

        _, approved, checks, applied = ActionAdjudicationService(repo).confirm(
            action["id"], expected_version=result.adjudication["version"],
            selected_skill=None, identity=player,
        )

        assert applied is True and checks == []
        assert approved["status"] == "approved"
        assert "抓挠观察窗" in approved["public_narration"]
        assert "你必须离开这里" in approved["public_narration"]
        assert "不可公开的真相" not in approved["public_narration"]
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 1
        assert repo.list_scenario_command_batches(run["id"]) == []


def test_unbound_campaign_still_routes_world_question_before_legacy_ai(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "unbound-tabletop.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Unbound tabletop campaign")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        player_bundle = sessions.join(
            kp_bundle["join_code"], display_name="Player"
        )
        player = repo.authenticate_access_token(player_bundle["access_token"])
        assert player is not None
        action = TurnService(repo).submit_player_action(
            player,
            action_text="我停在原地，现在直接能看到什么？",
            client_action_id="unbound-world-question",
        )

        result = asyncio.run(AutoTurnService(repo).advance_player_action(
            action["id"],
            director=KpOrchestrator(repo.connection, UnboundWorldQuestionLlm()),
            source_model="weak-model",
        ))

        assert result.adjudication is not None
        assert result.adjudication["tabletop_turn"]["route"] == "information"
        assert result.adjudication["mode"] == "direct_resolution"
        assert result.proposal["proposed_facts"] == []
        assert "现有公开状态不足" in result.proposal["public_narration"]
        assert KernelActionService.kernel_payload(result.proposal) is None


def test_bound_run_uses_kernel_by_default_and_commits_after_player_confirmation(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "kernel-action.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Kernel-first campaign")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        player_bundle = sessions.join(kp_bundle["join_code"], display_name="Player")
        kp_identity = repo.authenticate_access_token(kp_bundle["access_token"])
        player_identity = repo.authenticate_access_token(player_bundle["access_token"])
        assert kp_identity is not None
        assert player_identity is not None

        module = create_module(repo, campaign["id"])
        contracts = ScenarioContractService(repo)
        _, draft = contracts.compile_draft(
            module["id"],
            bind_payload_to_module(repo, module["id"], contract_payload()),
            created_by_member_id=kp_identity.member_id,
        )
        assert draft is not None
        published = contracts.publish(
            draft["id"],
            expected_row_version=1,
            published_by_member_id=kp_identity.member_id,
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key=None,
            active_spoiler_tags=[],
            state={},
            started_by_member_id=kp_identity.member_id,
        )
        run = repo.set_module_run_automation_level(
            run["id"],
            expected_version=run["version"],
            level="ai_kp",
            reason="Kernel authority integration test",
            member_id=kp_identity.member_id,
        )
        contracts.bind_run(run["id"], published["id"])
        action = TurnService(repo).submit_player_action(
            player_identity,
            action_text='选择已发布行动“Open an unlocked door”',
            client_action_id="kernel-action-1",
        )
        llm = ForbiddenExplicitSelectionLlm()

        result = asyncio.run(
            AutoTurnService(repo).advance_player_action(
                action["id"],
                director=KpOrchestrator(repo.connection, llm),
                source_model="weak-model",
            )
        )

        assert llm.calls == 0
        assert result.adjudication["mode"] == "direct_resolution"
        assert result.proposal["source_model"] == "kernel:weak-model"
        kernel_payload = KernelActionService.kernel_payload(result.proposal)
        assert kernel_payload is not None
        assert kernel_payload["schema_version"] == "kernel-resolution.v3"
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 0
        pending_action = TurnService(repo).submit_player_action(
            player_identity,
            action_text="I wait outside while the door is opened.",
            client_action_id="kernel-action-pending",
        )
        pending_job = repo.enqueue_auto_kp_job(
            campaign_id=campaign["id"],
            run_id=run["id"],
            job_type="player_action",
            resource_id=pending_action["id"],
            idempotency_key="kernel-action-pending",
        )
        adjudication, proposal, checks, applied = ActionAdjudicationService(repo).confirm(
            action["id"],
            expected_version=result.adjudication["version"],
            selected_skill=None,
            identity=player_identity,
        )

        assert adjudication["status"] == "confirmed"
        assert proposal["status"] == "approved"
        assert proposal["public_narration"] == "门已经敞开，通道现在可用。"
        assert checks == []
        assert applied is True
        state = repo.get_scenario_run_state(run["id"])
        assert state["state_version"] == 1
        assert state["snapshot"].facts["door"]["open"] is True
        assert state["snapshot"].facts["pressure"]["nightmare_pending"] is True
        assert state["snapshot"].clocks["pressure"] == 1
        assert state["snapshot"].ending_id == "door-opened"
        completed_run = repo.get_campaign_module_run(run["id"])
        assert completed_run["status"] == "completed"
        assert completed_run["completed_at"] is not None
        assert repo.get_player_action(pending_action["id"])["status"] == "rejected"
        assert repo.get_auto_kp_job(pending_job["id"])["status"] == "cancelled"
        query = ActionResolutionKernel.from_contract(published["contract"]).query_snapshot(
            state["snapshot"]
        )
        assert query.state_value(operator_outcome_path("open-unlocked-door")) == (
            True,
            "success",
        )
        table_signals = ConsequenceSignalService(repo).campaign_view(
            campaign["id"], audience="table"
        )
        assert table_signals["signals"][0]["title"] == "The situation is changing"
        assert table_signals["signals"][0]["label"] == (
            "Consequences are approaching"
        )
        assert "case-pressure-warning" not in str(table_signals)
        batches = repo.list_scenario_command_batches(run["id"])
        assert len(batches) == 1
        assert batches[0]["batch_kind"] == "action"
        assert batches[0]["authority_basis"] == kernel_payload["authority_basis"]
        assert [item["kind"] for item in batches[0]["commands"]] == [
            "emit_event",
            "advance_clock",
            "set_fact",
            "set_fact",
        ]
        assert batches[0]["commands"][0]["payload"]["actor_id"] == (
            action.get("pc_id") or action["member_id"]
        )


@pytest.mark.parametrize(
    "tamper_kind",
    ["legacy_v2", "contract_hash", "preview_hash", "run_id", "proposal_id"],
)
def test_kernel_commit_rejects_stale_or_tampered_authority_without_writes(
    tmp_path: Path,
    tamper_kind: str,
) -> None:
    with db_session(tmp_path / f"kernel-authority-{tamper_kind}.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Kernel authority tamper campaign")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        player_bundle = sessions.join(kp_bundle["join_code"], display_name="Player")
        kp_identity = repo.authenticate_access_token(kp_bundle["access_token"])
        player_identity = repo.authenticate_access_token(player_bundle["access_token"])
        assert kp_identity is not None and player_identity is not None

        module = create_module(repo, campaign["id"])
        contracts = ScenarioContractService(repo)
        _, draft = contracts.compile_draft(
            module["id"],
            bind_payload_to_module(repo, module["id"], contract_payload()),
            created_by_member_id=kp_identity.member_id,
        )
        assert draft is not None
        published = contracts.publish(
            draft["id"],
            expected_row_version=1,
            published_by_member_id=kp_identity.member_id,
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"],
            module_id=module["id"],
            current_scene_key=None,
            active_spoiler_tags=[],
            state={},
            started_by_member_id=kp_identity.member_id,
        )
        contracts.bind_run(run["id"], published["id"])
        action = TurnService(repo).submit_player_action(
            player_identity,
            action_text='选择已发布行动“Open an unlocked door”',
            client_action_id=f"kernel-authority-{tamper_kind}",
        )
        proposal, preview = KernelActionService(repo).prepare_manual(
            action,
            kp_identity,
            operator_id="open-unlocked-door",
            requested_skill_key=None,
        )
        assert preview.allowed is True
        stored_payload = next(
            item["payload"]
            for item in proposal["actions"]
            if item["action_type"] == "kernel_resolution"
        )
        if tamper_kind == "legacy_v2":
            stored_payload["schema_version"] = "kernel-resolution.v2"
            stored_payload.pop("authority_basis")
        elif tamper_kind == "contract_hash":
            stored_payload["authority_basis"]["contract_hash"] = "0" * 64
        elif tamper_kind == "preview_hash":
            stored_payload["authority_basis"]["preview_hash"] = "0" * 64
        elif tamper_kind == "run_id":
            stored_payload["authority_basis"]["run_id"] = "run-tampered"
        else:
            proposal["id"] = "proposal-tampered"

        with pytest.raises(ValueError):
            KernelActionService(repo).commit(
                proposal,
                outcome="success",
                identity=kp_identity,
            )

        state = repo.get_scenario_run_state(run["id"])
        assert state["state_version"] == 0
        assert state["snapshot"].facts.get("door") is None
        assert repo.list_scenario_command_batches(run["id"]) == []


def test_task_method_persists_and_repreviews_each_primitive_after_commit(
    tmp_path: Path,
) -> None:
    payload = contract_payload()
    payload["operators"] = [
        {
            "operator_id": "open-door",
            "title": "Open the door",
            "policy": "automatic",
            "success_commands": [
                {"kind": "set_fact", "path": "door.open", "value": True}
            ],
        },
        {
            "operator_id": "enter-room",
            "title": "Enter the room",
            "policy": "automatic",
            "preconditions": [
                {"path": "facts.door.open", "operator": "eq", "value": True}
            ],
            "success_commands": [
                {"kind": "set_fact", "path": "room.entered", "value": True}
            ],
        },
    ]
    payload["task_methods"] = [{
        "method_id": "open-and-enter",
        "task_key": "enter-room",
        "title": "Open and enter",
        "intent_hints": ["open then enter"],
        "steps": [
            {"step_id": "open", "operator_id": "open-door"},
            {
                "step_id": "enter",
                "operator_id": "enter-room",
                "depends_on": ["open"],
            },
        ],
    }]
    payload["reactive_policies"] = []
    payload["consequence_signals"] = []
    payload["endings"] = [{
        "ending_id": "inside",
        "title": "Inside",
        "all_conditions": [
            {"path": "facts.room.entered", "operator": "eq", "value": True}
        ],
    }]
    payload = source_bound_payload(
        payload, source_block_id="durable-plan-source"
    )
    with db_session(tmp_path / "durable-plan.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Durable plan")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        player_bundle = sessions.join(kp_bundle["join_code"], display_name="Player")
        kp = repo.authenticate_access_token(kp_bundle["access_token"])
        player = repo.authenticate_access_token(player_bundle["access_token"])
        assert kp is not None and player is not None
        module = create_module(repo, campaign["id"])
        contracts = ScenarioContractService(repo)
        _, draft = contracts.compile_draft(
            module["id"],
            bind_payload_to_module(repo, module["id"], payload),
            created_by_member_id=kp.member_id,
        )
        assert draft is not None
        published = contracts.publish(
            draft["id"], expected_row_version=1, published_by_member_id=kp.member_id
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"], module_id=module["id"],
            current_scene_key=None, active_spoiler_tags=[], state={},
            started_by_member_id=kp.member_id,
        )
        run = repo.set_module_run_automation_level(
            run["id"], expected_version=run["version"], level="ai_kp",
            reason="durable plan", member_id=kp.member_id,
        )
        contracts.bind_run(run["id"], published["id"])
        repo.save_model_configuration(
            provider_type="openai_compatible",
            base_url="http://unused.local/v1",
            api_key=None,
            model="large-model",
            local_model_path=None,
            local_port=8001,
            semantic_profile="large",
        )
        root = TurnService(repo).submit_player_action(
            player, action_text="Open the door then enter.",
            client_action_id="durable-plan-root",
        )
        result = asyncio.run(AutoTurnService(repo).advance_player_action(
            root["id"],
            director=KpOrchestrator(repo.connection, TaskMethodSelectionLlm()),
            source_model="large-model",
        ))
        plan = repo.get_kernel_plan_for_action(root["id"])
        assert plan is not None
        assert plan["status"] == "active"
        assert [item["status"] for item in plan["steps"]] == [
            "awaiting_resolution", "pending"
        ]

        _, _, checks, applied = ActionAdjudicationService(repo).confirm(
            root["id"], expected_version=result.adjudication["version"],
            selected_skill=None, identity=player,
        )
        assert checks == [] and applied is True
        plan = repo.get_kernel_plan(str(plan["id"]))
        assert plan["current_step_index"] == 1
        second_action_id = plan["steps"][1]["action_id"]
        assert second_action_id is not None
        second_ruling = repo.get_action_adjudication(second_action_id)
        second_payload = KernelActionService.kernel_payload(
            repo.get_turn_proposal(second_ruling["proposal_id"])
        )
        assert second_payload["preview"]["run_version"] == 1
        assert second_payload["preview"]["operator_id"] == "enter-room"

        _, _, checks, applied = ActionAdjudicationService(repo).confirm(
            second_action_id,
            expected_version=second_ruling["version"],
            selected_skill=None,
            identity=player,
        )
        assert checks == [] and applied is True
        plan = repo.get_kernel_plan(str(plan["id"]))
        assert plan["status"] == "completed"
        state = repo.get_scenario_run_state(run["id"])["snapshot"]
        assert state.facts["door"]["open"] is True
        assert state.facts["room"]["entered"] is True
        assert state.ending_id == "inside"
        assert state.run_version == 2


@pytest.mark.parametrize("outcome", ("failure", "pushed_failure"))
def test_kernel_plan_failure_stops_before_later_primitives(
    tmp_path: Path, outcome: str
) -> None:
    with db_session(tmp_path / "failed-plan.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Failed plan")
        session = SessionService(repo).create(campaign["id"])
        player_bundle = SessionService(repo).join(
            session["join_code"], display_name="Player"
        )
        player = repo.authenticate_access_token(player_bundle["access_token"])
        assert player is not None
        module = create_module(repo, campaign["id"])
        payload = delegation_payload()
        payload["task_methods"][0]["steps"][2]["actor_binding"] = "initiator"
        service = ScenarioContractService(repo)
        _, draft = service.compile_draft(
            module["id"],
            bind_payload_to_module(repo, module["id"], payload),
            created_by_member_id=session["member"]["id"],
        )
        assert draft is not None
        published = service.publish(
            draft["id"], expected_row_version=1,
            published_by_member_id=session["member"]["id"],
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"], module_id=module["id"],
            current_scene_key=None, active_spoiler_tags=[], state={},
            started_by_member_id=session["member"]["id"],
        )
        service.bind_run(run["id"], published["id"])
        repo.initialize_scenario_run_state(run["id"])
        action = TurnService(repo).submit_player_action(
            player, action_text="Earn, hire, and delegate",
            client_action_id="failed-plan-root",
        )
        repo.create_kernel_plan(
            run_id=run["id"], root_action_id=action["id"],
            contract_hash=published["contract_hash"],
            method_id="fund-hire-delegate", task_key="obtain-investigation-report",
            steps=[("earn", "earn-funds"), ("hire", "hire-agent"),
                   ("delegate", "delegate-search")],
        )
        repo.record_kernel_plan_preview(
            repo.get_kernel_plan_for_action(action["id"])["id"], 0,
            action_id=action["id"], preview_hash="a" * 64,
        )

        plan = repo.finish_kernel_plan_step(action["id"], outcome=outcome)

        assert plan["status"] == "failed"
        assert plan["current_step_index"] == 0
        assert [item["status"] for item in plan["steps"]] == [
            "failed", "pending", "pending"
        ]
        assert repo.get_scenario_run_state(run["id"])["snapshot"].run_version == 0


def test_kernel_plan_step_state_survives_database_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "restart-plan.sqlite3"
    connection = connect(db_path)
    try:
        init_db(connection)
        repo = Repository(connection)
        campaign = repo.create_campaign("Restart plan")
        session = SessionService(repo).create(campaign["id"])
        player_bundle = SessionService(repo).join(
            session["join_code"], display_name="Player"
        )
        player = repo.authenticate_access_token(player_bundle["access_token"])
        assert player is not None
        module = create_module(repo, campaign["id"])
        payload = delegation_payload()
        payload["task_methods"][0]["steps"][2]["actor_binding"] = "initiator"
        service = ScenarioContractService(repo)
        _, draft = service.compile_draft(
            module["id"],
            bind_payload_to_module(repo, module["id"], payload),
            created_by_member_id=session["member"]["id"],
        )
        assert draft is not None
        published = service.publish(
            draft["id"], expected_row_version=1,
            published_by_member_id=session["member"]["id"],
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"], module_id=module["id"],
            current_scene_key=None, active_spoiler_tags=[], state={},
            started_by_member_id=session["member"]["id"],
        )
        service.bind_run(run["id"], published["id"])
        repo.initialize_scenario_run_state(run["id"])
        action = TurnService(repo).submit_player_action(
            player, action_text="Earn, hire, and delegate",
            client_action_id="restart-plan-root",
        )
        plan = repo.create_kernel_plan(
            run_id=run["id"], root_action_id=action["id"],
            contract_hash=published["contract_hash"],
            method_id="fund-hire-delegate", task_key="obtain-investigation-report",
            steps=[("earn", "earn-funds"), ("hire", "hire-agent"),
                   ("delegate", "delegate-search")],
        )
        repo.record_kernel_plan_preview(
            plan["id"], 0, action_id=action["id"], preview_hash="b" * 64
        )
        connection.commit()
        plan_id = plan["id"]
    finally:
        connection.close()

    restarted = connect(db_path)
    try:
        init_db(restarted)
        recovered = Repository(restarted).get_kernel_plan(plan_id)
        assert recovered["status"] == "active"
        assert recovered["current_step_index"] == 0
        assert recovered["steps"][0]["action_id"] == action["id"]
        assert recovered["steps"][0]["preview_hash"] == "b" * 64
        assert recovered["steps"][0]["status"] == "awaiting_resolution"
    finally:
        restarted.close()


def test_check_success_level_selects_only_an_explicit_contract_outcome_branch() -> None:
    payload = {
        "preview": {
            "outcome_branches": [
                {"outcome_key": "hard", "commands": [{"kind": "emit_event"}]}
            ]
        }
    }

    assert exact_kernel_outcome(
        payload["preview"],
        [_check("check-hard", success_level="hard", passed=True)],
    ) == "hard"
    assert exact_kernel_outcome(
        payload["preview"],
        [_check("check-extreme", success_level="extreme", passed=True)],
    ) == "success"
    assert exact_kernel_outcome(
        payload["preview"],
        [_check("check-failure", success_level="failure", passed=False)],
    ) == "failure"


def test_pushed_failure_fallback_preserves_declared_approach_and_risk() -> None:
    checks = [
        {
            "id": "parent",
            "passed": False,
            "actions": [{
                "action_type": "pushed",
                "reason": "改用未编目索引，并接受暂时失去查档许可的风险。",
            }],
        },
        {
            "id": "child",
            "passed": False,
            "pushed_from_check_id": "parent",
            "actions": [],
        },
    ]

    narration = AutoTurnService._pushed_failure_narration(checks)

    assert narration is not None
    assert narration.startswith("孤注一掷失败")
    assert "暂时失去查档许可" in narration
    assert "严重风险现在发生" in narration


def test_large_profile_can_author_and_activate_a_bounded_missing_action(
    tmp_path: Path,
) -> None:
    with db_session(tmp_path / "kernel-large-overlay.sqlite3") as connection:
        repo = Repository(connection)
        campaign = repo.create_campaign("Large profile campaign")
        sessions = SessionService(repo)
        kp_bundle = sessions.create(campaign["id"])
        player_bundle = sessions.join(kp_bundle["join_code"], display_name="Player")
        kp_identity = repo.authenticate_access_token(kp_bundle["access_token"])
        player_identity = repo.authenticate_access_token(player_bundle["access_token"])
        assert kp_identity is not None and player_identity is not None
        module = create_module(repo, campaign["id"])
        contracts = ScenarioContractService(repo)
        _, draft = contracts.compile_draft(
            module["id"],
            bind_payload_to_module(repo, module["id"], delegation_payload()),
            created_by_member_id=kp_identity.member_id,
        )
        assert draft is not None
        published = contracts.publish(
            draft["id"], expected_row_version=1, published_by_member_id=kp_identity.member_id
        )
        run = repo.start_campaign_module_run(
            campaign_id=campaign["id"], module_id=module["id"], current_scene_key=None,
            active_spoiler_tags=[], state={}, started_by_member_id=kp_identity.member_id,
        )
        run = repo.set_module_run_automation_level(
            run["id"], expected_version=run["version"], level="ai_kp",
            reason="large overlay test", member_id=kp_identity.member_id,
        )
        contracts.bind_run(run["id"], published["id"])
        action = TurnService(repo).submit_player_action(
            player_identity,
            action_text="I arrange a paid courier to retrieve the records.",
            client_action_id="large-overlay-action",
        )
        prefix = f"expansion.{action['id']}."
        llm = SequencedExpansionLlm(
            [
                {
                    "kind": "action",
                    "goal": "Have one bounded records errand begin.",
                    "method": "Arrange a paid courier.",
                    "target_entity_ids": [],
                    "dialogue": "",
                    "steps": [],
                    "time_span": "",
                    "ambiguity": None,
                    "confidence": "high",
                },
                {
                    "kind": "clarification", "candidate_id": None,
                    "requested_skill_key": None, "confidence": "high",
                    "clarification": "No existing method matches.",
                },
                {
                    "goal": "Have one bounded records errand begin.",
                    "steps": [{
                        "step_id": "hire",
                        "operator_id": prefix + "hire-courier",
                        "goal": "Begin the records errand.",
                        "method": "Arrange a paid courier.",
                        "target": "The requested records.",
                        "requirements": [{
                            "requirement_id": "active_run",
                            "category": "execution_context",
                            "description": "The scenario run is active.",
                            "source": "state",
                            "state_path": "status",
                            "comparison": "eq",
                            "expected_value": "active",
                        }],
                        "effects": [{
                            "effect_id": "elapsed",
                            "role": "cost",
                            "command_kind": "advance_clock",
                            "target_ref": "elapsed",
                            "delta": 1,
                            "description": "The errand consumes time.",
                        }, {
                            "effect_id": "hired",
                            "role": "progress",
                            "command_kind": "set_fact",
                            "target_ref": prefix + "courier_hired",
                            "value": True,
                            "description": "One bounded errand begins.",
                        }],
                    }],
                },
                {
                    "verdict": "accept",
                    "reason": "The bounded delegation plan is complete.",
                    "issues": [],
                    "questions": [],
                },
                {
                    "confidence": "high",
                    "assumptions": ["A courier service is plausibly available."],
                    "rationale": "Adds a paid and time-consuming delegation route.",
                    "records": {
                        "operators": [{
                            "operator_id": prefix + "hire-courier",
                            "title": "Hire a records courier",
                            "policy": "automatic",
                            "always_commands": [
                                {"kind": "advance_clock", "clock_id": "elapsed", "delta": 1}
                            ],
                            "success_commands": [
                                {"kind": "set_fact", "path": prefix + "courier_hired", "value": True}
                            ],
                            "maximum_effect": "A courier begins one bounded records errand.",
                        }],
                        "consequence_signals": [{
                            "signal_id": prefix + "courier-progress",
                            "title": "Courier delegation state",
                            "visibility": "table",
                            "display_mode": "narrative",
                            "public_title": "Courier errand",
                            "bands": [{
                                "band_id": "hired",
                                "player_visible": True,
                                "public_label": "The courier has departed",
                                "all_conditions": [{
                                    "path": "facts." + prefix + "courier_hired",
                                    "operator": "eq",
                                    "value": True,
                                }],
                            }],
                        }],
                    },
                },
            ]
        )

        proposal, preview = asyncio.run(
            KernelActionService(repo).prepare(
                action,
                kp_identity,
                KpOrchestrator(repo.connection, llm),
                source_model="large-model",
                profile="large",
            )
        )

        assert preview is not None
        assert preview.operator_id == prefix + "hire-courier"
        assert llm.responses == []
        assert preview.run_version == 1
        assert proposal["source_model"] == "kernel:large-model"
        overlays = repo.list_scenario_contract_overlays(run["id"])
        assert len(overlays) == 1 and overlays[0]["status"] == "active"
        assert repo.get_scenario_run_state(run["id"])["state_version"] == 1
        assert ConsequenceSignalService(repo).campaign_view(
            campaign["id"], audience="table"
        )["signals"] == []
        committed = KernelActionService(repo).commit(
            proposal, outcome="success", identity=kp_identity
        )
        assert committed is not None
        visible = ConsequenceSignalService(repo).campaign_view(
            campaign["id"], audience="table"
        )["signals"]
        assert visible[0]["title"] == "Courier errand"
        assert visible[0]["label"] == "The courier has departed"

        stale_action = TurnService(repo).submit_player_action(
            player_identity,
            action_text="I hire a second courier while the run is being paused.",
            client_action_id="large-overlay-stale-run",
        )
        stale_prefix = f"expansion.{stale_action['id']}."
        stale_llm = SequencedExpansionLlm(
            [
                {
                    "kind": "action",
                    "goal": "Have one additional bounded errand begin.",
                    "method": "Arrange another courier.",
                    "target_entity_ids": [],
                    "dialogue": "",
                    "steps": [],
                    "time_span": "",
                    "ambiguity": None,
                    "confidence": "high",
                },
                {
                    "kind": "clarification",
                    "candidate_id": None,
                    "requested_skill_key": None,
                    "confidence": "high",
                    "clarification": "No existing method matches.",
                },
                {
                    "goal": "Have one additional bounded errand begin.",
                    "steps": [{
                        "step_id": "hire",
                        "operator_id": stale_prefix + "hire-second-courier",
                        "goal": "Begin the additional errand.",
                        "method": "Arrange another courier.",
                        "target": "The requested records.",
                        "requirements": [{
                            "requirement_id": "active_run",
                            "category": "execution_context",
                            "description": "The scenario run is active.",
                            "source": "state",
                            "state_path": "status",
                            "comparison": "eq",
                            "expected_value": "active",
                        }],
                        "effects": [{
                            "effect_id": "elapsed",
                            "role": "cost",
                            "command_kind": "advance_clock",
                            "target_ref": "elapsed",
                            "delta": 1,
                            "description": "The errand consumes time.",
                        }, {
                            "effect_id": "hired",
                            "role": "progress",
                            "command_kind": "set_fact",
                            "target_ref": stale_prefix + "courier_hired",
                            "value": True,
                            "description": "One bounded errand begins.",
                        }],
                    }],
                },
                {
                    "verdict": "accept",
                    "reason": "The bounded delegation plan is complete.",
                    "issues": [],
                    "questions": [],
                },
                {
                    "confidence": "high",
                    "assumptions": ["A second courier is available."],
                    "rationale": "Bounded stale-run regression candidate.",
                    "records": {
                        "operators": [
                            {
                                "operator_id": stale_prefix + "hire-second-courier",
                                "title": "Hire a second courier",
                                "policy": "automatic",
                                "always_commands": [
                                    {
                                        "kind": "advance_clock",
                                        "clock_id": "elapsed",
                                        "delta": 1,
                                    }
                                ],
                                "success_commands": [
                                    {
                                        "kind": "set_fact",
                                        "path": stale_prefix + "courier_hired",
                                        "value": True,
                                    }
                                ],
                                "maximum_effect": "One additional bounded errand begins.",
                            }
                        ]
                    },
                },
            ]
        )
        get_active = repo.get_active_campaign_module_run
        active_lookups = 0

        def run_disappears_after_model_call(campaign_id: str):
            nonlocal active_lookups
            active_lookups += 1
            return get_active(campaign_id) if active_lookups == 1 else None

        repo.get_active_campaign_module_run = run_disappears_after_model_call  # type: ignore[method-assign]
        stale_proposal, stale_preview = asyncio.run(
            KernelActionService(repo).prepare(
                stale_action,
                kp_identity,
                KpOrchestrator(repo.connection, stale_llm),
                source_model="large-model",
                profile="large",
            )
        )

        assert stale_preview is None
        assert len(repo.list_scenario_contract_overlays(run["id"])) == 1
        payload = KernelActionService.kernel_payload(stale_proposal)
        assert payload is not None
        assert payload["selection"]["selection"]["kind"] == "clarification"
        assert "运行状态已变化" in payload["selection"]["selection"]["clarification"]

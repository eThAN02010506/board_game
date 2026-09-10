import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.application.gameplay_service import GameplayService
from ai_kp.core.config import Settings
from tests.support_investigators import (
    coc7_sheet,
    confirm_current_session_zero_sync,
    create_approved_player_sync,
)


class FakeEncounterIntentLlm:
    def __init__(self, responses: list[dict | str]):
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, _messages: object, temperature: float = 0.7) -> str:
        self.calls += 1
        response = self.responses.pop(0)
        return response if isinstance(response, str) else json.dumps(response, ensure_ascii=False)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _setup(
    client: TestClient, *, canonical_growth_mark: bool = True
) -> tuple[dict, dict, dict[str, str], dict]:
    campaign = client.post(
        "/campaigns",
        headers={"X-AI-KP-Admin-Token": "gameplay-test-admin"},
        json={"title": "CoC7 state machine real case", "system": "coc7"},
    ).json()
    session = client.post(
        f"/campaigns/{campaign['id']}/sessions",
        headers={"X-AI-KP-Admin-Token": "gameplay-test-admin"},
        json={"kp_display_name": "Keeper"},
    ).json()
    kp_headers = _bearer(session["access_token"])
    sheet = coc7_sheet(
        "林若川",
        skills={"侦查": 55, "图书馆使用": 50},
        characteristics={"con": 50, "siz": 50, "int": 70, "pow": 60, "dex": 65},
    )
    sheet["skills"][0]["growth_mark"] = canonical_growth_mark
    player = create_approved_player_sync(
        client,
        campaign=campaign,
        session=session,
        kp_headers=kp_headers,
        display_name="Player",
        sheet=sheet,
    )
    return campaign, session, kp_headers, player


def _activate_human_kp_mode(
    client: TestClient,
    *,
    campaign_id: str,
    kp_headers: dict[str, str],
    player_headers: dict[str, str],
) -> None:
    """Keep manual encounter tests independent from the Full-AI worker clock."""

    view = client.get(
        f"/campaigns/{campaign_id}/session-zero", headers=kp_headers
    ).json()
    revision = view["revision"]
    config = {**revision["config"], "hosting_mode": "human_kp"}
    updated = client.put(
        f"/campaigns/{campaign_id}/session-zero/config",
        headers=kp_headers,
        json={"expected_version": revision["version"], **config},
    )
    assert updated.status_code == 200, updated.text
    confirm_current_session_zero_sync(
        client,
        campaign_id=campaign_id,
        member_headers=(kp_headers, player_headers),
    )


def test_combat_damage_is_atomic_idempotent_and_player_projected() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        app = create_app(
            Settings(
                db_path=Path(tmpdir) / "gameplay.sqlite3",
                admin_token="gameplay-test-admin",
                local_admin_enabled=False,
            )
        )
        with TestClient(app) as client:
            campaign, _session, kp_headers, player = _setup(client)
            _activate_human_kp_mode(
                client,
                campaign_id=campaign["id"],
                kp_headers=kp_headers,
                player_headers=player["headers"],
            )
            investigator_id = player["investigator"]["id"]
            created = client.post(
                f"/campaigns/{campaign['id']}/coc7/encounters",
                headers=kp_headers,
                json={
                    "kind": "combat",
                    "title": "车厢内的袭击",
                    "participants": [
                        {
                            "participant_id": "pc",
                            "name": "林若川",
                            "investigator_id": investigator_id,
                        },
                        {
                            "participant_id": "attacker",
                            "name": "黑影",
                            "dex": 70,
                            "max_hp": 12,
                            "current_hp": 12,
                        },
                    ],
                },
            ).json()
            player_view = client.get(
                f"/coc7/encounters/{created['id']}",
                headers=player["headers"],
            )
            assert player_view.status_code == 200
            npc = next(
                item
                for item in player_view.json()["state"]["participants"]
                if item["participant_id"] == "attacker"
            )
            assert "current_hp" not in npc
            assert "conditions" not in npc

            out_of_turn = client.post(
                f"/coc7/encounters/{created['id']}/commands",
                headers=kp_headers,
                json={
                    "command_id": "combat-out-of-turn-0001",
                    "expected_version": 0,
                    "command_type": "melee",
                    "payload": {
                        "attacker_id": "pc",
                        "target_id": "attacker",
                        "attacker_target": 60,
                        "attacker_roll": 30,
                        "defender_target": 50,
                        "defender_roll": 70,
                        "defense": "fight_back",
                        "damage": 3,
                    },
                },
            )
            assert out_of_turn.status_code == 409

            command = {
                "command_id": "combat-damage-0001",
                "expected_version": 0,
                "command_type": "damage",
                "payload": {"target_id": "pc", "damage": 6},
            }
            applied = client.post(
                f"/coc7/encounters/{created['id']}/commands",
                headers=kp_headers,
                json=command,
            )
            assert applied.status_code == 200, applied.text
            assert applied.json()["linked_character_transition"]["state"]["current_hp"] == 4
            assert any(
                item["type"] == "major_wound"
                for item in applied.json()["linked_character_transition"]["state"][
                    "conditions"
                ]
            )

            replay = client.post(
                f"/coc7/encounters/{created['id']}/commands",
                headers=kp_headers,
                json=command,
            )
            assert replay.status_code == 200
            assert replay.json()["idempotent_replay"]
            state = client.get(
                f"/campaigns/{campaign['id']}/investigators/"
                f"{investigator_id}/coc7/state",
                headers=kp_headers,
            ).json()
            assert state["state"]["current_hp"] == 4
            assert len(state["events"]) == 1


def test_player_encounter_action_requires_preview_and_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        app = create_app(
            Settings(
                db_path=Path(tmpdir) / "player-encounter-action.sqlite3",
                admin_token="gameplay-test-admin",
                local_admin_enabled=False,
                llm_model="fake-encounter-intent",
            )
        )
        with TestClient(app) as client:
            campaign, _session, kp_headers, player = _setup(client)
            confirm_current_session_zero_sync(
                client,
                campaign_id=campaign["id"],
                member_headers=(kp_headers, player["headers"]),
            )
            encounter = client.post(
                f"/campaigns/{campaign['id']}/coc7/encounters",
                headers=kp_headers,
                json={
                    "kind": "combat",
                    "title": "Player-owned encounter turn",
                    "participants": [
                        {
                            "participant_id": "player-actor",
                            "name": "林若川",
                            "investigator_id": player["investigator"]["id"],
                            "dex": 80,
                            "action_profiles": [
                                {
                                    "action_key": "unarmed",
                                    "label": "徒手格斗",
                                    "kind": "melee",
                                    "skill_target": 60,
                                    "damage_expression": "1d3+1",
                                }
                            ],
                        },
                        {
                            "participant_id": "threat",
                            "name": "公开威胁",
                            "dex": 20,
                            "max_hp": 8,
                            "current_hp": 8,
                            "dodge_target": 30,
                        },
                    ],
                },
            ).json()

            direct_bypass = client.post(
                f"/coc7/encounters/{encounter['id']}/commands",
                headers=player["headers"],
                json={
                    "command_id": "direct-player-bypass",
                    "expected_version": 0,
                    "command_type": "take_turn",
                    "payload": {
                        "participant_id": "player-actor",
                        "inner": {"action_type": "pass"},
                    },
                },
            )
            assert direct_bypass.status_code == 403

            options = client.get(
                f"/coc7/encounters/{encounter['id']}/action-options",
                headers=player["headers"],
            )
            assert options.status_code == 200, options.text
            assert {item["action_key"] for item in options.json()["options"]} >= {
                "unarmed",
                "defend",
                "end_turn",
                "improvised",
            }
            assert options.json()["targets"] == [
                {"participant_id": "threat", "name": "公开威胁"}
            ]
            assert "dodge_target" not in options.text
            assert "damage_expression" not in options.text

            improvised = client.post(
                f"/coc7/encounters/{encounter['id']}/action-previews",
                headers=player["headers"],
                json={
                    "client_action_id": "improvised-preview-0001",
                    "expected_encounter_version": 0,
                    "action_text": "我想踹翻桌子挡住对方视线。",
                    "action_key": "improvised",
                },
            ).json()
            assert improvised["status"] == "needs_attention"
            assert "追问" in improvised["preview"]["public_message"]
            fake_intent = FakeEncounterIntentLlm(
                [
                    {
                        "resolution": "select",
                        "action_key": "defend",
                        "target_id": None,
                        "maneuver_effect": None,
                        "public_message": "可将这个做法按本回合的防御姿态处理。",
                        "reason": "直接效果是避免暴露于下一次攻击。",
                    }
                ]
            )
            with patch(
                "ai_kp.api.main.OpenAICompatibleClient",
                return_value=fake_intent,
            ):
                proposed = client.post(
                    f"/encounter-action-requests/{improvised['id']}/agent-proposal",
                    headers=player["headers"],
                    json={"expected_version": improvised["version"]},
                )
            assert proposed.status_code == 200, proposed.text
            improvised = proposed.json()
            assert improvised["status"] == "awaiting_confirmation"
            assert improvised["action_key"] == "defend"
            assert improvised["preview"]["agent"] == {
                "prompt_version": "encounter-intent.v1",
                "source_model": "fake-encounter-intent",
                "resolution": "select",
            }
            assert fake_intent.calls == 1
            cancelled = client.post(
                f"/encounter-action-requests/{improvised['id']}/cancel",
                headers=player["headers"],
                json={"expected_version": improvised["version"]},
            )
            assert cancelled.status_code == 200

            malformed = client.post(
                f"/coc7/encounters/{encounter['id']}/action-previews",
                headers=player["headers"],
                json={
                    "client_action_id": "improvised-preview-0002",
                    "expected_encounter_version": 0,
                    "action_text": "我搞个花活。",
                    "action_key": "improvised",
                },
            ).json()
            broken_agent = FakeEncounterIntentLlm(["not-json", "still-not-json"])
            with patch(
                "ai_kp.api.main.OpenAICompatibleClient",
                return_value=broken_agent,
            ):
                clarified = client.post(
                    f"/encounter-action-requests/{malformed['id']}/agent-proposal",
                    headers=player["headers"],
                    json={"expected_version": malformed["version"]},
                )
            assert clarified.status_code == 200, clarified.text
            clarification = clarified.json()
            assert clarification["status"] == "needs_attention"
            assert "具体目标" in clarification["preview"]["public_message"]
            assert clarification["preview"]["agent"]["fallback"] is True
            assert broken_agent.calls == 2
            cancelled = client.post(
                f"/encounter-action-requests/{malformed['id']}/cancel",
                headers=player["headers"],
                json={"expected_version": clarification["version"]},
            )
            assert cancelled.status_code == 200

            preview = client.post(
                f"/coc7/encounters/{encounter['id']}/action-previews",
                headers=player["headers"],
                json={
                    "client_action_id": "melee-preview-0001",
                    "expected_encounter_version": 0,
                    "action_text": "我绕到侧面，用手肘攻击对方的肩部。",
                    "action_key": "unarmed",
                    "target_id": "threat",
                },
            )
            assert preview.status_code == 200, preview.text
            request = preview.json()
            assert request["status"] == "awaiting_confirmation"
            assert "roll" not in preview.text.lower()
            assert "dodge_target" not in preview.text

            original_command = GameplayService.command_encounter
            prepared_payloads: list[dict] = []

            def crash_after_random_checkpoint(
                service: GameplayService, encounter_id: str, identity: object, command: object
            ) -> dict:
                prepared_payloads.append(command.payload)  # type: ignore[attr-defined]
                raise RuntimeError("simulated crash after random evidence checkpoint")

            monkeypatch.setattr(
                GameplayService, "command_encounter", crash_after_random_checkpoint
            )
            with pytest.raises(RuntimeError, match="simulated crash"):
                client.post(
                    f"/encounter-action-requests/{request['id']}/confirm",
                    headers=player["headers"],
                    json={"expected_version": request["version"]},
                )

            def resume_with_checkpointed_evidence(
                service: GameplayService, encounter_id: str, identity: object, command: object
            ) -> dict:
                prepared_payloads.append(command.payload)  # type: ignore[attr-defined]
                return original_command(service, encounter_id, identity, command)  # type: ignore[arg-type]

            monkeypatch.setattr(
                GameplayService,
                "command_encounter",
                resume_with_checkpointed_evidence,
            )
            confirmed = client.post(
                f"/encounter-action-requests/{request['id']}/confirm",
                headers=player["headers"],
                json={"expected_version": request["version"]},
            )
            assert confirmed.status_code == 200, confirmed.text
            assert prepared_payloads[0] == prepared_payloads[1]
            committed = confirmed.json()
            assert committed["status"] == "committed"
            assert "prepared_command" not in committed
            assert committed["result"]["encounter"]["version"] == 1
            assert committed["result"]["encounter"]["state"]["turn_order"][
                committed["result"]["encounter"]["turn_index"]
            ] == "threat"
            assert "action_profiles" not in confirmed.text
            assert "dodge_target" not in confirmed.text

            replay = client.post(
                f"/encounter-action-requests/{request['id']}/confirm",
                headers=player["headers"],
                json={"expected_version": request["version"]},
            )
            assert replay.status_code == 200
            assert replay.json()["result"] == committed["result"]


def test_health_sanity_chase_and_growth_transitions_persist() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        app = create_app(
            Settings(
                db_path=Path(tmpdir) / "gameplay.sqlite3",
                admin_token="gameplay-test-admin",
                local_admin_enabled=False,
            )
        )
        with TestClient(app) as client:
            campaign, _session, kp_headers, player = _setup(client)
            _activate_human_kp_mode(
                client,
                campaign_id=campaign["id"],
                kp_headers=kp_headers,
                player_headers=player["headers"],
            )
            investigator_id = player["investigator"]["id"]
            character_url = (
                f"/campaigns/{campaign['id']}/investigators/"
                f"{investigator_id}/coc7/commands"
            )

            sanity = client.post(
                character_url,
                headers=kp_headers,
                json={
                    "command_id": "sanity-loss-0001",
                    "expected_version": 0,
                    "command_type": "sanity",
                    "payload": {
                        "sanity_roll": 90,
                        "success_loss": 0,
                        "failure_loss": 12,
                        "intelligence_roll": 40,
                        "bout_roll": 7,
                        "bout_duration": 4,
                    },
                },
            )
            assert sanity.status_code == 200, sanity.text
            san_result = sanity.json()
            assert san_result["state"]["current_san"] == 48
            assert san_result["event"]["result"]["temporary_insanity"]
            assert san_result["event"]["result"]["indefinite_insanity"]

            growth = client.post(
                character_url,
                headers=kp_headers,
                json={
                    "command_id": "development-0001",
                    "expected_version": 1,
                    "command_type": "development",
                    "payload": {
                        "skill_key": "test.1",
                        "development_roll": 68,
                        "increase_roll": 8,
                    },
                },
            )
            assert growth.status_code == 200, growth.text
            assert growth.json()["event"]["result"]["new_value"] == 63
            assert growth.json()["permanent_change"]["status"] == "proposed"
            repeated_growth = client.post(
                character_url,
                headers=kp_headers,
                json={
                    "command_id": "development-0002",
                    "expected_version": 2,
                    "command_type": "development",
                    "payload": {
                        "skill_key": "test.1",
                        "development_roll": 70,
                        "increase_roll": 4,
                    },
                },
            )
            assert repeated_growth.status_code == 409

            chase = client.post(
                f"/campaigns/{campaign['id']}/coc7/encounters",
                headers=kp_headers,
                json={
                    "kind": "chase",
                    "title": "列车顶追逐",
                    "participants": [
                        {
                            "participant_id": "runner",
                            "name": "逃亡者",
                            "dex": 70,
                            "move": 8,
                            "max_hp": 10,
                            "current_hp": 10,
                            "chase_role": "fleeing",
                            "con_success_level": "extreme",
                            "location_index": 1,
                        },
                        {
                            "participant_id": "pursuer",
                            "name": "追逐者",
                            "dex": 60,
                            "move": 7,
                            "max_hp": 12,
                            "current_hp": 12,
                            "chase_role": "pursuer",
                            "con_success_level": "regular",
                            "location_index": 0,
                        },
                    ],
                    "locations": [
                        {"label": "车尾"},
                        {"label": "湿滑车顶", "hazard": {"skill": "攀爬"}},
                        {"label": "车头"},
                    ],
                },
            )
            assert chase.status_code == 200, chase.text
            chase_state = chase.json()
            runner = next(
                item
                for item in chase_state["state"]["participants"]
                if item["participant_id"] == "runner"
            )
            assert runner["adjusted_move"] == 9
            assert runner["action_points"] == 3

            moved = client.post(
                f"/coc7/encounters/{chase_state['id']}/commands",
                headers=kp_headers,
                json={
                    "command_id": "chase-move-0001",
                    "expected_version": 0,
                    "command_type": "move",
                    "payload": {
                        "participant_id": "runner",
                        "steps": 1,
                        "direction": "forward",
                    },
                },
            )
            assert moved.status_code == 200, moved.text
            assert moved.json()["event"]["result"]["location_index"] == 2


def test_successful_check_marks_growth_and_override_removes_its_source() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        app = create_app(
            Settings(
                db_path=Path(tmpdir) / "growth-mark.sqlite3",
                admin_token="gameplay-test-admin",
                local_admin_enabled=False,
            )
        )
        with TestClient(app) as client:
            campaign, _session, kp_headers, player = _setup(
                client, canonical_growth_mark=False
            )
            check = client.post(
                f"/campaigns/{campaign['id']}/checks",
                headers=kp_headers,
                json={
                    "skill_name": "侦查",
                    "pc_id": player["pc"]["id"],
                },
            ).json()
            resolved = client.post(
                f"/checks/{check['id']}/resolve",
                headers=player["headers"],
                json={
                    "input_method": "physical",
                    "ones_digit": 0,
                    "tens_digits": [3],
                },
            )
            assert resolved.status_code == 200, resolved.text
            state_url = (
                f"/campaigns/{campaign['id']}/investigators/"
                f"{player['investigator']['id']}/coc7/state"
            )
            state = client.get(state_url, headers=kp_headers).json()["state"]
            mark = next(
                item
                for item in state["conditions"]
                if item["type"] == "skill_growth_mark"
            )
            assert mark["skill_key"] == "test.1"
            assert mark["source_check_ids"] == [check["id"]]

            overridden = client.post(
                f"/checks/{check['id']}/override",
                headers=kp_headers,
                json={
                    "success_level": "failure",
                    "passed": False,
                    "reason": "桌面复核后确认骰面读取错误",
                },
            )
            assert overridden.status_code == 200, overridden.text
            state = client.get(state_url, headers=kp_headers).json()["state"]
            assert all(
                item["type"] != "skill_growth_mark"
                for item in state["conditions"]
            )

            opposed = client.post(
                f"/campaigns/{campaign['id']}/opposed-checks",
                headers=kp_headers,
                json={
                    "left": {
                        "skill_name": "侦查",
                        "pc_id": player["pc"]["id"],
                    },
                    "right": {
                        "skill_name": "图书馆使用",
                        "pc_id": player["pc"]["id"],
                    },
                },
            ).json()
            for check_id, tens in (
                (opposed["left_check_id"], 3),
                (opposed["right_check_id"], 4),
            ):
                rolled = client.post(
                    f"/checks/{check_id}/resolve",
                    headers=player["headers"],
                    json={
                        "input_method": "physical",
                        "ones_digit": 0,
                        "tens_digits": [tens],
                    },
                )
                assert rolled.status_code == 200, rolled.text
            contest = client.post(
                f"/opposed-checks/{opposed['id']}/resolve",
                headers=kp_headers,
            )
            assert contest.status_code == 200, contest.text
            state = client.get(state_url, headers=kp_headers).json()["state"]
            marks = [
                item
                for item in state["conditions"]
                if item["type"] == "skill_growth_mark"
            ]
            assert [item["skill_key"] for item in marks] == ["test.1"]
            assert marks[0]["source_check_ids"] == [opposed["left_check_id"]]


def test_major_wound_natural_healing_is_persisted_and_period_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        app = create_app(
            Settings(
                db_path=Path(tmpdir) / "healing.sqlite3",
                admin_token="gameplay-test-admin",
                local_admin_enabled=False,
            )
        )
        with TestClient(app) as client:
            campaign, _session, kp_headers, player = _setup(client)
            investigator_id = player["investigator"]["id"]
            command_url = (
                f"/campaigns/{campaign['id']}/investigators/"
                f"{investigator_id}/coc7/commands"
            )
            damaged = client.post(
                command_url,
                headers=kp_headers,
                json={
                    "command_id": "healing-damage-0001",
                    "expected_version": 0,
                    "command_type": "damage",
                    "payload": {"damage": 6},
                },
            )
            assert damaged.status_code == 200, damaged.text
            healed = client.post(
                command_url,
                headers=kp_headers,
                json={
                    "command_id": "natural-healing-0001",
                    "expected_version": 1,
                    "command_type": "natural_healing",
                    "payload": {
                        "period_id": "1923-W08",
                        "con_success_level": "extreme",
                        "healing_rolls": [2, 3],
                    },
                },
            )
            assert healed.status_code == 200, healed.text
            assert healed.json()["state"]["current_hp"] == 9
            assert all(
                item["type"] != "major_wound"
                for item in healed.json()["state"]["conditions"]
            )
            repeated = client.post(
                command_url,
                headers=kp_headers,
                json={
                    "command_id": "natural-healing-0002",
                    "expected_version": 2,
                    "command_type": "natural_healing",
                    "payload": {
                        "period_id": "1923-W08",
                        "con_success_level": "regular",
                        "healing_rolls": [2],
                    },
                },
            )
            assert repeated.status_code == 409

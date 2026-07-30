import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings
from tests.support_investigators import (
    coc7_sheet,
    create_approved_player_sync,
)


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

import sqlite3
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.bootstrap.settings import Settings
from tests.support_investigators import (
    coc7_sheet,
    confirm_current_session_zero_sync,
    create_approved_player_sync,
)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _approve_spare(
    client: TestClient,
    *,
    campaign_id: str,
    player_headers: dict[str, str],
    kp_headers: dict[str, str],
) -> dict:
    created = client.post(
        "/investigators",
        headers=player_headers,
        json={"canonical_sheet": coc7_sheet("替补调查员"), "source_type": "manual"},
    )
    created.raise_for_status()
    investigator = created.json()
    submitted = client.post(
        f"/campaigns/{campaign_id}/investigators/{investigator['id']}/submit",
        headers=player_headers,
        json={"revision_id": investigator["current_revision_id"]},
    )
    submitted.raise_for_status()
    approved = client.post(
        f"/campaigns/{campaign_id}/investigators/{investigator['id']}/review",
        headers=kp_headers,
        json={"action": "approved", "comment": "备用角色审核"},
    )
    approved.raise_for_status()
    return investigator


def test_death_observer_replacement_leave_and_return_keep_history() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        app = create_app(
            Settings(
                db_path=Path(tmpdir) / "lifecycle.sqlite3",
                admin_token="lifecycle-admin",
                local_admin_enabled=False,
            )
        )
        with TestClient(app) as client:
            campaign = client.post(
                "/campaigns",
                headers={"X-AI-KP-Admin-Token": "lifecycle-admin"},
                json={"title": "角色生命周期", "system": "coc7"},
            ).json()
            session = client.post(
                f"/campaigns/{campaign['id']}/sessions",
                headers={"X-AI-KP-Admin-Token": "lifecycle-admin"},
                json={"kp_display_name": "KP"},
            ).json()
            kp_headers = _bearer(session["access_token"])
            player = create_approved_player_sync(
                client,
                campaign=campaign,
                session=session,
                kp_headers=kp_headers,
                display_name="玩家",
                sheet=coc7_sheet("原调查员"),
            )
            spare = _approve_spare(
                client,
                campaign_id=campaign["id"],
                player_headers=player["headers"],
                kp_headers=kp_headers,
            )
            confirm_current_session_zero_sync(
                client,
                campaign_id=campaign["id"],
                member_headers=(kp_headers, player["headers"]),
            )
            investigator_id = player["investigator"]["id"]
            state = client.get(
                f"/campaigns/{campaign['id']}/investigators/{investigator_id}/coc7/state",
                headers=kp_headers,
            ).json()["state"]
            killed = client.post(
                f"/campaigns/{campaign['id']}/investigators/{investigator_id}/coc7/commands",
                headers=kp_headers,
                json={
                    "command_id": "fatal-damage-event-0001",
                    "expected_version": state["state_version"],
                    "command_type": "damage",
                    "payload": {"damage": 10},
                    "visibility": "table",
                },
            )
            assert killed.status_code == 200, killed.text
            assert killed.json()["lifecycle_transition"]["lifecycle"]["state"] == "dead"

            blocked_action = client.post(
                f"/campaigns/{campaign['id']}/actions",
                headers=player["headers"],
                json={"action_text": "已经死亡的调查员仍尝试行动。"},
            )
            assert blocked_action.status_code == 409, blocked_action.text
            assert "dead" in blocked_action.text

            view = client.get(
                f"/campaigns/{campaign['id']}/character-lifecycle",
                headers=player["headers"],
            ).json()
            assert view["capabilities"]["resurrection"] is False
            assert next(
                row for row in view["characters"] if row["investigator_id"] == investigator_id
            )["state"] == "dead"
            resurrection = client.post(
                f"/campaigns/{campaign['id']}/character-lifecycle/requests",
                headers=kp_headers,
                json={
                    "member_id": player["bundle"]["member"]["id"],
                    "action": "resurrect",
                    "reason": "规则检查",
                },
            )
            assert resurrection.status_code == 409

            observe_request = client.post(
                f"/campaigns/{campaign['id']}/character-lifecycle/requests",
                headers=kp_headers,
                json={
                    "member_id": player["bundle"]["member"]["id"],
                    "action": "observe",
                    "reason": "角色死亡后继续观战",
                },
            ).json()
            observed = client.post(
                f"/character-lifecycle/requests/{observe_request['id']}/decision",
                headers=player["headers"],
                json={
                    "action": "accept",
                    "reason": "我选择先观战",
                    "expected_version": observe_request["version"],
                },
            )
            assert observed.status_code == 200, observed.text
            assert observed.json()["member"]["role"] == "observer"
            assert observed.json()["member"]["pc_id"] is None

            replace_request = client.post(
                f"/campaigns/{campaign['id']}/character-lifecycle/requests",
                headers=kp_headers,
                json={
                    "member_id": player["bundle"]["member"]["id"],
                    "action": "replace",
                    "reason": "以备用角色继续 Campaign",
                    "replacement_investigator_id": spare["id"],
                },
            ).json()
            replaced = client.post(
                f"/character-lifecycle/requests/{replace_request['id']}/decision",
                headers=player["headers"],
                json={
                    "action": "accept",
                    "reason": "确认换角",
                    "expected_version": replace_request["version"],
                },
            )
            assert replaced.status_code == 200, replaced.text
            assert replaced.json()["member"]["role"] == "player"
            assert replaced.json()["presence"]["investigator_id"] == spare["id"]

            leave_request = client.post(
                f"/campaigns/{campaign['id']}/character-lifecycle/requests",
                headers=kp_headers,
                json={
                    "member_id": player["bundle"]["member"]["id"],
                    "action": "temporary_leave",
                    "reason": "玩家本周缺席",
                },
            ).json()
            left = client.post(
                f"/character-lifecycle/requests/{leave_request['id']}/decision",
                headers=kp_headers,
                json={
                    "action": "accept",
                    "reason": "按预先约定暂离",
                    "expected_version": leave_request["version"],
                },
            )
            assert left.status_code == 200, left.text
            assert left.json()["presence"]["state"] == "temporarily_absent"

            return_request = client.post(
                f"/campaigns/{campaign['id']}/character-lifecycle/requests",
                headers=kp_headers,
                json={
                    "member_id": player["bundle"]["member"]["id"],
                    "action": "return",
                    "reason": "玩家回归",
                    "replacement_investigator_id": spare["id"],
                },
            ).json()
            returned = client.post(
                f"/character-lifecycle/requests/{return_request['id']}/decision",
                headers=player["headers"],
                json={
                    "action": "accept",
                    "reason": "继续扮演备用角色",
                    "expected_version": return_request["version"],
                },
            )
            assert returned.status_code == 200, returned.text
            assert returned.json()["presence"]["state"] == "active"
            assert returned.json()["member"]["role"] == "player"

            final_view = client.get(
                f"/campaigns/{campaign['id']}/character-lifecycle",
                headers=kp_headers,
            ).json()
            actions = [event["action"] for event in final_view["events"]]
            assert actions == [
                "ruleset_state_sync",
                "observe",
                "replace",
                "temporary_leave",
                "return",
            ]
            assert next(
                row
                for row in final_view["characters"]
                if row["investigator_id"] == investigator_id
            )["state"] == "dead"
            ended = client.post(
                f"/campaigns/{campaign['id']}/session-end",
                headers=kp_headers,
                json={"client_end_id": "lifecycle-session-end-0001"},
            )
            assert ended.status_code == 200, ended.text
            projection = ended.json()["latest_snapshot"]["projection"]
            assert next(
                row
                for row in projection["character_lifecycles"]
                if row["investigator_id"] == investigator_id
            )["state"] == "dead"
            assert projection["member_presence"][0]["state"] == "active"


def test_player_cannot_accept_another_members_lifecycle_request() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        app = create_app(
            Settings(
                db_path=Path(tmpdir) / "lifecycle-security.sqlite3",
                admin_token="lifecycle-admin",
                local_admin_enabled=False,
            )
        )
        with TestClient(app) as client:
            campaign = client.post(
                "/campaigns",
                headers={"X-AI-KP-Admin-Token": "lifecycle-admin"},
                json={"title": "边界", "system": "coc7"},
            ).json()
            session = client.post(
                f"/campaigns/{campaign['id']}/sessions",
                headers={"X-AI-KP-Admin-Token": "lifecycle-admin"},
                json={"kp_display_name": "KP"},
            ).json()
            kp_headers = _bearer(session["access_token"])
            first = create_approved_player_sync(
                client,
                campaign=campaign,
                session=session,
                kp_headers=kp_headers,
                display_name="甲",
                sheet=coc7_sheet("甲"),
            )
            second = create_approved_player_sync(
                client,
                campaign=campaign,
                session=session,
                kp_headers=kp_headers,
                display_name="乙",
                sheet=coc7_sheet("乙"),
            )
            request = client.post(
                f"/campaigns/{campaign['id']}/character-lifecycle/requests",
                headers=kp_headers,
                json={
                    "member_id": first["bundle"]["member"]["id"],
                    "action": "observe",
                    "reason": "切换到观战",
                },
            ).json()
            forbidden = client.post(
                f"/character-lifecycle/requests/{request['id']}/decision",
                headers=second["headers"],
                json={
                    "action": "accept",
                    "reason": "越权尝试",
                    "expected_version": request["version"],
                },
            )
            assert forbidden.status_code == 403


def test_lifecycle_view_is_read_only_while_another_writer_holds_the_database() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        database = Path(tmpdir) / "lifecycle-read-only.sqlite3"
        app = create_app(
            Settings(
                db_path=database,
                admin_token="lifecycle-admin",
                local_admin_enabled=False,
            )
        )
        with TestClient(app) as client:
            campaign = client.post(
                "/campaigns",
                headers={"X-AI-KP-Admin-Token": "lifecycle-admin"},
                json={"title": "只读生命周期", "system": "coc7"},
            ).json()
            session = client.post(
                f"/campaigns/{campaign['id']}/sessions",
                headers={"X-AI-KP-Admin-Token": "lifecycle-admin"},
                json={"kp_display_name": "KP"},
            ).json()
            kp_headers = _bearer(session["access_token"])
            player = create_approved_player_sync(
                client,
                campaign=campaign,
                session=session,
                kp_headers=kp_headers,
                display_name="玩家",
                sheet=coc7_sheet("只读调查员"),
            )

            writer = sqlite3.connect(database, timeout=0.1)
            writer.row_factory = sqlite3.Row
            try:
                assert writer.execute(
                    "SELECT COUNT(*) FROM campaign_investigator_lifecycle"
                ).fetchone()[0] == 0
                assert writer.execute(
                    "SELECT COUNT(*) FROM campaign_member_presence"
                ).fetchone()[0] == 0
                writer.execute("BEGIN IMMEDIATE")

                response = client.get(
                    f"/campaigns/{campaign['id']}/character-lifecycle",
                    headers=player["headers"],
                )
                assert response.status_code == 200, response.text
                view = response.json()
                assert view["characters"][0]["state"] == "active"
                assert view["characters"][0]["version"] == 1
                assert view["presence"][0]["state"] == "active"
                assert view["presence"][0]["investigator_id"] == player["investigator"]["id"]
                assert writer.execute(
                    "SELECT COUNT(*) FROM campaign_investigator_lifecycle"
                ).fetchone()[0] == 0
                assert writer.execute(
                    "SELECT COUNT(*) FROM campaign_member_presence"
                ).fetchone()[0] == 0
            finally:
                writer.rollback()
                writer.close()

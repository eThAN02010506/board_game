import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from ai_kp.api.main import create_app
from ai_kp.core.config import Settings
from tests.support_investigators import coc7_sheet

ADMIN_HEADERS = {"X-AI-KP-Admin-Token": "timeline-test-admin"}


def _campaign_session(client: TestClient, title: str) -> tuple[dict, dict, dict[str, str]]:
    campaign = client.post(
        "/campaigns",
        headers=ADMIN_HEADERS,
        json={"title": title},
    ).json()
    session = client.post(
        f"/campaigns/{campaign['id']}/sessions",
        headers=ADMIN_HEADERS,
        json={"kp_display_name": f"{title} KP"},
    ).json()
    return campaign, session, {
        "Authorization": f"Bearer {session['access_token']}",
    }


def _join(
    client: TestClient,
    session: dict,
    profile: dict,
) -> tuple[dict, dict[str, str]]:
    joined = client.post(
        "/sessions/join",
        json={"join_code": session["join_code"], "display_name": "林默玩家"},
    ).json()
    return joined, {
        "Authorization": f"Bearer {joined['access_token']}",
        "X-AI-KP-Player-Token": profile["player_token"],
    }


def test_cross_campaign_timeline_branch_and_permanent_change_flow(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(
            db_path=tmp_path / "character-timeline.sqlite3",
            local_admin_enabled=False,
            admin_token="timeline-test-admin",
        )
    )
    with TestClient(app) as client:
        profile = client.post(
            "/player-profiles",
            json={"display_name": "稳定玩家"},
        ).json()
        profile_headers = {"X-AI-KP-Player-Token": profile["player_token"]}
        investigator = client.post(
            "/investigators",
            headers=profile_headers,
            json={
                "canonical_sheet": coc7_sheet(
                    "林默",
                    skills={"侦查": 55},
                    characteristics={"con": 60, "siz": 50},
                ),
                "source_type": "manual",
            },
        ).json()
        base_revision_id = investigator["current_revision_id"]

        campaign_a, session_a, kp_a = _campaign_session(client, "雾港")
        joined_a, player_a = _join(client, session_a, profile)
        submitted_a = client.post(
            f"/campaigns/{campaign_a['id']}/investigators/{investigator['id']}/submit",
            headers=player_a,
            json={"revision_id": base_revision_id},
        )
        assert submitted_a.status_code == 200
        assert submitted_a.json()["timeline_branch"]["is_primary"] is True
        approved_a = client.post(
            f"/campaigns/{campaign_a['id']}/investigators/{investigator['id']}/review",
            headers=kp_a,
            json={"action": "approved", "comment": "进入雾港"},
        )
        assert approved_a.status_code == 200
        approved_a_payload = approved_a.json()
        client.post(
            f"/sessions/{session_a['session']['id']}/members/"
            f"{joined_a['member']['id']}/assign-investigator",
            headers=kp_a,
            json={"investigator_id": investigator["id"]},
        ).raise_for_status()

        damaged = client.patch(
            f"/campaigns/{campaign_a['id']}/investigators/{investigator['id']}/state",
            headers=kp_a,
            json={"expected_version": 0, "current_hp": 3},
        )
        assert damaged.status_code == 200
        assert damaged.json()["current_hp"] == 3

        visible_event = client.post(
            f"/campaigns/{campaign_a['id']}/events",
            headers=kp_a,
            json={
                "actor_type": "pc",
                "actor_id": approved_a_payload["legacy_pc_id"],
                "event_type": "clue.discovered",
                "summary": "在旧码头找到新月货运账簿",
                "visibility": "table",
                "happened_at": "1924-10-03T21:10:00",
            },
        ).json()
        visible_memory = client.post(
            f"/campaigns/{campaign_a['id']}/memories",
            headers=kp_a,
            json={
                "text": "新月午夜有无登记货船停靠三号栈桥",
                "scope": "pc_major",
                "pc_id": approved_a_payload["legacy_pc_id"],
                "visibility": "player",
                "importance": 5,
                "source_event_id": visible_event["id"],
            },
        ).json()
        secret_event = client.post(
            f"/campaigns/{campaign_a['id']}/events",
            headers=kp_a,
            json={
                "actor_type": "system",
                "event_type": "keeper.note",
                "summary": "账簿由深潜者伪造",
                "visibility": "kp",
            },
        ).json()
        secret_memory = client.post(
            f"/campaigns/{campaign_a['id']}/memories",
            headers=kp_a,
            json={
                "text": "账簿真相尚未揭示",
                "scope": "pc_major",
                "pc_id": approved_a_payload["legacy_pc_id"],
                "visibility": "kp",
                "importance": 5,
                "source_event_id": secret_event["id"],
            },
        ).json()
        with sqlite3.connect(tmp_path / "character-timeline.sqlite3") as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executemany(
                "INSERT INTO npcs (id, name) VALUES (?, ?)",
                (("npc_visible", "陈伯"), ("npc_secret", "隐秘祭司")),
            )
            connection.executemany(
                """
                INSERT INTO investigator_npc_encounters
                  (id, investigator_id, npc_id, campaign_id, source_event_id,
                   interaction_summary)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        "encounter_visible",
                        investigator["id"],
                        "npc_visible",
                        campaign_a["id"],
                        visible_event["id"],
                        "一起核对了码头账簿",
                    ),
                    (
                        "encounter_secret",
                        investigator["id"],
                        "npc_secret",
                        campaign_a["id"],
                        secret_event["id"],
                        "KP 私密遭遇，不得跨团泄露",
                    ),
                ),
            )

        proposed = client.post(
            f"/campaigns/{campaign_a['id']}/investigators/"
            f"{investigator['id']}/permanent-changes",
            headers=kp_a,
            json={
                "kind": "scar",
                "summary": "左手留下深潜者爪痕",
                "change": {"text": "左手手背留有三道无法消退的爪痕"},
                "source_event_id": visible_event["id"],
                "rationale": "该伤痕会在未来模组持续存在",
            },
        )
        assert proposed.status_code == 200, proposed.text
        proposal = proposed.json()
        assert proposal["base_revision_id"] == base_revision_id
        before_accept = client.get(
            f"/investigators/{investigator['id']}",
            headers=profile_headers,
        ).json()
        assert before_accept["current_revision_id"] == base_revision_id

        accepted = client.post(
            f"/investigator-permanent-changes/{proposal['id']}/decision",
            headers=profile_headers,
            json={
                "action": "accepted",
                "reason": "确认继承这道伤痕",
                "expected_revision_id": base_revision_id,
            },
        )
        assert accepted.status_code == 200
        accepted_payload = accepted.json()
        milestone_revision_id = accepted_payload["resulting_revision_id"]
        assert milestone_revision_id != base_revision_id
        accepted_retry = client.post(
            f"/investigator-permanent-changes/{proposal['id']}/decision",
            headers=profile_headers,
            json={
                "action": "accepted",
                "reason": "确认继承这道伤痕",
                "expected_revision_id": base_revision_id,
            },
        )
        assert accepted_retry.status_code == 200
        assert accepted_retry.json()["resulting_revision_id"] == milestone_revision_id
        conflicting_retry = client.post(
            f"/investigator-permanent-changes/{proposal['id']}/decision",
            headers=profile_headers,
            json={
                "action": "rejected",
                "reason": "改变主意",
                "expected_revision_id": base_revision_id,
            },
        )
        assert conflicting_retry.status_code == 409

        campaign_b, session_b, kp_b = _campaign_session(client, "黑水镇")
        _, player_b = _join(client, session_b, profile)
        submitted_b = client.post(
            f"/campaigns/{campaign_b['id']}/investigators/{investigator['id']}/submit",
            headers=player_b,
            json={"revision_id": milestone_revision_id},
        )
        assert submitted_b.status_code == 200
        blocked_primary = client.post(
            f"/campaigns/{campaign_b['id']}/investigators/{investigator['id']}/review",
            headers=kp_b,
            json={"action": "approved", "comment": "尝试并行主时间线"},
        )
        assert blocked_primary.status_code == 409
        assert "active timeline participation" in blocked_primary.json()["detail"]

        branch = client.post(
            f"/investigators/{investigator['id']}/timeline-branches",
            headers=profile_headers,
            json={"label": "黑水镇平行线"},
        )
        assert branch.status_code == 200
        branch_id = branch.json()["id"]
        client.post(
            f"/campaigns/{campaign_b['id']}/investigators/{investigator['id']}/submit",
            headers=player_b,
            json={
                "revision_id": milestone_revision_id,
                "timeline_branch_id": branch_id,
            },
        ).raise_for_status()
        approved_b = client.post(
            f"/campaigns/{campaign_b['id']}/investigators/{investigator['id']}/review",
            headers=kp_b,
            json={"action": "approved", "comment": "明确使用平行线"},
        )
        assert approved_b.status_code == 200
        assert approved_b.json()["campaign_state"]["current_hp"] == 11
        assert "左手手背留有三道" in approved_b.json()["approved_revision"][
            "canonical_sheet"
        ]["background"]["injuries_scars"]

        owner_timeline = client.get(
            f"/investigators/{investigator['id']}/timeline",
            headers=profile_headers,
        )
        assert owner_timeline.status_code == 200
        owner_payload = owner_timeline.json()
        assert visible_memory["id"] in {item["id"] for item in owner_payload["memories"]}
        assert secret_memory["id"] not in {item["id"] for item in owner_payload["memories"]}
        assert proposal["id"] in {
            item["id"] for item in owner_payload["permanent_changes"]
        }
        assert {item["npc_name"] for item in owner_payload["npc_encounters"]} == {
            "陈伯"
        }
        assert all("rationale" not in item for item in owner_payload["permanent_changes"])

        inherited = client.get(
            f"/campaigns/{campaign_b['id']}/investigators/"
            f"{investigator['id']}/timeline",
            headers=kp_b,
        )
        assert inherited.status_code == 200
        serialized = json.dumps(inherited.json(), ensure_ascii=False)
        assert "新月午夜有无登记货船" in serialized
        assert "账簿真相尚未揭示" not in serialized
        assert "该伤痕会在未来模组持续存在" not in serialized

        other_profile = client.post(
            "/player-profiles",
            json={"display_name": "其他玩家"},
        ).json()
        denied = client.get(
            f"/investigators/{investigator['id']}/timeline",
            headers={"X-AI-KP-Player-Token": other_profile["player_token"]},
        )
        assert denied.status_code == 404

        closed = client.post(
            f"/sessions/{session_a['session']['id']}/close",
            headers=kp_a,
        )
        assert closed.status_code == 200
        refreshed = client.get(
            f"/investigators/{investigator['id']}/timeline",
            headers=profile_headers,
        ).json()
        primary = next(
            item for item in refreshed["branches"] if item["is_primary"]
        )
        assert next(
            item
            for item in refreshed["participations"]
            if item["branch_id"] == primary["id"]
        )["status"] == "completed"

"""Exercise the cross-campaign investigator timeline through live HTTP APIs."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

import httpx


def require(response: httpx.Response, status: int = 200) -> dict | list:
    if response.status_code != status:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} returned "
            f"{response.status_code}: {response.text[:1000]}"
        )
    return response.json()


def sheet(name: str) -> dict:
    return {
        "schema_version": "coc7-investigator-v1",
        "ruleset_id": "coc7-keeper-cn-2002c",
        "identity": {
            "name": name,
            "occupation": "记者",
            "age": 29,
            "era": "1920s",
        },
        "characteristics": {
            key: value
            for key, value in {
                "str": 50,
                "con": 60,
                "siz": 50,
                "dex": 55,
                "app": 50,
                "int": 65,
                "pow": 60,
                "edu": 70,
                "luck": 55,
            }.items()
        },
        "skills": [{
            "skill_key": "coc7.library_use",
            "display_name": "图书馆使用",
            "base_value": 20,
            "occupation_points": 40,
            "interest_points": 0,
            "development_points": 0,
        }],
        "assets": {"items": []},
        "background": {},
        "provenance": {"source_type": "realcase"},
    }


def create_campaign_session(
    client: httpx.Client,
    title: str,
) -> tuple[dict, dict, dict[str, str]]:
    campaign = require(client.post("/campaigns", json={"title": title}))
    session = require(
        client.post(
            f"/campaigns/{campaign['id']}/sessions",
            json={"kp_display_name": f"{title} KP"},
        )
    )
    return campaign, session, {
        "Authorization": f"Bearer {session['access_token']}",
    }


def join(
    client: httpx.Client,
    session: dict,
    player_token: str,
) -> tuple[dict, dict[str, str]]:
    joined = require(
        client.post(
            "/sessions/join",
            json={
                "join_code": session["join_code"],
                "display_name": "稳定玩家",
            },
        )
    )
    return joined, {
        "Authorization": f"Bearer {joined['access_token']}",
        "X-AI-KP-Player-Token": player_token,
    }


def main(base_url: str = "http://127.0.0.1:8002") -> None:
    suffix = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    with httpx.Client(base_url=base_url, timeout=60) as client:
        profile = require(
            client.post("/player-profiles", json={"display_name": f"时间线玩家 {suffix}"})
        )
        owner_headers = {"X-AI-KP-Player-Token": profile["player_token"]}
        investigator = require(
            client.post(
                "/investigators",
                headers=owner_headers,
                json={
                    "canonical_sheet": sheet(f"林默 {suffix}"),
                    "source_type": "manual",
                },
            )
        )
        base_revision_id = investigator["current_revision_id"]

        campaign_a, session_a, kp_a = create_campaign_session(
            client, f"雾港时间线验收 {suffix}"
        )
        joined_a, player_a = join(client, session_a, profile["player_token"])
        submitted_a = require(
            client.post(
                f"/campaigns/{campaign_a['id']}/investigators/"
                f"{investigator['id']}/submit",
                headers=player_a,
                json={"revision_id": base_revision_id},
            )
        )
        approved_a = require(
            client.post(
                f"/campaigns/{campaign_a['id']}/investigators/"
                f"{investigator['id']}/review",
                headers=kp_a,
                json={"action": "approved", "comment": "真实案例主时间线"},
            )
        )
        require(
            client.post(
                f"/sessions/{session_a['session']['id']}/members/"
                f"{joined_a['member']['id']}/assign-investigator",
                headers=kp_a,
                json={"investigator_id": investigator["id"]},
            )
        )
        require(
            client.patch(
                f"/campaigns/{campaign_a['id']}/investigators/"
                f"{investigator['id']}/state",
                headers=kp_a,
                json={"expected_version": 0, "current_hp": 2},
            )
        )
        source_event = require(
            client.post(
                f"/campaigns/{campaign_a['id']}/events",
                headers=kp_a,
                json={
                    "actor_type": "pc",
                    "actor_id": approved_a["legacy_pc_id"],
                    "event_type": "investigator.injured",
                    "summary": "林默从燃烧的档案室逃出，左掌留下永久烧伤",
                    "visibility": "table",
                    "happened_at": "1928-10-03T23:10:00",
                },
            )
        )
        proposal = require(
            client.post(
                f"/campaigns/{campaign_a['id']}/investigators/"
                f"{investigator['id']}/permanent-changes",
                headers=kp_a,
                json={
                    "kind": "scar",
                    "summary": "左掌留下永久烧伤",
                    "change": {"text": "左手掌留有档案室火灾造成的烧伤"},
                    "source_event_id": source_event["id"],
                    "rationale": "来源事件已在桌面公开并对后续故事持续有效",
                },
            )
        )
        accepted = require(
            client.post(
                f"/investigator-permanent-changes/{proposal['id']}/decision",
                headers=owner_headers,
                json={
                    "action": "accepted",
                    "reason": "确认这道伤痕属于角色长期经历",
                    "expected_revision_id": base_revision_id,
                },
            )
        )

        campaign_b, session_b, kp_b = create_campaign_session(
            client, f"黑水镇时间线验收 {suffix}"
        )
        _, player_b = join(client, session_b, profile["player_token"])
        milestone_revision_id = accepted["resulting_revision_id"]
        require(
            client.post(
                f"/campaigns/{campaign_b['id']}/investigators/"
                f"{investigator['id']}/submit",
                headers=player_b,
                json={"revision_id": milestone_revision_id},
            )
        )
        blocked = client.post(
            f"/campaigns/{campaign_b['id']}/investigators/"
            f"{investigator['id']}/review",
            headers=kp_b,
            json={"action": "approved", "comment": "应被活动主线冲突阻止"},
        )
        if blocked.status_code != 409:
            raise RuntimeError("A second active campaign was not blocked on the primary branch")

        branch = require(
            client.post(
                f"/investigators/{investigator['id']}/timeline-branches",
                headers=owner_headers,
                json={"label": "黑水镇平行线"},
            )
        )
        require(
            client.post(
                f"/campaigns/{campaign_b['id']}/investigators/"
                f"{investigator['id']}/submit",
                headers=player_b,
                json={
                    "revision_id": milestone_revision_id,
                    "timeline_branch_id": branch["id"],
                },
            )
        )
        approved_b = require(
            client.post(
                f"/campaigns/{campaign_b['id']}/investigators/"
                f"{investigator['id']}/review",
                headers=kp_b,
                json={"action": "approved", "comment": "明确平行时间线"},
            )
        )
        timeline = require(
            client.get(
                f"/investigators/{investigator['id']}/timeline",
                headers=owner_headers,
            )
        )
        if approved_b["campaign_state"]["current_hp"] != 11:
            raise RuntimeError("Temporary HP leaked into the second campaign")
        if "左手掌留有" not in json.dumps(
            approved_b["approved_revision"]["canonical_sheet"], ensure_ascii=False
        ):
            raise RuntimeError("Accepted permanent scar is absent from the milestone revision")
        if len(timeline["participations"]) != 2:
            raise RuntimeError("Expected two explicit campaign participations")

        print(
            json.dumps(
                {
                    "ok": True,
                    "investigator_id": investigator["id"],
                    "primary_branch_id": submitted_a["timeline_branch_id"],
                    "alternate_branch_id": branch["id"],
                    "milestone_revision_id": milestone_revision_id,
                    "campaign_a_runtime_hp": 2,
                    "campaign_b_initial_hp": approved_b["campaign_state"]["current_hp"],
                    "participation_count": len(timeline["participations"]),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8002")

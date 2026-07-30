"""Run the memory timeline acceptance flow against a live local backend."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

import httpx


def require(response: httpx.Response, status: int = 200) -> dict | list:
    if response.status_code != status:
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} returned "
            f"{response.status_code}: {response.text[:500]}"
        )
    return response.json()


def main(base_url: str = "http://127.0.0.1:8002") -> None:
    suffix = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    with httpx.Client(base_url=base_url, timeout=20) as client:
        campaign = require(
            client.post(
                "/campaigns",
                json={"title": f"记忆工作台验收 {suffix}", "current_time": "1924-10-03"},
            )
        )
        session = require(
            client.post(
                f"/campaigns/{campaign['id']}/sessions",
                json={"kp_display_name": "OldOnes"},
            )
        )
        kp_headers = {"Authorization": f"Bearer {session['access_token']}"}
        profile = require(
            client.post("/player-profiles", json={"display_name": f"验收玩家 {suffix}"})
        )
        joined = require(
            client.post(
                "/sessions/join",
                json={"join_code": session["join_code"], "display_name": "验收玩家"},
            )
        )
        player_headers = {
            "Authorization": f"Bearer {joined['access_token']}",
            "X-AI-KP-Player-Token": profile["player_token"],
        }
        investigator = require(
            client.post(
                "/investigators",
                headers=player_headers,
                json={
                    "source_type": "manual",
                    "canonical_sheet": {
                        "schema_version": "coc7-investigator-v1",
                        "ruleset_id": "coc7-keeper-cn-2002c",
                        "identity": {
                            "name": "林默",
                            "occupation": "记者",
                            "age": 31,
                            "era": "1920s",
                        },
                        "characteristics": {
                            "str": 50, "con": 50, "siz": 50, "dex": 50,
                            "app": 50, "int": 60, "pow": 50, "edu": 60, "luck": 50,
                        },
                        "skills": [],
                        "assets": {"items": []},
                        "background": {},
                        "provenance": {"source_type": "realcase"},
                    },
                },
            )
        )
        require(
            client.post(
                f"/campaigns/{campaign['id']}/investigators/{investigator['id']}/submit",
                headers=player_headers,
                json={"revision_id": investigator["current_revision_id"]},
            )
        )
        approved = require(
            client.post(
                f"/campaigns/{campaign['id']}/investigators/{investigator['id']}/review",
                headers=kp_headers,
                json={"action": "approved", "comment": "真实验收"},
            )
        )
        require(
            client.post(
                f"/sessions/{session['session']['id']}/members/"
                f"{joined['member']['id']}/assign-investigator",
                headers=kp_headers,
                json={"investigator_id": investigator["id"]},
            )
        )
        event = require(
            client.post(
                f"/campaigns/{campaign['id']}/events",
                headers=kp_headers,
                json={
                    "actor_type": "pc",
                    "actor_id": approved["legacy_pc_id"],
                    "event_type": "clue.discovered",
                    "summary": "在旧档案馆找到港口账簿",
                    "visibility": "table",
                    "happened_at": "1924-10-03T21:10:00",
                },
            )
        )
        memory = require(
            client.post(
                f"/campaigns/{campaign['id']}/memories",
                headers=kp_headers,
                json={
                    "text": "账簿记录了午夜运货",
                    "scope": "pc_side",
                    "pc_id": approved["legacy_pc_id"],
                    "visibility": "player",
                    "importance": 2,
                    "happened_at": "1924-10-03T21:10:00",
                    "source_event_id": event["id"],
                },
            )
        )
        player_items = require(
            client.get(
                f"/campaigns/{campaign['id']}/memory/timeline",
                headers=player_headers,
            )
        )
        assert player_items[0]["source_event_summary"] == "在旧档案馆找到港口账簿"
        first = require(
            client.post(
                f"/campaigns/{campaign['id']}/memories/{memory['id']}/curation",
                headers=kp_headers,
                json={
                    "classification": "clue",
                    "importance": 5,
                    "hidden": True,
                    "reason": "验收隐藏",
                    "expected_head_action_id": None,
                },
            )
        )
        stale = client.post(
            f"/campaigns/{campaign['id']}/memories/{memory['id']}/curation",
            headers=kp_headers,
            json={
                "classification": "side",
                "importance": 1,
                "hidden": False,
                "reason": "陈旧窗口",
                "expected_head_action_id": None,
            },
        )
        if stale.status_code != 409:
            raise RuntimeError(f"stale curation returned {stale.status_code}")
        hidden_items = require(
            client.get(
                f"/campaigns/{campaign['id']}/memory/timeline",
                headers=player_headers,
            )
        )
        assert memory["id"] not in {item["id"] for item in hidden_items}
        require(
            client.post(
                f"/campaigns/{campaign['id']}/memories/{memory['id']}/curation",
                headers=kp_headers,
                json={
                    "classification": "clue",
                    "importance": 5,
                    "hidden": False,
                    "reason": "验收恢复",
                    "expected_head_action_id": first["id"],
                },
            )
        )
        restored = require(
            client.get(
                f"/campaigns/{campaign['id']}/memory/timeline",
                headers=player_headers,
            )
        )
        item = next(entry for entry in restored if entry["id"] == memory["id"])
        assert item["classification"] == "clue"
        assert item["importance"] == 5
        assert "curation_head_id" not in item
        print(
            json.dumps(
                {
                    "ok": True,
                    "campaign_id": campaign["id"],
                    "investigator_id": investigator["id"],
                    "memory_id": memory["id"],
                    "source_event_id": event["id"],
                    "stale_status": stale.status_code,
                    "final_classification": item["classification"],
                    "final_importance": item["importance"],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8002")

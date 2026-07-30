"""Exercise the Scene Director through a running HTTP server.

The script creates disposable uniquely identified records in the target database. It never prints
join codes or bearer tokens.
"""

from __future__ import annotations

import argparse
import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    player_token: str | None = None,
    expected_status: int = 200,
    timeout_seconds: float = 15,
) -> Any:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if player_token:
        headers["X-AI-KP-Player-Token"] = player_token
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        ),
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = response.status
            body = response.read()
    except HTTPError as exc:
        status = exc.code
        body = exc.read()
    if status != expected_status:
        detail = body.decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{method} {path} returned {status}; expected {expected_status}: {detail}"
        )
    return json.loads(body) if body else None


def request_model_json(
    base_url: str,
    path: str,
    *,
    payload: dict[str, Any],
    token: str,
) -> Any:
    """Allow one bounded retry for local servers that emit an empty final once."""

    for attempt in range(2):
        try:
            return request_json(
                base_url,
                "POST",
                path,
                token=token,
                payload=payload,
                timeout_seconds=180,
            )
        except RuntimeError as exc:
            if attempt == 0 and "returned 502" in str(exc):
                continue
            raise
    raise AssertionError("unreachable")


def run(base_url: str, *, exercise_world_expansion: bool = False) -> None:
    campaign = request_json(
        base_url,
        "POST",
        "/campaigns",
        payload={"title": "Scene Director HTTP real-case"},
    )
    session = request_json(
        base_url,
        "POST",
        f"/campaigns/{campaign['id']}/sessions",
        payload={"kp_display_name": "OldOnes"},
    )
    kp_token = str(session["access_token"])
    player = request_json(
        base_url,
        "POST",
        "/sessions/join",
        payload={
            "join_code": session["join_code"],
            "display_name": "HTTP real-case player",
        },
    )
    module = request_json(
        base_url,
        "POST",
        f"/campaigns/{campaign['id']}/modules",
        token=kp_token,
        payload={
            "title": "雾港 HTTP 样例",
            "text": (
                "@visibility=kp @spoiler=act-1\n"
                "仓库照片指向灯塔航海日志。\n\n"
                "@visibility=secret @spoiler=ending\n"
                "地下祭坛藏在灯塔之下。"
            ),
        },
    )
    module_run = request_json(
        base_url,
        "POST",
        f"/campaigns/{campaign['id']}/module-runs",
        token=kp_token,
        payload={
            "module_id": module["id"],
            "active_spoiler_tags": ["act-1"],
        },
    )
    transition = request_json(
        base_url,
        "POST",
        f"/module-runs/{module_run['id']}/scene-transitions",
        token=kp_token,
        payload={
            "expected_version": module_run["version"],
            "scene_key": "warehouse-night",
            "scene_title": "旧仓库夜间调查",
            "play_pace": "freeform",
            "world_time": "1928-10-03 21:00",
        },
    )
    transitioned_run = transition["run"]
    request_json(
        base_url,
        "POST",
        f"/module-runs/{module_run['id']}/scene-transitions",
        token=kp_token,
        payload={
            "expected_version": module_run["version"],
            "scene_key": "stale",
            "scene_title": "过期写入",
            "play_pace": "freeform",
        },
        expected_status=409,
    )
    canon = request_json(
        base_url,
        "POST",
        f"/module-runs/{module_run['id']}/director/analyze",
        token=kp_token,
        payload={"player_intent": "检查仓库照片"},
    )
    spoiler = request_json(
        base_url,
        "POST",
        f"/module-runs/{module_run['id']}/director/analyze",
        token=kp_token,
        payload={"player_intent": "寻找地下祭坛"},
    )
    request_json(
        base_url,
        "GET",
        f"/module-runs/{module_run['id']}/director-state",
        token=str(player["access_token"]),
        expected_status=403,
    )
    state = request_json(
        base_url,
        "GET",
        f"/module-runs/{module_run['id']}/director-state",
        token=kp_token,
    )
    world_expansion = None
    approved_expansion = None
    materialized_expansion = None
    saved_map = None
    reappearance_result = None
    if exercise_world_expansion:
        profile_bundle = request_json(
            base_url,
            "POST",
            "/player-profiles",
            payload={"display_name": "Stable real-case player"},
        )
        stable_player_token = str(profile_bundle["player_token"])
        investigator = request_json(
            base_url,
            "POST",
            "/investigators",
            token=str(player["access_token"]),
            player_token=stable_player_token,
            payload={
                "source_type": "manual",
                "canonical_sheet": {
                    "schema_version": "coc7-investigator-v1",
                    "ruleset_id": "coc7-keeper-cn-2002c",
                    "identity": {
                        "name": "林若川",
                        "occupation": "记者",
                        "age": 30,
                        "era": "1920s",
                    },
                    "characteristics": {
                        "str": 50,
                        "con": 50,
                        "siz": 50,
                        "dex": 50,
                        "app": 50,
                        "int": 50,
                        "pow": 50,
                        "edu": 50,
                        "luck": 50,
                    },
                    "skills": [],
                    "assets": {"items": []},
                    "background": {},
                    "provenance": {"source_type": "realcase"},
                },
            },
        )
        request_json(
            base_url,
            "POST",
            f"/campaigns/{campaign['id']}/investigators/{investigator['id']}/submit",
            token=str(player["access_token"]),
            player_token=stable_player_token,
            payload={"revision_id": investigator["current_revision_id"]},
        )
        request_json(
            base_url,
            "POST",
            f"/campaigns/{campaign['id']}/investigators/{investigator['id']}/review",
            token=kp_token,
            payload={"action": "approved", "comment": "真实案例批准"},
        )
        world_expansion = request_model_json(
            base_url,
            f"/module-runs/{module_run['id']}/director/world-expansion-proposals",
            token=kp_token,
            payload={"player_intent": "我去寻找镇上的警察局"},
        )
        approved_expansion = request_json(
            base_url,
            "POST",
            f"/kp/proposals/{world_expansion['id']}/approve",
            token=kp_token,
            payload={"note": "HTTP 真实模型测试批准"},
        )
        saved_map = request_json(
            base_url,
            "POST",
            f"/campaigns/{campaign['id']}/maps/generate",
            token=kp_token,
            payload={
                "title": "雾港镇调查图",
                "prompt": "1928 年新英格兰沿海小镇的已审核调查地图",
                "locations": ["镇中心", "钟楼"],
                "routes": [["镇中心", "钟楼"]],
                "map_kind": "regional",
                "era_year": 1928,
                "locale": "新英格兰",
            },
        )
        encounter_payload = {
            "idempotency_key": f"contact:{world_expansion['id']}",
            "summary": "调查员抵达镇中心的治安官办公室，并与值班治安官交谈。",
            "happened_at": "1928-10-03 22:15",
            "facts": [
                {
                    "fact_type": "canonical_fact",
                    "subject": "雾港镇警务设施",
                    "predicate": "实际存在",
                    "object_text": "镇中心有一间由本地治安官使用的小型办公室。",
                }
            ],
            "npc": {
                "name": "艾萨克·霍尔",
                "profession": "治安官",
                "home_location": "雾港镇",
                "public_notes": "负责本镇日常治安。",
            },
            "map_placement": {
                "map_id": saved_map["id"],
                "location_name": "镇中心",
                "visibility": "table",
                "color": "#b93f2d",
            },
            "participant_investigator_ids": [investigator["id"]],
            "interaction_summary": "林若川与治安官一起核对了镇上的失踪人口记录。",
        }
        materialized_expansion = request_json(
            base_url,
            "POST",
            f"/kp/proposals/{world_expansion['id']}/world-expansion-encounters",
            token=kp_token,
            payload=encounter_payload,
        )
        idempotent_retry = request_json(
            base_url,
            "POST",
            f"/kp/proposals/{world_expansion['id']}/world-expansion-encounters",
            token=kp_token,
            payload=encounter_payload,
        )
        map_after_contact = request_json(
            base_url,
            "GET",
            f"/maps/{saved_map['id']}",
            token=kp_token,
        )
        known_npc_id = materialized_expansion[
            "world_expansion_materialization"
        ]["npc_id"]
        request_json(
            base_url,
            "PUT",
            f"/campaigns/{campaign['id']}/npcs/{known_npc_id}/availability",
            token=kp_token,
            payload={
                "lifecycle_state": "active",
                "born_year": 1880,
                "died_year": 1950,
                "active_from_year": 1910,
                "active_until_year": 1940,
                "location_tags": ["雾港镇"],
                "profession_tags": ["治安官", "警务人员"],
                "kp_notes": "真实 HTTP 门控测试档案",
            },
        )
        second_campaign = request_json(
            base_url,
            "POST",
            "/campaigns",
            payload={
                "title": "NPC reappearance HTTP real-case",
                "current_time": "1929-02-08 09:00",
            },
        )
        second_session = request_json(
            base_url,
            "POST",
            f"/campaigns/{second_campaign['id']}/sessions",
            payload={"kp_display_name": "OldOnes"},
        )
        second_kp_token = str(second_session["access_token"])
        second_player = request_json(
            base_url,
            "POST",
            "/sessions/join",
            payload={
                "join_code": second_session["join_code"],
                "display_name": "Stable real-case player",
            },
        )
        request_json(
            base_url,
            "POST",
            f"/campaigns/{second_campaign['id']}/investigators/"
            f"{investigator['id']}/submit",
            token=str(second_player["access_token"]),
            player_token=stable_player_token,
            payload={"revision_id": investigator["current_revision_id"]},
        )
        request_json(
            base_url,
            "POST",
            f"/campaigns/{second_campaign['id']}/investigators/"
            f"{investigator['id']}/review",
            token=second_kp_token,
            payload={"action": "approved", "comment": "跨本复现测试批准"},
        )
        request_json(
            base_url,
            "PUT",
            f"/campaigns/{second_campaign['id']}/npc-reappearance-policy",
            token=second_kp_token,
            payload={
                "max_returning_npcs": 1,
                "require_location_match": True,
                "require_profession_match": True,
                "max_travel_minutes": 120,
            },
        )
        origin_location = request_json(
            base_url,
            "POST",
            f"/campaigns/{second_campaign['id']}/travel-locations",
            token=second_kp_token,
            payload={
                "name": "雾港镇",
                "aliases": ["治安官辖区"],
                "source_kind": "manual",
                "kp_notes": "旧识当前可从这里出发",
            },
        )
        interchange_location = request_json(
            base_url,
            "POST",
            f"/campaigns/{second_campaign['id']}/travel-locations",
            token=second_kp_token,
            payload={
                "name": "中央车站",
                "aliases": [],
                "source_kind": "manual",
                "kp_notes": "",
            },
        )
        destination_location = request_json(
            base_url,
            "POST",
            f"/campaigns/{second_campaign['id']}/travel-locations",
            token=second_kp_token,
            payload={
                "name": "车站",
                "aliases": ["第二座小镇车站"],
                "source_kind": "manual",
                "kp_notes": "",
            },
        )
        for route_payload in (
            {
                "from_location_id": origin_location["id"],
                "to_location_id": interchange_location["id"],
                "travel_minutes": 30,
                "travel_mode": "drive",
                "bidirectional": True,
                "status": "open",
                "kp_notes": "",
            },
            {
                "from_location_id": interchange_location["id"],
                "to_location_id": destination_location["id"],
                "travel_minutes": 40,
                "travel_mode": "rail",
                "bidirectional": True,
                "status": "open",
                "kp_notes": "",
            },
        ):
            request_json(
                base_url,
                "POST",
                f"/campaigns/{second_campaign['id']}/travel-routes",
                token=second_kp_token,
                payload=route_payload,
            )
        route_preview = request_json(
            base_url,
            "POST",
            f"/campaigns/{second_campaign['id']}/travel-route-preview",
            token=second_kp_token,
            payload={
                "origins": ["雾港镇"],
                "destination": "第二座小镇车站",
                "max_minutes": 120,
            },
        )
        assert route_preview["status"] == "reachable"
        assert route_preview["total_minutes"] == 70
        over_limit_preview = request_json(
            base_url,
            "POST",
            f"/campaigns/{second_campaign['id']}/travel-route-preview",
            token=second_kp_token,
            payload={
                "origins": ["治安官辖区"],
                "destination": "车站",
                "max_minutes": 60,
            },
        )
        assert over_limit_preview["status"] == "over_limit"
        reappearance_candidates = request_json(
            base_url,
            "GET",
            f"/campaigns/{second_campaign['id']}/npc-reappearance-candidates",
            token=second_kp_token,
        )
        assert [item["npc_id"] for item in reappearance_candidates] == [
            materialized_expansion["world_expansion_materialization"]["npc_id"]
        ]
        assert reappearance_candidates[0]["appearance_gate"]["decision"] == (
            "needs_review"
        )
        second_module = request_json(
            base_url,
            "POST",
            f"/campaigns/{second_campaign['id']}/modules",
            token=second_kp_token,
            payload={
                "title": "第二团样例",
                "text": (
                    "@visibility=kp @spoiler=act-1\n"
                    "本只描述钟楼，没有说明警务人员。"
                ),
            },
        )
        second_run = request_json(
            base_url,
            "POST",
            f"/campaigns/{second_campaign['id']}/module-runs",
            token=second_kp_token,
            payload={
                "module_id": second_module["id"],
                "current_scene_key": "town",
                "active_spoiler_tags": ["act-1"],
            },
        )
        second_proposal = request_model_json(
            base_url,
            f"/module-runs/{second_run['id']}/director/world-expansion-proposals",
            token=second_kp_token,
            payload={"player_intent": "我去寻找以前认识的治安官"},
        )
        request_json(
            base_url,
            "POST",
            f"/kp/proposals/{second_proposal['id']}/approve",
            token=second_kp_token,
            payload={"note": "批准旧识合理复现"},
        )
        second_map = request_json(
            base_url,
            "POST",
            f"/campaigns/{second_campaign['id']}/maps/generate",
            token=second_kp_token,
            payload={
                "title": "第二座小镇",
                "prompt": "1928 年新英格兰小镇",
                "locations": ["车站"],
                "routes": [],
                "map_kind": "regional",
                "era_year": 1928,
                "locale": "新英格兰",
            },
        )
        reappearance_result = request_json(
            base_url,
            "POST",
            f"/kp/proposals/{second_proposal['id']}/world-expansion-encounters",
            token=second_kp_token,
            payload={
                "idempotency_key": f"reappearance:{second_proposal['id']}",
                "summary": "林若川在车站再次遇见以前认识的治安官。",
                "happened_at": "1929-02-08 10:00",
                "facts": [
                    {
                        "fact_type": "canonical_fact",
                        "subject": "旧识治安官",
                        "predicate": "当前位于",
                        "object_text": "第二座小镇的车站",
                    }
                ],
                "npc": {"npc_id": known_npc_id, "role": "reappeared"},
                "map_placement": {
                    "map_id": second_map["id"],
                    "location_name": "车站",
                    "visibility": "table",
                    "color": "#b93f2d",
                },
                "participant_investigator_ids": [investigator["id"]],
                "interaction_summary": "重逢后一起讨论了另一宗失踪案。",
                "profession_context": "治安官",
            },
        )
        assert request_json(
            base_url,
            "GET",
            f"/campaigns/{second_campaign['id']}/npc-reappearance-candidates",
            token=second_kp_token,
        ) == []

    assert transitioned_run["version"] == module_run["version"] + 1
    assert canon["decision"] == "answer_from_canon"
    assert canon["writes_performed"] is False
    assert spoiler["decision"] == "blocked_by_spoiler"
    assert spoiler["sources"] == []
    assert state["run"]["version"] == transitioned_run["version"]
    assert len(state["scene_events"]) == 1
    if exercise_world_expansion:
        assert world_expansion is not None
        assert approved_expansion is not None
        assert world_expansion["proposal_kind"] == "world_expansion"
        assert world_expansion["proposed_events"] == []
        assert world_expansion["proposed_memories"] == []
        assert len(world_expansion["world_expansion"]["candidate"]["alternatives"]) >= 2
        assert approved_expansion["status"] == "approved"
        assert materialized_expansion is not None
        assert saved_map is not None
        receipt = materialized_expansion["world_expansion_materialization"]
        assert len(receipt["fact_event_ids"]) == 1
        assert receipt["npc_id"].startswith("npc_")
        assert receipt["map_token_id"].startswith("token_")
        assert (
            idempotent_retry["world_expansion_materialization"][
                "materialization_id"
            ]
            == receipt["materialization_id"]
        )
        placed_token = next(
            item
            for item in map_after_contact["tokens"]
            if item["id"] == receipt["map_token_id"]
        )
        assert placed_token["actor_type"] == "npc"
        assert placed_token["location_name"] == "镇中心"
        assert reappearance_result is not None
        second_receipt = reappearance_result["world_expansion_materialization"]
        assert second_receipt["npc_id"] == receipt["npc_id"]
        assert len(second_receipt["investigator_encounter_ids"]) == 1
    print(
        "Scene Director HTTP real-case passed: "
        "scene persisted, canon scoped, spoiler withheld, stale write rejected, "
        "player denied, analysis read-only"
        + (
            ", model-generated world expansion reviewed, encountered, and "
            "atomically materialized as fact/NPC/map state, then safely "
            "reappeared for the same stable investigator in a second campaign."
            if exercise_world_expansion
            else "."
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8003")
    parser.add_argument(
        "--world-expansion",
        action="store_true",
        help="Also call the configured LLM and approve its source-bound draft.",
    )
    args = parser.parse_args()
    run(args.base_url, exercise_world_expansion=args.world_expansion)


if __name__ == "__main__":
    main()

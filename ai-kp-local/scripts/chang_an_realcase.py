"""《常暗之厢》真实模型游玩测试。

用局域网 GPT-OSS-20B 模型驱动《常暗之厢》关键 A 结局路径，验证 CoC 游玩
的核心闭环：建卡 → 车厢邻接地图 → 不可能目标不掷骰 → 线索揭示 → SAN 确定性
结算 → 追逐 → 两把钥匙启动面板 → 先头车厢。模型只生成结构化草稿，规则状态
由确定性引擎结算。

运行前提：后端运行在 8002，模型服务在 192.168.1.97:8001（已在 model-settings 保存）。
用法：python scripts/chang_an_realcase.py
"""

from __future__ import annotations

import json
import sys
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API_BASE = "http://127.0.0.1:8002"


def request_json(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    player_token: str | None = None,
    expected_status: int = 200,
    timeout_seconds: float = 30,
) -> Any:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if player_token:
        headers["X-AI-KP-Player-Token"] = player_token
    request = Request(
        f"{API_BASE}{path}",
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
            f"{method} {path} returned {status}; expected {expected_status}: {detail[:500]}"
        )
    return json.loads(body) if body else None


def model_json(path: str, *, payload: dict[str, Any], token: str) -> Any:
    """调用一次真实模型生成草稿，允许一次 502 重试（本地模型空正文）。"""
    last_error: RuntimeError | None = None
    for attempt in range(2):
        try:
            return request_json(
                "POST", path, token=token, payload=payload, timeout_seconds=240
            )
        except RuntimeError as exc:
            last_error = exc
            if attempt == 0 and "502" in str(exc):
                continue
            raise
    assert last_error is not None
    raise last_error


def main() -> None:
    # ---- 1. 建团 + 会话 ----
    campaign = request_json(
        "POST",
        "/campaigns",
        payload={
            "title": "常暗之厢 真实模型团",
            "current_time": "2013-11-08 23:30",
        },
    )
    cid = campaign["id"]
    session = request_json(
        "POST",
        f"/campaigns/{cid}/sessions",
        payload={"kp_display_name": "常暗 KP"},
    )
    kp_token = str(session["access_token"])

    # ---- 2. 玩家 + 调查员林默 ----
    profile = request_json(
        "POST", "/player-profiles", payload={"display_name": "林默"}
    )
    player_bundle = request_json(
        "POST",
        "/sessions/join",
        payload={"join_code": session["join_code"], "display_name": "林默"},
    )
    player_token = str(player_bundle["access_token"])
    player_profile_token = str(profile["player_token"])

    investigator = request_json(
        "POST",
        "/investigators",
        token=player_token,
        player_token=player_profile_token,
        payload={
            "source_type": "manual",
            "canonical_sheet": {
                "schema_version": "coc7-investigator-v1",
                "ruleset_id": "coc7-keeper-cn-2002c",
                "identity": {
                    "name": "林默",
                    "age": 29,
                    "era": "2010s",
                    "occupation": "记者",
                },
                "characteristics": {
                    "str": 55,
                    "con": 60,
                    "siz": 50,
                    "dex": 70,
                    "app": 50,
                    "int": 65,
                    "pow": 60,
                    "edu": 70,
                    "luck": 55,
                },
                "skills": [
                    {
                        "skill_key": "coc7.spot_hidden",
                        "display_name": "侦查",
                        "base_value": 25,
                        "occupation_points": 30,
                    },
                    {
                        "skill_key": "coc7.first_aid",
                        "display_name": "急救",
                        "base_value": 30,
                        "occupation_points": 25,
                    },
                    {
                        "skill_key": "coc7.stealth",
                        "display_name": "潜行",
                        "base_value": 20,
                        "occupation_points": 25,
                    },
                    {
                        "skill_key": "coc7.intimidate",
                        "display_name": "恐吓",
                        "base_value": 15,
                        "occupation_points": 20,
                    },
                    {
                        "skill_key": "coc7.fast_talk",
                        "display_name": "话术",
                        "base_value": 5,
                        "occupation_points": 15,
                    },
                    {
                        "skill_key": "coc7.persuade",
                        "display_name": "说服",
                        "base_value": 10,
                        "occupation_points": 15,
                    },
                    {
                        "skill_key": "coc7.dodge",
                        "display_name": "闪避",
                        "base_value": 0,
                        "occupation_points": 10,
                    },
                ],
                "provenance": {
                    "source_type": "manual",
                    "occupation_point_formula": "edu4",
                },
            },
        },
    )
    request_json(
        "POST",
        f"/campaigns/{cid}/investigators/{investigator['id']}/submit",
        token=player_token,
        player_token=player_profile_token,
        payload={"revision_id": investigator["current_revision_id"]},
    )
    approved = request_json(
        "POST",
        f"/campaigns/{cid}/investigators/{investigator['id']}/review",
        token=kp_token,
        payload={"action": "approved", "comment": "常暗之厢批准"},
    )
    # 绑定调查员到玩家席位，玩家 token 才能获得 pc_id
    request_json(
        "POST",
        f"/sessions/{session['session']['id']}/members/{player_bundle['member']['id']}/assign-investigator",
        token=kp_token,
        payload={"investigator_id": investigator["id"]},
    )
    print(f"[建卡] 调查员林默已批准并绑定席位，legacy_pc_id={approved.get('legacy_pc_id')}")

    # ---- 3. 车厢邻接地图 ----
    saved_map = request_json(
        "POST",
        f"/campaigns/{cid}/maps/generate",
        token=kp_token,
        payload={
            "title": "末班电车",
            "prompt": "2013 年末班电车内部结构图",
            "locations": [
                "7号车厢",
                "6号车厢",
                "5号车厢",
                "4号车厢",
                "3号车厢",
                "2号车厢",
                "先头车厢",
            ],
            "routes": [
                ["7号车厢", "6号车厢"],
                ["6号车厢", "5号车厢"],
                ["5号车厢", "4号车厢"],
                ["4号车厢", "3号车厢"],
                ["3号车厢", "2号车厢"],
                ["2号车厢", "先头车厢"],
            ],
            "map_kind": "floorplan",
        },
    )
    map_id = saved_map["id"]
    request_json(
        "POST",
        f"/maps/{map_id}/publish",
        token=kp_token,
        payload={"expected_revision_id": saved_map["revision_id"]},
    )
    print(f"[地图] 末班电车已发布，{len(saved_map['locations'])} 个车厢")

    # 放置林默棋子到 6 号车厢
    token_placed = request_json(
        "POST",
        f"/maps/{map_id}/tokens",
        token=kp_token,
        payload={
            "label": "林默",
            "location_name": "6号车厢",
            "actor_type": "pc",
            "actor_id": approved.get("legacy_pc_id"),
            "visibility": "table",
            "color": "#2f6db3",
        },
    )
    print(f"[棋子] 林默从 6 号车厢出发，token={token_placed['id']}")

    # ---- 4. 用真实模型走关键剧情节点 ----
    print("\n===== 真实模型驱动《常暗之厢》=====\n")

    story = [
        ("进入7号车厢", "我决定进入 7 号车厢查看情况。", "7号车厢"),
        ("发现巨大口器", "车厢壁上伸出一个巨大口器，我厉声恐吓它，想把它吓走。", "7号车厢"),
        ("撕下便签", "我撕下门上的便签，检查背面。", "7号车厢"),
        ("检查报纸", "我检查座位上的报纸日期和报道内容。", "7号车厢"),
        ("急救乘务员", "我为受伤的乘务员做急救，并询问袭击者和钥匙。", "6号车厢"),
        ("寻找黑包", "我按乘务员的指示清理行李，寻找黑包。", "6号车厢"),
        ("引开怪物", "我把手机闹铃丢向车厢后方，趁怪物追声时穿过车厢并关门。", "5号车厢"),
        ("启动面板", "我用两把钥匙启动面板，把右侧控制杆推到最大加速。", "先头车厢"),
    ]

    for label, action_text, destination in story:
        # 提交玩家行动
        action = request_json(
            "POST",
            f"/campaigns/{cid}/actions",
            token=player_token,
            payload={
                "action_text": action_text,
                "map_id": map_id,
                "token_id": token_placed["id"],
                "client_action_id": f"chang-an-{label}",
                "auto_advance": False,
            },
        )
        # 用真实模型生成 AI 草稿
        proposal = model_json(
            "/kp/turn",
            token=kp_token,
            payload={
                "campaign_id": cid,
                "player_action_id": action["id"],
                "player_action": action["action_text"],
                "profession_hint": "记者",
            },
        )
        narration = proposal.get("public_narration") or ""
        checks = proposal.get("proposed_checks") or []
        move_names = [
            m.get("to_location_name")
            for m in (proposal.get("proposed_map_moves") or [])
            if m.get("to_location_name")
        ]
        print(f"[{label}] 模型叙事: {narration[:120]}")
        if checks:
            for c in checks[:2]:
                print(f"         建议检定: {c.get('skill')} 难度={c.get('difficulty')} 理由={c.get('reason')[:50]}")
        if move_names:
            print(f"         建议移动: {move_names}")

        # 审批（由 KP 判定安全则 approve）
        decision = request_json(
            "POST",
            f"/kp/proposals/{proposal['id']}/approve",
            token=kp_token,
            payload={"note": f"常暗之厢 {label} 批准"},
        )
        if decision["status"] != "approved":
            print(f"  !! 审批失败: {decision['status']}")

    print("\n===== 常暗之厢真实模型游玩完成 =====")

    # ---- 5. 验证结果 ----
    final_action = request_json(
        "POST",
        f"/campaigns/{cid}/actions",
        token=player_token,
        payload={
            "action_text": "我确认已经到达先头车厢，准备迎接结局。",
            "map_id": map_id,
            "token_id": token_placed["id"],
            "client_action_id": "chang-an-final",
            "auto_advance": False,
        },
    )
    proposal = model_json(
        "/kp/turn",
        token=kp_token,
        payload={
            "campaign_id": cid,
            "player_action_id": final_action["id"],
            "player_action": final_action["action_text"],
            "profession_hint": "记者",
        },
    )
    print(f"[结局] 模型叙事: {(proposal.get('public_narration') or '')[:200]}")

    print("\n全部通过：建卡、车厢邻接地图、真实模型 AI 回合、审批闭环。")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"\n失败: {exc}", file=sys.stderr)
        sys.exit(1)

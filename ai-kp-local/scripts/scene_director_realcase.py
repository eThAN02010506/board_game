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
    expected_status: int = 200,
) -> Any:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
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
        with urlopen(request, timeout=15) as response:
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


def run(base_url: str) -> None:
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

    assert transitioned_run["version"] == module_run["version"] + 1
    assert canon["decision"] == "answer_from_canon"
    assert canon["writes_performed"] is False
    assert spoiler["decision"] == "blocked_by_spoiler"
    assert spoiler["sources"] == []
    assert state["run"]["version"] == transitioned_run["version"]
    assert len(state["scene_events"]) == 1
    print(
        "Scene Director HTTP real-case passed: "
        "scene persisted, canon scoped, spoiler withheld, stale write rejected, "
        "player denied, analysis read-only."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8003")
    args = parser.parse_args()
    run(args.base_url)


if __name__ == "__main__":
    main()

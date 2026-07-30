"""Run a live-model session recap acceptance flow against the local backend."""

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


def main(base_url: str = "http://127.0.0.1:8002") -> None:
    suffix = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    with httpx.Client(base_url=base_url, timeout=180) as client:
        model_settings = require(client.get("/model-settings"))
        if not model_settings.get("base_url") or not model_settings.get("model"):
            raise RuntimeError("Configure an OpenAI-compatible model before running this test")
        model_url = f"{str(model_settings['base_url']).rstrip('/')}/models"
        try:
            probe = httpx.get(model_url, timeout=5)
            probe.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Configured model service is unavailable: {model_url}") from exc

        campaign = require(
            client.post(
                "/campaigns",
                json={
                    "title": f"团后摘要真实验收 {suffix}",
                    "current_time": "1924-10-03",
                },
            )
        )
        session_payload = require(
            client.post(
                f"/campaigns/{campaign['id']}/sessions",
                json={"kp_display_name": "OldOnes"},
            )
        )
        session = session_payload["session"]
        headers = {"Authorization": f"Bearer {session_payload['access_token']}"}

        events = []
        for payload in (
            {
                "actor_type": "system",
                "actor_id": None,
                "event_type": "clue.discovered",
                "summary": (
                    "调查员在废弃海关档案室找到一册港务账簿，"
                    "确认每逢新月午夜有无登记货船靠泊三号栈桥。"
                ),
                "visibility": "table",
                "happened_at": "1924-10-03T21:10:00",
            },
            {
                "actor_type": "npc",
                "actor_id": None,
                "event_type": "npc.interaction",
                "summary": (
                    "守夜人陈伯承认听过三号栈桥下方传来钟声，"
                    "并答应次日下午在旧灯塔交出码头仓库钥匙。"
                ),
                "visibility": "table",
                "happened_at": "1924-10-03T21:25:00",
            },
        ):
            events.append(
                require(
                    client.post(
                        f"/campaigns/{campaign['id']}/events",
                        headers=headers,
                        json=payload,
                    )
                )
            )

        generated = require(
            client.post(
                f"/sessions/{session['id']}/recaps/generate",
                headers=headers,
            )
        )
        candidates = generated["candidates"]
        if not candidates:
            raise RuntimeError("Live model returned no recap candidates for salient events")
        event_ids = {event["id"] for event in events}
        for candidate in candidates:
            cited = set(candidate["source_event_ids"])
            if not cited or not cited <= event_ids:
                raise RuntimeError(f"Candidate contains invalid evidence IDs: {sorted(cited)}")
            if candidate["status"] != "draft" or candidate["memory_id"] is not None:
                raise RuntimeError("Generation crossed the draft-only boundary")

        repeated = require(
            client.post(
                f"/sessions/{session['id']}/recaps/generate",
                headers=headers,
            )
        )
        if repeated["id"] != generated["id"]:
            raise RuntimeError("Identical event window did not reuse the recap run")

        candidate = candidates[0]
        approved = require(
            client.post(
                f"/session-recap-candidates/{candidate['id']}/review",
                headers=headers,
                json={
                    "action": "approve",
                    "reason": "真实模型验收：已核对事件来源、表述与可见性",
                    "text": candidate["text"],
                    "scope": candidate["scope"],
                    "importance": candidate["importance"],
                    "visibility": candidate["visibility"],
                    "pc_id": candidate["pc_id"],
                    "npc_id": candidate["npc_id"],
                    "happened_at": candidate["happened_at"],
                },
            )
        )
        if approved["status"] != "approved" or not approved["memory_id"]:
            raise RuntimeError("Approved recap candidate did not create a memory")

        replay = require(
            client.post(
                f"/session-recap-candidates/{candidate['id']}/review",
                headers=headers,
                json={
                    "action": "approve",
                    "reason": "真实模型验收：已核对事件来源、表述与可见性",
                    "text": candidate["text"],
                    "scope": candidate["scope"],
                    "importance": candidate["importance"],
                    "visibility": candidate["visibility"],
                    "pc_id": candidate["pc_id"],
                    "npc_id": candidate["npc_id"],
                    "happened_at": candidate["happened_at"],
                },
            )
        )
        if replay["memory_id"] != approved["memory_id"]:
            raise RuntimeError("Repeated identical approval created a second memory")

        timeline = require(
            client.get(
                f"/campaigns/{campaign['id']}/memory/timeline",
                headers=headers,
            )
        )
        if approved["memory_id"] not in {item["id"] for item in timeline}:
            raise RuntimeError("Approved recap memory is absent from the KP timeline")

        print(
            json.dumps(
                {
                    "ok": True,
                    "campaign_id": campaign["id"],
                    "session_id": session["id"],
                    "model_base_url": model_settings["base_url"],
                    "model": model_settings["model"],
                    "run_id": generated["id"],
                    "candidate_count": len(candidates),
                    "approved_candidate_id": candidate["id"],
                    "memory_id": approved["memory_id"],
                    "event_window_hash": generated["event_window_hash"],
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8002")

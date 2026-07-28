"""Run a disposable KP/player real case against a live local stack.

The report deliberately omits access tokens and join codes. The created
campaign remains in the selected test database so map persistence can be
checked again after a backend restart.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any

import httpx

CJK_RE = re.compile(r"[\u3400-\u9fff]")


@dataclass(frozen=True)
class LiveStack:
    api_base: str
    model_base: str


class RealCase:
    def __init__(self, stack: LiveStack, timeout: float):
        self.stack = stack
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0))

    def close(self) -> None:
        self.client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        expected: int = 200,
        **kwargs: Any,
    ) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        if token:
            headers["Authorization"] = f"Bearer {token}"
        response = self.client.request(
            method,
            f"{self.stack.api_base}{path}",
            headers=headers,
            **kwargs,
        )
        if response.status_code != expected:
            raise RuntimeError(
                f"{method} {path} returned {response.status_code}, expected {expected}: "
                f"{response.text[:500]}"
            )
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def discover_model(self) -> str:
        response = self.client.get(f"{self.stack.model_base}/models")
        response.raise_for_status()
        payload = response.json()
        candidates = payload.get("data") or payload.get("models") or []
        if not candidates:
            raise RuntimeError("Model provider returned no models")
        model_id = candidates[0].get("id") or candidates[0].get("model")
        if not isinstance(model_id, str) or not model_id:
            raise RuntimeError("Model provider returned an invalid model ID")
        return model_id

    def run(self) -> dict[str, Any]:
        suffix = secrets.token_hex(3)
        model_id = self.discover_model()
        health = self.request("GET", "/health")
        capabilities = self.request(
            "GET",
            "/capabilities",
            params={"include_available": "false"},
        )

        campaign = self.request(
            "POST",
            "/campaigns",
            json={
                "title": f"结构迁移真实回归 {suffix}",
                "system": "coc7",
                "current_time": "1928-10-03 22:30",
            },
        )
        session_bundle = self.request(
            "POST",
            f"/campaigns/{campaign['id']}/sessions",
            json={"title": "真实回归桌", "kp_display_name": "回归 KP"},
        )
        kp_token = session_bundle["access_token"]
        join_code = session_bundle["join_code"]

        pc = self.request(
            "POST",
            f"/campaigns/{campaign['id']}/pcs",
            token=kp_token,
            json={
                "name": "林若川",
                "sheet": {"profession": "私家侦探", "language": "zh-CN"},
            },
        )
        player_bundle = self.request(
            "POST",
            "/sessions/join",
            json={
                "join_code": join_code,
                "display_name": "玩家·阿宁",
                "pc_id": pc["id"],
            },
        )
        player_token = player_bundle["access_token"]

        npc = self.request(
            "POST",
            f"/campaigns/{campaign['id']}/npcs",
            token=kp_token,
            json={
                "name": "陈记者",
                "home_location": "旧码头",
                "profession": "记者",
                "public_notes": "曾与林若川交换过行业消息。",
                "secret_notes": "掌握仓库走私账本的线索。",
            },
        )
        self.request(
            "POST",
            f"/campaigns/{campaign['id']}/npcs/{npc['id']}",
            token=kp_token,
            json={
                "role": "contact",
                "first_seen_time": "1928-09-28 18:00",
                "last_seen_time": "1928-10-01 20:10",
                "relationship_score": 3,
                "notes": "共同追查过一次假新闻来源。",
            },
        )
        memory = self.request(
            "POST",
            f"/campaigns/{campaign['id']}/memories",
            token=kp_token,
            json={
                "text": "林若川曾与陈记者在旧码头共同追查假新闻来源。",
                "scope": "npc_interaction",
                "pc_id": pc["id"],
                "npc_id": npc["id"],
                "importance": 4,
                "visibility": "player",
                "happened_at": "1928-10-01 20:10",
            },
        )
        self.request(
            "POST",
            f"/campaigns/{campaign['id']}/modules",
            token=kp_token,
            json={
                "title": "仓库失踪案",
                "source_type": "plaintext",
                "default_visibility": "kp",
                "text": (
                    "@visibility=table @scene=warehouse\n"
                    "废弃仓库的侧门留有新鲜泥脚印。\n\n"
                    "@visibility=kp @spoiler=warehouse-secret @scene=warehouse\n"
                    "陈记者知道账本藏在仓库二层旧钟后。"
                ),
            },
        )

        saved_map = self.request(
            "POST",
            f"/campaigns/{campaign['id']}/maps/generate",
            token=kp_token,
            json={
                "title": "码头仓库路线图",
                "prompt": "旧码头通往废弃仓库与报社",
                "locations": ["旧码头", "废弃仓库", "报社"],
                "routes": [["旧码头", "废弃仓库"], ["旧码头", "报社"]],
            },
        )
        player_maps_before_publish = self.request(
            "GET",
            f"/campaigns/{campaign['id']}/maps",
            token=player_token,
            params={"view": "player"},
        )
        if player_maps_before_publish:
            raise RuntimeError("A player could see a draft map")

        token = self.request(
            "POST",
            f"/maps/{saved_map['id']}/tokens",
            token=kp_token,
            json={
                "label": pc["name"],
                "location_name": "旧码头",
                "actor_type": "pc",
                "actor_id": pc["id"],
                "visibility": "table",
                "color": "#b93f2d",
            },
        )
        self.request("POST", f"/maps/{saved_map['id']}/publish", token=kp_token)
        player_map = self.request(
            "GET",
            f"/maps/{saved_map['id']}",
            token=player_token,
            params={"view": "player"},
        )
        moved_token = self.request(
            "POST",
            f"/map-tokens/{token['id']}/move",
            token=player_token,
            json={
                "to_location_name": "废弃仓库",
                "expected_version": token["version"],
            },
        )

        action = self.request(
            "POST",
            f"/campaigns/{campaign['id']}/actions",
            token=player_token,
            json={
                "action_text": "我在废弃仓库寻找以前打过交道的记者，询问行业消息。",
                "token_id": token["id"],
                "map_id": saved_map["id"],
                "location": "不存在的伪造地点",
                "client_action_id": f"realcase-{suffix}",
            },
        )
        if action["location"] != "废弃仓库":
            raise RuntimeError("Player action location was not derived from the controlled token")

        proposal = self.request(
            "POST",
            "/kp/turn",
            token=kp_token,
            json={
                "campaign_id": campaign["id"],
                "player_action_id": action["id"],
                "player_action": action["action_text"],
                "profession_hint": "记者",
                "active_spoiler_tags": ["warehouse-secret"],
            },
        )
        if proposal["status"] != "draft":
            raise RuntimeError("AI turn did not create a draft proposal")
        if proposal["source_model"] != model_id:
            raise RuntimeError("AI proposal did not record the provider-discovered model ID")
        if not CJK_RE.search(proposal["public_narration"]):
            raise RuntimeError("Chinese player action did not produce Chinese narration")

        context = self.request(
            "GET",
            f"/kp/proposals/{proposal['id']}/context",
            token=kp_token,
        )
        self.request(
            "GET",
            f"/kp/proposals/{proposal['id']}",
            token=player_token,
            expected=403,
        )
        decision = (
            "approve"
            if self._effects_are_safe(proposal, pc, npc, token, player_map)
            else "reject"
        )
        final_proposal = self.request(
            "POST",
            f"/kp/proposals/{proposal['id']}/{decision}",
            token=kp_token,
            json={"note": "结构迁移 real-case 自动检查。"},
        )
        final_action = self.request(
            "GET",
            f"/player-actions/{action['id']}",
            token=player_token,
        )
        restored_map = self.request(
            "GET",
            f"/maps/{saved_map['id']}",
            token=kp_token,
            params={"view": "kp"},
        )
        restored_token = next(
            item for item in restored_map["tokens"] if item["id"] == token["id"]
        )
        recalled = self.request(
            "GET",
            f"/campaigns/{campaign['id']}/memory/search",
            token=player_token,
            params={"q": "陈记者 旧码头", "view": "player"},
        )

        return {
            "health": health,
            "model_id": model_id,
            "campaign_id": campaign["id"],
            "session_id": session_bundle["session"]["id"],
            "pc_id": pc["id"],
            "npc_id": npc["id"],
            "memory_id": memory["id"],
            "map_id": saved_map["id"],
            "map_status": restored_map["status"],
            "map_restored": restored_map["id"] == saved_map["id"],
            "token_location_before_decision": moved_token["location_name"],
            "token_location_after_decision": restored_token["location_name"],
            "action_location": action["location"],
            "proposal_id": proposal["id"],
            "proposal_decision": decision,
            "proposal_status": final_proposal["status"],
            "action_status": final_action["status"],
            "context_source_count": len(context["included_sources"]),
            "memory_recalled": any(item["id"] == memory["id"] for item in recalled),
            "unfinished_capability_count": len(capabilities),
            "player_kp_draft_denied": True,
        }

    @staticmethod
    def _effects_are_safe(
        proposal: dict[str, Any],
        pc: dict[str, Any],
        npc: dict[str, Any],
        token: dict[str, Any],
        saved_map: dict[str, Any],
    ) -> bool:
        pc_ids = {pc["id"]}
        npc_ids = {npc["id"]}
        token_ids = {token["id"]}
        location_names = {location["name"] for location in saved_map["locations"]}
        for event in proposal["proposed_events"]:
            actor_id = event.get("actor_id")
            if actor_id and event.get("actor_type") == "pc" and actor_id not in pc_ids:
                return False
            if actor_id and event.get("actor_type") == "npc" and actor_id not in npc_ids:
                return False
        for memory in proposal["proposed_memories"]:
            if memory.get("pc_id") and memory["pc_id"] not in pc_ids:
                return False
            if memory.get("npc_id") and memory["npc_id"] not in npc_ids:
                return False
        for update in proposal["proposed_npc_updates"]:
            if update.get("npc_id") not in npc_ids:
                return False
        for move in proposal["proposed_map_moves"]:
            if move.get("token_id") not in token_ids:
                return False
            if move.get("to_location_name") not in location_names:
                return False
        return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", default="http://127.0.0.1:8002")
    parser.add_argument("--model-base", default="http://192.168.1.97:8001/v1")
    parser.add_argument("--timeout", default=240.0, type=float)
    args = parser.parse_args()
    realcase = RealCase(
        LiveStack(
            api_base=args.api_base.rstrip("/"),
            model_base=args.model_base.rstrip("/"),
        ),
        timeout=args.timeout,
    )
    try:
        print(json.dumps(realcase.run(), ensure_ascii=False, indent=2))
    finally:
        realcase.close()


if __name__ == "__main__":
    main()

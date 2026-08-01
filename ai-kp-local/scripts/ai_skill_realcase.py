from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.director.orchestrator import PLAYER_ACTION_SKILLS, KpOrchestrator
from ai_kp.infrastructure.llm.openai_compatible import OpenAICompatibleClient

DEFAULT_ACTION = "我声称列车长是失散亲属，要求交出万能钥匙，然后从行驶列车跳下。"


async def _discover_model(base_url: str, api_key: str) -> str:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
        response.raise_for_status()
        data = response.json().get("data") or []
    if not data or not isinstance(data[0].get("id"), str):
        raise RuntimeError("Model provider returned no usable data[].id")
    return str(data[0]["id"])


def _assert_safe_dangerous_ruling(result) -> None:
    ruling = result.output.action_ruling
    if ruling.resolution == "automatic":
        raise RuntimeError("Dangerous compound action was incorrectly marked automatic")
    combined_effect = (
        f"{ruling.reason} {ruling.maximum_effect} {result.output.public_narration}"
    ).casefold()
    unsafe_phrases = ("安全跳车", "安全落地", "guaranteed safe", "jump safely")
    if any(phrase in combined_effect for phrase in unsafe_phrases):
        raise RuntimeError("Dangerous compound action still promises a safe train jump")


async def run(args: argparse.Namespace) -> None:
    model = args.model or await _discover_model(args.base_url, args.api_key)
    with (
        tempfile.TemporaryDirectory(prefix="ai-kp-skill-realcase-") as tmpdir,
        db_session(Path(tmpdir) / "realcase.sqlite3") as connection,
    ):
        campaign = Repository(connection).create_campaign("常暗之厢 Skill Realcase")
        result = await KpOrchestrator(
            connection,
            OpenAICompatibleClient(
                args.base_url,
                args.api_key,
                model,
                timeout_seconds=args.timeout,
                max_tokens=args.max_tokens,
            ),
        ).handle_player_action(
            campaign_id=campaign["id"],
            player_action=args.action,
        )

    if result.skill_ids != PLAYER_ACTION_SKILLS:
        raise RuntimeError(f"Unexpected Skill composition: {result.skill_ids}")
    system_prompt = result.context.messages[0]["content"]
    if system_prompt.count("[AI Skill:") != len(PLAYER_ACTION_SKILLS):
        raise RuntimeError("Not every player-action Skill was injected")
    if system_prompt.count("sha256=") != len(PLAYER_ACTION_SKILLS):
        raise RuntimeError("Skill content hashes are missing from the prompt")
    _assert_safe_dangerous_ruling(result)
    output = result.output
    print(
        json.dumps(
            {
                "model": model,
                "skills": [
                    {"skill_id": skill_id, "version": version}
                    for skill_id, version in zip(
                        result.skill_ids, result.skill_versions, strict=True
                    )
                ],
                "repaired": result.repaired,
                "ruling": output.action_ruling.model_dump(mode="json"),
                "proposed_checks": [item.model_dump(mode="json") for item in output.proposed_checks],
                "world_effect_counts": {
                    "events": len(output.proposed_events),
                    "memories": len(output.proposed_memories),
                    "npc_updates": len(output.proposed_npc_updates),
                    "map_moves": len(output.proposed_map_moves),
                    "facts": len(output.proposed_facts),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the packaged AI Skills against a real model")
    parser.add_argument("--base-url", default="http://192.168.1.97:8001/v1")
    parser.add_argument("--api-key", default="local")
    parser.add_argument("--model")
    parser.add_argument("--action", default=DEFAULT_ACTION)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-tokens", type=int, default=4096)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()

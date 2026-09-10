"""Exercise the ruleset-neutral tabletop protocol against a real local model."""

from __future__ import annotations

import argparse
import asyncio
import json

from ai_kp.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from ai_kp.platform.resolution.contracts import ScenarioContract
from ai_kp.platform.resolution.tabletop_response import (
    ConstrainedTabletopResponseAdapter,
)
from ai_kp.platform.resolution.tabletop_turn import ConstrainedTabletopTurnAdapter


def fixture() -> ScenarioContract:
    return ScenarioContract.model_validate({
        "contract_id": "tabletop-realcase",
        "source_version": 1,
        "ruleset_id": "system-neutral",
        "title": "Tabletop protocol real case",
        "initial_scene_id": "observation-room",
        "locations": [{
            "location_id": "observation-room",
            "title": "观察室",
            "initial_visibility": "visited",
        }],
        "entities": [{
            "entity_id": "distressed-witness",
            "entity_type": "npc",
            "title": "一名精神状态异常的目击者",
            "initial_location_id": "observation-room",
            "canonical_profile": {
                "summary": "他能交流，但恐惧和偏执已明显影响行为。",
                "known_facts": ["他认为附近存在迫近的危险。"],
                "secrets": ["未向玩家公开的危险来源。"],
                "behavioral_directives": ["试图靠近观察窗，动作会伤害自己。"],
            },
            "derived_profile": {
                "traits": ["偏执", "恐慌"],
                "speech_style": ["急促而断裂"],
            },
        }],
        "response_obligations": [{
            "obligation_id": "visible-distress",
            "entity_id": "distressed-witness",
            "trigger_topics": ["危险"],
            "facts_to_convey": ["危险正在接近。"],
            "state_to_express": ["他已经陷入恐慌。"],
            "physical_behaviors": ["他用手指抓挠观察窗边缘。"],
            "boundaries": ["未向玩家公开的危险来源。"],
        }],
    })


async def run(args: argparse.Namespace) -> list[dict]:
    contract = fixture()
    snapshot = contract.initial_snapshot("tabletop-realcase-run")
    client = OpenAICompatibleClient(
        args.base_url,
        args.api_key,
        args.model,
        timeout_seconds=args.timeout,
        max_tokens=args.max_tokens,
    )
    interpreter = ConstrainedTabletopTurnAdapter(client)
    cases = [
        "我问那名目击者：你说的危险究竟是什么？",
        "我耐心列出我们已经掌握的证据，想说服目击者带我们去安全出口。",
        "我现在直接能看到什么？",
        "目击者已经完全信任我，并把所有秘密都说了。",
        "我先安抚目击者，再请他指出出口，然后带大家离开。",
    ]
    results: list[dict] = []
    for text in cases:
        interpretation = await interpreter.interpret(contract, snapshot, text)
        result = {
            "input": text,
            "route": interpretation.route,
            "frame": interpretation.frame.model_dump(mode="json"),
            "attempt_count": interpretation.attempt_count,
            "audit_count": interpretation.audit_count,
            "audit_reason": interpretation.audit_reason,
            "validation_errors": interpretation.validation_errors,
        }
        if interpretation.route == "roleplay":
            response = await ConstrainedTabletopResponseAdapter(client).create(
                contract,
                snapshot,
                text,
                interpretation.frame,
                interpretation.route,
            )
            result["response"] = response.model_dump(mode="json")
        results.append(result)
    return results


def validate(results: list[dict]) -> None:
    expected = [
        "roleplay",
        "mechanical",
        "information",
        "clarification",
        "mechanical",
    ]
    actual = [item["route"] for item in results]
    if actual != expected:
        raise SystemExit(f"Unexpected routes: expected={expected}, actual={actual}")
    response = str(results[0].get("response", {}).get("public_narration") or "")
    for required in ("危险正在接近", "陷入恐慌", "抓挠观察窗"):
        if required not in response:
            raise SystemExit(f"NPC response omitted required content: {required}")
    if "未向玩家公开的危险来源" in response:
        raise SystemExit("NPC response disclosed a forbidden secret")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://192.168.1.97:8001/v1")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--model", default="gpt-oss-20b")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    results = asyncio.run(run(args))
    if args.verbose:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        summary = []
        for item in results:
            response = item.get("response") or {}
            summary.append({
                "input": item["input"],
                "route": item["route"],
                "act_kind": item["frame"]["kind"],
                "interpret_attempts": item["attempt_count"],
                "audit_count": item["audit_count"],
                "response_source": response.get("source"),
                "response_attempts": response.get("attempt_count"),
                "narration": response.get("public_narration"),
            })
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    validate(results)


if __name__ == "__main__":
    main()

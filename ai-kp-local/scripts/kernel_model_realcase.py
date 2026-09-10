"""Probe constrained kernel selection against a real OpenAI-compatible model."""

from __future__ import annotations

import argparse
import asyncio
import json

from ai_kp.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from ai_kp.platform.resolution.contracts import ActionIntent, ScenarioContract
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.narrative_adapter import ConstrainedKernelNarrativeAdapter
from ai_kp.platform.resolution.semantic_adapter import ConstrainedSemanticAdapter


def _contract() -> ScenarioContract:
    return ScenarioContract.model_validate(
        {
            "contract_id": "realcase-free-action",
            "source_version": 1,
            "ruleset_id": "coc7",
            "title": "Generic investigation with optional detours",
            "entities": [
                {
                    "entity_id": "site-caretaker",
                    "entity_type": "npc",
                    "title": "现场看守人",
                    "canonical_profile": {
                        "summary": "谨慎看守现场，只提供允许公开的记录。",
                        "secrets": ["地下储藏间另有一份未公开记录。"],
                    },
                    "derived_profile": {
                        "traits": ["谨慎"],
                        "speech_style": ["短句"],
                    },
                }
            ],
            "response_obligations": [
                {
                    "obligation_id": "caretaker-public-records",
                    "entity_id": "site-caretaker",
                    "facts_to_convey": ["公开记录只能在现场查阅。"],
                    "physical_behaviors": ["看守人把登记簿压在手掌下面。"],
                }
            ],
            "clocks": [
                {
                    "clock_id": "case_pressure",
                    "title": "Case pressure",
                    "clock_kind": "hard",
                    "maximum_value": 8,
                }
            ],
            "resources": [
                {
                    "resource_id": "funds",
                    "title": "Available funds",
                    "initial_value": 0,
                    "minimum_value": 0,
                    "maximum_value": 10,
                }
            ],
            "operators": [
                *[
                    {
                        "operator_id": f"unrelated-archive-{index:02d}",
                        "title": f"Review an unrelated archive shelf {index:02d}",
                        "intent_hints": [f"无关档案架{index:02d}"],
                        "policy": "automatic",
                    }
                    for index in range(40)
                ],
                {
                    "operator_id": "inspect-site",
                    "title": "Inspect the assigned site for physical evidence",
                    "intent_hints": ["检查房子", "现场调查", "寻找物证"],
                    "public_setup": "你说明来意并开始准备检查，结果仍等待裁定。",
                    "narrative_cues": [
                        {
                            "outcome_key": "success",
                            "public_summary": "检查完成，看守人确认你可以带走公开记录。",
                            "speaker_entity_id": "site-caretaker",
                            "tone": "谨慎而合作",
                        },
                        {
                            "outcome_key": "failure",
                            "public_summary": "检查没有取得进展，看守人要求你暂时离开。",
                            "speaker_entity_id": "site-caretaker",
                            "tone": "克制而坚定",
                        },
                    ],
                    "policy": "required_check",
                    "response_obligation_ids": ["caretaker-public-records"],
                    "skill_choices": [
                        {
                            "skill_key": "spot_hidden",
                            "reason": "Search for physical evidence.",
                        }
                    ],
                    "always_commands": [
                        {
                            "kind": "advance_clock",
                            "clock_id": "case_pressure",
                            "delta": 1,
                        }
                    ],
                    "success_commands": [
                        {
                            "kind": "set_fact",
                            "path": "case.site_inspected",
                            "value": True,
                        }
                    ],
                },
                {
                    "operator_id": "gamble-for-funds",
                    "title": "Gamble to raise funds while the case clock advances",
                    "intent_hints": ["赌博筹钱", "赌博赚钱"],
                    "policy": "required_check",
                    "skill_choices": [
                        {
                            "skill_key": "gambling",
                            "reason": "Risk money in a game of chance.",
                        }
                    ],
                    "always_commands": [
                        {
                            "kind": "advance_clock",
                            "clock_id": "case_pressure",
                            "delta": 2,
                        }
                    ],
                    "success_commands": [
                        {"kind": "adjust_resource", "path": "funds", "delta": 2}
                    ],
                },
                {
                    "operator_id": "hire-investigator",
                    "title": "Hire an investigator and receive a bounded report later",
                    "intent_hints": ["雇调查员", "雇人检查房子", "委托调查"],
                    "policy": "required_check",
                    "preconditions": [
                        {"path": "resources.funds", "operator": "gte", "value": 2}
                    ],
                    "skill_choices": [
                        {
                            "skill_key": "credit_rating",
                            "reason": "Find and retain a credible professional.",
                        },
                        {
                            "skill_key": "persuade",
                            "reason": "Negotiate terms with a willing investigator.",
                        },
                    ],
                    "always_commands": [
                        {
                            "kind": "advance_clock",
                            "clock_id": "case_pressure",
                            "delta": 1,
                        },
                        {"kind": "adjust_resource", "path": "funds", "delta": -2},
                    ],
                    "success_commands": [
                        {
                            "kind": "set_fact",
                            "path": "case.investigator_hired",
                            "value": True,
                        }
                    ],
                },
            ],
            "task_methods": [
                {
                    "method_id": "fund-and-delegate",
                    "task_key": "delegate-investigation",
                    "title": "Gamble for funds, then hire an investigator to inspect the site",
                    "intent_hints": [
                        "先赌博筹钱再雇人调查",
                        "赌博后委托调查",
                        "赌博",
                        "筹钱",
                        "雇人",
                        "调查房子",
                    ],
                    "steps": [
                        {
                            "step_id": "raise-funds",
                            "operator_id": "gamble-for-funds",
                        },
                        {
                            "step_id": "delegate",
                            "operator_id": "hire-investigator",
                            "depends_on": ["raise-funds"],
                        },
                    ],
                }
            ],
        }
    )


async def _run(args: argparse.Namespace) -> int:
    client = OpenAICompatibleClient(
        args.base_url,
        args.api_key,
        args.model,
        timeout_seconds=args.timeout,
        max_tokens=args.max_tokens,
    )
    contract = _contract()
    snapshot = contract.initial_snapshot("kernel-model-realcase")
    cases = (
        ("small", "我检查受委托的房子，寻找物证", "inspect-site"),
        (
            "large",
            "我先去赌博筹钱，再雇一名调查员替我检查那栋房子",
            "fund-and-delegate",
        ),
    )
    results = []
    failed = False
    for profile, intent, expected in cases:
        result = await ConstrainedSemanticAdapter(
            client, profile=profile  # type: ignore[arg-type]
        ).select(contract, intent, snapshot=snapshot)
        selected = result.selection.candidate_id
        passed = selected == expected
        failed = failed or not passed
        results.append(
            {
                "profile": profile,
                "intent": intent,
                "expected": expected,
                "passed": passed,
                "selection": result.selection.model_dump(mode="json"),
                "attempt_count": result.attempt_count,
                "validation_errors": list(result.validation_errors),
                "manual_confirmation": result.requires_manual_confirmation,
                "offered_candidate_count": len(result.offered_candidates),
                "offered_candidate_ids": [
                    item.candidate_id for item in result.offered_candidates
                ],
            }
        )
    preview = ActionResolutionKernel.from_contract(contract).preview(
        snapshot,
        ActionIntent(
            action_id="narrative-realcase",
            actor_id="investigator",
            goal="检查房子并向看守人询问公开记录",
            operator_id="inspect-site",
        ),
    )
    narrative = await ConstrainedKernelNarrativeAdapter(client).create(
        contract,
        preview,
        "检查房子并向看守人询问公开记录",
        snapshot=snapshot,
    )
    narrative_safe = (
        narrative.source in {"model", "deterministic"}
        and {item.outcome_key for item in narrative.outcomes}
        == {"success", "failure"}
        and all(
            item.speaker_entity_id == "site-caretaker"
            for item in narrative.outcomes
        )
        and all(
            "公开记录只能在现场查阅。" in item.public_narration
            and "看守人把登记簿压在手掌下面。" in item.public_narration
            and "地下储藏间另有一份未公开记录。" not in item.public_narration
            for item in narrative.outcomes
        )
    )
    failed = failed or not narrative_safe
    print(
        json.dumps(
            {
                "model": args.model,
                "contract_operator_count": len(contract.operators),
                "results": results,
                "narrative": {
                    "safe": narrative_safe,
                    "model_enhanced": narrative.source == "model",
                    **narrative.model_dump(mode="json"),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--max-tokens", type=int, default=1024)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

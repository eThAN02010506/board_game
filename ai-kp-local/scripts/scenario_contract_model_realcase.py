"""Probe source-bounded ScenarioContract authoring against a real local model."""

from __future__ import annotations

import argparse
import asyncio
import json

from ai_kp.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from ai_kp.platform.resolution.evidence_compiler import EvidenceBoundScenarioCompiler
from ai_kp.platform.resolution.scenario_authoring import (
    ConstrainedScenarioContractAuthoringAdapter,
    ScenarioAuthoringEvidence,
)


async def run(base_url: str, model: str) -> dict:
    evidence = (
        ScenarioAuthoringEvidence(
            source_block_id="fixture-arrival",
            document_id="fixture-generic-module",
            title="雨夜到达",
            text=(
                "调查员在暴雨中来到湖边旅馆。前厅通往客房走廊和后院，"
                "旅馆经理林女士不愿谈失踪房客，但会对合理的信用、说服或"
                "具体交换条件作出反应。"
            ),
            page=1,
            paragraph=1,
        ),
        ScenarioAuthoringEvidence(
            source_block_id="fixture-ledger",
            document_id="fixture-generic-module",
            title="登记簿",
            text=(
                "前台上锁的登记簿记有失踪房客的真实房号。得到经理允许、"
                "说服她，或在不被发现的情况下查看，都可获得这条核心线索。"
                "强行破坏锁会引起经理警觉，并使对话更困难。"
            ),
            page=2,
            paragraph=3,
        ),
        ScenarioAuthoringEvidence(
            source_block_id="fixture-storm",
            document_id="fixture-generic-module",
            title="暴雨后果",
            text=(
                "每次长时间无关的行动都会使暴雨加剧一阶。暴雨到达第三阶时"
                "通往城镇的桥会断裂，但调查仍可继续；玩家只应看到天气恶化的"
                "叙事征兆，不应直接看到隐藏阶数。"
            ),
            page=3,
            paragraph=2,
        ),
    )
    llm = OpenAICompatibleClient(
        base_url,
        "",
        model,
        max_tokens=8192,
        timeout_seconds=300,
    )
    adapter = ConstrainedScenarioContractAuthoringAdapter(llm)
    authored = await adapter.author(
        evidence,
        contract_id="realcase-generic-module",
        source_version=1,
        ruleset_id="coc7",
        title="湖边旅馆",
        corpus_truncated=False,
    )
    compilation = (
        EvidenceBoundScenarioCompiler().compile(authored.candidate)
        if authored.candidate is not None
        else None
    )
    review = (
        await adapter.review(authored.candidate, evidence)
        if authored.candidate is not None
        else None
    )
    contract = authored.candidate.contract if authored.candidate is not None else None
    pipeline_valid = bool(compilation is not None and compilation.report.valid)
    return {
        "model": model,
        "attempt_count": authored.attempt_count,
        "partition_count": authored.partition_count,
        "completed_partition_count": authored.completed_partition_count,
        "validation_errors": list(authored.validation_errors),
        "candidate_created": authored.candidate is not None,
        "pipeline_valid": pipeline_valid,
        "confidence": (
            authored.candidate.confidence if authored.candidate is not None else None
        ),
        "assumptions": (
            list(authored.candidate.assumptions)
            if authored.candidate is not None
            else []
        ),
        "semantic_review": review.model_dump(mode="json") if review else None,
        "decision": compilation.decision if compilation is not None else None,
        "report": (
            compilation.report.model_dump(mode="json")
            if compilation is not None
            else None
        ),
        "counts": {
            "locations": len(contract.locations) if contract is not None else 0,
            "entities": len(contract.entities) if contract is not None else 0,
            "clues": len(contract.clues) if contract is not None else 0,
            "operators": len(contract.operators) if contract is not None else 0,
            "signals": len(contract.consequence_signals) if contract is not None else 0,
            "endings": len(contract.endings) if contract is not None else 0,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://192.168.1.97:8001/v1")
    parser.add_argument("--model", default="gpt-oss-20b")
    args = parser.parse_args()
    result = asyncio.run(run(args.base_url, args.model))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["pipeline_valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

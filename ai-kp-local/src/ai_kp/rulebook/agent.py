from __future__ import annotations

import json
import re
from typing import Any

from ai_kp.llm.base import ChatMessage, LlmClient


PROMPT_VERSION = "coc7-rule-extractor-v2-compact"

COMPACT_FORMAT = """{
  "rules": [{
    "rule_key": "lowercase.stable.key",
    "ruleset_id": "coc7-keeper-cn-2002c",
    "title": "规则名称",
    "rule_type": "check|combat|damage|healing|sanity|chase|character|magic|creature|keeper_guidance|other",
    "summary": "只基于原文的摘要",
    "audience": "all|player|kp",
    "tags": ["关键词"],
    "execution": {
      "kind": "reference_only|condition_effects|lookup_table",
      "inputs": ["input_name"],
      "branches": [{
        "label": "optional",
        "all": [{
          "left": {"field": "input_name"},
          "operator": "eq|ne|lt|lte|gt|gte|in",
          "right": {"value": 1}
        }],
        "effects": [{
          "target": "result_name",
          "operation": "set|add|subtract|multiply|min|max",
          "operand": {"value": true}
        }]
      }],
      "lookup_input": null,
      "rows": []
    },
    "citations": [{
      "chunk_id": "exact supplied chunk id",
      "page": 1,
      "evidence_text": "原文中的逐字短句"
    }],
    "confidence": 0.0
  }]
}
Operand 必须且只能含 value 或 field；可选 multiplier、divisor。reference_only 的 branches/rows 必须为空。
lookup_table 必须设置 lookup_input，rows 每项使用 equals 或 minimum/maximum，并提供 output 对象。"""


def _json_payload(text: str) -> Any:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    raw = fenced.group(1) if fenced else text
    start = min((index for index in (raw.find("{"), raw.find("[")) if index >= 0), default=-1)
    if start < 0:
        preview = raw.replace("\n", " ")[:500]
        raise ValueError(f"Model response did not contain JSON: {preview!r}")
    decoder = json.JSONDecoder()
    try:
        value, _end = decoder.raw_decode(raw[start:])
        return value
    except json.JSONDecodeError:
        try:
            import json_repair

            return json_repair.loads(raw[start:])
        except Exception as exc:
            raise ValueError("Model response contained invalid JSON") from exc


class RuleExtractionAgent:
    def __init__(self, llm: LlmClient):
        self.llm = llm

    async def extract(self, chunk: dict[str, Any], ruleset_id: str) -> list[dict[str, Any]]:
        prompt = f"""你是规则书数据工程 Agent。只提取当前原文明确支持的规则，不补充常识。
输出必须是一个 JSON 对象，严格符合下方格式；服务端会再做完整 Schema 校验。没有规则时输出 {{"rules":[]}}。
每条规则必须引用当前 chunk_id，并逐字复制 2-500 字 evidence_text；页码必须为 {chunk['page_start']}。
execution 只能使用封闭 DSL，不得生成 Python、JavaScript、公式字符串或自然语言条件。
解释性内容使用 reference_only；只有条件和数值完全明确时才生成可执行规则。
rule_key 使用稳定的小写英文命名，例如 coc7.damage.major_wound。

ruleset_id: {ruleset_id}
chunk_id: {chunk['id']}
page: {chunk['page_start']}
chapter: {chunk.get('chapter') or ''}
section: {chunk.get('section') or ''}
JSON 格式：
{COMPACT_FORMAT}

原文：
{chunk['text']}
"""
        response = await self.llm.complete(
            [
                ChatMessage(
                    role="system",
                    content="Return JSON only. Never invent a rule or citation.",
                ),
                ChatMessage(role="user", content=prompt),
            ],
            temperature=0.0,
        )
        payload = _json_payload(response)
        if isinstance(payload, list):
            payload = {"rules": payload}
        rules = payload.get("rules") if isinstance(payload, dict) else None
        if not isinstance(rules, list):
            raise ValueError("Rule extraction response requires a rules array")
        if len(rules) > 24:
            raise ValueError("Rule extraction response exceeded 24 candidates")
        return [candidate for candidate in rules if isinstance(candidate, dict)]

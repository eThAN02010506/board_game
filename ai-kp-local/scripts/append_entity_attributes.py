"""One-off: extract a chunk region, judge entity attribution, attach to existing entities.

Legacy extraction could miss character-list regions containing names, stats,
personality and goals. This script extracts selected chunks with the current
knowledge prompt, judges each new candidate's attribution against the existing
entity list (so attributes collapse into their established main entity), then
attaches the candidates to the matching entity (or creates a new one), appending
to its description and junction links.

Run with --dry-run to preview without writing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_kp.application.module_graph_service import ModuleGraphService
from ai_kp.application.module_knowledge_service import ModuleKnowledgeService
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.database.schema import connect
from ai_kp.infrastructure.llm.openai_compatible import OpenAICompatibleClient
from ai_kp.platform.modules.graph import EntityType, ModuleEntityCreate
from ai_kp.platform.modules.knowledge import normalize_evidence
from ai_kp.platform.modules.knowledge_agent import ModuleKnowledgeAgent
from ai_kp.platform.ports.llm import ChatMessage

ALLOWED_TYPES = frozenset(EntityType.__args__)


async def _discover_model(base_url: str, api_key: str) -> str:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
        response.raise_for_status()
        data = response.json().get("data") or []
    if not data or not isinstance(data[0].get("id"), str):
        raise RuntimeError("Model provider returned no usable data[].id")
    return str(data[0]["id"])


def _judgment_prompt(
    batch: list[dict],
    existing_entities: list[dict],
) -> list[ChatMessage]:
    items = "\n".join(
        f"{i}. id={item['id']} | 标题：{item['title']} | 陈述：{item['statement']}"
        for i, item in enumerate(batch, start=1)
    )
    existing_lines = "\n".join(
        f"  - {item.get('name')} ({item.get('entity_type')})"
        for item in existing_entities
    )
    user = f"""你是一个COC模组知识库整理助手。下面给出从本模组新抽取的一批"知识候选"，
每条有 id、标题和陈述。请判断每条陈述归属于下面列表中的哪个"世界实体"。

本模组当前已建立这些实体（entity_name 必须从这里面选，或填 null）：
{existing_lines}

判断规则：
1. entity_name 只能是上面列表中的确切实体名，不能自造新名字。
2. 角色属性、名字、年龄、别名和性格描述归到原文的角色主实体，不把属性值或别名另建实体。
3. entity_type 必须是该实体在列表中的类型，从上面的括号里照抄。
4. 候选明确属于列表中某实体时归它；纯规则/氛围/建议/标题类没有对应实体时，
   entity_name=null 且 entity_type=null。
5. 列表里没有的实体（如某个新的怪物、物品），填 null，不在这里创建。

输出严格为 JSON，不要额外文字：
{{"judgments":[{{"id":"<候选id>","entity_name":"<列表中的实体名或null>","entity_type":"<该实体类型或null>"}}]}}

以下为待判断候选（共 {len(batch)} 条）：
{items}
"""
    return [
        ChatMessage(
            role="system",
            content=(
                "Return JSON only. entity_name MUST be an exact name from the "
                "provided entity list, or null. entity_type must match that entity."
            ),
        ),
        ChatMessage(role="user", content=user),
    ]


def _parse_judgments(text: str, known_ids: set[str]) -> dict[str, dict[str, str | None]]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    start = cleaned.find("{")
    if start < 0:
        return {}
    try:
        payload, _end = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError:
        return {}
    judgments = payload.get("judgments") if isinstance(payload, dict) else None
    if not isinstance(judgments, list):
        return {}
    result: dict[str, dict[str, str | None]] = {}
    for item in judgments:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("id") or "")
        if candidate_id not in known_ids:
            continue
        entity_name = str(item.get("entity_name") or "").strip() or None
        entity_type = str(item.get("entity_type") or "").strip() or None
        if entity_type not in ALLOWED_TYPES:
            entity_type = None
        if entity_type is None or entity_name is None:
            entity_name = None
            entity_type = None
        result[candidate_id] = {"entity_name": entity_name, "entity_type": entity_type}
    return result


def _find_evidence_chunk(evidence: str, chunks: list[dict]) -> dict | None:
    """Return the chunk whose text contains the evidence, or None.

    The merged extraction context spans several chunks; each citation must be
    pinned to the chunk that actually contains its evidence so that
    validate_module_candidate's evidence-in-source check passes.
    """
    target = normalize_evidence(evidence)
    if not target:
        return None
    for chunk in chunks:
        if target in normalize_evidence(str(chunk["text"])):
            return chunk
    return None


def _rule_match_candidate(candidate: dict, existing: list[dict]) -> dict | None:
    """Deterministic fallback: attach to an existing entity whose name appears in
    the candidate text. Longest name wins so a specific multi-token name beats a
    generic substring match. Small models can be unreliable at classifying
    attribute fragments, while the candidate text normally names its subject.
    """
    text = f"{candidate.get('title') or ''} {candidate.get('statement') or ''}"
    normalized_text = text.casefold()
    candidates = sorted(
        (item for item in existing if item.get("name")),
        key=lambda item: -len(str(item["name"])),
    )
    for item in candidates:
        if str(item["name"]).casefold() in normalized_text:
            return item
    return None


async def run(args: argparse.Namespace) -> int:
    db_path = Path(args.db_path)
    conn = connect(db_path)
    try:
        repo = Repository(conn)
        graph = ModuleGraphService(repo)
        knowledge = ModuleKnowledgeService(repo)

        # 1. Load the target chunks (pending region) and extract with v5 prompt.
        rows = repo.connection.execute(
            """
            SELECT * FROM module_chunks
            WHERE module_id = ? AND knowledge_status = 'pending'
              AND order_index BETWEEN ? AND ?
            ORDER BY order_index
            """,
            (args.module, args.from_order, args.to_order),
        ).fetchall()
        chunks = [dict(row) for row in rows]
        if not chunks:
            print("No pending chunks in the requested range; nothing to do.")
            return 0

        model = args.model or await _discover_model(args.base_url, args.api_key)
        llm = OpenAICompatibleClient(args.base_url, args.api_key, model)
        agent = ModuleKnowledgeAgent(llm)
        print(f"Extracting {len(chunks)} chunks (order {args.from_order}-{args.to_order})...")

        # Merge consecutive chunks into one extraction context. The NPC-list
        # region (name / age / STR / personality / goals) is only meaningful as
        # a whole: a lone attribute line like "30岁男性 STR55" does not name its
        # entity, so per-chunk extraction cannot attribute it to the conductor.
        merged_chunk = {
            "id": f"{args.module}:merged-{args.from_order}-{args.to_order}",
            "title": chunks[0]["title"],
            "text": "\n".join(str(chunk["text"]) for chunk in chunks),
            "visibility": chunks[0]["visibility"],
            "spoiler_tag": chunks[0]["spoiler_tag"],
            "source_locator": chunks[0]["source_locator"],
        }
        extracted_payloads: list[dict] = []
        try:
            for payload in await agent.extract_from_chunk(merged_chunk):
                # The merged context spans several chunks; map each citation's
                # evidence back to the chunk that actually contains it so the
                # evidence-in-source validation passes.
                for citation in payload.get("citations", []):
                    evidence = str(citation.get("evidence_text") or "")
                    owning_chunk = _find_evidence_chunk(evidence, chunks)
                    if owning_chunk is None:
                        continue
                    citation["chunk_id"] = owning_chunk["id"]
                    citation["asset_id"] = None
                extracted_payloads.append(payload)
        except (KeyError, RuntimeError, TypeError, ValueError) as exc:
            print(f"[warn] merged extraction failed: {exc}")

        if not extracted_payloads:
            print("No candidates extracted; nothing to do.")
            return 0

        if args.dry_run:
            print(f"[dry-run] Extracted {len(extracted_payloads)} candidates; no writes.")
            for payload in extracted_payloads:
                print(
                    f"  kind={payload.get('kind')} | entity="
                    f"{payload.get('entity_name')}/{payload.get('entity_type')} | "
                    f"{payload.get('title')}"
                )
            return 0

        # 3. Store candidates as pending (create_manual_candidate validates and
        #    dedups by object_hash).
        stored: list[dict] = []
        for payload in extracted_payloads:
            try:
                candidate = knowledge.create_manual_candidate(args.module, payload)
                stored.append(candidate)
            except (KeyError, TypeError, ValueError) as exc:
                print(f"[skip] store failed for {payload.get('title')}: {exc}")

        if not stored:
            print("No new candidates stored; nothing to do.")
            return 0

        # 4. Approve the new candidates.
        for candidate in stored:
            try:
                knowledge.review_candidate(
                    candidate["id"],
                    decision="approved",
                    member_id=None,
                    note="auto-confirmed on import (ai_kp)",
                )
            except (KeyError, ValueError):
                print(f"[skip] could not approve {candidate['title']}")
        repo.commit()

        # 5. Judge attribution with the existing entity list, then attach.
        existing = repo.list_module_entities(args.module)
        known_ids = {str(item["id"]) for item in stored}
        for index in range(0, len(stored), args.batch_size):
            batch = stored[index : index + args.batch_size]
            try:
                text = await llm.complete(
                    _judgment_prompt(batch, existing), temperature=0.0
                )
            except RuntimeError:
                print("[warn] judgment call failed; candidates stay unattached")
                continue
            decisions = _parse_judgments(text, known_ids)
            for candidate in batch:
                # Deterministic rule first: an exact established entity name in
                # the candidate is stronger evidence than model classification.
                target = _rule_match_candidate(candidate, existing)
                if target is None:
                    decision = decisions.get(str(candidate["id"]))
                    if not decision or not decision["entity_name"]:
                        continue
                    target = next(
                        (
                            item
                            for item in existing
                            if item["name"] == decision["entity_name"]
                        ),
                        None,
                    )
                if target is not None:
                    # Attach candidate to existing entity and append description.
                    try:
                        repo.link_candidate_to_entity(
                            str(target["id"]), str(candidate["id"])
                        )
                        repo.connection.execute(
                            """
                            UPDATE module_entities
                            SET description = description || char(10) || ?,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (str(candidate.get("statement") or "").strip(), target["id"]),
                        )
                        repo.commit()
                        print(
                            f"  ~ attached '{candidate['title']}' to "
                            f"{target['name']}"
                        )
                    except (KeyError, ValueError, sqlite3.IntegrityError) as exc:
                        print(f"  [skip] attach failed for {candidate['title']}: {exc}")
                    continue
                # No existing entity matched: create a new one from the LLM decision.
                entity_type = decision["entity_type"] or "npc"
                entity_name = decision["entity_name"]
                try:
                    created = graph.create_entity(
                        args.module,
                        ModuleEntityCreate(
                            entity_type=entity_type,
                            name=entity_name,
                            description=str(candidate.get("statement") or ""),
                            visibility=str(candidate.get("visibility") or "kp"),
                            spoiler_tag=candidate.get("spoiler_tag"),
                            source_candidate_id=candidate["id"],
                        ),
                        member_id=None,
                    )
                    existing.append(created)
                    repo.commit()
                    print(f"  + created entity {entity_name} ({entity_type})")
                except (KeyError, ValueError, sqlite3.IntegrityError) as exc:
                    print(f"  [skip] create failed for {candidate['title']}: {exc}")
            repo.commit()

        return 0
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract a chunk region and attach new candidates to existing entities."
    )
    parser.add_argument("--module", required=True, help="module_id to process")
    parser.add_argument("--from-order", type=int, required=True)
    parser.add_argument("--to-order", type=int, required=True)
    parser.add_argument("--db-path", default="data/ai_kp.sqlite3")
    parser.add_argument("--base-url", default="http://192.168.1.97:8001/v1")
    parser.add_argument("--api-key", default="local")
    parser.add_argument("--model", default=None, help="override model discovery")
    parser.add_argument("--batch-size", type=int, default=30)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print extracted candidates and stop before writing",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()

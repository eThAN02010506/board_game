"""Canonical scoped context assembly for the AI KP director."""

import json
import math
import re
import sqlite3
from dataclasses import dataclass, replace
from typing import Any

from ai_kp.director.context_sources import (
    InvestigatorContextProvider,
    ModuleContextProvider,
)
from ai_kp.director.prompts import KP_SYSTEM_PROMPT
from ai_kp.director.turn_output import STRUCTURED_OUTPUT_INSTRUCTIONS
from ai_kp.platform.facts import FactLedgerEntry, visible_fact_heads
from ai_kp.platform.memory.npc_candidates import NpcCandidateService
from ai_kp.platform.memory.retrieval import MemoryRetriever, tokenize

CJK_RE = re.compile(r"[\u3400-\u9fff]")


def estimate_tokens(text: str) -> int:
    """Conservative local estimate until a model-specific tokenizer is configured."""
    cjk_count = len(CJK_RE.findall(text))
    other_count = max(0, len(text) - cjk_count)
    return cjk_count + math.ceil(other_count / 4)


@dataclass(frozen=True)
class ContextAssembly:
    messages: list[dict[str, str]]
    included_sources: list[dict]
    excluded_sources: list[dict]
    token_estimate: int
    visibility_scope: str


class ContextBuilder:
    def __init__(self, connection: sqlite3.Connection, max_context_tokens: int = 12000):
        self.connection = connection
        self.max_context_tokens = max_context_tokens

    def build(
        self,
        *,
        campaign_id: str,
        player_action: str,
        pc_id: str | None = None,
        location: str | None = None,
        map_id: str | None = None,
        profession_hint: str | None = None,
        active_spoiler_tags: tuple[str, ...] = (),
        visibility_scope: str = "kp",
        output_instructions: str = STRUCTURED_OUTPUT_INSTRUCTIONS,
        additional_sources: tuple[dict[str, Any], ...] = (),
        skill_instructions: str = "",
    ) -> ContextAssembly:
        if visibility_scope not in {"kp", "player"}:
            raise ValueError("Visibility scope must be kp or player")
        if visibility_scope == "player" and not pc_id:
            raise ValueError("Player context requires an identity-bound PC")
        allowed_visibility = (
            ("player", "table", "kp", "secret")
            if visibility_scope == "kp"
            else ("player", "table")
        )
        included: list[dict] = []
        excluded: list[dict] = []

        campaign = self.connection.execute(
            "SELECT * FROM campaigns WHERE id = ?",
            (campaign_id,),
        ).fetchone()
        if campaign is None:
            raise KeyError(f"Campaign not found: {campaign_id}")
        included.append(
            {
                "kind": "campaign",
                "id": campaign["id"],
                "label": campaign["title"],
                "content": (
                    f"团名：{campaign['title']}；规则：{campaign['system']}；"
                    f"当前时间：{campaign['current_time'] or '未设定'}"
                ),
                "visibility": "table",
                "required": True,
            }
        )
        investigator_sources = InvestigatorContextProvider(self.connection).build(
            campaign_id=campaign_id,
            pc_id=pc_id,
            visibility_scope=visibility_scope,
        )
        included.extend(investigator_sources)

        module_provider = ModuleContextProvider(self.connection)
        module_scope = module_provider.resolve(
            campaign_id=campaign_id,
            requested_spoiler_tags=active_spoiler_tags,
            visibility_scope=visibility_scope,
            included=included,
            excluded=excluded,
            location=location,
        )
        continuity_source = self._latest_approved_action_context(
            campaign_id, module_scope.module_ids
        )
        action_location = module_provider.infer_action_location(
            module_scope.module_ids, player_action
        )
        if action_location and not location:
            module_scope = replace(module_scope, location=action_location)
            included.append(
                {
                    "kind": "explicit_action_location",
                    "id": f"action-location:{action_location}",
                    "label": "当前行动明确地点",
                    "content": f"玩家当前行动明确移动到：{action_location}",
                    "visibility": "table",
                    "required": True,
                }
            )
        retrieval_action = player_action
        if continuity_source is not None:
            included.append(continuity_source)
            if not module_scope.location:
                module_scope = replace(
                    module_scope,
                    location=str(continuity_source.get("location_hint") or "") or None,
                )
            retrieval_action = (
                f"{player_action}\n"
                + (f"当前行动明确地点：{action_location}\n" if action_location else "")
                + "上一轮已确认位置与目标："
                f"{continuity_source['content']}"
            )
        # Put authoritative scenario evidence before model-authored memories and
        # recent narration.  Small models are sensitive to source order; placing
        # prose echoes first made them repeat the last turn even when an exact
        # room or encounter excerpt was available later in the prompt.
        module_provider.add_chunks(
            scope=module_scope,
            player_action=retrieval_action,
            visibility=allowed_visibility,
            included=included,
            excluded=excluded,
        )
        module_provider.add_approved_knowledge(
            scope=module_scope,
            player_action=retrieval_action,
            visibility=allowed_visibility,
            included=included,
            excluded=excluded,
        )
        self._add_world_facts(campaign_id, pc_id, visibility_scope, included)
        self._add_world_entities(
            campaign_id,
            player_action,
            allowed_visibility,
            included,
        )

        memories = MemoryRetriever(self.connection).retrieve(
            player_action,
            campaign_id=campaign_id,
            pc_id=pc_id,
            visibility=allowed_visibility,
            limit=10,
        )
        for item in memories:
            included.append(
                {
                    "kind": "memory",
                    "id": item.id,
                    "label": item.scope,
                    "content": item.text,
                    "visibility": item.visibility,
                    "score": item.score,
                    "score_components": dict(item.score_components),
                }
            )

        if visibility_scope == "kp":
            candidates = NpcCandidateService(self.connection).find_candidates(
                campaign_id=campaign_id,
                action_text=player_action,
                location=location,
                profession_hint=profession_hint,
                limit=4,
            )
            for item in candidates:
                included.append(
                    {
                        "kind": "npc_candidate",
                        "id": item.npc_id,
                        "label": item.name,
                        "content": f"{item.name}：{item.reason}",
                        "visibility": "kp",
                        "score": item.score,
                    }
                )

        self._add_recent_events(campaign_id, allowed_visibility, included)
        self._add_map_context(
            campaign_id,
            map_id,
            allowed_visibility,
            visibility_scope,
            included,
        )
        # Trusted use-case sources (for example deterministic check results)
        # must be considered before optional memories and module excerpts.
        accepted_additional: list[dict[str, Any]] = []
        for source in additional_sources:
            copied = dict(source)
            if copied.get("visibility") not in allowed_visibility:
                copied["excluded_reason"] = "visibility_not_allowed"
                excluded.append(self._without_content(copied))
                continue
            accepted_additional.append(copied)
        included[1:1] = accepted_additional

        system_prompt = KP_SYSTEM_PROMPT
        if skill_instructions.strip():
            system_prompt = f"{system_prompt}\n\n{skill_instructions.strip()}"
        selected, budget_excluded = self._fit_budget(
            player_action,
            included,
            output_instructions,
            system_prompt,
        )
        excluded.extend(budget_excluded)
        sections = "\n\n".join(
            f"[{source['kind']}] id={source['id']} label={source['label']}\n"
            f"{source['content']}"
            for source in selected
        ) or "无可用背景资料"
        user_prompt = f"""玩家行动：
{player_action}

经筛选的事实与候选项：
{sections}

候选 NPC 只表示“可能自然出现”，不得强制安排出场。

{output_instructions}""".strip()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return ContextAssembly(
            messages=messages,
            included_sources=selected,
            excluded_sources=excluded,
            token_estimate=sum(
                estimate_tokens(message["content"]) for message in messages
            ),
            visibility_scope=visibility_scope,
        )

    def _add_world_entities(
        self,
        campaign_id: str,
        player_action: str,
        allowed_visibility: tuple[str, ...],
        included: list[dict],
    ) -> None:
        placeholders = ",".join("?" for _ in allowed_visibility)
        rows = self.connection.execute(
            f"""
            SELECT * FROM campaign_world_entities
            WHERE campaign_id = ? AND visibility IN ({placeholders})
            ORDER BY created_at DESC, id DESC
            """,
            (campaign_id, *allowed_visibility),
        ).fetchall()
        rows_by_id = {str(row["id"]): row for row in rows}
        data_by_id: dict[str, dict] = {}
        state_rows = self.connection.execute(
            f"""
            SELECT state.* FROM campaign_world_entity_states state
            JOIN campaign_world_entities entity ON entity.id = state.entity_id
            WHERE entity.campaign_id = ?
              AND entity.visibility IN ({placeholders})
              AND state.visibility IN ({placeholders})
            ORDER BY state.entity_id, state.dimension
            """,
            (campaign_id, *allowed_visibility, *allowed_visibility),
        ).fetchall()
        states_by_entity: dict[str, list[dict[str, Any]]] = {}
        for state in state_rows:
            try:
                value = json.loads(state["value_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            states_by_entity.setdefault(str(state["entity_id"]), []).append(
                {
                    "dimension": str(state["dimension"]),
                    "value": value,
                    "visibility": str(state["visibility"]),
                    "version": int(state["version"]),
                }
            )
        action_tokens = tokenize(player_action)
        scored: list[tuple[int, Any, dict]] = []
        for row in rows:
            try:
                data = json.loads(row["data_json"])
            except (TypeError, json.JSONDecodeError):
                data = {}
            data_by_id[str(row["id"])] = data
            searchable = " ".join(
                (
                    str(row["name"]),
                    str(row["description"]),
                    str(row["archetype_id"] or ""),
                    str(data.get("label_variant") or ""),
                    " ".join(str(item) for item in data.get("profession_ids") or ()),
                    str(data.get("candidate_subject") or ""),
                    str(data.get("candidate_proposal") or ""),
                    " ".join(
                        f"{item['dimension']} {item['value']}"
                        for item in states_by_entity.get(str(row["id"]), ())
                    ),
                )
            )
            score = len(action_tokens & tokenize(searchable))
            if score:
                scored.append((score, row, data))
        scored.sort(key=lambda item: (-item[0], str(item[1]["id"])))
        selected = scored[:8]
        selected_ids = {str(item[1]["id"]) for item in selected}
        relation_rows = []
        if selected_ids:
            relation_placeholders = ",".join("?" for _ in selected_ids)
            relation_rows = self.connection.execute(
                f"""
                SELECT relation.source_entity_id, relation.relation_slot_id,
                       relation.target_entity_id,
                       source.name AS source_name, target.name AS target_name
                FROM campaign_world_entity_relations relation
                JOIN campaign_world_entities source ON source.id = relation.source_entity_id
                JOIN campaign_world_entities target ON target.id = relation.target_entity_id
                WHERE relation.campaign_id = ?
                  AND source.visibility IN ({placeholders})
                  AND target.visibility IN ({placeholders})
                  AND (relation.source_entity_id IN ({relation_placeholders})
                       OR relation.target_entity_id IN ({relation_placeholders}))
                ORDER BY relation.created_at, relation.id
                """,
                (
                    campaign_id,
                    *allowed_visibility,
                    *allowed_visibility,
                    *selected_ids,
                    *selected_ids,
                ),
            ).fetchall()
        for relation in relation_rows:
            for neighbor_id in (
                str(relation["source_entity_id"]),
                str(relation["target_entity_id"]),
            ):
                if neighbor_id in selected_ids or len(selected) >= 8:
                    continue
                neighbor = rows_by_id.get(neighbor_id)
                if neighbor is None:
                    continue
                selected.append((0, neighbor, data_by_id[neighbor_id]))
                selected_ids.add(neighbor_id)
        for score, row, data in selected:
            entity_id = str(row["id"])
            relationships = [
                {
                    "direction": "outgoing"
                    if item["source_entity_id"] == entity_id
                    else "incoming",
                    "relation_slot_id": item["relation_slot_id"],
                    "other_entity_id": item["target_entity_id"]
                    if item["source_entity_id"] == entity_id
                    else item["source_entity_id"],
                    "other_name": item["target_name"]
                    if item["source_entity_id"] == entity_id
                    else item["source_name"],
                }
                for item in relation_rows
                if entity_id in {item["source_entity_id"], item["target_entity_id"]}
            ]
            included.append(
                {
                    "kind": "campaign_world_entity",
                    "id": entity_id,
                    "label": str(row["name"]),
                    "content": json.dumps(
                        {
                            "entity_id": entity_id,
                            "entity_kind": row["entity_kind"],
                            "archetype_id": row["archetype_id"],
                            "state_version": int(row["state_version"]),
                            "name": row["name"],
                            "description": row["description"],
                            "npc_id": row["npc_id"],
                            "label_variant": data.get("label_variant"),
                            "profession_ids": data.get("profession_ids") or [],
                            "state_dimensions": data.get("state_dimensions") or [],
                            "states": states_by_entity.get(entity_id, []),
                            "relationships": relationships,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "visibility": row["visibility"],
                    "score": score,
                }
            )

    def _latest_approved_action_context(
        self, campaign_id: str, module_ids: tuple[str, ...]
    ) -> dict | None:
        """Return the last ruling plus the last confirmed scenario location."""

        rows = self.connection.execute(
            """
            SELECT p.id AS proposal_id, a.payload_json
            FROM turn_proposals p
            JOIN proposal_actions a ON a.proposal_id = p.id
            WHERE p.campaign_id = ? AND p.status = 'approved'
              AND a.action_type = 'action_ruling'
            ORDER BY p.created_at DESC, a.created_at DESC, a.id DESC
            LIMIT 24
            """,
            (campaign_id,),
        ).fetchall()
        if not rows:
            return None
        parsed: list[tuple[Any, dict]] = []
        for row in rows:
            try:
                parsed.append((row, json.loads(row["payload_json"])))
            except (TypeError, json.JSONDecodeError):
                continue
        if not parsed:
            return None
        row, ruling = parsed[0]
        location_hint = self._latest_confirmed_module_location(parsed, module_ids)
        target = str(ruling.get("target") or "").strip()
        maximum_effect = str(ruling.get("maximum_effect") or "").strip()
        if not target and not maximum_effect:
            return None
        parts = []
        if target:
            parts.append(f"已确认目标/位置：{target}")
        if maximum_effect:
            parts.append(f"已确认效果上限：{maximum_effect}")
        if location_hint and location_hint != target:
            parts.append(f"持续所在场景：{location_hint}")
        return {
            "kind": "approved_action_context",
            "id": str(row["proposal_id"]),
            "label": "上一轮玩家已确认的行动连续性",
            "content": "；".join(parts),
            "visibility": "table",
            "required": True,
            "location_hint": location_hint,
        }

    def _latest_confirmed_module_location(
        self,
        parsed_rulings: list[tuple[Any, dict]],
        module_ids: tuple[str, ...],
    ) -> str:
        if not module_ids:
            return ""
        placeholders = ",".join("?" for _ in module_ids)
        heading_rows = self.connection.execute(
            f"""
            SELECT DISTINCT title FROM module_chunks
            WHERE module_id IN ({placeholders}) AND semantic_kind = 'heading'
            """,
            module_ids,
        ).fetchall()
        headings = tuple(str(row["title"]) for row in heading_rows)
        for _, ruling in parsed_rulings:
            target = str(ruling.get("target") or "").strip()
            target_tokens = tokenize(target)
            if len(target_tokens) < 2:
                continue
            for heading in headings:
                heading_tokens = tokenize(heading)
                overlap = len(target_tokens & heading_tokens)
                if overlap >= max(2, math.ceil(len(heading_tokens) * 0.6)):
                    return heading
        return ""

    def _add_map_context(
        self,
        campaign_id: str,
        map_id: str | None,
        visibility: tuple[str, ...],
        visibility_scope: str,
        included: list[dict],
    ) -> None:
        if map_id:
            if visibility_scope == "player":
                map_row = self.connection.execute(
                    """
                    SELECT * FROM maps
                    WHERE id = ? AND campaign_id = ? AND status = 'published'
                    """,
                    (map_id, campaign_id),
                ).fetchone()
            else:
                map_row = self.connection.execute(
                    "SELECT * FROM maps WHERE id = ? AND campaign_id = ?",
                    (map_id, campaign_id),
                ).fetchone()
            if map_row is None:
                raise ValueError(f"Map {map_id} does not belong to campaign {campaign_id}")
        else:
            status_clause = (
                "AND status = 'published'" if visibility_scope == "player" else ""
            )
            map_row = self.connection.execute(
                f"""
                SELECT * FROM maps
                WHERE campaign_id = ? {status_clause}
                ORDER BY created_at DESC LIMIT 1
                """,
                (campaign_id,),
            ).fetchone()
        if map_row is None:
            return
        included.append(
            {
                "kind": "current_map",
                "id": map_row["id"],
                "label": map_row["title"],
                "content": f"地图风格：{map_row['style']}",
                "visibility": "table",
            }
        )
        placeholders = ",".join("?" for _ in visibility)
        locations = self.connection.execute(
            f"""
            SELECT * FROM map_locations
            WHERE map_id = ? AND visibility IN ({placeholders})
            ORDER BY order_index
            """,
            (map_row["id"], *visibility),
        ).fetchall()
        for row in locations:
            included.append(
                {
                    "kind": "map_location",
                    "id": row["id"],
                    "label": row["name"],
                    "content": row["notes"] or "当前地图可用地点",
                    "visibility": row["visibility"],
                }
            )
        tokens = self.connection.execute(
            f"""
            SELECT t.*, l.name AS location_name
            FROM map_tokens t JOIN map_locations l ON l.id = t.location_id
            WHERE t.map_id = ? AND t.visibility IN ({placeholders})
              AND l.visibility IN ({placeholders})
            ORDER BY t.created_at
            """,
            (map_row["id"], *visibility, *visibility),
        ).fetchall()
        for row in tokens:
            included.append(
                {
                    "kind": "map_token",
                    "id": row["id"],
                    "label": row["label"],
                    "content": (
                        f"类型：{row['actor_type']}；"
                        f"actor_id：{row['actor_id'] or '无'}；"
                        f"位置：{row['location_name']}"
                    ),
                    "visibility": row["visibility"],
                }
            )

    def _add_world_facts(
        self,
        campaign_id: str,
        pc_id: str | None,
        visibility_scope: str,
        included: list[dict],
    ) -> None:
        rows = self.connection.execute(
            """
            SELECT * FROM events
            WHERE campaign_id = ?
              AND event_type IN ('world_fact.asserted', 'world_fact.retconned')
            ORDER BY created_at, id
            """,
            (campaign_id,),
        ).fetchall()
        entries = [FactLedgerEntry.from_event(dict(row)) for row in rows]
        for entry in visible_fact_heads(
            entries,
            role=visibility_scope,
            pc_id=pc_id,
        ):
            owner = (
                f"；仅角色 {entry.fact.pc_id} 的认知"
                if entry.fact.pc_id is not None
                else ""
            )
            included.append(
                {
                    "kind": "world_fact",
                    "id": entry.event_id,
                    "label": f"{entry.fact.category}:{entry.fact.subject}",
                    "content": (
                        f"类别：{entry.fact.category}{owner}；"
                        f"主语：{entry.fact.subject}；"
                        f"关系：{entry.fact.predicate}；"
                        f"内容：{entry.fact.object_text}"
                    ),
                    "visibility": entry.fact.visibility,
                    "fact_key": entry.fact_key,
                    "revision": entry.revision,
                }
            )

    def _add_recent_events(
        self,
        campaign_id: str,
        visibility: tuple[str, ...],
        included: list[dict],
    ) -> None:
        placeholders = ",".join("?" for _ in visibility)
        rows = self.connection.execute(
            f"""
            SELECT * FROM events
            WHERE campaign_id = ? AND visibility IN ({placeholders})
              AND event_type NOT LIKE 'world_fact.%'
            ORDER BY created_at DESC LIMIT 5
            """,
            (campaign_id, *visibility),
        ).fetchall()
        for row in reversed(rows):
            included.append(
                {
                    "kind": "recent_event",
                    "id": row["id"],
                    "label": row["event_type"],
                    "content": row["summary"],
                    "visibility": row["visibility"],
                }
            )

    def _fit_budget(
        self,
        player_action: str,
        sources: list[dict],
        output_instructions: str,
        system_prompt: str,
    ) -> tuple[list[dict], list[dict]]:
        reserved = (
            estimate_tokens(system_prompt)
            + estimate_tokens(player_action)
            + estimate_tokens(output_instructions)
            + 200
        )
        if reserved > self.max_context_tokens:
            raise ValueError(
                "Player action and fixed prompt exceed the configured context budget"
            )
        used = reserved
        selected: list[dict] = []
        excluded: list[dict] = []
        required_sources = [
            source for source in sources if source.get("required") is True
        ]
        optional_sources = [
            source for source in sources if source.get("required") is not True
        ]
        for source in required_sources:
            cost = estimate_tokens(
                f"{source['kind']} {source['id']} {source['label']} "
                f"{source['content']}"
            ) + 8
            if used + cost > self.max_context_tokens:
                raise ValueError(
                    f"Required context source exceeds token budget: {source['kind']}"
                )
            selected.append(source)
            used += cost
        for source in optional_sources:
            cost = estimate_tokens(
                f"{source['kind']} {source['id']} {source['label']} "
                f"{source['content']}"
            ) + 8
            if used + cost > self.max_context_tokens:
                rejected = dict(source)
                rejected["excluded_reason"] = "token_budget"
                excluded.append(self._without_content(rejected))
                continue
            selected.append(source)
            used += cost
        return selected, excluded

    @staticmethod
    def _without_content(source: dict) -> dict:
        return {key: value for key, value in source.items() if key != "content"}

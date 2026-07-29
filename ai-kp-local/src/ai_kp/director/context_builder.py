"""Canonical scoped context assembly for the AI KP director."""

import math
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from ai_kp.director.context_sources import (
    InvestigatorContextProvider,
    ModuleContextProvider,
)
from ai_kp.director.prompts import KP_SYSTEM_PROMPT
from ai_kp.director.turn_output import STRUCTURED_OUTPUT_INSTRUCTIONS
from ai_kp.platform.facts import FactLedgerEntry, visible_fact_heads
from ai_kp.platform.memory.npc_candidates import NpcCandidateService
from ai_kp.platform.memory.retrieval import MemoryRetriever

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
        self._add_world_facts(campaign_id, pc_id, visibility_scope, included)

        module_provider = ModuleContextProvider(self.connection)
        module_scope = module_provider.resolve(
            campaign_id=campaign_id,
            requested_spoiler_tags=active_spoiler_tags,
            visibility_scope=visibility_scope,
            included=included,
            excluded=excluded,
        )
        module_provider.add_approved_knowledge(
            scope=module_scope,
            player_action=player_action,
            visibility=allowed_visibility,
            included=included,
            excluded=excluded,
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
        module_provider.add_chunks(
            scope=module_scope,
            player_action=player_action,
            visibility=allowed_visibility,
            included=included,
            excluded=excluded,
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

        selected, budget_excluded = self._fit_budget(
            player_action,
            included,
            output_instructions,
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
            {"role": "system", "content": KP_SYSTEM_PROMPT},
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
    ) -> tuple[list[dict], list[dict]]:
        reserved = (
            estimate_tokens(KP_SYSTEM_PROMPT)
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

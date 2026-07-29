"""Approved investigator snapshot plus mutable campaign state for AI context."""

from __future__ import annotations

import json
import sqlite3


class InvestigatorContextProvider:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def build(
        self,
        *,
        campaign_id: str,
        pc_id: str | None,
        visibility_scope: str,
    ) -> list[dict]:
        if not pc_id:
            return []
        approved = self.connection.execute(
            """
            SELECT ci.investigator_id, ci.legacy_pc_id, ci.approved_revision_id,
                   ir.canonical_json, ir.public_summary_json,
                   state.current_hp, state.current_san, state.current_mp,
                   state.current_luck, state.conditions_json,
                   state.inventory_delta_json, state.current_game_time,
                   state.state_version
            FROM campaign_investigators ci
            JOIN investigator_revisions ir
              ON ir.id = ci.approved_revision_id
             AND ir.investigator_id = ci.investigator_id
            JOIN investigator_campaign_state state
              ON state.campaign_id = ci.campaign_id
             AND state.investigator_id = ci.investigator_id
             AND state.approved_revision_id = ci.approved_revision_id
            WHERE ci.campaign_id = ? AND ci.legacy_pc_id = ?
              AND ci.approved_revision_id IS NOT NULL
            """,
            (campaign_id, pc_id),
        ).fetchone()
        if approved is not None:
            return self._approved_sources(approved, visibility_scope)
        binding = self.connection.execute(
            """
            SELECT approved_revision_id
            FROM campaign_investigators
            WHERE campaign_id = ? AND legacy_pc_id = ?
            """,
            (campaign_id, pc_id),
        ).fetchone()
        if binding is not None and binding["approved_revision_id"] is not None:
            raise ValueError(
                "Approved investigator binding is inconsistent; "
                "refusing the legacy character fallback"
            )
        return [self._legacy_source(campaign_id, pc_id, visibility_scope)]

    @staticmethod
    def _approved_sources(
        approved: sqlite3.Row,
        visibility_scope: str,
    ) -> list[dict]:
        canonical = json.loads(approved["canonical_json"])
        public_summary = json.loads(approved["public_summary_json"])
        approved_name = str(
            public_summary.get("name")
            or (canonical.get("identity") or {}).get("name")
            or "未命名调查员"
        )
        runtime_state = {
            "current_hp": approved["current_hp"],
            "current_san": approved["current_san"],
            "current_mp": approved["current_mp"],
            "current_luck": approved["current_luck"],
            "conditions": json.loads(approved["conditions_json"]),
            "inventory_delta": json.loads(approved["inventory_delta_json"]),
            "current_game_time": approved["current_game_time"],
            "state_version": approved["state_version"],
        }
        content = (
            "当前玩家角色公开摘要："
            f"{_compact_json(public_summary)}"
        )
        source_visibility = "table"
        if visibility_scope == "kp":
            content = (
                "KP 已批准的不可变角色卡核心投影："
                f"{_compact_json(_core_projection(canonical))}；"
                "当前团内运行状态："
                f"{_compact_json(runtime_state)}"
            )
            source_visibility = "kp"
        sources = [{
            "kind": "player_character",
            "id": approved["investigator_id"],
            "label": approved_name,
            "content": content,
            "visibility": source_visibility,
            "approved_revision_id": approved["approved_revision_id"],
            "legacy_pc_id": approved["legacy_pc_id"],
            "state_version": approved["state_version"],
            "required": True,
        }]
        if visibility_scope == "kp":
            narrative = _narrative_projection(canonical)
            if narrative:
                sources.append(
                    {
                        "kind": "player_character_details",
                        "id": approved["investigator_id"],
                        "label": approved_name,
                        "content": (
                            "KP 已批准的角色背景、关系与经历："
                            f"{_compact_json(narrative)}"
                        ),
                        "visibility": "kp",
                        "approved_revision_id": approved["approved_revision_id"],
                    }
                )
        return sources

    def _legacy_source(
        self,
        campaign_id: str,
        pc_id: str,
        visibility_scope: str,
    ) -> dict:
        row = self.connection.execute(
            "SELECT * FROM player_characters WHERE id = ? AND campaign_id = ?",
            (pc_id, campaign_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"PC {pc_id} does not belong to campaign {campaign_id}")
        content = "当前玩家角色"
        source_visibility = "table"
        if visibility_scope == "kp":
            content = (
                "兼容角色投影："
                f"{json.dumps(json.loads(row['sheet_json']), ensure_ascii=False)}"
            )
            source_visibility = "kp"
        return {
            "kind": "player_character",
            "id": row["id"],
            "label": row["name"],
            "content": content,
            "visibility": source_visibility,
            "compatibility_projection": True,
        }


def _compact_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _core_projection(canonical: dict) -> dict:
    identity = canonical.get("identity")
    characteristics = canonical.get("characteristics")
    derived = canonical.get("derived")
    raw_skills = canonical.get("skills")
    skills = []
    for raw in raw_skills[:160] if isinstance(raw_skills, list) else []:
        if not isinstance(raw, dict):
            continue
        skills.append(
            {
                "name": str(raw.get("display_name") or "")[:200],
                "specialization": str(raw.get("specialization") or "")[:200]
                or None,
                "value": raw.get("current_value"),
                "hard": raw.get("half_value"),
                "extreme": raw.get("fifth_value"),
                "growth_mark": raw.get("growth_mark"),
            }
        )
    return {
        "schema_version": canonical.get("schema_version"),
        "ruleset_id": canonical.get("ruleset_id"),
        "identity": _bounded_collection(identity, 32),
        "characteristics": _bounded_collection(characteristics, 32),
        "derived": _bounded_collection(derived, 32),
        "skills": skills,
        "combat": _bounded_collection(canonical.get("combat"), 32),
        "assets": _bounded_collection(canonical.get("assets"), 64),
    }


def _narrative_projection(canonical: dict) -> dict:
    keys = (
        "background",
        "mythos",
        "relationships",
        "campaign_history",
        "visibility",
    )
    return {
        key: _bounded_collection(canonical[key], 32)
        for key in keys
        if key in canonical and canonical[key] not in ({}, [], None, "")
    }


def _bounded_collection(
    value: object,
    max_items: int,
    *,
    depth: int = 0,
) -> object:
    if depth >= 4:
        return "[nested]"
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, list):
        return [
            _bounded_collection(item, max_items, depth=depth + 1)
            for item in value[:max_items]
        ]
    if isinstance(value, dict):
        return {
            str(key)[:160]: _bounded_collection(
                item,
                max_items,
                depth=depth + 1,
            )
            for key, item in list(value.items())[:max_items]
        }
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:1000]

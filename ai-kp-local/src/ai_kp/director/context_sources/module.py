"""Authoritative current-module, reviewed-knowledge, graph, and evidence sources."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass

from ai_kp.platform.memory.retrieval import tokenize
from ai_kp.platform.modules.knowledge import validate_derived_scope


@dataclass(frozen=True)
class ModuleScope:
    module_ids: tuple[str, ...]
    spoiler_tags: tuple[str, ...]
    current_scene_key: str | None
    run_id: str | None
    explicit: bool
    location: str | None = None


class ModuleContextProvider:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def resolve(
        self,
        *,
        campaign_id: str,
        requested_spoiler_tags: tuple[str, ...],
        visibility_scope: str,
        included: list[dict],
        excluded: list[dict],
        location: str | None = None,
    ) -> ModuleScope:
        active = self.connection.execute(
            """
            SELECT r.*, m.title AS module_title
            FROM campaign_module_runs r
            JOIN modules m ON m.id = r.module_id
            WHERE r.campaign_id = ? AND r.status = 'active'
            """,
            (campaign_id,),
        ).fetchone()
        if active is not None:
            run_tags = tuple(json.loads(active["active_spoiler_tags_json"]))
            run_state = json.loads(active["state_json"])
            effective_tags = self._effective_tags(run_tags, requested_spoiler_tags)
            if visibility_scope == "kp":
                included.append(
                    {
                        "kind": "module_run",
                        "id": active["id"],
                        "label": active["module_title"],
                        "content": (
                            f"当前模组：{active['module_title']}；"
                            f"当前场景：{active['current_scene_key'] or '未指定'}；"
                            "已解锁剧透标签："
                            f"{json.dumps(effective_tags, ensure_ascii=False)}；"
                            "当前完整运行态："
                            f"{json.dumps(run_state, ensure_ascii=False, sort_keys=True)}"
                        ),
                        "visibility": "kp",
                        "module_id": active["module_id"],
                        "required": True,
                    }
                )
            return ModuleScope(
                module_ids=(str(active["module_id"]),),
                spoiler_tags=effective_tags,
                current_scene_key=active["current_scene_key"],
                run_id=active["id"],
                explicit=True,
                location=location,
            )

        modules = self.connection.execute(
            """
            SELECT id, title FROM modules
            WHERE campaign_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (campaign_id,),
        ).fetchall()
        if len(modules) == 1:
            module = modules[0]
            if visibility_scope != "kp":
                excluded.append(
                    {
                        "kind": "module",
                        "id": module["id"],
                        "label": module["title"],
                        "visibility": "kp",
                        "excluded_reason": "active_module_not_selected",
                    }
                )
                return ModuleScope((), (), None, None, False)
            included.append(
                {
                    "kind": "module_scope_fallback",
                    "id": module["id"],
                    "label": module["title"],
                    "content": (
                        "当前团只有一个模组，暂按兼容模式使用；"
                        "KP 应建立显式模组运行以固定场景与剧透水位。"
                    ),
                    "visibility": "kp",
                    "module_id": module["id"],
                }
            )
            return ModuleScope(
                module_ids=(str(module["id"]),),
                spoiler_tags=tuple(requested_spoiler_tags),
                current_scene_key=None,
                run_id=None,
                explicit=False,
                location=location,
            )
        for module in modules:
            excluded.append(
                {
                    "kind": "module",
                    "id": module["id"],
                    "label": module["title"],
                    "visibility": "kp",
                    "excluded_reason": "active_module_not_selected",
                }
            )
        return ModuleScope((), (), None, None, False)

    def infer_action_location(
        self, module_ids: tuple[str, ...], player_action: str
    ) -> str | None:
        """Infer an explicitly named module location from the current action.

        This is deliberately lexical: it identifies a heading the player actually
        named, but never asks a model to invent movement or scenario semantics.
        """

        if not module_ids or not player_action.strip():
            return None
        placeholders = ",".join("?" for _ in module_ids)
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT title FROM module_chunks
            WHERE module_id IN ({placeholders})
            """,
            module_ids,
        ).fetchall()
        action_tokens = tokenize(player_action)
        ranked: list[tuple[int, int, int, int, int, int, str]] = []
        action_cjk = set(re.findall(r"[\u3400-\u9fff]", player_action))
        low_signal_characters = set("的一是在与和及中内外")
        action_ordinals = self._structural_ordinals(player_action)
        for row in rows:
            title = str(row["title"]).strip()
            if not title:
                continue
            title_body = re.split(r"[:：]", title, maxsplit=1)[-1].strip()
            title_tokens = tokenize(title_body)
            overlap = len(action_tokens & title_tokens)
            common = self._longest_common_cjk_run(player_action, title_body)
            character_overlap = len(
                (action_cjk - low_signal_characters)
                & (
                    set(re.findall(r"[\u3400-\u9fff]", title_body))
                    - low_signal_characters
                )
            )
            if common < 3 and overlap < 2:
                continue
            title_ordinals = self._structural_ordinals(title)
            ordinal_match = int(bool(action_ordinals & title_ordinals))
            ordinal_conflict = int(
                bool(action_ordinals and title_ordinals and not ordinal_match)
            )
            # Equal lexical matches should resolve to the shorter, enclosing
            # scene heading instead of a longer nested room that merely repeats
            # the same entity name.
            ranked.append(
                (
                    ordinal_match,
                    -ordinal_conflict,
                    common,
                    character_overlap,
                    overlap,
                    -len(title),
                    title,
                )
            )
        if not ranked:
            return None
        ranked.sort(reverse=True)
        return ranked[0][6]

    @staticmethod
    def _structural_ordinals(text: str) -> set[str]:
        values: set[str] = set()
        for pattern in (
            r"(?:场景|房间|章节)\s*(\d+)",
            r"(\d+)\s*号(?:房间|房|车厢|门|储藏室)?",
        ):
            values.update(re.findall(pattern, text))
        return values

    @staticmethod
    def _longest_common_cjk_run(left: str, right: str) -> int:
        """Return longest shared contiguous CJK run with bounded linear memory."""

        left_cjk = "".join(re.findall(r"[\u3400-\u9fff]", left))
        right_cjk = "".join(re.findall(r"[\u3400-\u9fff]", right))
        if not left_cjk or not right_cjk:
            return 0
        previous = [0] * (len(right_cjk) + 1)
        longest = 0
        for left_char in left_cjk:
            current = [0]
            for index, right_char in enumerate(right_cjk, start=1):
                length = previous[index - 1] + 1 if left_char == right_char else 0
                current.append(length)
                longest = max(longest, length)
            previous = current
        return longest

    def add_approved_knowledge(
        self,
        *,
        scope: ModuleScope,
        player_action: str,
        visibility: tuple[str, ...],
        included: list[dict],
        excluded: list[dict],
    ) -> None:
        if not scope.module_ids:
            return
        module_placeholders = ",".join("?" for _ in scope.module_ids)
        visibility_placeholders = ",".join("?" for _ in visibility)
        query_tokens = self._action_query_tokens(player_action)
        selected_candidate_sources = self._candidate_sources(
            scope,
            query_tokens,
            visibility,
            module_placeholders,
            visibility_placeholders,
            excluded,
        )
        included.extend(selected_candidate_sources)
        selected_entity_sources = self._entity_sources(
            scope,
            query_tokens,
            visibility,
            module_placeholders,
            visibility_placeholders,
            excluded,
        )
        included.extend(selected_entity_sources)
        included.extend(
            self._relation_sources(
                scope,
                query_tokens,
                visibility,
                module_placeholders,
                visibility_placeholders,
                {item["id"] for item in selected_entity_sources},
                excluded,
            )
        )

    def add_chunks(
        self,
        *,
        scope: ModuleScope,
        player_action: str,
        visibility: tuple[str, ...],
        included: list[dict],
        excluded: list[dict],
    ) -> None:
        if not scope.module_ids:
            return
        module_placeholders = ",".join("?" for _ in scope.module_ids)
        visibility_placeholders = ",".join("?" for _ in visibility)
        rows = self.connection.execute(
            f"""
            SELECT mc.* FROM module_chunks mc
            WHERE mc.module_id IN ({module_placeholders})
              AND mc.visibility IN ({visibility_placeholders})
            ORDER BY mc.module_id, mc.order_index
            """,
            (*scope.module_ids, *visibility),
        ).fetchall()
        query_tokens = self._action_query_tokens(player_action)
        current_scene = str(scope.current_scene_key or "")
        current_location = str(scope.location or "").strip()
        section_anchors = {
            (str(row["module_id"]), int(row["order_index"]))
            for row in rows
            if current_location
            and self._confirmed_context_matches(current_location, str(row["title"]))
        }
        ranked: list[dict] = []
        for row in rows:
            style_annotations = tuple(json.loads(row["style_annotations_json"] or "[]"))
            is_scene_baseline = self._player_scene_baseline_allowed(
                semantic_kind=str(row["semantic_kind"]),
                style_annotations=style_annotations,
                text=str(row["text"]),
            )
            source = {
                "kind": "module_scene_baseline" if is_scene_baseline else "module_chunk",
                "id": row["id"],
                "label": row["title"],
                "content": row["text"],
                "visibility": row["visibility"],
                "spoiler_tag": row["spoiler_tag"],
                "module_id": row["module_id"],
                "source_locator": row["source_locator"],
                "semantic_kind": row["semantic_kind"],
                "required": is_scene_baseline,
            }
            if not self._spoiler_visible(row["spoiler_tag"], scope.spoiler_tags):
                source["excluded_reason"] = "spoiler_not_active"
                excluded.append(self._without_content(source))
                continue
            searchable = f"{row['title']} {row['text']} {row['scene_key'] or ''}"
            lexical_overlap = len(query_tokens & tokenize(searchable))
            score = lexical_overlap
            score_components: dict[str, int] = {"lexical_overlap": lexical_overlap}
            if current_scene and row["scene_key"] == current_scene:
                score += 5
                score_components["current_scene"] = 5
            # 玩家所在位置的剧情块优先进上下文：AI 需要知道当前位置发生了什么，
            # 而不是只按玩家行动的关键词匹配（否则会漏掉当前位置的既定剧情节点）。
            if current_location and current_location in searchable:
                score += 10
                score_components["exact_location"] = 10
            confirmed_context_match = bool(
                current_location
                and self._confirmed_context_matches(current_location, searchable)
            )
            section_distance = self._following_section_distance(
                str(row["module_id"]),
                int(row["order_index"]),
                section_anchors,
            )
            if section_distance is not None:
                # Rules and reactions are commonly placed immediately after a
                # location's boxed/read-aloud text under child headings. Keep a
                # bounded structural neighbourhood so retrieval does not discard
                # them merely because the child heading has a different title.
                confirmed_context_match = True
                section_bonus = max(2, 10 - section_distance // 2)
                score += section_bonus
                score_components["section_neighbourhood"] = section_bonus
            if confirmed_context_match:
                score += 10
                score_components["confirmed_context"] = 10
            if score == 0:
                source["excluded_reason"] = "not_relevant"
                excluded.append(self._without_content(source))
                continue
            source["score"] = score
            source["score_components"] = score_components
            source["_confirmed_context_match"] = confirmed_context_match
            source["_section_distance"] = section_distance
            ranked.append(source)
        ranked.sort(key=self._source_rank_key)
        confirmed_matches = [
            source for source in ranked if source["_confirmed_context_match"]
        ]
        if confirmed_matches:
            for source in ranked:
                if source["_confirmed_context_match"]:
                    continue
                source.pop("_confirmed_context_match", None)
                source.pop("_section_distance", None)
                source["excluded_reason"] = "outside_confirmed_action_context"
                excluded.append(self._without_content(source))
            ranked = confirmed_matches
        for source in ranked:
            source.pop("_confirmed_context_match", None)
            source.pop("_section_distance", None)
        included.extend(ranked[:8])
        for source in ranked[8:]:
            source["excluded_reason"] = "module_chunk_limit"
            excluded.append(self._without_content(source))

    @staticmethod
    def _action_query_tokens(player_action: str) -> set[str]:
        """Tokenize the action without injecting scenario-specific semantics."""

        return tokenize(player_action)

    @staticmethod
    def _confirmed_context_matches(context_target: str, searchable: str) -> bool:
        """Match a confirmed prior target without scenario-specific vocabulary."""

        target_tokens = tokenize(context_target)
        if len(target_tokens) < 2:
            return False
        overlap = len(target_tokens & tokenize(searchable))
        return overlap >= max(2, math.ceil(len(target_tokens) * 0.6))

    @staticmethod
    def _following_section_distance(
        module_id: str,
        order_index: int,
        anchors: set[tuple[str, int]],
        *,
        max_distance: int = 12,
    ) -> int | None:
        distances = (
            order_index - anchor_index
            for anchor_module_id, anchor_index in anchors
            if anchor_module_id == module_id
            and 0 <= order_index - anchor_index <= max_distance
        )
        return min(distances, default=None)

    @staticmethod
    def _source_rank_key(source: dict) -> tuple[bool, int, int, str]:
        section_distance = source.get("_section_distance")
        return (
            section_distance is None,
            int(section_distance) if section_distance is not None else 10_000,
            -int(source["score"]),
            str(source["id"]),
        )

    @staticmethod
    def _player_scene_baseline_allowed(
        *,
        semantic_kind: str,
        style_annotations: tuple[str, ...],
        text: str,
    ) -> bool:
        """Accept formatted read-aloud text while excluding role-only guidance."""

        if semantic_kind != "text" or "italic" not in style_annotations:
            return False
        role_only_markers = re.compile(
            r"(?:给\s*(?:守密人|主持人|kp)\s*的?\s*(?:提示|建议)|"
            r"(?:守密人|主持人|keeper|game\s*master|\bkp\b)\s*"
            r"(?:应当|应该|可以|必须|需要|注意|提示|建议))",
            re.IGNORECASE,
        )
        return role_only_markers.search(text) is None

    def _candidate_sources(
        self,
        scope: ModuleScope,
        query_tokens: set[str],
        visibility: tuple[str, ...],
        module_placeholders: str,
        visibility_placeholders: str,
        excluded: list[dict],
    ) -> list[dict]:
        rows = self.connection.execute(
            f"""
            SELECT * FROM module_knowledge_candidates
            WHERE module_id IN ({module_placeholders})
              AND status = 'approved'
              AND visibility IN ({visibility_placeholders})
            ORDER BY
              CASE kind WHEN 'module_anchor' THEN 0
                        WHEN 'module_canon' THEN 1 ELSE 2 END,
              created_at, id
            """,
            (*scope.module_ids, *visibility),
        ).fetchall()
        ranked: list[tuple[int, dict]] = []
        current_scene = str(scope.current_scene_key or "")
        for row in rows:
            source = {
                "kind": row["kind"],
                "id": row["id"],
                "label": row["title"],
                "content": "",
                "visibility": row["visibility"],
                "spoiler_tag": row["spoiler_tag"],
                "module_id": row["module_id"],
            }
            if not self._spoiler_visible(row["spoiler_tag"], scope.spoiler_tags):
                source["excluded_reason"] = "spoiler_not_active"
                excluded.append(self._without_content(source))
                continue
            locators, citation_exclusion = self._candidate_citation_scope(
                candidate_id=str(row["id"]),
                module_id=str(row["module_id"]),
                candidate_visibility=str(row["visibility"]),
                candidate_spoiler_tag=row["spoiler_tag"],
                allowed_visibility=visibility,
                active_spoiler_tags=scope.spoiler_tags,
            )
            if citation_exclusion:
                source["excluded_reason"] = citation_exclusion
                excluded.append(self._without_content(source))
                continue
            source["content"] = (
                f"已由 KP 批准：{row['statement']}"
                f"；理由：{row['rationale'] or '无'}"
                f"；原文位置：{json.dumps(locators, ensure_ascii=False)}"
            )
            searchable = f"{row['title']} {row['statement']} {row['rationale']}"
            score = len(query_tokens & tokenize(searchable))
            if current_scene and current_scene in searchable:
                score += 4
            score += {"module_anchor": 3, "module_canon": 2}.get(row["kind"], 0)
            if score == 0:
                source["excluded_reason"] = "not_relevant"
                excluded.append(self._without_content(source))
                continue
            ranked.append((score, source))
        ranked.sort(key=lambda item: (-item[0], item[1]["id"]))
        for _, source in ranked[8:]:
            source["excluded_reason"] = "module_knowledge_limit"
            excluded.append(self._without_content(source))
        return [item[1] for item in ranked[:8]]

    def _entity_sources(
        self,
        scope: ModuleScope,
        query_tokens: set[str],
        visibility: tuple[str, ...],
        module_placeholders: str,
        visibility_placeholders: str,
        excluded: list[dict],
    ) -> list[dict]:
        rows = self.connection.execute(
            f"""
            SELECT e.*,
                   source.visibility AS source_visibility,
                   source.spoiler_tag AS source_spoiler_tag
            FROM module_entities e
            JOIN module_knowledge_candidates source
              ON source.id = e.source_candidate_id
            WHERE e.module_id IN ({module_placeholders})
              AND source.status = 'approved'
              AND source.module_id = e.module_id
              AND e.visibility IN ({visibility_placeholders})
              AND source.visibility IN ({visibility_placeholders})
            ORDER BY e.entity_type, e.name, e.id
            """,
            (*scope.module_ids, *visibility, *visibility),
        ).fetchall()
        statements_by_entity: dict[str, list[dict]] = {}
        entity_ids = [str(row["id"]) for row in rows]
        if entity_ids:
            entity_placeholders = ",".join("?" for _ in entity_ids)
            statement_rows = self.connection.execute(
                f"""
                SELECT ec.entity_id, c.statement, c.spoiler_tag
                FROM module_entity_candidates ec
                JOIN module_entities linked_entity ON linked_entity.id = ec.entity_id
                JOIN module_knowledge_candidates c ON c.id = ec.candidate_id
                WHERE ec.entity_id IN ({entity_placeholders})
                  AND c.module_id = linked_entity.module_id
                  AND c.status = 'approved'
                  AND c.visibility IN ({visibility_placeholders})
                ORDER BY ec.entity_id, c.created_at, c.id
                """,
                (*entity_ids, *visibility),
            ).fetchall()
            for statement_row in statement_rows:
                statements_by_entity.setdefault(
                    str(statement_row["entity_id"]), []
                ).append(statement_row)
        ranked: list[tuple[int, dict]] = []
        current_scene = str(scope.current_scene_key or "")
        for row in rows:
            # Aggregate every approved statement attached to this entity via the
            # junction table so the AI sees all confirmed facts (shock, bite
            # mark, keys, devouring) about the same world entity.
            related_statements = [
                str(statement_row["statement"])
                for statement_row in statements_by_entity.get(str(row["id"]), ())
                if self._spoiler_visible(
                    statement_row["spoiler_tag"], scope.spoiler_tags
                )
            ]
            related_facts = ""
            if related_statements:
                related_facts = "；相关事实：" + "；".join(
                    statement for statement in related_statements if statement
                )
            source = {
                "kind": "module_entity",
                "id": row["id"],
                "label": row["name"],
                "content": (
                    f"类型：{row['entity_type']}；描述：{row['description'] or '无'}；"
                    f"批准来源：{row['source_candidate_id']}"
                    f"{related_facts}"
                ),
                "visibility": row["visibility"],
                "spoiler_tag": row["spoiler_tag"],
                "module_id": row["module_id"],
                "source_candidate_id": row["source_candidate_id"],
            }
            _, citation_exclusion = self._candidate_citation_scope(
                candidate_id=str(row["source_candidate_id"]),
                module_id=str(row["module_id"]),
                candidate_visibility=str(row["source_visibility"]),
                candidate_spoiler_tag=row["source_spoiler_tag"],
                allowed_visibility=visibility,
                active_spoiler_tags=scope.spoiler_tags,
            )
            if citation_exclusion:
                source["excluded_reason"] = citation_exclusion
                excluded.append(self._without_content(source))
                continue
            if any(
                not self._spoiler_visible(tag, scope.spoiler_tags)
                for tag in (row["spoiler_tag"], row["source_spoiler_tag"])
            ):
                source["excluded_reason"] = "spoiler_not_active"
                excluded.append(self._without_content(source))
                continue
            searchable = (
                f"{row['name']} {row['description']} "
                f"{' '.join(related_statements)}"
            )
            score = len(query_tokens & tokenize(searchable))
            if current_scene and current_scene in searchable:
                score += 4
            if row["entity_type"] == "anchor":
                score += 2
            if score == 0:
                source["excluded_reason"] = "not_relevant"
                excluded.append(self._without_content(source))
                continue
            ranked.append((score, source))
        ranked.sort(key=lambda item: (-item[0], item[1]["id"]))
        for _, source in ranked[8:]:
            source["excluded_reason"] = "module_entity_limit"
            excluded.append(self._without_content(source))
        return [item[1] for item in ranked[:8]]

    def _relation_sources(
        self,
        scope: ModuleScope,
        query_tokens: set[str],
        visibility: tuple[str, ...],
        module_placeholders: str,
        visibility_placeholders: str,
        selected_entity_ids: set[str],
        excluded: list[dict],
    ) -> list[dict]:
        rows = self.connection.execute(
            f"""
            SELECT r.*,
                   source.name AS source_name,
                   source.visibility AS source_visibility,
                   source.spoiler_tag AS source_spoiler_tag,
                   source.source_candidate_id AS source_evidence_id,
                   target.name AS target_name,
                   target.visibility AS target_visibility,
                   target.spoiler_tag AS target_spoiler_tag,
                   target.source_candidate_id AS target_evidence_id,
                   evidence.visibility AS evidence_visibility,
                   evidence.spoiler_tag AS evidence_spoiler_tag,
                   source_evidence.visibility AS source_evidence_visibility,
                   source_evidence.spoiler_tag AS source_evidence_spoiler_tag,
                   target_evidence.visibility AS target_evidence_visibility,
                   target_evidence.spoiler_tag AS target_evidence_spoiler_tag
            FROM module_entity_relations r
            JOIN module_entities source ON source.id = r.source_entity_id
            JOIN module_entities target ON target.id = r.target_entity_id
            JOIN module_knowledge_candidates evidence
              ON evidence.id = r.source_candidate_id
            JOIN module_knowledge_candidates source_evidence
              ON source_evidence.id = source.source_candidate_id
            JOIN module_knowledge_candidates target_evidence
              ON target_evidence.id = target.source_candidate_id
            WHERE r.module_id IN ({module_placeholders})
              AND evidence.status = 'approved'
              AND source_evidence.status = 'approved'
              AND target_evidence.status = 'approved'
              AND evidence.module_id = r.module_id
              AND source_evidence.module_id = r.module_id
              AND target_evidence.module_id = r.module_id
              AND r.visibility IN ({visibility_placeholders})
              AND source.visibility IN ({visibility_placeholders})
              AND target.visibility IN ({visibility_placeholders})
              AND evidence.visibility IN ({visibility_placeholders})
              AND source_evidence.visibility IN ({visibility_placeholders})
              AND target_evidence.visibility IN ({visibility_placeholders})
              AND source.module_id = r.module_id
              AND target.module_id = r.module_id
            ORDER BY source.name, r.predicate, target.name, r.id
            """,
            (
                *scope.module_ids,
                *visibility,
                *visibility,
                *visibility,
                *visibility,
                *visibility,
                *visibility,
            ),
        ).fetchall()
        selected: list[dict] = []
        for row in rows:
            source = {
                "kind": "module_relation",
                "id": row["id"],
                "label": row["predicate"],
                "content": (
                    f"{row['source_name']} --{row['predicate']}→ "
                    f"{row['target_name']}；备注：{row['note'] or '无'}；"
                    f"批准来源：{row['source_candidate_id']}"
                ),
                "visibility": row["visibility"],
                "spoiler_tag": row["spoiler_tag"],
                "module_id": row["module_id"],
                "source_candidate_id": row["source_candidate_id"],
            }
            citation_scopes = (
                (
                    row["source_candidate_id"],
                    row["evidence_visibility"],
                    row["evidence_spoiler_tag"],
                ),
                (
                    row["source_evidence_id"],
                    row["source_evidence_visibility"],
                    row["source_evidence_spoiler_tag"],
                ),
                (
                    row["target_evidence_id"],
                    row["target_evidence_visibility"],
                    row["target_evidence_spoiler_tag"],
                ),
            )
            citation_exclusion = next(
                (
                    reason
                    for candidate_id, candidate_visibility, candidate_spoiler_tag
                    in citation_scopes
                    if (
                        reason := self._candidate_citation_scope(
                            candidate_id=str(candidate_id),
                            module_id=str(row["module_id"]),
                            candidate_visibility=str(candidate_visibility),
                            candidate_spoiler_tag=candidate_spoiler_tag,
                            allowed_visibility=visibility,
                            active_spoiler_tags=scope.spoiler_tags,
                        )[1]
                    )
                ),
                None,
            )
            if citation_exclusion:
                source["excluded_reason"] = citation_exclusion
                excluded.append(self._without_content(source))
                continue
            spoiler_tags = (
                row["spoiler_tag"],
                row["source_spoiler_tag"],
                row["target_spoiler_tag"],
                row["evidence_spoiler_tag"],
                row["source_evidence_spoiler_tag"],
                row["target_evidence_spoiler_tag"],
            )
            if any(
                not self._spoiler_visible(tag, scope.spoiler_tags)
                for tag in spoiler_tags
            ):
                source["excluded_reason"] = "spoiler_not_active"
                excluded.append(self._without_content(source))
                continue
            endpoint_selected = (
                row["source_entity_id"] in selected_entity_ids
                or row["target_entity_id"] in selected_entity_ids
            )
            score = len(
                query_tokens
                & tokenize(
                    f"{row['source_name']} {row['predicate']} "
                    f"{row['target_name']} {row['note']}"
                )
            )
            if not endpoint_selected and score == 0:
                source["excluded_reason"] = "not_relevant"
                excluded.append(self._without_content(source))
                continue
            if len(selected) >= 8:
                source["excluded_reason"] = "module_relation_limit"
                excluded.append(self._without_content(source))
                continue
            selected.append(source)
        return selected

    def _candidate_citation_scope(
        self,
        *,
        candidate_id: str,
        module_id: str,
        candidate_visibility: str,
        candidate_spoiler_tag: str | None,
        allowed_visibility: tuple[str, ...],
        active_spoiler_tags: tuple[str, ...],
    ) -> tuple[list[str], str | None]:
        citations = self.connection.execute(
            """
            SELECT citation.source_locator,
                   CASE
                     WHEN citation.chunk_id IS NOT NULL THEN chunk.module_id
                     ELSE asset.module_id
                   END AS source_module_id,
                   CASE
                     WHEN citation.chunk_id IS NOT NULL THEN chunk.visibility
                     ELSE asset.visibility
                   END AS source_visibility,
                   CASE
                     WHEN citation.chunk_id IS NOT NULL THEN chunk.spoiler_tag
                     ELSE asset.spoiler_tag
                   END AS source_spoiler_tag
            FROM module_knowledge_citations citation
            LEFT JOIN module_chunks chunk ON chunk.id = citation.chunk_id
            LEFT JOIN module_assets asset ON asset.id = citation.asset_id
            WHERE citation.candidate_id = ?
            ORDER BY citation.source_locator, citation.evidence_hash
            """,
            (candidate_id,),
        ).fetchall()
        if not citations:
            return [], "citation_source_invalid"
        locators: list[str] = []
        for citation in citations:
            if citation["source_module_id"] != module_id:
                return [], "citation_source_invalid"
            source_visibility = citation["source_visibility"]
            try:
                validate_derived_scope(
                    source_visibility=str(source_visibility),
                    source_spoiler_tag=citation["source_spoiler_tag"],
                    derived_visibility=candidate_visibility,
                    derived_spoiler_tag=candidate_spoiler_tag,
                    label="Candidate",
                )
            except (KeyError, ValueError):
                return [], "citation_scope_invalid"
            if source_visibility not in allowed_visibility:
                return [], "citation_scope_not_visible"
            if not self._spoiler_visible(
                citation["source_spoiler_tag"],
                active_spoiler_tags,
            ):
                return [], "spoiler_not_active"
            locators.append(str(citation["source_locator"]))
        return locators, None

    @staticmethod
    def _effective_tags(
        run_tags: tuple[str, ...],
        requested_tags: tuple[str, ...],
    ) -> tuple[str, ...]:
        if not requested_tags:
            return run_tags
        requested = set(requested_tags)
        return tuple(tag for tag in run_tags if tag in requested)

    @staticmethod
    def _spoiler_visible(
        spoiler_tag: str | None,
        active_spoiler_tags: tuple[str, ...],
    ) -> bool:
        return not spoiler_tag or spoiler_tag in active_spoiler_tags

    @staticmethod
    def _without_content(source: dict) -> dict:
        return {key: value for key, value in source.items() if key != "content"}

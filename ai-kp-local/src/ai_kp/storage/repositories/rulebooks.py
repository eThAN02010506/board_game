from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.storage.rows import decode_json_field, row_to_dict
from ai_kp.storage.sqlite import SQLiteRepository


class RulebookRepository(SQLiteRepository):
    def create_rule_source(
        self,
        *,
        ruleset_id: str,
        title: str,
        source_filename: str,
        source_hash: str,
        page_count: int,
        metadata: dict[str, Any],
    ) -> dict:
        existing = self.connection.execute(
            "SELECT id FROM rule_sources WHERE source_hash = ?", (source_hash,)
        ).fetchone()
        if existing is not None:
            return self.get_rule_source(str(existing["id"]))
        source_id = new_id("rulesource")
        self.connection.execute(
            """
            INSERT INTO rule_sources
              (id, ruleset_id, title, source_filename, source_hash, page_count, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                ruleset_id,
                title,
                source_filename,
                source_hash,
                page_count,
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        return self.get_rule_source(source_id)

    def get_rule_source(self, source_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM rule_sources WHERE id = ?", (source_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Rule source not found: {source_id}")
        result = row_to_dict(row)
        result["metadata"] = decode_json_field(result.pop("metadata_json"), {})
        result["chunk_count"] = self.connection.execute(
            "SELECT COUNT(*) FROM rule_chunks WHERE source_id = ?", (source_id,)
        ).fetchone()[0]
        result["rule_count"] = self.connection.execute(
            "SELECT COUNT(*) FROM rule_objects WHERE source_id = ?", (source_id,)
        ).fetchone()[0]
        return result

    def list_rule_sources(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT id FROM rule_sources ORDER BY created_at DESC, id"
        ).fetchall()
        return [self.get_rule_source(str(row["id"])) for row in rows]

    def find_rule_source(self, ruleset_id: str) -> dict:
        row = self.connection.execute(
            """
            SELECT id FROM rule_sources WHERE ruleset_id = ?
            ORDER BY CASE status WHEN 'ready' THEN 0 ELSE 1 END, updated_at DESC, id DESC
            LIMIT 1
            """,
            (ruleset_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Rule source not found for ruleset: {ruleset_id}")
        return self.get_rule_source(str(row["id"]))

    def set_rule_source_status(self, source_id: str, status: str) -> None:
        cursor = self.connection.execute(
            """
            UPDATE rule_sources SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (status, source_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"Rule source not found: {source_id}")

    def create_ingestion_run(
        self,
        source_id: str,
        *,
        stage: str,
        agent_model: str | None = None,
        prompt_version: str | None = None,
    ) -> dict:
        self.get_rule_source(source_id)
        run_id = new_id("ruleingest")
        self.connection.execute(
            """
            INSERT INTO rule_ingestion_runs
              (id, source_id, status, stage, agent_model, prompt_version)
            VALUES (?, ?, 'running', ?, ?, ?)
            """,
            (run_id, source_id, stage, agent_model, prompt_version),
        )
        return self.get_ingestion_run(run_id)

    def update_ingestion_run(self, run_id: str, **changes: Any) -> dict:
        allowed = {
            "status",
            "stage",
            "cursor_page",
            "processed_count",
            "accepted_count",
            "rejected_count",
            "error_text",
        }
        fields = [(key, value) for key, value in changes.items() if key in allowed]
        if fields:
            assignments = ", ".join(f"{key} = ?" for key, _value in fields)
            self.connection.execute(
                f"UPDATE rule_ingestion_runs SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (*[value for _key, value in fields], run_id),
            )
        return self.get_ingestion_run(run_id)

    def get_ingestion_run(self, run_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM rule_ingestion_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Rule ingestion run not found: {run_id}")
        return row_to_dict(row)

    def latest_ingestion_run(self, source_id: str) -> dict | None:
        row = self.connection.execute(
            """
            SELECT id FROM rule_ingestion_runs
            WHERE source_id = ? ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (source_id,),
        ).fetchone()
        return self.get_ingestion_run(str(row["id"])) if row else None

    def replace_rule_chunks(self, source_id: str, chunks: list[dict[str, Any]]) -> None:
        self.get_rule_source(source_id)
        if self.connection.execute(
            "SELECT COUNT(*) FROM rule_objects WHERE source_id = ?", (source_id,)
        ).fetchone()[0]:
            raise ValueError("Cannot replace chunks after rule objects have been extracted")
        self.connection.execute("DELETE FROM rule_chunks WHERE source_id = ?", (source_id,))
        for chunk in chunks:
            self.connection.execute(
                """
                INSERT INTO rule_chunks
                  (id, source_id, page_start, page_end, order_index, chapter, section,
                   content_kind, audience, text, text_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk["id"],
                    source_id,
                    chunk["page_start"],
                    chunk["page_end"],
                    chunk["order_index"],
                    chunk.get("chapter"),
                    chunk.get("section"),
                    chunk.get("content_kind", "text"),
                    chunk.get("audience", "all"),
                    chunk["text"],
                    chunk["text_hash"],
                ),
            )

    def list_rule_chunks(
        self,
        source_id: str,
        *,
        extraction_status: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        query = "SELECT * FROM rule_chunks WHERE source_id = ?"
        parameters: list[Any] = [source_id]
        if extraction_status:
            query += " AND extraction_status = ?"
            parameters.append(extraction_status)
        query += " ORDER BY order_index"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)
        return [row_to_dict(row) for row in self.connection.execute(query, parameters)]

    def get_rule_chunk(self, chunk_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM rule_chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Rule chunk not found: {chunk_id}")
        return row_to_dict(row)

    def mark_rule_chunk(
        self,
        chunk_id: str,
        *,
        extraction_status: str | None = None,
        minirag_doc_id: str | None = None,
    ) -> None:
        if extraction_status is not None:
            self.connection.execute(
                "UPDATE rule_chunks SET extraction_status = ? WHERE id = ?",
                (extraction_status, chunk_id),
            )
        if minirag_doc_id is not None:
            self.connection.execute(
                "UPDATE rule_chunks SET minirag_doc_id = ? WHERE id = ?",
                (minirag_doc_id, chunk_id),
            )

    def reset_failed_rule_chunks(self, source_id: str) -> int:
        cursor = self.connection.execute(
            """
            UPDATE rule_chunks SET extraction_status = 'pending'
            WHERE source_id = ? AND extraction_status = 'failed'
            """,
            (source_id,),
        )
        return cursor.rowcount

    def store_rule_object(
        self,
        source_id: str,
        payload: dict[str, Any],
        *,
        status: str,
        validation: dict[str, Any],
    ) -> dict:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        object_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        same = self.connection.execute(
            "SELECT id FROM rule_objects WHERE source_id = ? AND object_hash = ?",
            (source_id, object_hash),
        ).fetchone()
        if same:
            return self.get_rule_object(str(same["id"]))
        version = self.connection.execute(
            """
            SELECT COALESCE(MAX(version), 0) + 1 FROM rule_objects
            WHERE source_id = ? AND rule_key = ?
            """,
            (source_id, payload["rule_key"]),
        ).fetchone()[0]
        object_id = new_id("rule")
        self.connection.execute(
            """
            INSERT INTO rule_objects
              (id, source_id, rule_key, version, rule_type, title, status, object_json,
               object_hash, confidence, validation_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                object_id,
                source_id,
                payload["rule_key"],
                version,
                payload["rule_type"],
                payload["title"],
                status,
                canonical,
                object_hash,
                payload["confidence"],
                json.dumps(validation, ensure_ascii=False),
            ),
        )
        for citation in payload["citations"]:
            evidence = citation["evidence_text"].strip()
            evidence_hash = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
            self.connection.execute(
                """
                INSERT INTO rule_object_citations
                  (rule_object_id, chunk_id, page, evidence_text, evidence_hash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (object_id, citation["chunk_id"], citation["page"], evidence, evidence_hash),
            )
        return self.get_rule_object(object_id)

    def record_rule_validation_issue(
        self,
        source_id: str,
        *,
        validation_layer: str,
        error_text: str,
        chunk_id: str | None = None,
        run_id: str | None = None,
        candidate: Any = None,
    ) -> dict:
        issue_id = new_id("ruleissue")
        self.connection.execute(
            """
            INSERT INTO rule_validation_issues
              (id, source_id, chunk_id, run_id, validation_layer, error_text, candidate_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                issue_id,
                source_id,
                chunk_id,
                run_id,
                validation_layer,
                error_text[:4000],
                json.dumps(candidate, ensure_ascii=False) if candidate is not None else None,
            ),
        )
        row = self.connection.execute(
            "SELECT * FROM rule_validation_issues WHERE id = ?", (issue_id,)
        ).fetchone()
        return row_to_dict(row)

    def list_rule_validation_issues(self, source_id: str, limit: int = 100) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM rule_validation_issues WHERE source_id = ?
            ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (source_id, limit),
        ).fetchall()
        results = []
        for row in rows:
            item = row_to_dict(row)
            item["candidate"] = decode_json_field(item.pop("candidate_json"), None)
            results.append(item)
        return results

    def get_rule_object(self, object_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM rule_objects WHERE id = ?", (object_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Rule object not found: {object_id}")
        result = row_to_dict(row)
        result["object"] = decode_json_field(result.pop("object_json"), {})
        result["validation"] = decode_json_field(result.pop("validation_json"), {})
        return result

    def list_rule_objects(
        self,
        source_id: str,
        *,
        status: str | None = None,
        rule_key: str | None = None,
    ) -> list[dict]:
        filters = ["source_id = ?"]
        parameters: list[Any] = [source_id]
        if status:
            filters.append("status = ?")
            parameters.append(status)
        if rule_key:
            filters.append("rule_key = ?")
            parameters.append(rule_key)
        rows = self.connection.execute(
            f"SELECT id FROM rule_objects WHERE {' AND '.join(filters)} ORDER BY rule_key, version DESC",
            parameters,
        ).fetchall()
        return [self.get_rule_object(str(row["id"])) for row in rows]

    def latest_validated_rule(self, source_id: str, rule_key: str) -> dict:
        row = self.connection.execute(
            """
            SELECT id FROM rule_objects
            WHERE source_id = ? AND rule_key = ? AND status = 'validated'
            ORDER BY version DESC LIMIT 1
            """,
            (source_id, rule_key),
        ).fetchone()
        if row is None:
            raise KeyError(f"Validated rule not found: {rule_key}")
        return self.get_rule_object(str(row["id"]))

    def search_validated_rules(
        self, source_id: str, query: str, *, audience: str, limit: int = 8
    ) -> list[dict]:
        terms = [term for term in re.split(r"[\s，。！？、]+", query.casefold()) if term]
        objects = self.list_rule_objects(source_id, status="validated")
        visible = [
            item
            for item in objects
            if item["object"].get("audience", "all") in {"all", audience}
        ]
        for item in visible:
            payload = item["object"]
            haystack = " ".join(
                [
                    str(payload.get("rule_key", "")),
                    str(payload.get("title", "")),
                    str(payload.get("summary", "")),
                    " ".join(payload.get("tags") or []),
                ]
            ).casefold()
            item["score"] = sum(haystack.count(term) for term in terms)
        return sorted(visible, key=lambda item: (-item["score"], item["rule_key"]))[:limit]

    def lexical_rule_chunks(self, source_id: str, query: str, limit: int = 8) -> list[dict]:
        normalized_query = re.sub(r"[^\w\u3400-\u9fff]+", "", query.casefold())
        terms = [
            normalized_query[index : index + size]
            for size in (2, 3, 4)
            for index in range(max(0, len(normalized_query) - size + 1))
        ]
        chunks = self.list_rule_chunks(source_id)
        for chunk in chunks:
            haystack = f"{chunk.get('chapter') or ''} {chunk.get('section') or ''} {chunk['text']}".lower()
            chunk["score"] = sum(haystack.count(term) * len(term) for term in terms)
        ranked = sorted(chunks, key=lambda item: (-item["score"], item["order_index"]))
        return [chunk for chunk in ranked if chunk["score"] > 0][:limit]

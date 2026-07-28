"""Migration 17: searchable module evidence, image analysis, and KP review."""

import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 17
NAME = "add_module_knowledge_review"


def migrate(connection: sqlite3.Connection) -> None:
    for table, column, definition in (
        (
            "module_chunks",
            "knowledge_status",
            (
                "TEXT NOT NULL DEFAULT 'pending' "
                "CHECK (knowledge_status IN "
                "('pending','processing','completed','failed'))"
            ),
        ),
        ("module_assets", "spoiler_tag", "TEXT"),
        ("module_assets", "analysis_error", "TEXT"),
        ("module_assets", "analysis_prompt_version", "TEXT"),
        ("module_assets", "analyzed_at", "TEXT"),
    ):
        ensure_column(connection, table, column, definition)

    statements = (
        """
        CREATE TABLE IF NOT EXISTS module_knowledge_candidates (
          id TEXT PRIMARY KEY,
          module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
          kind TEXT NOT NULL
            CHECK (kind IN ('module_canon', 'module_anchor', 'reference')),
          title TEXT NOT NULL,
          statement TEXT NOT NULL,
          rationale TEXT NOT NULL DEFAULT '',
          confidence REAL NOT NULL DEFAULT 0 CHECK (confidence BETWEEN 0 AND 1),
          visibility TEXT NOT NULL DEFAULT 'kp'
            CHECK (visibility IN ('player', 'table', 'kp', 'secret')),
          spoiler_tag TEXT,
          status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'approved', 'rejected')),
          object_hash TEXT NOT NULL,
          created_by TEXT NOT NULL DEFAULT 'ai' CHECK (created_by IN ('ai', 'human_kp')),
          source_model TEXT,
          prompt_version TEXT,
          review_note TEXT,
          reviewed_by_member_id TEXT REFERENCES session_members(id) ON DELETE SET NULL,
          reviewed_at TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(module_id, object_hash)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS module_knowledge_citations (
          candidate_id TEXT NOT NULL
            REFERENCES module_knowledge_candidates(id) ON DELETE CASCADE,
          chunk_id TEXT REFERENCES module_chunks(id) ON DELETE CASCADE,
          asset_id TEXT REFERENCES module_assets(id) ON DELETE CASCADE,
          evidence_text TEXT NOT NULL,
          evidence_hash TEXT NOT NULL,
          source_locator TEXT NOT NULL,
          PRIMARY KEY (candidate_id, evidence_hash),
          CHECK (
            (chunk_id IS NOT NULL AND asset_id IS NULL)
            OR (chunk_id IS NULL AND asset_id IS NOT NULL)
          )
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_module_knowledge_status
          ON module_knowledge_candidates(module_id, status, created_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_module_knowledge_citations_chunk
          ON module_knowledge_citations(chunk_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_module_knowledge_citations_asset
          ON module_knowledge_citations(asset_id)
        """,
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS module_search_fts USING fts5(
          source_type UNINDEXED,
          source_id UNINDEXED,
          module_id UNINDEXED,
          visibility UNINDEXED,
          spoiler_tag UNINDEXED,
          source_locator UNINDEXED,
          title,
          text,
          tokenize='trigram'
        )
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_module_chunks_search_insert
        AFTER INSERT ON module_chunks BEGIN
          INSERT INTO module_search_fts
            (source_type, source_id, module_id, visibility, spoiler_tag,
             source_locator, title, text)
          VALUES
            ('chunk', new.id, new.module_id, new.visibility, new.spoiler_tag,
             COALESCE(new.source_locator, ''), new.title, new.text);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_module_chunks_search_delete
        AFTER DELETE ON module_chunks BEGIN
          DELETE FROM module_search_fts
          WHERE source_type = 'chunk' AND source_id = old.id;
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_module_chunks_search_update
        AFTER UPDATE ON module_chunks BEGIN
          DELETE FROM module_search_fts
          WHERE source_type = 'chunk' AND source_id = old.id;
          INSERT INTO module_search_fts
            (source_type, source_id, module_id, visibility, spoiler_tag,
             source_locator, title, text)
          VALUES
            ('chunk', new.id, new.module_id, new.visibility, new.spoiler_tag,
             COALESCE(new.source_locator, ''), new.title, new.text);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_module_assets_search_insert
        AFTER INSERT ON module_assets BEGIN
          INSERT INTO module_search_fts
            (source_type, source_id, module_id, visibility, spoiler_tag,
             source_locator, title, text)
          VALUES
            ('asset', new.id, new.module_id, new.visibility, new.spoiler_tag,
             new.source_locator,
             COALESCE(new.nearby_heading, '图片'),
             TRIM(COALESCE(new.ocr_text, '') || char(10) ||
                  COALESCE(new.visual_summary, '')));
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_module_assets_search_delete
        AFTER DELETE ON module_assets BEGIN
          DELETE FROM module_search_fts
          WHERE source_type = 'asset' AND source_id = old.id;
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_module_assets_search_update
        AFTER UPDATE ON module_assets BEGIN
          DELETE FROM module_search_fts
          WHERE source_type = 'asset' AND source_id = old.id;
          INSERT INTO module_search_fts
            (source_type, source_id, module_id, visibility, spoiler_tag,
             source_locator, title, text)
          VALUES
            ('asset', new.id, new.module_id, new.visibility, new.spoiler_tag,
             new.source_locator,
             COALESCE(new.nearby_heading, '图片'),
             TRIM(COALESCE(new.ocr_text, '') || char(10) ||
                  COALESCE(new.visual_summary, '')));
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_module_knowledge_search_insert
        AFTER INSERT ON module_knowledge_candidates
        WHEN new.status = 'approved' BEGIN
          INSERT INTO module_search_fts
            (source_type, source_id, module_id, visibility, spoiler_tag,
             source_locator, title, text)
          VALUES
            ('knowledge', new.id, new.module_id, new.visibility, new.spoiler_tag,
             'knowledge:' || new.id, new.title, new.statement);
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_module_knowledge_search_delete
        AFTER DELETE ON module_knowledge_candidates BEGIN
          DELETE FROM module_search_fts
          WHERE source_type = 'knowledge' AND source_id = old.id;
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_module_knowledge_search_update
        AFTER UPDATE ON module_knowledge_candidates BEGIN
          DELETE FROM module_search_fts
          WHERE source_type = 'knowledge' AND source_id = old.id;
          INSERT INTO module_search_fts
            (source_type, source_id, module_id, visibility, spoiler_tag,
             source_locator, title, text)
          SELECT
            'knowledge', new.id, new.module_id, new.visibility, new.spoiler_tag,
            'knowledge:' || new.id, new.title, new.statement
          WHERE new.status = 'approved';
        END
        """,
    )
    for statement in statements:
        connection.execute(statement)

    connection.execute("DELETE FROM module_search_fts")
    connection.execute(
        """
        INSERT INTO module_search_fts
          (source_type, source_id, module_id, visibility, spoiler_tag,
           source_locator, title, text)
        SELECT 'chunk', id, module_id, visibility, spoiler_tag,
               COALESCE(source_locator, ''), title, text
        FROM module_chunks
        """
    )
    connection.execute(
        """
        INSERT INTO module_search_fts
          (source_type, source_id, module_id, visibility, spoiler_tag,
           source_locator, title, text)
        SELECT 'asset', id, module_id, visibility, spoiler_tag,
               source_locator,
               COALESCE(nearby_heading, '图片'),
               TRIM(COALESCE(ocr_text, '') || char(10) ||
                    COALESCE(visual_summary, ''))
        FROM module_assets
        """
    )
    connection.execute(
        """
        INSERT INTO module_search_fts
          (source_type, source_id, module_id, visibility, spoiler_tag,
           source_locator, title, text)
        SELECT 'knowledge', id, module_id, visibility, spoiler_tag,
               'knowledge:' || id, title, statement
        FROM module_knowledge_candidates
        WHERE status = 'approved'
        """
    )

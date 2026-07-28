"""Migration 16: durable PDF/DOCX module import jobs and provenance."""

import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 16
NAME = "add_module_document_imports"


def migrate(connection: sqlite3.Connection) -> None:
    for table, column, definition in (
        ("modules", "source_filename", "TEXT"),
        ("modules", "source_hash", "TEXT"),
        ("modules", "source_storage_path", "TEXT"),
        ("modules", "parser_version", "TEXT"),
        ("module_chunks", "content_kind", "TEXT NOT NULL DEFAULT 'text'"),
        ("module_chunks", "page_start", "INTEGER"),
        ("module_chunks", "page_end", "INTEGER"),
        ("module_chunks", "paragraph_start", "INTEGER"),
        ("module_chunks", "paragraph_end", "INTEGER"),
        ("module_chunks", "source_locator", "TEXT"),
    ):
        ensure_column(connection, table, column, definition)

    statements = (
        """
        CREATE TABLE IF NOT EXISTS module_import_jobs (
          id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
          title TEXT NOT NULL,
          source_filename TEXT NOT NULL,
          source_type TEXT NOT NULL CHECK (source_type IN ('pdf', 'docx')),
          source_hash TEXT NOT NULL,
          source_storage_path TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
          stage TEXT NOT NULL DEFAULT 'queued',
          progress_current INTEGER NOT NULL DEFAULT 0,
          progress_total INTEGER NOT NULL DEFAULT 0,
          attempt_count INTEGER NOT NULL DEFAULT 0,
          error_text TEXT,
          module_id TEXT REFERENCES modules(id) ON DELETE SET NULL,
          parser_version TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS module_assets (
          id TEXT PRIMARY KEY,
          module_id TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
          content_hash TEXT NOT NULL,
          storage_path TEXT NOT NULL,
          mime_type TEXT NOT NULL,
          width INTEGER,
          height INTEGER,
          source_locator TEXT NOT NULL,
          nearby_heading TEXT,
          visibility TEXT NOT NULL DEFAULT 'kp'
            CHECK (visibility IN ('player', 'table', 'kp', 'secret')),
          analysis_status TEXT NOT NULL DEFAULT 'pending_analysis'
            CHECK (analysis_status IN ('pending_analysis', 'completed', 'failed')),
          ocr_text TEXT,
          visual_summary TEXT,
          analysis_model TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_module_import_jobs_campaign_created
          ON module_import_jobs(campaign_id, created_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_module_import_jobs_status
          ON module_import_jobs(status, updated_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_module_assets_module
          ON module_assets(module_id, created_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_module_assets_hash
          ON module_assets(content_hash)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_module_chunks_source_locator
          ON module_chunks(module_id, source_locator)
        """,
    )
    for statement in statements:
        connection.execute(statement)

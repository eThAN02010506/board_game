"""Migration 7: rulebook knowledge."""

import sqlite3


VERSION = 7
NAME = "add_rulebook_dual_storage"


STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS rule_sources (
      id TEXT PRIMARY KEY,
      ruleset_id TEXT NOT NULL,
      title TEXT NOT NULL,
      source_filename TEXT NOT NULL,
      source_hash TEXT NOT NULL UNIQUE,
      page_count INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'extracted'
        CHECK (status IN ('extracting', 'extracted', 'indexing', 'ready', 'failed')),
      metadata_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rule_ingestion_runs (
      id TEXT PRIMARY KEY,
      source_id TEXT NOT NULL REFERENCES rule_sources(id) ON DELETE CASCADE,
      status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
      stage TEXT NOT NULL,
      cursor_page INTEGER NOT NULL DEFAULT 0,
      agent_model TEXT,
      prompt_version TEXT,
      processed_count INTEGER NOT NULL DEFAULT 0,
      accepted_count INTEGER NOT NULL DEFAULT 0,
      rejected_count INTEGER NOT NULL DEFAULT 0,
      error_text TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rule_chunks (
      id TEXT PRIMARY KEY,
      source_id TEXT NOT NULL REFERENCES rule_sources(id) ON DELETE CASCADE,
      page_start INTEGER NOT NULL,
      page_end INTEGER NOT NULL,
      order_index INTEGER NOT NULL,
      chapter TEXT,
      section TEXT,
      content_kind TEXT NOT NULL DEFAULT 'text' CHECK (content_kind IN ('text', 'table')),
      audience TEXT NOT NULL DEFAULT 'all' CHECK (audience IN ('all', 'player', 'kp')),
      text TEXT NOT NULL,
      text_hash TEXT NOT NULL,
      extraction_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (extraction_status IN ('pending', 'processing', 'completed', 'failed')),
      minirag_doc_id TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(source_id, order_index),
      UNIQUE(source_id, text_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rule_objects (
      id TEXT PRIMARY KEY,
      source_id TEXT NOT NULL REFERENCES rule_sources(id) ON DELETE CASCADE,
      rule_key TEXT NOT NULL,
      version INTEGER NOT NULL DEFAULT 1,
      rule_type TEXT NOT NULL,
      title TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'validated', 'review_required', 'quarantined')),
      object_json TEXT NOT NULL,
      object_hash TEXT NOT NULL,
      confidence REAL NOT NULL DEFAULT 0,
      validation_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(source_id, rule_key, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rule_object_citations (
      rule_object_id TEXT NOT NULL REFERENCES rule_objects(id) ON DELETE CASCADE,
      chunk_id TEXT NOT NULL REFERENCES rule_chunks(id) ON DELETE CASCADE,
      page INTEGER NOT NULL,
      evidence_text TEXT NOT NULL,
      evidence_hash TEXT NOT NULL,
      PRIMARY KEY (rule_object_id, chunk_id, evidence_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rule_relations (
      id TEXT PRIMARY KEY,
      source_id TEXT NOT NULL REFERENCES rule_sources(id) ON DELETE CASCADE,
      source_rule_key TEXT NOT NULL,
      target_rule_key TEXT NOT NULL,
      relation_type TEXT NOT NULL,
      evidence_chunk_id TEXT REFERENCES rule_chunks(id) ON DELETE SET NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(source_id, source_rule_key, target_rule_key, relation_type)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rule_validation_issues (
      id TEXT PRIMARY KEY,
      source_id TEXT NOT NULL REFERENCES rule_sources(id) ON DELETE CASCADE,
      chunk_id TEXT REFERENCES rule_chunks(id) ON DELETE SET NULL,
      run_id TEXT REFERENCES rule_ingestion_runs(id) ON DELETE SET NULL,
      validation_layer TEXT NOT NULL,
      error_text TEXT NOT NULL,
      candidate_json TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rule_sources_ruleset ON rule_sources(ruleset_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_rule_chunks_source_page ON rule_chunks(source_id, page_start, order_index)",
    "CREATE INDEX IF NOT EXISTS idx_rule_chunks_status ON rule_chunks(source_id, extraction_status)",
    "CREATE INDEX IF NOT EXISTS idx_rule_objects_key_status ON rule_objects(source_id, rule_key, status)",
    "CREATE INDEX IF NOT EXISTS idx_rule_citations_chunk ON rule_object_citations(chunk_id)",
    "CREATE INDEX IF NOT EXISTS idx_rule_validation_issues_source ON rule_validation_issues(source_id, validation_layer)",
)


def migrate(connection: sqlite3.Connection) -> None:
    for statement in STATEMENTS:
        connection.execute(statement)

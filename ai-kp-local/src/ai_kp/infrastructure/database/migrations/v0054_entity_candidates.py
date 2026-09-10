"""Migration 54: module entity multi-source attribution."""

import sqlite3

from ai_kp.core.ids import new_id

VERSION = 54
NAME = "module_entity_candidates"

DDL = """
CREATE TABLE IF NOT EXISTS module_entity_candidates (
  id TEXT PRIMARY KEY,
  entity_id TEXT NOT NULL REFERENCES module_entities(id) ON DELETE CASCADE,
  candidate_id TEXT NOT NULL
    REFERENCES module_knowledge_candidates(id) ON DELETE RESTRICT,
  role TEXT NOT NULL DEFAULT 'source'
    CHECK (role IN ('source', 'reference')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(entity_id, candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_module_entity_candidates_entity
  ON module_entity_candidates(entity_id);
CREATE INDEX IF NOT EXISTS idx_module_entity_candidates_candidate
  ON module_entity_candidates(candidate_id);
"""


def migrate(connection: sqlite3.Connection) -> None:
    for statement in DDL.split(";"):
        if statement.strip():
            connection.execute(statement)
    # Candidates may carry an entity attribution produced at extraction time.
    # Both columns are optional so pre-existing candidates keep validating.
    # ALTER TABLE ADD COLUMN has no IF NOT EXISTS, so guard explicitly for
    # fresh databases where the schema already includes the columns.
    existing = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(module_knowledge_candidates)"
        )
    }
    if "entity_name" not in existing:
        connection.execute(
            "ALTER TABLE module_knowledge_candidates ADD COLUMN entity_name TEXT"
        )
    if "entity_type" not in existing:
        connection.execute(
            "ALTER TABLE module_knowledge_candidates ADD COLUMN entity_type TEXT"
        )
    existing_links = {
        (str(row["entity_id"]), str(row["candidate_id"]))
        for row in connection.execute(
            "SELECT entity_id, candidate_id FROM module_entity_candidates"
        )
    }
    for row in connection.execute(
        "SELECT id, source_candidate_id FROM module_entities"
    ):
        link = (str(row["id"]), str(row["source_candidate_id"]))
        if link in existing_links:
            continue
        connection.execute(
            """
            INSERT INTO module_entity_candidates
              (id, entity_id, candidate_id, role)
            VALUES (?, ?, ?, 'source')
            """,
            (new_id("modentcand"), *link),
        )

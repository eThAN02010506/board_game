"""Migration 19: source-preserving scenario document structure hints."""

import sqlite3

from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 19
NAME = "add_module_document_structure"


def migrate(connection: sqlite3.Connection) -> None:
    for table, name, definition in (
        ("module_chunks", "semantic_kind", "TEXT NOT NULL DEFAULT 'text'"),
        ("module_chunks", "classification_confidence", "REAL NOT NULL DEFAULT 0"),
        ("module_chunks", "style_annotations_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("module_chunks", "review_flags_json", "TEXT NOT NULL DEFAULT '[]'"),
        ("module_assets", "asset_role", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("module_assets", "classification_confidence", "REAL NOT NULL DEFAULT 0"),
        ("module_assets", "review_flags_json", "TEXT NOT NULL DEFAULT '[]'"),
    ):
        ensure_column(connection, table, name, definition)
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_module_chunks_semantic_kind
          ON module_chunks(module_id, semantic_kind, order_index)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_module_assets_role
          ON module_assets(module_id, asset_role)
        """
    )

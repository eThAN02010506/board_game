"""Migration 24: scope source-content identity to one ruleset."""

import sqlite3

VERSION = 24
NAME = "scope_rule_source_hash_by_ruleset"

_TABLE_NAME = "rule_sources"
_NEW_TABLE_NAME = "rule_sources_v24"
_RULESET_HASH_INDEX = "idx_rule_sources_ruleset_hash"


def _unique_indexes(connection: sqlite3.Connection) -> dict[str, list[str]]:
    indexes: dict[str, list[str]] = {}
    for row in connection.execute(
        "SELECT name, \"unique\" FROM pragma_index_list(?)",
        (_TABLE_NAME,),
    ):
        if not int(row[1]):
            continue
        index_name = str(row[0])
        indexes[index_name] = [
            str(column[0])
            for column in connection.execute(
                "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                (index_name,),
            )
        ]
    return indexes


def migrate(connection: sqlite3.Connection) -> None:
    table = connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = ?",
        (_TABLE_NAME,),
    ).fetchone()
    if table is None:
        raise RuntimeError("rule_sources must exist before migration 24")

    unique_indexes = _unique_indexes(connection)
    global_hash_indexes = {
        name for name, columns in unique_indexes.items() if columns == ["source_hash"]
    }
    has_ruleset_hash_index = any(
        columns == ["ruleset_id", "source_hash"]
        for columns in unique_indexes.values()
    )

    if not global_hash_indexes:
        if not has_ruleset_hash_index:
            connection.execute(
                f"""
                CREATE UNIQUE INDEX {_RULESET_HASH_INDEX}
                  ON rule_sources(ruleset_id, source_hash)
                """
            )
        return

    if connection.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError(
            "migration 24 requires foreign key enforcement to be disabled "
            "before its savepoint starts"
        )

    preserved_schema_objects = [
        str(row[1])
        for row in connection.execute(
            """
            SELECT name, sql
            FROM sqlite_schema
            WHERE tbl_name = ?
              AND type IN ('index', 'trigger')
              AND sql IS NOT NULL
            ORDER BY type, name
            """,
            (_TABLE_NAME,),
        )
        if str(row[0]) not in global_hash_indexes
    ]
    source_count = int(
        connection.execute("SELECT COUNT(*) FROM rule_sources").fetchone()[0]
    )

    connection.execute(
        f"""
        CREATE TABLE {_NEW_TABLE_NAME} (
          id TEXT PRIMARY KEY,
          ruleset_id TEXT NOT NULL,
          title TEXT NOT NULL,
          source_filename TEXT NOT NULL,
          source_hash TEXT NOT NULL,
          page_count INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'extracted'
            CHECK (status IN ('extracting', 'extracted', 'indexing', 'ready', 'failed')),
          metadata_json TEXT NOT NULL DEFAULT '{{}}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        f"""
        INSERT INTO {_NEW_TABLE_NAME}
          (id, ruleset_id, title, source_filename, source_hash, page_count,
           status, metadata_json, created_at, updated_at)
        SELECT
          id, ruleset_id, title, source_filename, source_hash, page_count,
          status, metadata_json, created_at, updated_at
        FROM rule_sources
        """
    )
    copied_count = int(
        connection.execute(
            f"SELECT COUNT(*) FROM {_NEW_TABLE_NAME}"
        ).fetchone()[0]
    )
    if copied_count != source_count:
        raise RuntimeError(
            "rule source table rebuild copied an unexpected number of rows"
        )

    connection.execute("DROP TABLE rule_sources")
    connection.execute(
        f"ALTER TABLE {_NEW_TABLE_NAME} RENAME TO {_TABLE_NAME}"
    )
    for statement in preserved_schema_objects:
        connection.execute(statement)
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_rule_sources_ruleset
          ON rule_sources(ruleset_id, status)
        """
    )
    connection.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {_RULESET_HASH_INDEX}
          ON rule_sources(ruleset_id, source_hash)
        """
    )

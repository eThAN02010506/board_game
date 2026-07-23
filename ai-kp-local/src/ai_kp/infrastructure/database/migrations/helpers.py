"""Helpers shared by database migrations."""

import sqlite3


def ensure_column(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    """Add a column when upgrading a database created by an older release."""

    columns = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing_names = {
        str(row["name"] if isinstance(row, sqlite3.Row) else row[1]) for row in columns
    }
    if column_name not in existing_names:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

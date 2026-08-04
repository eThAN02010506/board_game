"""Migration 51: remember the revision behind the last public map publish."""

import sqlite3

VERSION = 51
NAME = "map_published_revision"


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE maps ADD COLUMN published_revision_id TEXT "
        "REFERENCES map_revisions(id) ON DELETE SET NULL"
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_maps_published_revision
        ON maps(published_revision_id)
        """
    )
    connection.execute(
        """
        UPDATE maps
        SET published_revision_id = current_revision_id
        WHERE status = 'published' AND published_revision_id IS NULL
        """
    )

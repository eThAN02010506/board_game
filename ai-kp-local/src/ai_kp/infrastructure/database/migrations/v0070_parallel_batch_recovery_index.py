"""Index session-scoped parallel batch recovery queries."""

import sqlite3

VERSION = 70
NAME = "parallel_batch_recovery_index"


def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_parallel_action_batches_recovery
          ON parallel_action_batches(
            campaign_id, session_id, status, updated_at, id
          )
        """
    )

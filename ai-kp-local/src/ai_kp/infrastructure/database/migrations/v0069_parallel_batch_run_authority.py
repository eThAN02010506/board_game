"""Freeze AI-control authority for the lifetime of a parallel action batch."""

from __future__ import annotations

import json
import sqlite3

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.migrations.helpers import ensure_column

VERSION = 69
NAME = "parallel_batch_run_authority"


def migrate(connection: sqlite3.Connection) -> None:
    """Add the run-version fence and fail closed for legacy active batches.

    A v68 batch did not record the ``campaign_module_runs.version`` used while
    its model plans were produced.  There is no safe value to reconstruct for
    any unsettled batch, so the upgrade preserves it for audit but permanently
    supersedes it.  Replanning creates a fresh batch with a real version fence.
    """

    existing_columns = {
        str(row["name"] if isinstance(row, sqlite3.Row) else row[1])
        for row in connection.execute(
            "PRAGMA table_info(parallel_action_batches)"
        ).fetchall()
    }
    if "module_run_version" in existing_columns:
        return

    active = connection.execute(
        """
        SELECT id, status, version, attention_reason
        FROM parallel_action_batches
        WHERE status NOT IN ('settled', 'superseded')
        ORDER BY created_at, id
        """
    ).fetchall()
    ensure_column(
        connection,
        "parallel_action_batches",
        "module_run_version",
        "INTEGER NOT NULL DEFAULT 0 CHECK (module_run_version >= 0)",
    )
    connection.execute(
        """
        UPDATE parallel_action_batches
        SET module_run_version = COALESCE(
              (SELECT r.version FROM campaign_module_runs r
               WHERE r.id = parallel_action_batches.run_id),
              0
            )
        """
    )
    reason = "Batch predates the durable run-authority fence and must be re-planned."
    for row in active:
        batch_id = str(row["id"] if isinstance(row, sqlite3.Row) else row[0])
        _dispose_legacy_batch_items(connection, batch_id=batch_id)
        prior_status = str(
            row["status"] if isinstance(row, sqlite3.Row) else row[1]
        )
        old_version = int(
            row["version"] if isinstance(row, sqlite3.Row) else row[2]
        )
        prior_reason = str(
            row["attention_reason"] if isinstance(row, sqlite3.Row) else row[3]
        ).strip()
        if prior_status == "committing":
            attention_version = old_version + 1
            connection.execute(
                """
                UPDATE parallel_action_batches
                SET status = 'needs_attention', version = ?, attention_reason = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND version = ?
                """,
                (attention_version, prior_reason or reason, batch_id, old_version),
            )
            _append_migration_event(
                connection,
                batch_id=batch_id,
                version=attention_version,
                event_type="request_attention",
                reason=prior_reason or reason,
            )
            old_version = attention_version
        next_version = old_version + 1
        connection.execute(
            """
            UPDATE parallel_action_batches
            SET status = 'superseded', version = ?, attention_reason = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ?
            """,
            (next_version, prior_reason or reason, batch_id, old_version),
        )
        _append_migration_event(
            connection,
            batch_id=batch_id,
            version=next_version,
            event_type="supersede",
            reason=prior_reason or reason,
        )


def _dispose_legacy_batch_items(
    connection: sqlite3.Connection,
    *,
    batch_id: str,
) -> None:
    """Close every mutable v68 surface before the batch becomes terminal.

    The batch audit event records why this happened.  Per-item history remains
    queryable, but old player endpoints can no longer confirm an adjudication,
    approve a draft, resolve a pending action, or roll an abandoned check.
    """

    connection.execute(
        """
        UPDATE skill_checks
        SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
        WHERE status = 'requested' AND player_action_id IN (
          SELECT action_id FROM parallel_action_batch_items WHERE batch_id = ?
        )
        """,
        (batch_id,),
    )
    connection.execute(
        """
        UPDATE turn_proposals
        SET status = 'rejected'
        WHERE status = 'draft' AND id IN (
          SELECT proposal_id FROM parallel_action_batch_items WHERE batch_id = ?
        )
        """,
        (batch_id,),
    )
    connection.execute(
        """
        UPDATE player_action_adjudications
        SET status = 'superseded', updated_at = CURRENT_TIMESTAMP
        WHERE status IN ('pending', 'confirmed') AND id IN (
          SELECT adjudication_id
          FROM parallel_action_batch_items WHERE batch_id = ?
        )
        """,
        (batch_id,),
    )
    connection.execute(
        """
        UPDATE player_actions
        SET status = 'rejected', resolved_at = CURRENT_TIMESTAMP
        WHERE status = 'reviewed' AND id IN (
          SELECT action_id FROM parallel_action_batch_items WHERE batch_id = ?
        )
        """,
        (batch_id,),
    )


def _append_migration_event(
    connection: sqlite3.Connection,
    *,
    batch_id: str,
    version: int,
    event_type: str,
    reason: str,
) -> None:
    connection.execute(
        """
        INSERT INTO parallel_action_batch_events
          (id, batch_id, version, event_type, actor_member_id, payload_json)
        VALUES (?, ?, ?, ?, NULL, ?)
        """,
        (
            new_id("parallelbatchevent"),
            batch_id,
            version,
            event_type,
            json.dumps(
                {"migration": VERSION, "reason": reason},
                ensure_ascii=False,
                sort_keys=True,
            ),
        ),
    )


__all__ = ["NAME", "VERSION", "migrate"]

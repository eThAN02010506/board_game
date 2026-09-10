"""Authoritative outcome-fishing guard for parallel batch abandonment."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

_KNOWN_RESULT_BLOCK_REASON = (
    "A linked skill check already has a known result; resume and settle this "
    "batch instead."
)


@dataclass(frozen=True)
class ParallelActionAbandonmentDecision:
    """Safe coordinator projection of an authority-only result check."""

    abandon_allowed: bool
    abandon_block_reason: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ParallelActionAbandonmentAuthority:
    """Detect terminal results across every check in a batch's push family."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def evaluate(self, batch_id: str) -> ParallelActionAbandonmentDecision:
        return self.evaluate_many((batch_id,))[batch_id]

    def evaluate_many(
        self, batch_ids: tuple[str, ...] | list[str]
    ) -> dict[str, ParallelActionAbandonmentDecision]:
        normalized = tuple(dict.fromkeys(str(item).strip() for item in batch_ids))
        if not normalized:
            return {}
        if any(not item for item in normalized) or len(normalized) > 100:
            raise ValueError("Abandonment authority requires 1-100 batch IDs")
        values = ", ".join("(?)" for _ in normalized)
        rows = self.connection.execute(
            f"""
            WITH RECURSIVE
              requested_batches(batch_id) AS (VALUES {values}),
              target_batches(batch_id) AS (
                SELECT batch.id
                FROM parallel_action_batches batch
                JOIN requested_batches requested
                  ON requested.batch_id = batch.id
              ),
              linked_checks(batch_id, check_id) AS (
                SELECT target.batch_id, skill_check.id
                FROM target_batches target
                JOIN parallel_action_batch_items item
                  ON item.batch_id = target.batch_id
                JOIN skill_checks skill_check
                  ON skill_check.player_action_id = item.action_id
                 AND skill_check.proposal_id = item.proposal_id
                UNION
                SELECT linked.batch_id, child.id
                FROM linked_checks linked
                JOIN skill_checks child
                  ON child.pushed_from_check_id = linked.check_id
              )
            SELECT
              target.batch_id,
              EXISTS (
                SELECT 1
                FROM linked_checks linked
                JOIN skill_checks skill_check ON skill_check.id = linked.check_id
                WHERE linked.batch_id = target.batch_id
                  AND skill_check.status IN ('resolved', 'overridden')
              ) AS has_known_result
            FROM target_batches target
            """,
            normalized,
        ).fetchall()
        if len(rows) != len(normalized):
            found = {str(row["batch_id"]) for row in rows}
            missing = next(item for item in normalized if item not in found)
            raise KeyError(f"Parallel action batch not found: {missing}")
        return {
            str(row["batch_id"]): self._decision(bool(row["has_known_result"]))
            for row in rows
        }

    @staticmethod
    def _decision(has_known_result: bool) -> ParallelActionAbandonmentDecision:
        return ParallelActionAbandonmentDecision(
            abandon_allowed=not has_known_result,
            abandon_block_reason=(
                _KNOWN_RESULT_BLOCK_REASON if has_known_result else ""
            ),
        )


__all__ = [
    "ParallelActionAbandonmentAuthority",
    "ParallelActionAbandonmentDecision",
]

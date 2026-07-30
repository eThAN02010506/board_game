"""SQLite persistence for replayable dynamic-branch aggregates."""

from __future__ import annotations

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class DynamicBranchRepository(SQLiteRepository):
    def create_dynamic_branch_run(
        self,
        *,
        proposal_id: str,
        campaign_id: str,
        module_run_id: str,
        plan: dict[str, Any],
        member_id: str,
    ) -> dict[str, Any]:
        existing = self.get_dynamic_branch_for_proposal(proposal_id)
        if existing is not None:
            return existing
        branch_id = new_id("branch")
        self.connection.execute(
            """
            INSERT INTO dynamic_branch_runs
              (id, proposal_id, campaign_id, module_run_id, plan_json,
               created_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                branch_id,
                proposal_id,
                campaign_id,
                module_run_id,
                json.dumps(plan, ensure_ascii=False, sort_keys=True),
                member_id,
            ),
        )
        self._insert_dynamic_branch_event(
            branch_id=branch_id,
            event_seq=1,
            event_type="approved",
            beat_id=None,
            outcome=None,
            note="approved world-expansion branch plan",
            payload={"proposal_id": proposal_id},
            member_id=member_id,
            aggregate_version=0,
            command_id=f"approval:{proposal_id}",
        )
        return self.get_dynamic_branch_run(branch_id)

    def get_dynamic_branch_run(self, branch_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM dynamic_branch_runs WHERE id = ?",
            (branch_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Dynamic branch not found: {branch_id}")
        result = row_to_dict(row)
        result["plan"] = decode_json_field(result.pop("plan_json"), {})
        result["events"] = self._list_dynamic_branch_events(branch_id)
        return result

    def get_dynamic_branch_for_proposal(
        self,
        proposal_id: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT id FROM dynamic_branch_runs WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        return self.get_dynamic_branch_run(str(row["id"])) if row is not None else None

    def list_dynamic_branch_runs(
        self,
        campaign_id: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if status is None:
            rows = self.connection.execute(
                """
                SELECT id FROM dynamic_branch_runs
                WHERE campaign_id = ?
                ORDER BY updated_at DESC, id DESC
                """,
                (campaign_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT id FROM dynamic_branch_runs
                WHERE campaign_id = ? AND status = ?
                ORDER BY updated_at DESC, id DESC
                """,
                (campaign_id, status),
            ).fetchall()
        return [self.get_dynamic_branch_run(str(row["id"])) for row in rows]

    def find_dynamic_branch_event(
        self,
        branch_id: str,
        command_id: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM dynamic_branch_events
            WHERE branch_id = ? AND command_id = ?
            """,
            (branch_id, command_id),
        ).fetchone()
        return self._decode_dynamic_branch_event(row) if row is not None else None

    def transition_dynamic_branch(
        self,
        branch_id: str,
        *,
        expected_version: int,
        status: str,
        current_beat_index: int,
        event_type: str,
        beat_id: str | None,
        outcome: str | None,
        note: str,
        payload: dict[str, Any],
        member_id: str,
        command_id: str,
    ) -> dict[str, Any]:
        branch = self.get_dynamic_branch_run(branch_id)
        existing = self.find_dynamic_branch_event(branch_id, command_id)
        if existing is not None:
            return {
                "branch": branch,
                "event": existing,
                "idempotent_replay": True,
            }
        if int(branch["version"]) != expected_version:
            raise ValueError("Dynamic branch changed; refresh and retry")
        next_version = expected_version + 1
        updated = self.connection.execute(
            """
            UPDATE dynamic_branch_runs
            SET status = ?, current_beat_index = ?, version = ?,
                activated_at = CASE
                  WHEN ? = 'active' AND activated_at IS NULL
                    THEN CURRENT_TIMESTAMP
                  ELSE activated_at
                END,
                completed_at = CASE
                  WHEN ? IN ('completed', 'abandoned') THEN CURRENT_TIMESTAMP
                  ELSE NULL
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ?
            """,
            (
                status,
                current_beat_index,
                next_version,
                status,
                status,
                branch_id,
                expected_version,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("Dynamic branch changed; refresh and retry")
        event = self._insert_dynamic_branch_event(
            branch_id=branch_id,
            event_seq=len(branch["events"]) + 1,
            event_type=event_type,
            beat_id=beat_id,
            outcome=outcome,
            note=note,
            payload=payload,
            member_id=member_id,
            aggregate_version=next_version,
            command_id=command_id,
        )
        return {
            "branch": self.get_dynamic_branch_run(branch_id),
            "event": event,
            "idempotent_replay": False,
        }

    def _list_dynamic_branch_events(
        self,
        branch_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM dynamic_branch_events
            WHERE branch_id = ?
            ORDER BY event_seq
            """,
            (branch_id,),
        ).fetchall()
        return [self._decode_dynamic_branch_event(row) for row in rows]

    def _insert_dynamic_branch_event(self, **values: Any) -> dict[str, Any]:
        event_id = new_id("branch_event")
        self.connection.execute(
            """
            INSERT INTO dynamic_branch_events
              (id, branch_id, event_seq, event_type, beat_id, outcome, note,
               payload_json, actor_member_id, aggregate_version, command_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                values["branch_id"],
                values["event_seq"],
                values["event_type"],
                values["beat_id"],
                values["outcome"],
                values["note"],
                json.dumps(values["payload"], ensure_ascii=False, sort_keys=True),
                values["member_id"],
                values["aggregate_version"],
                values["command_id"],
            ),
        )
        saved = self.connection.execute(
            "SELECT * FROM dynamic_branch_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        return self._decode_dynamic_branch_event(saved)

    @staticmethod
    def _decode_dynamic_branch_event(row: Any) -> dict[str, Any]:
        result = row_to_dict(row)
        result["payload"] = decode_json_field(result.pop("payload_json"), {})
        return result


__all__ = ["DynamicBranchRepository"]

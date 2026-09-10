"""Frozen run/control authority checks for durable parallel action batches."""

from __future__ import annotations

import sqlite3
from typing import Any

from ai_kp.infrastructure.database.rows import decode_json_field


class ParallelRunFence:
    """Verify that a batch still owns its exact scenario execution basis.

    This collaborator is read-only and has no transaction lifecycle.  Callers
    provide the same SQLite connection used for the surrounding batch mutation.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def validate_scope(
        self,
        *,
        campaign_id: str,
        session_id: str,
        run_id: str,
        module_run_version: int,
        contract_version_id: str,
        base_state_version: int,
        created_by_member_id: str | None,
    ) -> None:
        session = self.connection.execute(
            "SELECT campaign_id, status FROM campaign_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if (
            session is None
            or session["campaign_id"] != campaign_id
            or session["status"] != "active"
        ):
            raise ValueError("Parallel batch requires the campaign's active session")
        run = self.connection.execute(
            """
            SELECT r.campaign_id, r.status, r.version,
                   r.director_control_mode, b.contract_version_id,
                   s.state_version, s.snapshot_json
            FROM campaign_module_runs r
            JOIN module_run_contract_bindings b ON b.run_id = r.id
            JOIN scenario_run_states s ON s.run_id = r.id
            WHERE r.id = ?
            """,
            (run_id,),
        ).fetchone()
        if run is None or run["campaign_id"] != campaign_id or run["status"] != "active":
            raise ValueError("Parallel batch requires an active, contract-bound run")
        if (
            type(module_run_version) is not int
            or module_run_version < 0
            or int(run["version"]) != module_run_version
            or run["director_control_mode"] != "ai_assist"
        ):
            raise ValueError("Parallel batch AI-control authority changed")
        if run["contract_version_id"] != contract_version_id:
            raise ValueError("Parallel batch contract binding changed")
        if int(run["state_version"]) != base_state_version:
            raise ValueError("Parallel batch base state version is stale")
        snapshot = decode_json_field(run["snapshot_json"], {})
        if not isinstance(snapshot, dict) or snapshot.get("status") != "active":
            raise ValueError("Parallel batch requires an active scenario state")
        if created_by_member_id is not None:
            member = self.connection.execute(
                """
                SELECT campaign_id, session_id, role, revoked_at
                FROM session_members WHERE id = ?
                """,
                (created_by_member_id,),
            ).fetchone()
            if (
                member is None
                or member["campaign_id"] != campaign_id
                or member["session_id"] != session_id
                or member["role"] != "kp"
                or member["revoked_at"] is not None
            ):
                raise PermissionError(
                    "Parallel batch creator must be the active session KP"
                )

    def validate_execution_authority(self, batch: dict[str, Any]) -> None:
        """Revalidate every frozen authority surface before a stage mutation."""

        self.validate_scope(
            campaign_id=str(batch["campaign_id"]),
            session_id=str(batch["session_id"]),
            run_id=str(batch["run_id"]),
            module_run_version=int(batch["module_run_version"]),
            contract_version_id=str(batch["contract_version_id"]),
            base_state_version=int(batch["base_state_version"]),
            created_by_member_id=None,
        )

    def execution_authority_error(self, batch: dict[str, Any]) -> str:
        """Describe a stale batch without allowing a caller to ignore it."""

        try:
            self.validate_execution_authority(batch)
        except (KeyError, TypeError, ValueError) as exc:
            return f"Parallel batch authority changed: {exc}"
        return ""


__all__ = ["ParallelRunFence"]

"""SQLite persistence for the one explicitly active module run per campaign."""

from __future__ import annotations

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class ModuleRunRepository(SQLiteRepository):
    def _decode_module_run(self, row: Any) -> dict:
        result = row_to_dict(row)
        result["active_spoiler_tags"] = decode_json_field(
            result.pop("active_spoiler_tags_json"),
            [],
        )
        result["state"] = decode_json_field(result.pop("state_json"), {})
        return result

    def get_campaign_module_run(self, run_id: str) -> dict:
        row = self.connection.execute(
            """
            SELECT r.*, m.title AS module_title
            FROM campaign_module_runs r
            JOIN modules m ON m.id = r.module_id
            WHERE r.id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Campaign module run not found: {run_id}")
        return self._decode_module_run(row)

    def get_active_campaign_module_run(self, campaign_id: str) -> dict | None:
        row = self.connection.execute(
            """
            SELECT r.*, m.title AS module_title
            FROM campaign_module_runs r
            JOIN modules m ON m.id = r.module_id
            WHERE r.campaign_id = ? AND r.status = 'active'
            """,
            (campaign_id,),
        ).fetchone()
        return self._decode_module_run(row) if row is not None else None

    def list_campaign_module_runs(
        self,
        campaign_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid module run pagination")
        rows = self.connection.execute(
            """
            SELECT r.*, m.title AS module_title
            FROM campaign_module_runs r
            JOIN modules m ON m.id = r.module_id
            WHERE r.campaign_id = ?
            ORDER BY r.started_at DESC, r.id DESC
            LIMIT ? OFFSET ?
            """,
            (campaign_id, limit, offset),
        ).fetchall()
        return [self._decode_module_run(row) for row in rows]

    def start_campaign_module_run(
        self,
        *,
        campaign_id: str,
        module_id: str,
        current_scene_key: str | None,
        active_spoiler_tags: list[str],
        state: dict[str, Any],
        started_by_member_id: str | None,
    ) -> dict:
        # Serialize the short read/pause/insert decision across SQLite
        # connections. The partial unique index remains the final invariant.
        self.begin_immediate()
        self.get_campaign(campaign_id)
        module = self.get_module(module_id)
        if module["campaign_id"] not in {None, campaign_id}:
            raise ValueError("Module does not belong to this campaign")
        tags = self._normalize_spoiler_tags(active_spoiler_tags)
        active = self.get_active_campaign_module_run(campaign_id)
        if active is not None and active["module_id"] == module_id:
            # POST is idempotent. Scene, spoiler, and progress changes belong to
            # PATCH so a UI retry or double-click cannot erase a live run.
            return active
        if active is not None:
            self.connection.execute(
                """
                UPDATE campaign_module_runs
                SET status = 'paused', version = version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'active'
                """,
                (active["id"],),
            )
        run_id = new_id("modrun")
        self.connection.execute(
            """
            INSERT INTO campaign_module_runs
              (id, campaign_id, module_id, module_source_hash, current_scene_key,
               active_spoiler_tags_json, state_json, started_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                campaign_id,
                module_id,
                module.get("source_hash"),
                self._normalize_scene_key(current_scene_key),
                json.dumps(tags, ensure_ascii=False),
                self._encode_state(state),
                started_by_member_id,
            ),
        )
        return self.get_campaign_module_run(run_id)

    def update_campaign_module_run(self, run_id: str, changes: dict[str, Any]) -> dict:
        self.begin_immediate()
        run = self.get_campaign_module_run(run_id)
        if "expected_version" not in changes:
            raise ValueError("expected_version is required for module run updates")
        expected_version = changes["expected_version"]
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 0
        ):
            raise ValueError("expected_version must be a non-negative integer")
        if run["version"] != expected_version:
            raise ValueError(
                "Module run changed; refresh it before applying this update"
            )
        assignments: list[str] = []
        params: list[Any] = []
        if "status" in changes:
            status = str(changes["status"])
            if status not in {"active", "paused", "completed"}:
                raise ValueError("Unsupported module run status")
            if status == "active":
                self.connection.execute(
                    """
                    UPDATE campaign_module_runs
                    SET status = 'paused', version = version + 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE campaign_id = ? AND status = 'active' AND id != ?
                    """,
                    (run["campaign_id"], run_id),
                )
            assignments.append("status = ?")
            params.append(status)
            assignments.append(
                "completed_at = CASE "
                "WHEN ? = 'completed' AND status != 'completed' THEN CURRENT_TIMESTAMP "
                "WHEN ? != 'completed' THEN NULL "
                "ELSE completed_at END"
            )
            params.append(status)
            params.append(status)
        if "current_scene_key" in changes:
            assignments.append("current_scene_key = ?")
            params.append(self._normalize_scene_key(changes["current_scene_key"]))
        if "active_spoiler_tags" in changes:
            assignments.append("active_spoiler_tags_json = ?")
            params.append(
                json.dumps(
                    self._normalize_spoiler_tags(changes["active_spoiler_tags"]),
                    ensure_ascii=False,
                )
            )
        if "state" in changes:
            assignments.append("state_json = ?")
            params.append(self._encode_state(changes["state"]))
        if not assignments:
            return run
        assignments.append("version = version + 1")
        assignments.append("updated_at = CURRENT_TIMESTAMP")
        params.append(run_id)
        params.append(expected_version)
        self.connection.execute(
            f"""
            UPDATE campaign_module_runs
            SET {', '.join(assignments)}
            WHERE id = ? AND version = ?
            """,
            params,
        )
        updated = self.get_campaign_module_run(run_id)
        if updated["version"] != expected_version + 1:
            raise ValueError(
                "Module run changed; refresh it before applying this update"
            )
        return updated

    @staticmethod
    def _normalize_scene_key(value: Any) -> str | None:
        normalized = str(value or "").strip()
        if len(normalized) > 160:
            raise ValueError("Current scene key is too long")
        return normalized or None

    @staticmethod
    def _normalize_spoiler_tags(values: Any) -> list[str]:
        if not isinstance(values, (list, tuple)):
            raise TypeError("Active spoiler tags must be a list")
        tags = list(
            dict.fromkeys(
                str(value).strip() for value in values if str(value).strip()
            )
        )
        if len(tags) > 100 or any(len(tag) > 160 for tag in tags):
            raise ValueError("Active spoiler tag list is too large")
        return tags

    @staticmethod
    def _encode_state(state: Any) -> str:
        if not isinstance(state, dict):
            raise TypeError("Module run state must be an object")
        ModuleRunRepository._validate_state_value(state, depth=0)
        try:
            encoded = json.dumps(
                state,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Module run state must contain finite JSON values") from exc
        if len(encoded.encode("utf-8")) > 4 * 1024:
            raise ValueError("Module run state exceeds 4 KiB")
        return encoded

    @staticmethod
    def _validate_state_value(value: Any, *, depth: int) -> None:
        if depth > 4:
            raise ValueError("Module run state exceeds maximum nesting depth")
        if isinstance(value, dict):
            if len(value) > 32:
                raise ValueError("Module run state objects may contain at most 32 keys")
            for key, item in value.items():
                if not isinstance(key, str) or not key or len(key) > 80:
                    raise ValueError("Module run state keys must contain 1-80 characters")
                ModuleRunRepository._validate_state_value(item, depth=depth + 1)
            return
        if isinstance(value, list):
            if len(value) > 64:
                raise ValueError("Module run state arrays may contain at most 64 items")
            for item in value:
                ModuleRunRepository._validate_state_value(item, depth=depth + 1)
            return
        if isinstance(value, str):
            if len(value) > 2000:
                raise ValueError("Module run state strings may contain at most 2000 characters")
            return
        if value is None or isinstance(value, (bool, int, float)):
            return
        raise TypeError("Module run state must contain JSON values")

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
               current_scene_title, active_spoiler_tags_json, state_json,
               started_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                campaign_id,
                module_id,
                module.get("source_hash"),
                self._normalize_scene_key(current_scene_key),
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

    def set_module_run_control(
        self,
        run_id: str,
        *,
        expected_version: int,
        mode: str,
        reason: str,
        member_id: str,
    ) -> dict:
        self.begin_immediate()
        run = self.get_campaign_module_run(run_id)
        self._require_expected_version(run, expected_version)
        if mode not in {"ai_assist", "safety_paused", "human_kp"}:
            raise ValueError("Unsupported director control mode")
        normalized_reason = self._require_text(reason, "Control handoff reason", 2000)
        prior = str(run.get("director_control_mode") or "ai_assist")
        if prior == mode:
            return run
        event_seq = int(
            self.connection.execute(
                """
                SELECT COALESCE(MAX(event_seq), 0) + 1
                FROM module_run_control_events
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()[0]
        )
        self.connection.execute(
            """
            INSERT INTO module_run_control_events
              (id, run_id, event_seq, from_mode, to_mode, reason,
               changed_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("control"),
                run_id,
                event_seq,
                prior,
                mode,
                normalized_reason,
                member_id,
            ),
        )
        cursor = self.connection.execute(
            """
            UPDATE campaign_module_runs
            SET director_control_mode = ?, director_control_reason = ?,
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ?
            """,
            (mode, normalized_reason, run_id, expected_version),
        )
        if cursor.rowcount != 1:
            raise ValueError("Module run changed; refresh it before applying this update")
        return self.get_campaign_module_run(run_id)

    def list_module_run_control_events(self, run_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM module_run_control_events
            WHERE run_id = ? ORDER BY event_seq
            """,
            (run_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def set_module_run_automation_level(
        self,
        run_id: str,
        *,
        expected_version: int,
        level: str,
        reason: str,
        member_id: str,
    ) -> dict:
        self.begin_immediate()
        run = self.get_campaign_module_run(run_id)
        self._require_expected_version(run, expected_version)
        if level not in {"conservative", "balanced", "ai_kp"}:
            raise ValueError("Unsupported automation level")
        normalized_reason = self._require_text(reason, "Automation level reason", 2000)
        prior = str(run.get("automation_level") or "conservative")
        if prior == level:
            return run
        event_seq = int(
            self.connection.execute(
                """
                SELECT COALESCE(MAX(event_seq), 0) + 1
                FROM module_run_automation_events
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()[0]
        )
        self.connection.execute(
            """
            INSERT INTO module_run_automation_events
              (id, run_id, event_seq, from_level, to_level, reason,
               changed_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("automation"),
                run_id,
                event_seq,
                prior,
                level,
                normalized_reason,
                member_id,
            ),
        )
        cursor = self.connection.execute(
            """
            UPDATE campaign_module_runs
            SET automation_level = ?, automation_reason = ?,
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ?
            """,
            (level, normalized_reason, run_id, expected_version),
        )
        if cursor.rowcount != 1:
            raise ValueError("Module run changed; refresh it before applying this update")
        return self.get_campaign_module_run(run_id)

    def list_module_run_automation_events(self, run_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM module_run_automation_events
            WHERE run_id = ? ORDER BY event_seq
            """,
            (run_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def transition_module_run_scene(
        self,
        run_id: str,
        *,
        expected_version: int,
        scene_key: str,
        scene_title: str,
        play_pace: str,
        location_entity_id: str | None,
        world_time: str | None,
        note: str,
        member_id: str | None,
    ) -> dict:
        self.begin_immediate()
        run = self.get_campaign_module_run(run_id)
        self._require_expected_version(run, expected_version)
        if run["status"] != "active":
            raise ValueError("Only an active module run can transition scenes")
        normalized_key = self._require_text(scene_key, "Scene key", 160)
        normalized_title = self._require_text(scene_title, "Scene title", 300)
        normalized_pace = str(play_pace).strip()
        if normalized_pace not in {"freeform", "structured", "downtime"}:
            raise ValueError("Unsupported scene play pace")
        normalized_location = self._validate_location_entity(
            run,
            location_entity_id,
        )
        normalized_world_time = self._optional_text(world_time, "World time", 160)
        normalized_note = self._optional_text(note, "Scene transition note", 2000) or ""
        event_id = new_id("sceneevt")
        self.connection.execute(
            """
            INSERT INTO module_run_scene_events
              (id, run_id, from_scene_key, to_scene_key, from_scene_title,
               to_scene_title, from_play_pace, to_play_pace,
               from_location_entity_id, to_location_entity_id, world_time, note,
               changed_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                run_id,
                run.get("current_scene_key"),
                normalized_key,
                run.get("current_scene_title"),
                normalized_title,
                run.get("play_pace"),
                normalized_pace,
                run.get("current_location_entity_id"),
                normalized_location,
                normalized_world_time,
                normalized_note,
                member_id,
            ),
        )
        cursor = self.connection.execute(
            """
            UPDATE campaign_module_runs
            SET current_scene_key = ?, current_scene_title = ?, play_pace = ?,
                current_location_entity_id = ?, scene_started_world_time = ?,
                version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ? AND status = 'active'
            """,
            (
                normalized_key,
                normalized_title,
                normalized_pace,
                normalized_location,
                normalized_world_time,
                run_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("Module run changed; refresh it before applying this update")
        return {
            "run": self.get_campaign_module_run(run_id),
            "event": self.get_module_run_scene_event(event_id),
        }

    def get_module_run_scene_event(self, event_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM module_run_scene_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Module run scene event not found: {event_id}")
        return row_to_dict(row)

    def list_module_run_scene_events(self, run_id: str) -> list[dict]:
        self.get_campaign_module_run(run_id)
        rows = self.connection.execute(
            """
            SELECT * FROM module_run_scene_events
            WHERE run_id = ?
            ORDER BY created_at, rowid
            """,
            (run_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def set_module_run_entity_state(
        self,
        run_id: str,
        entity_id: str,
        *,
        expected_version: int,
        status: str,
        note: str,
        member_id: str | None,
    ) -> dict:
        self.begin_immediate()
        run = self.get_campaign_module_run(run_id)
        self._require_expected_version(run, expected_version)
        if run["status"] != "active":
            raise ValueError("Only an active module run can change entity state")
        entity = self.get_module_entity(entity_id)
        if entity["module_id"] != run["module_id"]:
            raise ValueError("Runtime entity belongs to another module")
        normalized_status = str(status).strip()
        if normalized_status not in {"hidden", "available", "discovered", "resolved"}:
            raise ValueError("Unsupported module runtime entity status")
        normalized_note = self._optional_text(note, "Entity state note", 2000) or ""
        current_row = self.connection.execute(
            """
            SELECT * FROM module_run_entity_states
            WHERE run_id = ? AND entity_id = ?
            """,
            (run_id, entity_id),
        ).fetchone()
        current_status = str(current_row["status"]) if current_row else "hidden"
        if current_status == normalized_status:
            return {
                "run": run,
                "entity_state": self._module_run_entity_state(
                    run_id,
                    entity,
                    current_row,
                ),
                "event": None,
            }
        cursor = self.connection.execute(
            """
            UPDATE campaign_module_runs
            SET version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ? AND status = 'active'
            """,
            (run_id, expected_version),
        )
        if cursor.rowcount != 1:
            raise ValueError("Module run changed; refresh it before applying this update")
        self.connection.execute(
            """
            INSERT INTO module_run_entity_states
              (run_id, entity_id, status, updated_by_member_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id, entity_id) DO UPDATE SET
              status = excluded.status,
              version = module_run_entity_states.version + 1,
              updated_by_member_id = excluded.updated_by_member_id,
              updated_at = CURRENT_TIMESTAMP
            """,
            (run_id, entity_id, normalized_status, member_id),
        )
        event_id = new_id("entityevt")
        self.connection.execute(
            """
            INSERT INTO module_run_entity_state_events
              (id, run_id, entity_id, from_status, to_status, note,
               changed_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                run_id,
                entity_id,
                current_status,
                normalized_status,
                normalized_note,
                member_id,
            ),
        )
        updated_row = self.connection.execute(
            """
            SELECT * FROM module_run_entity_states
            WHERE run_id = ? AND entity_id = ?
            """,
            (run_id, entity_id),
        ).fetchone()
        return {
            "run": self.get_campaign_module_run(run_id),
            "entity_state": self._module_run_entity_state(
                run_id,
                entity,
                updated_row,
            ),
            "event": self.get_module_run_entity_state_event(event_id),
        }

    def list_module_run_entity_states(self, run_id: str) -> list[dict]:
        run = self.get_campaign_module_run(run_id)
        state_rows = {
            str(row["entity_id"]): row
            for row in self.connection.execute(
                "SELECT * FROM module_run_entity_states WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        }
        return [
            self._module_run_entity_state(
                run_id,
                entity,
                state_rows.get(str(entity["id"])),
            )
            for entity in self.list_module_entities(str(run["module_id"]))
        ]

    def get_module_run_entity_state_event(self, event_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM module_run_entity_state_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Module run entity state event not found: {event_id}")
        return row_to_dict(row)

    def list_module_run_entity_state_events(self, run_id: str) -> list[dict]:
        self.get_campaign_module_run(run_id)
        rows = self.connection.execute(
            """
            SELECT e.*, entity.name AS entity_name,
                   entity.entity_type AS entity_type
            FROM module_run_entity_state_events e
            JOIN module_entities entity ON entity.id = e.entity_id
            WHERE e.run_id = ?
            ORDER BY e.created_at, e.rowid
            """,
            (run_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    @staticmethod
    def _module_run_entity_state(run_id: str, entity: dict, row: Any) -> dict:
        return {
            "run_id": run_id,
            "entity_id": entity["id"],
            "entity_type": entity["entity_type"],
            "name": entity["name"],
            "description": entity["description"],
            "visibility": entity["visibility"],
            "spoiler_tag": entity["spoiler_tag"],
            "status": row["status"] if row is not None else "hidden",
            "version": int(row["version"]) if row is not None else 0,
            "updated_by_member_id": (
                row["updated_by_member_id"] if row is not None else None
            ),
            "updated_at": row["updated_at"] if row is not None else None,
        }

    def _validate_location_entity(
        self,
        run: dict,
        entity_id: str | None,
    ) -> str | None:
        normalized = str(entity_id or "").strip()
        if not normalized:
            return None
        entity = self.get_module_entity(normalized)
        if entity["module_id"] != run["module_id"]:
            raise ValueError("Scene location belongs to another module")
        if entity["entity_type"] != "location":
            raise ValueError("Scene location must reference a location entity")
        return normalized

    @staticmethod
    def _require_expected_version(run: dict, expected_version: Any) -> None:
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 0
        ):
            raise ValueError("expected_version must be a non-negative integer")
        if run["version"] != expected_version:
            raise ValueError("Module run changed; refresh it before applying this update")

    @staticmethod
    def _require_text(value: Any, label: str, maximum: int) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{label} is required")
        if len(normalized) > maximum:
            raise ValueError(f"{label} is too long")
        return normalized

    @staticmethod
    def _optional_text(value: Any, label: str, maximum: int) -> str | None:
        normalized = str(value or "").strip()
        if len(normalized) > maximum:
            raise ValueError(f"{label} is too long")
        return normalized or None

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

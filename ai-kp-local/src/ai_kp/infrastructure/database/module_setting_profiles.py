"""Immutable setting-profile versions and audited run selections."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class ModuleSettingProfileRepository(SQLiteRepository):
    def create_module_setting_profile(
        self,
        *,
        module_id: str,
        title: str,
        setting_pack_id: str,
        setting_pack_version: str,
        document: dict[str, Any],
        member_id: str | None,
    ) -> dict:
        self.begin_immediate()
        self.get_module(module_id)
        profile_id = new_id("setprof")
        normalized_title = self._profile_text(title, "Profile title", 200)
        normalized_pack_id = self._profile_text(
            setting_pack_id, "Setting pack id", 80
        )
        encoded, content_hash = self._encode_document(document)
        self.connection.execute(
            """
            INSERT INTO module_setting_profiles
              (id, module_id, title, setting_pack_id,
               created_by_member_id, updated_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                module_id,
                normalized_title,
                normalized_pack_id,
                member_id,
                member_id,
            ),
        )
        self._insert_profile_version(
            profile_id=profile_id,
            version=1,
            setting_pack_version=setting_pack_version,
            encoded_document=encoded,
            content_hash=content_hash,
            member_id=member_id,
        )
        return self.get_module_setting_profile(profile_id)

    def update_module_setting_profile(
        self,
        profile_id: str,
        *,
        expected_version: int,
        title: str,
        setting_pack_version: str,
        document: dict[str, Any],
        member_id: str | None,
    ) -> dict:
        self.begin_immediate()
        current = self.get_module_setting_profile(profile_id)
        if current["status"] != "active":
            raise ValueError("Archived setting profiles cannot be updated")
        if current["current_version"] != expected_version:
            raise ValueError("Setting profile changed; refresh it before updating")
        next_version = expected_version + 1
        encoded, content_hash = self._encode_document(document)
        self._insert_profile_version(
            profile_id=profile_id,
            version=next_version,
            setting_pack_version=setting_pack_version,
            encoded_document=encoded,
            content_hash=content_hash,
            member_id=member_id,
        )
        cursor = self.connection.execute(
            """
            UPDATE module_setting_profiles
            SET title = ?, current_version = ?, updated_by_member_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND current_version = ?
            """,
            (
                self._profile_text(title, "Profile title", 200),
                next_version,
                member_id,
                profile_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("Setting profile changed; refresh it before updating")
        return self.get_module_setting_profile(profile_id)

    def get_module_setting_profile(
        self, profile_id: str, *, version: int | None = None
    ) -> dict:
        if version is None:
            row = self.connection.execute(
                """
                SELECT p.*, v.version, v.setting_pack_version, v.document_json,
                       v.content_hash, v.created_at AS version_created_at
                FROM module_setting_profiles p
                JOIN module_setting_profile_versions v
                  ON v.profile_id = p.id AND v.version = p.current_version
                WHERE p.id = ?
                """,
                (profile_id,),
            ).fetchone()
        else:
            row = self.connection.execute(
                """
                SELECT p.*, v.version, v.setting_pack_version, v.document_json,
                       v.content_hash, v.created_at AS version_created_at
                FROM module_setting_profiles p
                JOIN module_setting_profile_versions v ON v.profile_id = p.id
                WHERE p.id = ? AND v.version = ?
                """,
                (profile_id, version),
            ).fetchone()
        if row is None:
            raise KeyError(f"Module setting profile not found: {profile_id}")
        return self._decode_profile(row)

    def list_module_setting_profiles(self, module_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT id FROM module_setting_profiles
            WHERE module_id = ? AND status = 'active'
            ORDER BY updated_at DESC, id
            """,
            (module_id,),
        ).fetchall()
        return [self.get_module_setting_profile(str(row["id"])) for row in rows]

    def set_module_run_setting_selection(
        self,
        run_id: str,
        *,
        expected_run_version: int,
        profile_id: str,
        profile_version: int,
        settlement_id: str,
        reason: str,
        member_id: str | None,
    ) -> dict:
        self.begin_immediate()
        run = self.get_campaign_module_run(run_id)
        self._require_run_version(run, expected_run_version)
        profile = self.get_module_setting_profile(profile_id, version=profile_version)
        if profile["module_id"] != run["module_id"]:
            raise ValueError("Setting profile belongs to another module")
        if profile["status"] != "active":
            raise ValueError("Archived setting profile cannot be selected")
        if profile["current_version"] != profile_version:
            raise ValueError("Select the current setting profile version")
        normalized_settlement = self._profile_text(
            settlement_id, "Settlement id", 160
        )
        if normalized_settlement not in {
            str(item.get("settlement_id"))
            for item in profile["document"].get("settlements", ())
        }:
            raise ValueError("Setting profile does not contain the selected settlement")
        normalized_reason = self._profile_text(reason, "Selection reason", 2000)
        prior = self.get_module_run_setting_selection(run_id)
        selection_version = int(prior["version"]) + 1 if prior else 1
        self.connection.execute(
            """
            INSERT INTO module_run_setting_selections
              (run_id, profile_id, profile_version, settlement_id, version,
               updated_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
              profile_id = excluded.profile_id,
              profile_version = excluded.profile_version,
              settlement_id = excluded.settlement_id,
              version = excluded.version,
              updated_by_member_id = excluded.updated_by_member_id,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                run_id,
                profile_id,
                profile_version,
                normalized_settlement,
                selection_version,
                member_id,
            ),
        )
        self.connection.execute(
            """
            UPDATE campaign_module_runs
            SET version = version + 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND version = ?
            """,
            (run_id, expected_run_version),
        )
        self.connection.execute(
            """
            INSERT INTO module_run_setting_selection_events
              (event_id, run_id, from_profile_id, from_profile_version,
               from_settlement_id, to_profile_id, to_profile_version,
               to_settlement_id, changed_by_member_id, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("setselevent"),
                run_id,
                prior.get("profile_id") if prior else None,
                prior.get("profile_version") if prior else None,
                prior.get("settlement_id") if prior else None,
                profile_id,
                profile_version,
                normalized_settlement,
                member_id,
                normalized_reason,
            ),
        )
        return {
            "run": self.get_campaign_module_run(run_id),
            "selection": self.get_module_run_setting_selection(run_id),
        }

    def get_module_run_setting_selection(self, run_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM module_run_setting_selections WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        result = row_to_dict(row)
        result["profile"] = self.get_module_setting_profile(
            str(result["profile_id"]), version=int(result["profile_version"])
        )
        return result

    def list_module_run_setting_selection_events(self, run_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM module_run_setting_selection_events
            WHERE run_id = ? ORDER BY sequence
            """,
            (run_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def _insert_profile_version(
        self,
        *,
        profile_id: str,
        version: int,
        setting_pack_version: str,
        encoded_document: str,
        content_hash: str,
        member_id: str | None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO module_setting_profile_versions
              (profile_id, version, setting_pack_version, document_json,
               content_hash, created_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                version,
                self._profile_text(
                    setting_pack_version, "Setting pack version", 40
                ),
                encoded_document,
                content_hash,
                member_id,
            ),
        )

    @staticmethod
    def _decode_profile(row: Any) -> dict:
        result = row_to_dict(row)
        result["current_version"] = int(result["current_version"])
        result["version"] = int(result["version"])
        result["document"] = decode_json_field(result.pop("document_json"), {})
        return result

    @staticmethod
    def _encode_document(document: dict[str, Any]) -> tuple[str, str]:
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > 1024 * 1024:
            raise ValueError("Setting profile document exceeds 1 MiB")
        return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _profile_text(value: Any, label: str, maximum: int) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{label} is required")
        if len(normalized) > maximum:
            raise ValueError(f"{label} is too long")
        return normalized

    @staticmethod
    def _require_run_version(run: dict, expected_version: Any) -> None:
        if (
            not isinstance(expected_version, int)
            or isinstance(expected_version, bool)
            or expected_version < 0
            or run["version"] != expected_version
        ):
            raise ValueError("Module run changed; refresh it before selecting a setting")


__all__ = ["ModuleSettingProfileRepository"]

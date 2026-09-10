"""Persistence for immutable action-resolution previews and shadow comparisons."""

from __future__ import annotations

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.resolution.action_kernel import ResolutionPreview


class ActionResolutionPreviewRepository(SQLiteRepository):
    def create_action_resolution_preview(
        self,
        *,
        action_id: str,
        run_id: str | None,
        source: str,
        preview: ResolutionPreview,
    ) -> dict[str, Any]:
        if source not in {"legacy_projection", "kernel_shadow", "kernel_authority"}:
            raise ValueError("Unsupported action-resolution preview source")
        preview_id = new_id("resolutionpreview")
        self.connection.execute(
            """
            INSERT OR IGNORE INTO action_resolution_previews
              (id, action_id, run_id, source, contract_id, scenario_version,
               snapshot_version, operator_id, preview_hash, preview_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                preview_id,
                action_id,
                run_id,
                source,
                preview.contract_id,
                preview.scenario_version,
                preview.run_version,
                preview.operator_id,
                preview.preview_hash,
                json.dumps(preview.model_dump(mode="json"), ensure_ascii=False),
            ),
        )
        row = self.connection.execute(
            """
            SELECT * FROM action_resolution_previews
            WHERE action_id = ? AND source = ? AND snapshot_version = ?
              AND preview_hash = ?
            """,
            (action_id, source, preview.run_version, preview.preview_hash),
        ).fetchone()
        if row is None:
            raise RuntimeError("Action-resolution preview was not persisted")
        return self._decode_preview(row)

    def list_action_resolution_previews(self, action_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT * FROM action_resolution_previews
            WHERE action_id = ? ORDER BY created_at, id
            """,
            (action_id,),
        ).fetchall()
        return [self._decode_preview(row) for row in rows]

    @staticmethod
    def _decode_preview(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["preview"] = decode_json_field(result.pop("preview_json"), {})
        return result


__all__ = ["ActionResolutionPreviewRepository"]

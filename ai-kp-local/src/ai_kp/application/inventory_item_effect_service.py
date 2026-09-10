"""Apply a KP-authored item effect through the installed ruleset command path."""

from __future__ import annotations

from typing import Any

from ai_kp.application.gameplay_service import GameplayCommand, GameplayService
from ai_kp.platform.sessions.models import AuthenticatedMember


class InventoryItemEffectService:
    def __init__(self, repo: Any):
        self.repo = repo

    def apply(
        self,
        *,
        item: dict[str, Any],
        identity: AuthenticatedMember,
        command_id: str,
    ) -> dict[str, Any] | None:
        effect = dict(item.get("use_effect") or {})
        if not effect:
            return None
        if effect.get("kind") != "ruleset_character_command":
            raise ValueError("Unsupported authoritative inventory use effect")
        if item["holder_kind"] != "investigator":
            raise ValueError("Character item effects require an investigator holder")
        command_type = str(effect.get("command_type") or "").strip()
        payload = effect.get("payload")
        if not command_type or not isinstance(payload, dict):
            raise ValueError("Inventory use effect requires command_type and payload")
        investigator = self.repo.get_campaign_investigator(
            item["campaign_id"], item["holder_id"]
        )
        state = investigator.get("campaign_state")
        if state is None:
            raise ValueError("Inventory use effect target has no campaign state")
        return GameplayService(self.repo).command_character(
            item["campaign_id"],
            item["holder_id"],
            self._system_kp_identity(identity),
            GameplayCommand(
                command_id=f"inventory-effect:{command_id}",
                expected_version=int(state["state_version"]),
                command_type=command_type,
                payload=dict(payload),
                visibility="table",
            ),
        )

    def _system_kp_identity(
        self, identity: AuthenticatedMember
    ) -> AuthenticatedMember:
        row = self.repo.connection.execute(
            """
            SELECT id, session_id, campaign_id, display_name
            FROM session_members
            WHERE session_id = ? AND campaign_id = ? AND role = 'kp'
              AND revoked_at IS NULL
            ORDER BY joined_at, id LIMIT 1
            """,
            (identity.session_id, identity.campaign_id),
        ).fetchone()
        if row is None:
            raise ValueError("No active KP authority exists for this item effect")
        return AuthenticatedMember(
            member_id=str(row["id"]),
            session_id=str(row["session_id"]),
            campaign_id=str(row["campaign_id"]),
            role="kp",
            display_name=f"{row['display_name']} · Item Rules",
            pc_id=None,
        )


__all__ = ["InventoryItemEffectService"]

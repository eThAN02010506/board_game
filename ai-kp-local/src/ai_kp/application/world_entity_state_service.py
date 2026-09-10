"""Shared typed state-commit boundary for AI and human Keepers."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from ai_kp.application.errors import (
    AccessDeniedError,
    ConflictError,
    InvalidInputError,
    KpSessionEndedError,
    ResourceNotFoundError,
)
from ai_kp.application.ports.world_entities import WorldEntityStateStore
from ai_kp.platform.sessions.models import AuthenticatedMember

WorldEntityStateValue = str | int | bool | None
WorldEntityStateVisibility = Literal["table", "kp", "secret"]
WorldEntityStateSource = Literal["human_kp", "ai_kp", "rules_kernel"]

_DIMENSION_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,199}$")
_VISIBILITY_RANK = {"table": 0, "kp": 1, "secret": 2}


@dataclass(frozen=True)
class SetWorldEntityStateCommand:
    expected_version: int
    dimension: str
    value: WorldEntityStateValue
    visibility: WorldEntityStateVisibility
    idempotency_key: str
    note: str = ""
    happened_at: str | None = None
    source_kind: WorldEntityStateSource = "human_kp"


class WorldEntityStateService:
    """Validate and atomically append one ruleset-neutral entity-state change."""

    def __init__(self, repo: WorldEntityStateStore):
        self.repo = repo

    def set_state(
        self,
        campaign_id: str,
        entity_id: str,
        identity: AuthenticatedMember,
        command: SetWorldEntityStateCommand,
    ) -> dict:
        self._require_kp(campaign_id, identity)
        normalized = self._normalize(command)
        command_hash = self._command_hash(entity_id, normalized)
        self.repo.begin_immediate()
        self.repo.begin_campaign_world_entity_state_change()
        try:
            if not self.repo.is_session_member_active(
                identity.member_id, identity.session_id
            ):
                raise KpSessionEndedError("KP session ended before entity state commit")
            replay = self.repo.find_campaign_world_entity_state_change_by_key(
                campaign_id, normalized.idempotency_key
            )
            if replay is not None:
                if replay["command_hash"] != command_hash:
                    raise ConflictError(
                        "Entity state idempotency key was already used for another command"
                    )
                result = {
                    "entity": self.repo.get_campaign_world_entity(entity_id),
                    "change": replay,
                }
                self.repo.finish_campaign_world_entity_state_change()
                return result
            try:
                entity = self.repo.get_campaign_world_entity(entity_id)
            except KeyError as exc:
                raise ResourceNotFoundError(str(exc)) from exc
            if entity["campaign_id"] != campaign_id:
                raise ResourceNotFoundError("Campaign world entity not found")
            allowed_dimensions = set(entity.get("data", {}).get("state_dimensions") or ())
            if normalized.dimension not in allowed_dimensions:
                allowed = ", ".join(sorted(allowed_dimensions)) or "none"
                raise InvalidInputError(
                    f"Entity archetype does not declare state dimension "
                    f"{normalized.dimension}; allowed={allowed}"
                )
            if _VISIBILITY_RANK[normalized.visibility] < _VISIBILITY_RANK[entity["visibility"]]:
                raise InvalidInputError(
                    "Entity state visibility cannot be broader than entity visibility"
                )
            current = self.repo.get_campaign_world_entity_state(
                entity_id, normalized.dimension
            )
            # Deterministic assignments need a receipt even for no-ops: otherwise
            # replay after a later edit would apply an old effect for the first time.
            allow_unchanged = normalized.source_kind == "rules_kernel"
            if normalized.value is None and current is None and not allow_unchanged:
                raise InvalidInputError("Cannot clear an entity state that is not set")
            if (
                current is not None
                and current["value"] == normalized.value
                and type(current["value"]) is type(normalized.value)
                and current["visibility"] == normalized.visibility
                and not allow_unchanged
            ):
                raise InvalidInputError("Entity state already has this value and visibility")
            if entity["state_version"] != normalized.expected_version:
                raise ConflictError(
                    "Entity state changed; refresh it before applying this update"
                )
            if not self.repo.bump_campaign_world_entity_state_version(
                entity_id, normalized.expected_version
            ):
                raise ConflictError(
                    "Entity state changed; refresh it before applying this update"
                )
            next_version = normalized.expected_version + 1
            # A newly public value must not disclose the previous secret value
            # through the immutable event or change receipt.
            history_visibility = max(
                normalized.visibility,
                current["visibility"] if current is not None else normalized.visibility,
                key=_VISIBILITY_RANK.__getitem__,
            )
            event = self.repo.append_event(
                campaign_id=campaign_id,
                actor_type="member" if normalized.source_kind == "human_kp" else "system",
                actor_id=identity.member_id if normalized.source_kind == "human_kp" else None,
                visibility=history_visibility,
                event_type="world_entity.state_changed",
                happened_at=normalized.happened_at,
                summary=f"{entity['name']} 的 {normalized.dimension} 状态已更新。",
                payload={
                    "world_entity_id": entity_id,
                    "dimension": normalized.dimension,
                    "from_value": current["value"] if current is not None else None,
                    "to_value": normalized.value,
                    "state_version": next_version,
                    "source_kind": normalized.source_kind,
                },
            )
            self.repo.set_campaign_world_entity_state(
                entity_id=entity_id,
                dimension=normalized.dimension,
                value=normalized.value,
                visibility=normalized.visibility,
                source_event_id=event["id"],
            )
            change = self.repo.create_campaign_world_entity_state_change(
                campaign_id=campaign_id,
                entity_id=entity_id,
                dimension=normalized.dimension,
                from_value=current["value"] if current is not None else None,
                to_value=normalized.value,
                visibility=history_visibility,
                note=normalized.note,
                source_kind=normalized.source_kind,
                state_version=next_version,
                idempotency_key=normalized.idempotency_key,
                command_hash=command_hash,
                event_id=event["id"],
                changed_by_member_id=identity.member_id,
            )
            updated = self.repo.get_campaign_world_entity(entity_id)
            self.repo.append_realtime_event(
                session_id=identity.session_id,
                campaign_id=campaign_id,
                audience="session" if normalized.visibility == "table" else "kp",
                event_type="world_entity.state_changed",
                resource_type="campaign_world_entity",
                resource_id=entity_id,
                payload={
                    "dimension": normalized.dimension,
                    "state_version": next_version,
                },
            )
            result = {"entity": updated, "change": change}
            self.repo.finish_campaign_world_entity_state_change()
            return result
        except Exception:
            self.repo.rollback_campaign_world_entity_state_change()
            raise

    @staticmethod
    def _require_kp(campaign_id: str, identity: AuthenticatedMember) -> None:
        if identity.campaign_id != campaign_id:
            raise AccessDeniedError("Authenticated member belongs to another campaign")
        if identity.role != "kp":
            raise AccessDeniedError("Only the current KP can change world entity state")

    @staticmethod
    def _normalize(
        command: SetWorldEntityStateCommand
    ) -> SetWorldEntityStateCommand:
        if command.visibility not in _VISIBILITY_RANK:
            raise InvalidInputError("Invalid entity state visibility")
        if command.source_kind not in {"human_kp", "ai_kp", "rules_kernel"}:
            raise InvalidInputError("Invalid entity state source")
        if type(command.expected_version) is not int or command.expected_version < 0:
            raise InvalidInputError("Entity state version cannot be negative")
        dimension = unicodedata.normalize("NFKC", command.dimension).strip()
        if not _DIMENSION_PATTERN.fullmatch(dimension):
            raise InvalidInputError("Entity state dimension must be a stable lowercase ID")
        key = unicodedata.normalize("NFKC", command.idempotency_key).strip()
        if not _IDEMPOTENCY_PATTERN.fullmatch(key):
            raise InvalidInputError("Entity state idempotency key is invalid")
        note = unicodedata.normalize("NFKC", command.note).strip()
        if len(note) > 2000:
            raise InvalidInputError("Entity state note is too long")
        happened_at = (
            unicodedata.normalize("NFKC", command.happened_at).strip()
            if command.happened_at is not None
            else None
        )
        if happened_at == "" or (happened_at is not None and len(happened_at) > 80):
            raise InvalidInputError("Entity state time is invalid")
        value = command.value
        if isinstance(value, str):
            value = unicodedata.normalize("NFKC", value).strip()
            if not value or len(value) > 500:
                raise InvalidInputError("Entity state text must contain 1..500 characters")
        elif isinstance(value, bool) or value is None:
            pass
        elif isinstance(value, int):
            if not -(10**9) <= value <= 10**9:
                raise InvalidInputError("Entity state number is outside the supported range")
        else:
            raise InvalidInputError("Entity state value must be text, integer, boolean, or null")
        return SetWorldEntityStateCommand(
            expected_version=command.expected_version,
            dimension=dimension,
            value=value,
            visibility=command.visibility,
            idempotency_key=key,
            note=note,
            happened_at=happened_at,
            source_kind=command.source_kind,
        )

    @staticmethod
    def _command_hash(entity_id: str, command: SetWorldEntityStateCommand) -> str:
        payload = {
            "entity_id": entity_id,
            "expected_version": command.expected_version,
            "dimension": command.dimension,
            "value": command.value,
            "visibility": command.visibility,
            "idempotency_key": command.idempotency_key,
            "note": command.note,
            "happened_at": command.happened_at,
            "source_kind": command.source_kind,
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "SetWorldEntityStateCommand",
    "WorldEntityStateService",
    "WorldEntityStateSource",
    "WorldEntityStateValue",
    "WorldEntityStateVisibility",
]

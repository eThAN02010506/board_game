"""Deterministic Session End and Continue orchestration.

Model agents may later propose prose from these projections, but they never
choose the event window, visibility, episode transition, or committed state.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from ai_kp.application.errors import ConflictError, InvalidInputError
from ai_kp.platform.sessions.models import AuthenticatedMember


class ContinuityStore(Protocol):
    def begin_immediate(self) -> None: ...
    def get_current_campaign_episode(self, session_id: str) -> dict | None: ...
    def get_continuity_cutoff(self) -> str: ...
    def get_episode_continuity_context(
        self, episode_id: str, *, cutoff: str
    ) -> dict: ...
    def get_session_end_blockers(self, session_id: str) -> dict[str, int]: ...
    def create_continuity_snapshot(self, **values: Any) -> dict: ...
    def get_latest_campaign_continuity_snapshot(self, campaign_id: str) -> dict | None: ...
    def continue_campaign_episode(
        self, session_id: str, *, client_continue_id: str
    ) -> dict: ...
    def transition_campaign_episode(
        self, episode_id: str, *, expected_version: int, target: str
    ) -> dict: ...
    def append_realtime_event(self, **values: Any) -> dict: ...


@dataclass(frozen=True)
class ContinuityProjections:
    observer: dict[str, Any]
    player: dict[str, Any]
    kp: dict[str, Any]


class DeterministicContinuityProjector:
    """Build evidence-only summaries when no model is available or trustworthy."""

    def build(self, context: dict[str, Any], *, cutoff: str) -> ContinuityProjections:
        events = list(context["events"])
        public_events = [item for item in events if item["visibility"] == "table"]
        module = context.get("module")
        public_objectives = [
            self._objective_projection(item)
            for item in context.get("objectives", [])
            if item.get("visibility") == "table"
        ]
        public_npcs = [self._npc_projection(item) for item in context.get("known_npcs", [])]
        base = {
            "episode_sequence": context["episode"]["sequence_no"],
            "ended_at": cutoff,
            "campaign_time": context["campaign"].get("current_time"),
            "module": self._module_projection(module),
            "recent_events": [self._event_projection(item) for item in public_events[-20:]],
            "summary_text": self._summary_text(public_events),
            "visible_inventory": self._visible_inventory(context.get("inventory", [])),
            "objectives": public_objectives,
            "current_objectives": [
                item for item in public_objectives if item["status"] in {"open", "blocked"}
            ],
            "unresolved_questions": [
                item for item in public_objectives if item["status"] == "blocked"
            ],
            "known_npcs": public_npcs[-20:],
            "lifecycle_events": [
                {
                    key: item.get(key)
                    for key in (
                        "id",
                        "investigator_id",
                        "member_id",
                        "action",
                        "from_state",
                        "to_state",
                        "public_summary",
                        "created_at",
                    )
                }
                for item in context.get("lifecycle_events", [])[-20:]
            ],
        }
        observer = dict(base)
        player = {
            **base,
            "party": list(context["party"]),
            "party_balances": [
                item
                for item in context.get("balances", [])
                if item["account_kind"] == "party"
            ],
            "character_lifecycles": list(
                context.get("character_lifecycles", [])
            ),
            "member_presence": list(context.get("member_presence", [])),
        }
        kp = {
            **player,
            "private_events": [self._event_projection(item) for item in events[-50:]],
            "private_event_count": sum(
                item["visibility"] in {"kp", "secret"} for item in events
            ),
            "authoritative_inventory": list(context.get("inventory", [])),
            "authoritative_balances": list(context.get("balances", [])),
            "authoritative_lifecycle_events": list(
                context.get("lifecycle_events", [])
            ),
            "authoritative_objectives": list(context.get("objectives", [])),
            "authoritative_npcs": list(context.get("known_npcs", [])),
        }
        return ContinuityProjections(observer=observer, player=player, kp=kp)

    @staticmethod
    def _event_projection(event: dict[str, Any]) -> dict[str, Any]:
        return {
            key: event.get(key)
            for key in ("id", "event_type", "summary", "happened_at", "created_at")
        }

    @staticmethod
    def _module_projection(module: dict[str, Any] | None) -> dict[str, Any] | None:
        if module is None:
            return None
        return {
            key: module.get(key)
            for key in (
                "module_title",
                "status",
                "current_scene_key",
                "current_scene_title",
                "current_location_entity_id",
                "play_pace",
            )
        }

    @staticmethod
    def _summary_text(events: list[dict[str, Any]]) -> str:
        summaries = [str(item.get("summary") or "").strip() for item in events]
        summaries = [item for item in summaries if item]
        if not summaries:
            return "本次游戏尚无可公开回顾的权威事件。"
        return "Previously on… " + "；".join(summaries[-8:])

    @staticmethod
    def _visible_inventory(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                key: item.get(key)
                for key in (
                    "id",
                    "item_type",
                    "public_name",
                    "public_description",
                    "publicly_listed",
                    "quantity",
                    "is_unique",
                    "holder_kind",
                    "holder_id",
                    "state",
                    "equipped_slot",
                    "version",
                )
            }
            for item in items
            if item["holder_kind"] in {"party", "location", "loot"}
            or bool(item.get("equipped_slot"))
        ]

    @staticmethod
    def _objective_projection(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item.get(key)
            for key in (
                "id", "title", "public_description", "status", "version", "updated_at"
            )
        } | {
            "progress": [
                {
                    key: event.get(key)
                    for key in ("id", "to_status", "public_progress", "created_at")
                }
                for event in item.get("events", [])
                if event.get("public_progress")
            ][-10:]
        }

    @staticmethod
    def _npc_projection(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: item.get(key)
            for key in (
                "id", "name", "home_location", "profession", "public_notes", "role",
                "first_seen_time", "last_seen_time", "relationship_score",
            )
        }


class SessionContinuityService:
    def __init__(
        self,
        repo: ContinuityStore,
        projector: DeterministicContinuityProjector | None = None,
    ):
        self.repo = repo
        self.projector = projector or DeterministicContinuityProjector()

    def view(self, identity: AuthenticatedMember) -> dict[str, Any]:
        episode = self.repo.get_current_campaign_episode(identity.session_id)
        snapshot = self.repo.get_latest_campaign_continuity_snapshot(
            identity.campaign_id
        )
        return {
            "current_episode": episode,
            "latest_snapshot": self._safe_snapshot(snapshot, identity.role),
            "accepts_actions": bool(episode and episode["status"] == "in_progress"),
        }

    def end(
        self, identity: AuthenticatedMember, *, client_end_id: str
    ) -> dict[str, Any]:
        self._require_kp(identity)
        client_end_id = self._client_id(client_end_id)
        self.repo.begin_immediate()
        episode = self.repo.get_current_campaign_episode(identity.session_id)
        if episode is None:
            raise ConflictError("No campaign episode exists for this session")
        if episode["campaign_id"] != identity.campaign_id:
            raise ConflictError("Campaign episode authority changed")
        if episode["status"] == "ended":
            snapshot = self.repo.get_latest_campaign_continuity_snapshot(
                identity.campaign_id
            )
            if snapshot is None:
                raise ConflictError("Ended episode has no continuity snapshot")
            return self.view(identity)
        if episode["status"] == "prepared":
            raise ConflictError("Complete Session 0 before ending the play session")
        blockers = self.repo.get_session_end_blockers(identity.session_id)
        if any(blockers.values()):
            details = ", ".join(
                f"{name}={count}" for name, count in blockers.items() if count
            )
            raise ConflictError(f"Resolve active tabletop work before Session End: {details}")
        cutoff = self.repo.get_continuity_cutoff()
        context = self.repo.get_episode_continuity_context(
            str(episode["id"]), cutoff=cutoff
        )
        projections = self.projector.build(context, cutoff=cutoff)
        events = list(context["events"])
        event_hash = hashlib.sha256(
            json.dumps(
                events, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        snapshot = self.repo.create_continuity_snapshot(
            episode_id=str(episode["id"]),
            client_end_id=client_end_id,
            event_window_hash=event_hash,
            event_ids=[str(item["id"]) for item in events],
            generation_cutoff=cutoff,
            public_projection=projections.player,
            observer_projection=projections.observer,
            kp_projection=projections.kp,
            ended_by_member_id=identity.member_id,
            expected_version=int(episode["version"]),
        )
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=identity.campaign_id,
            audience="session",
            event_type="session.episode_ended",
            resource_type="session_continuity_snapshot",
            resource_id=str(snapshot["id"]),
            payload={"episode_sequence": projections.player["episode_sequence"]},
        )
        return self.view(identity)

    def continue_campaign(
        self, identity: AuthenticatedMember, *, client_continue_id: str
    ) -> dict[str, Any]:
        self._require_kp(identity)
        client_continue_id = self._client_id(client_continue_id)
        self.repo.begin_immediate()
        episode = self.repo.continue_campaign_episode(
            identity.session_id, client_continue_id=client_continue_id
        )
        if not episode.pop("_idempotent_replay", False):
            self.repo.append_realtime_event(
                session_id=identity.session_id,
                campaign_id=identity.campaign_id,
                audience="session",
                event_type="session.episode_started",
                resource_type="campaign_episode",
                resource_id=str(episode["id"]),
                payload={"episode_sequence": episode["sequence_no"]},
            )
        return self.view(identity)

    def transition(
        self,
        identity: AuthenticatedMember,
        *,
        target: str,
        expected_version: int,
    ) -> dict[str, Any]:
        self._require_kp(identity)
        if target not in {"paused", "in_progress"}:
            raise InvalidInputError("Episode target must be paused or in_progress")
        self.repo.begin_immediate()
        episode = self.repo.get_current_campaign_episode(identity.session_id)
        if episode is None or episode["campaign_id"] != identity.campaign_id:
            raise ConflictError("Campaign episode not found")
        self.repo.transition_campaign_episode(
            str(episode["id"]),
            expected_version=expected_version,
            target=target,
        )
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=identity.campaign_id,
            audience="session",
            event_type=f"session.episode_{target}",
            resource_type="campaign_episode",
            resource_id=str(episode["id"]),
            payload={},
        )
        return self.view(identity)

    def require_actions_allowed(self, identity: AuthenticatedMember) -> None:
        episode = self.repo.get_current_campaign_episode(identity.session_id)
        if episode is None or episode["status"] != "in_progress":
            raise ConflictError(
                "The current play session is not in progress; continue or resume it first"
            )

    @staticmethod
    def _safe_snapshot(snapshot: dict | None, role: str) -> dict | None:
        if snapshot is None:
            return None
        projection_key = (
            "kp_projection"
            if role == "kp"
            else "observer_projection" if role == "observer" else "public_projection"
        )
        result = {
            key: snapshot[key]
            for key in (
                "id",
                "episode_id",
                "campaign_id",
                "session_id",
                "generation_cutoff",
                "created_at",
            )
        }
        if role == "kp":
            result["event_window_hash"] = snapshot["event_window_hash"]
        result["projection"] = snapshot[projection_key]
        return result

    @staticmethod
    def _require_kp(identity: AuthenticatedMember) -> None:
        if identity.role != "kp":
            raise PermissionError("KP access required")

    @staticmethod
    def _client_id(value: str) -> str:
        normalized = value.strip()
        if not 8 <= len(normalized) <= 200:
            raise InvalidInputError("Idempotency id must contain 8-200 characters")
        return normalized


__all__ = [
    "ContinuityProjections",
    "DeterministicContinuityProjector",
    "SessionContinuityService",
]

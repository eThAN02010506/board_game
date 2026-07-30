"""Generate evidence-bound recap drafts and apply explicit KP review."""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass

from ai_kp.application.ai_control_service import AiControlService
from ai_kp.application.errors import ConflictError, InvalidInputError
from ai_kp.application.ports.director import SessionRecapDirector
from ai_kp.application.ports.repositories import SessionRecapStore
from ai_kp.director.session_recap import (
    MAX_RECAP_EVENT_CHARACTERS,
    MAX_RECAP_EVENTS,
    PROMPT_VERSION,
)
from ai_kp.platform.sessions.models import AuthenticatedMember


@dataclass(frozen=True)
class ReviewRecapCandidateCommand:
    action: str
    reason: str
    text: str | None = None
    scope: str | None = None
    importance: int | None = None
    visibility: str | None = None
    pc_id: str | None = None
    npc_id: str | None = None
    happened_at: str | None = None


class SessionRecapService:
    def __init__(self, repo: SessionRecapStore):
        self.repo = repo

    async def generate(
        self,
        identity: AuthenticatedMember,
        director: SessionRecapDirector,
        *,
        source_model: str,
    ) -> dict:
        snapshot = self._snapshot(identity.session_id)
        existing = self.repo.find_session_recap_run(
            identity.session_id,
            snapshot["event_window_hash"],
        )
        if existing is not None:
            return existing

        output = None
        repaired = False
        control = None
        if snapshot["events"]:
            control = AiControlService(self.repo).authorize(
                identity.campaign_id,
                "session recap generation",
            )
            result = await director.handle_session_recap(snapshot)
            output = result.output
            repaired = bool(result.repaired)

        candidates = self._validated_candidates(
            [] if output is None else [item.model_dump(mode="json") for item in output.candidates],
            snapshot,
        )

        self.repo.begin_immediate()
        if not self.repo.is_session_member_active(identity.member_id, identity.session_id):
            raise ConflictError("KP session ended while recap generation was running")
        if control is not None:
            AiControlService(self.repo).revalidate(control)
        refreshed = self._snapshot(identity.session_id)
        if refreshed["event_window_hash"] != snapshot["event_window_hash"]:
            raise ConflictError("Session events changed while recap generation was running")
        existing = self.repo.find_session_recap_run(
            identity.session_id,
            snapshot["event_window_hash"],
        )
        if existing is not None:
            return existing
        run = self.repo.create_session_recap_run(
            campaign_id=identity.campaign_id,
            session_id=identity.session_id,
            event_window_hash=snapshot["event_window_hash"],
            event_ids=[item["id"] for item in snapshot["events"]],
            generation_cutoff=refreshed["generation_cutoff"],
            source_model=source_model,
            prompt_version=PROMPT_VERSION,
            repaired=repaired,
            created_by_member_id=identity.member_id,
            candidates=candidates,
        )
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=identity.campaign_id,
            audience="kp",
            event_type="session_recap.generated",
            resource_type="session_recap_run",
            resource_id=run["id"],
            payload={"candidate_count": len(candidates)},
        )
        return run

    def latest(self, identity: AuthenticatedMember) -> dict | None:
        return self.repo.get_latest_session_recap_run(identity.session_id)

    def review(
        self,
        candidate_id: str,
        identity: AuthenticatedMember,
        command: ReviewRecapCandidateCommand,
    ) -> dict:
        if command.action not in {"approve", "reject"}:
            raise InvalidInputError("Recap action must be approve or reject")
        candidate = self.repo.get_session_recap_candidate(candidate_id)
        run = self.repo.get_session_recap_run(str(candidate["run_id"]))
        if (
            run["campaign_id"] != identity.campaign_id
            or run["session_id"] != identity.session_id
        ):
            raise KeyError(f"Session recap candidate not found: {candidate_id}")

        effective = {
            "text": (command.text if command.text is not None else candidate["text"]).strip(),
            "scope": command.scope or candidate["scope"],
            "importance": (
                command.importance
                if command.importance is not None
                else candidate["importance"]
            ),
            "visibility": command.visibility or candidate["visibility"],
            "pc_id": command.pc_id if command.pc_id is not None else candidate["pc_id"],
            "npc_id": (
                command.npc_id if command.npc_id is not None else candidate["npc_id"]
            ),
            "happened_at": (
                command.happened_at
                if command.happened_at is not None
                else candidate["happened_at"]
            ),
        }
        if command.action == "approve":
            self._validate_effective_review(candidate, effective)
        reason = command.reason.strip()
        if not reason:
            raise InvalidInputError("KP review reason is required")
        decision_hash = self._hash(
            {
                "candidate_id": candidate_id,
                "action": command.action,
                "effective": effective,
                "reason": reason,
            }
        )
        reviewed = self.repo.review_session_recap_candidate(
            candidate_id,
            action=command.action,
            effective=effective,
            decision_hash=decision_hash,
            reason=reason,
            reviewed_by_member_id=identity.member_id,
        )
        if reviewed.pop("_idempotent_replay", False):
            return reviewed
        audience = (
            "kp"
            if command.action == "reject" or effective["visibility"] == "kp"
            else "session"
        )
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=identity.campaign_id,
            audience=audience,
            event_type=f"session_recap.{reviewed['status']}",
            resource_type="session_recap_candidate",
            resource_id=candidate_id,
            payload={"memory_id": reviewed.get("memory_id")},
        )
        return reviewed

    def _snapshot(self, session_id: str) -> dict:
        context = self.repo.get_session_recap_snapshot_context(session_id)
        if context["session"]["status"] != "active":
            raise ConflictError("Recap generation requires an active KP session")
        cutoff = self.repo.get_recap_generation_cutoff()
        events = self.repo.list_session_recap_events(session_id, cutoff=cutoff)
        if len(events) > MAX_RECAP_EVENTS:
            raise InvalidInputError(
                f"Session has {len(events)} events; recap limit is {MAX_RECAP_EVENTS}"
            )
        canonical_events = json.dumps(
            events,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(canonical_events) > MAX_RECAP_EVENT_CHARACTERS:
            raise InvalidInputError(
                "Session event window exceeds the 60,000-character recap limit"
            )
        event_window_hash = hashlib.sha256(canonical_events.encode("utf-8")).hexdigest()
        return {
            **context,
            "campaign_id": str(context["session"]["campaign_id"]),
            "events": events,
            "generation_cutoff": cutoff,
            "event_window_hash": event_window_hash,
        }

    def _validated_candidates(self, candidates: list[dict], snapshot: dict) -> list[dict]:
        events = {item["id"]: item for item in snapshot["events"]}
        pc_ids = {item["id"] for item in snapshot["pcs"]}
        npc_ids = {item["id"] for item in snapshot["npcs"]}
        existing_text = {
            self._normalize(item["text"]) for item in snapshot["existing_memories"]
        }
        seen: set[tuple] = set()
        validated: list[dict] = []
        for item in candidates:
            source_ids = item["source_event_ids"]
            if any(event_id not in events for event_id in source_ids):
                raise InvalidInputError("Recap candidate cited an event outside the frozen window")
            if item.get("pc_id") and item["pc_id"] not in pc_ids:
                raise InvalidInputError("Recap candidate cited an unknown campaign PC")
            if item.get("npc_id") and item["npc_id"] not in npc_ids:
                raise InvalidInputError("Recap candidate cited an unknown campaign NPC")
            normalized = self._normalize(item["text"])
            if normalized in existing_text:
                continue
            item["visibility"] = self._safe_visibility(item, events)
            if item["visibility"] == "player" and not item.get("pc_id"):
                item["visibility"] = "kp"
            if not item.get("happened_at"):
                item["happened_at"] = events[source_ids[0]].get("happened_at")
            key = (
                normalized,
                item["scope"],
                item.get("pc_id"),
                item.get("npc_id"),
            )
            if key in seen:
                continue
            seen.add(key)
            validated.append(item)
        return validated

    def _validate_effective_review(self, candidate: dict, effective: dict) -> None:
        if not effective["text"] or len(effective["text"]) > 2000:
            raise InvalidInputError("Approved recap text must contain 1-2000 characters")
        if effective["scope"] not in {
            "campaign_fact",
            "pc_major",
            "pc_side",
            "npc_interaction",
            "npc_relationship",
            "location_fact",
            "clue",
        }:
            raise InvalidInputError("Invalid recap memory scope")
        if not isinstance(effective["importance"], int) or not 1 <= effective["importance"] <= 5:
            raise InvalidInputError("Recap importance must be between 1 and 5")
        if effective["visibility"] not in {"player", "table", "kp"}:
            raise InvalidInputError("Invalid recap visibility")
        events = self.repo.list_events_by_ids(
            str(candidate["campaign_id"]),
            list(candidate["source_event_ids"]),
        )
        if len(events) != len(candidate["source_event_ids"]):
            raise ConflictError("A recap source event is no longer available")
        allowed = self._safe_visibility(
            {**effective, "source_event_ids": candidate["source_event_ids"]},
            {item["id"]: item for item in events},
        )
        if allowed != effective["visibility"]:
            raise InvalidInputError(
                f"Recap visibility cannot expose its source events; use {allowed}"
            )
        context = self.repo.get_session_recap_snapshot_context(
            str(self.repo.get_session_recap_run(str(candidate["run_id"]))["session_id"])
        )
        if effective.get("pc_id") not in {None, *(item["id"] for item in context["pcs"])}:
            raise InvalidInputError("Approved recap cited an unknown campaign PC")
        if effective.get("npc_id") not in {None, *(item["id"] for item in context["npcs"])}:
            raise InvalidInputError("Approved recap cited an unknown campaign NPC")
        if effective["visibility"] == "player" and not effective.get("pc_id"):
            raise InvalidInputError("Player-visible recap memory requires a PC")

    @staticmethod
    def _safe_visibility(candidate: dict, events: dict[str, dict]) -> str:
        sources = [events[event_id] for event_id in candidate["source_event_ids"]]
        if any(item["visibility"] in {"kp", "secret"} for item in sources):
            return "kp"
        player_sources = [item for item in sources if item["visibility"] == "player"]
        if player_sources:
            pc_id = candidate.get("pc_id")
            if not pc_id or any(item.get("actor_id") != pc_id for item in player_sources):
                return "kp"
            return "kp" if candidate["visibility"] == "kp" else "player"
        return candidate["visibility"]

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()

    @staticmethod
    def _hash(value: dict) -> str:
        return hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

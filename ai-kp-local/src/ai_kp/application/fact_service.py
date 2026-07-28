"""Application service for typed, append-only world facts."""

from dataclasses import dataclass, field
from typing import Any

from ai_kp.application.ports.facts import FactStore
from ai_kp.core.ids import new_id
from ai_kp.platform.facts import (
    FACT_CATEGORIES,
    AppendOnlyCorrectionCommand,
    FactLedgerEntry,
    WorldFact,
    visible_fact_heads,
)
from ai_kp.platform.sessions.models import AuthenticatedMember


_CATEGORY_VISIBILITY = {
    "canonical_fact": "table",
    "kp_secret": "kp",
    "character_belief": "player",
    "rumor": "table",
    "ai_hypothesis": "kp",
}


@dataclass(frozen=True)
class AssertWorldFactCommand:
    fact_type: str
    subject: str
    predicate: str
    object_text: str
    pc_id: str | None = None
    evidence_event_ids: tuple[str, ...] = ()
    source_reference: dict[str, Any] = field(default_factory=dict)
    happened_at: str | None = None


@dataclass(frozen=True)
class RetconWorldFactCommand:
    expected_head_event_id: str
    reason: str
    evidence_event_ids: tuple[str, ...] = ()
    source_reference: dict[str, Any] = field(default_factory=dict)
    happened_at: str | None = None


class FactService:
    """Keep facts typed, scoped, auditable, and derived from append-only events."""

    def __init__(self, repo: FactStore):
        self.repo = repo

    def assert_fact(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: AssertWorldFactCommand,
    ) -> dict[str, Any]:
        self._require_kp(identity, campaign_id)
        if command.fact_type not in _CATEGORY_VISIBILITY:
            raise ValueError(
                "fact_type must be canonical_fact, kp_secret, character_belief, "
                "rumor, or ai_hypothesis"
            )
        fact = WorldFact(
            fact_id=new_id("evt"),
            category=command.fact_type,  # type: ignore[arg-type]
            visibility=_CATEGORY_VISIBILITY[command.fact_type],  # type: ignore[arg-type]
            subject=command.subject,
            predicate=command.predicate,
            object_text=command.object_text,
            pc_id=command.pc_id,
        )
        self.repo.begin_immediate()
        conflict = self.repo.find_active_fact(
            campaign_id,
            category=fact.category,
            subject=fact.subject,
            predicate=fact.predicate,
        )
        if conflict is not None and fact.category in {"canonical_fact", "kp_secret"}:
            raise ValueError(
                "An active authoritative fact already uses this subject and predicate; "
                f"retcon {conflict.fact_key} first"
            )
        entry = FactLedgerEntry(
            campaign_id=campaign_id,
            fact_key=new_id("fact"),
            event_id=fact.fact_id,
            revision=1,
            fact=fact,
            asserted_by=f"kp:{identity.member_id}",
            evidence_event_ids=command.evidence_event_ids,
            source_reference=command.source_reference,
            happened_at=command.happened_at,
        )
        saved = self.repo.append_fact_entry(entry)
        self._publish_change(identity, saved)
        return saved.as_dict()

    def retcon_fact(
        self,
        campaign_id: str,
        fact_key: str,
        identity: AuthenticatedMember,
        command: RetconWorldFactCommand,
    ) -> dict[str, Any]:
        self._require_kp(identity, campaign_id)
        self.repo.begin_immediate()
        current = self.repo.get_fact_head(campaign_id, fact_key)
        if current.event_id != command.expected_head_event_id:
            raise ValueError(
                "World fact head changed; refresh before correcting it"
            )
        if not current.active:
            raise ValueError("World fact is already retconned")
        correction_fact = WorldFact(
            fact_id=new_id("evt"),
            category="retconned",
            visibility=current.fact.visibility,
            subject=current.fact.subject,
            predicate=current.fact.predicate,
            object_text=command.reason,
            pc_id=current.fact.pc_id,
            supersedes_fact_id=current.event_id,
        )
        AppendOnlyCorrectionCommand(current.event_id, correction_fact)
        correction = FactLedgerEntry(
            campaign_id=campaign_id,
            fact_key=current.fact_key,
            event_id=correction_fact.fact_id,
            revision=current.revision + 1,
            supersedes_event_id=current.event_id,
            fact=correction_fact,
            asserted_by=f"kp:{identity.member_id}",
            evidence_event_ids=command.evidence_event_ids,
            source_reference=command.source_reference,
            happened_at=command.happened_at,
        )
        saved = self.repo.append_fact_entry(correction)
        self._publish_change(identity, saved)
        return {
            "append_only": True,
            "retained_revision": current.as_dict(),
            "appended_revision": saved.as_dict(),
        }

    def list_facts(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        *,
        include_history: bool = False,
        fact_type: str | None = None,
    ) -> list[dict[str, Any]]:
        self._require_campaign(identity, campaign_id)
        if fact_type is not None and fact_type not in FACT_CATEGORIES:
            raise ValueError(f"Unknown fact type: {fact_type}")
        entries = self.repo.list_fact_entries(campaign_id)
        if identity.role == "kp" and include_history:
            selected = sorted(
                entries,
                key=lambda item: (item.created_at or "", item.event_id),
                reverse=True,
            )
        else:
            selected = visible_fact_heads(
                entries,
                role=identity.role,
                pc_id=identity.pc_id,
            )
        if fact_type is not None:
            selected = [
                entry for entry in selected if entry.fact.category == fact_type
            ]
        return [entry.as_dict() for entry in selected]

    def get_fact(
        self,
        campaign_id: str,
        fact_key: str,
        identity: AuthenticatedMember,
    ) -> dict[str, Any]:
        self._require_campaign(identity, campaign_id)
        current = self.repo.get_fact_head(campaign_id, fact_key)
        visible = visible_fact_heads(
            self.repo.list_fact_entries(campaign_id),
            role=identity.role,
            pc_id=identity.pc_id,
            include_retconned=identity.role == "kp",
        )
        if current not in visible:
            raise KeyError(f"World fact not found: {fact_key}")
        return current.as_dict()

    def _publish_change(
        self,
        identity: AuthenticatedMember,
        entry: FactLedgerEntry,
    ) -> None:
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=entry.campaign_id,
            audience="kp",
            event_type="world_fact.changed",
            resource_type="world_fact",
            resource_id=entry.fact_key,
            payload={"revision": entry.revision, "active": entry.active},
        )
        if entry.fact.visibility == "table":
            self.repo.append_realtime_event(
                session_id=identity.session_id,
                campaign_id=entry.campaign_id,
                audience="session",
                event_type="world.updated",
                resource_type="campaign",
                resource_id=entry.campaign_id,
                payload={"change": "world_fact"},
            )

    @staticmethod
    def _require_campaign(
        identity: AuthenticatedMember,
        campaign_id: str,
    ) -> None:
        if identity.campaign_id != campaign_id:
            raise KeyError(f"Campaign not found: {campaign_id}")

    @classmethod
    def _require_kp(
        cls,
        identity: AuthenticatedMember,
        campaign_id: str,
    ) -> None:
        cls._require_campaign(identity, campaign_id)
        if identity.role != "kp":
            raise PermissionError("KP access required")


__all__ = [
    "AssertWorldFactCommand",
    "FactService",
    "RetconWorldFactCommand",
]

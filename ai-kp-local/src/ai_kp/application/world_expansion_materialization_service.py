"""Atomically project an approved world-expansion encounter into durable world state."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Literal

from ai_kp.application.dynamic_branch_service import DynamicBranchService
from ai_kp.application.errors import ConflictError, InvalidInputError
from ai_kp.application.npc_reappearance_service import NpcReappearanceService
from ai_kp.application.ports.world_expansion_materializations import (
    WorldExpansionMaterializationStore,
)
from ai_kp.application.world_entity_materializer import (
    EncounterEntityRealization,
    WorldEntityMaterializer,
)
from ai_kp.core.ids import new_id
from ai_kp.platform.facts import FactLedgerEntry, WorldFact
from ai_kp.platform.sessions.models import AuthenticatedMember

MaterializedFactType = Literal["canonical_fact", "kp_secret", "rumor"]
_FACT_VISIBILITY = {
    "canonical_fact": "table",
    "kp_secret": "kp",
    "rumor": "table",
}
_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_MATERIALIZED_ACTION_TYPE = "world_expansion_materialized"


@dataclass(frozen=True)
class EncounterFact:
    fact_type: MaterializedFactType
    subject: str
    predicate: str
    object_text: str


@dataclass(frozen=True)
class EncounterNpc:
    npc_id: str | None = None
    name: str | None = None
    home_location: str | None = None
    profession: str | None = None
    public_notes: str = ""
    secret_notes: str = ""
    role: str = "encountered"
    relationship_score: int = 0
    notes: str = ""


@dataclass(frozen=True)
class EncounterMapPlacement:
    map_id: str
    location_name: str
    entity_ref: str | None = None
    visibility: Literal["table", "kp"] = "table"
    color: str = "#b93f2d"


@dataclass(frozen=True)
class MaterializeWorldExpansionCommand:
    idempotency_key: str
    summary: str
    happened_at: str | None
    facts: tuple[EncounterFact, ...]
    entities: tuple[EncounterEntityRealization, ...] = ()
    npc: EncounterNpc | None = None
    map_placement: EncounterMapPlacement | None = None
    participant_investigator_ids: tuple[str, ...] = ()
    interaction_summary: str | None = None
    profession_context: str | None = None


class WorldExpansionMaterializationService:
    """Turn confirmed play at the table into one auditable, idempotent write."""

    def __init__(self, repo: WorldExpansionMaterializationStore):
        self.repo = repo

    def materialize(
        self,
        proposal_id: str,
        identity: AuthenticatedMember,
        command: MaterializeWorldExpansionCommand,
    ) -> dict:
        self._validate_command(command)
        command_hash = self._command_hash(command)
        self.repo.begin_immediate()
        self.repo.begin_world_expansion_materialization()
        try:
            result = self._materialize_locked(
                proposal_id,
                identity,
                command,
                command_hash=command_hash,
            )
            self.repo.finish_world_expansion_materialization()
            return result
        except Exception:
            self.repo.rollback_world_expansion_materialization()
            raise

    def _materialize_locked(
        self,
        proposal_id: str,
        identity: AuthenticatedMember,
        command: MaterializeWorldExpansionCommand,
        *,
        command_hash: str,
    ) -> dict:
        proposal = self.repo.get_turn_proposal(proposal_id)
        campaign_id = str(proposal["campaign_id"])
        self._require_kp(identity, campaign_id)

        by_key = self.repo.find_world_expansion_materialization_by_key(
            campaign_id,
            command.idempotency_key,
        )
        if by_key is not None:
            if by_key["proposal_id"] != proposal_id or by_key["command_hash"] != command_hash:
                raise ConflictError("Idempotency key was already used for a different encounter")
            return self.repo.get_turn_proposal(proposal_id)

        existing = self.repo.get_world_expansion_materialization(proposal_id)
        if existing is not None:
            raise ConflictError("This world expansion was already materialized")
        if proposal.get("proposal_kind") != "world_expansion":
            raise InvalidInputError("Only world expansion proposals can be materialized")
        if proposal["status"] != "approved":
            raise ConflictError("World expansion must be approved before confirming contact")
        if not self.repo.is_session_member_active(
            identity.member_id,
            identity.session_id,
        ):
            raise ConflictError("KP session ended before contact confirmation")

        participant_ids = tuple(dict.fromkeys(command.participant_investigator_ids))
        self.repo.require_approved_contact_investigators(
            campaign_id,
            participant_ids,
        )
        is_reappearance = bool(
            command.npc
            and command.npc.npc_id
            and not self.repo.npc_is_linked_to_campaign(
                campaign_id,
                command.npc.npc_id,
            )
        )
        if is_reappearance:
            self._require_reappearance_eligible(
                campaign_id,
                str(command.npc.npc_id),
                participant_ids,
                command,
            )
        prepared_facts = self._prepare_facts(
            campaign_id,
            identity,
            command.facts,
        )
        materialization_id = new_id("worldmat")
        expansion = proposal["world_expansion"]
        encounter_event = self.repo.append_event(
            campaign_id=campaign_id,
            actor_type="pc" if proposal.get("pc_id") else "kp",
            actor_id=proposal.get("pc_id") or identity.member_id,
            visibility="table",
            event_type="world_expansion.encountered",
            happened_at=command.happened_at,
            summary=self._text(command.summary, "summary", 2000),
            payload={
                "schema_version": "world-expansion-materialization.v1",
                "materialization_id": materialization_id,
                "proposal_id": proposal_id,
                "module_run_id": expansion["module_run_id"],
                "candidate_subject": expansion["candidate"]["subject"],
                "template_binding": expansion["candidate"].get("template_binding"),
            },
        )

        template_entities = WorldEntityMaterializer(self.repo).materialize(
            campaign_id=campaign_id,
            proposal_id=proposal_id,
            encounter_event_id=str(encounter_event["id"]),
            expansion=expansion,
            realizations=command.entities,
            happened_at=command.happened_at,
        )
        candidate_bindings = (
            (expansion["candidate"].get("template_binding") or {}).get(
                "entity_bindings", ()
            )
        )
        if command.npc is not None and any(
            item.get("entity_kind") == "npc" for item in candidate_bindings
        ):
            raise InvalidInputError(
                "Typed NPC candidates are materialized from entity realizations; "
                "a separate manual NPC would not match the approved candidate"
            )

        npc = self._materialize_npc(
            campaign_id,
            command,
            participant_ids=participant_ids,
            happened_at=command.happened_at,
        )
        map_token = self._materialize_map(
            campaign_id,
            identity,
            command,
            npc,
            template_entities.entities_by_ref,
        )

        fact_entries: list[FactLedgerEntry] = []
        for entry in prepared_facts:
            sourced_entry = FactLedgerEntry(
                campaign_id=entry.campaign_id,
                fact_key=entry.fact_key,
                event_id=entry.event_id,
                revision=entry.revision,
                fact=entry.fact,
                asserted_by=entry.asserted_by,
                evidence_event_ids=(str(encounter_event["id"]),),
                source_reference={
                    "kind": "world_expansion_contact",
                    "materialization_id": materialization_id,
                    "proposal_id": proposal_id,
                    "module_id": expansion["module_id"],
                    "module_run_id": expansion["module_run_id"],
                    "module_source_hash": expansion["module_source_hash"],
                    "candidate_fingerprint": expansion["fingerprint"],
                    "template_binding": expansion["candidate"].get("template_binding"),
                },
                happened_at=command.happened_at,
            )
            fact_entries.append(self.repo.append_fact_entry(sourced_entry))

        npc_records = list(template_entities.npc_records)
        if npc is not None:
            npc_records.append(npc)
        npc_records = list({str(item["id"]): item for item in npc_records}.values())
        if command.participant_investigator_ids and not npc_records:
            raise InvalidInputError(
                "Investigator/NPC history requires a materialized NPC entity"
            )
        primary_npc = npc or (npc_records[0] if npc_records else None)
        materialization = self.repo.create_world_expansion_materialization(
            materialization_id=materialization_id,
            proposal_id=proposal_id,
            campaign_id=campaign_id,
            idempotency_key=command.idempotency_key,
            command_hash=command_hash,
            encounter_event_id=str(encounter_event["id"]),
            fact_event_ids=[item.event_id for item in fact_entries],
            npc_id=str(primary_npc["id"]) if primary_npc else None,
            map_token_id=str(map_token["id"]) if map_token else None,
            created_by_member_id=identity.member_id,
            payload={
                "summary": command.summary,
                "happened_at": command.happened_at,
                "fact_count": len(fact_entries),
                "map_id": command.map_placement.map_id if command.map_placement else None,
                "map_location_name": command.map_placement.location_name
                if command.map_placement
                else None,
                "participant_investigator_ids": list(participant_ids),
                "template_binding": expansion["candidate"].get("template_binding"),
                "world_entity_ids": [
                    item["world_entity_id"] for item in template_entities.attachments
                ],
            },
        )
        self.repo.attach_world_expansion_materialization_entities(
            materialization_id,
            list(template_entities.attachments),
        )
        materialization = self.repo.get_world_expansion_materialization(proposal_id)
        if materialization is None:
            raise RuntimeError("World expansion entity attachments were not persisted")
        appearance = None
        if is_reappearance and npc is not None:
            appearance = self.repo.record_npc_reappearance(
                campaign_id=campaign_id,
                npc_id=str(npc["id"]),
                materialization_id=materialization_id,
                appeared_at=command.happened_at,
            )
        investigator_encounters = []
        if npc_records and participant_ids:
            interaction_summary = self._text(
                command.interaction_summary or command.summary,
                "interaction_summary",
                1000,
            )
            for npc_record in npc_records:
                for investigator_id in participant_ids:
                    investigator_encounters.append(
                        self.repo.record_investigator_npc_encounter(
                            investigator_id=investigator_id,
                            npc_id=str(npc_record["id"]),
                            campaign_id=campaign_id,
                            source_event_id=str(encounter_event["id"]),
                            materialization_id=materialization_id,
                            interaction_summary=interaction_summary,
                            happened_at=command.happened_at,
                        )
                    )
        dynamic_branch = DynamicBranchService(self.repo).activate_after_contact(
            proposal_id,
            identity,
            command_id=f"contact:{materialization_id}",
        )
        action_payload = {
            "schema_version": "world-expansion-materialization.v1",
            "materialization_id": materialization["id"],
            "encounter_event_id": materialization["encounter_event_id"],
            "fact_event_ids": materialization["fact_event_ids"],
            "npc_id": materialization["npc_id"],
            "map_token_id": materialization["map_token_id"],
            "world_entity_ids": materialization["world_entity_ids"],
            "npc_ids": [str(item["id"]) for item in npc_records],
            "investigator_encounter_ids": [item["id"] for item in investigator_encounters],
            "npc_reappearance_id": appearance["id"] if appearance else None,
            "dynamic_branch_id": dynamic_branch["id"] if dynamic_branch else None,
            "dynamic_branch_status": (dynamic_branch["status"] if dynamic_branch else None),
            "created_at": materialization["created_at"],
        }
        self.repo.add_proposal_action(
            proposal_id,
            _MATERIALIZED_ACTION_TYPE,
            actor=f"kp:{identity.member_id}",
            note="confirmed actual contact and projected durable world state",
            payload=action_payload,
        )
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=campaign_id,
            audience="kp",
            event_type="world_expansion.materialized",
            resource_type="turn_proposal",
            resource_id=proposal_id,
            payload=action_payload,
        )
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=campaign_id,
            audience="session",
            event_type="world.updated",
            resource_type="campaign",
            resource_id=campaign_id,
            payload={"change": "world_expansion_contact"},
        )
        return self.repo.get_turn_proposal(proposal_id)

    def _require_reappearance_eligible(
        self,
        campaign_id: str,
        npc_id: str,
        participant_ids: tuple[str, ...],
        command: MaterializeWorldExpansionCommand,
    ) -> None:
        if not self.repo.npc_is_authorized_reappearance(
            campaign_id,
            npc_id,
            participant_ids,
        ):
            raise InvalidInputError(
                "Existing NPC requires a qualifying prior encounter by an approved participant"
            )
        policy = self.repo.get_campaign_npc_reappearance_policy(campaign_id)
        used = self.repo.count_npc_reappearances(campaign_id)
        if used >= int(policy["max_returning_npcs"]):
            raise ConflictError("Campaign returning-NPC budget is exhausted")
        gate_service = NpcReappearanceService(self.repo)
        profile = self.repo.get_npc_availability_profile(npc_id)
        context_location = (
            command.map_placement.location_name if command.map_placement is not None else None
        )
        decision = gate_service.evaluate(
            campaign_time=self.repo.get_campaign(campaign_id).get("current_time"),
            policy=policy,
            profile=profile,
            context_location=context_location,
            profession_hint=command.profession_context,
            travel_result=gate_service.travel_result(
                campaign_id,
                profile,
                context_location,
                int(policy["max_travel_minutes"]),
            ),
            final=True,
        )
        if decision["decision"] == "blocked":
            raise ConflictError("NPC appearance is impossible: " + "; ".join(decision["warnings"]))

    def _prepare_facts(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        facts: tuple[EncounterFact, ...],
    ) -> list[FactLedgerEntry]:
        prepared: list[FactLedgerEntry] = []
        identities: set[tuple[str, str, str]] = set()
        for item in facts:
            if item.fact_type not in _FACT_VISIBILITY:
                raise InvalidInputError(
                    "Encounter facts must be canonical_fact, kp_secret, or rumor"
                )
            event_id = new_id("evt")
            fact = WorldFact(
                fact_id=event_id,
                category=item.fact_type,
                visibility=_FACT_VISIBILITY[item.fact_type],  # type: ignore[arg-type]
                subject=item.subject,
                predicate=item.predicate,
                object_text=item.object_text,
            )
            identity_key = (fact.category, fact.subject, fact.predicate)
            if identity_key in identities:
                raise InvalidInputError(
                    "Encounter facts cannot repeat the same type, subject, and predicate"
                )
            identities.add(identity_key)
            conflict = self.repo.find_active_fact(
                campaign_id,
                category=fact.category,
                subject=fact.subject,
                predicate=fact.predicate,
            )
            if conflict is not None:
                raise ConflictError(
                    "An active world fact already uses "
                    f"{fact.subject} / {fact.predicate}; retcon or revise first"
                )
            prepared.append(
                FactLedgerEntry(
                    campaign_id=campaign_id,
                    fact_key=new_id("fact"),
                    event_id=event_id,
                    revision=1,
                    fact=fact,
                    asserted_by=f"kp:{identity.member_id}",
                )
            )
        return prepared

    def _materialize_npc(
        self,
        campaign_id: str,
        command: MaterializeWorldExpansionCommand,
        *,
        participant_ids: tuple[str, ...],
        happened_at: str | None,
    ) -> dict | None:
        item = command.npc
        if item is None:
            return None
        already_linked = False
        if item.npc_id:
            already_linked = self.repo.npc_is_linked_to_campaign(
                campaign_id,
                item.npc_id,
            )
            if already_linked:
                npc = self.repo.get_campaign_npc(campaign_id, item.npc_id)
            elif self.repo.npc_is_authorized_reappearance(
                campaign_id,
                item.npc_id,
                participant_ids,
            ):
                npc = self.repo.get_npc(item.npc_id)
            else:
                raise InvalidInputError(
                    "Existing NPC requires a qualifying prior encounter by an approved participant"
                )
        else:
            npc = self.repo.create_npc(
                name=self._text(item.name or "", "npc.name", 200),
                home_location=self._optional_text(
                    item.home_location,
                    "npc.home_location",
                    300,
                ),
                profession=self._optional_text(
                    item.profession,
                    "npc.profession",
                    200,
                ),
                public_notes=self._text(
                    item.public_notes,
                    "npc.public_notes",
                    4000,
                    allow_blank=True,
                ),
                secret_notes=self._text(
                    item.secret_notes,
                    "npc.secret_notes",
                    8000,
                    allow_blank=True,
                ),
            )
        if already_linked:
            self.repo.update_campaign_npc_last_seen(
                campaign_id,
                str(npc["id"]),
                happened_at,
            )
        else:
            self.repo.link_npc_to_campaign(
                campaign_id,
                str(npc["id"]),
                role=self._text(item.role, "npc.role", 120),
                first_seen_time=happened_at,
                last_seen_time=happened_at,
                relationship_score=item.relationship_score,
                notes=self._text(
                    item.notes,
                    "npc.notes",
                    4000,
                    allow_blank=True,
                ),
            )
        return npc

    def _materialize_map(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: MaterializeWorldExpansionCommand,
        npc: dict | None,
        entities_by_ref: dict[str, dict],
    ) -> dict | None:
        placement = command.map_placement
        if placement is None:
            return None
        if placement.entity_ref is not None:
            world_entity = entities_by_ref.get(placement.entity_ref)
            if world_entity is None:
                raise InvalidInputError("Map placement references an unknown approved entity")
            if not world_entity.get("npc_id"):
                raise InvalidInputError("Map placement currently requires an NPC entity")
            npc = self.repo.get_npc(str(world_entity["npc_id"]))
        if npc is None:
            raise InvalidInputError("Map placement requires an encountered NPC")
        saved_map = self.repo.get_map(placement.map_id)
        if saved_map["campaign_id"] != campaign_id:
            raise InvalidInputError("Map placement belongs to another campaign")
        if not any(item["name"] == placement.location_name for item in saved_map["locations"]):
            raise InvalidInputError("Map placement must use an existing reviewed map location")
        npc_id = str(npc["id"])
        existing = self.repo.find_map_token_for_actor(
            placement.map_id,
            "npc",
            npc_id,
        )
        if existing is None:
            return self.repo.place_map_token(
                map_id=placement.map_id,
                label=str(npc["name"]),
                location_name=placement.location_name,
                actor_type="npc",
                actor_id=npc_id,
                visibility=placement.visibility,
                color=placement.color,
            )
        if existing["location_name"] == placement.location_name:
            return existing
        return self.repo.move_map_token(
            token_id=str(existing["id"]),
            to_location_name=placement.location_name,
            moved_by=f"kp:{identity.member_id}",
            note=f"confirmed world expansion contact in {campaign_id}",
            require_route=False,
            expected_version=int(existing["version"]),
        )

    @staticmethod
    def _validate_command(command: MaterializeWorldExpansionCommand) -> None:
        if not _IDEMPOTENCY_PATTERN.fullmatch(command.idempotency_key):
            raise InvalidInputError(
                "Idempotency key must contain 8-128 letters, digits, '.', '_', ':', or '-'"
            )
        if not 1 <= len(command.facts) <= 8:
            raise InvalidInputError("Contact confirmation requires 1-8 world facts")
        if command.npc is not None:
            has_id = bool(command.npc.npc_id)
            has_name = bool(command.npc.name and command.npc.name.strip())
            if has_id == has_name:
                raise InvalidInputError("Encountered NPC requires exactly one of npc_id or name")
            if not -100 <= command.npc.relationship_score <= 100:
                raise InvalidInputError("NPC relationship_score must be between -100 and 100")
        if (
            command.map_placement is not None
            and command.npc is None
            and command.map_placement.entity_ref is None
        ):
            raise InvalidInputError(
                "Map placement requires an encountered NPC or approved entity ref"
            )
        if len(command.participant_investigator_ids) > 12:
            raise InvalidInputError("Contact can include at most 12 investigators")
        if len(set(command.participant_investigator_ids)) != len(
            command.participant_investigator_ids
        ):
            raise InvalidInputError("Contact participant investigators must be unique")
        if (
            command.participant_investigator_ids
            and command.npc is None
            and not command.entities
        ):
            raise InvalidInputError("Investigator/NPC history requires an encountered NPC")
        if command.interaction_summary is not None:
            WorldExpansionMaterializationService._text(
                command.interaction_summary,
                "interaction_summary",
                1000,
            )

    @staticmethod
    def _command_hash(command: MaterializeWorldExpansionCommand) -> str:
        encoded = json.dumps(
            asdict(command),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _text(
        value: str,
        field_name: str,
        max_length: int,
        *,
        allow_blank: bool = False,
    ) -> str:
        normalized = " ".join(unicodedata.normalize("NFKC", value).split())
        if not normalized and not allow_blank:
            raise InvalidInputError(f"{field_name} cannot be blank")
        if len(normalized) > max_length:
            raise InvalidInputError(f"{field_name} cannot exceed {max_length} characters")
        return normalized

    @classmethod
    def _optional_text(
        cls,
        value: str | None,
        field_name: str,
        max_length: int,
    ) -> str | None:
        if value is None:
            return None
        normalized = cls._text(
            value,
            field_name,
            max_length,
            allow_blank=True,
        )
        return normalized or None

    @staticmethod
    def _require_kp(
        identity: AuthenticatedMember,
        campaign_id: str,
    ) -> None:
        if identity.campaign_id != campaign_id:
            raise KeyError(f"Campaign not found: {campaign_id}")
        if identity.role != "kp":
            raise PermissionError("KP access required")


__all__ = [
    "EncounterEntityRealization",
    "EncounterFact",
    "EncounterMapPlacement",
    "EncounterNpc",
    "MaterializeWorldExpansionCommand",
    "WorldExpansionMaterializationService",
]

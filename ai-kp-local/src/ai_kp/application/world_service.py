from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from ai_kp.application.ports.repositories import WorldStore
from ai_kp.application.scenario_entity_identity import validate_scenario_entity_identities
from ai_kp.platform.modules.ingestion import chunk_plaintext_module

WorldView = Literal["player", "kp"]


@dataclass(frozen=True)
class CreatePcCommand:
    name: str
    sheet: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AppendEventCommand:
    actor_type: str
    event_type: str
    summary: str
    actor_id: str | None = None
    visibility: str = "table"
    happened_at: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AddMemoryCommand:
    text: str
    scope: str
    pc_id: str | None = None
    npc_id: str | None = None
    importance: int = 1
    visibility: str = "table"
    happened_at: str | None = None
    source_event_id: str | None = None


@dataclass(frozen=True)
class ImportModuleCommand:
    title: str
    text: str
    source_type: str = "plaintext"
    default_visibility: str = "kp"


@dataclass(frozen=True)
class CreateNpcCommand:
    name: str
    home_location: str | None = None
    profession: str | None = None
    public_notes: str = ""
    secret_notes: str = ""


@dataclass(frozen=True)
class LinkNpcCommand:
    role: str = "encountered"
    first_seen_time: str | None = None
    last_seen_time: str | None = None
    relationship_score: int = 0
    notes: str = ""
    participant_investigator_ids: tuple[str, ...] = ()


class WorldService:
    """Coordinate durable campaign-world writes and visibility-aware queries."""

    def __init__(self, repo: WorldStore):
        self.repo = repo

    def create_pc(
        self,
        campaign_id: str,
        session_id: str,
        command: CreatePcCommand,
    ) -> dict:
        pc = self.repo.create_pc(campaign_id, command.name, command.sheet)
        self.repo.append_realtime_event(
            session_id=session_id,
            campaign_id=campaign_id,
            audience="session",
            event_type="pc.created",
            resource_type="player_character",
            resource_id=pc["id"],
        )
        return pc

    def list_pcs(self, campaign_id: str) -> list[dict]:
        return self.repo.list_pcs(campaign_id)

    def list_world_entities(self, campaign_id: str, *, view: WorldView) -> dict:
        allowed_visibility = (
            {"table"} if view == "player" else {"table", "kp", "secret"}
        )
        base_entities = [
            item
            for item in self.repo.list_campaign_world_entities(campaign_id)
            if item["visibility"] in allowed_visibility
        ]
        visible_ids = {str(item["id"]) for item in base_entities}
        states_by_entity: dict[str, list[dict]] = {}
        for state in self.repo.list_campaign_world_entity_states(campaign_id):
            if (
                state["entity_id"] in visible_ids
                and state["visibility"] in allowed_visibility
            ):
                states_by_entity.setdefault(str(state["entity_id"]), []).append(state)
        entities = [
            {**item, "states": states_by_entity.get(str(item["id"]), [])}
            for item in base_entities
        ]
        if view == "kp":
            identities = self._scenario_identities(campaign_id)
            entities = [
                {
                    **item,
                    "scenario_identity": identities.get(str(item["origin_ref"]))
                    if item["origin_kind"] == "module_source" else None,
                }
                for item in entities
            ]
        relations = [
            item
            for item in self.repo.list_campaign_world_entity_relations(campaign_id)
            if item["source_entity_id"] in visible_ids
            and item["target_entity_id"] in visible_ids
        ]
        state_changes = [
            self._project_world_entity_state_change(item, view=view)
            for item in self.repo.list_campaign_world_entity_state_changes(campaign_id)
            if item["entity_id"] in visible_ids
            and item["visibility"] in allowed_visibility
        ]
        return {
            "entities": entities,
            "relations": relations,
            "state_changes": state_changes,
        }

    def _scenario_identities(self, campaign_id: str) -> dict[str, dict]:
        run = self.repo.get_active_campaign_module_run(campaign_id)
        if run is None:
            return {}
        try:
            binding = self.repo.get_module_run_contract_binding(str(run["id"]))
        except KeyError:
            return {}
        contract = binding["contract"]
        if not any(item.module_entity_id for item in contract.entities):
            return {}
        if validate_scenario_entity_identities(
            contract, self.repo.list_module_entities(str(run["module_id"]))
        ):
            return {}
        return {
            item.module_entity_id: {
                "run_id": run["id"], "entity_id": item.entity_id,
                "contract_hash": binding["contract_hash"],
            }
            for item in contract.entities if item.module_entity_id is not None
        }

    @staticmethod
    def _project_world_entity_state_change(item: dict, *, view: WorldView) -> dict:
        keys = (
            "id",
            "campaign_id",
            "entity_id",
            "dimension",
            "from_value",
            "to_value",
            "visibility",
            "note",
            "source_kind",
            "state_version",
            "event_id",
            "created_at",
        )
        projected = {key: item[key] for key in keys}
        if view == "player":
            projected.pop("note", None)
            projected.pop("source_kind", None)
        return projected

    def append_event(self, campaign_id: str, command: AppendEventCommand) -> dict:
        return self.repo.append_event(
            campaign_id=campaign_id,
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            visibility=command.visibility,
            event_type=command.event_type,
            happened_at=command.happened_at,
            summary=command.summary,
            payload=command.payload,
        )

    def add_memory(self, campaign_id: str, command: AddMemoryCommand) -> dict:
        return self.repo.add_memory(
            text=command.text,
            scope=command.scope,
            campaign_id=campaign_id,
            pc_id=command.pc_id,
            npc_id=command.npc_id,
            importance=command.importance,
            visibility=command.visibility,
            happened_at=command.happened_at,
            source_event_id=command.source_event_id,
        )

    def list_modules(self, campaign_id: str) -> list[dict]:
        return self.repo.list_modules(campaign_id)

    def import_module(self, campaign_id: str, command: ImportModuleCommand) -> dict:
        chunks = chunk_plaintext_module(
            command.text,
            title=command.title,
            visibility=command.default_visibility,
        )
        return self.repo.create_module(
            campaign_id=campaign_id,
            title=command.title,
            chunks=chunks,
            source_type=command.source_type,
        )

    def get_module(self, module_id: str) -> dict:
        """Expose module ownership so the transport can authorize before reading chunks."""

        return self.repo.get_module(module_id)

    def list_module_chunks(
        self,
        module_id: str,
        *,
        view: WorldView,
        spoiler: str | None = None,
    ) -> list[dict]:
        allowed_visibility = (
            (
                "player",
                "table",
            )
            if view == "player"
            else ("player", "table", "kp", "secret")
        )
        if view == "player":
            spoiler_tags: tuple[str, ...] | None = ()
        elif spoiler:
            spoiler_tags = tuple(tag.strip() for tag in spoiler.split(",") if tag.strip())
        else:
            spoiler_tags = None
        return self.repo.list_module_chunks(
            module_id,
            allowed_visibility=allowed_visibility,
            spoiler_tags=spoiler_tags,
        )

    def search_memory(
        self,
        campaign_id: str,
        query: str,
        *,
        pc_id: str | None = None,
        view: WorldView,
    ) -> list[dict]:
        visibility = (
            (
                "player",
                "table",
            )
            if view == "player"
            else ("player", "table", "kp")
        )
        results = self.repo.retrieve_memories(
            query,
            campaign_id=campaign_id,
            pc_id=pc_id,
            visibility=visibility,
        )
        return [asdict(result) for result in results]

    def create_npc(self, command: CreateNpcCommand) -> dict:
        return self.repo.create_npc(
            name=command.name,
            home_location=command.home_location,
            profession=command.profession,
            public_notes=command.public_notes,
            secret_notes=command.secret_notes,
        )

    def create_campaign_npc(
        self,
        campaign_id: str,
        command: CreateNpcCommand,
    ) -> dict:
        npc = self.create_npc(command)
        self.repo.link_npc_to_campaign(campaign_id, npc["id"])
        return npc

    def link_npc(
        self,
        campaign_id: str,
        npc_id: str,
        command: LinkNpcCommand,
    ) -> dict:
        participant_ids = tuple(dict.fromkeys(command.participant_investigator_ids))
        self.repo.require_approved_contact_investigators(
            campaign_id,
            participant_ids,
        )
        already_linked = self.repo.npc_is_linked_to_campaign(campaign_id, npc_id)
        if not already_linked and not self.repo.npc_is_authorized_reappearance(
            campaign_id,
            npc_id,
            participant_ids,
        ):
            raise PermissionError(
                "NPC linking requires qualifying prior contact by an approved investigator"
            )
        self.repo.link_npc_to_campaign(
            campaign_id=campaign_id,
            npc_id=npc_id,
            role=command.role,
            first_seen_time=command.first_seen_time,
            last_seen_time=command.last_seen_time,
            relationship_score=command.relationship_score,
            notes=command.notes,
        )
        return {"ok": True}

    def npc_candidates(
        self,
        campaign_id: str,
        action_text: str,
        *,
        location: str | None = None,
        profession_hint: str | None = None,
    ) -> list[dict]:
        results = self.repo.find_npc_candidates(
            campaign_id=campaign_id,
            action_text=action_text,
            location=location,
            profession_hint=profession_hint,
        )
        return [asdict(result) for result in results]

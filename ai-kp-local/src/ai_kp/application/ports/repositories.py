"""Narrow persistence ports for application services."""

from typing import Any, Protocol

from ai_kp.platform.memory.npc_candidates import NpcCandidate
from ai_kp.platform.memory.retrieval import RetrievedMemory
from ai_kp.platform.modules.graph import ModuleEntityCreate, ModuleRelationCreate
from ai_kp.platform.modules.ingestion import ModuleChunk
from ai_kp.platform.modules.knowledge import ModuleKnowledgeCandidate
from ai_kp.platform.scenes.map_generation import GeneratedMap


class CampaignStore(Protocol):
    def create_campaign(
        self,
        title: str,
        system: str = "coc7",
        current_time: str | None = None,
    ) -> dict: ...

    def get_campaign(self, campaign_id: str) -> dict: ...

    def list_campaigns(self) -> list[dict]: ...


class RealtimeOutbox(Protocol):
    def append_realtime_event(
        self,
        *,
        session_id: str,
        campaign_id: str,
        event_type: str,
        audience: str = "session",
        member_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class MapStore(CampaignStore, RealtimeOutbox, Protocol):
    def begin_immediate(self) -> None: ...

    def create_map(
        self,
        campaign_id: str,
        generated_map: GeneratedMap,
        created_by: str = "ai",
    ) -> dict: ...

    def set_map_status(self, map_id: str, status: str) -> dict: ...

    def get_map_publish_snapshot(self, map_id: str) -> dict[str, Any]: ...

    def is_map_token_player_visible(self, token_id: str) -> bool: ...

    def place_map_token(
        self,
        map_id: str,
        label: str,
        location_name: str,
        actor_type: str = "pc",
        actor_id: str | None = None,
        visibility: str = "table",
        color: str = "#b93f2d",
    ) -> dict: ...

    def move_map_token(
        self,
        token_id: str,
        to_location_name: str,
        moved_by: str = "player",
        note: str = "",
        require_route: bool = True,
        allowed_visibility: tuple[str, ...] | None = None,
        expected_version: int | None = None,
    ) -> dict: ...


class MapImageStore(Protocol):
    def begin_immediate(self) -> None: ...

    def is_session_member_active(self, member_id: str, session_id: str) -> bool: ...

    def get_current_map_revision(self, map_id: str) -> dict[str, Any] | None: ...

    def find_map_asset_by_generation_hash(
        self,
        map_id: str,
        generation_input_hash: str,
    ) -> dict[str, Any] | None: ...

    def create_map_asset(self, **values: Any) -> dict[str, Any]: ...

    def repair_map_asset(self, asset_id: str, **values: Any) -> dict[str, Any]: ...


class ModuleKnowledgeStore(Protocol):
    def begin_immediate(self) -> None: ...

    def rollback(self) -> None: ...

    def get_module_chunk(self, chunk_id: str) -> dict: ...

    def get_module_asset(self, asset_id: str) -> dict: ...

    def list_module_chunks_for_knowledge(
        self,
        module_id: str,
        *,
        status: str = "pending",
        limit: int = 5,
    ) -> list[dict]: ...

    def claim_module_chunk_for_knowledge(self, chunk_id: str) -> int | None: ...

    def module_chunk_knowledge_claim_is_current(
        self,
        chunk_id: str,
        *,
        expected_attempt: int,
    ) -> bool: ...

    def mark_module_chunk_knowledge_status(
        self,
        chunk_id: str,
        status: str,
        *,
        expected_attempt: int,
    ) -> bool: ...

    def reset_failed_module_chunks(self, module_id: str) -> int: ...

    def store_module_knowledge_candidate(
        self,
        module_id: str,
        candidate: ModuleKnowledgeCandidate,
        *,
        object_hash: str,
        created_by: str,
        source_model: str | None,
        prompt_version: str | None,
    ) -> dict: ...

    def get_module_knowledge_candidate(self, candidate_id: str) -> dict: ...

    def review_module_knowledge_candidate(
        self,
        candidate_id: str,
        *,
        decision: str,
        member_id: str | None,
        note: str | None,
    ) -> dict: ...

    def commit(self) -> None: ...


class ModuleGraphStore(Protocol):
    def get_module_knowledge_candidate(self, candidate_id: str) -> dict: ...

    def module_candidate_sources_are_current(
        self,
        candidate_id: str,
        *,
        module_id: str,
    ) -> bool: ...

    def create_module_entity(
        self,
        module_id: str,
        entity: ModuleEntityCreate,
        *,
        member_id: str,
    ) -> dict: ...

    def get_module_entity(self, entity_id: str) -> dict: ...

    def create_module_relation(
        self,
        module_id: str,
        relation: ModuleRelationCreate,
        *,
        member_id: str,
    ) -> dict: ...

    def module_graph_reachability(
        self,
        module_id: str,
        entry_entity_ids: tuple[str, ...],
    ) -> dict: ...


class ModuleRunStore(Protocol):
    def get_campaign_module_run(self, run_id: str) -> dict: ...

    def start_campaign_module_run(
        self,
        *,
        campaign_id: str,
        module_id: str,
        current_scene_key: str | None,
        active_spoiler_tags: list[str],
        state: dict[str, Any],
        started_by_member_id: str | None,
    ) -> dict: ...

    def update_campaign_module_run(
        self,
        run_id: str,
        changes: dict[str, Any],
    ) -> dict: ...

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
    ) -> dict: ...

    def list_module_run_scene_events(self, run_id: str) -> list[dict]: ...

    def set_module_run_entity_state(
        self,
        run_id: str,
        entity_id: str,
        *,
        expected_version: int,
        status: str,
        note: str,
        member_id: str | None,
    ) -> dict: ...

    def list_module_run_entity_states(self, run_id: str) -> list[dict]: ...

    def list_module_run_entity_state_events(self, run_id: str) -> list[dict]: ...

    def search_module(
        self,
        module_id: str,
        query: str,
        *,
        allowed_visibility: tuple[str, ...],
        spoiler_tags: tuple[str, ...] | None,
        limit: int = 12,
    ) -> list[dict]: ...

    def module_graph_reachability(
        self,
        module_id: str,
        entry_entity_ids: tuple[str, ...],
    ) -> dict: ...


class CheckStore(CampaignStore, Protocol):
    def create_skill_check(
        self,
        *,
        campaign_id: str,
        session_id: str,
        requested_by_member_id: str,
        skill_name: str,
        difficulty: str,
        ruleset_id: str,
        ruleset_version: str,
        source_reference: dict[str, Any],
        bonus_dice: int = 0,
        hidden: bool = False,
        allow_push: bool = True,
        roller_member_id: str | None = None,
        pc_id: str | None = None,
        target: int | None = None,
        proposal_id: str | None = None,
        player_action_id: str | None = None,
        pushed_from_check_id: str | None = None,
    ) -> dict[str, Any]: ...

    def get_skill_check(self, check_id: str) -> dict[str, Any]: ...

    def list_skill_checks(
        self,
        campaign_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]: ...

    def list_skill_checks_for_action(
        self,
        player_action_id: str,
    ) -> list[dict[str, Any]]: ...

    def resolve_skill_check(
        self,
        check_id: str,
        *,
        actor_member_id: str,
        input_method: str,
        resolution: dict[str, Any],
    ) -> dict[str, Any]: ...

    def override_skill_check(
        self,
        check_id: str,
        *,
        actor_member_id: str,
        success_level: str,
        passed: bool,
        reason: str,
    ) -> dict[str, Any]: ...

    def cancel_skill_check(
        self,
        check_id: str,
        *,
        actor_member_id: str,
        reason: str,
    ) -> dict[str, Any]: ...

    def push_skill_check(
        self,
        check_id: str,
        *,
        actor_member_id: str,
        reason: str,
    ) -> dict[str, Any]: ...


class InvestigatorStore(Protocol):
    def create_player_profile(self, display_name: str) -> dict: ...

    def create_investigator(
        self,
        owner_profile_id: str,
        canonical_sheet: dict[str, Any],
        *,
        source_type: str,
        source_hash: str | None = None,
        source_filename: str | None = None,
        template_id: str | None = None,
        parser_version: str | None = None,
        warnings: list[str] | None = None,
    ) -> dict: ...

    def list_investigators(self, owner_profile_id: str) -> list[dict]: ...

    def get_investigator(
        self,
        investigator_id: str,
        owner_profile_id: str | None = None,
    ) -> dict: ...

    def add_investigator_revision(
        self,
        investigator_id: str,
        owner_profile_id: str,
        canonical_sheet: dict[str, Any],
        *,
        source_type: str,
        source_hash: str | None = None,
        source_filename: str | None = None,
        template_id: str | None = None,
        parser_version: str | None = None,
        warnings: list[str] | None = None,
    ) -> dict: ...

    def list_investigator_revisions(
        self,
        investigator_id: str,
        owner_profile_id: str | None = None,
    ) -> list[dict]: ...

    def submit_investigator_to_campaign(
        self,
        *,
        campaign_id: str,
        investigator_id: str,
        revision_id: str,
        owner_profile_id: str,
        member_id: str,
        session_id: str,
    ) -> dict: ...

    def list_campaign_investigators(
        self,
        campaign_id: str,
        owner_profile_id: str | None = None,
    ) -> list[dict]: ...

    def review_campaign_investigator(
        self,
        *,
        campaign_id: str,
        investigator_id: str,
        action: str,
        comment: str | None,
        kp_member_id: str,
        session_id: str,
    ) -> dict: ...

    def assign_approved_investigator(
        self,
        *,
        session_id: str,
        member_id: str,
        investigator_id: str,
    ) -> dict: ...

    def list_public_campaign_investigators(self, campaign_id: str) -> list[dict]: ...

    def update_investigator_campaign_state(
        self,
        *,
        campaign_id: str,
        investigator_id: str,
        expected_version: int,
        changes: dict[str, Any],
    ) -> dict: ...


class SessionStore(Protocol):
    def create_campaign_session(
        self,
        campaign_id: str,
        *,
        title: str | None = None,
        kp_display_name: str = "KP",
    ) -> dict: ...

    def join_campaign_session(
        self,
        join_code: str,
        *,
        display_name: str,
    ) -> dict: ...

    def reissue_campaign_kp_access_token(
        self,
        campaign_id: str,
        *,
        display_name: str,
    ) -> dict: ...

    def seat_for_member(self, member_id: str) -> dict | None: ...

    def revoke_session_seat(self, session_id: str, seat_id: str) -> dict: ...

    def get_session_member(self, member_id: str) -> dict: ...

    def revoke_session_member(self, session_id: str, member_id: str) -> dict: ...

    def rotate_session_join_code(self, session_id: str) -> dict: ...

    def close_campaign_session(self, session_id: str) -> dict: ...

    def create_session_seat(
        self,
        session_id: str,
        *,
        label: str,
        created_by_member_id: str,
    ) -> dict: ...

    def create_player_profile(self, display_name: str) -> dict: ...

    def get_player_profile(self, profile_id: str) -> dict: ...

    def claim_session_seat(
        self,
        invitation_code: str,
        *,
        player_profile_id: str,
        display_name: str,
    ) -> dict: ...

    def recover_session_seat(self, seat_id: str, player_profile_id: str) -> dict: ...

    def list_player_session_seats(self, player_profile_id: str) -> list[dict]: ...

    def reissue_seat_invitation(self, session_id: str, seat_id: str) -> dict: ...

class WorldStore(RealtimeOutbox, Protocol):
    def require_approved_contact_investigators(
        self,
        campaign_id: str,
        investigator_ids: tuple[str, ...],
    ) -> list[dict]: ...

    def npc_is_linked_to_campaign(self, campaign_id: str, npc_id: str) -> bool: ...

    def npc_is_authorized_reappearance(
        self,
        campaign_id: str,
        npc_id: str,
        investigator_ids: tuple[str, ...],
    ) -> bool: ...

    def create_pc(self, campaign_id: str, name: str, sheet: dict | None = None) -> dict: ...

    def list_pcs(self, campaign_id: str) -> list[dict]: ...

    def append_event(
        self,
        campaign_id: str,
        actor_type: str,
        event_type: str,
        summary: str,
        *,
        actor_id: str | None = None,
        visibility: str = "table",
        happened_at: str | None = None,
        payload: dict | None = None,
    ) -> dict: ...

    def add_memory(
        self,
        text: str,
        scope: str,
        *,
        campaign_id: str | None = None,
        pc_id: str | None = None,
        npc_id: str | None = None,
        importance: int = 1,
        visibility: str = "table",
        happened_at: str | None = None,
        source_event_id: str | None = None,
    ) -> dict: ...

    def list_modules(self, campaign_id: str) -> list[dict]: ...

    def create_module(
        self,
        campaign_id: str,
        title: str,
        chunks: list[ModuleChunk],
        *,
        source_type: str = "plaintext",
    ) -> dict: ...

    def get_module(self, module_id: str) -> dict: ...

    def list_module_chunks(
        self,
        module_id: str,
        *,
        allowed_visibility: tuple[str, ...],
        spoiler_tags: tuple[str, ...] | None,
    ) -> list[dict]: ...

    def retrieve_memories(
        self,
        query: str,
        *,
        campaign_id: str,
        pc_id: str | None,
        visibility: tuple[str, ...],
    ) -> list[RetrievedMemory]: ...

    def create_npc(
        self,
        name: str,
        home_location: str | None = None,
        profession: str | None = None,
        public_notes: str = "",
        secret_notes: str = "",
    ) -> dict: ...

    def link_npc_to_campaign(
        self,
        campaign_id: str,
        npc_id: str,
        *,
        role: str = "encountered",
        first_seen_time: str | None = None,
        last_seen_time: str | None = None,
        relationship_score: int = 0,
        notes: str = "",
    ) -> None: ...

    def find_npc_candidates(
        self,
        *,
        campaign_id: str,
        action_text: str,
        location: str | None = None,
        profession_hint: str | None = None,
    ) -> list[NpcCandidate]: ...


class RulebookStore(Protocol):
    def begin_immediate(self) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def create_rule_source(
        self,
        *,
        ruleset_id: str,
        title: str,
        source_filename: str,
        source_hash: str,
        page_count: int,
        metadata: dict[str, Any],
    ) -> dict: ...

    def get_rule_source(self, source_id: str) -> dict: ...

    def find_rule_source(self, ruleset_id: str) -> dict: ...

    def set_rule_source_status(self, source_id: str, status: str) -> None: ...

    def create_ingestion_run(
        self,
        source_id: str,
        *,
        stage: str,
        agent_model: str | None = None,
        prompt_version: str | None = None,
    ) -> dict: ...

    def update_ingestion_run(
        self,
        run_id: str,
        *,
        expected_status: str | None = None,
        **changes: Any,
    ) -> dict: ...

    def transition_ingestion_run(
        self,
        run_id: str,
        *,
        expected_status: str,
        **changes: Any,
    ) -> dict | None: ...

    def ingestion_run_is_current(
        self,
        run_id: str,
        *,
        expected_status: str,
    ) -> bool: ...

    def replace_rule_chunks(
        self,
        source_id: str,
        chunks: list[dict[str, Any]],
    ) -> None: ...

    def list_rule_chunks(
        self,
        source_id: str,
        *,
        extraction_status: str | None = None,
        limit: int | None = None,
    ) -> list[dict]: ...

    def get_rule_chunk(self, chunk_id: str) -> dict: ...

    def claim_rule_chunk_for_extraction(
        self,
        chunk_id: str,
        *,
        run_id: str | None = None,
    ) -> int | None: ...

    def rule_chunk_extraction_claim_is_current(
        self,
        chunk_id: str,
        *,
        expected_attempt: int,
    ) -> bool: ...

    def mark_rule_chunk(
        self,
        chunk_id: str,
        *,
        extraction_status: str | None = None,
        minirag_doc_id: str | None = None,
        expected_attempt: int | None = None,
    ) -> bool: ...

    def reset_failed_rule_chunks(self, source_id: str) -> int: ...

    def store_rule_object(
        self,
        source_id: str,
        payload: dict[str, Any],
        *,
        status: str,
        validation: dict[str, Any],
    ) -> dict: ...

    def get_rule_object(self, object_id: str) -> dict: ...

    def update_rule_object_review(
        self,
        object_id: str,
        *,
        status: str,
        validation: dict[str, Any],
        expected_status: str,
        expected_object_hash: str,
    ) -> dict: ...

    def record_rule_validation_issue(
        self,
        source_id: str,
        *,
        validation_layer: str,
        error_text: str,
        chunk_id: str | None = None,
        run_id: str | None = None,
        candidate: Any = None,
    ) -> dict: ...

    def latest_validated_rule(self, source_id: str, rule_key: str) -> dict: ...

    def search_validated_rules(
        self,
        source_id: str,
        query: str,
        *,
        audience: str,
        limit: int = 8,
    ) -> list[dict]: ...

    def lexical_rule_chunks(
        self,
        source_id: str,
        query: str,
        limit: int = 8,
    ) -> list[dict]: ...

    def list_rule_objects(
        self,
        source_id: str,
        *,
        status: str | None = None,
        rule_key: str | None = None,
    ) -> list[dict]: ...


class TurnStore(CheckStore, ModuleRunStore, RealtimeOutbox, Protocol):
    def begin_immediate(self) -> None: ...

    def create_player_action(
        self,
        identity: Any,
        *,
        action_text: str,
        map_id: str | None = None,
        token_id: str | None = None,
        client_action_id: str | None = None,
    ) -> dict: ...

    def get_player_action(self, action_id: str) -> dict: ...

    def create_turn_proposal(self, **values: Any) -> dict: ...

    def link_player_action_to_proposal(
        self,
        action_id: str,
        proposal_id: str,
        campaign_id: str,
    ) -> dict: ...

    def create_context_assembly(self, **values: Any) -> dict: ...

    def is_session_member_active(self, member_id: str, session_id: str) -> bool: ...

    def get_turn_proposal(self, proposal_id: str) -> dict: ...

    def list_fact_heads(self, campaign_id: str) -> list[Any]: ...

    def attach_check_consequence_basis(
        self,
        proposal_id: str,
        *,
        origin_proposal_id: str,
        player_action_id: str,
        check_ids: list[str],
        result_fingerprint: str,
    ) -> dict: ...

    def find_live_check_consequence(
        self,
        campaign_id: str,
        *,
        player_action_id: str,
        result_fingerprint: str,
    ) -> dict | None: ...

    def attach_world_expansion_basis(
        self,
        proposal_id: str,
        *,
        module_run_id: str,
        module_run_version: int,
        fingerprint: str,
        analysis: dict,
        candidate: dict,
    ) -> dict: ...

    def get_world_expansion_basis(self, proposal_id: str) -> dict | None: ...

    def find_live_world_expansion(
        self,
        campaign_id: str,
        *,
        fingerprint: str,
    ) -> dict | None: ...

    def approve_turn_proposal(
        self,
        proposal_id: str,
        *,
        actor: str,
        note: str = "",
        override_public_narration: str | None = None,
    ) -> dict: ...

    def player_action_id_for_proposal(self, proposal_id: str) -> str | None: ...

    def reject_turn_proposal(
        self,
        proposal_id: str,
        *,
        actor: str,
        note: str = "",
    ) -> dict: ...

    def require_submitted_player_action(
        self,
        action_id: str,
        campaign_id: str,
        session_id: str,
    ) -> dict: ...


__all__ = [
    "CampaignStore",
    "CheckStore",
    "InvestigatorStore",
    "MapStore",
    "RealtimeOutbox",
    "RulebookStore",
    "SessionStore",
    "TurnStore",
    "WorldStore",
]

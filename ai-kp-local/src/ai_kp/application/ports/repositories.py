"""Narrow persistence ports for application services."""

from typing import Any, Protocol

from ai_kp.platform.memory.npc_candidates import NpcCandidate
from ai_kp.platform.memory.retrieval import RetrievedMemory
from ai_kp.platform.modules.ingestion import ModuleChunk
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


class MapStore(RealtimeOutbox, Protocol):
    def create_map(
        self,
        campaign_id: str,
        generated_map: GeneratedMap,
        created_by: str = "ai",
    ) -> dict: ...

    def set_map_status(self, map_id: str, status: str) -> dict: ...

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
        pc_id: str | None = None,
    ) -> dict: ...

    def seat_for_member(self, member_id: str) -> dict | None: ...

    def revoke_session_seat(self, session_id: str, seat_id: str) -> dict: ...

    def get_session_member(self, member_id: str) -> dict: ...

    def revoke_session_member(self, session_id: str, member_id: str) -> dict: ...

    def rotate_session_join_code(self, session_id: str) -> dict: ...

    def assign_member_pc(self, session_id: str, member_id: str, pc_id: str) -> dict: ...

    def sync_member_seat_pc(self, member_id: str, pc_id: str | None) -> None: ...

    def close_campaign_session(self, session_id: str) -> dict: ...

    def create_session_seat(
        self,
        session_id: str,
        *,
        label: str,
        created_by_member_id: str,
        pc_id: str | None = None,
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

    def assign_session_seat_pc(
        self,
        session_id: str,
        seat_id: str,
        pc_id: str | None,
    ) -> dict: ...


class WorldStore(RealtimeOutbox, Protocol):
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


__all__ = [
    "CampaignStore",
    "CheckStore",
    "InvestigatorStore",
    "MapStore",
    "RealtimeOutbox",
    "SessionStore",
    "WorldStore",
]

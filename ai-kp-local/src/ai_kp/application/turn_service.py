from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ai_kp.application.errors import KpSessionEndedError
from ai_kp.application.play.proposal_approval import plan_proposed_checks
from ai_kp.application.ports.director import KpDirector
from ai_kp.application.ports.repositories import TurnStore
from ai_kp.platform.resolution.proposals import validate_unresolved_check_boundary
from ai_kp.rulesets import get_ruleset
from ai_kp.platform.sessions.models import AuthenticatedMember


@dataclass(frozen=True)
class ManualProposalCommand:
    player_action: str
    public_narration: str
    player_action_id: str | None = None
    pc_id: str | None = None
    kp_notes: str = ""
    proposed_checks: Sequence[Any] = ()
    proposed_events: Sequence[Any] = ()
    proposed_memories: Sequence[Any] = ()
    proposed_npc_updates: Sequence[Any] = ()
    proposed_map_moves: Sequence[Any] = ()
    source_model: str = "manual-dev"


@dataclass(frozen=True)
class KpTurnCommand:
    campaign_id: str
    player_action: str
    player_action_id: str | None = None
    pc_id: str | None = None
    location: str | None = None
    map_id: str | None = None
    profession_hint: str | None = None
    active_spoiler_tags: tuple[str, ...] = ()


class TurnService:
    """Coordinate player actions, KP drafts, context audit, and approval outbox."""

    def __init__(self, repo: TurnStore):
        self.repo = repo

    def submit_player_action(
        self,
        identity: AuthenticatedMember,
        *,
        action_text: str,
        map_id: str | None = None,
        token_id: str | None = None,
        client_action_id: str | None = None,
    ) -> dict:
        return self.repo.create_player_action(
            identity,
            action_text=action_text,
            map_id=map_id,
            token_id=token_id,
            client_action_id=client_action_id,
        )

    def create_manual_proposal(
        self,
        campaign_id: str,
        identity: AuthenticatedMember,
        command: ManualProposalCommand,
    ) -> dict:
        validate_unresolved_check_boundary(
            command.proposed_checks,
            command.proposed_events,
            command.proposed_memories,
            command.proposed_npc_updates,
            command.proposed_map_moves,
        )
        queued_action = self._queued_action(
            command.player_action_id,
            campaign_id,
            identity.session_id,
        )
        proposal = self.repo.create_turn_proposal(
            campaign_id=campaign_id,
            pc_id=queued_action["pc_id"] if queued_action else command.pc_id,
            player_action=(
                queued_action["action_text"] if queued_action else command.player_action
            ),
            public_narration=command.public_narration,
            kp_notes=command.kp_notes,
            proposed_checks=list(command.proposed_checks),
            proposed_events=list(command.proposed_events),
            proposed_memories=list(command.proposed_memories),
            proposed_npc_updates=list(command.proposed_npc_updates),
            proposed_map_moves=list(command.proposed_map_moves),
            source_model=command.source_model,
        )
        if queued_action:
            self.repo.link_player_action_to_proposal(
                queued_action["id"],
                proposal["id"],
                campaign_id,
            )
        self._append_proposal_created(identity.session_id, campaign_id, proposal)
        return proposal

    async def create_ai_proposal(
        self,
        command: KpTurnCommand,
        identity: AuthenticatedMember,
        director: KpDirector,
        *,
        source_model: str,
    ) -> dict:
        queued_action = self._queued_action(
            command.player_action_id,
            command.campaign_id,
            identity.session_id,
        )
        player_action = queued_action["action_text"] if queued_action else command.player_action
        pc_id = queued_action["pc_id"] if queued_action else command.pc_id
        location = queued_action["location"] if queued_action else command.location
        map_id = queued_action["map_id"] if queued_action else command.map_id

        result = await director.handle_player_action(
            campaign_id=command.campaign_id,
            player_action=player_action,
            pc_id=pc_id,
            location=location,
            map_id=map_id,
            profession_hint=command.profession_hint,
            active_spoiler_tags=command.active_spoiler_tags,
        )

        # The model call intentionally runs outside the write transaction. Claim
        # the database only for the short revalidation-and-persist phase.
        self.repo.begin_immediate()
        if not self.repo.is_session_member_active(identity.member_id, identity.session_id):
            raise KpSessionEndedError("KP session ended while the model was running")

        output = result.output
        proposal = self.repo.create_turn_proposal(
            campaign_id=command.campaign_id,
            pc_id=pc_id,
            player_action=player_action,
            public_narration=output.public_narration,
            kp_notes=output.kp_notes,
            proposed_checks=output.proposed_checks,
            proposed_events=output.proposed_events,
            proposed_memories=output.proposed_memories,
            proposed_npc_updates=output.proposed_npc_updates,
            proposed_map_moves=output.proposed_map_moves,
            source_model=source_model,
        )
        self.repo.create_context_assembly(
            proposal_id=proposal["id"],
            campaign_id=command.campaign_id,
            visibility_scope=result.context.visibility_scope,
            final_prompt=result.context.messages,
            included_sources=result.context.included_sources,
            excluded_sources=result.context.excluded_sources,
            token_estimate=result.context.token_estimate,
        )
        if queued_action:
            self.repo.link_player_action_to_proposal(
                queued_action["id"],
                proposal["id"],
                command.campaign_id,
            )
        self._append_proposal_created(identity.session_id, command.campaign_id, proposal)
        return proposal

    def approve(
        self,
        proposal_id: str,
        campaign_id: str,
        identity: AuthenticatedMember,
        *,
        note: str = "",
        override_public_narration: str | None = None,
    ) -> dict:
        pending = self.repo.get_turn_proposal(proposal_id)
        validate_unresolved_check_boundary(
            pending["proposed_checks"],
            pending["proposed_events"],
            pending["proposed_memories"],
            pending["proposed_npc_updates"],
            pending["proposed_map_moves"],
        )
        campaign = self.repo.get_campaign(campaign_id)
        ruleset = get_ruleset(str(campaign["system"]))
        planned_checks = plan_proposed_checks(pending, ruleset)
        proposal = self.repo.approve_turn_proposal(
            proposal_id,
            actor=f"kp:{identity.member_id}",
            note=note,
            override_public_narration=override_public_narration,
        )
        player_action_id = self.repo.player_action_id_for_proposal(proposal_id)
        for planned_check in planned_checks:
            self.repo.create_skill_check(
                campaign_id=campaign_id,
                session_id=identity.session_id,
                requested_by_member_id=identity.member_id,
                skill_name=planned_check.skill_name,
                difficulty=planned_check.difficulty,
                ruleset_id=ruleset.manifest.ruleset_id,
                ruleset_version=ruleset.manifest.version,
                source_reference=dict(ruleset.manifest.source_reference),
                hidden=planned_check.hidden,
                pc_id=planned_check.pc_id,
                proposal_id=proposal_id,
                player_action_id=player_action_id,
            )
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=campaign_id,
            audience="kp",
            event_type="proposal.approved",
            resource_type="turn_proposal",
            resource_id=proposal_id,
            payload={"status": "approved"},
        )
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=campaign_id,
            audience="session",
            event_type="world.updated",
            resource_type="campaign",
            resource_id=campaign_id,
        )
        return proposal

    def reject(
        self,
        proposal_id: str,
        campaign_id: str,
        identity: AuthenticatedMember,
        *,
        note: str = "",
    ) -> dict:
        proposal = self.repo.reject_turn_proposal(
            proposal_id,
            actor=f"kp:{identity.member_id}",
            note=note,
        )
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=campaign_id,
            audience="kp",
            event_type="proposal.rejected",
            resource_type="turn_proposal",
            resource_id=proposal_id,
            payload={"status": "rejected"},
        )
        return proposal

    def _queued_action(
        self,
        action_id: str | None,
        campaign_id: str,
        session_id: str,
    ) -> dict | None:
        if action_id is None:
            return None
        return self.repo.require_submitted_player_action(
            action_id,
            campaign_id,
            session_id,
        )

    def _append_proposal_created(
        self,
        session_id: str,
        campaign_id: str,
        proposal: dict,
    ) -> None:
        self.repo.append_realtime_event(
            session_id=session_id,
            campaign_id=campaign_id,
            audience="kp",
            event_type="proposal.created",
            resource_type="turn_proposal",
            resource_id=proposal["id"],
            payload={"status": proposal["status"]},
        )

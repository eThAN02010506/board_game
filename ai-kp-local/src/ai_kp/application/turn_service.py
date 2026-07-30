import hashlib
import json
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ai_kp.application.errors import ConflictError, InvalidInputError, KpSessionEndedError
from ai_kp.application.module_run_service import ModuleRunService
from ai_kp.application.play.proposal_approval import plan_proposed_checks
from ai_kp.application.ports.director import KpDirector, WorldExpansionDirector
from ai_kp.application.ports.repositories import TurnStore
from ai_kp.platform.resolution.proposals import validate_unresolved_check_boundary
from ai_kp.platform.sessions.models import AuthenticatedMember
from ai_kp.rulesets import get_ruleset


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


@dataclass(frozen=True)
class WorldExpansionCommand:
    run_id: str
    player_intent: str
    pc_id: str | None = None
    map_id: str | None = None


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

    async def create_world_expansion_proposal(
        self,
        command: WorldExpansionCommand,
        identity: AuthenticatedMember,
        director: WorldExpansionDirector,
        *,
        source_model: str,
    ) -> dict:
        intent = unicodedata.normalize("NFKC", command.player_intent).strip()
        if not intent:
            raise InvalidInputError("Player intent is required")
        analysis = ModuleRunService(self.repo).analyze(command.run_id, intent)
        if analysis["decision"] != "world_gap":
            raise InvalidInputError(
                "World expansion requires a confirmed world_gap analysis; "
                f"current decision is {analysis['decision']}"
            )
        if analysis["reachability"].get("has_conflicts"):
            raise InvalidInputError(
                "World expansion is blocked by explicit module graph conflicts"
            )
        run = self.repo.get_campaign_module_run(command.run_id)
        world_fact_head_hash = self._world_fact_head_hash(
            self.repo.list_fact_heads(str(run["campaign_id"]))
        )
        fingerprint = self._world_expansion_fingerprint(
            run,
            intent,
            world_fact_head_hash,
        )
        existing = self.repo.find_live_world_expansion(
            str(run["campaign_id"]),
            fingerprint=fingerprint,
        )
        if existing is not None:
            return existing
        location = next(
            (
                str(item["name"])
                for item in analysis["entity_states"]
                if item["entity_id"] == run.get("current_location_entity_id")
            ),
            None,
        )
        analysis_snapshot = self._world_expansion_analysis_snapshot(
            run,
            analysis,
            fingerprint,
            world_fact_head_hash,
        )
        result = await director.handle_world_expansion(
            campaign_id=str(run["campaign_id"]),
            player_intent=intent,
            analysis_snapshot=analysis_snapshot,
            pc_id=command.pc_id,
            location=location,
            map_id=command.map_id,
            active_spoiler_tags=tuple(run["active_spoiler_tags"]),
        )

        # The LLM call stays outside the write transaction. Revalidate the
        # session, run version and gap decision before persisting its draft.
        self.repo.begin_immediate()
        if not self.repo.is_session_member_active(identity.member_id, identity.session_id):
            raise KpSessionEndedError("KP session ended while the model was running")
        refreshed = self.repo.get_campaign_module_run(command.run_id)
        refreshed_fact_hash = self._world_fact_head_hash(
            self.repo.list_fact_heads(str(run["campaign_id"]))
        )
        if (
            refreshed["status"] != "active"
            or refreshed["campaign_id"] != run["campaign_id"]
            or refreshed["module_id"] != run["module_id"]
            or refreshed["version"] != run["version"]
            or refreshed_fact_hash != world_fact_head_hash
        ):
            raise ConflictError(
                "Module run changed while world expansion was generated; analyze again"
            )
        refreshed_analysis = ModuleRunService(self.repo).analyze(command.run_id, intent)
        if refreshed_analysis["decision"] != "world_gap":
            raise ConflictError(
                "World gap is no longer current; analyze again before creating a proposal"
            )
        existing = self.repo.find_live_world_expansion(
            str(run["campaign_id"]),
            fingerprint=fingerprint,
        )
        if existing is not None:
            return existing

        output = result.output
        proposal = self.repo.create_turn_proposal(
            campaign_id=run["campaign_id"],
            pc_id=command.pc_id,
            player_action=intent,
            public_narration=output.public_narration,
            kp_notes=output.kp_notes,
            proposed_checks=[],
            proposed_events=[],
            proposed_memories=[],
            proposed_npc_updates=[],
            proposed_map_moves=[],
            source_model=source_model,
        )
        self.repo.attach_world_expansion_basis(
            proposal["id"],
            module_run_id=command.run_id,
            module_run_version=int(run["version"]),
            fingerprint=fingerprint,
            analysis=analysis_snapshot,
            candidate=output.candidate.model_dump(mode="json"),
        )
        self.repo.create_context_assembly(
            proposal_id=proposal["id"],
            campaign_id=run["campaign_id"],
            visibility_scope=result.context.visibility_scope,
            final_prompt=result.context.messages,
            included_sources=result.context.included_sources,
            excluded_sources=result.context.excluded_sources,
            token_estimate=result.context.token_estimate,
        )
        proposal = self.repo.get_turn_proposal(proposal["id"])
        self._append_proposal_created(
            identity.session_id,
            str(run["campaign_id"]),
            proposal,
        )
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
        # Own the request transaction before the repository opens its savepoint.
        # This keeps proposal effects, concrete checks, and outbox messages in one
        # unit that the request dependency can commit or roll back together.
        self.repo.begin_immediate()
        pending = self.repo.get_turn_proposal(proposal_id)
        self._validate_world_expansion_approval(pending, campaign_id)
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
        # reject_turn_proposal uses a savepoint for its local state transition;
        # start the request transaction first so its outbox write is atomic too.
        self.repo.begin_immediate()
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
            payload={
                "status": proposal["status"],
                "proposal_kind": proposal["proposal_kind"],
            },
        )

    def _validate_world_expansion_approval(
        self,
        proposal: dict,
        campaign_id: str,
    ) -> None:
        basis = proposal.get("world_expansion")
        if basis is None:
            return
        run = self.repo.get_campaign_module_run(str(basis["module_run_id"]))
        if (
            run["campaign_id"] != campaign_id
            or run["module_id"] != basis["module_id"]
            or run["module_source_hash"] != basis["module_source_hash"]
            or run["status"] != "active"
            or run["version"] != basis["module_run_version"]
            or self._world_fact_head_hash(
                self.repo.list_fact_heads(campaign_id)
            )
            != basis["analysis"]["world_fact_head_hash"]
        ):
            raise ConflictError(
                "World expansion proposal is stale because its module run changed; "
                "reject it and analyze again"
            )

    @staticmethod
    def _world_expansion_fingerprint(
        run: dict,
        intent: str,
        world_fact_head_hash: str,
    ) -> str:
        basis = json.dumps(
            {
                "run_id": run["id"],
                "run_version": run["version"],
                "intent": intent,
                "world_fact_head_hash": world_fact_head_hash,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

    @staticmethod
    def _world_expansion_analysis_snapshot(
        run: dict,
        analysis: dict,
        fingerprint: str,
        world_fact_head_hash: str,
    ) -> dict:
        return {
            "fingerprint": fingerprint,
            "decision": analysis["decision"],
            "reasons": list(analysis["reasons"]),
            "scene": dict(analysis["scene"]),
            "module_id": run["module_id"],
            "module_title": run["module_title"],
            "module_source_hash": run["module_source_hash"],
            "module_run_id": run["id"],
            "module_run_version": run["version"],
            "active_spoiler_tags": list(run["active_spoiler_tags"]),
            "unreachable_anchor_count": analysis["unreachable_anchor_count"],
            "deferred_source_count": analysis["deferred_source_count"],
            "world_fact_head_hash": world_fact_head_hash,
            "writes_performed": analysis["writes_performed"],
        }

    @staticmethod
    def _world_fact_head_hash(heads: Sequence[Any]) -> str:
        payload = [
            item.as_dict() if hasattr(item, "as_dict") else dict(item)
            for item in heads
        ]
        encoded = json.dumps(
            sorted(payload, key=lambda item: str(item.get("fact_key", ""))),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

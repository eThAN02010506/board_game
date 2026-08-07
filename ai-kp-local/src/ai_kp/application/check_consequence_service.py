"""Generate an AI consequence draft from a stable terminal check batch."""

from dataclasses import dataclass

from ai_kp.application.ai_control_service import AiControlService
from ai_kp.application.errors import KpSessionEndedError
from ai_kp.application.ports.director import CheckConsequenceDirector
from ai_kp.application.ports.repositories import TurnStore
from ai_kp.platform.resolution import (
    HIDDEN_CHECK_PUBLIC_NARRATION,
    build_check_consequence_snapshot,
)
from ai_kp.platform.sessions.models import AuthenticatedMember


@dataclass(frozen=True)
class GenerateCheckConsequenceCommand:
    check_id: str


class CheckConsequenceService:
    """Keep the model call outside writes, then revalidate before persisting."""

    def __init__(self, repo: TurnStore):
        self.repo = repo

    async def generate(
        self,
        command: GenerateCheckConsequenceCommand,
        identity: AuthenticatedMember,
        director: CheckConsequenceDirector,
        *,
        source_model: str,
    ) -> dict:
        check = self.repo.get_skill_check(command.check_id)
        self._require_kp(identity, check)
        action_id = check.get("player_action_id")
        if not action_id:
            raise ValueError(
                "Only checks linked to a queued player action have consequence drafts"
            )
        action = self.repo.get_player_action(str(action_id))
        checks = self.repo.list_skill_checks_for_action(str(action_id))
        self._require_linked_scope(identity, check, action, checks)
        opposed_checks = self.repo.list_opposed_checks_for_action(str(action_id))
        snapshot = build_check_consequence_snapshot(checks, opposed_checks)
        existing = self.repo.find_live_check_consequence(
            str(action["campaign_id"]),
            player_action_id=str(action_id),
            result_fingerprint=str(snapshot["result_fingerprint"]),
        )
        if existing is not None:
            return existing
        self._require_ready_action(action)
        origin = self._require_approved_origin(action)
        ceiling_value = (origin.get("action_ruling") or {}).get("maximum_effect")
        effect_ceiling = (
            str(ceiling_value)[:1000] if ceiling_value else None
        )
        control = AiControlService(self.repo).authorize(
            str(action["campaign_id"]),
            "check consequence proposal",
        )

        result = await director.handle_check_consequence(
            campaign_id=str(action["campaign_id"]),
            player_action=str(action["action_text"]),
            check_snapshot=snapshot,
            pc_id=action.get("pc_id"),
            location=action.get("location"),
            map_id=action.get("map_id"),
            effect_ceiling=effect_ceiling,
        )

        self.repo.begin_immediate()
        if not self.repo.is_session_member_active(
            identity.member_id,
            identity.session_id,
        ):
            raise KpSessionEndedError(
                "KP session ended while the consequence model was running"
            )
        AiControlService(self.repo).revalidate(control)
        current_action = self.repo.get_player_action(str(action_id))
        current_checks = self.repo.list_skill_checks_for_action(str(action_id))
        current_opposed_checks = self.repo.list_opposed_checks_for_action(str(action_id))
        self._require_linked_scope(
            identity,
            self.repo.get_skill_check(command.check_id),
            current_action,
            current_checks,
        )
        current_snapshot = build_check_consequence_snapshot(
            current_checks, current_opposed_checks
        )
        if (
            current_snapshot["result_fingerprint"]
            != snapshot["result_fingerprint"]
        ):
            raise ValueError(
                "Check results changed while the consequence draft was generated"
            )
        existing = self.repo.find_live_check_consequence(
            str(current_action["campaign_id"]),
            player_action_id=str(action_id),
            result_fingerprint=str(current_snapshot["result_fingerprint"]),
        )
        if existing is not None:
            return existing
        self._require_ready_action(current_action)
        self._require_approved_origin(current_action)

        output = result.output
        hidden_batch = current_snapshot["has_hidden_checks"] is True
        if hidden_batch:
            self._require_kp_only_hidden_effects(output)
        proposal = self.repo.create_turn_proposal(
            campaign_id=str(current_action["campaign_id"]),
            pc_id=current_action.get("pc_id"),
            player_action=str(current_action["action_text"]),
            public_narration=(
                HIDDEN_CHECK_PUBLIC_NARRATION
                if hidden_batch
                else output.public_narration
            ),
            kp_notes=output.kp_notes,
            proposed_checks=[],
            proposed_events=output.proposed_events,
            proposed_memories=output.proposed_memories,
            proposed_npc_updates=output.proposed_npc_updates,
            proposed_map_moves=[],
            proposed_facts=output.proposed_facts,
            source_model=source_model,
        )
        self.repo.add_proposal_action(
            str(proposal["id"]),
            "action_ruling",
            actor="system",
            note="verified check consequence effect ceiling",
            payload=output.action_ruling.model_dump(mode="json"),
        )
        self.repo.attach_check_consequence_basis(
            proposal["id"],
            origin_proposal_id=str(origin["id"]),
            player_action_id=str(action_id),
            check_ids=[str(item["id"]) for item in current_checks],
            result_fingerprint=str(current_snapshot["result_fingerprint"]),
        )
        self.repo.create_context_assembly(
            proposal_id=proposal["id"],
            campaign_id=str(current_action["campaign_id"]),
            visibility_scope=result.context.visibility_scope,
            final_prompt=result.context.messages,
            included_sources=result.context.included_sources,
            excluded_sources=result.context.excluded_sources,
            token_estimate=result.context.token_estimate,
        )
        self.repo.append_realtime_event(
            session_id=identity.session_id,
            campaign_id=str(current_action["campaign_id"]),
            audience="kp",
            event_type="proposal.created",
            resource_type="turn_proposal",
            resource_id=proposal["id"],
            payload={
                "status": "draft",
                "proposal_kind": "check_consequence",
            },
        )
        return self.repo.get_turn_proposal(proposal["id"])

    def _require_approved_origin(self, action: dict) -> dict:
        proposal_id = action.get("proposal_id")
        if not proposal_id:
            raise ValueError("Player action has no approved check-request proposal")
        origin = self.repo.get_turn_proposal(str(proposal_id))
        if (
            origin["campaign_id"] != action["campaign_id"]
            or origin["status"] != "approved"
        ):
            raise ValueError("Check consequence requires an approved origin proposal")
        return origin

    @staticmethod
    def _require_ready_action(action: dict) -> None:
        if action["status"] != "reviewed":
            raise ValueError(
                "Player action must remain reviewed until its consequence is approved"
            )

    @staticmethod
    def _require_kp_only_hidden_effects(output: object) -> None:
        proposed_events = getattr(output, "proposed_events", ())
        if any(getattr(event, "visibility", None) != "kp" for event in proposed_events):
            raise ValueError(
                "Hidden check consequences can only persist KP-visible events"
            )
        proposed_memories = getattr(output, "proposed_memories", ())
        if any(
            getattr(memory, "visibility", None) != "kp"
            for memory in proposed_memories
        ):
            raise ValueError(
                "Hidden check consequences can only persist KP-visible memories"
            )
        if getattr(output, "proposed_npc_updates", ()):
            raise ValueError("Hidden check consequences cannot persist NPC updates")

    @staticmethod
    def _require_linked_scope(
        identity: AuthenticatedMember,
        selected_check: dict,
        action: dict,
        checks: list[dict],
    ) -> None:
        action_id = str(action["id"])
        origin_proposal_id = action.get("proposal_id")
        expected_scope = (
            identity.campaign_id,
            identity.session_id,
            action_id,
            origin_proposal_id,
        )
        if (
            action["campaign_id"] != identity.campaign_id
            or action["session_id"] != identity.session_id
            or not origin_proposal_id
            or selected_check.get("player_action_id") != action_id
        ):
            raise ValueError(
                "Check, player action, and origin proposal must share one campaign session"
            )
        if not checks or all(
            str(item["id"]) != str(selected_check["id"]) for item in checks
        ):
            raise ValueError("Selected check is not part of the linked action batch")
        for item in checks:
            item_scope = (
                item.get("campaign_id"),
                item.get("session_id"),
                item.get("player_action_id"),
                item.get("proposal_id"),
            )
            if item_scope != expected_scope:
                raise ValueError(
                    "Every check in a consequence batch must match its "
                    "campaign, session, action, and origin proposal"
                )

    @staticmethod
    def _require_kp(
        identity: AuthenticatedMember,
        check: dict,
    ) -> None:
        if identity.campaign_id != check["campaign_id"]:
            raise KeyError(f"Skill check not found: {check['id']}")
        if identity.session_id != check["session_id"]:
            raise KeyError(f"Skill check not found: {check['id']}")
        if identity.role != "kp":
            raise PermissionError("KP access required")


__all__ = [
    "CheckConsequenceService",
    "GenerateCheckConsequenceCommand",
]

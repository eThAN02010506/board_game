"""Proposal finalization after an authoritative parallel kernel commit."""

from __future__ import annotations

from typing import Any

from ai_kp.application.kernel_action_service import KernelActionService
from ai_kp.application.turn_service import TurnService
from ai_kp.platform.resolution import (
    HIDDEN_CHECK_PUBLIC_NARRATION,
    build_check_consequence_snapshot,
    project_public_check_consequences,
)
from ai_kp.platform.sessions.models import AuthenticatedMember


class ParallelActionWorkflowFinalizer:
    """Resolve direct origins and checked consequences without another model call."""

    def __init__(self, repo: Any):
        self.repo = repo
        self.turns = TurnService(repo)

    def finalize_item(
        self,
        item: dict[str, Any],
        *,
        batch: dict[str, Any],
        coordinator: AuthenticatedMember,
    ) -> None:
        outcome = str(item["outcome_key"])
        origin = self.repo.get_turn_proposal(str(item["proposal_id"]))
        narration = KernelActionService.narrative_for_outcome(origin, outcome)
        if item["adjudication"]["mode"] == "direct_resolution":
            if origin["status"] != "draft":
                raise ValueError("Direct parallel origin must remain draft until commit")
            self.turns.approve_parallel_proposal(
                str(origin["id"]),
                str(batch["campaign_id"]),
                coordinator,
                batch_id=str(batch["id"]),
                phase="commit",
                note=f"atomic parallel kernel outcome={outcome}",
                override_public_narration=narration,
            )
            return
        if origin["status"] != "approved":
            raise ValueError("Checked parallel origin must be an approved check request")
        checks = list(item["checks"])
        snapshot = build_check_consequence_snapshot(checks)
        public_narration = self._public_check_narration(
            snapshot,
            outcome=outcome,
            narration=narration,
        )
        consequence = self.repo.create_turn_proposal(
            campaign_id=str(batch["campaign_id"]),
            pc_id=origin.get("pc_id"),
            player_action=str(origin["player_action"]),
            public_narration=public_narration,
            kp_notes=(
                "deterministic parallel check consequence; "
                f"operator={item['operator_id']}; outcome={outcome}"
            ),
            proposed_checks=[],
            proposed_events=[],
            proposed_memories=[],
            proposed_npc_updates=[],
            proposed_map_moves=[],
            proposed_facts=[],
            source_model="kernel:parallel-consequence",
        )
        self.repo.add_proposal_action(
            str(consequence["id"]),
            "action_ruling",
            actor="system",
            note="deterministic parallel check consequence",
            payload={
                "goal": str(origin["player_action"])[:500],
                "method": str(item["operator_id"]),
                "target": "current contract world",
                "feasibility": "possible",
                "resolution": "automatic",
                "reason": f"The authoritative check produced {outcome}.",
                "maximum_effect": (
                    (origin.get("action_ruling") or {}).get("maximum_effect")
                    or "Only the contract-authorized outcome is applied."
                ),
                "alternative": "",
            },
        )
        self.repo.attach_check_consequence_basis(
            str(consequence["id"]),
            origin_proposal_id=str(origin["id"]),
            player_action_id=str(item["action_id"]),
            check_ids=[str(check["id"]) for check in checks],
            result_fingerprint=str(snapshot["result_fingerprint"]),
        )
        self.turns.approve_parallel_proposal(
            str(consequence["id"]),
            str(batch["campaign_id"]),
            coordinator,
            batch_id=str(batch["id"]),
            phase="commit",
            note=f"atomic parallel kernel outcome={outcome}",
            override_public_narration=public_narration,
        )

    @staticmethod
    def _public_check_narration(
        snapshot: dict[str, Any],
        *,
        outcome: str,
        narration: str | None,
    ) -> str:
        """Make accepted public stakes explicit without exposing hidden rolls."""

        public = project_public_check_consequences(snapshot)
        information_suffix = (
            f"\n\n无需检定即可确认：{'；'.join(public['automatic_information'])}。"
            if public["automatic_information"]
            else ""
        )
        # `narration` was authored from contract-declared public cues before the
        # roll existed. It can disclose an observable clue/effect, but cannot
        # contain the later blind roll, target or success tier.
        base = str(narration or "").strip()
        if not base:
            base = (
                HIDDEN_CHECK_PUBLIC_NARRATION
                if snapshot["has_hidden_checks"] is True
                else f"The action resolves with outcome: {outcome}."
            )
        details: list[str] = []
        for consequence in public["accepted_stakes"]:
            approach = str(consequence["push_approach"])
            stakes = str(consequence["text"])
            if approach and approach not in base:
                details.append(f"玩家采用的推动方式：{approach}")
            if stakes and stakes not in base and stakes not in details:
                label = (
                    "推动失败的既定代价已执行"
                    if consequence["was_pushed"] is True
                    else "失败的既定代价已执行"
                )
                details.append(f"{label}：{stakes}")
        consequence_suffix = (
            f"\n\n{'；'.join(details)}。" if details else ""
        )
        return f"{base}{information_suffix}{consequence_suffix}"


__all__ = ["ParallelActionWorkflowFinalizer"]

"""Read-only planning for independently adjudicated simultaneous actions.

The planner deliberately stops before proposal persistence.  Every model call is
made against one immutable contract/state basis and outside a SQLite transaction;
the later workflow can therefore persist all successful plans in one short write
transaction or discard the whole batch without leaving partially reviewed actions.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Literal

from ai_kp.application.ai_control_service import AiControlService
from ai_kp.application.errors import ConflictError, UpstreamServiceError
from ai_kp.application.ports.director import KernelDirector
from ai_kp.application.scenario_authority import (
    ScenarioAuthorityContext,
    load_scenario_authority_context,
    revalidate_scenario_authority_context,
)
from ai_kp.director.errors import CampaignAiCallCancelledError
from ai_kp.platform.resolution.contracts import (
    ResolutionPreview,
    ScenarioContract,
    ScenarioSnapshot,
)
from ai_kp.platform.resolution.explicit_operator_selection import (
    resolve_explicit_published_operator,
)
from ai_kp.platform.resolution.narrative_adapter import KernelNarrativeBundle
from ai_kp.platform.resolution.selected_action import (
    SelectedOperator,
    prepare_selected_kernel_action,
    selected_operator_from_semantic,
)
from ai_kp.platform.resolution.semantic_adapter import (
    SemanticSelectionResult,
)
from ai_kp.platform.resolution.tabletop_turn import TabletopTurnInterpretation

ParallelAttentionRoute = Literal[
    "conversation",
    "information",
    "roleplay",
    "clarification",
    "world_expansion",
    "task_method",
    "unavailable_operator",
]


class ParallelPlanningModelError(RuntimeError):
    """A model boundary failed before any parallel authority was persisted."""


@dataclass(frozen=True)
class PreparedParallelAction:
    """One independent mechanical plan bound to the batch's frozen authority."""

    action_id: str
    campaign_id: str
    session_id: str
    member_id: str
    pc_id: str | None
    actor_id: str
    action_text: str
    input_index: int
    run_id: str
    module_run_version: int
    contract_version_id: str
    contract_hash: str
    base_state_version: int
    operator_id: str
    requested_skill_key: str | None
    selected_skill_key: str | None
    preview: ResolutionPreview
    selection: SemanticSelectionResult
    deterministic_narrative: KernelNarrativeBundle
    narrative: KernelNarrativeBundle
    tabletop_turn: TabletopTurnInterpretation
    source_model: str
    diagnostic_narrative: KernelNarrativeBundle | None = None


@dataclass(frozen=True)
class UnsupportedParallelAction:
    """A safe planning result that grants no authority and needs attention."""

    action_id: str
    actor_id: str
    input_index: int
    route: ParallelAttentionRoute
    reason: str
    tabletop_turn: TabletopTurnInterpretation
    selection: SemanticSelectionResult | None = None
    needs_attention: bool = True


ParallelPlanningItem = PreparedParallelAction | UnsupportedParallelAction


@dataclass(frozen=True)
class ParallelSkillRepreview:
    """Restart-safe deterministic replacement for one durable batch item."""

    batch_id: str
    action_id: str
    actor_id: str
    run_id: str
    contract_version_id: str
    contract_hash: str
    base_state_version: int
    operator_id: str
    selected_skill_key: str
    preview: ResolutionPreview
    narrative: KernelNarrativeBundle


@dataclass(frozen=True)
class ParallelPlanningBatch:
    """All planning results derived from exactly one contract/state snapshot."""

    campaign_id: str
    session_id: str
    run_id: str
    module_run_version: int
    contract_version_id: str
    contract_hash: str
    base_state_version: int
    contract: ScenarioContract
    snapshot: ScenarioSnapshot
    items: tuple[ParallelPlanningItem, ...]

    @property
    def prepared(self) -> tuple[PreparedParallelAction, ...]:
        return tuple(
            item for item in self.items if isinstance(item, PreparedParallelAction)
        )

    @property
    def unsupported(self) -> tuple[UnsupportedParallelAction, ...]:
        return tuple(
            item for item in self.items if isinstance(item, UnsupportedParallelAction)
        )

    @property
    def ready(self) -> bool:
        return len(self.items) >= 2 and not self.unsupported


class ParallelActionPlanningService:
    """Interpret and preview 2--12 actions without acquiring a write lock."""

    def __init__(self, repo: Any):
        self.repo = repo

    async def plan(
        self,
        actions: Sequence[dict[str, Any]],
        director: KernelDirector,
        *,
        source_model: str,
        profile: str = "small",
    ) -> ParallelPlanningBatch:
        self._require_no_transaction("parallel planning")
        authoritative_actions = self._authoritative_actions(actions)
        campaign_id = str(authoritative_actions[0]["campaign_id"])
        session_id = str(authoritative_actions[0]["session_id"])

        control_service = AiControlService(self.repo)
        control = control_service.authorize(campaign_id, "parallel action planning")
        if control.run_id is None or control.run_version is None:
            raise ConflictError("Parallel planning requires an active scenario run")
        authority = self._freeze_context(control.run_id)
        contract = self._clone_contract(authority.contract)
        snapshot = self._clone_snapshot(authority.snapshot)
        base_state_version = authority.state_version
        if snapshot.status != "active":
            raise ConflictError("Parallel planning requires an active scenario state")

        items: list[ParallelPlanningItem] = []
        for index, action in enumerate(authoritative_actions):
            player_action = str(action["action_text"])
            explicit = resolve_explicit_published_operator(
                contract, snapshot, player_action
            )
            if explicit is None:
                self._require_no_transaction("tabletop interpretation")
                try:
                    interpretation = await director.interpret_tabletop_turn(
                        campaign_id=campaign_id,
                        contract=self._clone_contract(contract),
                        snapshot=self._clone_snapshot(snapshot),
                        player_action=player_action,
                    )
                    interpretation = TabletopTurnInterpretation.model_validate(
                        interpretation
                    )
                except CampaignAiCallCancelledError:
                    raise
                except (RuntimeError, UpstreamServiceError, ValueError) as exc:
                    raise ParallelPlanningModelError(
                        "The tabletop interpretation model was unavailable or malformed"
                    ) from exc
                self._revalidate_control(control_service, control)
            else:
                interpretation = explicit.interpretation
                self._revalidate_control(control_service, control)
                if explicit.selection_result is None:
                    actor_id = str(action.get("pc_id") or action["member_id"])
                    items.append(
                        UnsupportedParallelAction(
                            action_id=str(action["id"]),
                            actor_id=actor_id,
                            input_index=index,
                            route="clarification",
                            reason=explicit.reason,
                            tabletop_turn=interpretation,
                        )
                    )
                    continue
            actor_id = str(action.get("pc_id") or action["member_id"])
            if interpretation.route != "mechanical":
                items.append(
                    UnsupportedParallelAction(
                        action_id=str(action["id"]),
                        actor_id=actor_id,
                        input_index=index,
                        route=interpretation.route,
                        reason=self._unsupported_route_reason(interpretation),
                        tabletop_turn=interpretation,
                    )
                )
                continue

            if explicit is not None:
                selection = explicit.selection_result
                if selection is None:  # guarded by the deterministic branch above
                    raise RuntimeError(
                        "Explicit operator selection lost its authority result"
                    )
            else:
                self._require_no_transaction("kernel action selection")
                try:
                    selection = await director.select_kernel_action(
                        campaign_id=campaign_id,
                        contract=self._clone_contract(contract),
                        snapshot=self._clone_snapshot(snapshot),
                        player_action=player_action,
                        profile=profile,
                    )
                    selection = SemanticSelectionResult.model_validate(selection)
                except CampaignAiCallCancelledError:
                    raise
                except (RuntimeError, UpstreamServiceError, ValueError) as exc:
                    raise ParallelPlanningModelError(
                        "The kernel selection model was unavailable or malformed"
                    ) from exc
                self._revalidate_control(control_service, control)
            selected = selection.selection
            if selected.kind != "operator":
                route: ParallelAttentionRoute = (
                    "task_method" if selected.kind == "task_method" else "world_expansion"
                )
                if selected.kind == "clarification":
                    route = "clarification"
                items.append(
                    UnsupportedParallelAction(
                        action_id=str(action["id"]),
                        actor_id=actor_id,
                        input_index=index,
                        route=route,
                        reason=(
                            "Parallel planning currently accepts only an existing "
                            "contract operator; this action was not materialized or executed."
                        ),
                        tabletop_turn=interpretation,
                        selection=selection,
                    )
                )
                continue

            try:
                prepared_action = prepare_selected_kernel_action(
                    contract,
                    snapshot,
                    action_id=str(action["id"]),
                    actor_id=actor_id,
                    player_action=str(action["action_text"]),
                    selection=selected_operator_from_semantic(selected),
                )
            except ValueError:
                items.append(
                    UnsupportedParallelAction(
                        action_id=str(action["id"]),
                        actor_id=actor_id,
                        input_index=index,
                        route="unavailable_operator",
                        reason=(
                            "The selected operator or skill is outside the frozen "
                            "scenario contract; no proposal was written."
                        ),
                        tabletop_turn=interpretation,
                        selection=selection,
                    )
                )
                continue
            operator = prepared_action.operator
            preview = prepared_action.preview
            if not preview.allowed:
                items.append(
                    UnsupportedParallelAction(
                        action_id=str(action["id"]),
                        actor_id=actor_id,
                        input_index=index,
                        route="unavailable_operator",
                        reason=preview.reason,
                        tabletop_turn=interpretation,
                        selection=selection,
                    )
                )
                continue

            player_requested_skill_key = selected.requested_skill_key
            selection = selection.model_copy(
                update={
                    "selection": selected.model_copy(
                        update={
                            "requested_skill_key": preview.selected_skill_key,
                        }
                    )
                }
            )

            deterministic = prepared_action.deterministic_narrative
            diagnostic_narrative = None
            if profile == "large":
                try:
                    self._require_no_transaction("kernel outcome narration")
                    candidate_narrative = await director.narrate_kernel_action(
                        campaign_id=campaign_id,
                        contract=self._clone_contract(contract),
                        preview=preview,
                        snapshot=self._clone_snapshot(snapshot),
                        player_action=str(action["action_text"]),
                    )
                    candidate_narrative = KernelNarrativeBundle.model_validate(
                        candidate_narrative
                    )
                    self._revalidate_control(control_service, control)
                    if self._narrative_preserves_basis(
                        candidate_narrative, deterministic
                    ):
                        # Parallel proposals remain deterministic in the first
                        # release.  Keeping a shape-compatible model result only
                        # as an in-memory diagnostic prevents prose from becoming
                        # an authority or consent surface.
                        diagnostic_narrative = candidate_narrative
                except CampaignAiCallCancelledError:
                    raise
                except (RuntimeError, UpstreamServiceError, ValueError):
                    # Presentation diagnostics are optional; authority and
                    # player-visible consent are always deterministic.
                    diagnostic_narrative = None

            items.append(
                PreparedParallelAction(
                    action_id=str(action["id"]),
                    campaign_id=campaign_id,
                    session_id=session_id,
                    member_id=str(action["member_id"]),
                    pc_id=str(action["pc_id"]) if action.get("pc_id") else None,
                    actor_id=actor_id,
                    action_text=str(action["action_text"]),
                    input_index=index,
                    run_id=control.run_id,
                    module_run_version=control.run_version,
                    contract_version_id=authority.contract_version_id,
                    contract_hash=authority.contract_hash,
                    base_state_version=base_state_version,
                    operator_id=operator.operator_id,
                    requested_skill_key=player_requested_skill_key,
                    selected_skill_key=preview.selected_skill_key,
                    preview=preview,
                    selection=selection,
                    deterministic_narrative=deterministic,
                    narrative=deterministic,
                    tabletop_turn=interpretation,
                    source_model=source_model,
                    diagnostic_narrative=diagnostic_narrative,
                )
            )

        self._revalidate_control(control_service, control)
        self._revalidate_frozen_context(authority)
        self._revalidate_actions(authoritative_actions)
        self._require_no_transaction("parallel planning completion")
        return ParallelPlanningBatch(
            campaign_id=campaign_id,
            session_id=session_id,
            run_id=control.run_id,
            module_run_version=control.run_version,
            contract_version_id=authority.contract_version_id,
            contract_hash=authority.contract_hash,
            base_state_version=base_state_version,
            contract=self._clone_contract(contract),
            snapshot=self._clone_snapshot(snapshot),
            items=tuple(items),
        )

    def repreview_selected_skill(
        self,
        prepared: PreparedParallelAction,
        *,
        requested_skill_key: str,
    ) -> PreparedParallelAction:
        """Re-preview one player skill choice against the original state basis.

        This method performs no model calls and no writes.  It rejects a changed
        contract or state instead of carrying the old preview hash forward.
        """

        self._require_no_transaction("parallel skill re-preview")
        authority = self._freeze_context(prepared.run_id)
        contract = authority.contract
        snapshot = authority.snapshot
        base_state_version = authority.state_version
        if (
            authority.contract_version_id != prepared.contract_version_id
            or authority.contract_hash != prepared.contract_hash
            or base_state_version != prepared.base_state_version
        ):
            raise ConflictError(
                "Scenario authority changed before the selected skill was re-previewed"
            )
        action = self.repo.get_player_action(prepared.action_id)
        if not self._same_action(action, prepared):
            raise ConflictError("Player action changed before skill re-preview")
        self._validate_repreview_action_status(action, prepared)
        selected = prepared.selection.selection.model_copy(
            update={"requested_skill_key": requested_skill_key}
        )
        authoritative = prepare_selected_kernel_action(
            contract,
            snapshot,
            action_id=str(action["id"]),
            actor_id=str(action.get("pc_id") or action["member_id"]),
            player_action=prepared.action_text,
            selection=selected_operator_from_semantic(selected),
        )
        preview = authoritative.preview
        if not preview.allowed or preview.selected_skill_key != requested_skill_key:
            raise ValueError("Selected skill is not authorized by the frozen operator")
        deterministic = authoritative.deterministic_narrative
        selection = prepared.selection.model_copy(
            update={
                "selection": prepared.selection.selection.model_copy(
                    update={"requested_skill_key": requested_skill_key}
                ),
                "allowed_skill_keys": tuple(
                    choice.skill_key for choice in preview.skill_choices
                ),
            }
        )
        return replace(
            prepared,
            requested_skill_key=requested_skill_key,
            selected_skill_key=requested_skill_key,
            preview=preview,
            selection=selection,
            deterministic_narrative=deterministic,
            narrative=deterministic,
        )

    def repreview_persisted_skill(
        self,
        batch_id: str,
        action_id: str,
        *,
        requested_skill_key: str,
        actor_member_id: str,
    ) -> ParallelSkillRepreview:
        """Rebuild a skill preview from durable batch authority after a restart."""

        self._require_no_transaction("persisted parallel skill re-preview")
        batch = self.repo.get_parallel_action_batch(batch_id)
        if batch["status"] != "awaiting_confirmation":
            raise ConflictError("Parallel skill selection is already closed")
        run = self.repo.get_active_campaign_module_run(str(batch["campaign_id"]))
        if (
            run is None
            or str(run["id"]) != str(batch["run_id"])
            or int(run["version"]) != int(batch["module_run_version"])
            or run.get("director_control_mode") != "ai_assist"
        ):
            raise ConflictError(
                "Scenario run control changed before the selected skill was re-previewed"
            )
        matching = [
            item for item in batch["items"] if item["action_id"] == action_id
        ]
        if len(matching) != 1:
            raise KeyError(f"Parallel batch action not found: {action_id}")
        item = matching[0]
        action = self.repo.get_player_action(action_id)
        if (
            action["campaign_id"] != batch["campaign_id"]
            or action["session_id"] != batch["session_id"]
            or action["member_id"] != actor_member_id
            or not self.repo.is_session_member_active(
                actor_member_id, str(batch["session_id"])
            )
            or action["status"] != "reviewed"
            or action.get("proposal_id") != item["proposal_id"]
            or str(action.get("pc_id") or action["member_id"]) != item["actor_id"]
        ):
            raise ConflictError(
                "Player action no longer matches the durable parallel batch item"
            )
        authority = self._freeze_context(str(batch["run_id"]))
        contract = authority.contract
        snapshot = authority.snapshot
        base_state_version = authority.state_version
        if (
            authority.contract_version_id != batch["contract_version_id"]
            or base_state_version != batch["base_state_version"]
        ):
            raise ConflictError(
                "Scenario authority changed before the selected skill was re-previewed"
            )
        authoritative = prepare_selected_kernel_action(
            contract,
            snapshot,
            action_id=str(action["id"]),
            actor_id=str(item["actor_id"]),
            player_action=str(action["action_text"]),
            selection=SelectedOperator(
                operator_id=str(item["operator_id"]),
                requested_skill_key=requested_skill_key,
            ),
        )
        preview = authoritative.preview
        if not preview.allowed or preview.selected_skill_key != requested_skill_key:
            raise ValueError("Selected skill is not authorized by the frozen operator")
        narrative = authoritative.deterministic_narrative
        return ParallelSkillRepreview(
            batch_id=str(batch["id"]),
            action_id=action_id,
            actor_id=str(item["actor_id"]),
            run_id=str(batch["run_id"]),
            contract_version_id=str(batch["contract_version_id"]),
            contract_hash=authority.contract_hash,
            base_state_version=int(batch["base_state_version"]),
            operator_id=str(item["operator_id"]),
            selected_skill_key=requested_skill_key,
            preview=preview,
            narrative=narrative,
        )

    def _authoritative_actions(
        self, actions: Sequence[dict[str, Any]]
    ) -> tuple[dict[str, Any], ...]:
        if not 2 <= len(actions) <= 12:
            raise ValueError("Parallel planning requires between 2 and 12 actions")
        action_ids = [str(action.get("id") or "") for action in actions]
        if any(not action_id for action_id in action_ids):
            raise ValueError("Every parallel action requires an ID")
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("Parallel action IDs must be unique")
        authoritative = tuple(self.repo.get_player_action(item) for item in action_ids)
        campaign_ids = {str(action["campaign_id"]) for action in authoritative}
        session_ids = {str(action["session_id"]) for action in authoritative}
        member_ids = {str(action["member_id"]) for action in authoritative}
        actor_ids = {
            str(action.get("pc_id") or action["member_id"])
            for action in authoritative
        }
        if len(campaign_ids) != 1 or len(session_ids) != 1:
            raise ValueError("Parallel actions must belong to one campaign session")
        if len(member_ids) != len(authoritative) or len(actor_ids) != len(authoritative):
            raise ValueError("Each parallel action must belong to a different player actor")
        for action in authoritative:
            if action["status"] != "submitted" or action.get("proposal_id") is not None:
                raise ConflictError(f"Player action is already {action['status']}")
            if not self.repo.is_session_member_active(
                str(action["member_id"]), str(action["session_id"])
            ):
                raise ConflictError(
                    "Parallel planning requires every action owner to remain active"
                )
        return authoritative

    def _freeze_context(self, run_id: str) -> ScenarioAuthorityContext:
        try:
            return load_scenario_authority_context(
                self.repo,
                run_id,
                state_policy="require",
            )
        except (KeyError, ConflictError) as exc:
            raise ConflictError(
                "Parallel planning requires an initialized, contract-bound scenario state"
            ) from exc

    def _revalidate_frozen_context(
        self,
        expected: ScenarioAuthorityContext,
    ) -> None:
        try:
            revalidate_scenario_authority_context(self.repo, expected)
        except ConflictError as exc:
            raise ConflictError(
                "Scenario authority changed while parallel planning ran"
            ) from exc

    def _revalidate_actions(self, actions: Sequence[dict[str, Any]]) -> None:
        for original in actions:
            current = self.repo.require_submitted_player_action(
                str(original["id"]),
                str(original["campaign_id"]),
                str(original["session_id"]),
            )
            if not self._same_action(current, original):
                raise ConflictError("Player action changed while parallel planning ran")
            if not self.repo.is_session_member_active(
                str(current["member_id"]), str(current["session_id"])
            ):
                raise ConflictError(
                    "Parallel action owner left while parallel planning ran"
                )

    def _validate_repreview_action_status(
        self,
        action: dict[str, Any],
        prepared: PreparedParallelAction,
    ) -> None:
        if action["status"] == "submitted" and action.get("proposal_id") is None:
            return
        if action["status"] != "reviewed" or action.get("proposal_id") is None:
            raise ConflictError("Player action is not eligible for skill re-preview")
        batch = self.repo.get_active_parallel_action_batch_for_action(
            prepared.action_id
        )
        if batch is None or (
            batch["run_id"] != prepared.run_id
            or batch["contract_version_id"] != prepared.contract_version_id
            or batch["base_state_version"] != prepared.base_state_version
        ):
            raise ConflictError("Player action lost its parallel batch authority")
        matching = [
            item for item in batch["items"] if item["action_id"] == prepared.action_id
        ]
        if len(matching) != 1 or (
            matching[0]["proposal_id"] != action["proposal_id"]
            or matching[0]["operator_id"] != prepared.operator_id
            or matching[0]["preview_hash"] != prepared.preview.preview_hash
        ):
            raise ConflictError("Player action no longer matches its parallel batch item")

    @staticmethod
    def _same_action(current: dict[str, Any], original: Any) -> bool:
        def value(key: str) -> Any:
            if not isinstance(original, PreparedParallelAction):
                return original.get(key)
            if key == "id":
                return original.action_id
            return getattr(original, key)

        return all(
            current.get(key) == value(key)
            for key in ("id", "campaign_id", "session_id", "member_id", "pc_id", "action_text")
        )

    def _revalidate_control(self, service: AiControlService, control: Any) -> None:
        self._require_no_transaction("parallel AI control revalidation")
        service.revalidate(control)

    def _require_no_transaction(self, operation: str) -> None:
        connection = getattr(self.repo, "connection", None)
        if connection is not None and connection.in_transaction:
            raise ConflictError(
                f"{operation} cannot run while the SQLite connection holds a transaction"
            )

    @staticmethod
    def _unsupported_route_reason(
        interpretation: TabletopTurnInterpretation,
    ) -> str:
        if interpretation.route == "clarification":
            return "The action needs a material clarification before parallel settlement."
        return (
            f"The {interpretation.route} route is not a mechanical parallel action; "
            "it must be handled as an independent tabletop response."
        )

    @staticmethod
    def _narrative_preserves_basis(
        candidate: KernelNarrativeBundle,
        deterministic: KernelNarrativeBundle,
    ) -> bool:
        if candidate.basis_hash != deterministic.basis_hash:
            return False
        return tuple(
            (item.outcome_key, item.speaker_entity_id)
            for item in candidate.outcomes
        ) == tuple(
            (item.outcome_key, item.speaker_entity_id)
            for item in deterministic.outcomes
        )

    @staticmethod
    def _clone_contract(contract: ScenarioContract) -> ScenarioContract:
        return ScenarioContract.model_validate(contract.model_dump(mode="python"))

    @staticmethod
    def _clone_snapshot(snapshot: ScenarioSnapshot) -> ScenarioSnapshot:
        return ScenarioSnapshot.model_validate(snapshot.model_dump(mode="python"))


__all__ = [
    "ParallelActionPlanningService",
    "ParallelAttentionRoute",
    "ParallelPlanningBatch",
    "ParallelPlanningItem",
    "ParallelPlanningModelError",
    "ParallelSkillRepreview",
    "PreparedParallelAction",
    "UnsupportedParallelAction",
]

"""Persistence boundary for approved dynamic-branch execution."""

from typing import Any, Protocol


class DynamicBranchStore(Protocol):
    def begin_immediate(self) -> None: ...

    def create_dynamic_branch_run(
        self,
        *,
        proposal_id: str,
        campaign_id: str,
        module_run_id: str,
        plan: dict[str, Any],
        member_id: str,
    ) -> dict[str, Any]: ...

    def get_dynamic_branch_run(self, branch_id: str) -> dict[str, Any]: ...

    def get_dynamic_branch_for_proposal(
        self,
        proposal_id: str,
    ) -> dict[str, Any] | None: ...

    def list_dynamic_branch_runs(
        self,
        campaign_id: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def find_dynamic_branch_event(
        self,
        branch_id: str,
        command_id: str,
    ) -> dict[str, Any] | None: ...

    def transition_dynamic_branch(
        self,
        branch_id: str,
        *,
        expected_version: int,
        status: str,
        current_beat_index: int,
        event_type: str,
        beat_id: str | None,
        outcome: str | None,
        note: str,
        payload: dict[str, Any],
        member_id: str,
        command_id: str,
    ) -> dict[str, Any]: ...

    def get_campaign_module_run(self, run_id: str) -> dict[str, Any]: ...

    def list_module_run_entity_states(self, run_id: str) -> list[dict[str, Any]]: ...

    def module_graph_reachability(
        self,
        module_id: str,
        entry_entity_ids: tuple[str, ...],
    ) -> dict[str, Any]: ...

    def list_fact_heads(self, campaign_id: str) -> list[Any]: ...


__all__ = ["DynamicBranchStore"]

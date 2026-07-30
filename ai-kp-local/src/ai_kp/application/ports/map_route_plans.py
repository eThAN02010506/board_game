"""Application boundary for persisted map route plans."""

from typing import Protocol


class MapRoutePlanStore(Protocol):
    def create_map_route_plan(
        self,
        *,
        campaign_id: str,
        map_id: str,
        member_id: str,
        title: str,
        note: str,
        token_routes: list[dict],
        initial_status: str,
        visibility: str,
        allowed_visibility: tuple[str, ...],
    ) -> dict: ...

    def list_map_route_plans(
        self,
        campaign_id: str,
        *,
        map_id: str | None,
        member_token_id: str | None,
        include_kp: bool,
    ) -> list[dict]: ...

    def update_map_route_plan_status(
        self,
        plan_id: str,
        *,
        expected_version: int,
        status: str,
    ) -> dict: ...


__all__ = ["MapRoutePlanStore"]

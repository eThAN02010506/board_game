"""Use cases for planning split-party movement without moving tokens early."""

from dataclasses import dataclass

from ai_kp.application.ports.map_route_plans import MapRoutePlanStore


@dataclass(frozen=True)
class TokenRoute:
    token_id: str
    waypoints: tuple[str, ...]


@dataclass(frozen=True)
class CreateRoutePlanCommand:
    title: str
    note: str
    token_routes: tuple[TokenRoute, ...]
    player_submission: bool


class MapRoutePlanService:
    def __init__(self, repo: MapRoutePlanStore):
        self.repo = repo

    def create(
        self,
        *,
        campaign_id: str,
        map_id: str,
        member_id: str,
        command: CreateRoutePlanCommand,
    ) -> dict:
        routes = [
            {"token_id": route.token_id, "waypoints": list(route.waypoints)}
            for route in command.token_routes
        ]
        if not routes:
            raise ValueError("Route plan requires at least one token route")
        return self.repo.create_map_route_plan(
            campaign_id=campaign_id,
            map_id=map_id,
            member_id=member_id,
            title=command.title,
            note=command.note,
            token_routes=routes,
            initial_status="proposed" if command.player_submission else "approved",
            visibility="session",
            allowed_visibility=(
                ("player", "table")
                if command.player_submission
                else ("player", "table", "kp")
            ),
        )


__all__ = [
    "CreateRoutePlanCommand",
    "MapRoutePlanService",
    "TokenRoute",
]

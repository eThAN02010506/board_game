"""Persistence contract for explicit campaign travel graphs."""

from typing import Protocol


class TravelGraphStore(Protocol):
    def get_campaign(self, campaign_id: str) -> dict: ...

    def list_travel_locations(self, campaign_id: str) -> list[dict]: ...

    def find_travel_location_by_normalized_name(
        self,
        campaign_id: str,
        normalized_name: str,
    ) -> dict | None: ...

    def create_travel_location(self, campaign_id: str, **values) -> dict: ...

    def delete_travel_location(self, campaign_id: str, location_id: str) -> None: ...

    def list_travel_routes(self, campaign_id: str) -> list[dict]: ...

    def find_travel_route(
        self,
        campaign_id: str,
        from_location_id: str,
        to_location_id: str,
        travel_mode: str,
    ) -> dict | None: ...

    def create_travel_route(self, campaign_id: str, **values) -> dict: ...

    def delete_travel_route(self, campaign_id: str, route_id: str) -> None: ...


__all__ = ["TravelGraphStore"]

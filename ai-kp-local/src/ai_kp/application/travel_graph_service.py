"""Validate campaign travel facts and compute explainable shortest routes."""

from __future__ import annotations

import heapq
import unicodedata
from dataclasses import dataclass

from ai_kp.application.ports.travel_graph import TravelGraphStore


@dataclass(frozen=True)
class TravelLocationCommand:
    name: str
    aliases: tuple[str, ...] = ()
    source_kind: str = "manual"
    source_ref: str | None = None
    kp_notes: str = ""


@dataclass(frozen=True)
class TravelRouteCommand:
    from_location_id: str
    to_location_id: str
    travel_minutes: int
    travel_mode: str = "other"
    bidirectional: bool = True
    status: str = "open"
    kp_notes: str = ""


class TravelGraphService:
    def __init__(self, repo: TravelGraphStore):
        self.repo = repo

    def list_graph(self, campaign_id: str) -> dict:
        self.repo.get_campaign(campaign_id)
        return {
            "locations": self.repo.list_travel_locations(campaign_id),
            "routes": self.repo.list_travel_routes(campaign_id),
        }

    def create_location(
        self,
        campaign_id: str,
        command: TravelLocationCommand,
    ) -> dict:
        name = self._text(command.name, "location name", 200)
        aliases = tuple(
            dict.fromkeys(
                self.normalize(item)
                for item in command.aliases
                if self.normalize(item)
            )
        )
        if len(aliases) > 30:
            raise ValueError("Travel location accepts at most 30 aliases")
        if any(len(item) > 200 for item in aliases):
            raise ValueError("Travel location aliases cannot exceed 200 characters")
        return self.repo.create_travel_location(
            campaign_id,
            name=name,
            normalized_name=self.normalize(name),
            aliases=list(aliases),
            source_kind=command.source_kind,
            source_ref=self._optional_text(command.source_ref, 300),
            kp_notes=self._text(
                command.kp_notes,
                "KP notes",
                2000,
                allow_blank=True,
            ),
        )

    def delete_location(self, campaign_id: str, location_id: str) -> None:
        self.repo.delete_travel_location(campaign_id, location_id)

    def create_route(
        self,
        campaign_id: str,
        command: TravelRouteCommand,
    ) -> dict:
        if command.from_location_id == command.to_location_id:
            raise ValueError("Travel route endpoints must differ")
        if not 1 <= command.travel_minutes <= 525_600:
            raise ValueError("Travel minutes must be between 1 and 525600")
        return self.repo.create_travel_route(
            campaign_id,
            from_location_id=command.from_location_id,
            to_location_id=command.to_location_id,
            travel_minutes=command.travel_minutes,
            travel_mode=command.travel_mode,
            bidirectional=command.bidirectional,
            status=command.status,
            kp_notes=self._text(
                command.kp_notes,
                "KP notes",
                2000,
                allow_blank=True,
            ),
        )

    def delete_route(self, campaign_id: str, route_id: str) -> None:
        self.repo.delete_travel_route(campaign_id, route_id)

    def preview(
        self,
        campaign_id: str,
        *,
        origins: tuple[str, ...],
        destination: str,
        max_minutes: int,
    ) -> dict:
        return self.resolve_destinations(
            campaign_id,
            origins=origins,
            destinations=(destination,),
            max_minutes=max_minutes,
        )[0]

    def resolve_destinations(
        self,
        campaign_id: str,
        *,
        origins: tuple[str, ...],
        destinations: tuple[str, ...],
        max_minutes: int,
    ) -> list[dict]:
        """Resolve many destinations with one multi-source Dijkstra traversal."""
        if not destinations:
            return []
        graph = self.list_graph(campaign_id)
        locations = graph["locations"]
        if not locations:
            return [
                self._empty_result("graph_empty", max_minutes)
                for _destination in destinations
            ]
        origin_ids = self._resolve_ids(locations, origins)
        if not origin_ids:
            return [
                self._empty_result("unresolved_origin", max_minutes)
                for _destination in destinations
            ]
        adjacency: dict[str, list[tuple[str, dict]]] = {
            str(item["id"]): [] for item in locations
        }
        for route in graph["routes"]:
            if route["status"] != "open":
                continue
            start = str(route["from_location_id"])
            end = str(route["to_location_id"])
            adjacency[start].append((end, route))
            if route["bidirectional"]:
                adjacency[end].append((start, route))

        queue: list[tuple[int, str]] = []
        distances: dict[str, int] = {}
        previous: dict[str, tuple[str, dict]] = {}
        for location_id in sorted(origin_ids):
            distances[location_id] = 0
            heapq.heappush(queue, (0, location_id))
        while queue:
            total, current = heapq.heappop(queue)
            if total != distances.get(current):
                continue
            for neighbour, route in adjacency.get(current, []):
                candidate = total + int(route["travel_minutes"])
                if candidate < distances.get(neighbour, 2**63 - 1):
                    distances[neighbour] = candidate
                    previous[neighbour] = (current, route)
                    heapq.heappush(queue, (candidate, neighbour))
        location_by_id = {str(item["id"]): item for item in locations}
        return [
            self._destination_result(
                locations=locations,
                destination=destination,
                origin_ids=origin_ids,
                distances=distances,
                previous=previous,
                location_by_id=location_by_id,
                max_minutes=max_minutes,
            )
            for destination in destinations
        ]

    def _destination_result(
        self,
        *,
        locations: list[dict],
        destination: str,
        origin_ids: set[str],
        distances: dict[str, int],
        previous: dict[str, tuple[str, dict]],
        location_by_id: dict[str, dict],
        max_minutes: int,
    ) -> dict:
        destination_ids = self._resolve_ids(locations, (destination,))
        if not destination_ids:
            return self._empty_result("unresolved_destination", max_minutes)
        reachable_ids = destination_ids & distances.keys()
        if not reachable_ids:
            return self._empty_result("unreachable", max_minutes)
        target_id = min(reachable_ids, key=lambda item: (distances[item], item))
        path_ids = [target_id]
        legs = []
        current = target_id
        while current not in origin_ids:
            prior, route = previous[current]
            legs.append(
                {
                    "route_id": route["id"],
                    "from_location_id": prior,
                    "to_location_id": current,
                    "travel_minutes": int(route["travel_minutes"]),
                    "travel_mode": route["travel_mode"],
                }
            )
            current = prior
            path_ids.append(current)
        path_ids.reverse()
        legs.reverse()
        total_minutes = distances[target_id]
        return {
            "status": (
                "same_location"
                if total_minutes == 0
                else "reachable"
                if total_minutes <= max_minutes
                else "over_limit"
            ),
            "total_minutes": total_minutes,
            "max_minutes": max_minutes,
            "within_limit": total_minutes <= max_minutes,
            "locations": [
                self._public_location(location_by_id[item]) for item in path_ids
            ],
            "legs": legs,
        }

    @classmethod
    def _resolve_ids(cls, locations: list[dict], names: tuple[str, ...]) -> set[str]:
        wanted = {cls.normalize(item) for item in names if cls.normalize(item)}
        result = set()
        for location in locations:
            known = {str(location["normalized_name"])}
            known.update(str(item) for item in location["aliases"])
            if wanted & known:
                result.add(str(location["id"]))
        return result

    @staticmethod
    def _public_location(location: dict) -> dict:
        return {"id": location["id"], "name": location["name"]}

    @staticmethod
    def _empty_result(status: str, max_minutes: int) -> dict:
        return {
            "status": status,
            "total_minutes": None,
            "max_minutes": max_minutes,
            "within_limit": False,
            "locations": [],
            "legs": [],
        }

    @staticmethod
    def normalize(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

    @classmethod
    def _text(
        cls,
        value: str,
        field: str,
        maximum: int,
        *,
        allow_blank: bool = False,
    ) -> str:
        normalized = " ".join(unicodedata.normalize("NFKC", value).split())
        if not normalized and not allow_blank:
            raise ValueError(f"{field} cannot be blank")
        if len(normalized) > maximum:
            raise ValueError(f"{field} cannot exceed {maximum} characters")
        return normalized

    @classmethod
    def _optional_text(cls, value: str | None, maximum: int) -> str | None:
        if value is None:
            return None
        return cls._text(value, "source reference", maximum, allow_blank=True) or None


__all__ = [
    "TravelGraphService",
    "TravelLocationCommand",
    "TravelRouteCommand",
]

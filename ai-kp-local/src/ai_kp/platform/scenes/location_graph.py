"""Canonical system-neutral location graph for route and adjacency queries."""

from collections import deque
from dataclasses import dataclass, field


@dataclass
class LocationGraph:
    edges: dict[str, set[str]] = field(default_factory=dict)

    def add_location(self, location: str) -> None:
        self.edges.setdefault(location, set())

    def add_route(self, start: str, end: str) -> None:
        self.add_location(start)
        self.add_location(end)
        self.edges[start].add(end)
        self.edges[end].add(start)

    def shortest_route(self, start: str, end: str) -> list[str]:
        if start == end:
            return [start]
        queue: deque[tuple[str, list[str]]] = deque([(start, [start])])
        visited = {start}
        while queue:
            current, path = queue.popleft()
            for neighbor in self.edges.get(current, set()):
                if neighbor in visited:
                    continue
                next_path = [*path, neighbor]
                if neighbor == end:
                    return next_path
                visited.add(neighbor)
                queue.append((neighbor, next_path))
        return []

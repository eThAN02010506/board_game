from dataclasses import dataclass

from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.platform.scenes.map_generation import generate_map


@dataclass(frozen=True)
class GenerateMapCommand:
    title: str
    prompt: str
    locations: tuple[str, ...] = ()
    routes: tuple[tuple[str, str], ...] = ()
    style: str = "investigation"
    width: int = 960
    height: int = 640


@dataclass(frozen=True)
class PlaceTokenCommand:
    label: str
    location_name: str
    actor_type: str = "pc"
    actor_id: str | None = None
    visibility: str = "table"
    color: str = "#b93f2d"


@dataclass(frozen=True)
class MoveTokenCommand:
    to_location_name: str
    moved_by: str
    note: str = ""
    require_route: bool = True
    allowed_visibility: tuple[str, ...] | None = None
    expected_version: int | None = None


class MapService:
    """Coordinate persistent map changes and their realtime outbox records."""

    def __init__(self, repo: Repository):
        self.repo = repo

    def generate_and_save(
        self,
        campaign_id: str,
        session_id: str,
        command: GenerateMapCommand,
    ) -> dict:
        generated_map = generate_map(
            title=command.title,
            prompt=command.prompt,
            location_names=list(command.locations) or None,
            routes=list(command.routes) or None,
            style=command.style,
            width=command.width,
            height=command.height,
        )
        saved_map = self.repo.create_map(campaign_id, generated_map, created_by="ai")
        self.repo.append_realtime_event(
            session_id=session_id,
            campaign_id=campaign_id,
            audience="kp",
            event_type="map.created",
            resource_type="map",
            resource_id=saved_map["id"],
            payload={"status": "draft"},
        )
        return saved_map

    def publish(self, map_id: str, campaign_id: str, session_id: str) -> dict:
        return self._set_status(
            map_id,
            campaign_id,
            session_id,
            status="published",
            event_type="map.published",
        )

    def unpublish(self, map_id: str, campaign_id: str, session_id: str) -> dict:
        return self._set_status(
            map_id,
            campaign_id,
            session_id,
            status="draft",
            event_type="map.unpublished",
        )

    def _set_status(
        self,
        map_id: str,
        campaign_id: str,
        session_id: str,
        *,
        status: str,
        event_type: str,
    ) -> dict:
        saved_map = self.repo.set_map_status(map_id, status)
        self.repo.append_realtime_event(
            session_id=session_id,
            campaign_id=campaign_id,
            audience="session",
            event_type=event_type,
            resource_type="map",
            resource_id=map_id,
            payload={"status": status},
        )
        return saved_map

    def place_token(
        self,
        map_id: str,
        campaign_id: str,
        session_id: str,
        command: PlaceTokenCommand,
    ) -> dict:
        token = self.repo.place_map_token(
            map_id=map_id,
            label=command.label,
            location_name=command.location_name,
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            visibility=command.visibility,
            color=command.color,
        )
        audience = "session" if self.repo.is_map_token_player_visible(token["id"]) else "kp"
        self.repo.append_realtime_event(
            session_id=session_id,
            campaign_id=campaign_id,
            audience=audience,
            event_type="map.token_placed",
            resource_type="map_token",
            resource_id=token["id"],
            payload={"map_id": map_id},
        )
        return token

    def move_token(
        self,
        token_id: str,
        campaign_id: str,
        session_id: str,
        command: MoveTokenCommand,
    ) -> dict:
        was_player_visible = self.repo.is_map_token_player_visible(token_id)
        moved_token = self.repo.move_map_token(
            token_id=token_id,
            to_location_name=command.to_location_name,
            moved_by=command.moved_by,
            note=command.note,
            require_route=command.require_route,
            allowed_visibility=command.allowed_visibility,
            expected_version=command.expected_version,
        )
        is_player_visible = self.repo.is_map_token_player_visible(token_id)
        if is_player_visible:
            self._append_token_event(
                token_id,
                campaign_id,
                session_id,
                moved_token["map_id"],
                audience="session",
            )
        elif was_player_visible:
            self.repo.append_realtime_event(
                session_id=session_id,
                campaign_id=campaign_id,
                audience="session",
                event_type="map.changed",
                resource_type="map",
                resource_id=moved_token["map_id"],
                payload={"map_id": moved_token["map_id"]},
            )
            self._append_token_event(
                token_id,
                campaign_id,
                session_id,
                moved_token["map_id"],
                audience="kp",
            )
        else:
            self._append_token_event(
                token_id,
                campaign_id,
                session_id,
                moved_token["map_id"],
                audience="kp",
            )
        return moved_token

    def _append_token_event(
        self,
        token_id: str,
        campaign_id: str,
        session_id: str,
        map_id: str,
        *,
        audience: str,
    ) -> None:
        self.repo.append_realtime_event(
            session_id=session_id,
            campaign_id=campaign_id,
            audience=audience,
            event_type="map.token_moved",
            resource_type="map_token",
            resource_id=token_id,
            payload={"map_id": map_id},
        )

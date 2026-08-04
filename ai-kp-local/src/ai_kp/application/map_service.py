from dataclasses import dataclass

from ai_kp.application.ports.repositories import MapStore
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
    map_kind: str = "regional"
    era_year: int | None = None
    locale: str = ""
    season: str = ""
    time_of_day: str = ""
    weather: str = ""
    public_architecture: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    required_elements: tuple[str, ...] = ()
    forbidden_elements: tuple[str, ...] = ()
    visual_style: str = "period_illustrated_map"


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

    def __init__(self, repo: MapStore):
        self.repo = repo

    def generate_and_save(
        self,
        campaign_id: str,
        session_id: str,
        command: GenerateMapCommand,
    ) -> dict:
        campaign = self.repo.get_campaign(campaign_id)
        generated_map = generate_map(
            title=command.title,
            prompt=command.prompt,
            location_names=list(command.locations) or None,
            routes=list(command.routes) or None,
            style=command.style,
            width=command.width,
            height=command.height,
            map_kind=command.map_kind,
            era_year=command.era_year,
            locale=command.locale,
            season=command.season,
            time_of_day=command.time_of_day,
            weather=command.weather,
            public_architecture=list(command.public_architecture),
            feature_names=list(command.features),
            required_elements=list(command.required_elements),
            forbidden_elements=list(command.forbidden_elements),
            visual_style=command.visual_style,
            campaign_time=campaign.get("current_time"),
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

    def publish(
        self,
        map_id: str,
        campaign_id: str,
        session_id: str,
        *,
        expected_revision_id: str,
        expected_selected_asset_id: str | None = None,
    ) -> dict:
        self.repo.begin_immediate()
        snapshot = self.repo.get_map_publish_snapshot(map_id)
        validation = snapshot.get("validation")
        if not isinstance(validation, dict) or validation.get("valid") is not True:
            raise ValueError("地图规范未通过校验，不能发布")
        if snapshot["current_revision_id"] != expected_revision_id:
            raise ValueError("地图版本已变化，请刷新并重新审核后发布")
        if snapshot["selected_public_asset_id"] != expected_selected_asset_id:
            raise ValueError("正式背景已变化，请刷新并重新审核后发布")
        self.repo.record_published_revision(map_id, expected_revision_id)
        return self._set_status(
            map_id,
            campaign_id,
            session_id,
            status="published",
            event_type="map.published",
        )

    def publish_diff(
        self,
        map_id: str,
        campaign_id: str,
        *,
        expected_revision_id: str,
        expected_selected_asset_id: str | None = None,
    ) -> dict:
        """Read-only preview of what a publish would change for players."""
        self.repo.begin_immediate()
        snapshot = self.repo.get_map_publish_snapshot(map_id)
        validation = snapshot.get("validation")
        if not isinstance(validation, dict) or validation.get("valid") is not True:
            raise ValueError("地图规范未通过校验，不能发布")
        if snapshot["current_revision_id"] != expected_revision_id:
            raise ValueError("地图版本已变化，请刷新并重新审核后发布")
        if snapshot["selected_public_asset_id"] != expected_selected_asset_id:
            raise ValueError("正式背景已变化，请刷新并重新审核后发布")
        current = self.repo.get_current_map_revision(map_id)
        published = self.repo.get_published_revision_spec(map_id)
        if current is None:
            raise ValueError("地图当前没有可审核的版本")
        if published is None:
            return {
                "revision": {
                    "current_id": current["id"],
                    "current_no": int(current["revision_no"]),
                    "published_id": None,
                    "published_no": None,
                },
                "first_publish": True,
                "layout_hash_changed": True,
                "locations": {"added": [], "removed": [], "changed": []},
                "connections": {"added": [], "removed": [], "changed": []},
                "canvas_changed": False,
                "title_changed": False,
                "player_visible_changes": [],
            }
        return _diff_map_specs(
            current_spec=current["spec"],
            published_spec=published["spec"],
            current_no=int(current["revision_no"]),
            published_no=int(published["revision_no"]),
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


_PLAYER_VISIBILITY = frozenset({"player", "table"})


def _diff_map_specs(
    *,
    current_spec: dict,
    published_spec: dict,
    current_no: int,
    published_no: int,
) -> dict:
    """Three-way diff of MapSpec locations and connections by stable element id."""

    current_locations = {str(item["id"]): item for item in current_spec.get("locations", [])}
    published_locations = {
        str(item["id"]): item for item in published_spec.get("locations", [])
    }
    current_connections = {
        str(item["id"]): item for item in current_spec.get("connections", [])
    }
    published_connections = {
        str(item["id"]): item for item in published_spec.get("connections", [])
    }

    location_changes = _diff_elements(
        current_elements=current_locations,
        published_elements=published_locations,
        compare_fields=(
            "name",
            "visibility",
            "position",
            "public_description",
        ),
    )
    connection_changes = _diff_elements(
        current_elements=current_connections,
        published_elements=published_connections,
        compare_fields=(
            "visibility",
            "traversal",
            "direction",
        ),
    )

    current_canvas = current_spec.get("canvas") or {}
    published_canvas = published_spec.get("canvas") or {}
    canvas_changed = (
        current_canvas.get("width") != published_canvas.get("width")
        or current_canvas.get("height") != published_canvas.get("height")
    )
    title_changed = current_spec.get("title") != published_spec.get("title")
    layout_hash_changed = (
        current_spec.get("layout_hash") != published_spec.get("layout_hash")
    )

    player_visible_changes = _collect_player_visible_changes(
        location_changes,
        connection_changes,
        current_locations,
        current_connections,
    )

    return {
        "revision": {
            "current_id": None,
            "current_no": current_no,
            "published_id": None,
            "published_no": published_no,
        },
        "first_publish": False,
        "layout_hash_changed": layout_hash_changed,
        "locations": location_changes,
        "connections": connection_changes,
        "canvas_changed": canvas_changed,
        "title_changed": title_changed,
        "player_visible_changes": player_visible_changes,
    }


def _diff_elements(
    *,
    current_elements: dict,
    published_elements: dict,
    compare_fields: tuple,
) -> dict:
    added: list[dict] = []
    removed: list[dict] = []
    changed: list[dict] = []
    for element_id in sorted(set(current_elements) - set(published_elements)):
        added.append(
            {
                "id": element_id,
                "name": _element_label(current_elements[element_id]),
            }
        )
    for element_id in sorted(set(published_elements) - set(current_elements)):
        removed.append(
            {
                "id": element_id,
                "name": _element_label(published_elements[element_id]),
            }
        )
    for element_id in sorted(set(current_elements) & set(published_elements)):
        current_item = current_elements[element_id]
        published_item = published_elements[element_id]
        field_diffs = []
        for field in compare_fields:
            if _field_value(current_item.get(field)) != _field_value(published_item.get(field)):
                field_diffs.append(field)
        if field_diffs:
            changed.append(
                {
                    "id": element_id,
                    "name": _element_label(current_item),
                    "fields": field_diffs,
                }
            )
    return {"added": added, "removed": removed, "changed": changed}


def _element_label(item: dict) -> str:
    return str(item.get("name") or item.get("from_name") or item.get("id") or "?")


def _field_value(value) -> object:
    return value


def _collect_player_visible_changes(
    location_changes: dict,
    connection_changes: dict,
    current_locations: dict,
    current_connections: dict,
) -> list[dict]:
    changes: list[dict] = []
    for change in location_changes.get("added") + location_changes.get("changed"):
        item = current_locations.get(change["id"], {})
        if item.get("visibility") in _PLAYER_VISIBILITY:
            changes.append(
                {
                    "kind": "location",
                    "change": "added_or_changed",
                    "id": change["id"],
                    "name": change["name"],
                }
            )
    for change in connection_changes.get("added") + connection_changes.get("changed"):
        item = current_connections.get(change["id"], {})
        if item.get("visibility") in _PLAYER_VISIBILITY:
            changes.append(
                {
                    "kind": "connection",
                    "change": "added_or_changed",
                    "id": change["id"],
                    "name": change["name"],
                }
            )
    return changes

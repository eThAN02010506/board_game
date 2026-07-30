"""Event-driven KP-private random resolution after deterministic gates."""

from __future__ import annotations

import hashlib
import json
import secrets
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass

from ai_kp.application.npc_reappearance_service import NpcReappearanceService
from ai_kp.application.ports.private_random_resolutions import (
    PrivateRandomResolutionStore,
)
from ai_kp.application.travel_graph_service import TravelGraphService


@dataclass(frozen=True)
class HiddenAppearanceDestination:
    location_name: str
    weight: int = 1


@dataclass(frozen=True)
class HiddenAppearanceCommand:
    idempotency_key: str
    npc_id: str
    trigger_text: str
    appearance_chance: int
    destinations: tuple[HiddenAppearanceDestination, ...]
    profession_hint: str | None = None


class PrivateRandomResolutionService:
    def __init__(
        self,
        repo: PrivateRandomResolutionStore,
        *,
        randbelow: Callable[[int], int] = secrets.randbelow,
    ):
        self.repo = repo
        self.randbelow = randbelow

    def list_hidden_appearances(self, campaign_id: str, limit: int = 50) -> list[dict]:
        self.repo.get_campaign(campaign_id)
        return self.repo.list_npc_hidden_appearance_resolutions(campaign_id, limit)

    def resolve_hidden_appearance(
        self,
        campaign_id: str,
        command: HiddenAppearanceCommand,
        *,
        created_by_member_id: str,
    ) -> dict:
        normalized = self._normalize_command(command)
        command_hash = self._command_hash(normalized)
        self.repo.begin_immediate()
        self.repo.begin_private_random_resolution()
        try:
            existing = self.repo.find_npc_hidden_appearance_resolution(
                campaign_id, normalized.idempotency_key
            )
            if existing is not None:
                if existing["command_hash"] != command_hash:
                    raise ValueError(
                        "Idempotency key was already used with different hidden-roll input"
                    )
                self.repo.finish_private_random_resolution()
                return existing

            campaign = self.repo.get_campaign(campaign_id)
            if not self.repo.npc_is_linked_to_campaign(
                campaign_id, normalized.npc_id
            ):
                raise ValueError(
                    "Hidden appearance rolls require an NPC already linked to this campaign"
                )
            profile = self.repo.get_npc_availability_profile(normalized.npc_id)
            if profile is None or not profile.get("location_tags"):
                raise ValueError(
                    "Configure the NPC lifecycle and at least one location tag first"
                )
            policy = self.repo.get_campaign_npc_reappearance_policy(campaign_id)
            base_policy = {**policy, "require_location_match": False}
            gate = NpcReappearanceService.evaluate(
                campaign_time=campaign.get("current_time"),
                policy=base_policy,
                profile=profile,
                context_location=None,
                profession_hint=normalized.profession_hint,
                final=True,
            )
            if gate["decision"] != "eligible":
                explanation = "; ".join(gate["warnings"]) or "NPC is not eligible"
                raise ValueError(f"Deterministic NPC gate rejected the roll: {explanation}")

            route_results = TravelGraphService(self.repo).resolve_destinations(
                campaign_id,
                origins=tuple(profile["location_tags"]),
                destinations=tuple(
                    item.location_name for item in normalized.destinations
                ),
                max_minutes=int(policy["max_travel_minutes"]),
            )
            eligible_locations = []
            for candidate, route in zip(
                normalized.destinations, route_results, strict=True
            ):
                if route["status"] not in {"same_location", "reachable"}:
                    continue
                location = route["locations"][-1]
                eligible_locations.append(
                    {
                        "location_id": location["id"],
                        "location_name": location["name"],
                        "requested_name": candidate.location_name,
                        "weight": candidate.weight,
                        "travel_minutes": int(route["total_minutes"] or 0),
                    }
                )
            if not eligible_locations:
                raise ValueError(
                    "No proposed location passed deterministic travel reachability"
                )

            appearance_roll = self.randbelow(100) + 1
            appears = appearance_roll <= normalized.appearance_chance
            selected = None
            location_roll = None
            if appears:
                total_weight = sum(item["weight"] for item in eligible_locations)
                if len(eligible_locations) == 1:
                    selected = eligible_locations[0]
                else:
                    location_roll = self.randbelow(total_weight) + 1
                    cursor = 0
                    for item in eligible_locations:
                        cursor += item["weight"]
                        if location_roll <= cursor:
                            selected = item
                            break
            saved = self.repo.create_npc_hidden_appearance_resolution(
                campaign_id=campaign_id,
                npc_id=normalized.npc_id,
                idempotency_key=normalized.idempotency_key,
                command_hash=command_hash,
                trigger_text=normalized.trigger_text,
                appearance_chance=normalized.appearance_chance,
                appearance_roll=appearance_roll,
                appears=appears,
                eligible_locations=eligible_locations,
                selected_location_id=selected["location_id"] if selected else None,
                selected_location_name=selected["location_name"] if selected else None,
                location_roll=location_roll,
                created_by_member_id=created_by_member_id,
            )
            self.repo.finish_private_random_resolution()
            return saved
        except Exception:
            self.repo.rollback_private_random_resolution()
            raise

    @classmethod
    def _normalize_command(
        cls, command: HiddenAppearanceCommand
    ) -> HiddenAppearanceCommand:
        key = cls._text(command.idempotency_key, "idempotency key", 200)
        if len(key) < 8:
            raise ValueError("Idempotency key must contain at least 8 characters")
        if not 0 <= command.appearance_chance <= 100:
            raise ValueError("Appearance chance must be between 0 and 100")
        if not 1 <= len(command.destinations) <= 20:
            raise ValueError("Provide between 1 and 20 destination candidates")
        destinations = tuple(
            HiddenAppearanceDestination(
                cls._text(item.location_name, "location name", 200),
                item.weight,
            )
            for item in command.destinations
        )
        if any(not 1 <= item.weight <= 1000 for item in destinations):
            raise ValueError("Destination weights must be between 1 and 1000")
        names = [TravelGraphService.normalize(item.location_name) for item in destinations]
        if len(set(names)) != len(names):
            raise ValueError("Destination candidates must be unique")
        return HiddenAppearanceCommand(
            idempotency_key=key,
            npc_id=cls._text(command.npc_id, "NPC id", 100),
            trigger_text=cls._text(command.trigger_text, "trigger text", 500),
            appearance_chance=command.appearance_chance,
            destinations=destinations,
            profession_hint=(
                cls._text(command.profession_hint, "profession hint", 200)
                if command.profession_hint
                else None
            ),
        )

    @staticmethod
    def _command_hash(command: HiddenAppearanceCommand) -> str:
        payload = asdict(command)
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _text(value: str, field: str, maximum: int) -> str:
        normalized = " ".join(unicodedata.normalize("NFKC", value).split())
        if not normalized:
            raise ValueError(f"{field} cannot be blank")
        if len(normalized) > maximum:
            raise ValueError(f"{field} cannot exceed {maximum} characters")
        return normalized


__all__ = [
    "HiddenAppearanceCommand",
    "HiddenAppearanceDestination",
    "PrivateRandomResolutionService",
]

"""Read-only module-location coverage against a generic setting template."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ai_kp.application.errors import InvalidInputError
from ai_kp.platform.scenes.builtin_setting_packs import get_setting_pack
from ai_kp.platform.scenes.setting_profiles import (
    ConfiguredRegion,
    SettingProfileDocument,
    build_setting_profile_document,
    validate_setting_profile,
)
from ai_kp.platform.scenes.settlement_template_matching import (
    LocationMention,
    associate_location_mentions,
)


class ModuleSettingAnalysisStore(Protocol):
    def list_module_entities(self, module_id: str) -> list[dict]: ...

    def get_module_knowledge_candidate(self, candidate_id: str) -> dict: ...

    def create_module_setting_profile(self, **values: object) -> dict: ...

    def update_module_setting_profile(self, profile_id: str, **values: object) -> dict: ...

    def get_module_setting_profile(
        self, profile_id: str, *, version: int | None = None
    ) -> dict: ...

    def list_module_setting_profiles(self, module_id: str) -> list[dict]: ...

    def set_module_run_setting_selection(self, run_id: str, **values: object) -> dict: ...

    def get_campaign_module_run(self, run_id: str) -> dict: ...


@dataclass(frozen=True)
class CreateSettingProfileCommand:
    module_id: str
    title: str
    setting_pack_id: str
    regions: tuple[ConfiguredRegion, ...]


@dataclass(frozen=True)
class UpdateSettingProfileCommand:
    profile_id: str
    expected_version: int
    title: str
    document: SettingProfileDocument


@dataclass(frozen=True)
class SelectRunSettingCommand:
    run_id: str
    expected_run_version: int
    profile_id: str
    profile_version: int
    settlement_id: str
    reason: str


class ModuleSettingAnalysisService:
    """Bind confirmed source entities; never materialize template suggestions."""

    def __init__(self, store: ModuleSettingAnalysisStore):
        self.store = store

    def create_profile(
        self,
        command: CreateSettingProfileCommand,
        *,
        member_id: str | None,
    ) -> dict:
        pack = self._pack(command.setting_pack_id)
        document = build_setting_profile_document(pack, command.regions)
        validated = validate_setting_profile(
            pack,
            document,
            self.store.list_module_entities(command.module_id),
        )
        return self.store.create_module_setting_profile(
            module_id=command.module_id,
            title=command.title,
            setting_pack_id=pack.setting_pack_id,
            setting_pack_version=pack.pack_version,
            document=validated.document.model_dump(mode="json"),
            member_id=member_id,
        )

    def update_profile(
        self,
        command: UpdateSettingProfileCommand,
        *,
        member_id: str | None,
    ) -> dict:
        current = self.store.get_module_setting_profile(command.profile_id)
        pack = self._pack(str(current["setting_pack_id"]))
        validated = validate_setting_profile(
            pack,
            command.document,
            self.store.list_module_entities(str(current["module_id"])),
        )
        return self.store.update_module_setting_profile(
            command.profile_id,
            expected_version=command.expected_version,
            title=command.title,
            setting_pack_version=pack.pack_version,
            document=validated.document.model_dump(mode="json"),
            member_id=member_id,
        )

    def select_for_run(
        self,
        command: SelectRunSettingCommand,
        *,
        member_id: str | None,
    ) -> dict:
        run = self.store.get_campaign_module_run(command.run_id)
        profile = self.store.get_module_setting_profile(
            command.profile_id,
            version=command.profile_version,
        )
        if profile["module_id"] != run["module_id"]:
            raise InvalidInputError("Setting profile belongs to another module")
        pack = self._pack(str(profile["setting_pack_id"]))
        document = SettingProfileDocument.model_validate(profile["document"])
        validate_setting_profile(
            pack,
            document,
            self.store.list_module_entities(str(run["module_id"])),
        )
        try:
            document.settlement(command.settlement_id)
        except KeyError as exc:
            raise InvalidInputError(str(exc)) from exc
        return self.store.set_module_run_setting_selection(
            command.run_id,
            expected_run_version=command.expected_run_version,
            profile_id=command.profile_id,
            profile_version=command.profile_version,
            settlement_id=command.settlement_id,
            reason=command.reason,
            member_id=member_id,
        )

    def analyze_profile_settlement(
        self,
        *,
        profile_id: str,
        profile_version: int | None,
        settlement_id: str,
    ) -> dict:
        profile = self.store.get_module_setting_profile(
            profile_id,
            version=profile_version,
        )
        pack = self._pack(str(profile["setting_pack_id"]))
        document = SettingProfileDocument.model_validate(profile["document"])
        validated = validate_setting_profile(
            pack,
            document,
            self.store.list_module_entities(str(profile["module_id"])),
        )
        configured = document.settlement(settlement_id)
        skeleton = next(
            item
            for item in validated.settlement_skeletons
            if item.settlement_id == settlement_id
        )
        entities = {
            str(item["id"]): item
            for item in self.store.list_module_entities(str(profile["module_id"]))
        }
        archetypes = {item.archetype_id: item for item in pack.entity_archetypes}
        source_entity_bindings = [
            {
                **binding.model_dump(mode="json"),
                "entity": {
                    "entity_id": binding.module_entity_id,
                    "entity_type": entities[binding.module_entity_id]["entity_type"],
                    "name": entities[binding.module_entity_id]["name"],
                    "description": entities[binding.module_entity_id].get("description") or "",
                },
                "archetype": archetypes[binding.archetype_id].model_dump(mode="json"),
            }
            for binding in document.entity_bindings
            if binding.settlement_id in {None, settlement_id}
        ]
        mentions = tuple(
            self._mention(
                entities[binding.module_entity_id],
                explicit_slot_id=binding.slot_id,
            )
            for binding in configured.location_bindings
        )
        return {
            "module_id": profile["module_id"],
            "profile_id": profile["id"],
            "profile_version": profile["version"],
            "profile_content_hash": profile["content_hash"],
            "setting_pack": {
                "setting_pack_id": pack.setting_pack_id,
                "schema_version": pack.schema_version,
                "pack_version": pack.pack_version,
                "title": pack.title,
                "content_scope": pack.content_scope,
                "provenance": list(pack.provenance),
            },
            "configured_settlement": configured.model_dump(mode="json"),
            "settlement_template": skeleton.model_dump(mode="json"),
            "source_coverage": associate_location_mentions(
                skeleton, mentions
            ).model_dump(mode="json"),
            "unassigned_location_entity_ids": list(
                validated.unassigned_location_entity_ids
            ),
            "source_entity_bindings": source_entity_bindings,
            "unassigned_entity_ids": list(validated.unassigned_entity_ids),
            "write_policy": "read_only_kp_review_required",
        }

    def _mention(
        self,
        entity: dict,
        *,
        explicit_slot_id: str | None = None,
    ) -> LocationMention:
        source_ids: list[str] = []
        candidate_id = str(entity.get("source_candidate_id") or "")
        if candidate_id:
            try:
                candidate = self.store.get_module_knowledge_candidate(candidate_id)
            except KeyError:
                candidate = {}
            for citation in candidate.get("citations") or ():
                source_id = citation.get("chunk_id") or citation.get("asset_id")
                if source_id:
                    source_ids.append(str(source_id))
        return LocationMention(
            mention_id=str(entity["id"]),
            title=str(entity["name"]),
            context=str(entity.get("description") or ""),
            source_block_ids=tuple(dict.fromkeys(source_ids)),
            explicit_slot_id=explicit_slot_id,
        )

    @staticmethod
    def _pack(setting_pack_id: str):
        try:
            return get_setting_pack(setting_pack_id)
        except KeyError as exc:
            raise InvalidInputError(str(exc)) from exc


__all__ = [
    "CreateSettingProfileCommand",
    "ModuleSettingAnalysisService",
    "SelectRunSettingCommand",
    "UpdateSettingProfileCommand",
]

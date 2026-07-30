"""Use cases for selecting the authoritative module and spoiler scope of a campaign."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NoReturn

from ai_kp.application.errors import ConflictError, InvalidInputError
from ai_kp.application.ports.repositories import ModuleRunStore


@dataclass(frozen=True)
class StartModuleRunCommand:
    module_id: str
    current_scene_key: str | None = None
    active_spoiler_tags: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SceneTransitionCommand:
    expected_version: int
    scene_key: str
    scene_title: str
    play_pace: str
    location_entity_id: str | None = None
    world_time: str | None = None
    note: str = ""


@dataclass(frozen=True)
class EntityStateCommand:
    expected_version: int
    status: str
    note: str = ""


@dataclass(frozen=True)
class DirectorControlCommand:
    expected_version: int
    mode: str
    reason: str


class ModuleRunService:
    def __init__(self, repo: ModuleRunStore):
        self.repo = repo

    def start(
        self,
        campaign_id: str,
        command: StartModuleRunCommand,
        *,
        member_id: str,
    ) -> dict:
        try:
            return self.repo.start_campaign_module_run(
                campaign_id=campaign_id,
                module_id=command.module_id,
                current_scene_key=command.current_scene_key,
                active_spoiler_tags=command.active_spoiler_tags,
                state=command.state,
                started_by_member_id=member_id,
            )
        except (TypeError, ValueError) as exc:
            raise InvalidInputError(str(exc)) from exc

    def update(self, run_id: str, changes: dict[str, Any]) -> dict:
        try:
            return self.repo.update_campaign_module_run(run_id, changes)
        except ValueError as exc:
            if str(exc).startswith("Module run changed;"):
                raise ConflictError(str(exc)) from exc
            raise InvalidInputError(str(exc)) from exc
        except TypeError as exc:
            raise InvalidInputError(str(exc)) from exc

    def transition_scene(
        self,
        run_id: str,
        command: SceneTransitionCommand,
        *,
        member_id: str,
    ) -> dict:
        try:
            return self.repo.transition_module_run_scene(
                run_id,
                expected_version=command.expected_version,
                scene_key=command.scene_key,
                scene_title=command.scene_title,
                play_pace=command.play_pace,
                location_entity_id=command.location_entity_id,
                world_time=command.world_time,
                note=command.note,
                member_id=member_id,
            )
        except ValueError as exc:
            self._raise_input_or_conflict(exc)
        except TypeError as exc:
            raise InvalidInputError(str(exc)) from exc

    def set_entity_state(
        self,
        run_id: str,
        entity_id: str,
        command: EntityStateCommand,
        *,
        member_id: str,
    ) -> dict:
        try:
            return self.repo.set_module_run_entity_state(
                run_id,
                entity_id,
                expected_version=command.expected_version,
                status=command.status,
                note=command.note,
                member_id=member_id,
            )
        except ValueError as exc:
            self._raise_input_or_conflict(exc)
        except TypeError as exc:
            raise InvalidInputError(str(exc)) from exc

    def analyze(self, run_id: str, player_intent: str) -> dict:
        intent = str(player_intent or "").strip()
        if not intent:
            raise InvalidInputError("Player intent is required")
        if len(intent) > 1000:
            raise InvalidInputError("Player intent is too long")
        run = self.repo.get_campaign_module_run(run_id)
        if run.get("director_control_mode") != "ai_assist":
            raise ConflictError(
                "AI director is paused or under human KP control; return control first"
            )
        entity_states = self.repo.list_module_run_entity_states(run_id)
        active_entry_ids = tuple(
            str(item["entity_id"])
            for item in entity_states
            if item["status"] in {"available", "discovered", "resolved"}
        )
        reachability = (
            self.repo.module_graph_reachability(
                str(run["module_id"]),
                active_entry_ids,
            )
            if active_entry_ids
            else {
                "module_id": run["module_id"],
                "entry_entity_ids": [],
                "reached_entity_ids": [],
                "anchors": [
                    {**item, "reachable": False}
                    for item in entity_states
                    if item["entity_type"] == "anchor"
                ],
                "all_anchors_reachable": False,
                "has_conflicts": False,
                "safe": False,
                "conflicts": [],
                "evaluated": False,
            }
        )
        if active_entry_ids:
            reachability["evaluated"] = True
        active_tags = tuple(str(tag) for tag in run["active_spoiler_tags"])
        scoped_sources = self.repo.search_module(
            str(run["module_id"]),
            intent,
            allowed_visibility=("player", "table", "kp", "secret"),
            spoiler_tags=active_tags,
            limit=8,
        )
        deferred_source_count = 0
        if not scoped_sources:
            all_sources = self.repo.search_module(
                str(run["module_id"]),
                intent,
                allowed_visibility=("player", "table", "kp", "secret"),
                spoiler_tags=None,
                limit=8,
            )
            deferred_source_count = len(all_sources)
        reasons: list[str] = []
        if not run.get("current_scene_key"):
            decision = "needs_scene"
            recommended_action = "pause_for_human_kp"
            reasons.append("当前活动模组尚未设置场景，AI 不应脱离场景直接推进。")
        elif scoped_sources:
            decision = "answer_from_canon"
            recommended_action = "narrate_existing_world"
            reasons.append("当前剧透范围内存在与玩家意图相关的模组来源。")
        elif deferred_source_count:
            decision = "blocked_by_spoiler"
            recommended_action = "pause_for_human_kp"
            reasons.append("相关来源只存在于尚未解锁的剧透范围，不能提前用于当前回合。")
        else:
            decision = "world_gap"
            recommended_action = "propose_world_expansion"
            reasons.append("当前允许的模组来源没有直接回答该意图，可进入受约束世界补全。")
        if reachability.get("has_conflicts"):
            reasons.append("当前可达图存在显式 blocks/contradicts 关系，需要 KP 处理。")
        unreachable_anchors = [
            item for item in reachability.get("anchors", []) if not item["reachable"]
        ]
        return {
            "run_id": run_id,
            "module_id": run["module_id"],
            "module_title": run["module_title"],
            "player_intent": intent,
            "scene": {
                "key": run.get("current_scene_key"),
                "title": run.get("current_scene_title"),
                "play_pace": run.get("play_pace") or "freeform",
                "location_entity_id": run.get("current_location_entity_id"),
                "started_world_time": run.get("scene_started_world_time"),
            },
            "decision": decision,
            "recommended_action": recommended_action,
            "reasons": reasons,
            "sources": scoped_sources,
            "deferred_source_count": deferred_source_count,
            "entity_states": entity_states,
            "reachability": reachability,
            "unreachable_anchor_count": len(unreachable_anchors),
            "writes_performed": False,
        }

    def set_control(
        self,
        run_id: str,
        command: DirectorControlCommand,
        *,
        member_id: str,
    ) -> dict:
        try:
            return self.repo.set_module_run_control(
                run_id,
                expected_version=command.expected_version,
                mode=command.mode,
                reason=command.reason,
                member_id=member_id,
            )
        except ValueError as exc:
            self._raise_input_or_conflict(exc)

    @staticmethod
    def _raise_input_or_conflict(exc: ValueError) -> NoReturn:
        if str(exc).startswith("Module run changed;"):
            raise ConflictError(str(exc)) from exc
        raise InvalidInputError(str(exc)) from exc

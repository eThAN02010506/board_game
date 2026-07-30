"""Replay deterministic scenarios through real application services in a sandbox."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from ai_kp.application.gameplay_service import (
    EncounterCreateCommand,
    GameplayCommand,
    GameplayService,
)
from ai_kp.application.investigator_service import (
    CreateInvestigatorCommand,
    InvestigatorService,
)
from ai_kp.application.map_route_plan_service import (
    CreateRoutePlanCommand,
    MapRoutePlanService,
    TokenRoute,
)
from ai_kp.application.map_service import (
    GenerateMapCommand,
    MapService,
    MoveTokenCommand,
    PlaceTokenCommand,
)
from ai_kp.application.session_service import SessionService
from ai_kp.application.turn_service import ManualProposalCommand, TurnService
from ai_kp.evaluation.simulated_campaign import run_simulation
from ai_kp.observability import operational_telemetry

SERVICE_RUNNER_VERSION = "product-service-replay.v3"


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(encoded.encode()).hexdigest()


class SimulationReplayService:
    """Run a case without leaving sandbox world state in the live campaign."""

    def __init__(self, repo: Any):
        self.repo = repo

    def run_case(self, case_id: str) -> dict:
        case = self.repo.get_simulation_case(case_id)
        definition = case["definition"]
        if definition.get("mode") != "service_replay":
            return self.repo.record_simulation_run(
                case_id,
                run_simulation(definition),
            )
        result = self._run_service_replay(case)
        return self.repo.record_simulation_run(case_id, result)

    def _run_service_replay(self, case: dict) -> dict[str, Any]:
        steps = case["definition"].get("steps")
        if not isinstance(steps, list) or not 1 <= len(steps) <= 200:
            raise ValueError("Service replay requires 1-200 steps")
        aliases: dict[str, dict[str, Any]] = {}
        trajectory: list[dict[str, Any]] = []
        metrics = {
            "steps": len(steps),
            "service_calls": 0,
            "assertions": 0,
            "passed_assertions": 0,
            "failed_assertions": 0,
        }
        source_campaign = self.repo.get_campaign(str(case["campaign_id"]))
        self.repo.begin_simulation_sandbox()
        try:
            campaign = self.repo.create_campaign(
                f"模拟回放：{case['name']}",
                system=str(source_campaign["system"]),
                current_time=source_campaign.get("current_time"),
            )
            session = SessionService(self.repo).create(
                str(campaign["id"]),
                title="isolated service replay",
                kp_display_name="Simulation KP",
            )
            identity = self.repo.authenticate_access_token(session["access_token"])
            if identity is None:
                raise RuntimeError("Simulation KP identity could not be created")
            for index, raw in enumerate(steps):
                if not isinstance(raw, dict):
                    raise TypeError(f"Step {index + 1} must be an object")
                record = self._execute_step(
                    raw,
                    campaign_id=str(campaign["id"]),
                    identity=identity,
                    aliases=aliases,
                    metrics=metrics,
                )
                trajectory.append({"index": index, **record})
            status = "passed" if metrics["failed_assertions"] == 0 else "failed"
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            status = "error"
            trajectory.append(
                {
                    "index": len(trajectory),
                    "action": "runner_error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
        finally:
            self.repo.rollback_simulation_sandbox()
        result = {
            "runner_version": SERVICE_RUNNER_VERSION,
            "status": status,
            "metrics": metrics,
            "trajectory": trajectory,
        }
        result["result_fingerprint"] = _hash(result)
        return result

    def _execute_step(
        self,
        raw: dict[str, Any],
        *,
        campaign_id: str,
        identity: Any,
        aliases: dict[str, dict[str, Any]],
        metrics: dict[str, int],
    ) -> dict[str, Any]:
        action = str(raw.get("action") or "")
        if action.startswith("assert_"):
            return self._assert_step(action, raw, aliases, metrics)
        handlers = {
            "create_investigator": self._create_investigator,
            "character_command": self._character_command,
            "generate_map": self._generate_map,
            "place_token": self._place_token,
            "plan_routes": self._plan_routes,
            "move_token": self._move_token,
            "create_proposal": self._create_proposal,
            "approve_proposal": self._approve_proposal,
            "create_handout": self._create_handout,
            "update_handout": self._update_handout,
            "create_encounter": self._create_encounter,
            "encounter_command": self._encounter_command,
            "capture_player_map": self._capture_player_map,
            "capture_player_handouts": self._capture_player_handouts,
        }
        handler = handlers.get(action)
        if handler is None:
            raise ValueError(f"Unsupported service replay action: {action}")
        result = handler(
            raw,
            campaign_id=campaign_id,
            identity=identity,
            aliases=aliases,
        )
        metrics["service_calls"] += 1
        alias = str(raw.get("alias") or "").strip()
        if alias:
            if alias in aliases:
                raise ValueError(f"Duplicate simulation alias: {alias}")
            aliases[alias] = result
        record = {
            "action": action,
            "alias": alias or None,
            "result_type": str(result.get("_type") or "resource"),
        }
        log = self._step_log(action, raw, result)
        if log:
            record["log"] = log
        return record

    def _create_investigator(
        self,
        raw: dict,
        *,
        campaign_id: str,
        identity: Any,
        **_: Any,
    ) -> dict:
        service = InvestigatorService(self.repo)
        session_service = SessionService(self.repo)
        display_name = str(raw.get("player_name") or "Simulation Player")
        seat = session_service.create_seat(
            identity.session_id,
            label=f"{display_name} seat",
            kp_member_id=identity.member_id,
        )
        claimed = session_service.claim_seat(
            str(seat["invitation_code"]),
            display_name=display_name,
            player=None,
        )
        profile_id = str(claimed["profile"]["id"])
        member_id = str(claimed["member"]["id"])
        investigator = service.create_investigator(
            profile_id,
            CreateInvestigatorCommand(canonical_sheet=dict(raw["sheet"])),
        )
        submitted = service.submit_to_campaign(
            campaign_id=campaign_id,
            investigator_id=str(investigator["id"]),
            revision_id=str(investigator["current_revision_id"]),
            owner_profile_id=profile_id,
            member_id=member_id,
            session_id=identity.session_id,
        )
        approved = service.review(
            campaign_id=campaign_id,
            investigator_id=str(investigator["id"]),
            action="approved",
            comment="simulation replay approval",
            kp_member_id=identity.member_id,
            session_id=identity.session_id,
        )
        service.assign(
            session_id=identity.session_id,
            member_id=member_id,
            investigator_id=str(investigator["id"]),
        )
        return {
            "_type": "investigator",
            "id": investigator["id"],
            "name": investigator["name"],
            "member_id": member_id,
            "submitted_status": submitted["status"],
            "status": approved["status"],
            "state": approved["campaign_state"],
        }

    def _character_command(
        self,
        raw: dict,
        *,
        campaign_id: str,
        identity: Any,
        aliases: dict,
        **_: Any,
    ) -> dict:
        investigator = self._alias(
            aliases,
            raw,
            "investigator_ref",
            "investigator",
        )
        result = GameplayService(self.repo).command_character(
            campaign_id,
            str(investigator["id"]),
            identity,
            GameplayCommand(
                command_id=str(raw.get("command_id") or _hash(raw)[:24]),
                expected_version=int(investigator["state"]["state_version"]),
                command_type=str(raw["command_type"]),
                payload=dict(raw.get("payload") or {}),
                visibility=str(raw.get("visibility") or "table"),
            ),
        )
        investigator["state"] = result["state"]
        return {
            "_type": "character_transition",
            "investigator_id": investigator["id"],
            "state": result["state"],
            "event": result["event"],
        }

    def _generate_map(self, raw: dict, *, campaign_id: str, identity: Any, **_: Any) -> dict:
        result = MapService(self.repo).generate_and_save(
            campaign_id,
            identity.session_id,
            GenerateMapCommand(
                title=str(raw.get("title") or "模拟地图"),
                prompt=str(raw.get("prompt") or "deterministic service replay"),
                locations=tuple(str(item) for item in raw.get("locations") or ()),
                routes=tuple(
                    (str(item[0]), str(item[1]))
                    for item in raw.get("routes") or ()
                ),
            ),
        )
        return {"_type": "map", **result}

    def _place_token(self, raw: dict, *, campaign_id: str, identity: Any, aliases: dict, **_: Any) -> dict:
        saved_map = self._alias(aliases, raw, "map_ref", "map")
        result = MapService(self.repo).place_token(
            str(saved_map["id"]),
            campaign_id,
            identity.session_id,
            PlaceTokenCommand(
                label=str(raw.get("label") or "调查员"),
                location_name=str(raw["location"]),
                actor_type="marker",
            ),
        )
        return {"_type": "token", **result}

    def _plan_routes(self, raw: dict, *, campaign_id: str, identity: Any, aliases: dict, **_: Any) -> dict:
        saved_map = self._alias(aliases, raw, "map_ref", "map")
        routes = tuple(
            TokenRoute(
                token_id=str(self._named_alias(aliases, str(item["token_ref"]), "token")["id"]),
                waypoints=tuple(str(point) for point in item["waypoints"]),
            )
            for item in raw.get("token_routes") or ()
        )
        result = MapRoutePlanService(self.repo).create(
            campaign_id=campaign_id,
            map_id=str(saved_map["id"]),
            member_id=identity.member_id,
            command=CreateRoutePlanCommand(
                title=str(raw.get("title") or "模拟路线"),
                note=str(raw.get("note") or ""),
                token_routes=routes,
                player_submission=False,
            ),
        )
        return {"_type": "route_plan", **result}

    def _move_token(self, raw: dict, *, campaign_id: str, identity: Any, aliases: dict, **_: Any) -> dict:
        token = self._alias(aliases, raw, "token_ref", "token")
        result = MapService(self.repo).move_token(
            str(token["id"]),
            campaign_id,
            identity.session_id,
            MoveTokenCommand(
                to_location_name=str(raw["to"]),
                moved_by="simulation:kp",
                expected_version=int(token["version"]),
            ),
        )
        token.clear()
        token.update({"_type": "token", **result})
        return token

    def _create_proposal(self, raw: dict, *, campaign_id: str, identity: Any, **_: Any) -> dict:
        result = TurnService(self.repo).create_manual_proposal(
            campaign_id,
            identity,
            ManualProposalCommand(
                player_action=str(raw.get("player_action") or "模拟行动"),
                public_narration=str(raw.get("public_narration") or "模拟反馈"),
                kp_notes=str(raw.get("kp_notes") or ""),
                proposed_facts=tuple(raw.get("proposed_facts") or ()),
                source_model="simulation-service-replay",
            ),
        )
        return {"_type": "proposal", **result}

    def _approve_proposal(self, raw: dict, *, campaign_id: str, identity: Any, aliases: dict, **_: Any) -> dict:
        proposal = self._alias(aliases, raw, "proposal_ref", "proposal")
        result = TurnService(self.repo).approve(
            str(proposal["id"]),
            campaign_id,
            identity,
            note="service replay approval",
        )
        proposal.clear()
        proposal.update({"_type": "proposal", **result})
        return proposal

    def _create_handout(self, raw: dict, *, campaign_id: str, identity: Any, aliases: dict, **_: Any) -> dict:
        link_type = raw.get("link_type")
        link_id = None
        if raw.get("link_ref"):
            linked = self._named_alias(aliases, str(raw["link_ref"]), str(link_type))
            link_id = str(linked["id"])
        result = self.repo.create_handout(
            campaign_id=campaign_id,
            member_id=identity.member_id,
            title=str(raw.get("title") or "模拟线索"),
            body=str(raw.get("body") or "模拟线索内容"),
            kind=str(raw.get("kind") or "clue"),
            link_type=link_type,
            link_id=link_id,
        )
        return {"_type": "handout", **result}

    def _update_handout(self, raw: dict, *, identity: Any, aliases: dict, **_: Any) -> dict:
        handout = self._alias(aliases, raw, "handout_ref", "handout")
        result = self.repo.update_handout(
            str(handout["id"]),
            expected_version=int(handout["version"]),
            member_id=identity.member_id,
            status=str(raw.get("status") or handout["status"]),
            pinned=bool(raw.get("pinned", handout["pinned"])),
            link_type=handout["link_type"],
            link_id=handout["link_id"],
        )
        handout.clear()
        handout.update({"_type": "handout", **result})
        return handout

    def _create_encounter(self, raw: dict, *, campaign_id: str, identity: Any, **_: Any) -> dict:
        result = GameplayService(self.repo).create_encounter(
            campaign_id,
            identity,
            EncounterCreateCommand(
                kind=str(raw.get("kind") or "combat"),
                title=str(raw.get("title") or "模拟遭遇"),
                participants=tuple(raw.get("participants") or ()),
                locations=tuple(raw.get("locations") or ()),
            ),
        )
        return {"_type": "encounter", **result}

    def _encounter_command(self, raw: dict, *, identity: Any, aliases: dict, **_: Any) -> dict:
        encounter = self._alias(aliases, raw, "encounter_ref", "encounter")
        result = GameplayService(self.repo).command_encounter(
            str(encounter["id"]),
            identity,
            GameplayCommand(
                command_id=str(raw.get("command_id") or _hash(raw)[:24]),
                expected_version=int(encounter["version"]),
                command_type=str(raw["command_type"]),
                payload=dict(raw.get("payload") or {}),
            ),
        )
        encounter.clear()
        encounter.update({"_type": "encounter", **result})
        return encounter

    def _capture_player_map(self, raw: dict, *, aliases: dict, **_: Any) -> dict:
        saved_map = self._alias(aliases, raw, "map_ref", "map")
        result = self.repo.get_map(
            str(saved_map["id"]),
            allowed_visibility=("player", "table"),
        )
        return {"_type": "player_view", "value": result}

    def _capture_player_handouts(
        self,
        raw: dict,
        *,
        campaign_id: str,
        **_: Any,
    ) -> dict:
        del raw
        rows = self.repo.list_handouts(campaign_id, include_drafts=False)
        projected = [
            {
                key: value
                for key, value in row.items()
                if key not in {
                    "created_by_member_id",
                    "revealed_by_member_id",
                    "read_receipts",
                }
            }
            for row in rows
        ]
        return {"_type": "player_view", "value": projected}

    @staticmethod
    def _step_log(
        action: str,
        raw: dict[str, Any],
        result: dict[str, Any],
    ) -> list[dict[str, str]]:
        if action == "create_proposal":
            entries = [
                {
                    "actor": "player",
                    "audience": "table",
                    "content": str(result["player_action"]),
                },
                {
                    "actor": "kp",
                    "audience": "table",
                    "content": str(result["public_narration"]),
                },
            ]
            notes = str(result.get("kp_notes") or "").strip()
            if notes:
                entries.append(
                    {
                        "actor": "model",
                        "audience": "kp",
                        "content": notes,
                    }
                )
            return entries
        if action == "move_token":
            return [
                {
                    "actor": "system",
                    "audience": "table",
                    "content": f"棋子移动至 {result['location_name']}",
                }
            ]
        if action == "character_command":
            event = result["event"]
            return [
                {
                    "actor": "rules_engine",
                    "audience": str(event["visibility"]),
                    "content": (
                        f"{event['event_type']} 已结算；"
                        f"角色状态版本 {result['state']['state_version']}"
                    ),
                }
            ]
        return []

    def _assert_step(
        self,
        action: str,
        raw: dict[str, Any],
        aliases: dict[str, dict[str, Any]],
        metrics: dict[str, int],
    ) -> dict[str, Any]:
        value: Any = self._named_alias(aliases, str(raw["ref"]), None)
        for segment in str(raw.get("path") or "").split("."):
            if segment:
                value = value[int(segment)] if isinstance(value, list) else value[segment]
        expected = raw.get("value")
        if action == "assert_equals":
            passed = value == expected
        else:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
            contains = str(expected) in text
            passed = contains if action == "assert_contains" else not contains
            if action == "assert_not_contains" and not passed:
                operational_telemetry.record_secret_leak(
                    "product_service_replay",
                    {"ref": str(raw["ref"]), "text_hash": _hash(expected)},
                )
        metrics["assertions"] += 1
        metrics["passed_assertions"] += int(passed)
        metrics["failed_assertions"] += int(not passed)
        return {
            "action": action,
            "ref": str(raw["ref"]),
            "path": str(raw.get("path") or ""),
            "passed": passed,
            "expected_hash": _hash(expected),
        }

    @staticmethod
    def _alias(
        aliases: dict[str, dict[str, Any]],
        raw: dict[str, Any],
        field: str,
        expected_type: str,
    ) -> dict[str, Any]:
        return SimulationReplayService._named_alias(
            aliases,
            str(raw.get(field) or ""),
            expected_type,
        )

    @staticmethod
    def _named_alias(
        aliases: dict[str, dict[str, Any]],
        name: str,
        expected_type: str | None,
    ) -> dict[str, Any]:
        value = aliases.get(name)
        if value is None:
            raise ValueError(f"Unknown simulation alias: {name}")
        if expected_type is not None and value.get("_type") != expected_type:
            raise ValueError(
                f"Simulation alias {name} is not a {expected_type}"
            )
        return value


__all__ = ["SERVICE_RUNNER_VERSION", "SimulationReplayService"]

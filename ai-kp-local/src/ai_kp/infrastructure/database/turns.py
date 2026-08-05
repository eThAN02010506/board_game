"""SQLite adapter for player actions, KP proposals, approval, and audit."""

import json
import sqlite3

from ai_kp.core.ids import new_id
from ai_kp.director.turn_output import (
    ActionRuling,
    CheckCandidate,
    EventCandidate,
    FactCandidate,
    MapMoveCandidate,
    MemoryCandidate,
    NpcUpdateCandidate,
    dump_candidates,
)
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.resolution import build_check_consequence_snapshot
from ai_kp.platform.resolution.proposals import (
    validate_proposal_resolution_boundary,
)

PROPOSAL_JSON_FIELDS = (
    "proposed_checks",
    "proposed_events",
    "proposed_memories",
    "proposed_npc_updates",
    "proposed_map_moves",
    "proposed_facts",
)
CHECK_CONSEQUENCE_ACTION_TYPE = "check_consequence_basis"
WORLD_EXPANSION_ACTION_TYPE = "world_expansion_basis"
WORLD_EXPANSION_MATERIALIZED_ACTION_TYPE = "world_expansion_materialized"
PROPOSED_FACTS_APPLIED_ACTION_TYPE = "proposed_facts_applied"
ACTION_RULING_ACTION_TYPE = "action_ruling"


class TurnRepository(SQLiteRepository):
    """Persistence and atomic application of KP turn proposals."""

    def create_turn_proposal(
        self,
        campaign_id: str,
        player_action: str,
        public_narration: str,
        pc_id: str | None = None,
        kp_notes: str = "",
        proposed_checks: list[dict] | None = None,
        proposed_events: list[dict] | None = None,
        proposed_memories: list[dict] | None = None,
        proposed_npc_updates: list[dict] | None = None,
        proposed_map_moves: list[dict] | None = None,
        proposed_facts: list[dict] | None = None,
        source_model: str = "unknown",
    ) -> dict:
        if pc_id:
            self._require_pc_in_campaign(pc_id, campaign_id)
        checks = dump_candidates(proposed_checks or [], CheckCandidate)
        events = dump_candidates(proposed_events or [], EventCandidate)
        memories = dump_candidates(proposed_memories or [], MemoryCandidate)
        npc_updates = dump_candidates(proposed_npc_updates or [], NpcUpdateCandidate)
        map_moves = dump_candidates(proposed_map_moves or [], MapMoveCandidate)
        facts = dump_candidates(proposed_facts or [], FactCandidate)
        proposal_id = new_id("proposal")
        self.connection.execute(
            """
            INSERT INTO turn_proposals
              (
                id, campaign_id, pc_id, status, player_action, public_narration, kp_notes,
                proposed_checks_json, proposed_events_json, proposed_memories_json,
                proposed_npc_updates_json, proposed_map_moves_json,
                proposed_facts_json, source_model
              )
            VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal_id,
                campaign_id,
                pc_id,
                player_action,
                public_narration,
                kp_notes,
                json.dumps(checks, ensure_ascii=False),
                json.dumps(events, ensure_ascii=False),
                json.dumps(memories, ensure_ascii=False),
                json.dumps(npc_updates, ensure_ascii=False),
                json.dumps(map_moves, ensure_ascii=False),
                json.dumps(facts, ensure_ascii=False),
                source_model,
            ),
        )
        self.add_proposal_action(proposal_id, "created", actor="ai", note="proposal created")
        return self.get_turn_proposal(proposal_id)

    def get_turn_proposal(self, proposal_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM turn_proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Turn proposal not found: {proposal_id}")
        result = self._decode_proposal(row)
        result["actions"] = self.list_proposal_actions(proposal_id)
        return self._decorate_proposal(result, result["actions"])

    def replace_draft_proposed_checks(
        self, proposal_id: str, proposed_checks: list[dict]
    ) -> dict:
        checks = dump_candidates(proposed_checks, CheckCandidate)
        updated = self.connection.execute(
            """
            UPDATE turn_proposals SET proposed_checks_json = ?
            WHERE id = ? AND status = 'draft'
            """,
            (json.dumps(checks, ensure_ascii=False), proposal_id),
        )
        if updated.rowcount != 1:
            raise ValueError("Only a draft proposal can change its proposed checks")
        return self.get_turn_proposal(proposal_id)

    def replace_draft_with_precheck(
        self,
        proposal_id: str,
        proposed_checks: list[dict],
        *,
        public_narration: str,
        action_ruling: dict,
    ) -> dict:
        checks = dump_candidates(proposed_checks, CheckCandidate)
        ruling = ActionRuling.model_validate(action_ruling).model_dump(mode="json")
        updated = self.connection.execute(
            """
            UPDATE turn_proposals
            SET public_narration = ?, proposed_checks_json = ?,
                proposed_events_json = '[]', proposed_memories_json = '[]',
                proposed_npc_updates_json = '[]', proposed_map_moves_json = '[]',
                proposed_facts_json = '[]'
            WHERE id = ? AND status = 'draft'
            """,
            (public_narration, json.dumps(checks, ensure_ascii=False), proposal_id),
        )
        if updated.rowcount != 1:
            raise ValueError("Only a draft proposal can be replaced by a precheck")
        ruling_update = self.connection.execute(
            """
            UPDATE proposal_actions SET payload_json = ?
            WHERE proposal_id = ? AND action_type = ?
            """,
            (json.dumps(ruling, ensure_ascii=False), proposal_id, ACTION_RULING_ACTION_TYPE),
        )
        if ruling_update.rowcount != 1:
            raise ValueError("A precheck proposal requires exactly one action ruling")
        return self.get_turn_proposal(proposal_id)

    def list_turn_proposals(self, campaign_id: str, status: str | None = None) -> list[dict]:
        if status:
            rows = self.connection.execute(
                """
                SELECT * FROM turn_proposals
                WHERE campaign_id = ? AND status = ?
                ORDER BY created_at DESC
                """,
                (campaign_id, status),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT * FROM turn_proposals
                WHERE campaign_id = ?
                ORDER BY created_at DESC
                """,
                (campaign_id,),
            ).fetchall()
        proposals = []
        for row in rows:
            proposal = self._decode_proposal(row)
            actions = self.list_proposal_actions(proposal["id"])
            proposals.append(self._decorate_proposal(proposal, actions))
        return proposals

    @staticmethod
    def _decode_proposal(row: sqlite3.Row) -> dict:
        proposal = row_to_dict(row)
        for field_name in PROPOSAL_JSON_FIELDS:
            proposal[field_name] = decode_json_field(
                proposal.pop(f"{field_name}_json"),
                [],
            )
        return proposal

    def add_proposal_action(
        self,
        proposal_id: str,
        action_type: str,
        actor: str = "human_kp",
        note: str = "",
        payload: dict | None = None,
    ) -> dict:
        action_id = new_id("pa")
        self.connection.execute(
            """
            INSERT INTO proposal_actions (id, proposal_id, action_type, actor, note, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                proposal_id,
                action_type,
                actor,
                note,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )
        return row_to_dict(
            self.connection.execute(
                "SELECT * FROM proposal_actions WHERE id = ?",
                (action_id,),
            ).fetchone()
        )

    def list_proposal_actions(self, proposal_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM proposal_actions
            WHERE proposal_id = ?
            ORDER BY created_at ASC
            """,
            (proposal_id,),
        ).fetchall()
        actions = []
        for row in rows:
            action = row_to_dict(row)
            action["payload"] = decode_json_field(action.pop("payload_json"), {})
            actions.append(action)
        return actions

    @staticmethod
    def _decorate_proposal(
        proposal: dict,
        actions: list[dict] | None = None,
    ) -> dict:
        resolved_actions = actions or []
        consequence = next(
            (
                action["payload"]
                for action in resolved_actions
                if action["action_type"] == CHECK_CONSEQUENCE_ACTION_TYPE
            ),
            None,
        )
        expansion_matches = [
            action["payload"]
            for action in resolved_actions
            if action["action_type"] == WORLD_EXPANSION_ACTION_TYPE
        ]
        if len(expansion_matches) > 1:
            raise ValueError("A proposal has multiple world expansion bases")
        world_expansion = expansion_matches[0] if expansion_matches else None
        materialization_matches = [
            action["payload"]
            for action in resolved_actions
            if action["action_type"] == WORLD_EXPANSION_MATERIALIZED_ACTION_TYPE
        ]
        if len(materialization_matches) > 1:
            raise ValueError("A proposal has multiple world expansion materializations")
        materialization = (
            materialization_matches[0] if materialization_matches else None
        )
        proposal["proposal_kind"] = (
            "check_consequence"
            if consequence is not None
            else "world_expansion"
            if world_expansion is not None
            else "standard"
        )
        proposal["check_consequence"] = consequence
        proposal["world_expansion"] = world_expansion
        proposal["world_expansion_materialization"] = materialization
        ruling_matches = [
            action["payload"]
            for action in resolved_actions
            if action["action_type"] == ACTION_RULING_ACTION_TYPE
        ]
        if len(ruling_matches) > 1:
            raise ValueError("A proposal has multiple action rulings")
        proposal["action_ruling"] = ruling_matches[0] if ruling_matches else None
        applied_fact_matches = [
            action["payload"]
            for action in resolved_actions
            if action["action_type"] == PROPOSED_FACTS_APPLIED_ACTION_TYPE
        ]
        if len(applied_fact_matches) > 1:
            raise ValueError("A proposal has multiple proposed-fact application records")
        proposal["applied_facts"] = (
            applied_fact_matches[0].get("facts", [])
            if applied_fact_matches
            else []
        )
        return proposal

    def attach_world_expansion_basis(
        self,
        proposal_id: str,
        *,
        module_run_id: str,
        module_run_version: int,
        fingerprint: str,
        analysis: dict,
        candidate: dict,
    ) -> dict:
        proposal = self.get_turn_proposal(proposal_id)
        if proposal["status"] != "draft":
            raise ValueError("World expansion metadata requires a draft proposal")
        if proposal["check_consequence"] is not None:
            raise ValueError("Check consequence proposals cannot become world expansions")
        if self.get_world_expansion_basis(proposal_id) is not None:
            raise ValueError("World expansion metadata is already attached")
        run = self.get_campaign_module_run(module_run_id)
        if run["campaign_id"] != proposal["campaign_id"]:
            raise ValueError("World expansion run and proposal belong to different campaigns")
        if run["status"] != "active" or run["version"] != module_run_version:
            raise ValueError("World expansion basis is stale")
        if len(fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in fingerprint
        ):
            raise ValueError("World expansion fingerprint must be SHA-256")
        payload = {
            "proposal_kind": "world_expansion",
            "module_run_id": module_run_id,
            "module_run_version": module_run_version,
            "module_id": run["module_id"],
            "module_source_hash": run["module_source_hash"],
            "fingerprint": fingerprint,
            "analysis": analysis,
            "candidate": candidate,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ValueError("World expansion basis exceeds 64 KiB")
        self.add_proposal_action(
            proposal_id,
            WORLD_EXPANSION_ACTION_TYPE,
            actor="system",
            note="source-bound scene director world gap",
            payload=payload,
        )
        return payload

    def get_world_expansion_basis(self, proposal_id: str) -> dict | None:
        matches = [
            action["payload"]
            for action in self.list_proposal_actions(proposal_id)
            if action["action_type"] == WORLD_EXPANSION_ACTION_TYPE
        ]
        if len(matches) > 1:
            raise ValueError("A proposal has multiple world expansion bases")
        return matches[0] if matches else None

    def find_live_world_expansion(
        self,
        campaign_id: str,
        *,
        fingerprint: str,
    ) -> dict | None:
        rows = self.connection.execute(
            """
            SELECT p.id, pa.payload_json
            FROM proposal_actions pa
            JOIN turn_proposals p ON p.id = pa.proposal_id
            WHERE p.campaign_id = ?
              AND pa.action_type = ?
              AND p.status IN ('draft', 'approved', 'overridden')
            ORDER BY p.created_at, p.id
            """,
            (campaign_id, WORLD_EXPANSION_ACTION_TYPE),
        ).fetchall()
        for row in rows:
            payload = decode_json_field(row["payload_json"], {})
            if payload.get("fingerprint") == fingerprint:
                return self.get_turn_proposal(str(row["id"]))
        return None

    def attach_check_consequence_basis(
        self,
        proposal_id: str,
        *,
        origin_proposal_id: str,
        player_action_id: str,
        check_ids: list[str],
        result_fingerprint: str,
    ) -> dict:
        proposal = self.get_turn_proposal(proposal_id)
        if proposal["status"] != "draft":
            raise ValueError("Check consequence metadata requires a draft proposal")
        if self.get_check_consequence_basis(proposal_id) is not None:
            raise ValueError("Check consequence metadata is already attached")
        normalized_check_ids = sorted(set(check_ids))
        if not normalized_check_ids:
            raise ValueError("Check consequence metadata requires check IDs")
        if len(result_fingerprint) != 64 or any(
            character not in "0123456789abcdef"
            for character in result_fingerprint
        ):
            raise ValueError("Check consequence fingerprint must be SHA-256")
        payload = {
            "proposal_kind": "check_consequence",
            "origin_proposal_id": origin_proposal_id,
            "player_action_id": player_action_id,
            "check_ids": normalized_check_ids,
            "result_fingerprint": result_fingerprint,
        }
        self.add_proposal_action(
            proposal_id,
            CHECK_CONSEQUENCE_ACTION_TYPE,
            actor="system",
            note="verified terminal check basis",
            payload=payload,
        )
        return payload

    def get_check_consequence_basis(self, proposal_id: str) -> dict | None:
        matches = [
            action["payload"]
            for action in self.list_proposal_actions(proposal_id)
            if action["action_type"] == CHECK_CONSEQUENCE_ACTION_TYPE
        ]
        if len(matches) > 1:
            raise ValueError("A proposal has multiple check consequence bases")
        return matches[0] if matches else None

    def find_live_check_consequence(
        self,
        campaign_id: str,
        *,
        player_action_id: str,
        result_fingerprint: str,
    ) -> dict | None:
        rows = self.connection.execute(
            """
            SELECT p.id, p.status, pa.payload_json
            FROM proposal_actions pa
            JOIN turn_proposals p ON p.id = pa.proposal_id
            WHERE p.campaign_id = ?
              AND pa.action_type = ?
              AND p.status IN ('draft', 'approved')
            ORDER BY p.created_at, p.id
            """,
            (campaign_id, CHECK_CONSEQUENCE_ACTION_TYPE),
        ).fetchall()
        for row in rows:
            payload = decode_json_field(row["payload_json"], {})
            if (
                payload.get("player_action_id") == player_action_id
                and payload.get("result_fingerprint") == result_fingerprint
            ):
                return self.get_turn_proposal(str(row["id"]))
        return None

    def approve_turn_proposal(
        self,
        proposal_id: str,
        actor: str = "human_kp",
        note: str = "",
        override_public_narration: str | None = None,
    ) -> dict:
        self.connection.execute("SAVEPOINT approve_turn_proposal")
        try:
            claimed = self.connection.execute(
                """
                UPDATE turn_proposals
                SET status = 'applying'
                WHERE id = ? AND status = 'draft'
                """,
                (proposal_id,),
            )
            if claimed.rowcount != 1:
                proposal = self.get_turn_proposal(proposal_id)
                raise ValueError(
                    "Only draft proposals can be approved; "
                    f"current status is {proposal['status']}"
                )
            validate_proposal_resolution_boundary(
                self.get_turn_proposal(proposal_id)
            )
            self._validate_check_consequence_basis(
                self.get_turn_proposal(proposal_id)
            )
            result = self._apply_turn_proposal(
                proposal_id,
                actor=actor,
                note=note,
                override_public_narration=override_public_narration,
            )
            self.connection.execute("RELEASE SAVEPOINT approve_turn_proposal")
            return result
        except Exception:
            self.connection.execute("ROLLBACK TO SAVEPOINT approve_turn_proposal")
            self.connection.execute("RELEASE SAVEPOINT approve_turn_proposal")
            raise

    def _apply_turn_proposal(
        self,
        proposal_id: str,
        actor: str,
        note: str,
        override_public_narration: str | None,
    ) -> dict:
        proposal = self.get_turn_proposal(proposal_id)
        public_narration = self._apply_narration_override(
            proposal,
            actor=actor,
            note=note,
            override_public_narration=override_public_narration,
        )
        applied_event = self._append_turn_narration(
            proposal,
            actor=actor,
            public_narration=public_narration,
        )
        self._apply_proposed_checks(proposal)
        self._apply_proposed_events(proposal)
        self._apply_proposed_memories(proposal, applied_event["id"])
        self._apply_proposed_npc_updates(proposal)
        self._apply_proposed_map_moves(proposal, actor=actor)
        self._finalize_approved_proposal(
            proposal,
            actor=actor,
            note=note,
            applied_event_id=applied_event["id"],
        )
        return self.get_turn_proposal(proposal_id)

    def _apply_narration_override(
        self,
        proposal: dict,
        *,
        actor: str,
        note: str,
        override_public_narration: str | None,
    ) -> str:
        if override_public_narration:
            self.connection.execute(
                """
                UPDATE turn_proposals
                SET public_narration = ?, status = 'overridden'
                WHERE id = ?
                """,
                (override_public_narration, proposal["id"]),
            )
            self.add_proposal_action(
                proposal["id"],
                "overridden",
                actor=actor,
                note=note,
                payload={"public_narration": override_public_narration},
            )
            return override_public_narration
        return str(proposal["public_narration"])

    def _append_turn_narration(
        self,
        proposal: dict,
        *,
        actor: str,
        public_narration: str,
    ) -> dict:
        return self.append_event(
            campaign_id=proposal["campaign_id"],
            actor_type="kp",
            actor_id=actor,
            visibility="table",
            event_type="kp_turn",
            summary=public_narration,
            payload={
                "proposal_id": proposal["id"],
                "player_action": proposal["player_action"],
            },
        )

    def _apply_proposed_checks(self, proposal: dict) -> None:
        for proposed_check in proposal["proposed_checks"]:
            # The server derives the PC from the authenticated action. A model
            # may attach a pc_id to an individual check, but it is never
            # authority over the proposal's server-derived pc_id.
            check_pc_id = proposal["pc_id"]
            if check_pc_id:
                self._require_pc_in_campaign(check_pc_id, proposal["campaign_id"])
            self.append_event(
                campaign_id=proposal["campaign_id"],
                actor_type="pc",
                actor_id=check_pc_id,
                visibility="kp" if proposed_check.get("hidden") else "table",
                event_type="check_requested",
                summary=(
                    f"{proposed_check['skill']} {proposed_check['difficulty']} check: "
                    f"{proposed_check['reason']}"
                ),
                payload=proposed_check,
            )

    def _apply_proposed_events(self, proposal: dict) -> None:
        for proposed_event in proposal["proposed_events"]:
            actor_type = proposed_event.get("actor_type", "system")
            actor_id = proposed_event.get("actor_id")
            if actor_type == "pc":
                # The server derives the PC from the authenticated action. A
                # model may guess an investigator id here, but it is never
                # authority over the proposal's server-derived pc_id.
                actor_id = proposal["pc_id"]
                if actor_id:
                    self._require_pc_in_campaign(actor_id, proposal["campaign_id"])
            elif actor_id and actor_type == "npc":
                self._require_npc_in_campaign(actor_id, proposal["campaign_id"])
            self.append_event(
                campaign_id=proposal["campaign_id"],
                actor_type=actor_type,
                actor_id=actor_id,
                visibility=proposed_event.get("visibility", "table"),
                event_type=proposed_event.get("event_type", "proposed_event"),
                happened_at=proposed_event.get("happened_at"),
                summary=proposed_event.get("summary", ""),
                payload=proposed_event.get("payload", {}),
            )

    def _apply_proposed_memories(
        self,
        proposal: dict,
        source_event_id: str,
    ) -> None:
        for proposed_memory in proposal["proposed_memories"]:
            # The server derives the PC from the authenticated action, never
            # from a model-supplied pc_id on an individual memory candidate.
            memory_pc_id = proposal["pc_id"]
            if memory_pc_id:
                self._require_pc_in_campaign(memory_pc_id, proposal["campaign_id"])
            if proposed_memory.get("npc_id"):
                self._require_npc_in_campaign(
                    proposed_memory["npc_id"], proposal["campaign_id"]
                )
            self.add_memory(
                text=proposed_memory.get("text", ""),
                scope=proposed_memory.get("scope", "campaign_fact"),
                campaign_id=proposal["campaign_id"],
                pc_id=memory_pc_id,
                npc_id=proposed_memory.get("npc_id"),
                importance=int(proposed_memory.get("importance", 1)),
                visibility=proposed_memory.get("visibility", "table"),
                happened_at=proposed_memory.get("happened_at"),
                source_event_id=source_event_id,
            )

    def _apply_proposed_npc_updates(self, proposal: dict) -> None:
        for proposed_npc_update in proposal["proposed_npc_updates"]:
            self._apply_npc_update(proposal["campaign_id"], proposed_npc_update)
            self.append_event(
                campaign_id=proposal["campaign_id"],
                actor_type="npc",
                actor_id=proposed_npc_update["npc_id"],
                visibility="kp",
                event_type="npc_updated",
                summary=(
                    proposed_npc_update.get("note")
                    or "NPC relationship changed by "
                    f"{proposed_npc_update['relationship_delta']}"
                ),
                payload={
                    "appeared": proposed_npc_update.get("appeared", False),
                    "relationship_delta": proposed_npc_update.get(
                        "relationship_delta", 0
                    ),
                    "last_seen_time": proposed_npc_update.get("last_seen_time"),
                },
            )

    def _apply_proposed_map_moves(self, proposal: dict, *, actor: str) -> None:
        for proposed_map_move in proposal["proposed_map_moves"]:
            token = self._require_token_in_campaign(
                proposed_map_move["token_id"], proposal["campaign_id"]
            )
            moved_token = self.move_map_token(
                token_id=proposed_map_move["token_id"],
                to_location_name=proposed_map_move["to_location_name"],
                moved_by=actor,
                note=f"approved proposal {proposal['id']}",
                require_route=proposed_map_move.get("require_route", True),
            )
            self.append_event(
                campaign_id=proposal["campaign_id"],
                actor_type=token["actor_type"],
                actor_id=token["actor_id"],
                visibility=token["visibility"],
                event_type="map_token_moved",
                summary=(
                    f"{token['label']} moved from {token['location_name']} "
                    f"to {moved_token['location_name']}"
                ),
                payload={
                    "token_id": token["id"],
                    "map_id": token["map_id"],
                    "from_location": token["location_name"],
                    "to_location": moved_token["location_name"],
                },
            )

    def _finalize_approved_proposal(
        self,
        proposal: dict,
        *,
        actor: str,
        note: str,
        applied_event_id: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE turn_proposals
            SET status = 'approved', applied_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (proposal["id"],),
        )
        self.add_proposal_action(
            proposal["id"],
            "approved",
            actor=actor,
            note=note,
            payload={"applied_event_id": applied_event_id},
        )
        consequence = self.get_check_consequence_basis(proposal["id"])
        if consequence is not None:
            self.resolve_player_action_for_proposal(
                consequence["origin_proposal_id"],
                "resolved",
            )
        elif not proposal["proposed_checks"]:
            self.resolve_player_action_for_proposal(proposal["id"], "resolved")

    def _validate_check_consequence_basis(self, proposal: dict) -> None:
        basis = self.get_check_consequence_basis(proposal["id"])
        if basis is None:
            return
        if proposal["proposed_checks"]:
            raise ValueError("A check consequence cannot request another check")
        if proposal["proposed_map_moves"]:
            raise ValueError(
                "The first check-consequence slice cannot move map tokens"
            )
        origin = self.connection.execute(
            """
            SELECT id, campaign_id, status FROM turn_proposals
            WHERE id = ?
            """,
            (basis.get("origin_proposal_id"),),
        ).fetchone()
        if (
            origin is None
            or origin["campaign_id"] != proposal["campaign_id"]
            or origin["status"] != "approved"
        ):
            raise ValueError(
                "Check consequence origin must be an approved proposal "
                "in the same campaign"
            )
        action = self.connection.execute(
            """
            SELECT * FROM player_actions
            WHERE id = ? AND campaign_id = ? AND proposal_id = ?
            """,
            (
                basis.get("player_action_id"),
                proposal["campaign_id"],
                basis.get("origin_proposal_id"),
            ),
        ).fetchone()
        if action is None or action["status"] != "reviewed":
            raise ValueError(
                "Check consequence requires its original action to remain reviewed"
            )
        checks = self.list_skill_checks_for_action(str(action["id"]))
        expected_check_scope = (
            proposal["campaign_id"],
            action["session_id"],
            action["id"],
            basis.get("origin_proposal_id"),
        )
        if not checks or any(
            (
                check.get("campaign_id"),
                check.get("session_id"),
                check.get("player_action_id"),
                check.get("proposal_id"),
            )
            != expected_check_scope
            for check in checks
        ):
            raise ValueError(
                "Check consequence batch crosses its campaign, session, "
                "action, or origin proposal boundary"
            )
        snapshot = build_check_consequence_snapshot(
            checks, self.list_opposed_checks_for_action(str(action["id"]))
        )
        if sorted(basis.get("check_ids") or []) != sorted(
            item["id"] for item in checks
        ):
            raise ValueError("Check consequence basis no longer matches its check set")
        if basis.get("result_fingerprint") != snapshot["result_fingerprint"]:
            raise ValueError(
                "Check results changed after this consequence draft was created"
            )

    def _require_token_in_campaign(self, token_id: str, campaign_id: str) -> dict:
        row = self.connection.execute(
            """
            SELECT t.*, l.name AS location_name
            FROM map_tokens t
            JOIN maps m ON m.id = t.map_id
            JOIN map_locations l ON l.id = t.location_id
            WHERE t.id = ? AND m.campaign_id = ?
            """,
            (token_id, campaign_id),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"Map token {token_id} does not belong to campaign {campaign_id}"
            )
        return row_to_dict(row)

    def reject_turn_proposal(
        self,
        proposal_id: str,
        actor: str = "human_kp",
        note: str = "",
    ) -> dict:
        self.connection.execute("SAVEPOINT reject_turn_proposal")
        try:
            rejected = self.connection.execute(
                """
                UPDATE turn_proposals
                SET status = 'rejected'
                WHERE id = ? AND status = 'draft'
                """,
                (proposal_id,),
            )
            if rejected.rowcount != 1:
                proposal = self.get_turn_proposal(proposal_id)
                raise ValueError(
                    "Only draft proposals can be rejected; "
                    f"current status is {proposal['status']}"
                )
            self.add_proposal_action(proposal_id, "rejected", actor=actor, note=note)
            self.resolve_player_action_for_proposal(proposal_id, "rejected")
            result = self.get_turn_proposal(proposal_id)
            self.connection.execute("RELEASE SAVEPOINT reject_turn_proposal")
            return result
        except Exception:
            self.connection.execute("ROLLBACK TO SAVEPOINT reject_turn_proposal")
            self.connection.execute("RELEASE SAVEPOINT reject_turn_proposal")
            raise

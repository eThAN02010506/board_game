"""Persistence and deterministic source projection for campaign continuity."""

import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class SessionContinuityRepository(SQLiteRepository):
    def create_initial_episode(self, campaign_id: str, session_id: str) -> dict[str, Any]:
        episode_id = new_id("episode")
        event_start_rowid = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(rowid), 0) + 1 FROM events"
            ).fetchone()[0]
        )
        self.connection.execute(
            """
            INSERT INTO campaign_episodes
              (id, campaign_id, session_id, sequence_no, status, event_start_rowid)
            VALUES (?, ?, ?, 1, 'prepared', ?)
            """,
            (episode_id, campaign_id, session_id, event_start_rowid),
        )
        return self.get_campaign_episode(episode_id)

    def get_campaign_episode(self, episode_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM campaign_episodes WHERE id = ?", (episode_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Campaign episode not found: {episode_id}")
        return row_to_dict(row)

    def get_current_campaign_episode(self, session_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT * FROM campaign_episodes
            WHERE session_id = ?
            ORDER BY sequence_no DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        return row_to_dict(row) if row is not None else None

    def activate_prepared_episode(self, session_id: str) -> dict[str, Any] | None:
        episode = self.get_current_campaign_episode(session_id)
        if episode is None or episode["status"] != "prepared":
            return episode
        self.connection.execute(
            """
            UPDATE campaign_episodes
            SET status = 'in_progress', version = version + 1
            WHERE id = ? AND status = 'prepared' AND version = ?
            """,
            (episode["id"], episode["version"]),
        )
        return self.get_campaign_episode(str(episode["id"]))

    def get_continuity_cutoff(self) -> str:
        return str(
            self.connection.execute("SELECT CURRENT_TIMESTAMP AS value").fetchone()[0]
        )

    def get_episode_continuity_context(
        self, episode_id: str, *, cutoff: str
    ) -> dict[str, Any]:
        episode = self.get_campaign_episode(episode_id)
        events = self.connection.execute(
            """
            SELECT id, actor_type, actor_id, visibility, event_type,
                   happened_at, summary, created_at
            FROM events
            WHERE campaign_id = ? AND rowid >= ?
            ORDER BY created_at, id
            """,
            (episode["campaign_id"], episode["event_start_rowid"]),
        ).fetchall()
        party = self.connection.execute(
            """
            SELECT pc.id, pc.name, state.current_hp, state.current_san,
                   state.current_mp, state.current_luck, state.conditions_json,
                   state.inventory_delta_json, state.state_version
            FROM campaign_investigators binding
            JOIN player_characters pc ON pc.id = binding.legacy_pc_id
            LEFT JOIN investigator_campaign_state state
              ON state.campaign_id = binding.campaign_id
             AND state.investigator_id = binding.investigator_id
            WHERE binding.campaign_id = ? AND binding.status = 'approved'
            ORDER BY pc.name, pc.id
            """,
            (episode["campaign_id"],),
        ).fetchall()
        run = self.connection.execute(
            """
            SELECT run.id, run.status, run.current_scene_key, run.current_scene_title,
                   run.current_location_entity_id, run.play_pace, module.title AS module_title
            FROM campaign_module_runs run
            JOIN modules module ON module.id = run.module_id
            WHERE run.campaign_id = ?
            ORDER BY run.started_at DESC, run.id DESC LIMIT 1
            """,
            (episode["campaign_id"],),
        ).fetchone()
        campaign = self.connection.execute(
            "SELECT id, title, current_time FROM campaigns WHERE id = ?",
            (episode["campaign_id"],),
        ).fetchone()
        inventory = self.connection.execute(
            """
            SELECT id, item_type, public_name, public_description, publicly_listed, quantity,
                   is_unique, holder_kind, holder_id, state, equipped_slot,
                   use_effect_json, hidden_properties_json, source_refs_json, version
            FROM campaign_inventory_items
            WHERE campaign_id = ? ORDER BY public_name, id
            """,
            (episode["campaign_id"],),
        ).fetchall()
        balances = self.connection.execute(
            """
            SELECT account_kind, account_id, currency_code, balance_minor, version
            FROM campaign_currency_accounts
            WHERE campaign_id = ? ORDER BY account_kind, account_id, currency_code
            """,
            (episode["campaign_id"],),
        ).fetchall()
        lifecycles = self.list_investigator_lifecycles(  # type: ignore[attr-defined]
            str(episode["campaign_id"])
        )
        lifecycle_events = self.connection.execute(
            """
            SELECT id, investigator_id, member_id, action, from_state, to_state,
                   reason, source_event_id, actor_member_id, public_summary,
                   details_json, created_at
            FROM character_lifecycle_events
            WHERE campaign_id = ? ORDER BY rowid
            """,
            (episode["campaign_id"],),
        ).fetchall()
        presence = self.list_member_presence(  # type: ignore[attr-defined]
            str(episode["campaign_id"]), str(episode["session_id"])
        )
        objectives = self.list_campaign_objectives(str(episode["campaign_id"]))  # type: ignore[attr-defined]
        known_npcs = self.connection.execute(
            """
            SELECT npc.id, npc.name, npc.home_location, npc.profession,
                   npc.public_notes, npc.secret_notes, link.role,
                   link.first_seen_time, link.last_seen_time,
                   link.relationship_score, link.notes AS campaign_notes
            FROM campaign_npcs link
            JOIN npcs npc ON npc.id = link.npc_id
            WHERE link.campaign_id = ?
              AND (
                link.first_seen_time IS NOT NULL
                OR EXISTS (
                  SELECT 1
                  FROM investigator_npc_encounters encounter
                  WHERE encounter.campaign_id = link.campaign_id
                    AND encounter.npc_id = link.npc_id
                )
              )
            ORDER BY COALESCE(link.last_seen_time, link.first_seen_time, ''), npc.name, npc.id
            """,
            (episode["campaign_id"],),
        ).fetchall()
        decoded_inventory = []
        for row in inventory:
            item = row_to_dict(row)
            item["is_unique"] = bool(item["is_unique"])
            item["use_effect"] = decode_json_field(item.pop("use_effect_json"), {})
            item["hidden_properties"] = decode_json_field(
                item.pop("hidden_properties_json"), {}
            )
            item["source_refs"] = decode_json_field(item.pop("source_refs_json"), [])
            decoded_inventory.append(item)
        return {
            "episode": episode,
            "campaign": row_to_dict(campaign),
            "events": [row_to_dict(row) for row in events],
            "party": [self._decode_party_member(row) for row in party],
            "module": row_to_dict(run) if run is not None else None,
            "inventory": decoded_inventory,
            "balances": [row_to_dict(row) for row in balances],
            "character_lifecycles": lifecycles,
            "member_presence": presence,
            "lifecycle_events": [
                {
                    **row_to_dict(row),
                    "details": decode_json_field(row["details_json"], {}),
                }
                for row in lifecycle_events
            ],
            "objectives": objectives,
            "known_npcs": [row_to_dict(row) for row in known_npcs],
        }

    def get_session_end_blockers(self, session_id: str) -> dict[str, int]:
        queries = {
            "actions": """
                SELECT COUNT(*) FROM player_actions
                WHERE session_id = ? AND status NOT IN ('resolved', 'rejected')
            """,
            "checks": """
                SELECT COUNT(*) FROM skill_checks
                WHERE session_id = ? AND status = 'requested'
            """,
            "jobs": """
                SELECT COUNT(*) FROM auto_kp_jobs
                WHERE campaign_id = (
                  SELECT campaign_id FROM campaign_sessions WHERE id = ?
                ) AND status IN ('queued', 'running', 'retry_wait')
            """,
            "parallel_batches": """
                SELECT COUNT(*) FROM parallel_action_batches
                WHERE session_id = ? AND status NOT IN ('settled', 'superseded')
            """,
            "lifecycle_requests": """
                SELECT COUNT(*) FROM character_lifecycle_requests
                WHERE session_id = ? AND status = 'awaiting_player'
            """,
        }
        return {
            key: int(self.connection.execute(sql, (session_id,)).fetchone()[0])
            for key, sql in queries.items()
        }

    def create_continuity_snapshot(
        self,
        *,
        episode_id: str,
        client_end_id: str,
        event_window_hash: str,
        event_ids: list[str],
        generation_cutoff: str,
        public_projection: dict[str, Any],
        observer_projection: dict[str, Any],
        kp_projection: dict[str, Any],
        ended_by_member_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        episode = self.get_campaign_episode(episode_id)
        existing = self.connection.execute(
            """
            SELECT id FROM session_continuity_snapshots
            WHERE session_id = ? AND client_end_id = ?
            """,
            (episode["session_id"], client_end_id),
        ).fetchone()
        if existing is not None:
            return self.get_continuity_snapshot(str(existing["id"]))
        updated = self.connection.execute(
            """
            UPDATE campaign_episodes
            SET status = 'ended', ended_at = ?, version = version + 1
            WHERE id = ? AND version = ? AND status IN ('prepared', 'in_progress', 'paused')
            """,
            (generation_cutoff, episode_id, expected_version),
        )
        if updated.rowcount != 1:
            raise ValueError("Session episode changed; refresh before ending it")
        snapshot_id = new_id("continuity")
        self.connection.execute(
            """
            INSERT INTO session_continuity_snapshots
              (id, episode_id, campaign_id, session_id, client_end_id,
               event_window_hash, event_ids_json, generation_cutoff,
               public_projection_json, observer_projection_json,
               kp_projection_json, ended_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                episode_id,
                episode["campaign_id"],
                episode["session_id"],
                client_end_id,
                event_window_hash,
                json.dumps(event_ids, ensure_ascii=False),
                generation_cutoff,
                json.dumps(public_projection, ensure_ascii=False, sort_keys=True),
                json.dumps(observer_projection, ensure_ascii=False, sort_keys=True),
                json.dumps(kp_projection, ensure_ascii=False, sort_keys=True),
                ended_by_member_id,
            ),
        )
        return self.get_continuity_snapshot(snapshot_id)

    def continue_campaign_episode(
        self, session_id: str, *, client_continue_id: str
    ) -> dict[str, Any]:
        replay = self.connection.execute(
            """
            SELECT id FROM campaign_episodes
            WHERE session_id = ? AND client_continue_id = ?
            """,
            (session_id, client_continue_id),
        ).fetchone()
        if replay is not None:
            episode = self.get_campaign_episode(str(replay["id"]))
            episode["_idempotent_replay"] = True
            return episode
        latest = self.get_current_campaign_episode(session_id)
        if latest is None or latest["status"] != "ended":
            raise ValueError("The current session episode has not ended")
        episode_id = new_id("episode")
        event_start_rowid = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(rowid), 0) + 1 FROM events"
            ).fetchone()[0]
        )
        self.connection.execute(
            """
            INSERT INTO campaign_episodes
              (id, campaign_id, session_id, sequence_no, status, client_continue_id,
               event_start_rowid)
            VALUES (?, ?, ?, ?, 'in_progress', ?, ?)
            """,
            (
                episode_id,
                latest["campaign_id"],
                session_id,
                int(latest["sequence_no"]) + 1,
                client_continue_id,
                event_start_rowid,
            ),
        )
        return self.get_campaign_episode(episode_id)

    def transition_campaign_episode(
        self, episode_id: str, *, expected_version: int, target: str
    ) -> dict[str, Any]:
        expected_source = "in_progress" if target == "paused" else "paused"
        updated = self.connection.execute(
            """
            UPDATE campaign_episodes
            SET status = ?, version = version + 1
            WHERE id = ? AND version = ? AND status = ?
            """,
            (target, episode_id, expected_version, expected_source),
        )
        if updated.rowcount != 1:
            raise ValueError("Campaign episode changed; refresh before updating it")
        return self.get_campaign_episode(episode_id)

    def get_continuity_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM session_continuity_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Continuity snapshot not found: {snapshot_id}")
        result = row_to_dict(row)
        result["event_ids"] = decode_json_field(result.pop("event_ids_json"), [])
        result["public_projection"] = decode_json_field(
            result.pop("public_projection_json"), {}
        )
        result["observer_projection"] = decode_json_field(
            result.pop("observer_projection_json"), {}
        )
        result["kp_projection"] = decode_json_field(result.pop("kp_projection_json"), {})
        return result

    def get_latest_campaign_continuity_snapshot(
        self, campaign_id: str
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT id FROM session_continuity_snapshots
            WHERE campaign_id = ? ORDER BY rowid DESC LIMIT 1
            """,
            (campaign_id,),
        ).fetchone()
        return self.get_continuity_snapshot(str(row["id"])) if row else None

    @staticmethod
    def _decode_party_member(row: Any) -> dict[str, Any]:
        result = row_to_dict(row)
        result["conditions"] = decode_json_field(result.pop("conditions_json"), [])
        result["inventory_delta"] = decode_json_field(
            result.pop("inventory_delta_json"), {}
        )
        return result


__all__ = ["SessionContinuityRepository"]

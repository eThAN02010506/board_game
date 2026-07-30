"""SQLite persistence for bounded session recap drafts and KP review."""

import json

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class SessionRecapRepository(SQLiteRepository):
    def get_recap_generation_cutoff(self) -> str:
        row = self.connection.execute("SELECT CURRENT_TIMESTAMP AS value").fetchone()
        return str(row["value"])

    def list_session_recap_events(
        self,
        session_id: str,
        *,
        cutoff: str,
    ) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT e.id, e.actor_type, e.actor_id, e.visibility, e.event_type,
                   e.happened_at, e.summary, e.created_at
            FROM events e
            JOIN campaign_sessions session ON session.campaign_id = e.campaign_id
            WHERE session.id = ?
              AND e.created_at >= session.started_at
              AND e.created_at <= ?
            ORDER BY e.created_at, e.id
            """,
            (session_id, cutoff),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def get_session_recap_snapshot_context(self, session_id: str) -> dict:
        session = self.get_campaign_session(session_id)
        campaign_id = str(session["campaign_id"])
        campaign = self.connection.execute(
            "SELECT id, title, system, current_time FROM campaigns WHERE id = ?",
            (campaign_id,),
        ).fetchone()
        pcs = self.connection.execute(
            """
            SELECT pc.id, pc.name, ci.investigator_id
            FROM campaign_investigators ci
            JOIN player_characters pc ON pc.id = ci.legacy_pc_id
            WHERE ci.campaign_id = ? AND ci.status = 'approved'
              AND ci.approved_revision_id IS NOT NULL
            ORDER BY pc.name, pc.id
            """,
            (campaign_id,),
        ).fetchall()
        npcs = self.connection.execute(
            """
            SELECT npc.id, npc.name, npc.profession, npc.home_location
            FROM campaign_npcs cn JOIN npcs npc ON npc.id = cn.npc_id
            WHERE cn.campaign_id = ?
            ORDER BY npc.name, npc.id
            """,
            (campaign_id,),
        ).fetchall()
        memories = self.connection.execute(
            """
            SELECT id, text, pc_id, npc_id, source_event_id
            FROM memories WHERE campaign_id = ?
            ORDER BY created_at DESC, id DESC LIMIT 300
            """,
            (campaign_id,),
        ).fetchall()
        return {
            "session": {
                key: session[key]
                for key in ("id", "campaign_id", "title", "status", "started_at", "ended_at")
            },
            "campaign": row_to_dict(campaign),
            "pcs": [row_to_dict(row) for row in pcs],
            "npcs": [row_to_dict(row) for row in npcs],
            "existing_memories": [row_to_dict(row) for row in memories],
        }

    def find_session_recap_run(
        self,
        session_id: str,
        event_window_hash: str,
    ) -> dict | None:
        row = self.connection.execute(
            """
            SELECT * FROM session_recap_runs
            WHERE session_id = ? AND event_window_hash = ?
            """,
            (session_id, event_window_hash),
        ).fetchone()
        return self.get_session_recap_run(str(row["id"])) if row else None

    def list_events_by_ids(
        self,
        campaign_id: str,
        event_ids: list[str],
    ) -> list[dict]:
        if not event_ids:
            return []
        placeholders = ",".join("?" for _ in event_ids)
        rows = self.connection.execute(
            f"""
            SELECT id, actor_type, actor_id, visibility, event_type,
                   happened_at, summary, created_at
            FROM events
            WHERE campaign_id = ? AND id IN ({placeholders})
            """,
            (campaign_id, *event_ids),
        ).fetchall()
        by_id = {str(row["id"]): row_to_dict(row) for row in rows}
        return [by_id[event_id] for event_id in event_ids if event_id in by_id]

    def create_session_recap_run(
        self,
        *,
        campaign_id: str,
        session_id: str,
        event_window_hash: str,
        event_ids: list[str],
        generation_cutoff: str,
        source_model: str,
        prompt_version: str,
        repaired: bool,
        created_by_member_id: str,
        candidates: list[dict],
    ) -> dict:
        run_id = new_id("recap")
        self.connection.execute(
            """
            INSERT INTO session_recap_runs
              (id, campaign_id, session_id, event_window_hash, event_ids_json,
               generation_cutoff, source_model, prompt_version, repaired,
               created_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                campaign_id,
                session_id,
                event_window_hash,
                json.dumps(event_ids, ensure_ascii=False),
                generation_cutoff,
                source_model,
                prompt_version,
                int(repaired),
                created_by_member_id,
            ),
        )
        for index, candidate in enumerate(candidates):
            self.connection.execute(
                """
                INSERT INTO session_recap_candidates
                  (id, run_id, campaign_id, order_index, text, scope, importance,
                   visibility, pc_id, npc_id, happened_at, source_event_ids_json,
                   rationale)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("recap_candidate"),
                    run_id,
                    campaign_id,
                    index,
                    candidate["text"],
                    candidate["scope"],
                    candidate["importance"],
                    candidate["visibility"],
                    candidate.get("pc_id"),
                    candidate.get("npc_id"),
                    candidate.get("happened_at"),
                    json.dumps(candidate["source_event_ids"], ensure_ascii=False),
                    candidate["rationale"],
                ),
            )
        if not candidates:
            self.connection.execute(
                """
                UPDATE session_recap_runs
                SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (run_id,),
            )
        return self.get_session_recap_run(run_id)

    def get_session_recap_run(self, run_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM session_recap_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Session recap run not found: {run_id}")
        result = self._decode_run(row)
        result["candidates"] = self.list_session_recap_candidates(run_id)
        return result

    def get_latest_session_recap_run(self, session_id: str) -> dict | None:
        row = self.connection.execute(
            """
            SELECT id FROM session_recap_runs
            WHERE session_id = ?
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        return self.get_session_recap_run(str(row["id"])) if row else None

    def list_session_recap_candidates(self, run_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT * FROM session_recap_candidates
            WHERE run_id = ? ORDER BY order_index
            """,
            (run_id,),
        ).fetchall()
        return [self._decode_candidate(row) for row in rows]

    def get_session_recap_candidate(self, candidate_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM session_recap_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Session recap candidate not found: {candidate_id}")
        return self._decode_candidate(row)

    def review_session_recap_candidate(
        self,
        candidate_id: str,
        *,
        action: str,
        effective: dict,
        decision_hash: str,
        reason: str,
        reviewed_by_member_id: str,
    ) -> dict:
        self.begin_immediate()
        candidate = self.get_session_recap_candidate(candidate_id)
        if candidate["status"] != "draft":
            previous = candidate["review_payload"]
            if (
                candidate["status"] == ("approved" if action == "approve" else "rejected")
                and previous.get("decision_hash") == decision_hash
            ):
                candidate["_idempotent_replay"] = True
                return candidate
            raise ValueError(
                f"Recap candidate is already {candidate['status']}; refresh before reviewing"
            )

        memory_id = None
        if action == "approve":
            memory = self.add_memory(
                text=effective["text"],
                scope=effective["scope"],
                campaign_id=candidate["campaign_id"],
                pc_id=effective.get("pc_id"),
                npc_id=effective.get("npc_id"),
                importance=effective["importance"],
                visibility=effective["visibility"],
                happened_at=effective.get("happened_at"),
                source_event_id=candidate["source_event_ids"][0],
            )
            memory_id = memory["id"]
        review_payload = {
            "action": action,
            "reason": reason,
            "effective": effective,
            "decision_hash": decision_hash,
        }
        updated = self.connection.execute(
            """
            UPDATE session_recap_candidates
            SET status = ?, review_payload_json = ?, memory_id = ?,
                reviewed_by_member_id = ?, reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'draft'
            """,
            (
                "approved" if action == "approve" else "rejected",
                json.dumps(review_payload, ensure_ascii=False, sort_keys=True),
                memory_id,
                reviewed_by_member_id,
                candidate_id,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("Recap candidate changed; refresh before reviewing")
        self._complete_recap_run_if_decided(str(candidate["run_id"]))
        return self.get_session_recap_candidate(candidate_id)

    def _complete_recap_run_if_decided(self, run_id: str) -> None:
        remaining = self.connection.execute(
            """
            SELECT 1 FROM session_recap_candidates
            WHERE run_id = ? AND status = 'draft' LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if remaining is None:
            self.connection.execute(
                """
                UPDATE session_recap_runs
                SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND status = 'draft'
                """,
                (run_id,),
            )

    @staticmethod
    def _decode_run(row) -> dict:
        result = row_to_dict(row)
        result["event_ids"] = decode_json_field(result.pop("event_ids_json"), [])
        result["repaired"] = bool(result["repaired"])
        return result

    @staticmethod
    def _decode_candidate(row) -> dict:
        result = row_to_dict(row)
        result["source_event_ids"] = decode_json_field(
            result.pop("source_event_ids_json"),
            [],
        )
        result["review_payload"] = decode_json_field(
            result.pop("review_payload_json"),
            {},
        )
        return result

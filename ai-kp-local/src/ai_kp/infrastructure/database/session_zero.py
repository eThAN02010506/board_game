"""SQLite persistence for versioned Session 0 agreements and safety events."""

import json
import sqlite3
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository
from ai_kp.platform.sessions.session_zero import (
    CampaignSetupConfig,
    SessionZeroModelPolicy,
    SessionZeroPreferences,
)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _unique_text(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


class SessionZeroRepository(SQLiteRepository):
    def get_current_session_zero_revision(self, campaign_id: str) -> dict | None:
        row = self.connection.execute(
            """
            SELECT * FROM campaign_setup_revisions
            WHERE campaign_id = ? AND status IN ('pending', 'active')
            ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, version DESC
            LIMIT 1
            """,
            (campaign_id,),
        ).fetchone()
        return self._decode_revision(row) if row is not None else None

    def get_active_session_zero_revision(self, campaign_id: str) -> dict | None:
        row = self.connection.execute(
            """
            SELECT * FROM campaign_setup_revisions
            WHERE campaign_id = ? AND status = 'active'
            """,
            (campaign_id,),
        ).fetchone()
        return self._decode_revision(row) if row is not None else None

    def get_session_zero_revision(self, revision_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM campaign_setup_revisions WHERE id = ?", (revision_id,)
        ).fetchone()
        if row is None:
            raise KeyError("Session 0 revision not found")
        return self._decode_revision(row)

    def create_session_zero_revision(
        self,
        *,
        campaign_id: str,
        config: CampaignSetupConfig,
        expected_version: int,
        actor_member_id: str,
    ) -> dict:
        self.begin_immediate()
        self._require_active_campaign_member(campaign_id, actor_member_id, role="kp")
        latest = self.connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM campaign_setup_revisions WHERE campaign_id = ?",
            (campaign_id,),
        ).fetchone()[0]
        if int(latest) != expected_version:
            raise ValueError("Session 0 changed; refresh before editing")
        source = self.get_current_session_zero_revision(campaign_id)
        self.connection.execute(
            """
            UPDATE campaign_setup_revisions SET status = 'superseded'
            WHERE campaign_id = ? AND status = 'pending'
            """,
            (campaign_id,),
        )
        revision_id = new_id("setup")
        version = expected_version + 1
        self.connection.execute(
            """
            INSERT INTO campaign_setup_revisions
              (id, campaign_id, version, status, config_json, created_by_member_id)
            VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (revision_id, campaign_id, version, _json(config.model_dump(mode="json")), actor_member_id),
        )
        if source is not None:
            self.connection.execute(
                """
                INSERT INTO session_zero_preferences
                  (revision_id, member_id, public_json, private_json)
                SELECT ?, member_id, public_json, private_json
                FROM session_zero_preferences WHERE revision_id = ?
                """,
                (revision_id, source["id"]),
            )
        self._confirm(revision_id, actor_member_id)
        self._activate_if_complete(revision_id)
        return self.get_session_zero_revision(revision_id)

    def update_session_zero_preferences(
        self,
        *,
        campaign_id: str,
        expected_version: int,
        preferences: SessionZeroPreferences,
        actor_member_id: str,
    ) -> dict:
        self.begin_immediate()
        self._require_active_campaign_member(campaign_id, actor_member_id)
        current = self.get_current_session_zero_revision(campaign_id)
        if current is None or int(current["version"]) != expected_version:
            raise ValueError("Session 0 changed; refresh before editing preferences")
        # A preference change is safety-relevant. Clone the immutable agreement,
        # retire any prior pending revision, and require fresh table consent.
        self.connection.execute(
            """
            UPDATE campaign_setup_revisions SET status = 'superseded'
            WHERE campaign_id = ? AND status = 'pending'
            """,
            (campaign_id,),
        )
        latest = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM campaign_setup_revisions WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()[0]
        )
        revision_id = new_id("setup")
        self.connection.execute(
            """
            INSERT INTO campaign_setup_revisions
              (id, campaign_id, version, status, config_json, created_by_member_id)
            VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (
                revision_id,
                campaign_id,
                latest + 1,
                _json(current["config"]),
                actor_member_id,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO session_zero_preferences
              (revision_id, member_id, public_json, private_json)
            SELECT ?, member_id, public_json, private_json
            FROM session_zero_preferences WHERE revision_id = ?
            """,
            (revision_id, current["id"]),
        )
        public = {"public_style": preferences.public_style}
        private = {
            "private_style": preferences.private_style,
            "lines": preferences.lines,
            "veils": preferences.veils,
        }
        self.connection.execute(
            """
            INSERT INTO session_zero_preferences
              (revision_id, member_id, public_json, private_json, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(revision_id, member_id) DO UPDATE SET
              public_json = excluded.public_json,
              private_json = excluded.private_json,
              updated_at = CURRENT_TIMESTAMP
            """,
            (revision_id, actor_member_id, _json(public), _json(private)),
        )
        self._confirm(revision_id, actor_member_id)
        self._activate_if_complete(revision_id)
        return self.get_session_zero_revision(revision_id)

    def confirm_session_zero_revision(
        self, revision_id: str, *, actor_member_id: str, expected_version: int
    ) -> dict:
        self.begin_immediate()
        revision = self.get_session_zero_revision(revision_id)
        current = self.get_current_session_zero_revision(str(revision["campaign_id"]))
        if current is None or current["id"] != revision_id:
            raise ValueError("Session 0 changed; refresh before confirming")
        if revision["status"] not in {"pending", "active"}:
            raise ValueError("Session 0 revision is no longer confirmable")
        if int(revision["version"]) != expected_version:
            raise ValueError("Session 0 changed; refresh before confirming")
        self._require_active_campaign_member(str(revision["campaign_id"]), actor_member_id)
        self._confirm(revision_id, actor_member_id)
        self._activate_if_complete(revision_id)
        return self.get_session_zero_revision(revision_id)

    def session_zero_projection(self, campaign_id: str, member_id: str) -> dict:
        member = self._require_active_campaign_member(campaign_id, member_id)
        revision = self.get_current_session_zero_revision(campaign_id)
        if revision is None:
            return {
                "revision": None,
                "ready": False,
                "confirmed": False,
                "confirmed_count": 0,
                "required_count": self._required_member_count(campaign_id),
                "public_preferences": [],
                "own_preferences": None,
                "model_policy": None,
                "active_safety_event": self._project_safety_event(
                    self.get_active_session_safety_event(str(member["session_id"]))
                ),
                "can_resolve_safety": False,
            }
        confirmations = {
            str(row["member_id"])
            for row in self.connection.execute(
                "SELECT member_id FROM session_zero_confirmations WHERE revision_id = ?",
                (revision["id"],),
            ).fetchall()
        }
        preferences = self.connection.execute(
            """
            SELECT member_id, public_json, private_json
            FROM session_zero_preferences WHERE revision_id = ?
            ORDER BY member_id
            """,
            (revision["id"],),
        ).fetchall()
        own = next((row for row in preferences if row["member_id"] == member_id), None)
        active_safety = self.get_active_session_safety_event(str(member["session_id"]))
        return {
            "revision": self._project_revision(revision),
            "ready": revision["status"] == "active" and self._all_required_confirmed(revision["id"]),
            "confirmed": member_id in confirmations,
            "confirmed_count": len(confirmations),
            "required_count": self._required_member_count(campaign_id),
            "public_preferences": [json.loads(row["public_json"]) for row in preferences],
            "own_preferences": (
                {
                    **json.loads(own["public_json"]),
                    **json.loads(own["private_json"]),
                }
                if own is not None
                else None
            ),
            # The aggregate may contain another member's private boundary.
            # It is consumed only by the server-side LLM policy decorator and
            # must never become a client-visible projection.
            "model_policy": None,
            "active_safety_event": self._project_safety_event(active_safety),
            "can_resolve_safety": bool(
                active_safety
                and (
                    member["role"] == "kp"
                    or active_safety.get("actor_member_id") == member_id
                )
            ),
        }

    def session_zero_model_policy(self, campaign_id: str) -> SessionZeroModelPolicy:
        revision = self.get_current_session_zero_revision(campaign_id)
        if revision is None:
            return SessionZeroModelPolicy()
        config = CampaignSetupConfig.model_validate(revision["config"])
        lines = list(config.lines)
        veils = list(config.veils)
        rows = self.connection.execute(
            "SELECT private_json FROM session_zero_preferences WHERE revision_id = ?",
            (revision["id"],),
        ).fetchall()
        for row in rows:
            private = json.loads(row["private_json"])
            lines.extend(private.get("lines") or ())
            veils.extend(private.get("veils") or ())
        return SessionZeroModelPolicy(
            content_warnings=_unique_text(list(config.content_warnings)),
            lines=_unique_text(lines),
            veils=_unique_text(veils),
            safety_default=config.safety_default,
        )

    def create_session_safety_event(
        self, *, actor_member_id: str, response_kind: str
    ) -> dict:
        self.begin_immediate()
        member = self.get_session_member(actor_member_id)
        if member.get("revoked_at") is not None:
            raise PermissionError("Active session membership required")
        active = self.get_active_session_safety_event(str(member["session_id"]))
        if active is not None:
            return active
        run = self.get_active_campaign_module_run(str(member["campaign_id"]))
        prior_mode = str(run["director_control_mode"]) if run is not None else None
        event_id = new_id("safety")
        self.connection.execute(
            """
            INSERT INTO session_safety_events
              (id, campaign_id, session_id, actor_member_id, response_kind,
               run_id, prior_control_mode, public_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                member["campaign_id"],
                member["session_id"],
                actor_member_id,
                response_kind,
                run["id"] if run is not None else None,
                prior_mode,
                "安全工具已触发。当前内容暂停，触发者无需说明原因。",
            ),
        )
        if run is not None and prior_mode != "safety_paused":
            self.set_module_run_control(
                str(run["id"]),
                expected_version=int(run["version"]),
                mode="safety_paused",
                reason="Session safety tool activated; no private reason recorded",
                member_id=actor_member_id,
            )
        self.append_realtime_event(
            session_id=str(member["session_id"]),
            campaign_id=str(member["campaign_id"]),
            audience="session",
            event_type="session.safety_paused",
            resource_type="session_safety_event",
            resource_id=event_id,
            payload={"message": "安全工具已触发，当前内容暂停。"},
        )
        return self.get_session_safety_event(event_id)

    def resolve_session_safety_event(
        self,
        event_id: str,
        *,
        actor_member_id: str,
        resolution_kind: str,
    ) -> dict:
        self.begin_immediate()
        event = self.get_session_safety_event(event_id)
        member = self._require_active_campaign_member(
            str(event["campaign_id"]), actor_member_id
        )
        if member["role"] != "kp" and event.get("actor_member_id") != actor_member_id:
            raise PermissionError("Only the triggering member or KP may resolve safety pause")
        if event["status"] == "resolved":
            return event
        run = self.get_active_campaign_module_run(str(event["campaign_id"]))
        if run is not None and run.get("director_control_mode") == "safety_paused":
            restore = str(event.get("prior_control_mode") or "ai_assist")
            if restore == "safety_paused":
                restore = "ai_assist"
            self.set_module_run_control(
                str(run["id"]),
                expected_version=int(run["version"]),
                mode=restore,
                reason=f"Safety content handled with {resolution_kind}",
                member_id=actor_member_id,
            )
        self.connection.execute(
            """
            UPDATE session_safety_events
            SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP,
                resolved_by_member_id = ?, resolution_kind = ?
            WHERE id = ? AND status = 'active'
            """,
            (actor_member_id, resolution_kind, event_id),
        )
        self.append_realtime_event(
            session_id=str(event["session_id"]),
            campaign_id=str(event["campaign_id"]),
            audience="session",
            event_type="session.safety_resolved",
            resource_type="session_safety_event",
            resource_id=event_id,
            payload={"resolution_kind": resolution_kind},
        )
        return self.get_session_safety_event(event_id)

    def get_session_safety_event(self, event_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM session_safety_events WHERE id = ?", (event_id,)
        ).fetchone()
        if row is None:
            raise KeyError("Safety event not found")
        return row_to_dict(row)

    def get_active_session_safety_event(self, session_id: str) -> dict | None:
        row = self.connection.execute(
            """
            SELECT * FROM session_safety_events
            WHERE session_id = ? AND status = 'active'
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        return row_to_dict(row) if row is not None else None

    def get_latest_session_safety_event(self, session_id: str) -> dict | None:
        row = self.connection.execute(
            """
            SELECT * FROM session_safety_events
            WHERE session_id = ?
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        return row_to_dict(row) if row is not None else None

    def _activate_if_complete(self, revision_id: str) -> None:
        revision = self.get_session_zero_revision(revision_id)
        if not self._all_required_confirmed(revision_id):
            return
        self.connection.execute(
            """
            UPDATE campaign_setup_revisions SET status = 'superseded'
            WHERE campaign_id = ? AND status = 'active' AND id != ?
            """,
            (revision["campaign_id"], revision_id),
        )
        self.connection.execute(
            """
            UPDATE campaign_setup_revisions
            SET status = 'active', activated_at = COALESCE(activated_at, CURRENT_TIMESTAMP)
            WHERE id = ? AND status IN ('pending', 'active')
            """,
            (revision_id,),
        )
        active_session = self.connection.execute(
            """
            SELECT id FROM campaign_sessions
            WHERE campaign_id = ? AND status = 'active'
            ORDER BY started_at DESC, id DESC LIMIT 1
            """,
            (revision["campaign_id"],),
        ).fetchone()
        if active_session is not None:
            self.connection.execute(
                """
                UPDATE campaign_episodes
                SET status = 'in_progress', version = version + 1
                WHERE session_id = ? AND status = 'prepared'
                """,
                (active_session["id"],),
            )

    def _all_required_confirmed(self, revision_id: str) -> bool:
        revision = self.get_session_zero_revision(revision_id)
        required = self._required_member_count(str(revision["campaign_id"]))
        confirmed = int(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM session_zero_confirmations c
                JOIN session_members sm ON sm.id = c.member_id
                JOIN campaign_sessions cs ON cs.id = sm.session_id
                WHERE c.revision_id = ? AND sm.revoked_at IS NULL
                  AND cs.status = 'active' AND sm.campaign_id = ?
                  AND sm.role IN ('kp', 'player')
                """,
                (revision_id, revision["campaign_id"]),
            ).fetchone()[0]
        )
        return required > 0 and confirmed == required

    def _required_member_count(self, campaign_id: str) -> int:
        return int(
            self.connection.execute(
                """
                SELECT COUNT(*) FROM session_members sm
                JOIN campaign_sessions cs ON cs.id = sm.session_id
                WHERE sm.campaign_id = ? AND sm.revoked_at IS NULL AND cs.status = 'active'
                  AND sm.role IN ('kp', 'player')
                """,
                (campaign_id,),
            ).fetchone()[0]
        )

    def _confirm(self, revision_id: str, member_id: str) -> None:
        self.connection.execute(
            """
            INSERT INTO session_zero_confirmations (revision_id, member_id)
            VALUES (?, ?) ON CONFLICT(revision_id, member_id) DO NOTHING
            """,
            (revision_id, member_id),
        )

    def _require_active_campaign_member(
        self, campaign_id: str, member_id: str, *, role: str | None = None
    ) -> dict[str, Any]:
        member = self.get_session_member(member_id)
        if member["campaign_id"] != campaign_id or member.get("revoked_at") is not None:
            raise PermissionError("Active campaign membership required")
        session = self.get_campaign_session(str(member["session_id"]))
        if session["status"] != "active":
            raise PermissionError("Active campaign session required")
        if role is not None and member["role"] != role:
            raise PermissionError(f"{role} role required")
        return member

    @staticmethod
    def _project_revision(revision: dict) -> dict:
        return {
            "id": revision["id"],
            "campaign_id": revision["campaign_id"],
            "version": revision["version"],
            "status": revision["status"],
            "config": revision["config"],
            "created_at": revision["created_at"],
            "activated_at": revision.get("activated_at"),
        }

    @staticmethod
    def _decode_revision(row: sqlite3.Row) -> dict:
        result = row_to_dict(row)
        result["config"] = json.loads(result.pop("config_json"))
        return result

    @staticmethod
    def _project_safety_event(event: dict | None) -> dict | None:
        if event is None:
            return None
        return {
            "id": event["id"],
            "status": event["status"],
            "response_kind": event["response_kind"],
            "public_message": event["public_message"],
            "created_at": event["created_at"],
        }


__all__ = ["SessionZeroRepository"]

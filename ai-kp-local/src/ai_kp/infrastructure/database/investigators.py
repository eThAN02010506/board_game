"""SQLite adapter for player-owned investigators, revisions, reviews, and runtime state."""

import json
from dataclasses import dataclass
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.security.tokens import generate_player_token, hash_player_token
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


@dataclass(frozen=True)
class AuthenticatedPlayer:
    profile_id: str
    display_name: str


class InvestigatorRepository(SQLiteRepository):
    def create_player_profile(self, display_name: str) -> dict:
        profile_id = new_id("player")
        token = generate_player_token()
        self.connection.execute(
            "INSERT INTO player_profiles (id, display_name, token_hash) VALUES (?, ?, ?)",
            (profile_id, display_name, hash_player_token(token)),
        )
        return {"profile": self.get_player_profile(profile_id), "player_token": token}

    def get_player_profile(self, profile_id: str) -> dict:
        row = self.connection.execute(
            "SELECT id, display_name, created_at, updated_at FROM player_profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Player profile not found: {profile_id}")
        return row_to_dict(row)

    def authenticate_player_token(self, token: str) -> AuthenticatedPlayer | None:
        row = self.connection.execute(
            "SELECT id, display_name FROM player_profiles WHERE token_hash = ?",
            (hash_player_token(token),),
        ).fetchone()
        if row is None:
            return None
        return AuthenticatedPlayer(profile_id=str(row["id"]), display_name=str(row["display_name"]))

    def create_investigator(
        self,
        owner_profile_id: str,
        canonical_sheet: dict[str, Any],
        *,
        source_type: str,
        source_hash: str | None = None,
        source_filename: str | None = None,
        template_id: str | None = None,
        parser_version: str | None = None,
        warnings: list[str] | None = None,
    ) -> dict:
        name = str((canonical_sheet.get("identity") or {}).get("name") or "").strip()
        if not name:
            raise ValueError("Investigator name is required before saving")
        investigator_id = new_id("investigator")
        self.connection.execute(
            """
            INSERT INTO investigators (id, owner_profile_id, ruleset_id, name)
            VALUES (?, ?, ?, ?)
            """,
            (
                investigator_id,
                owner_profile_id,
                str(canonical_sheet.get("ruleset_id") or "coc7-keeper-cn-2002c"),
                name,
            ),
        )
        revision = self._create_revision(
            investigator_id,
            canonical_sheet,
            revision_no=1,
            source_type=source_type,
            source_hash=source_hash,
            template_id=template_id,
            parser_version=parser_version,
            warnings=warnings,
        )
        self.connection.execute(
            "UPDATE investigators SET current_revision_id = ? WHERE id = ?",
            (revision["id"], investigator_id),
        )
        if source_type == "xlsx":
            if not all((source_hash, source_filename, template_id, parser_version)):
                raise ValueError("Excel provenance is incomplete")
            self.connection.execute(
                """
                INSERT INTO character_imports
                  (id, owner_profile_id, investigator_id, revision_id, source_filename,
                   source_hash, template_id, parser_version, warnings_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("charimport"),
                    owner_profile_id,
                    investigator_id,
                    revision["id"],
                    source_filename,
                    source_hash,
                    template_id,
                    parser_version,
                    json.dumps(warnings or [], ensure_ascii=False),
                ),
            )
        return self.get_investigator(investigator_id, owner_profile_id)

    def _create_revision(
        self,
        investigator_id: str,
        canonical_sheet: dict[str, Any],
        *,
        revision_no: int,
        source_type: str,
        source_hash: str | None,
        template_id: str | None,
        parser_version: str | None,
        warnings: list[str] | None = None,
    ) -> dict:
        revision_id = new_id("charrev")
        identity = canonical_sheet.get("identity") or {}
        derived = canonical_sheet.get("derived") or {}
        assets = canonical_sheet.get("assets") or {}
        characteristics = canonical_sheet.get("characteristics") or {}
        public_summary = {
            "name": identity.get("name"),
            "occupation": identity.get("occupation"),
            "era": identity.get("era"),
            "age": identity.get("age"),
            "mov": derived.get("mov"),
            "cash": assets.get("cash"),
            "credit_rating": assets.get("credit_rating"),
            "attributes": {
                key: characteristics.get(key)
                for key in ("str", "con", "siz", "dex", "app", "int", "pow", "edu")
            },
        }
        self.connection.execute(
            """
            INSERT INTO investigator_revisions
              (id, investigator_id, revision_no, canonical_json, public_summary_json,
               source_type, source_hash, template_id, parser_version, warnings_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision_id,
                investigator_id,
                revision_no,
                json.dumps(canonical_sheet, ensure_ascii=False),
                json.dumps(public_summary, ensure_ascii=False),
                source_type,
                source_hash,
                template_id,
                parser_version,
                json.dumps(warnings or [], ensure_ascii=False),
            ),
        )
        return self.get_investigator_revision(revision_id)

    def get_investigator_revision(self, revision_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM investigator_revisions WHERE id = ?", (revision_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Investigator revision not found: {revision_id}")
        result = row_to_dict(row)
        result["canonical_sheet"] = decode_json_field(result.pop("canonical_json"), {})
        result["public_summary"] = decode_json_field(result.pop("public_summary_json"), {})
        result["warnings"] = decode_json_field(result.pop("warnings_json", "[]"), [])
        return result

    def get_investigator(self, investigator_id: str, owner_profile_id: str) -> dict:
        row = self.connection.execute(
            """
            SELECT * FROM investigators
            WHERE id = ? AND owner_profile_id = ? AND archived_at IS NULL
            """,
            (investigator_id, owner_profile_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"Investigator not found: {investigator_id}")
        result = row_to_dict(row)
        result["current_revision"] = self.get_investigator_revision(result["current_revision_id"])
        return result

    def list_investigators(self, owner_profile_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT id FROM investigators
            WHERE owner_profile_id = ? AND archived_at IS NULL
            ORDER BY updated_at DESC, id
            """,
            (owner_profile_id,),
        ).fetchall()
        return [self.get_investigator(str(row["id"]), owner_profile_id) for row in rows]

    def add_investigator_revision(
        self,
        investigator_id: str,
        owner_profile_id: str,
        canonical_sheet: dict[str, Any],
        *,
        source_type: str,
        source_hash: str | None = None,
        source_filename: str | None = None,
        template_id: str | None = None,
        parser_version: str | None = None,
        warnings: list[str] | None = None,
    ) -> dict:
        self.get_investigator(investigator_id, owner_profile_id)
        row = self.connection.execute(
            """
            SELECT COALESCE(MAX(revision_no), 0) AS revision_no
            FROM investigator_revisions WHERE investigator_id = ?
            """,
            (investigator_id,),
        ).fetchone()
        revision = self._create_revision(
            investigator_id,
            canonical_sheet,
            revision_no=int(row["revision_no"]) + 1,
            source_type=source_type,
            source_hash=source_hash,
            template_id=template_id,
            parser_version=parser_version,
            warnings=warnings,
        )
        name = str((canonical_sheet.get("identity") or {}).get("name") or "").strip()
        if not name:
            raise ValueError("Investigator name is required before saving")
        self.connection.execute(
            """
            UPDATE investigators
            SET name = ?, current_revision_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (name, revision["id"], investigator_id),
        )
        if source_type == "xlsx":
            if not all((source_hash, source_filename, template_id, parser_version)):
                raise ValueError("Excel provenance is incomplete")
            self.connection.execute(
                """
                INSERT INTO character_imports
                  (id, owner_profile_id, investigator_id, revision_id, source_filename,
                   source_hash, template_id, parser_version, warnings_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("charimport"),
                    owner_profile_id,
                    investigator_id,
                    revision["id"],
                    source_filename,
                    source_hash,
                    template_id,
                    parser_version,
                    json.dumps(warnings or [], ensure_ascii=False),
                ),
            )
        return self.get_investigator(investigator_id, owner_profile_id)

    def list_investigator_revisions(
        self, investigator_id: str, owner_profile_id: str
    ) -> list[dict]:
        self.get_investigator(investigator_id, owner_profile_id)
        rows = self.connection.execute(
            """
            SELECT id FROM investigator_revisions
            WHERE investigator_id = ? ORDER BY revision_no DESC
            """,
            (investigator_id,),
        ).fetchall()
        return [self.get_investigator_revision(str(row["id"])) for row in rows]

    def submit_investigator_to_campaign(
        self,
        *,
        campaign_id: str,
        investigator_id: str,
        revision_id: str,
        owner_profile_id: str,
        member_id: str,
        session_id: str,
    ) -> dict:
        investigator = self.get_investigator(investigator_id, owner_profile_id)
        revision = self.get_investigator_revision(revision_id)
        if revision["investigator_id"] != investigator_id:
            raise ValueError("Revision does not belong to this investigator")
        member = self.get_session_member(member_id)
        if (
            member["session_id"] != session_id
            or member["campaign_id"] != campaign_id
            or member["role"] != "player"
            or member["revoked_at"] is not None
        ):
            raise ValueError("Only an active player member can submit an investigator")
        linked_profile = member.get("player_profile_id")
        if linked_profile is not None and linked_profile != owner_profile_id:
            raise ValueError("This session seat is linked to another player profile")
        self.connection.execute(
            "UPDATE session_members SET player_profile_id = ? WHERE id = ?",
            (owner_profile_id, member_id),
        )
        self.connection.execute(
            """
            INSERT INTO campaign_investigators
              (campaign_id, investigator_id, owner_profile_id, submitted_revision_id,
               status, review_comment, reviewed_by_member_id, reviewed_at)
            VALUES (?, ?, ?, ?, 'submitted', NULL, NULL, NULL)
            ON CONFLICT(campaign_id, investigator_id) DO UPDATE SET
              owner_profile_id = excluded.owner_profile_id,
              submitted_revision_id = excluded.submitted_revision_id,
              status = 'submitted',
              review_comment = NULL,
              reviewed_by_member_id = NULL,
              reviewed_at = NULL,
              updated_at = CURRENT_TIMESTAMP
            """,
            (campaign_id, investigator_id, owner_profile_id, revision_id),
        )
        self._append_character_review(
            campaign_id=campaign_id,
            investigator_id=investigator_id,
            revision_id=revision_id,
            action="submitted",
            actor_member_id=member_id,
            actor_profile_id=owner_profile_id,
        )
        self.append_realtime_event(
            session_id=session_id,
            campaign_id=campaign_id,
            audience="kp",
            event_type="investigator.submitted",
            resource_type="investigator",
            resource_id=investigator_id,
            payload={"revision_id": revision_id, "name": investigator["name"]},
        )
        return self.get_campaign_investigator(campaign_id, investigator_id)

    def _append_character_review(
        self,
        *,
        campaign_id: str,
        investigator_id: str,
        revision_id: str,
        action: str,
        actor_member_id: str | None = None,
        actor_profile_id: str | None = None,
        comment: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO character_reviews
              (id, campaign_id, investigator_id, revision_id, action,
               actor_member_id, actor_profile_id, comment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id("charreview"),
                campaign_id,
                investigator_id,
                revision_id,
                action,
                actor_member_id,
                actor_profile_id,
                comment,
            ),
        )

    def _decode_campaign_state(self, row: Any) -> dict:
        result = row_to_dict(row)
        result["conditions"] = decode_json_field(result.pop("conditions_json"), [])
        result["inventory_delta"] = decode_json_field(
            result.pop("inventory_delta_json"), {}
        )
        return result

    def get_campaign_investigator(self, campaign_id: str, investigator_id: str) -> dict:
        row = self.connection.execute(
            """
            SELECT ci.*, i.name, i.ruleset_id
            FROM campaign_investigators ci
            JOIN investigators i ON i.id = ci.investigator_id
            WHERE ci.campaign_id = ? AND ci.investigator_id = ?
            """,
            (campaign_id, investigator_id),
        ).fetchone()
        if row is None:
            raise KeyError(f"Campaign investigator not found: {investigator_id}")
        result = row_to_dict(row)
        submitted_id = result.get("submitted_revision_id")
        approved_id = result.get("approved_revision_id")
        result["submitted_revision"] = (
            self.get_investigator_revision(str(submitted_id)) if submitted_id else None
        )
        result["approved_revision"] = (
            self.get_investigator_revision(str(approved_id)) if approved_id else None
        )
        state_row = self.connection.execute(
            """
            SELECT * FROM investigator_campaign_state
            WHERE campaign_id = ? AND investigator_id = ?
            """,
            (campaign_id, investigator_id),
        ).fetchone()
        result["campaign_state"] = (
            self._decode_campaign_state(state_row) if state_row is not None else None
        )
        reviews = self.connection.execute(
            """
            SELECT * FROM character_reviews
            WHERE campaign_id = ? AND investigator_id = ?
            ORDER BY created_at, id
            """,
            (campaign_id, investigator_id),
        ).fetchall()
        result["reviews"] = [row_to_dict(item) for item in reviews]
        return result

    def list_campaign_investigators(
        self, campaign_id: str, owner_profile_id: str | None = None
    ) -> list[dict]:
        query = "SELECT investigator_id FROM campaign_investigators WHERE campaign_id = ?"
        params: list[str] = [campaign_id]
        if owner_profile_id is not None:
            query += " AND owner_profile_id = ?"
            params.append(owner_profile_id)
        query += " ORDER BY updated_at DESC, investigator_id"
        rows = self.connection.execute(query, params).fetchall()
        return [
            self.get_campaign_investigator(campaign_id, str(row["investigator_id"]))
            for row in rows
        ]

    def review_campaign_investigator(
        self,
        *,
        campaign_id: str,
        investigator_id: str,
        action: str,
        comment: str | None,
        kp_member_id: str,
        session_id: str,
    ) -> dict:
        record = self.get_campaign_investigator(campaign_id, investigator_id)
        if record["status"] != "submitted" or not record["submitted_revision_id"]:
            raise ValueError("Only a submitted investigator revision can be reviewed")
        if action not in {"approved", "changes_requested"}:
            raise ValueError("Unsupported investigator review action")
        normalized_comment = (comment or "").strip()
        if action == "changes_requested" and not normalized_comment:
            raise ValueError("A change request must include a player-visible comment")
        revision_id = str(record["submitted_revision_id"])
        legacy_pc_id = record.get("legacy_pc_id")
        if action == "approved":
            revision = self.get_investigator_revision(revision_id)
            canonical = revision["canonical_sheet"]
            projected_sheet = {
                **canonical,
                "public_summary": revision["public_summary"],
            }
            if legacy_pc_id:
                self.connection.execute(
                    """
                    UPDATE player_characters SET name = ?, sheet_json = ? WHERE id = ?
                    """,
                    (
                        record["name"],
                        json.dumps(projected_sheet, ensure_ascii=False),
                        legacy_pc_id,
                    ),
                )
            else:
                legacy_pc_id = new_id("pc")
                self.connection.execute(
                    """
                    INSERT INTO player_characters (id, campaign_id, name, sheet_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        legacy_pc_id,
                        campaign_id,
                        record["name"],
                        json.dumps(projected_sheet, ensure_ascii=False),
                    ),
                )
            derived = canonical.get("derived") or {}
            characteristics = canonical.get("characteristics") or {}
            self.connection.execute(
                """
                INSERT INTO investigator_campaign_state
                  (campaign_id, investigator_id, approved_revision_id, current_hp,
                   current_san, current_mp, current_luck, current_game_time)
                VALUES (?, ?, ?, ?, ?, ?, ?,
                        (SELECT current_time FROM campaigns WHERE id = ?))
                ON CONFLICT(campaign_id, investigator_id) DO UPDATE SET
                  approved_revision_id = excluded.approved_revision_id,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (
                    campaign_id,
                    investigator_id,
                    revision_id,
                    max(0, int(derived.get("max_hp") or 0)),
                    max(0, int(derived.get("initial_san") or 0)),
                    max(0, int(derived.get("max_mp") or 0)),
                    max(0, int(characteristics.get("luck") or 0)),
                    campaign_id,
                ),
            )
            self.connection.execute(
                """
                UPDATE campaign_investigators
                SET approved_revision_id = ?, legacy_pc_id = ?, status = 'approved',
                    review_comment = ?, reviewed_by_member_id = ?,
                    reviewed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE campaign_id = ? AND investigator_id = ?
                """,
                (
                    revision_id,
                    legacy_pc_id,
                    normalized_comment or None,
                    kp_member_id,
                    campaign_id,
                    investigator_id,
                ),
            )
        else:
            self.connection.execute(
                """
                UPDATE campaign_investigators
                SET status = 'changes_requested', review_comment = ?,
                    reviewed_by_member_id = ?, reviewed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE campaign_id = ? AND investigator_id = ?
                """,
                (normalized_comment, kp_member_id, campaign_id, investigator_id),
            )
        self._append_character_review(
            campaign_id=campaign_id,
            investigator_id=investigator_id,
            revision_id=revision_id,
            action=action,
            actor_member_id=kp_member_id,
            comment=normalized_comment or None,
        )
        owner_member = self.connection.execute(
            """
            SELECT id FROM session_members
            WHERE session_id = ? AND player_profile_id = ? AND revoked_at IS NULL
            ORDER BY joined_at DESC LIMIT 1
            """,
            (session_id, record["owner_profile_id"]),
        ).fetchone()
        self.append_realtime_event(
            session_id=session_id,
            campaign_id=campaign_id,
            audience="member" if owner_member is not None else "kp",
            member_id=str(owner_member["id"]) if owner_member is not None else None,
            event_type=f"investigator.{action}",
            resource_type="investigator",
            resource_id=investigator_id,
            payload={"revision_id": revision_id, "comment": normalized_comment},
        )
        return self.get_campaign_investigator(campaign_id, investigator_id)

    def assign_approved_investigator(
        self, *, session_id: str, member_id: str, investigator_id: str
    ) -> dict:
        member = self.get_session_member(member_id)
        if (
            member["session_id"] != session_id
            or member["role"] != "player"
            or member["revoked_at"] is not None
        ):
            raise ValueError("Target must be an active player in this session")
        record = self.get_campaign_investigator(member["campaign_id"], investigator_id)
        if not record.get("approved_revision_id") or not record.get("legacy_pc_id"):
            raise ValueError("Only an approved investigator can be bound to a player")
        if (
            member.get("player_profile_id") is not None
            and member["player_profile_id"] != record["owner_profile_id"]
        ):
            raise ValueError("Investigator belongs to another player profile")
        assigned = self.connection.execute(
            """
            SELECT id FROM session_members
            WHERE session_id = ? AND pc_id = ? AND revoked_at IS NULL AND id != ?
            """,
            (session_id, record["legacy_pc_id"], member_id),
        ).fetchone()
        if assigned is not None:
            raise ValueError("Investigator is already controlled by another active member")
        self.connection.execute(
            """
            UPDATE session_members SET pc_id = ?, player_profile_id = ? WHERE id = ?
            """,
            (record["legacy_pc_id"], record["owner_profile_id"], member_id),
        )
        self.append_realtime_event(
            session_id=session_id,
            campaign_id=str(member["campaign_id"]),
            audience="member",
            member_id=member_id,
            event_type="session.pc_assigned",
            resource_type="session_member",
            resource_id=member_id,
            payload={"pc_id": record["legacy_pc_id"], "investigator_id": investigator_id},
        )
        return self.get_session_member(member_id)

    def list_public_campaign_investigators(self, campaign_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT ci.investigator_id, ci.legacy_pc_id, ir.public_summary_json
            FROM campaign_investigators ci
            JOIN investigator_revisions ir ON ir.id = ci.approved_revision_id
            WHERE ci.campaign_id = ? AND ci.approved_revision_id IS NOT NULL
            ORDER BY ci.updated_at, ci.investigator_id
            """,
            (campaign_id,),
        ).fetchall()
        return [
            {
                "investigator_id": str(row["investigator_id"]),
                "pc_id": str(row["legacy_pc_id"]) if row["legacy_pc_id"] else None,
                "public_summary": decode_json_field(row["public_summary_json"], {}),
            }
            for row in rows
        ]

    def update_investigator_campaign_state(
        self,
        *,
        campaign_id: str,
        investigator_id: str,
        expected_version: int,
        changes: dict[str, Any],
    ) -> dict:
        record = self.get_campaign_investigator(campaign_id, investigator_id)
        state = record.get("campaign_state")
        approved = record.get("approved_revision")
        if state is None or approved is None:
            raise ValueError("Investigator has no approved campaign state")
        canonical = approved["canonical_sheet"]
        derived = canonical.get("derived") or {}
        limits = {
            "current_hp": max(0, int(derived.get("max_hp") or 0)),
            "current_san": max(0, int(derived.get("max_san", 99))),
            "current_mp": max(0, int(derived.get("max_mp") or 0)),
            "current_luck": 99,
        }
        assignments: list[str] = []
        params: list[Any] = []
        for key, limit in limits.items():
            if key not in changes or changes[key] is None:
                continue
            value = int(changes[key])
            if not 0 <= value <= limit:
                raise ValueError(f"{key} must be between 0 and {limit}")
            assignments.append(f"{key} = ?")
            params.append(value)
        for key, column, default in (
            ("conditions", "conditions_json", []),
            ("inventory_delta", "inventory_delta_json", {}),
        ):
            if key in changes and changes[key] is not None:
                value = changes[key]
                if not isinstance(value, type(default)):
                    raise ValueError(f"{key} has an invalid shape")
                assignments.append(f"{column} = ?")
                params.append(json.dumps(value, ensure_ascii=False))
        if "current_game_time" in changes and changes["current_game_time"] is not None:
            assignments.append("current_game_time = ?")
            params.append(str(changes["current_game_time"]))
        if not assignments:
            return state
        assignments.extend(
            ["state_version = state_version + 1", "updated_at = CURRENT_TIMESTAMP"]
        )
        params.extend([campaign_id, investigator_id, expected_version])
        updated = self.connection.execute(
            f"""
            UPDATE investigator_campaign_state SET {', '.join(assignments)}
            WHERE campaign_id = ? AND investigator_id = ? AND state_version = ?
            """,
            params,
        )
        if updated.rowcount != 1:
            raise ValueError("Investigator campaign state changed; refresh and retry")
        row = self.connection.execute(
            """
            SELECT * FROM investigator_campaign_state
            WHERE campaign_id = ? AND investigator_id = ?
            """,
            (campaign_id, investigator_id),
        ).fetchone()
        return self._decode_campaign_state(row)

"""Stable-investigator encounter history and safe NPC reappearance queries."""

from __future__ import annotations

import json

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class NpcReappearanceRepository(SQLiteRepository):
    """Persist encounter provenance and derive relationship-authorized candidates."""

    def require_approved_contact_investigators(
        self,
        campaign_id: str,
        investigator_ids: tuple[str, ...],
    ) -> list[dict]:
        if not investigator_ids:
            return []
        placeholders = ",".join("?" for _ in investigator_ids)
        rows = self.connection.execute(
            f"""
            SELECT ci.investigator_id, ci.owner_profile_id, i.name
            FROM campaign_investigators ci
            JOIN investigators i ON i.id = ci.investigator_id
            WHERE ci.campaign_id = ?
              AND ci.status = 'approved'
              AND ci.approved_revision_id IS NOT NULL
              AND ci.investigator_id IN ({placeholders})
            ORDER BY ci.investigator_id
            """,
            (campaign_id, *investigator_ids),
        ).fetchall()
        found = {str(row["investigator_id"]) for row in rows}
        if found != set(investigator_ids):
            raise ValueError(
                "Every contact participant must be an approved investigator in this campaign"
            )
        return [row_to_dict(row) for row in rows]

    def npc_is_linked_to_campaign(self, campaign_id: str, npc_id: str) -> bool:
        row = self.connection.execute(
            """
            SELECT 1 FROM campaign_npcs
            WHERE campaign_id = ? AND npc_id = ?
            """,
            (campaign_id, npc_id),
        ).fetchone()
        return row is not None

    def npc_is_authorized_reappearance(
        self,
        campaign_id: str,
        npc_id: str,
        investigator_ids: tuple[str, ...],
    ) -> bool:
        if not investigator_ids:
            return False
        placeholders = ",".join("?" for _ in investigator_ids)
        row = self.connection.execute(
            f"""
            SELECT 1
            FROM investigator_npc_encounters ine
            JOIN campaign_investigators ci
              ON ci.investigator_id = ine.investigator_id
             AND ci.campaign_id = ?
             AND ci.status = 'approved'
             AND ci.approved_revision_id IS NOT NULL
            WHERE ine.npc_id = ?
              AND ine.campaign_id != ?
              AND ine.investigator_id IN ({placeholders})
            LIMIT 1
            """,
            (campaign_id, npc_id, campaign_id, *investigator_ids),
        ).fetchone()
        return row is not None

    def record_investigator_npc_encounter(
        self,
        *,
        investigator_id: str,
        npc_id: str,
        campaign_id: str,
        source_event_id: str,
        materialization_id: str | None,
        interaction_summary: str,
        happened_at: str | None,
    ) -> dict:
        encounter_id = new_id("npcenc")
        self.connection.execute(
            """
            INSERT INTO investigator_npc_encounters
              (id, investigator_id, npc_id, campaign_id, source_event_id,
               materialization_id, interaction_summary, happened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                encounter_id,
                investigator_id,
                npc_id,
                campaign_id,
                source_event_id,
                materialization_id,
                interaction_summary,
                happened_at,
            ),
        )
        row = self.connection.execute(
            "SELECT * FROM investigator_npc_encounters WHERE id = ?",
            (encounter_id,),
        ).fetchone()
        return row_to_dict(row)

    def list_npc_reappearance_candidate_rows(
        self,
        campaign_id: str,
    ) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT n.id AS npc_id, n.name, n.home_location, n.profession,
                   ine.investigator_id, i.name AS investigator_name,
                   ine.interaction_summary, ine.happened_at, ine.created_at
            FROM campaign_investigators ci
            JOIN investigators i ON i.id = ci.investigator_id
            JOIN investigator_npc_encounters ine
              ON ine.investigator_id = ci.investigator_id
             AND ine.campaign_id != ci.campaign_id
            JOIN npcs n ON n.id = ine.npc_id
            WHERE ci.campaign_id = ?
              AND ci.status = 'approved'
              AND ci.approved_revision_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM campaign_npcs current_link
                WHERE current_link.campaign_id = ci.campaign_id
                  AND current_link.npc_id = n.id
              )
            ORDER BY n.name COLLATE NOCASE, n.id,
                     ine.created_at DESC, ine.id DESC
            """,
            (campaign_id,),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def get_npc_availability_profile(self, npc_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM npc_availability_profiles WHERE npc_id = ?",
            (npc_id,),
        ).fetchone()
        if row is None:
            return None
        result = row_to_dict(row)
        result["location_tags"] = json.loads(result.pop("location_tags_json"))
        result["profession_tags"] = json.loads(result.pop("profession_tags_json"))
        return result

    def save_npc_availability_profile(self, npc_id: str, **values) -> dict:
        self.get_npc(npc_id)
        self.connection.execute(
            """
            INSERT INTO npc_availability_profiles
              (npc_id, lifecycle_state, born_year, died_year,
               active_from_year, active_until_year, location_tags_json,
               profession_tags_json, kp_notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(npc_id) DO UPDATE SET
              lifecycle_state = excluded.lifecycle_state,
              born_year = excluded.born_year,
              died_year = excluded.died_year,
              active_from_year = excluded.active_from_year,
              active_until_year = excluded.active_until_year,
              location_tags_json = excluded.location_tags_json,
              profession_tags_json = excluded.profession_tags_json,
              kp_notes = excluded.kp_notes,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                npc_id,
                values["lifecycle_state"],
                values.get("born_year"),
                values.get("died_year"),
                values.get("active_from_year"),
                values.get("active_until_year"),
                json.dumps(values.get("location_tags", []), ensure_ascii=False),
                json.dumps(values.get("profession_tags", []), ensure_ascii=False),
                values.get("kp_notes", ""),
            ),
        )
        profile = self.get_npc_availability_profile(npc_id)
        assert profile is not None
        return profile

    def get_campaign_npc_reappearance_policy(self, campaign_id: str) -> dict:
        self.get_campaign(campaign_id)
        row = self.connection.execute(
            """
            SELECT * FROM campaign_npc_reappearance_policies
            WHERE campaign_id = ?
            """,
            (campaign_id,),
        ).fetchone()
        if row is None:
            return {
                "campaign_id": campaign_id,
                "max_returning_npcs": 1,
                "require_location_match": False,
                "require_profession_match": False,
                "max_travel_minutes": 1440,
                "updated_at": None,
            }
        result = row_to_dict(row)
        result["require_location_match"] = bool(result["require_location_match"])
        result["require_profession_match"] = bool(
            result["require_profession_match"]
        )
        return result

    def save_campaign_npc_reappearance_policy(
        self,
        campaign_id: str,
        **values,
    ) -> dict:
        self.get_campaign(campaign_id)
        self.connection.execute(
            """
            INSERT INTO campaign_npc_reappearance_policies
              (campaign_id, max_returning_npcs, require_location_match,
               require_profession_match, max_travel_minutes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id) DO UPDATE SET
              max_returning_npcs = excluded.max_returning_npcs,
              require_location_match = excluded.require_location_match,
              require_profession_match = excluded.require_profession_match,
              max_travel_minutes = excluded.max_travel_minutes,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                campaign_id,
                values["max_returning_npcs"],
                int(values["require_location_match"]),
                int(values["require_profession_match"]),
                values.get("max_travel_minutes", 1440),
            ),
        )
        return self.get_campaign_npc_reappearance_policy(campaign_id)

    def list_campaign_npcs_with_profiles(self, campaign_id: str) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT n.id, n.name, n.home_location, n.profession, n.public_notes,
                   cn.role, cn.first_seen_time, cn.last_seen_time,
                   cn.relationship_score, cn.notes AS campaign_notes
            FROM campaign_npcs cn
            JOIN npcs n ON n.id = cn.npc_id
            WHERE cn.campaign_id = ?
            ORDER BY n.name COLLATE NOCASE, n.id
            """,
            (campaign_id,),
        ).fetchall()
        results = []
        for row in rows:
            result = row_to_dict(row)
            result["availability_profile"] = self.get_npc_availability_profile(
                str(result["id"])
            )
            results.append(result)
        return results

    def count_npc_reappearances(self, campaign_id: str) -> int:
        row = self.connection.execute(
            """
            SELECT COUNT(*) FROM npc_reappearance_appearances
            WHERE campaign_id = ?
            """,
            (campaign_id,),
        ).fetchone()
        return int(row[0])

    def record_npc_reappearance(
        self,
        *,
        campaign_id: str,
        npc_id: str,
        materialization_id: str,
        appeared_at: str | None,
    ) -> dict:
        appearance_id = new_id("npcapp")
        self.connection.execute(
            """
            INSERT INTO npc_reappearance_appearances
              (id, campaign_id, npc_id, materialization_id, appeared_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                appearance_id,
                campaign_id,
                npc_id,
                materialization_id,
                appeared_at,
            ),
        )
        row = self.connection.execute(
            "SELECT * FROM npc_reappearance_appearances WHERE id = ?",
            (appearance_id,),
        ).fetchone()
        return row_to_dict(row)


__all__ = ["NpcReappearanceRepository"]

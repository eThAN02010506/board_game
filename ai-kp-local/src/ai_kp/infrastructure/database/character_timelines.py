"""Cross-campaign investigator continuity and permanent milestone persistence."""

import hashlib
import json
from typing import Any

from ai_kp.core.ids import new_id
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.sqlite import SQLiteRepository


class CharacterTimelineRepository(SQLiteRepository):
    def ensure_primary_timeline_branch(self, investigator_id: str) -> dict:
        row = self.connection.execute(
            """
            SELECT * FROM investigator_timeline_branches
            WHERE investigator_id = ? AND is_primary = 1
            """,
            (investigator_id,),
        ).fetchone()
        if row is not None:
            return self._decode_branch(row)
        branch_id = new_id("timeline")
        self.connection.execute(
            """
            INSERT INTO investigator_timeline_branches
              (id, investigator_id, label, is_primary)
            VALUES (?, ?, '主时间线', 1)
            """,
            (branch_id, investigator_id),
        )
        return self.get_timeline_branch(branch_id)

    def create_timeline_branch(
        self,
        investigator_id: str,
        owner_profile_id: str,
        label: str,
    ) -> dict:
        self.get_investigator(investigator_id, owner_profile_id)
        normalized = label.strip()
        if not normalized:
            raise ValueError("Timeline branch label is required")
        branch_id = new_id("timeline")
        self.connection.execute(
            """
            INSERT INTO investigator_timeline_branches
              (id, investigator_id, label, is_primary)
            VALUES (?, ?, ?, 0)
            """,
            (branch_id, investigator_id, normalized),
        )
        return self.get_timeline_branch(branch_id)

    def get_timeline_branch(self, branch_id: str) -> dict:
        row = self.connection.execute(
            "SELECT * FROM investigator_timeline_branches WHERE id = ?",
            (branch_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Investigator timeline branch not found: {branch_id}")
        return self._decode_branch(row)

    def list_timeline_branches(self, investigator_id: str) -> list[dict]:
        self.ensure_primary_timeline_branch(investigator_id)
        rows = self.connection.execute(
            """
            SELECT * FROM investigator_timeline_branches
            WHERE investigator_id = ?
            ORDER BY is_primary DESC, created_at, id
            """,
            (investigator_id,),
        ).fetchall()
        return [self._decode_branch(row) for row in rows]

    def select_timeline_branch(
        self,
        investigator_id: str,
        owner_profile_id: str,
        branch_id: str | None,
    ) -> dict:
        self.get_investigator(investigator_id, owner_profile_id)
        branch = (
            self.get_timeline_branch(branch_id)
            if branch_id
            else self.ensure_primary_timeline_branch(investigator_id)
        )
        if (
            branch["investigator_id"] != investigator_id
            or branch["status"] != "active"
        ):
            raise ValueError("Timeline branch is not active for this investigator")
        return branch

    def ensure_timeline_participation(
        self,
        *,
        campaign_id: str,
        session_id: str,
        investigator_id: str,
        branch_id: str,
        approved_revision_id: str,
        legacy_pc_id: str | None,
    ) -> dict:
        branch = self.get_timeline_branch(branch_id)
        if branch["investigator_id"] != investigator_id:
            raise ValueError("Timeline branch belongs to another investigator")
        existing = self.connection.execute(
            """
            SELECT * FROM investigator_campaign_participations
            WHERE session_id = ? AND investigator_id = ?
            """,
            (session_id, investigator_id),
        ).fetchone()
        if existing is not None:
            participation = row_to_dict(existing)
            if participation["branch_id"] != branch_id:
                raise ValueError("This session already uses another timeline branch")
            self.connection.execute(
                """
                UPDATE investigator_campaign_participations
                SET approved_revision_id = ?, legacy_pc_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (approved_revision_id, legacy_pc_id, participation["id"]),
            )
            return self.get_timeline_participation(str(participation["id"]))
        active = self.connection.execute(
            """
            SELECT participation.id, participation.session_id, campaign.title
            FROM investigator_campaign_participations participation
            JOIN campaigns campaign ON campaign.id = participation.campaign_id
            WHERE participation.branch_id = ? AND participation.status = 'active'
            """,
            (branch_id,),
        ).fetchone()
        if active is not None:
            raise ValueError(
                "Timeline branch already has an active timeline participation "
                f"in {active['title']} ({active['session_id']})"
            )
        participation_id = new_id("participation")
        self.connection.execute(
            """
            INSERT INTO investigator_campaign_participations
              (id, branch_id, investigator_id, campaign_id, session_id,
               approved_revision_id, legacy_pc_id, world_started_at)
            VALUES (
              ?, ?, ?, ?, ?, ?, ?,
              (SELECT current_time FROM campaigns WHERE id = ?)
            )
            """,
            (
                participation_id,
                branch_id,
                investigator_id,
                campaign_id,
                session_id,
                approved_revision_id,
                legacy_pc_id,
                campaign_id,
            ),
        )
        return self.get_timeline_participation(participation_id)

    def get_timeline_participation(self, participation_id: str) -> dict:
        row = self.connection.execute(
            """
            SELECT participation.*, campaign.title AS campaign_title,
                   session.title AS session_title
            FROM investigator_campaign_participations participation
            JOIN campaigns campaign ON campaign.id = participation.campaign_id
            JOIN campaign_sessions session ON session.id = participation.session_id
            WHERE participation.id = ?
            """,
            (participation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Timeline participation not found: {participation_id}")
        return row_to_dict(row)

    def complete_session_timeline_participations(self, session_id: str) -> None:
        self.connection.execute(
            """
            UPDATE investigator_campaign_participations
            SET status = 'completed',
                world_ended_at = COALESCE(
                  (SELECT current_time FROM campaigns
                   WHERE id = investigator_campaign_participations.campaign_id),
                  world_ended_at
                ),
                ended_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ? AND status = 'active'
            """,
            (session_id,),
        )

    def create_permanent_change_proposal(
        self,
        *,
        campaign_id: str,
        investigator_id: str,
        kind: str,
        summary: str,
        change: dict[str, Any],
        source_event_id: str,
        rationale: str,
        proposed_by_member_id: str,
    ) -> dict:
        record = self.get_campaign_investigator(campaign_id, investigator_id)
        if record["status"] != "approved" or not record.get("timeline_branch_id"):
            raise ValueError("Permanent changes require an approved timeline investigator")
        event = self.connection.execute(
            "SELECT id FROM events WHERE id = ? AND campaign_id = ?",
            (source_event_id, campaign_id),
        ).fetchone()
        if event is None:
            raise ValueError("Permanent change source event must belong to this campaign")
        investigator = self.connection.execute(
            "SELECT current_revision_id FROM investigators WHERE id = ?",
            (investigator_id,),
        ).fetchone()
        if investigator is None or not investigator["current_revision_id"]:
            raise KeyError(f"Investigator not found: {investigator_id}")
        proposal_id = new_id("permanent_change")
        self.connection.execute(
            """
            INSERT INTO investigator_permanent_change_proposals
              (id, investigator_id, campaign_id, branch_id, base_revision_id,
               source_event_id, kind, summary, change_json, rationale,
               proposed_by_member_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                proposal_id,
                investigator_id,
                campaign_id,
                record["timeline_branch_id"],
                investigator["current_revision_id"],
                source_event_id,
                kind,
                summary.strip(),
                json.dumps(change, ensure_ascii=False, sort_keys=True),
                rationale.strip(),
                proposed_by_member_id,
            ),
        )
        return self.get_permanent_change_proposal(proposal_id)

    def get_permanent_change_proposal(self, proposal_id: str) -> dict:
        row = self.connection.execute(
            """
            SELECT proposal.*, campaign.title AS campaign_title,
                   investigator.owner_profile_id
            FROM investigator_permanent_change_proposals proposal
            JOIN campaigns campaign ON campaign.id = proposal.campaign_id
            JOIN investigators investigator ON investigator.id = proposal.investigator_id
            WHERE proposal.id = ?
            """,
            (proposal_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Permanent change proposal not found: {proposal_id}")
        return self._decode_permanent_change(row)

    def list_permanent_change_proposals(
        self,
        investigator_id: str,
        owner_profile_id: str,
    ) -> list[dict]:
        self.get_investigator(investigator_id, owner_profile_id)
        rows = self.connection.execute(
            """
            SELECT proposal.*, campaign.title AS campaign_title,
                   investigator.owner_profile_id
            FROM investigator_permanent_change_proposals proposal
            JOIN campaigns campaign ON campaign.id = proposal.campaign_id
            JOIN investigators investigator ON investigator.id = proposal.investigator_id
            WHERE proposal.investigator_id = ?
            ORDER BY proposal.created_at DESC, proposal.id DESC
            """,
            (investigator_id,),
        ).fetchall()
        return [self._decode_permanent_change(row) for row in rows]

    def decide_permanent_change(
        self,
        proposal_id: str,
        *,
        owner_profile_id: str,
        action: str,
        reason: str,
        expected_revision_id: str,
        canonical_sheet: dict[str, Any] | None,
        warnings: list[str],
    ) -> dict:
        self.begin_immediate()
        proposal = self.get_permanent_change_proposal(proposal_id)
        if proposal["owner_profile_id"] != owner_profile_id:
            raise KeyError(f"Permanent change proposal not found: {proposal_id}")
        decision_hash = hashlib.sha256(
            json.dumps(
                {
                    "action": action,
                    "reason": reason,
                    "expected_revision_id": expected_revision_id,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if proposal["status"] != "proposed":
            if (
                proposal["status"] == action
                and proposal.get("decision_hash") == decision_hash
            ):
                return proposal
            raise ValueError(
                f"Permanent change is already {proposal['status']}; refresh before deciding"
            )
        if proposal["base_revision_id"] != expected_revision_id:
            raise ValueError("Permanent change base revision does not match the request")
        current = self.connection.execute(
            "SELECT current_revision_id FROM investigators WHERE id = ?",
            (proposal["investigator_id"],),
        ).fetchone()
        if current is None or current["current_revision_id"] != expected_revision_id:
            raise ValueError(
                "Investigator revision changed after this proposal; regenerate the change"
            )

        resulting_revision_id = None
        if action == "accepted":
            if canonical_sheet is None:
                raise ValueError("Accepted permanent change requires a validated character sheet")
            row = self.connection.execute(
                """
                SELECT COALESCE(MAX(revision_no), 0) AS revision_no
                FROM investigator_revisions WHERE investigator_id = ?
                """,
                (proposal["investigator_id"],),
            ).fetchone()
            revision = self._create_revision(
                str(proposal["investigator_id"]),
                canonical_sheet,
                revision_no=int(row["revision_no"]) + 1,
                source_type="manual",
                source_hash=None,
                template_id=None,
                parser_version="permanent-change.v1",
                warnings=warnings,
                origin_type="milestone",
            )
            resulting_revision_id = revision["id"]
            name = str((canonical_sheet.get("identity") or {}).get("name") or "").strip()
            updated = self.connection.execute(
                """
                UPDATE investigators
                SET name = ?, current_revision_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND current_revision_id = ?
                """,
                (
                    name,
                    resulting_revision_id,
                    proposal["investigator_id"],
                    expected_revision_id,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("Investigator revision changed; refresh before accepting")

        updated = self.connection.execute(
            """
            UPDATE investigator_permanent_change_proposals
            SET status = ?, decided_by_profile_id = ?, decision_reason = ?,
                decision_hash = ?, resulting_revision_id = ?,
                decided_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'proposed'
            """,
            (
                action,
                owner_profile_id,
                reason,
                decision_hash,
                resulting_revision_id,
                proposal_id,
            ),
        )
        if updated.rowcount != 1:
            raise ValueError("Permanent change changed; refresh before deciding")
        return self.get_permanent_change_proposal(proposal_id)

    def get_character_timeline(self, investigator_id: str) -> dict:
        branches = self.list_timeline_branches(investigator_id)
        participations = [
            row_to_dict(row)
            for row in self.connection.execute(
                """
                SELECT participation.*, campaign.title AS campaign_title,
                       session.title AS session_title
                FROM investigator_campaign_participations participation
                JOIN campaigns campaign ON campaign.id = participation.campaign_id
                JOIN campaign_sessions session ON session.id = participation.session_id
                WHERE participation.investigator_id = ?
                ORDER BY participation.started_at, participation.id
                """,
                (investigator_id,),
            ).fetchall()
        ]
        mappings = self.connection.execute(
            """
            SELECT ci.campaign_id, ci.legacy_pc_id, campaign.title
            FROM campaign_investigators ci
            JOIN campaigns campaign ON campaign.id = ci.campaign_id
            WHERE ci.investigator_id = ? AND ci.legacy_pc_id IS NOT NULL
            ORDER BY ci.created_at, ci.campaign_id
            """,
            (investigator_id,),
        ).fetchall()
        memories: list[dict] = []
        for mapping in mappings:
            for item in self.list_memory_timeline(
                str(mapping["campaign_id"]),
                pc_id=str(mapping["legacy_pc_id"]),
                view="player",
                include_hidden=False,
                limit=250,
            ):
                item["campaign_title"] = str(mapping["title"])
                memories.append(item)
        memories.sort(
            key=lambda item: (str(item.get("effective_time") or ""), str(item["id"])),
            reverse=True,
        )
        memories = memories[:500]
        encounters = [
            row_to_dict(row)
            for row in self.connection.execute(
                """
                SELECT encounter.id, encounter.npc_id, npc.name AS npc_name,
                       encounter.campaign_id, campaign.title AS campaign_title,
                       encounter.interaction_summary, encounter.happened_at,
                       encounter.created_at
                FROM investigator_npc_encounters encounter
                JOIN npcs npc ON npc.id = encounter.npc_id
                JOIN campaigns campaign ON campaign.id = encounter.campaign_id
                JOIN events source_event ON source_event.id = encounter.source_event_id
                WHERE encounter.investigator_id = ?
                  AND source_event.visibility IN ('player', 'table')
                ORDER BY COALESCE(encounter.happened_at, encounter.created_at) DESC,
                         encounter.id DESC
                LIMIT 500
                """,
                (investigator_id,),
            ).fetchall()
        ]
        permanent_changes = [
            self._public_permanent_change(self._decode_permanent_change(row))
            for row in self.connection.execute(
                """
                SELECT proposal.*, campaign.title AS campaign_title,
                       investigator.owner_profile_id
                FROM investigator_permanent_change_proposals proposal
                JOIN campaigns campaign ON campaign.id = proposal.campaign_id
                JOIN investigators investigator
                  ON investigator.id = proposal.investigator_id
                WHERE proposal.investigator_id = ? AND proposal.status = 'accepted'
                ORDER BY proposal.decided_at, proposal.id
                """,
                (investigator_id,),
            ).fetchall()
        ]
        return {
            "investigator_id": investigator_id,
            "branches": branches,
            "participations": participations,
            "memories": memories,
            "npc_encounters": encounters,
            "permanent_changes": permanent_changes,
        }

    @staticmethod
    def _decode_branch(row: Any) -> dict:
        result = row_to_dict(row)
        result["is_primary"] = bool(result["is_primary"])
        return result

    @staticmethod
    def _decode_permanent_change(row: Any) -> dict:
        result = row_to_dict(row)
        result["change"] = decode_json_field(result.pop("change_json"), {})
        return result

    @staticmethod
    def _public_permanent_change(proposal: dict) -> dict:
        return {
            key: proposal.get(key)
            for key in (
                "id",
                "investigator_id",
                "campaign_id",
                "campaign_title",
                "branch_id",
                "base_revision_id",
                "source_event_id",
                "kind",
                "summary",
                "change",
                "status",
                "resulting_revision_id",
                "created_at",
                "decided_at",
            )
        }


__all__ = ["CharacterTimelineRepository"]

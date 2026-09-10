"""Read-only reconstruction of a strict multi-episode UI journey."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_kp.evaluation.continuity_checkpoint import (
    ContinuityCheckpointEvidence,
    reconstruct_continuity_checkpoint,
)
from ai_kp.evaluation.long_ui_journey_evidence import (
    LongUiJourneyEvidenceResult,
    verify_long_ui_journey_evidence,
)
from ai_kp.evaluation.ui_journey_reconstruction import (
    reconstruct_ui_journey_anchor_evidence,
)

_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
_SECRET_VALUE = re.compile(
    r"(?:\bBearer\s+[A-Za-z0-9._~+/=-]{8,}|\bsk-[A-Za-z0-9_-]{8,})",
    re.IGNORECASE,
)
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "cookie",
        "cookies",
        "headers",
        "invitation",
        "join_code",
        "password",
        "private_key",
        "secret",
        "storage",
        "token",
    }
)
_AUTHORITY_REF_KEYS = frozenset(
    {
        "campaign_ids",
        "session_ids",
        "episode_ids",
        "continuity_snapshot_ids",
        "member_ids",
        "investigator_ids",
        "module_import_job_ids",
        "module_ids",
        "module_run_ids",
        "scenario_contract_job_ids",
        "scenario_contract_version_ids",
        "action_ids",
        "adjudication_ids",
        "check_ids",
        "parallel_batch_ids",
        "public_turn_ids",
        "scenario_command_batch_ids",
    }
)


@dataclass(frozen=True)
class LongUiJourneyReconstructionResult:
    strict_evidence: dict[str, Any]
    gaps: tuple[str, ...]
    ignored_producer_claims: tuple[str, ...]
    verification: LongUiJourneyEvidenceResult

    @property
    def accepted(self) -> bool:
        """Require both a complete reconstruction and a passing strict envelope."""

        return not self.gaps and self.verification.accepted

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "strict_evidence": self.strict_evidence,
            "gaps": list(self.gaps),
            "ignored_producer_claims": list(self.ignored_producer_claims),
            "verification": self.verification.as_dict(),
        }


def reconstruct_long_ui_journey_evidence(
    playwright_evidence: Mapping[str, Any],
    database_path: Path,
) -> LongUiJourneyReconstructionResult:
    """Rebuild a long journey without trusting producer-owned pass claims."""

    if not isinstance(playwright_evidence, Mapping):
        raise TypeError("Playwright evidence must be an object")
    if _contains_secret(playwright_evidence):
        raise ValueError("Playwright evidence contains forbidden secret-bearing material")

    ignored_claims = tuple(
        sorted(
            key
            for key in playwright_evidence
            if isinstance(key, str) and key.endswith("_passed")
        )
    )
    gaps: list[str] = []
    strict: dict[str, Any] = {"schema_version": 1}
    source_commit = playwright_evidence.get("source_commit")
    if isinstance(source_commit, str) and _COMMIT.fullmatch(source_commit):
        strict["source_commit"] = source_commit
    else:
        gaps.append("Playwright evidence lacks the tested source_commit")
    completed_at = playwright_evidence.get("completed_at")
    if isinstance(completed_at, str) and completed_at:
        strict["produced_at"] = completed_at
    else:
        gaps.append("Playwright evidence lacks completed_at")

    refs = _parse_authority_refs(playwright_evidence.get("authority_refs"), gaps)
    connection = _open_read_only(database_path)
    try:
        _reconstruct_authority(connection, refs, strict, gaps)
        _reconstruct_restart(
            connection,
            playwright_evidence.get("process_restart"),
            strict,
            gaps,
        )
    finally:
        connection.close()

    ui_events = playwright_evidence.get("ui_corroboration_events")
    if isinstance(ui_events, list):
        strict["ui_corroboration"] = ui_events
    else:
        gaps.append("Playwright evidence lacks typed UI corroboration events")

    anchor_evidence = playwright_evidence.get("single_session_anchor_evidence")
    if isinstance(anchor_evidence, Mapping):
        anchor = reconstruct_ui_journey_anchor_evidence(
            anchor_evidence, database_path
        )
        strict["single_session_anchor"] = anchor.strict_evidence
        gaps.extend(f"single_session_anchor: {gap}" for gap in anchor.gaps)
    else:
        strict["single_session_anchor"] = {}
        gaps.append("Playwright evidence lacks scoped single_session_anchor_evidence")

    verification = verify_long_ui_journey_evidence(strict)
    return LongUiJourneyReconstructionResult(
        strict_evidence=strict,
        gaps=tuple(dict.fromkeys(gaps)),
        ignored_producer_claims=ignored_claims,
        verification=verification,
    )


def _reconstruct_authority(
    connection: sqlite3.Connection,
    refs: Mapping[str, tuple[str, ...]],
    strict: dict[str, Any],
    gaps: list[str],
) -> None:
    campaign_ids = refs.get("campaign_ids", ())
    session_ids = refs.get("session_ids", ())
    if len(campaign_ids) != 1 or len(session_ids) != 1:
        gaps.append("authority_refs must identify exactly one campaign and table Session")
        return
    campaign_id, session_id = campaign_ids[0], session_ids[0]
    campaign = connection.execute(
        """
        SELECT campaign.id, campaign.ruleset_id, campaign.ruleset_version,
               session.id AS session_id
        FROM campaigns campaign
        JOIN campaign_sessions session ON session.campaign_id = campaign.id
        WHERE campaign.id = ? AND session.id = ?
        """,
        (campaign_id, session_id),
    ).fetchone()
    if campaign is None:
        gaps.append("UI-observed campaign/Session authority relation does not exist")
        return
    strict.update(
        {
            "journey_id": _stable_id("long-ui-journey", campaign_id, session_id),
            "campaign_id": campaign_id,
            "table_session_id": session_id,
            "automation_mode": "full_ai",
            "ruleset": {
                "id": str(campaign["ruleset_id"]),
                "version": str(campaign["ruleset_version"]),
            },
        }
    )
    _reconstruct_players(connection, campaign_id, session_id, refs, strict, gaps)
    _reconstruct_module_runs(connection, campaign_id, refs, strict, gaps)
    _reconstruct_episodes(connection, campaign_id, session_id, refs, strict, gaps)


def _reconstruct_players(
    connection: sqlite3.Connection,
    campaign_id: str,
    session_id: str,
    refs: Mapping[str, tuple[str, ...]],
    strict: dict[str, Any],
    gaps: list[str],
) -> None:
    rows = connection.execute(
        """
        SELECT member.id AS member_id, investigator.investigator_id
        FROM session_members member
        JOIN campaign_investigators investigator
          ON investigator.campaign_id = member.campaign_id
         AND investigator.legacy_pc_id = member.pc_id
         AND investigator.status = 'approved'
        WHERE member.campaign_id = ? AND member.session_id = ?
          AND member.role = 'player' AND member.revoked_at IS NULL
        ORDER BY member.id
        """,
        (campaign_id, session_id),
    ).fetchall()
    member_ids = {str(row["member_id"]) for row in rows}
    investigator_ids = {str(row["investigator_id"]) for row in rows}
    observed_member_ids = set(refs.get("member_ids", ()))
    observed_investigator_ids = set(refs.get("investigator_ids", ()))
    referenced_members = (
        connection.execute(
            f"SELECT id FROM session_members WHERE campaign_id = ? AND session_id = ? "
            f"AND id IN ({_placeholders(tuple(observed_member_ids))})",
            (campaign_id, session_id, *observed_member_ids),
        ).fetchall()
        if observed_member_ids
        else []
    )
    referenced_investigators = (
        connection.execute(
            f"SELECT investigator_id FROM campaign_investigators "
            f"WHERE campaign_id = ? AND status = 'approved' "
            f"AND investigator_id IN ({_placeholders(tuple(observed_investigator_ids))})",
            (campaign_id, *observed_investigator_ids),
        ).fetchall()
        if observed_investigator_ids
        else []
    )
    if (
        len(rows) != 4
        or not member_ids.issubset(observed_member_ids)
        or not investigator_ids.issubset(observed_investigator_ids)
        or len(referenced_members) != len(observed_member_ids)
        or len(referenced_investigators) != len(observed_investigator_ids)
    ):
        gaps.append(
            "authority_refs do not prove exactly four final active players or contain foreign member/investigator IDs"
        )
        return
    strict["players"] = [
        {
            "member_id": str(row["member_id"]),
            "investigator_id": str(row["investigator_id"]),
        }
        for row in rows
    ]


def _reconstruct_module_runs(
    connection: sqlite3.Connection,
    campaign_id: str,
    refs: Mapping[str, tuple[str, ...]],
    strict: dict[str, Any],
    gaps: list[str],
) -> None:
    all_rows = connection.execute(
        """
        SELECT run.id, run.module_id, run.status, run.automation_level,
               run.started_at, run.completed_at,
               COALESCE(run.module_source_hash, module.source_hash) AS source_hash
        FROM campaign_module_runs run
        JOIN modules module ON module.id = run.module_id
        WHERE run.campaign_id = ?
        ORDER BY run.started_at, run.id
        """,
        (campaign_id,),
    ).fetchall()
    run_ids = {str(row["id"]) for row in all_rows}
    module_ids = {str(row["module_id"]) for row in all_rows}
    if not all_rows or run_ids != set(refs.get("module_run_ids", ())):
        gaps.append("authority_refs.module_run_ids do not match the campaign run ledger")
        return
    if module_ids != set(refs.get("module_ids", ())):
        gaps.append("authority_refs.module_ids do not match the campaign run ledger")
        return
    if any(row["automation_level"] != "ai_kp" for row in all_rows):
        gaps.append("the module run ledger contains a non-AI-KP run")
        return
    if any(not _sha256(row["source_hash"]) for row in all_rows):
        gaps.append("the module run ledger contains a run without a source SHA-256")
        return
    strict["module_runs"] = [
        {
            "id": str(row["id"]),
            "module_id": str(row["module_id"]),
            "module_sha256": str(row["source_hash"]),
            "status": str(row["status"]),
            "automation_mode": "full_ai",
            "started_at": _sqlite_timestamp(row["started_at"]),
            "completed_at": (
                _sqlite_timestamp(row["completed_at"])
                if row["completed_at"] is not None
                else None
            ),
        }
        for row in all_rows
    ]


def _reconstruct_episodes(
    connection: sqlite3.Connection,
    campaign_id: str,
    session_id: str,
    refs: Mapping[str, tuple[str, ...]],
    strict: dict[str, Any],
    gaps: list[str],
) -> None:
    rows = connection.execute(
        """
        SELECT id, sequence_no, status, client_continue_id, event_start_rowid,
               started_at, ended_at, version
        FROM campaign_episodes
        WHERE campaign_id = ? AND session_id = ?
        ORDER BY sequence_no
        """,
        (campaign_id, session_id),
    ).fetchall()
    episode_ids = {str(row["id"]) for row in rows}
    if not rows or episode_ids != set(refs.get("episode_ids", ())):
        gaps.append("authority_refs.episode_ids do not match the complete episode ledger")
        return
    checkpoints: list[ContinuityCheckpointEvidence] = []
    try:
        checkpoints = [
            reconstruct_continuity_checkpoint(connection, str(row["id"]))
            for row in rows
        ]
    except (KeyError, TypeError, ValueError) as exc:
        gaps.append(f"continuity checkpoint reconstruction failed: {exc}")
        return
    snapshot_ids = {checkpoint.snapshot_id for checkpoint in checkpoints}
    if snapshot_ids != set(refs.get("continuity_snapshot_ids", ())):
        gaps.append(
            "authority_refs.continuity_snapshot_ids do not match the complete snapshot ledger"
        )
        return
    strict["episodes"] = []
    for index, (row, checkpoint) in enumerate(zip(rows, checkpoints, strict=True)):
        next_episode = None
        if index + 1 < len(rows):
            successor = rows[index + 1]
            next_episode = {
                "id": str(successor["id"]),
                "sequence_no": int(successor["sequence_no"]),
                "client_continue_id": str(successor["client_continue_id"] or ""),
            }
        strict["episodes"].append(
            {
                "sequence_no": int(row["sequence_no"]),
                "id": str(row["id"]),
                "status": str(row["status"]),
                "version": int(row["version"]),
                "event_start_rowid": int(row["event_start_rowid"]),
                "started_at": _sqlite_timestamp(row["started_at"]),
                "ended_at": (
                    _sqlite_timestamp(row["ended_at"])
                    if row["ended_at"] is not None
                    else ""
                ),
                "snapshot": {
                    "id": checkpoint.snapshot_id,
                    "client_end_id": checkpoint.client_end_id,
                    "event_window_hash": checkpoint.event_window_hash,
                    "event_ids": list(checkpoint.event_ids),
                    "generation_cutoff": _sqlite_timestamp(
                        checkpoint.generation_cutoff
                    ),
                    "public_projection_hash": checkpoint.public_projection_hash,
                    "observer_projection_hash": checkpoint.observer_projection_hash,
                    "kp_projection_hash": checkpoint.kp_projection_hash,
                    "checkpoint_fingerprint": checkpoint.checkpoint_fingerprint,
                },
                "next_episode": next_episode,
            }
        )


def _reconstruct_restart(
    connection: sqlite3.Connection,
    raw: Any,
    strict: dict[str, Any],
    gaps: list[str],
) -> None:
    if not isinstance(raw, Mapping):
        gaps.append("Playwright evidence lacks process_restart corroboration")
        return
    expected_root = {"before", "stopped", "started", "after"}
    if set(raw) != expected_root:
        gaps.append("process_restart must contain exactly before/stopped/started/after")
        return
    checkpoint_keys = {
        "observed_at",
        "process_instance_id",
        "os_pid",
        "persistent_store_id",
        "checkpoint_snapshot_id",
    }
    stopped_keys = {
        "observed_at",
        "process_instance_id",
        "os_pid",
        "persistent_store_id",
        "clean_shutdown",
    }
    started_keys = {
        "observed_at",
        "process_instance_id",
        "os_pid",
        "persistent_store_id",
        "health_status",
    }
    phases: dict[str, Mapping[str, Any]] = {}
    for label, expected in (
        ("before", checkpoint_keys),
        ("stopped", stopped_keys),
        ("started", started_keys),
        ("after", checkpoint_keys),
    ):
        item = raw.get(label)
        if not isinstance(item, Mapping) or set(item) != expected:
            gaps.append(f"process_restart.{label} has an invalid field set")
            return
        phases[label] = item
    store = connection.execute(
        "SELECT persistent_store_id FROM persistent_store_identity WHERE singleton = 1"
    ).fetchone()
    if store is None:
        gaps.append("authority database lacks a persistent store identity")
        return
    store_id = str(store["persistent_store_id"])
    observed_stores = {
        str(item.get("persistent_store_id"))
        for item in phases.values()
    }
    if observed_stores != {store_id}:
        gaps.append("process_restart does not match the authority database store identity")
        return
    before_snapshot = str(phases["before"].get("checkpoint_snapshot_id") or "")
    after_snapshot = str(phases["after"].get("checkpoint_snapshot_id") or "")
    if not before_snapshot or before_snapshot != after_snapshot:
        gaps.append("process_restart does not preserve one checkpoint snapshot id")
        return
    checkpoint_row = connection.execute(
        "SELECT episode_id FROM session_continuity_snapshots WHERE id = ?",
        (before_snapshot,),
    ).fetchone()
    if checkpoint_row is None:
        gaps.append("process_restart checkpoint snapshot is absent from authority storage")
        return
    try:
        checkpoint = reconstruct_continuity_checkpoint(
            connection, str(checkpoint_row["episode_id"])
        )
    except (KeyError, TypeError, ValueError) as exc:
        gaps.append(f"process_restart checkpoint reconstruction failed: {exc}")
        return
    if checkpoint.snapshot_id != before_snapshot:
        gaps.append("process_restart checkpoint resolves to a different snapshot")
        return
    strict["restart_checkpoint"] = {
        "before": {
            **dict(phases["before"]),
            "checkpoint_fingerprint": checkpoint.checkpoint_fingerprint,
        },
        "stopped": dict(phases["stopped"]),
        "started": dict(phases["started"]),
        "after": {
            **dict(phases["after"]),
            "checkpoint_fingerprint": checkpoint.checkpoint_fingerprint,
        },
    }


def _parse_authority_refs(
    value: Any, gaps: list[str]
) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        gaps.append("Playwright evidence lacks endpoint-projected authority_refs")
        return {}
    unknown = sorted(str(key) for key in value if key not in _AUTHORITY_REF_KEYS)
    if unknown:
        gaps.append(f"authority_refs contains non-whitelisted keys: {', '.join(unknown)}")
        return {}
    parsed: dict[str, tuple[str, ...]] = {}
    for key in _AUTHORITY_REF_KEYS:
        raw = value.get(key, [])
        if not isinstance(raw, list) or any(
            not isinstance(item, str) or not item.strip() for item in raw
        ):
            gaps.append(f"authority_refs.{key} must be an array of non-empty strings")
            return {}
        identifiers = tuple(raw)
        if len(set(identifiers)) != len(identifiers):
            gaps.append(f"authority_refs.{key} contains duplicate identifiers")
            return {}
        parsed[key] = identifiers
    return parsed


def _open_read_only(database_path: Path) -> sqlite3.Connection:
    resolved = database_path.resolve(strict=True)
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _contains_secret(value: Any, *, key: str | None = None) -> bool:
    if key is not None:
        normalized = key.casefold()
        if any(part in normalized for part in _SECRET_KEYS):
            return True
        if normalized.endswith("_passed"):
            return False
    if isinstance(value, str):
        return bool(_SECRET_VALUE.search(value))
    if isinstance(value, Mapping):
        return any(
            _contains_secret(nested, key=str(nested_key))
            for nested_key, nested in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_secret(item) for item in value)
    return False


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _placeholders(values: tuple[str, ...]) -> str:
    if not values:
        raise ValueError("SQL identifier projection requires at least one value")
    return ",".join("?" for _ in values)


def _sqlite_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


__all__ = [
    "LongUiJourneyReconstructionResult",
    "reconstruct_long_ui_journey_evidence",
]

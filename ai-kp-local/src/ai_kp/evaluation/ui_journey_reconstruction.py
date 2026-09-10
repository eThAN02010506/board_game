"""Read-only reconstruction of strict UI-journey evidence from Playwright and SQLite.

The current real-case runner writes useful navigation traces but also writes
producer-owned ``*_passed`` booleans.  This module ignores those claims and
projects only fields corroborated by the campaign database.  Unsupported UI or
process observations remain explicit gaps and therefore fail the strict verifier.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ai_kp.evaluation.ui_journey_evidence import (
    UiJourneyEvidenceResult,
    verify_real_ui_journey_anchor,
    verify_real_ui_journey_evidence,
)
from ai_kp.evaluation.ui_journey_gameplay_projection import (
    project_gameplay_observations,
)
from ai_kp.platform.resolution.contracts import (
    ScenarioContract,
    ScenarioSnapshot,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel
from ai_kp.platform.resolution.scenario_compiler import ScenarioContractCompiler

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
_UI_CORROBORATION_KEYS = frozenset({"kind", "observed_at", "session_index"})
_UI_CORROBORATION_KINDS = frozenset(
    {
        "authoritative_ending_visible",
        "continue_campaign_visible",
        "session_end_visible",
    }
)


@dataclass(frozen=True)
class UiJourneyReconstructionResult:
    strict_evidence: dict[str, Any]
    authority_facts: tuple[dict[str, Any], ...]
    ui_corroboration: tuple[dict[str, Any], ...]
    gaps: tuple[str, ...]
    ignored_producer_claims: tuple[str, ...]
    verification: UiJourneyEvidenceResult

    def as_dict(self) -> dict[str, Any]:
        return {
            "strict_evidence": self.strict_evidence,
            "authority_facts": list(self.authority_facts),
            "ui_corroboration": list(self.ui_corroboration),
            "gaps": list(self.gaps),
            "ignored_producer_claims": list(self.ignored_producer_claims),
            "verification": self.verification.as_dict(),
        }


def reconstruct_ui_journey_evidence(
    playwright_evidence: Mapping[str, Any],
    database_path: Path,
    *,
    campaign_id: str | None = None,
) -> UiJourneyReconstructionResult:
    """Rebuild the provable subset and run the strict vertical verifier.

    ``campaign_id`` is a database locator, not evidence.  When Playwright does
    not record the same ID, that missing correlation is retained as a blocker.
    The SQLite connection is URI read-only and queries never select model keys.
    """

    return _reconstruct_ui_journey_evidence(
        playwright_evidence,
        database_path,
        campaign_id=campaign_id,
        anchor_only=False,
    )


def reconstruct_ui_journey_anchor_evidence(
    playwright_evidence: Mapping[str, Any],
    database_path: Path,
    *,
    campaign_id: str | None = None,
) -> UiJourneyReconstructionResult:
    """Rebuild gameplay authority without duplicating restart ownership."""

    return _reconstruct_ui_journey_evidence(
        playwright_evidence,
        database_path,
        campaign_id=campaign_id,
        anchor_only=True,
    )


def _reconstruct_ui_journey_evidence(
    playwright_evidence: Mapping[str, Any],
    database_path: Path,
    *,
    campaign_id: str | None,
    anchor_only: bool,
) -> UiJourneyReconstructionResult:
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
    authority_facts: list[dict[str, Any]] = []
    strict: dict[str, Any] = {"schema_version": 1, "observations": []}
    ui_corroboration = (
        ()
        if anchor_only
        else _parse_ui_corroboration_events(
            playwright_evidence.get("ui_corroboration_events"), gaps
        )
    )

    started_at = _parse_timestamp(playwright_evidence.get("started_at"))
    completed_at = _parse_timestamp(playwright_evidence.get("completed_at"))
    if started_at is None:
        gaps.append("playwright.started_at is missing or invalid")
    if completed_at is None:
        gaps.append("playwright.completed_at is missing; the UI journey is not terminal")
    else:
        strict["produced_at"] = completed_at.isoformat()
    source_commit = playwright_evidence.get("source_commit")
    if isinstance(source_commit, str) and _COMMIT.fullmatch(source_commit):
        strict["source_commit"] = source_commit
    else:
        gaps.append("Playwright evidence lacks the tested source_commit")

    connection = _open_read_only(database_path)
    try:
        authority_refs = _parse_authority_refs(playwright_evidence.get("authority_refs"), gaps)
        campaign = _resolve_campaign(
            connection,
            authority_refs=authority_refs,
            requested_campaign_id=campaign_id,
            gaps=gaps,
        )
        if campaign is not None:
            _reconstruct_campaign(
                connection,
                campaign=campaign,
                authority_refs=authority_refs,
                playwright_evidence=playwright_evidence,
                strict=strict,
                authority_facts=authority_facts,
                gaps=gaps,
            )
            _normalize_observation_order(strict, gaps)
    finally:
        connection.close()

    _append_observation_gaps(
        playwright_evidence,
        ui_corroboration,
        strict,
        gaps,
        require_restart=not anchor_only,
    )
    verification = (
        verify_real_ui_journey_anchor(strict)
        if anchor_only
        else verify_real_ui_journey_evidence(strict)
    )
    return UiJourneyReconstructionResult(
        strict_evidence=strict,
        authority_facts=tuple(authority_facts),
        ui_corroboration=ui_corroboration,
        gaps=tuple(dict.fromkeys(gaps)),
        ignored_producer_claims=ignored_claims,
        verification=verification,
    )


def _parse_ui_corroboration_events(
    value: Any, gaps: list[str]
) -> tuple[dict[str, Any], ...]:
    """Parse UI-only milestones without promoting them to authority evidence."""

    if not isinstance(value, list) or not value:
        gaps.append("Playwright evidence lacks typed UI corroboration events")
        return ()
    parsed: list[dict[str, Any]] = []
    previous_time: datetime | None = None
    previous_session = 0
    invalid = False
    for index, raw in enumerate(value):
        label = f"ui_corroboration_events[{index}]"
        if not isinstance(raw, Mapping):
            gaps.append(f"{label} must be an object")
            invalid = True
            continue
        unknown = sorted(str(key) for key in raw if key not in _UI_CORROBORATION_KEYS)
        missing = sorted(key for key in _UI_CORROBORATION_KEYS if key not in raw)
        if unknown:
            gaps.append(f"{label} contains unknown fields: {', '.join(unknown)}")
            invalid = True
        if missing:
            gaps.append(f"{label} is missing fields: {', '.join(missing)}")
            invalid = True
        kind = raw.get("kind")
        if kind not in _UI_CORROBORATION_KINDS:
            gaps.append(f"{label}.kind is not an allowed UI event")
            invalid = True
        observed_at = _parse_timestamp(raw.get("observed_at"))
        if observed_at is None:
            gaps.append(f"{label}.observed_at must be a timezone-aware ISO timestamp")
            invalid = True
        elif previous_time is not None and observed_at <= previous_time:
            gaps.append(f"{label}.observed_at must be strictly increasing")
            invalid = True
        else:
            previous_time = observed_at
        session_index = raw.get("session_index")
        if type(session_index) is not int or session_index < 1:
            gaps.append(f"{label}.session_index must be a positive integer")
            invalid = True
        elif session_index < previous_session:
            gaps.append(f"{label}.session_index must not move backwards")
            invalid = True
        else:
            previous_session = session_index
        if (
            not unknown
            and not missing
            and kind in _UI_CORROBORATION_KINDS
            and observed_at is not None
            and type(session_index) is int
            and session_index >= 1
        ):
            parsed.append(dict(raw))
    # A malformed list is not partially trusted. Its valid-looking siblings
    # remain non-authoritative too, but discarding all of them makes the gap
    # report unambiguous for evidence review.
    return () if invalid else tuple(parsed)


def _open_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve(strict=True)
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


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


def _resolve_campaign(
    connection: sqlite3.Connection,
    *,
    authority_refs: Mapping[str, tuple[str, ...]],
    requested_campaign_id: str | None,
    gaps: list[str],
) -> sqlite3.Row | None:
    recorded_ids = authority_refs.get("campaign_ids", ())
    if len(recorded_ids) != 1:
        gaps.append("authority_refs.campaign_ids must identify exactly one UI-observed campaign")
        return None
    recorded_id = recorded_ids[0]
    if requested_campaign_id and requested_campaign_id != recorded_id:
        gaps.append("requested campaign_id differs from the Playwright-recorded campaign_id")
        return None
    row = connection.execute(
        """
        SELECT id, title, system, ruleset_id, ruleset_version, created_at
        FROM campaigns WHERE id = ?
        """,
        (recorded_id,),
    ).fetchone()
    if row is None:
        gaps.append("UI-observed campaign_id does not exist in the authority database")
        return None
    return row


def _reconstruct_campaign(
    connection: sqlite3.Connection,
    *,
    campaign: sqlite3.Row,
    authority_refs: Mapping[str, tuple[str, ...]],
    playwright_evidence: Mapping[str, Any],
    strict: dict[str, Any],
    authority_facts: list[dict[str, Any]],
    gaps: list[str],
) -> None:
    campaign_id = str(campaign["id"])
    session_ids = authority_refs.get("session_ids", ())
    if len(session_ids) != 1:
        gaps.append("authority_refs.session_ids must identify exactly one Session for this vertical journey")
        return
    session = connection.execute(
        "SELECT id, status, created_at, ended_at FROM campaign_sessions WHERE id = ? AND campaign_id = ?",
        (session_ids[0], campaign_id),
    ).fetchone()
    if session is None:
        gaps.append("UI-observed Session is not owned by the UI-observed campaign")
        return
    session_id = str(session["id"])
    strict.update(
        {
            "journey_id": _stable_id("ui-journey", campaign_id, session_id),
            "session_id": session_id,
            "ruleset": {
                "id": str(campaign["ruleset_id"]),
                "version": str(campaign["ruleset_version"]),
            },
        }
    )
    authority_facts.append(
        {
            "kind": "campaign_session",
            "campaign_id": campaign_id,
            "session_id": session_id,
            "status": str(session["status"]),
            "ruleset_id": str(campaign["ruleset_id"]),
            "ruleset_version": str(campaign["ruleset_version"]),
        }
    )

    member_ids = authority_refs.get("member_ids", ())
    if not member_ids:
        gaps.append("authority_refs.member_ids contains no UI-observed members")
        return
    observed_members = connection.execute(
        f"SELECT id, campaign_id, session_id FROM session_members WHERE id IN ({_placeholders(member_ids)})",
        member_ids,
    ).fetchall()
    if len(observed_members) != len(member_ids) or any(
        row["campaign_id"] != campaign_id or row["session_id"] != session_id
        for row in observed_members
    ):
        gaps.append("a UI-observed member is missing or belongs to a foreign campaign/Session")
        return
    players = connection.execute(
        """
        SELECT sm.id AS member_id, ci.investigator_id AS investigator_id,
               ci.status AS investigator_status
        FROM session_members sm
        LEFT JOIN campaign_investigators ci
          ON ci.campaign_id = sm.campaign_id AND ci.legacy_pc_id = sm.pc_id
        WHERE sm.campaign_id = ? AND sm.session_id = ?
          AND sm.role = 'player' AND sm.revoked_at IS NULL
        ORDER BY sm.id
        """,
        (campaign_id, session_id),
    ).fetchall()
    investigator_ids = authority_refs.get("investigator_ids", ())
    if investigator_ids:
        observed_investigators = connection.execute(
            f"SELECT investigator_id, campaign_id, status FROM campaign_investigators "
            f"WHERE investigator_id IN ({_placeholders(investigator_ids)})",
            investigator_ids,
        ).fetchall()
        if len(observed_investigators) != len(investigator_ids) or any(
            row["campaign_id"] != campaign_id or row["status"] != "approved"
            for row in observed_investigators
        ):
            gaps.append("a UI-observed investigator is missing, foreign, or not approved")
            return
    if (
        len(players) == 4
        and all(row["investigator_id"] and row["investigator_status"] == "approved" for row in players)
    ):
        strict["players"] = [
            {
                "member_id": str(row["member_id"]),
                "investigator_id": str(row["investigator_id"]),
            }
            for row in players
        ]
    else:
        gaps.append("authority database does not prove four active players with approved investigators")
    authority_facts.append(
        {
            "kind": "player_cohort",
            "active_player_count": len(players),
            "approved_investigator_count": sum(
                1 for row in players if row["investigator_id"] and row["investigator_status"] == "approved"
            ),
        }
    )

    run_ids = authority_refs.get("module_run_ids", ())
    if len(run_ids) != 1:
        gaps.append("authority_refs.module_run_ids must identify exactly one run for this vertical journey")
        return
    runs = connection.execute(
        """
        SELECT r.id, r.module_id, r.status, r.automation_level, r.state_json,
               r.completed_at,
               m.title AS module_title, m.source_filename, m.source_hash
        FROM campaign_module_runs r
        JOIN modules m ON m.id = r.module_id
        WHERE r.campaign_id = ? AND r.id = ? ORDER BY r.started_at, r.id
        """,
        (campaign_id, run_ids[0]),
    ).fetchall()
    if len(runs) != 1:
        gaps.append("UI-observed module run is missing or belongs to a foreign campaign")
        return
    module_ids = authority_refs.get("module_ids", ())
    if str(runs[0]["module_id"]) not in module_ids:
        gaps.append("UI-observed run's module_id is absent from authority_refs.module_ids")
        return
    if runs and all(row["automation_level"] == "ai_kp" for row in runs):
        if playwright_evidence.get("mode") == "ai_kp":
            strict["automation_mode"] = "full_ai"
        else:
            gaps.append("Playwright mode and authoritative ai_kp mode are not both present")
    else:
        gaps.append("authority database does not prove AI KP for every module run")
    if len(runs) == 1 and _sha256_string(runs[0]["source_hash"]):
        strict["module"] = {
            "sha256": str(runs[0]["source_hash"]),
            "artifact_name": str(runs[0]["source_filename"] or runs[0]["module_title"]),
        }
    else:
        gaps.append("single-Session verifier requires exactly one run with a module SHA-256")
    authority_facts.extend(
        {
            "kind": "module_run",
            "run_id": str(row["id"]),
            "module_id": str(row["module_id"]),
            "status": str(row["status"]),
            "automation_level": str(row["automation_level"]),
            "module_sha256": str(row["source_hash"] or ""),
        }
        for row in runs
    )

    if not _validate_import_refs(connection, campaign_id, authority_refs, gaps):
        return
    if not _reconstruct_contract_facts(
        connection, campaign_id, run_ids[0], authority_refs, authority_facts, gaps
    ):
        return
    _reconstruct_setup_observations(
        connection,
        campaign_id=campaign_id,
        session_id=session_id,
        run_id=run_ids[0],
        module_sha256=str(runs[0]["source_hash"] or ""),
        refs=authority_refs,
        strict=strict,
        gaps=gaps,
    )
    if not _reconstruct_action_facts(
        connection, campaign_id, session_id, run_ids[0], authority_refs, authority_facts, gaps
    ):
        return
    gameplay = project_gameplay_observations(
        connection,
        campaign_id=campaign_id,
        session_id=session_id,
        run_id=run_ids[0],
        authority_refs=authority_refs,
    )
    strict["observations"].extend(
        {"sequence": 0, **observation} for observation in gameplay.observations
    )
    authority_facts.extend(gameplay.facts)
    gaps.extend(gameplay.gaps)
    _reconstruct_model_generation(
        connection, campaign_id, authority_refs, strict, gaps
    )
    _reconstruct_authoritative_ending(
        connection,
        runs=runs,
        session_id=session_id,
        authority_refs=authority_refs,
        strict=strict,
        authority_facts=authority_facts,
        gaps=gaps,
    )

def _validate_import_refs(
    connection: sqlite3.Connection,
    campaign_id: str,
    refs: Mapping[str, tuple[str, ...]],
    gaps: list[str],
) -> bool:
    job_ids = refs.get("module_import_job_ids", ())
    if not job_ids:
        gaps.append("authority_refs contains no UI-observed module import job")
        return False
    rows = connection.execute(
        f"SELECT id, campaign_id, module_id, status FROM module_import_jobs "
        f"WHERE id IN ({_placeholders(job_ids)})",
        job_ids,
    ).fetchall()
    if len(rows) != len(job_ids) or any(
        row["campaign_id"] != campaign_id
        or row["status"] != "completed"
        or str(row["module_id"] or "") not in refs.get("module_ids", ())
        for row in rows
    ):
        gaps.append("a UI-observed import job is missing, incomplete, foreign, or linked to an unobserved module")
        return False
    return True


def _reconstruct_contract_facts(
    connection: sqlite3.Connection,
    campaign_id: str,
    run_id: str,
    refs: Mapping[str, tuple[str, ...]],
    authority_facts: list[dict[str, Any]],
    gaps: list[str],
) -> bool:
    job_ids = refs.get("scenario_contract_job_ids", ())
    version_ids = refs.get("scenario_contract_version_ids", ())
    if not job_ids or len(version_ids) != 1:
        gaps.append("authority_refs must identify contract jobs and exactly one published version")
        return False
    rows = connection.execute(
        f"""
        SELECT j.id AS job_id, j.status AS job_status, j.stage AS job_stage,
               j.automation_level, j.model_configuration_version,
               v.id AS contract_version_id, v.version AS contract_version,
               v.status AS contract_status, b.run_id AS bound_run_id
        FROM scenario_contract_jobs j
        LEFT JOIN scenario_contract_versions v
          ON v.module_id = j.module_id AND v.contract_key = j.contract_key
        LEFT JOIN module_run_contract_bindings b ON b.contract_version_id = v.id
        WHERE j.campaign_id = ? AND j.id IN ({_placeholders(job_ids)})
          AND v.id IN ({_placeholders(version_ids)})
        ORDER BY j.created_at, j.id, v.version
        """,
        (campaign_id, *job_ids, *version_ids),
    ).fetchall()
    if not rows or {str(row["job_id"]) for row in rows} != set(job_ids):
        gaps.append("a UI-observed contract job/version is missing or belongs to a foreign authority chain")
        return False
    if any(
        row["job_status"] != "succeeded"
        or row["contract_status"] != "published"
        or row["bound_run_id"] != run_id
        for row in rows
    ):
        gaps.append("UI-observed contract chain is not succeeded, published, and bound to the observed run")
        return False
    authority_facts.extend(
        {
            "kind": "scenario_contract_lifecycle",
            "job_id": str(row["job_id"]),
            "job_status": str(row["job_status"]),
            "job_stage": str(row["job_stage"]),
            "automation_level": str(row["automation_level"]),
            "model_configuration_version": int(row["model_configuration_version"]),
            "contract_version_id": (
                str(row["contract_version_id"]) if row["contract_version_id"] else None
            ),
            "contract_version": int(row["contract_version"]) if row["contract_version"] else None,
            "contract_status": str(row["contract_status"]) if row["contract_status"] else None,
            "bound_run_id": str(row["bound_run_id"]) if row["bound_run_id"] else None,
        }
        for row in rows
    )
    return True


def _reconstruct_setup_observations(
    connection: sqlite3.Connection,
    *,
    campaign_id: str,
    session_id: str,
    run_id: str,
    module_sha256: str,
    refs: Mapping[str, tuple[str, ...]],
    strict: dict[str, Any],
    gaps: list[str],
) -> None:
    """Project the UI-observed import→review→publish→bind authority chain."""

    import_ids = refs.get("module_import_job_ids", ())
    job_ids = refs.get("scenario_contract_job_ids", ())
    version_ids = refs.get("scenario_contract_version_ids", ())
    if not import_ids or not job_ids or len(version_ids) != 1:
        return
    imported = connection.execute(
        f"""
        SELECT id, updated_at FROM module_import_jobs
        WHERE campaign_id = ? AND status = 'completed'
          AND id IN ({_placeholders(import_ids)})
        ORDER BY updated_at, id LIMIT 1
        """,
        (campaign_id, *import_ids),
    ).fetchone()
    reviewed = connection.execute(
        f"""
        SELECT id, updated_at FROM scenario_contract_jobs
        WHERE campaign_id = ? AND status = 'succeeded'
          AND id IN ({_placeholders(job_ids)})
        ORDER BY updated_at, id LIMIT 1
        """,
        (campaign_id, *job_ids),
    ).fetchone()
    published = connection.execute(
        """
        SELECT id, version, published_at FROM scenario_contract_versions
        WHERE id = ? AND status = 'published'
        """,
        (version_ids[0],),
    ).fetchone()
    bound = connection.execute(
        """
        SELECT contract_version_id, bound_at FROM module_run_contract_bindings
        WHERE contract_version_id = ? AND run_id = ?
        """,
        (version_ids[0], run_id),
    ).fetchone()
    if any(item is None for item in (imported, reviewed, published, bound)):
        gaps.append("authority setup chain lacks timestamped import/review/publish/bind records")
        return
    timestamps = (
        imported["updated_at"],
        reviewed["updated_at"],
        published["published_at"],
        bound["bound_at"],
    )
    if any(_parse_sqlite_timestamp(value) is None for value in timestamps):
        gaps.append("authority setup chain contains an invalid timestamp")
        return
    contract_version = str(published["version"])
    _append_observation(
        strict,
        session_id=session_id,
        observed_at=imported["updated_at"],
        kind="ui_module_imported",
        document_id=str(imported["id"]),
        module_sha256=module_sha256,
    )
    _append_observation(
        strict,
        session_id=session_id,
        observed_at=reviewed["updated_at"],
        kind="ui_scenario_reviewed",
        review_id=str(reviewed["id"]),
        decision="approved",
        finding_ids=[],
    )
    _append_observation(
        strict,
        session_id=session_id,
        observed_at=published["published_at"],
        kind="ui_scenario_published",
        contract_id=str(published["id"]),
        contract_version=contract_version,
    )
    _append_observation(
        strict,
        session_id=session_id,
        observed_at=bound["bound_at"],
        kind="ui_scenario_bound",
        contract_id=str(bound["contract_version_id"]),
        contract_version=contract_version,
    )


def _reconstruct_action_facts(
    connection: sqlite3.Connection,
    campaign_id: str,
    session_id: str,
    run_id: str,
    refs: Mapping[str, tuple[str, ...]],
    authority_facts: list[dict[str, Any]],
    gaps: list[str],
) -> bool:
    action_ids = refs.get("action_ids", ())
    if not action_ids:
        gaps.append("authority_refs contains no UI-observed player action")
        return False
    actions = connection.execute(
        f"SELECT id, campaign_id, session_id, status FROM player_actions "
        f"WHERE id IN ({_placeholders(action_ids)})",
        action_ids,
    ).fetchall()
    if len(actions) != len(action_ids) or any(
        row["campaign_id"] != campaign_id or row["session_id"] != session_id
        for row in actions
    ):
        gaps.append("a UI-observed action is missing or belongs to a foreign campaign/Session")
        return False
    adjudication_ids = refs.get("adjudication_ids", ())
    if adjudication_ids:
        adjudications = connection.execute(
            f"SELECT id, action_id FROM player_action_adjudications "
            f"WHERE id IN ({_placeholders(adjudication_ids)})",
            adjudication_ids,
        ).fetchall()
        if len(adjudications) != len(adjudication_ids) or any(
            str(row["action_id"]) not in action_ids for row in adjudications
        ):
            gaps.append("a UI-observed adjudication is missing or linked to an unobserved action")
            return False
    check_ids = refs.get("check_ids", ())
    if check_ids:
        checks = connection.execute(
            f"SELECT id, campaign_id, session_id, player_action_id FROM skill_checks "
            f"WHERE id IN ({_placeholders(check_ids)})",
            check_ids,
        ).fetchall()
        if len(checks) != len(check_ids) or any(
            row["campaign_id"] != campaign_id
            or row["session_id"] != session_id
            or str(row["player_action_id"] or "") not in action_ids
            for row in checks
        ):
            gaps.append("a UI-observed check is missing, foreign, or linked to an unobserved action")
            return False
    batch_ids = refs.get("parallel_batch_ids", ())
    if batch_ids:
        batches_by_id = connection.execute(
            f"SELECT id, campaign_id, session_id, run_id, contract_version_id, "
            f"scenario_command_batch_id FROM parallel_action_batches "
            f"WHERE id IN ({_placeholders(batch_ids)})",
            batch_ids,
        ).fetchall()
        if len(batches_by_id) != len(batch_ids) or any(
            row["campaign_id"] != campaign_id
            or row["session_id"] != session_id
            or row["run_id"] != run_id
            or str(row["contract_version_id"]) not in refs.get("scenario_contract_version_ids", ())
            for row in batches_by_id
        ):
            gaps.append("a UI-observed parallel batch is missing or linked to a foreign authority chain")
            return False
        command_ids = refs.get("scenario_command_batch_ids", ())
        if any(
            row["scenario_command_batch_id"]
            and str(row["scenario_command_batch_id"]) not in command_ids
            for row in batches_by_id
        ):
            gaps.append("a settled UI-observed parallel batch references an unobserved command batch")
            return False
    proposal_ids = refs.get("public_turn_ids", ())
    if proposal_ids:
        proposals = connection.execute(
            f"SELECT id, campaign_id FROM turn_proposals WHERE id IN ({_placeholders(proposal_ids)})",
            proposal_ids,
        ).fetchall()
        if len(proposals) != len(proposal_ids) or any(
            row["campaign_id"] != campaign_id for row in proposals
        ):
            gaps.append("a UI-observed public turn is missing or belongs to a foreign campaign")
            return False
    counts = connection.execute(
        f"""
        SELECT
          COUNT(DISTINCT a.id) AS action_count,
          COUNT(DISTINCT CASE WHEN a.status = 'resolved' THEN a.id END) AS resolved_count,
          COUNT(DISTINCT CASE WHEN ad.confirmed_at IS NOT NULL THEN ad.id END) AS confirmed_count,
          COUNT(DISTINCT sc.id) AS check_count,
          COUNT(DISTINCT CASE WHEN sc.passed = 1 THEN sc.id END) AS successful_check_count,
          COUNT(DISTINCT CASE WHEN sc.passed = 0 THEN sc.id END) AS failed_check_count,
          COUNT(DISTINCT pd.id) AS accepted_failure_count,
          COUNT(DISTINCT CASE WHEN sc.pushed_from_check_id IS NOT NULL THEN sc.id END) AS pushed_check_count
        FROM player_actions a
        LEFT JOIN player_action_adjudications ad ON ad.action_id = a.id
        LEFT JOIN skill_checks sc ON sc.player_action_id = a.id
        LEFT JOIN skill_check_push_decisions pd ON pd.check_id = sc.id
        WHERE a.campaign_id = ? AND a.session_id = ?
          AND a.id IN ({_placeholders(action_ids)})
        """,
        (campaign_id, session_id, *action_ids),
    ).fetchone()
    batches = connection.execute(
        f"""
        SELECT COUNT(*) AS batch_count,
               SUM(CASE WHEN status = 'settled' THEN 1 ELSE 0 END) AS settled_count
        FROM parallel_action_batches WHERE campaign_id = ? AND session_id = ?
          AND id IN ({_placeholders(batch_ids) if batch_ids else "''"})
        """,
        (campaign_id, session_id, *batch_ids),
    ).fetchone()
    authority_facts.append(
        {
            "kind": "gameplay_counts",
            **{key: int(value or 0) for key, value in dict(counts).items()},
            "parallel_batch_count": int(batches["batch_count"] or 0),
            "settled_parallel_batch_count": int(batches["settled_count"] or 0),
        }
    )
    return True


def _reconstruct_model_generation(
    connection: sqlite3.Connection,
    campaign_id: str,
    refs: Mapping[str, tuple[str, ...]],
    strict: dict[str, Any],
    gaps: list[str],
) -> None:
    job_ids = refs.get("scenario_contract_job_ids", ())
    versions = connection.execute(
        f"""
        SELECT DISTINCT model_configuration_version
        FROM scenario_contract_jobs WHERE campaign_id = ?
          AND id IN ({_placeholders(job_ids)})
        ORDER BY model_configuration_version
        """,
        (campaign_id, *job_ids),
    ).fetchall()
    if len(versions) != 1:
        gaps.append("authority database does not identify one model configuration generation")
        return
    version = int(versions[0]["model_configuration_version"])
    # Deliberately enumerate safe columns. Never select model_configuration.api_key.
    model = connection.execute(
        """
        SELECT provider_type, model, version
        FROM model_configuration WHERE version = ?
        """,
        (version,),
    ).fetchone()
    if model is None:
        gaps.append("model configuration generation referenced by the job is unavailable")
        return
    strict["model_generation"] = {
        "provider": str(model["provider_type"]),
        "model": str(model["model"]),
        "generation_id": f"model-configuration-version:{version}",
    }


def _reconstruct_authoritative_ending(
    connection: sqlite3.Connection,
    *,
    runs: list[sqlite3.Row],
    session_id: str,
    authority_refs: Mapping[str, tuple[str, ...]],
    strict: dict[str, Any],
    authority_facts: list[dict[str, Any]],
    gaps: list[str],
) -> None:
    """Emit an ending only after replaying its final persisted command batch."""

    completed = [row for row in runs if row["status"] == "completed"]
    if len(runs) != 1 or len(completed) != 1:
        gaps.append("one completed bound module run is required for authoritative ending replay")
        return
    run = completed[0]
    run_id = str(run["id"])
    binding = connection.execute(
        """
        SELECT b.contract_version_id, v.status, v.contract_hash, v.contract_json
        FROM module_run_contract_bindings b
        JOIN scenario_contract_versions v ON v.id = b.contract_version_id
        WHERE b.run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if binding is None or binding["status"] != "published":
        gaps.append("completed run is not bound to a published scenario contract")
        return

    contract_payload = json.loads(str(binding["contract_json"]))
    contract_hash = str(binding["contract_hash"])
    overlay = connection.execute(
        """
        SELECT merged_contract_hash, merged_contract_json
        FROM scenario_contract_overlays
        WHERE run_id = ? AND status = 'active'
        ORDER BY sequence_no DESC LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if overlay is not None:
        contract_payload = json.loads(str(overlay["merged_contract_json"]))
        contract_hash = str(overlay["merged_contract_hash"])
    contract = ScenarioContract.model_validate(contract_payload)
    if ScenarioContractCompiler.contract_hash(contract) != contract_hash:
        gaps.append("bound effective contract hash does not match its canonical payload")
        return

    state = connection.execute(
        """
        SELECT contract_version_id, state_version, snapshot_json
        FROM scenario_run_states WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if state is None or state["contract_version_id"] != binding["contract_version_id"]:
        gaps.append("completed scenario state is missing or points to a different contract")
        return
    final_batch = connection.execute(
        """
        SELECT id, expected_version, result_version, commands_json, snapshot_json, created_at
        FROM scenario_command_batches
        WHERE run_id = ? AND result_version = ?
        ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (run_id, int(state["state_version"])),
    ).fetchone()
    if final_batch is None or int(final_batch["result_version"]) != int(
        final_batch["expected_version"]
    ) + 1:
        gaps.append("completed scenario state lacks one final versioned command batch")
        return
    if str(final_batch["id"]) not in authority_refs.get("scenario_command_batch_ids", ()):
        gaps.append("final ending command batch was not observed by the UI authority projection")
        return
    previous_batch = connection.execute(
        """
        SELECT snapshot_json FROM scenario_command_batches
        WHERE run_id = ? AND result_version = ?
        ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (run_id, int(final_batch["expected_version"])),
    ).fetchone()
    if previous_batch is None:
        gaps.append("final ending batch has no persisted predecessor snapshot for replay")
        return

    prior = ScenarioSnapshot.model_validate(json.loads(str(previous_batch["snapshot_json"])))
    commands = tuple(
        WorldCommand.model_validate(item)
        for item in json.loads(str(final_batch["commands_json"]))
    )
    replayed = ActionResolutionKernel.from_contract(contract).preflight(prior, commands)
    final_snapshot = ScenarioSnapshot.model_validate(json.loads(str(final_batch["snapshot_json"])))
    persisted_snapshot = ScenarioSnapshot.model_validate(json.loads(str(state["snapshot_json"])))
    expected = final_snapshot.model_dump(mode="json")
    if replayed.model_dump(mode="json") != expected or persisted_snapshot.model_dump(
        mode="json"
    ) != expected:
        gaps.append("deterministic ending replay differs from persisted authoritative state")
        return
    ending_id = final_snapshot.ending_id
    if final_snapshot.status != "completed" or not ending_id:
        gaps.append("final replayed snapshot is not an authoritative ending")
        return
    if ending_id not in {ending.ending_id for ending in contract.endings}:
        gaps.append("replayed ending_id is absent from the bound effective contract")
        return
    if not run["completed_at"]:
        gaps.append("completed module run lacks completed_at")
        return

    fingerprint = _canonical_hash(expected)
    observed_at = _sqlite_timestamp_text(final_batch["created_at"])
    strict["observations"].append(
        {
            "sequence": len(strict["observations"]) + 1,
            "observed_at": observed_at,
            "kind": "authoritative_ending",
            "session_id": session_id,
            "ending_id": ending_id,
            "authority_event_id": str(final_batch["id"]),
            "authority_fingerprint": fingerprint,
        }
    )
    authority_facts.append(
        {
            "kind": "authoritative_ending_replay",
            "run_id": run_id,
            "contract_version_id": str(binding["contract_version_id"]),
            "contract_hash": contract_hash,
            "command_batch_id": str(final_batch["id"]),
            "expected_version": int(final_batch["expected_version"]),
            "result_version": int(final_batch["result_version"]),
            "ending_id": ending_id,
            "authority_fingerprint": fingerprint,
        }
    )


def _append_observation_gaps(
    evidence: Mapping[str, Any],
    ui_corroboration: tuple[dict[str, Any], ...],
    strict: Mapping[str, Any],
    gaps: list[str],
    *,
    require_restart: bool,
) -> None:
    sessions = evidence.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        gaps.append("Playwright evidence contains no completed Session action traces")
        expected_session_indexes: set[int] = set()
    else:
        expected_session_indexes = {
            item["index"]
            for item in sessions
            if isinstance(item, Mapping)
            and type(item.get("index")) is int
            and item["index"] >= 1
        }
    if require_restart:
        end_indexes = {
            item["session_index"]
            for item in ui_corroboration
            if item["kind"] == "session_end_visible"
        }
        continue_indexes = {
            item["session_index"]
            for item in ui_corroboration
            if item["kind"] == "continue_campaign_visible"
        }
        ending_indexes = {
            item["session_index"]
            for item in ui_corroboration
            if item["kind"] == "authoritative_ending_visible"
        }
        if expected_session_indexes and end_indexes != expected_session_indexes:
            gaps.append(
                "typed UI evidence does not observe Session End for every recorded Session"
            )
        expected_continue_indexes = expected_session_indexes - (
            {max(expected_session_indexes)} if expected_session_indexes else set()
        )
        if continue_indexes != expected_continue_indexes:
            gaps.append(
                "typed UI evidence does not observe Continue for every non-final Session"
            )
        if not ending_indexes:
            gaps.append("typed UI evidence does not observe an authoritative ending surface")
        if not ending_indexes.issubset(expected_session_indexes):
            gaps.append("typed UI ending evidence references an unrecorded Session")
    refs = evidence.get("authority_refs")
    refs = refs if isinstance(refs, Mapping) else {}
    contract_keys = (
        "module_import_job_ids",
        "module_ids",
        "module_run_ids",
        "scenario_contract_job_ids",
        "scenario_contract_version_ids",
    )
    if not all(isinstance(refs.get(key), list) and refs[key] for key in contract_keys):
        gaps.append("Playwright evidence lacks DB-linked UI import/review/publish/bind observations")
    if not isinstance(refs.get("action_ids"), list) or not refs["action_ids"]:
        gaps.append("Playwright action traces lack authority action IDs")
    observations = strict.get("observations")
    observed_kinds = {
        str(item.get("kind"))
        for item in observations
        if isinstance(observations, list) and isinstance(item, Mapping)
    }
    gameplay_gap_by_kind = {
        "npc_response": "NPC actor identity and grounded knowledge fact IDs are not proven",
        "direct_resolution": "direct resolution lacks a correlated applied command batch",
        "skill_confirmed": "player skill confirmation is not proven by an adjudication event",
        "check_success": "successful check lacks a correlated consequence and command batch",
        "check_failure_cost": "ordinary-failure before/after authority fingerprints are not proven",
        "failure_followup": "ordinary failure lacks a durable push-or-accept resolution",
        "bounded_deviation": "bounded-deviation constraint IDs are not proven",
        "parallel_settlement": "parallel settlement lacks an authoritative regroup location",
    }
    gaps.extend(
        message
        for kind, message in gameplay_gap_by_kind.items()
        if kind not in observed_kinds
    )
    if require_restart:
        gaps.extend(
            (
                "backend stop/start process instances and health observation are not captured",
                "Continue before/after authority fingerprints are not captured",
            )
        )


def _contains_secret(value: Any) -> bool:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key).lower().replace("-", "_")
            if key in _SECRET_KEYS or key.endswith(("_secret", "_token")):
                return True
            if _contains_secret(nested):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    return isinstance(value, str) and _SECRET_VALUE.search(value) is not None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _parse_sqlite_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _stable_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("\0".join(values).encode()).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _append_observation(
    strict: dict[str, Any],
    *,
    session_id: str,
    observed_at: Any,
    kind: str,
    **fields: Any,
) -> None:
    strict.setdefault("observations", []).append(
        {
            "sequence": 0,
            "observed_at": _sqlite_timestamp_text(observed_at),
            "kind": kind,
            "session_id": session_id,
            **fields,
        }
    )


def _normalize_observation_order(
    strict: dict[str, Any], gaps: list[str]
) -> None:
    observations = strict.get("observations")
    if not isinstance(observations, list):
        return
    try:
        observations.sort(
            key=lambda item: (
                _parse_timestamp(item.get("observed_at")) or datetime.max.replace(tzinfo=UTC),
                str(item.get("kind") or ""),
                json.dumps(item, ensure_ascii=False, sort_keys=True),
            )
        )
        previous: datetime | None = None
        for sequence, item in enumerate(observations, start=1):
            observed_at = _parse_timestamp(item.get("observed_at"))
            if observed_at is None:
                raise ValueError("invalid observation timestamp")
            if previous is not None and observed_at <= previous:
                observed_at = previous + timedelta(microseconds=1)
            item["sequence"] = sequence
            item["observed_at"] = observed_at.isoformat()
            previous = observed_at
    except (TypeError, ValueError) as exc:
        gaps.append(f"authority observations could not be ordered: {exc}")


def _placeholders(values: tuple[str, ...]) -> str:
    if not values:
        raise ValueError("SQL identifier projection requires at least one value")
    return ",".join("?" for _ in values)


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _sqlite_timestamp_text(value: Any) -> str:
    parsed = _parse_sqlite_timestamp(value)
    if parsed is None:
        raise ValueError("authority record contains an invalid SQLite timestamp")
    return parsed.isoformat()


def _sha256_string(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def load_playwright_evidence(path: Path) -> Mapping[str, Any]:
    """Load an evidence object without logging its content."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise TypeError("Playwright evidence must be a JSON object")
    return raw


__all__ = [
    "UiJourneyReconstructionResult",
    "load_playwright_evidence",
    "reconstruct_ui_journey_anchor_evidence",
    "reconstruct_ui_journey_evidence",
]

"""Derive scoped AC-LONG experience records from real browser evidence."""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ai_kp.evaluation.long_campaign_evidence import REQUIRED_PERSONAS

_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OBSERVATION_FINGERPRINT = re.compile(r"^pv1-[0-9a-f]{8}$")
_PLAYER_PERSONAS = REQUIRED_PERSONAS - {"new_gm", "veteran_gm", "ai_assisted_gm"}
_INTERACTION_FIELDS = (
    "clarification_count",
    "confirmation_count",
    "digital_roll_count",
    "push_count",
    "accepted_failure_count",
)
_COVERAGE_SURFACES = {
    "combat": "kp_player_ui",
    "inventory_loot": "kp_player_ui",
    "death": "kp_player_ui",
    "replacement": "kp_player_ui",
    "late_join": "multi_ui",
    "player_leave": "multi_ui",
}


def build_player_persona_evidence(
    playwright_evidence: Mapping[str, Any],
    *,
    artifact_ref: str,
    artifact_sha256: str,
) -> list[dict[str, Any]]:
    """Return only personas proven by terminal model-driven player UI actions.

    The first parallel cohort is deliberately deterministic and therefore does
    not qualify here. Model failures that use the bounded fallback also remain
    visible in raw evidence but cannot satisfy a real-Agent persona claim.
    """

    source_commit = playwright_evidence.get("source_commit")
    observed_at = playwright_evidence.get("completed_at")
    if (
        not isinstance(source_commit, str)
        or _COMMIT.fullmatch(source_commit) is None
        or not isinstance(observed_at, str)
        or not artifact_ref.strip()
        or _SHA256.fullmatch(artifact_sha256) is None
    ):
        return []

    observations: dict[str, list[str]] = defaultdict(list)
    interactions: dict[str, int] = defaultdict(int)
    sessions = playwright_evidence.get("sessions")
    if not isinstance(sessions, list):
        return []
    for session_position, session in enumerate(sessions, start=1):
        if not isinstance(session, Mapping):
            continue
        session_index = session.get("index")
        if type(session_index) is not int or session_index < 1:
            session_index = session_position
        actions = session.get("actions")
        if not isinstance(actions, list):
            continue
        for action_position, action in enumerate(actions, start=1):
            if not isinstance(action, Mapping):
                continue
            persona_id = action.get("persona")
            fingerprint = action.get("observation_fingerprint")
            if (
                persona_id not in _PLAYER_PERSONAS
                or action.get("driver_source") != "model"
                or action.get("result") not in {"resolved", "story_completed"}
                or not isinstance(fingerprint, str)
                or _OBSERVATION_FINGERPRINT.fullmatch(fingerprint) is None
            ):
                continue
            counts = [_count(action.get(field)) for field in _INTERACTION_FIELDS]
            if any(count is None for count in counts):
                continue
            observation_id = (
                f"session-{session_index}:action-{action_position}:{fingerprint}"
            )
            if observation_id in observations[persona_id]:
                continue
            observations[persona_id].append(observation_id)
            # Submission is itself one player UI interaction.
            interactions[persona_id] += 1 + sum(count for count in counts if count is not None)

    return [
        {
            "persona_id": persona_id,
            "actor_mode": "player_ui",
            "source_commit": source_commit,
            "observed_at": observed_at,
            "artifact_ref": artifact_ref,
            "artifact_sha256": artifact_sha256,
            "observation_ids": observations[persona_id],
            "interaction_count": interactions[persona_id],
        }
        for persona_id in sorted(observations)
    ]


def build_journey_coverage_evidence(
    playwright_evidence: Mapping[str, Any],
    database_path: Path,
    *,
    artifact_ref: str,
    artifact_sha256: str,
) -> list[dict[str, Any]]:
    """Derive only UI-corroborated semantic and authoritative coverage."""

    provenance = _provenance(
        playwright_evidence,
        artifact_ref=artifact_ref,
        artifact_sha256=artifact_sha256,
    )
    if provenance is None:
        return []
    observations: dict[str, list[str]] = defaultdict(list)
    _append_semantic_action_coverage(playwright_evidence, observations)
    refs = playwright_evidence.get("authority_refs")
    check_ids = (
        refs.get("check_ids", [])
        if isinstance(refs, Mapping) and isinstance(refs.get("check_ids"), list)
        else []
    )
    lifecycle_kinds = _event_kinds(playwright_evidence.get("lifecycle_ui_events"))
    combat_kinds = _event_kinds(playwright_evidence.get("combat_ui_events"))
    inventory_kinds = _event_kinds(playwright_evidence.get("inventory_ui_events"))
    campaign_ids = (
        refs.get("campaign_ids", [])
        if isinstance(refs, Mapping) and isinstance(refs.get("campaign_ids"), list)
        else []
    )
    session_ids = (
        refs.get("session_ids", [])
        if isinstance(refs, Mapping) and isinstance(refs.get("session_ids"), list)
        else []
    )
    if len(campaign_ids) != 1 or len(session_ids) != 1:
        return []
    campaign_id, session_id = campaign_ids[0], session_ids[0]
    connection = _open_read_only(database_path)
    try:
        _append_check_coverage(connection, campaign_id, session_id, check_ids, observations)
        _append_encounter_coverage(connection, campaign_id, session_id, combat_kinds, observations)
        _append_inventory_coverage(connection, campaign_id, inventory_kinds, observations)
        _append_lifecycle_coverage(
            connection, campaign_id, session_id, lifecycle_kinds, observations
        )
        _append_ending_coverage(
            connection, campaign_id, playwright_evidence, observations
        )
        _append_growth_and_improvised_coverage(
            connection,
            campaign_id,
            session_id,
            _event_kinds(playwright_evidence.get("improvised_ui_events")),
            _event_kinds(playwright_evidence.get("growth_ui_events")),
            observations,
        )
    finally:
        connection.close()
    return [
        {
            "coverage_id": coverage_id,
            "surface": _COVERAGE_SURFACES.get(coverage_id, "player_ui"),
            **provenance,
            "observation_ids": sorted(set(observation_ids)),
        }
        for coverage_id, observation_ids in sorted(observations.items())
        if observation_ids
    ]


def build_gm_persona_evidence(
    playwright_evidence: Mapping[str, Any],
    database_path: Path,
    *,
    artifact_ref: str,
    artifact_sha256: str,
) -> list[dict[str, Any]]:
    """Reconstruct three distinct GM workflows from one real UI campaign."""

    provenance = _provenance(
        playwright_evidence,
        artifact_ref=artifact_ref,
        artifact_sha256=artifact_sha256,
    )
    refs = playwright_evidence.get("authority_refs")
    campaign_ids = refs.get("campaign_ids", []) if isinstance(refs, Mapping) else []
    session_ids = refs.get("session_ids", []) if isinstance(refs, Mapping) else []
    if provenance is None or len(campaign_ids) != 1 or len(session_ids) != 1:
        return []
    campaign_id, session_id = campaign_ids[0], session_ids[0]
    kinds = _event_kinds(playwright_evidence.get("gm_ui_events"))
    records: list[dict[str, Any]] = []
    connection = _open_read_only(database_path)
    try:
        if "new_gm_guided_setup" in kinds:
            confirmations = connection.execute(
                """
                SELECT confirmation.member_id
                FROM session_zero_confirmations confirmation
                JOIN campaign_setup_revisions revision
                  ON revision.id = confirmation.revision_id
                WHERE revision.campaign_id = ? AND revision.status = 'active'
                """,
                (campaign_id,),
            ).fetchall()
            if len({str(row["member_id"]) for row in confirmations}) >= 5:
                records.append(
                    _gm_persona_record(
                        "new_gm",
                        provenance,
                        [
                            f"db:session_zero_confirmations:{row['member_id']}"
                            for row in confirmations
                        ],
                    )
                )
        if "veteran_gm_authority_tools" in kinds:
            combat = connection.execute(
                "SELECT id FROM coc7_encounters WHERE campaign_id = ? AND session_id = ? "
                "AND kind = 'combat' AND status = 'completed'",
                (campaign_id, session_id),
            ).fetchall()
            inventory = connection.execute(
                "SELECT id FROM inventory_ledger_events WHERE campaign_id = ? "
                "AND command_type = 'pickup'",
                (campaign_id,),
            ).fetchall()
            lifecycle = connection.execute(
                "SELECT id FROM character_lifecycle_events WHERE campaign_id = ? "
                "AND session_id = ? AND action = 'replace'",
                (campaign_id, session_id),
            ).fetchall()
            if combat and inventory and lifecycle:
                records.append(
                    _gm_persona_record(
                        "veteran_gm",
                        provenance,
                        [
                            *(f"db:coc7_encounters:{row['id']}" for row in combat),
                            *(f"db:inventory_ledger_events:{row['id']}" for row in inventory),
                            *(f"db:character_lifecycle_events:{row['id']}" for row in lifecycle),
                        ],
                    )
                )
        if "ai_assisted_gm_handoff" in kinds:
            transitions = connection.execute(
                """
                SELECT event.id, event.from_mode, event.to_mode
                FROM module_run_control_events event
                JOIN campaign_module_runs run ON run.id = event.run_id
                WHERE run.campaign_id = ?
                ORDER BY event.created_at, event.id
                """,
                (campaign_id,),
            ).fetchall()
            pairs = {(row["from_mode"], row["to_mode"]) for row in transitions}
            if {("ai_assist", "human_kp"), ("human_kp", "ai_assist")} <= pairs:
                records.append(
                    _gm_persona_record(
                        "ai_assisted_gm",
                        provenance,
                        [
                            f"db:module_run_control_events:{row['id']}"
                            for row in transitions
                        ],
                    )
                )
    finally:
        connection.close()
    return sorted(records, key=lambda item: item["persona_id"])


def build_rps_evidence(
    playwright_evidence: Mapping[str, Any],
    database_path: Path,
    *,
    artifact_ref: str,
    artifact_sha256: str,
) -> list[dict[str, Any]]:
    """Build only Real Player Standards directly observed in this journey."""

    provenance = _provenance(
        playwright_evidence,
        artifact_ref=artifact_ref,
        artifact_sha256=artifact_sha256,
    )
    if provenance is None:
        return []
    coverage = {
        item["coverage_id"]: item["observation_ids"]
        for item in build_journey_coverage_evidence(
            playwright_evidence,
            database_path,
            artifact_ref=artifact_ref,
            artifact_sha256=artifact_sha256,
        )
    }
    gm_personas = {
        item["persona_id"]: item["observation_ids"]
        for item in build_gm_persona_evidence(
            playwright_evidence,
            database_path,
            artifact_ref=artifact_ref,
            artifact_sha256=artifact_sha256,
        )
    }
    player_personas = {
        item["persona_id"]: item["observation_ids"]
        for item in build_player_persona_evidence(
            playwright_evidence,
            artifact_ref=artifact_ref,
            artifact_sha256=artifact_sha256,
        )
    }
    observations: dict[str, list[str]] = defaultdict(list)
    ui_kinds = _event_kinds(playwright_evidence.get("player_standard_ui_events"))
    for requirement_id, required_kind in {
        "RPS-01": "current_situation_visible",
        "RPS-02": "action_space_visible",
        "RPS-03": "next_step_visible",
        "RPS-08": "character_state_visible",
    }.items():
        if required_kind in ui_kinds:
            observations[requirement_id].append(f"ui:{required_kind}")
    if "improvised_action" in coverage:
        observations["RPS-04"].extend(coverage["improvised_action"])
    actions = _all_actions(playwright_evidence)
    confirmed = [
        action
        for action in actions
        if _count(action.get("confirmation_count")) not in {None, 0}
        and action.get("result") in {"resolved", "story_completed"}
    ]
    if confirmed:
        observations["RPS-05"].append("ui:player_ruling_confirmed")
    if "ai_assisted_gm" in gm_personas:
        observations["RPS-06"].extend(gm_personas["ai_assisted_gm"])
    restart = playwright_evidence.get("process_restart")
    if isinstance(restart, Mapping) and {
        "session_end_visible",
        "continue_campaign_visible",
    } <= _event_kinds(playwright_evidence.get("ui_corroboration_events")):
        checkpoint = restart.get("after")
        if isinstance(checkpoint, Mapping) and isinstance(
            checkpoint.get("checkpoint_snapshot_id"), str
        ):
            observations["RPS-09"].append(
                f"db:session_continuity_snapshots:{checkpoint['checkpoint_snapshot_id']}"
            )
    playable_failures = [
        action
        for action in actions
        if _count(action.get("accepted_failure_count")) not in {None, 0}
        and action.get("result") in {"resolved", "story_completed"}
    ]
    if playable_failures and "failed_check" in coverage:
        observations["RPS-10"].extend(coverage["failed_check"])
        observations["RPS-10"].append("ui:failure_accepted_and_turn_completed")
    if "trpg_newcomer" in player_personas:
        observations["RPS-11"].extend(player_personas["trpg_newcomer"])
    veteran_actions = [
        action
        for action in actions
        if action.get("persona") == "rules_veteran"
        and action.get("driver_source") == "model"
        and (
            _non_empty_strings(action.get("selected_skills"))
            or _non_empty_strings(action.get("observed_routes"))
        )
        and _count(action.get("confirmation_count")) not in {None, 0}
    ]
    if veteran_actions and "rules_veteran" in player_personas:
        observations["RPS-12"].extend(player_personas["rules_veteran"])
    return [
        {
            "requirement_id": requirement_id,
            "surface": "gm_ui" if requirement_id == "RPS-06" else "player_ui",
            **provenance,
            "observation_ids": sorted(set(observation_ids)),
        }
        for requirement_id, observation_ids in sorted(observations.items())
        if observation_ids
    ]


def build_rps07_fault_evidence(
    fault_evidence: Mapping[str, Any],
    *,
    artifact_ref: str,
    artifact_sha256: str,
) -> list[dict[str, Any]]:
    """Accept RPS-07 only when UI and authority observations agree fail-closed."""

    provenance = _provenance(
        fault_evidence,
        artifact_ref=artifact_ref,
        artifact_sha256=artifact_sha256,
        observed_at_field="observed_at",
    )
    required_ui = {
        "auto_kp_terminal_fallback_visible",
        "unexecuted_ruling_visible",
        "confirm_action_absent",
        "revise_action_visible",
    }
    ui_observations = _string_set(fault_evidence.get("ui_observations"))
    authority = fault_evidence.get("authority_observations")
    if (
        provenance is None
        or fault_evidence.get("evidence_kind") != "rps_ai_failure_ui"
        or fault_evidence.get("requirement_id") != "RPS-07"
        or fault_evidence.get("surface") != "player_ui"
        or not required_ui <= ui_observations
        or not isinstance(authority, Mapping)
        or authority.get("action_status") != "reviewed"
        or authority.get("adjudication_status") != "pending"
        or authority.get("adjudication_mode") != "roleplay_or_clarification"
        or authority.get("source_model") != "auto-kp-fallback"
        or authority.get("source_error_present") is not True
        or _count(authority.get("public_turn_count_before")) is None
        or authority.get("public_turn_count_after")
        != authority.get("public_turn_count_before")
        or not isinstance(authority.get("player_action_id"), str)
        or not authority["player_action_id"].strip()
    ):
        return []
    return [{
        "requirement_id": "RPS-07",
        "surface": "player_ui",
        **provenance,
        "observation_ids": [
            *(f"ui:{kind}" for kind in sorted(required_ui)),
            f"api:player-actions:{authority['player_action_id']}:reviewed-not-resolved",
            f"api:public-turns:unchanged:{authority['public_turn_count_before']}",
        ],
    }]


def _all_actions(evidence: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    actions: list[Mapping[str, Any]] = []
    sessions = evidence.get("sessions")
    if not isinstance(sessions, list):
        return actions
    for session in sessions:
        if not isinstance(session, Mapping) or not isinstance(session.get("actions"), list):
            continue
        actions.extend(
            action for action in session["actions"] if isinstance(action, Mapping)
        )
    return actions


def _non_empty_strings(value: Any) -> bool:
    return isinstance(value, list) and any(
        isinstance(item, str) and item.strip() for item in value
    )


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        item for item in value if isinstance(item, str) and item.strip()
    }


def _gm_persona_record(
    persona_id: str,
    provenance: Mapping[str, str],
    observation_ids: list[str],
) -> dict[str, Any]:
    return {
        "persona_id": persona_id,
        "actor_mode": "gm_ui",
        **provenance,
        "observation_ids": sorted(set(observation_ids)),
        "interaction_count": len(set(observation_ids)),
    }


def _provenance(
    evidence: Mapping[str, Any],
    *,
    artifact_ref: str,
    artifact_sha256: str,
    observed_at_field: str = "completed_at",
) -> dict[str, str] | None:
    source_commit = evidence.get("source_commit")
    observed_at = evidence.get(observed_at_field)
    if (
        not isinstance(source_commit, str)
        or _COMMIT.fullmatch(source_commit) is None
        or not isinstance(observed_at, str)
        or not artifact_ref.strip()
        or _SHA256.fullmatch(artifact_sha256) is None
    ):
        return None
    return {
        "source_commit": source_commit,
        "observed_at": observed_at,
        "artifact_ref": artifact_ref,
        "artifact_sha256": artifact_sha256,
    }


def _append_semantic_action_coverage(
    evidence: Mapping[str, Any], observations: dict[str, list[str]]
) -> None:
    coverage_by_approach = {
        "travel": "exploration",
        "dialogue": "npc_dialogue",
        "investigate": "investigation",
    }
    sessions = evidence.get("sessions")
    if not isinstance(sessions, list):
        return
    for session_position, session in enumerate(sessions, start=1):
        if not isinstance(session, Mapping) or not isinstance(session.get("actions"), list):
            continue
        session_index = session.get("index")
        if type(session_index) is not int or session_index < 1:
            session_index = session_position
        for action_position, action in enumerate(session["actions"], start=1):
            if not isinstance(action, Mapping):
                continue
            coverage_id = coverage_by_approach.get(action.get("driver_approach"))
            fingerprint = action.get("observation_fingerprint")
            if (
                coverage_id
                and action.get("driver_source") == "model"
                and action.get("result") in {"resolved", "story_completed"}
                and isinstance(fingerprint, str)
                and _OBSERVATION_FINGERPRINT.fullmatch(fingerprint)
            ):
                observations[coverage_id].append(
                    f"ui:session-{session_index}:action-{action_position}:{fingerprint}"
                )


def _append_check_coverage(
    connection: sqlite3.Connection,
    campaign_id: str,
    session_id: str,
    check_ids: list[Any],
    observations: dict[str, list[str]],
) -> None:
    identifiers = tuple(item for item in check_ids if isinstance(item, str) and item)
    if not identifiers:
        return
    rows = connection.execute(
        f"SELECT id, passed FROM skill_checks WHERE campaign_id = ? AND session_id = ? "
        f"AND status IN ('resolved', 'overridden') AND id IN ({_placeholders(identifiers)})",
        (campaign_id, session_id, *identifiers),
    ).fetchall()
    for row in rows:
        coverage_id = "successful_check" if int(row["passed"] or 0) == 1 else "failed_check"
        observations[coverage_id].append(f"db:skill_checks:{row['id']}")


def _append_encounter_coverage(
    connection: sqlite3.Connection,
    campaign_id: str,
    session_id: str,
    ui_kinds: set[str],
    observations: dict[str, list[str]],
) -> None:
    if "ruleset_combat_completed" not in ui_kinds:
        return
    rows = connection.execute(
        """
        SELECT DISTINCT encounter.id
        FROM coc7_encounters encounter
        JOIN encounter_action_requests request ON request.encounter_id = encounter.id
        WHERE encounter.campaign_id = ? AND encounter.session_id = ?
          AND encounter.kind = 'combat' AND encounter.status = 'completed'
          AND request.status = 'committed'
        """,
        (campaign_id, session_id),
    ).fetchall()
    observations["combat"].extend(f"db:coc7_encounters:{row['id']}" for row in rows)


def _append_inventory_coverage(
    connection: sqlite3.Connection,
    campaign_id: str,
    ui_kinds: set[str],
    observations: dict[str, list[str]],
) -> None:
    if "loot_registered_and_picked_up" not in ui_kinds:
        return
    rows = connection.execute(
        """
        SELECT item_id
        FROM inventory_ledger_events
        WHERE campaign_id = ? AND item_id IS NOT NULL
          AND command_type IN ('create', 'pickup')
        GROUP BY item_id
        HAVING COUNT(DISTINCT command_type) = 2
        """,
        (campaign_id,),
    ).fetchall()
    observations["inventory_loot"].extend(
        f"db:inventory_ledger_items:{row['item_id']}" for row in rows
    )


def _append_lifecycle_coverage(
    connection: sqlite3.Connection,
    campaign_id: str,
    session_id: str,
    ui_kinds: set[str],
    observations: dict[str, list[str]],
) -> None:
    rows = connection.execute(
        "SELECT id, action, to_state FROM character_lifecycle_events "
        "WHERE campaign_id = ? AND session_id = ?",
        (campaign_id, session_id),
    ).fetchall()
    if "death_and_replacement" in ui_kinds:
        observations["death"].extend(
            f"db:character_lifecycle_events:{row['id']}"
            for row in rows
            if row["to_state"] == "dead"
        )
        observations["replacement"].extend(
            f"db:character_lifecycle_events:{row['id']}"
            for row in rows
            if row["action"] == "replace"
        )
    if "player_departed_to_observer" in ui_kinds:
        observations["player_leave"].extend(
            f"db:character_lifecycle_events:{row['id']}"
            for row in rows
            if row["action"] == "observe"
        )
    if "late_player_joined" in ui_kinds:
        members = connection.execute(
            "SELECT id FROM session_members WHERE campaign_id = ? AND session_id = ? "
            "AND role IN ('player', 'observer') ORDER BY joined_at, id",
            (campaign_id, session_id),
        ).fetchall()
        if len(members) >= 5:
            observations["late_join"].append(
                f"db:session_members:{members[-1]['id']}"
            )


def _append_ending_coverage(
    connection: sqlite3.Connection,
    campaign_id: str,
    evidence: Mapping[str, Any],
    observations: dict[str, list[str]],
) -> None:
    if "authoritative_ending_visible" not in _event_kinds(
        evidence.get("ui_corroboration_events")
    ):
        return
    rows = connection.execute(
        "SELECT id FROM campaign_module_runs WHERE campaign_id = ? AND status = 'completed'",
        (campaign_id,),
    ).fetchall()
    observations["major_choice"].extend(
        f"db:campaign_module_runs:{row['id']}" for row in rows
    )


def _append_growth_and_improvised_coverage(
    connection: sqlite3.Connection,
    campaign_id: str,
    session_id: str,
    improvised_ui_kinds: set[str],
    growth_ui_kinds: set[str],
    observations: dict[str, list[str]],
) -> None:
    if "ruleset_growth_player_confirmed" in growth_ui_kinds:
        growth = connection.execute(
            "SELECT id FROM coc7_gameplay_events WHERE campaign_id = ? AND session_id = ? "
            "AND event_type = 'character.development'",
            (campaign_id, session_id),
        ).fetchall()
        accepted = connection.execute(
            "SELECT id FROM investigator_permanent_change_proposals "
            "WHERE campaign_id = ? AND kind = 'skill' AND status = 'accepted'",
            (campaign_id,),
        ).fetchall()
        if growth and accepted:
            observations["growth"].extend(
                f"db:coc7_gameplay_events:{row['id']}" for row in growth
            )
            observations["growth"].extend(
                f"db:investigator_permanent_change_proposals:{row['id']}"
                for row in accepted
            )
    if "improvised_agent_action_confirmed" in improvised_ui_kinds:
        improvised = connection.execute(
            "SELECT id FROM encounter_action_requests WHERE campaign_id = ? AND session_id = ? "
            "AND action_key = 'improvised' AND status = 'committed'",
            (campaign_id, session_id),
        ).fetchall()
        observations["improvised_action"].extend(
            f"db:encounter_action_requests:{row['id']}" for row in improvised
        )


def _event_kinds(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        str(item["kind"])
        for item in value
        if isinstance(item, Mapping) and isinstance(item.get("kind"), str)
    }


def _open_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve(strict=True)
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _placeholders(values: tuple[str, ...]) -> str:
    return ",".join("?" for _ in values)


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


__all__ = [
    "build_gm_persona_evidence",
    "build_journey_coverage_evidence",
    "build_player_persona_evidence",
    "build_rps07_fault_evidence",
    "build_rps_evidence",
]

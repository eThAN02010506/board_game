"""Read-only projection of durable gameplay authority into UI observations.

The projector never trusts Playwright summary booleans or model-authored IDs.
Every emitted observation is joined back to the UI-observed campaign, Session,
action, and command-batch identifiers.  Incomplete branches remain gaps.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ai_kp.evaluation.ui_journey_action_projection import project_resolved_actions
from ai_kp.evaluation.ui_journey_projection_support import (
    canonical_hash,
    json_array,
    json_object,
    projection_observation,
    sql_placeholders,
)
from ai_kp.platform.resolution.contracts import (
    ResolutionPreview,
    ScenarioContract,
    ScenarioSnapshot,
    WorldCommand,
)
from ai_kp.platform.resolution.kernel import ActionResolutionKernel


@dataclass(frozen=True)
class GameplayProjection:
    observations: tuple[dict[str, Any], ...]
    facts: tuple[dict[str, Any], ...]
    gaps: tuple[str, ...]


def project_gameplay_observations(
    connection: sqlite3.Connection,
    *,
    campaign_id: str,
    session_id: str,
    run_id: str,
    authority_refs: Mapping[str, tuple[str, ...]],
) -> GameplayProjection:
    """Project only gameplay branches with a complete durable authority chain."""

    action_ids = authority_refs.get("action_ids", ())
    command_batch_ids = authority_refs.get("scenario_command_batch_ids", ())
    if not action_ids:
        return GameplayProjection((), (), ("gameplay projection has no observed actions",))
    if not _supports_gameplay_projection(connection):
        return GameplayProjection(
            (),
            (),
            ("authority database predates the durable gameplay projection schema",),
        )

    observations: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    gaps: list[str] = []
    try:
        contract = _effective_contract(connection, run_id, gaps)
        batches = _action_batches(
            connection,
            run_id=run_id,
            contract=contract,
            action_ids=action_ids,
            command_batch_ids=command_batch_ids,
            gaps=gaps,
        )
    except (sqlite3.DatabaseError, TypeError, ValueError):
        return GameplayProjection(
            (), (), ("gameplay authority metadata could not be validated",)
        )

    try:
        _project_npc_response(
            connection,
            campaign_id=campaign_id,
            session_id=session_id,
            action_ids=action_ids,
            contract=contract,
            observations=observations,
        )
    except (sqlite3.DatabaseError, TypeError, ValueError):
        gaps.append("NPC gameplay authority could not be validated")
    try:
        project_resolved_actions(
            connection,
            campaign_id=campaign_id,
            session_id=session_id,
            action_ids=action_ids,
            batches=batches,
            observations=observations,
            facts=facts,
            gaps=gaps,
        )
    except (sqlite3.DatabaseError, TypeError, ValueError):
        gaps.append("resolved-action gameplay authority could not be validated")
    try:
        _project_parallel_settlement(
            connection,
            campaign_id=campaign_id,
            session_id=session_id,
            run_id=run_id,
            batch_ids=authority_refs.get("parallel_batch_ids", ()),
            command_batch_ids=command_batch_ids,
            action_ids=action_ids,
            observations=observations,
            facts=facts,
            gaps=gaps,
        )
    except (sqlite3.DatabaseError, TypeError, ValueError):
        gaps.append("parallel gameplay authority could not be validated")
    return GameplayProjection(
        tuple(observations), tuple(facts), tuple(dict.fromkeys(gaps))
    )


def _project_npc_response(
    connection: sqlite3.Connection,
    *,
    campaign_id: str,
    session_id: str,
    action_ids: tuple[str, ...],
    contract: ScenarioContract | None,
    observations: list[dict[str, Any]],
) -> None:
    if contract is None:
        return
    rows = connection.execute(
        f"""
        SELECT a.id AS action_id, a.resolved_at, p.public_narration,
               p.applied_at, pa.payload_json
        FROM player_actions a
        JOIN turn_proposals p ON p.id = a.proposal_id
        JOIN proposal_actions pa ON pa.proposal_id = p.id
        WHERE a.campaign_id = ? AND a.session_id = ?
          AND a.id IN ({sql_placeholders(action_ids)})
          AND a.status = 'resolved' AND p.status = 'approved'
          AND pa.action_type = 'tabletop_turn'
          AND (SELECT COUNT(*) FROM proposal_actions one
               WHERE one.proposal_id = p.id
                 AND one.action_type = 'tabletop_turn') = 1
        ORDER BY p.applied_at, p.id
        """,
        (campaign_id, session_id, *action_ids),
    ).fetchall()
    obligations: dict[str, list[Any]] = {}
    for item in contract.response_obligations:
        obligations.setdefault(item.entity_id, []).append(item)
    for row in rows:
        payload = json_object(row["payload_json"])
        response = payload.get("response")
        if payload.get("route") != "roleplay" or not isinstance(response, Mapping):
            continue
        response_text = response.get("public_narration")
        speakers = response.get("speaker_entity_ids")
        traces = response.get("actor_traces")
        if (
            not isinstance(response_text, str)
            or len(response_text.strip()) < 12
            or not isinstance(speakers, list)
            or not isinstance(traces, list)
        ):
            continue
        traced = {
            str(item.get("entity_id"))
            for item in traces
            if isinstance(item, Mapping) and item.get("entity_id")
        }
        for entity_id in dict.fromkeys(str(item) for item in speakers):
            entity_obligations = obligations.get(entity_id, [])
            if not entity_obligations or entity_id not in traced:
                continue
            fact_ids = [
                f"{obligation.obligation_id}:fact:{index}"
                for obligation in entity_obligations
                for index, fact in enumerate(obligation.facts_to_convey, start=1)
                if not obligation.conditions and fact and fact in response_text
            ]
            if not fact_ids:
                continue
            observations.append(
                projection_observation(
                    row["applied_at"] or row["resolved_at"],
                    "npc_response",
                    session_id,
                    action_id=str(row["action_id"]),
                    npc_entity_id=entity_id,
                    response_text=response_text,
                    knowledge_fact_ids=fact_ids,
                )
            )
            return


def _project_parallel_settlement(
    connection: sqlite3.Connection,
    *,
    campaign_id: str,
    session_id: str,
    run_id: str,
    batch_ids: tuple[str, ...],
    command_batch_ids: tuple[str, ...],
    action_ids: tuple[str, ...],
    observations: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    gaps: list[str],
) -> None:
    if not batch_ids:
        return
    rows = connection.execute(
        f"""
        SELECT b.id, b.scenario_command_batch_id, b.settled_at,
               cb.snapshot_json
        FROM parallel_action_batches b
        JOIN scenario_command_batches cb ON cb.id = b.scenario_command_batch_id
        WHERE b.campaign_id = ? AND b.session_id = ? AND b.run_id = ?
          AND b.status = 'settled' AND b.id IN ({sql_placeholders(batch_ids)})
          AND cb.id IN ({sql_placeholders(command_batch_ids) if command_batch_ids else "''"})
        ORDER BY b.settled_at, b.id
        """,
        (campaign_id, session_id, run_id, *batch_ids, *command_batch_ids),
    ).fetchall()
    for row in rows:
        items = connection.execute(
            """
            SELECT i.action_id, i.adjudication_id, a.member_id
            FROM parallel_action_batch_items i
            JOIN player_actions a ON a.id = i.action_id
            WHERE i.batch_id = ? ORDER BY i.priority DESC, i.action_id
            """,
            (row["id"],),
        ).fetchall()
        if len(items) < 2 or any(str(item["action_id"]) not in action_ids for item in items):
            continue
        snapshot = json_object(row["snapshot_json"])
        try:
            parsed = ScenarioSnapshot.model_validate(snapshot)
        except ValueError:
            gaps.append(f"parallel batch {row['id']} has an invalid scenario snapshot")
            continue
        if not parsed.scene_id:
            gaps.append(f"parallel batch {row['id']} has no authoritative regroup scene")
            continue
        observations.append(
            projection_observation(
                row["settled_at"],
                "parallel_settlement",
                session_id,
                batch_id=str(row["id"]),
                participant_member_ids=[str(item["member_id"]) for item in items],
                action_ids=[str(item["action_id"]) for item in items],
                resolution_ids=[str(item["adjudication_id"]) for item in items],
                regroup_location_id=parsed.scene_id,
                authority_fingerprint=canonical_hash(snapshot),
            )
        )
        facts.append(
            {
                "kind": "parallel_settlement",
                "batch_id": str(row["id"]),
                "command_batch_id": str(row["scenario_command_batch_id"]),
                "participant_count": len(items),
                "regroup_location_id": parsed.scene_id,
            }
        )
        return


def _action_batches(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    contract: ScenarioContract | None,
    action_ids: tuple[str, ...],
    command_batch_ids: tuple[str, ...],
    gaps: list[str],
) -> dict[str, dict[str, Any]]:
    if not command_batch_ids:
        return {}
    rows = connection.execute(
        f"""
        SELECT id, idempotency_key, expected_version, result_version,
               preview_hash, preview_json, commands_json, snapshot_json, created_at
        FROM scenario_command_batches
        WHERE run_id = ? AND batch_kind = 'action'
          AND id IN ({sql_placeholders(command_batch_ids)})
        ORDER BY result_version, id
        """,
        (run_id, *command_batch_ids),
    ).fetchall()
    by_action: dict[str, dict[str, Any]] = {}
    prior_snapshots = {
        int(row["result_version"]): json_object(row["snapshot_json"]) for row in rows
    }
    if contract is not None:
        prior_snapshots[0] = contract.initial_snapshot(run_id).model_dump(mode="json")
    for row in rows:
        preview_payload = json_object(row["preview_json"])
        try:
            preview = ResolutionPreview.model_validate(preview_payload)
            snapshot = ScenarioSnapshot.model_validate(json_object(row["snapshot_json"]))
        except ValueError:
            gaps.append(f"command batch {row['id']} has an invalid preview or snapshot")
            continue
        action_id = preview.action_id
        idempotency_key = str(row["idempotency_key"])
        key_prefix = f"action:{action_id}:v{int(row['expected_version'])}:"
        outcome = idempotency_key.removeprefix(key_prefix)
        try:
            expected_commands = [
                item.model_dump(mode="json")
                for item in preview.commands_for_outcome(outcome)
            ]
        except ValueError:
            expected_commands = []
        commands = json_array(row["commands_json"])
        applied_commands = [
            dict(item)
            for item in commands
            if isinstance(item, Mapping)
            and not (
                item.get("kind") == "emit_event"
                and item.get("event_type") == "action_resolved"
            )
        ]
        before = prior_snapshots.get(int(row["expected_version"]))
        replay_matches = False
        if contract is not None and isinstance(before, Mapping):
            try:
                replayed = ActionResolutionKernel.from_contract(contract).preflight(
                    ScenarioSnapshot.model_validate(before),
                    tuple(WorldCommand.model_validate(item) for item in commands),
                )
                replay_matches = replayed.model_dump(mode="json") == snapshot.model_dump(
                    mode="json"
                )
            except (TypeError, ValueError):
                replay_matches = False
        if (
            action_id not in action_ids
            or preview.preview_hash != row["preview_hash"]
            or canonical_hash(preview.canonical_payload()) != preview.preview_hash
            or preview.run_id != run_id
            or preview.run_version != int(row["expected_version"])
            or snapshot.run_id != run_id
            or snapshot.run_version != int(row["result_version"])
            or not idempotency_key.startswith(key_prefix)
            or not expected_commands
            or applied_commands[: len(expected_commands)] != expected_commands
            or not replay_matches
        ):
            gaps.append(f"command batch {row['id']} does not match an observed action preview")
            continue
        snapshot_payload = snapshot.model_dump(mode="json")
        if not commands:
            continue
        by_action[action_id] = {
            **dict(row),
            "preview": preview.model_dump(mode="json"),
            "commands": commands,
            "outcome": outcome,
            "snapshot": snapshot_payload,
            "snapshot_before": before,
        }
    return by_action


def _effective_contract(
    connection: sqlite3.Connection, run_id: str, gaps: list[str]
) -> ScenarioContract | None:
    row = connection.execute(
        """
        SELECT v.contract_hash, v.contract_json
        FROM module_run_contract_bindings b
        JOIN scenario_contract_versions v ON v.id = b.contract_version_id
        WHERE b.run_id = ? AND v.status = 'published'
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    payload = json_object(row["contract_json"])
    overlay = connection.execute(
        """
        SELECT merged_contract_hash, merged_contract_json
        FROM scenario_contract_overlays
        WHERE run_id = ? AND status = 'active'
        ORDER BY sequence_no DESC LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    expected_hash = str(row["contract_hash"])
    if overlay is not None:
        payload = json_object(overlay["merged_contract_json"])
        expected_hash = str(overlay["merged_contract_hash"])
    if canonical_hash(payload) != expected_hash:
        gaps.append("effective gameplay contract hash does not match its payload")
        return None
    try:
        return ScenarioContract.model_validate(payload)
    except ValueError:
        gaps.append("effective gameplay contract payload is invalid")
        return None


def _supports_gameplay_projection(connection: sqlite3.Connection) -> bool:
    required = {
        "player_actions": {"member_id", "resolved_at", "proposal_id"},
        "turn_proposals": {"public_narration", "applied_at"},
        "proposal_actions": {"action_type", "payload_json"},
        "player_action_adjudications": {"mode", "selected_skill"},
        "player_action_adjudication_events": {"actor_member_id", "created_at"},
        "skill_checks": {"skill_key", "success_level", "passed"},
        "skill_check_push_decisions": {"decision", "actor_member_id"},
        "scenario_command_batches": {
            "batch_kind", "idempotency_key", "preview_hash", "preview_json"
        },
        "parallel_action_batches": {"scenario_command_batch_id", "settled_at"},
        "parallel_action_batch_items": {"action_id", "adjudication_id"},
    }
    for table, columns in required.items():
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        if not rows or not columns.issubset({str(row["name"]) for row in rows}):
            return False
    return True


__all__ = ["GameplayProjection", "project_gameplay_observations"]

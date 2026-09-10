"""Fail-closed evidence verification for one real, longitudinal UI journey.

The verifier consumes observations; it never accepts a producer-owned ``passed``
flag.  The schema is intentionally narrow so that adding a claim requires adding
an independently inspectable observation and a cross-record invariant here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

SCHEMA_VERSION = 1
REQUIRED_OBSERVATION_KINDS = frozenset(
    {
        "ui_module_imported",
        "ui_scenario_reviewed",
        "ui_scenario_published",
        "ui_scenario_bound",
        "npc_response",
        "direct_resolution",
        "skill_confirmed",
        "check_success",
        "check_failure_cost",
        "failure_followup",
        "bounded_deviation",
        "parallel_settlement",
        "backend_stopped",
        "backend_started",
        "continue_before_restart",
        "continue_after_restart",
        "authoritative_ending",
    }
)
_RESTART_OBSERVATION_KINDS = frozenset(
    {
        "backend_stopped",
        "backend_started",
        "continue_before_restart",
        "continue_after_restart",
    }
)
ANCHOR_OBSERVATION_KINDS = REQUIRED_OBSERVATION_KINDS - _RESTART_OBSERVATION_KINDS

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
_SECRET_VALUE = re.compile(
    r"(?:\bBearer\s+[A-Za-z0-9._~+/=-]{8,}|\bsk-[A-Za-z0-9_-]{8,})",
    re.IGNORECASE,
)
_SECRET_KEY_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "password",
        "private_key",
        "secret",
        "token",
    }
)

_ROOT_KEYS = frozenset(
    {
        "schema_version",
        "journey_id",
        "session_id",
        "automation_mode",
        "produced_at",
        "module",
        "source_commit",
        "model_generation",
        "ruleset",
        "players",
        "observations",
    }
)
_MODULE_KEYS = frozenset({"sha256", "artifact_name"})
_MODEL_KEYS = frozenset({"provider", "model", "generation_id"})
_RULESET_KEYS = frozenset({"id", "version"})
_PLAYER_KEYS = frozenset({"member_id", "investigator_id"})
_COMMON_OBSERVATION_KEYS = frozenset({"sequence", "observed_at", "kind", "session_id"})
_OBSERVATION_FIELDS: dict[str, frozenset[str]] = {
    "ui_module_imported": frozenset({"document_id", "module_sha256"}),
    "ui_scenario_reviewed": frozenset({"review_id", "decision", "finding_ids"}),
    "ui_scenario_published": frozenset({"contract_id", "contract_version"}),
    "ui_scenario_bound": frozenset({"contract_id", "contract_version"}),
    "npc_response": frozenset(
        {"action_id", "npc_entity_id", "response_text", "knowledge_fact_ids"}
    ),
    "direct_resolution": frozenset({"action_id", "result_text", "applied_effect_ids"}),
    "skill_confirmed": frozenset(
        {"action_id", "check_id", "skill_id", "difficulty", "confirmed_by_member_id"}
    ),
    "check_success": frozenset(
        {"action_id", "check_id", "degree", "result_text", "applied_effect_ids"}
    ),
    "check_failure_cost": frozenset(
        {
            "action_id",
            "check_id",
            "consequence_text",
            "applied_effect_ids",
            "fingerprint_before",
            "fingerprint_after",
        }
    ),
    "failure_followup": frozenset({"check_id", "mode", "resolution_id"}),
    "bounded_deviation": frozenset(
        {
            "action_id",
            "proposal_id",
            "constraint_ids",
            "confirmed_by_member_id",
            "result_text",
            "applied_effect_ids",
        }
    ),
    "parallel_settlement": frozenset(
        {
            "batch_id",
            "participant_member_ids",
            "action_ids",
            "resolution_ids",
            "regroup_location_id",
            "authority_fingerprint",
        }
    ),
    "continue_before_restart": frozenset(
        {"continue_id", "authority_fingerprint", "process_instance_id", "persistent_store_id"}
    ),
    "backend_stopped": frozenset(
        {"process_instance_id", "os_pid", "exit_code", "persistent_store_id"}
    ),
    "backend_started": frozenset(
        {"process_instance_id", "os_pid", "health_status", "persistent_store_id"}
    ),
    "continue_after_restart": frozenset(
        {"continue_id", "authority_fingerprint", "process_instance_id", "persistent_store_id"}
    ),
    "authoritative_ending": frozenset(
        {"ending_id", "authority_event_id", "authority_fingerprint"}
    ),
}


@dataclass(frozen=True)
class UiJourneyEvidenceResult:
    accepted: bool
    blockers: tuple[str, ...]
    metrics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "blockers": list(self.blockers),
            "metrics": self.metrics,
        }


def verify_real_ui_journey_evidence(evidence: Mapping[str, Any]) -> UiJourneyEvidenceResult:
    """Verify a single-Session Full AI journey from typed observations.

    Invalid or unknown fields fail closed.  In particular, a producer cannot
    influence the decision with ``ui_journey_passed`` or another summary flag.
    """

    return _verify_ui_journey(
        evidence,
        required_kinds=REQUIRED_OBSERVATION_KINDS,
        verify_restart=True,
    )


def verify_real_ui_journey_anchor(
    evidence: Mapping[str, Any],
) -> UiJourneyEvidenceResult:
    """Verify one complete gameplay anchor without owning restart evidence.

    Multi-Session verification has a stronger checkpoint-based restart gate.
    Keeping restart observations out of this component prevents two verifiers
    from assigning incompatible meanings to the same End/Continue transition.
    """

    return _verify_ui_journey(
        evidence,
        required_kinds=ANCHOR_OBSERVATION_KINDS,
        verify_restart=False,
    )


def _verify_ui_journey(
    evidence: Mapping[str, Any],
    *,
    required_kinds: frozenset[str],
    verify_restart: bool,
) -> UiJourneyEvidenceResult:
    blockers: list[str] = []
    if not isinstance(evidence, Mapping):
        return UiJourneyEvidenceResult(False, ("evidence must be an object",), {})

    _find_secrets(evidence, "$", blockers)
    _require_exact_keys(evidence, _ROOT_KEYS, "evidence", blockers)
    if evidence.get("schema_version") != SCHEMA_VERSION:
        blockers.append(f"schema_version must equal {SCHEMA_VERSION}")

    journey_id = _required_string(evidence.get("journey_id"), "journey_id", blockers)
    session_id = _required_string(evidence.get("session_id"), "session_id", blockers)
    if evidence.get("automation_mode") != "full_ai":
        blockers.append("automation_mode must be full_ai")
    _timestamp(evidence.get("produced_at"), "produced_at", blockers)

    module = _strict_object(evidence.get("module"), _MODULE_KEYS, "module", blockers)
    module_sha = _required_string(module.get("sha256"), "module.sha256", blockers)
    if module_sha and not _SHA256.fullmatch(module_sha):
        blockers.append("module.sha256 must be a lowercase SHA-256 digest")
    _required_string(module.get("artifact_name"), "module.artifact_name", blockers)

    source_commit = _required_string(evidence.get("source_commit"), "source_commit", blockers)
    if source_commit and not _COMMIT.fullmatch(source_commit):
        blockers.append("source_commit must be a 7-40 character lowercase Git object id")

    model = _strict_object(
        evidence.get("model_generation"), _MODEL_KEYS, "model_generation", blockers
    )
    for key in sorted(_MODEL_KEYS):
        _required_string(model.get(key), f"model_generation.{key}", blockers)
    ruleset = _strict_object(evidence.get("ruleset"), _RULESET_KEYS, "ruleset", blockers)
    for key in sorted(_RULESET_KEYS):
        _required_string(ruleset.get(key), f"ruleset.{key}", blockers)

    player_ids = _validate_players(evidence.get("players"), blockers)
    observations = _validate_observations(
        evidence.get("observations"), session_id=session_id, blockers=blockers
    )
    by_kind: dict[str, list[Mapping[str, Any]]] = {}
    for observation in observations:
        by_kind.setdefault(str(observation["kind"]), []).append(observation)

    missing = sorted(required_kinds - by_kind.keys())
    if missing:
        blockers.append("missing required observations: " + ", ".join(missing))

    unexpected_restart = sorted(_RESTART_OBSERVATION_KINDS & by_kind.keys())
    if not verify_restart and unexpected_restart:
        blockers.append(
            "gameplay anchor must not contain restart observations: "
            + ", ".join(unexpected_restart)
        )

    _verify_setup_chain(by_kind, module_sha, blockers)
    _verify_gameplay_branches(by_kind, player_ids, blockers)
    if verify_restart:
        _verify_restart_chain(by_kind, blockers)
    _verify_ending_order(by_kind, blockers, require_restart=verify_restart)

    metrics = {
        "journey_id": journey_id,
        "session_id": session_id,
        "players": len(player_ids),
        "observations": len(observations),
        "observed_kinds": len(by_kind),
    }
    return UiJourneyEvidenceResult(not blockers, tuple(blockers), metrics)


def _validate_players(value: Any, blockers: list[str]) -> frozenset[str]:
    if not isinstance(value, list) or len(value) != 4:
        blockers.append("players must contain exactly four records")
        return frozenset()
    members: set[str] = set()
    investigators: set[str] = set()
    for index, raw in enumerate(value):
        player = _strict_object(raw, _PLAYER_KEYS, f"players[{index}]", blockers)
        member = _required_string(player.get("member_id"), f"players[{index}].member_id", blockers)
        investigator = _required_string(
            player.get("investigator_id"), f"players[{index}].investigator_id", blockers
        )
        if member:
            members.add(member)
        if investigator:
            investigators.add(investigator)
    if len(members) != 4:
        blockers.append("four distinct player member_id values are required")
    if len(investigators) != 4:
        blockers.append("four distinct investigator_id values are required")
    return frozenset(members)


def _validate_observations(
    value: Any, *, session_id: str, blockers: list[str]
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list) or not value:
        blockers.append("observations must be a non-empty list")
        return ()
    valid: list[Mapping[str, Any]] = []
    previous_sequence = 0
    previous_time: datetime | None = None
    for index, raw in enumerate(value):
        label = f"observations[{index}]"
        if not isinstance(raw, Mapping):
            blockers.append(f"{label} must be an object")
            continue
        kind = raw.get("kind")
        if not isinstance(kind, str) or kind not in _OBSERVATION_FIELDS:
            blockers.append(f"{label}.kind is unknown")
            continue
        _require_exact_keys(raw, _COMMON_OBSERVATION_KEYS | _OBSERVATION_FIELDS[kind], label, blockers)
        sequence = raw.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= previous_sequence:
            blockers.append(f"{label}.sequence must be a strictly increasing positive integer")
        else:
            previous_sequence = sequence
        observed_at = _timestamp(raw.get("observed_at"), f"{label}.observed_at", blockers)
        if observed_at is not None and previous_time is not None and observed_at <= previous_time:
            blockers.append(f"{label}.observed_at must be strictly increasing")
        if observed_at is not None:
            previous_time = observed_at
        if raw.get("session_id") != session_id or not session_id:
            blockers.append(f"{label}.session_id must match the journey session")
        _validate_observation_fields(raw, kind, label, blockers)
        valid.append(raw)
    return tuple(valid)


def _validate_observation_fields(
    item: Mapping[str, Any], kind: str, label: str, blockers: list[str]
) -> None:
    string_fields = _OBSERVATION_FIELDS[kind] - {
        "finding_ids",
        "knowledge_fact_ids",
        "applied_effect_ids",
        "constraint_ids",
        "participant_member_ids",
        "action_ids",
        "resolution_ids",
        "os_pid",
        "exit_code",
        "health_status",
    }
    for field in sorted(string_fields):
        _required_string(item.get(field), f"{label}.{field}", blockers)
    for field in (
        "finding_ids",
        "knowledge_fact_ids",
        "applied_effect_ids",
        "constraint_ids",
        "participant_member_ids",
        "action_ids",
        "resolution_ids",
    ):
        if field in item:
            allow_empty = field == "finding_ids"
            _string_list(item.get(field), f"{label}.{field}", blockers, allow_empty=allow_empty)
    if kind == "ui_scenario_reviewed" and item.get("decision") != "approved":
        blockers.append(f"{label}.decision must be approved")
    if kind == "skill_confirmed" and item.get("difficulty") not in {
        "regular",
        "hard",
        "extreme",
    }:
        blockers.append(f"{label}.difficulty must be regular, hard, or extreme")
    if kind == "check_success" and item.get("degree") not in {
        "regular",
        "hard",
        "extreme",
        "critical",
    }:
        blockers.append(f"{label}.degree is not a successful check degree")
    if kind == "failure_followup" and item.get("mode") not in {"push", "accept_failure"}:
        blockers.append(f"{label}.mode must be push or accept_failure")
    for field in ("response_text", "result_text", "consequence_text"):
        text = item.get(field)
        if field in item and isinstance(text, str) and len(text.strip()) < 12:
            blockers.append(f"{label}.{field} is too short to evidence a concrete outcome")
    for field in (
        "authority_fingerprint",
        "fingerprint_before",
        "fingerprint_after",
    ):
        fingerprint = item.get(field)
        if field in item and isinstance(fingerprint, str) and not _SHA256.fullmatch(fingerprint):
            blockers.append(f"{label}.{field} must be a lowercase SHA-256 digest")
    if kind == "backend_stopped":
        _positive_int(item.get("os_pid"), f"{label}.os_pid", blockers)
        if isinstance(item.get("exit_code"), bool) or not isinstance(item.get("exit_code"), int):
            blockers.append(f"{label}.exit_code must be an integer")
    if kind == "backend_started":
        _positive_int(item.get("os_pid"), f"{label}.os_pid", blockers)
        if item.get("health_status") != 200:
            blockers.append(f"{label}.health_status must equal 200")


def _verify_setup_chain(
    by_kind: Mapping[str, list[Mapping[str, Any]]], module_sha: str, blockers: list[str]
) -> None:
    chain = [
        "ui_module_imported",
        "ui_scenario_reviewed",
        "ui_scenario_published",
        "ui_scenario_bound",
    ]
    if not all(by_kind.get(kind) for kind in chain):
        return
    records = [by_kind[kind][0] for kind in chain]
    if [item["sequence"] for item in records] != sorted(item["sequence"] for item in records):
        blockers.append("UI import, review, publish, and bind observations are out of order")
    if records[0].get("module_sha256") != module_sha:
        blockers.append("UI import module hash does not match journey metadata")
    contract_identity = (records[2].get("contract_id"), records[2].get("contract_version"))
    if contract_identity != (records[3].get("contract_id"), records[3].get("contract_version")):
        blockers.append("published and bound scenario contract identities differ")


def _verify_gameplay_branches(
    by_kind: Mapping[str, list[Mapping[str, Any]]],
    player_ids: frozenset[str],
    blockers: list[str],
) -> None:
    confirms = {item.get("check_id"): item for item in by_kind.get("skill_confirmed", [])}
    successes = by_kind.get("check_success", [])
    failures = by_kind.get("check_failure_cost", [])
    followups = {
        item.get("check_id"): item for item in by_kind.get("failure_followup", [])
    }
    for success in successes:
        confirmation = confirms.get(success.get("check_id"))
        if confirmation is None or confirmation["sequence"] >= success["sequence"]:
            blockers.append("successful check lacks a preceding player skill confirmation")
    for failure in failures:
        confirmation = confirms.get(failure.get("check_id"))
        if confirmation is None or confirmation["sequence"] >= failure["sequence"]:
            blockers.append("failed check lacks a preceding player skill confirmation")
        if failure.get("fingerprint_before") == failure.get("fingerprint_after"):
            blockers.append("ordinary failure cost did not change authoritative state")
        followup = followups.get(failure.get("check_id"))
        if followup is None or followup["sequence"] <= failure["sequence"]:
            blockers.append("ordinary failure lacks push-or-accept follow-up")
    for confirm in confirms.values():
        member_id = confirm.get("confirmed_by_member_id")
        if member_id not in player_ids:
            blockers.append("skill confirmation was not made by one of the four players")
    for deviation in by_kind.get("bounded_deviation", []):
        if deviation.get("confirmed_by_member_id") not in player_ids:
            blockers.append("bounded deviation was not confirmed by one of the four players")
    for settlement in by_kind.get("parallel_settlement", []):
        participants = settlement.get("participant_member_ids")
        actions = settlement.get("action_ids")
        resolutions = settlement.get("resolution_ids")
        if not isinstance(participants, list) or len(set(participants)) < 2:
            blockers.append("parallel settlement must include at least two distinct players")
        elif not set(participants).issubset(player_ids):
            blockers.append("parallel settlement contains a non-player participant")
        if not isinstance(actions, list) or not isinstance(resolutions, list) or len(actions) != len(resolutions):
            blockers.append("parallel settlement must pair every action with one resolution")


def _verify_restart_chain(
    by_kind: Mapping[str, list[Mapping[str, Any]]], blockers: list[str]
) -> None:
    kinds = (
        "continue_before_restart",
        "backend_stopped",
        "backend_started",
        "continue_after_restart",
    )
    if not all(by_kind.get(kind) for kind in kinds):
        return
    before, stopped, started, after = (by_kind[kind][0] for kind in kinds)
    if not (before["sequence"] < stopped["sequence"] < started["sequence"] < after["sequence"]):
        blockers.append("Continue/restart observations are out of order")
    old_instance = before.get("process_instance_id")
    new_instance = after.get("process_instance_id")
    if stopped.get("process_instance_id") != old_instance:
        blockers.append("stopped backend does not match the pre-restart process")
    if started.get("process_instance_id") != new_instance:
        blockers.append("started backend does not match the post-restart process")
    if old_instance == new_instance or stopped.get("os_pid") == started.get("os_pid"):
        blockers.append("backend restart requires distinct process instances and OS pids")
    stores = {
        before.get("persistent_store_id"),
        stopped.get("persistent_store_id"),
        started.get("persistent_store_id"),
        after.get("persistent_store_id"),
    }
    if len(stores) != 1:
        blockers.append("backend restart did not preserve the persistent store identity")
    if before.get("continue_id") != after.get("continue_id"):
        blockers.append("Continue observations refer to different checkpoints")
    if before.get("authority_fingerprint") != after.get("authority_fingerprint"):
        blockers.append("authoritative fingerprint drifted across restart and Continue")


def _verify_ending_order(
    by_kind: Mapping[str, list[Mapping[str, Any]]],
    blockers: list[str],
    *,
    require_restart: bool,
) -> None:
    endings = by_kind.get("authoritative_ending", [])
    bindings = by_kind.get("ui_scenario_bound", [])
    continues = by_kind.get("continue_after_restart", [])
    if endings and bindings and endings[0]["sequence"] <= bindings[0]["sequence"]:
        blockers.append("authoritative ending occurred before scenario binding")
    if (
        require_restart
        and endings
        and continues
        and endings[0]["sequence"] <= continues[0]["sequence"]
    ):
        blockers.append("authoritative ending must be observed after restart recovery")


def _strict_object(
    value: Any, allowed: frozenset[str], label: str, blockers: list[str]
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        blockers.append(f"{label} must be an object")
        return {}
    _require_exact_keys(value, allowed, label, blockers)
    return value


def _require_exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], label: str, blockers: list[str]
) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        blockers.append(f"{label} missing fields: {', '.join(missing)}")
    if unknown:
        blockers.append(f"{label} contains unknown fields: {', '.join(unknown)}")


def _required_string(value: Any, label: str, blockers: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        blockers.append(f"{label} must be a non-empty string")
        return ""
    return value


def _string_list(value: Any, label: str, blockers: list[str], *, allow_empty: bool = False) -> None:
    if not isinstance(value, list) or (not value and not allow_empty):
        blockers.append(f"{label} must be {'a' if allow_empty else 'a non-empty'} string list")
        return
    if any(not isinstance(item, str) or not item.strip() for item in value):
        blockers.append(f"{label} must contain only non-empty strings")
    elif len(value) != len(set(value)):
        blockers.append(f"{label} must not contain duplicates")


def _positive_int(value: Any, label: str, blockers: list[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        blockers.append(f"{label} must be a positive integer")


def _timestamp(value: Any, label: str, blockers: list[str]) -> datetime | None:
    if not isinstance(value, str):
        blockers.append(f"{label} must be an ISO-8601 timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        blockers.append(f"{label} must be an ISO-8601 timestamp")
        return None
    if parsed.tzinfo is None:
        blockers.append(f"{label} must include a timezone")
        return None
    return parsed


def _find_secrets(value: Any, path: str, blockers: list[str]) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            if (
                normalized in _SECRET_KEY_PARTS
                or normalized.endswith(("_secret", "_token"))
            ):
                blockers.append(f"secret-bearing field is forbidden at {path}.{key}")
            _find_secrets(nested, f"{path}.{key}", blockers)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _find_secrets(nested, f"{path}[{index}]", blockers)
    elif isinstance(value, str) and _SECRET_VALUE.search(value):
        blockers.append(f"secret-like value is forbidden at {path}")


__all__ = [
    "ANCHOR_OBSERVATION_KINDS",
    "REQUIRED_OBSERVATION_KINDS",
    "SCHEMA_VERSION",
    "UiJourneyEvidenceResult",
    "verify_real_ui_journey_anchor",
    "verify_real_ui_journey_evidence",
]

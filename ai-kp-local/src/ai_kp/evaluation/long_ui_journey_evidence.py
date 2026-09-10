"""Fail-closed verification for a real, multi-Session player-UI journey.

This verifier only evaluates an already captured evidence envelope.  It does
not reconstruct database state, run a model, or trust producer-owned pass
claims.  A release claim is accepted only when the longitudinal authority
chain, the existing strict single-Session anchor, and the process-restart
checkpoint corroboration all independently verify.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from typing import Any

from ai_kp.evaluation.ui_journey_evidence import verify_real_ui_journey_anchor

SCHEMA_VERSION = 1
MINIMUM_ENDED_EPISODES = 10

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
        "campaign_id",
        "table_session_id",
        "automation_mode",
        "produced_at",
        "source_commit",
        "ruleset",
        "players",
        "module_runs",
        "episodes",
        "restart_checkpoint",
        "ui_corroboration",
        "single_session_anchor",
    }
)
_RULESET_KEYS = frozenset({"id", "version"})
_PLAYER_KEYS = frozenset({"member_id", "investigator_id"})
_MODULE_RUN_KEYS = frozenset(
    {
        "id",
        "module_id",
        "module_sha256",
        "status",
        "automation_mode",
        "started_at",
        "completed_at",
    }
)
_EPISODE_KEYS = frozenset(
    {
        "sequence_no",
        "id",
        "status",
        "version",
        "event_start_rowid",
        "started_at",
        "ended_at",
        "snapshot",
        "next_episode",
    }
)
_SNAPSHOT_KEYS = frozenset(
    {
        "id",
        "client_end_id",
        "event_window_hash",
        "event_ids",
        "generation_cutoff",
        "public_projection_hash",
        "observer_projection_hash",
        "kp_projection_hash",
        "checkpoint_fingerprint",
    }
)
_NEXT_EPISODE_KEYS = frozenset({"id", "sequence_no", "client_continue_id"})
_RESTART_KEYS = frozenset({"before", "stopped", "started", "after"})
_CHECKPOINT_OBSERVATION_KEYS = frozenset(
    {
        "observed_at",
        "process_instance_id",
        "os_pid",
        "persistent_store_id",
        "checkpoint_snapshot_id",
        "checkpoint_fingerprint",
    }
)
_STOPPED_KEYS = frozenset(
    {
        "observed_at",
        "process_instance_id",
        "os_pid",
        "persistent_store_id",
        "clean_shutdown",
    }
)
_STARTED_KEYS = frozenset(
    {
        "observed_at",
        "process_instance_id",
        "os_pid",
        "persistent_store_id",
        "health_status",
    }
)
_UI_EVENT_KEYS = frozenset({"kind", "observed_at", "session_index"})
_UI_EVENT_KINDS = frozenset(
    {
        "authoritative_ending_visible",
        "continue_campaign_visible",
        "session_end_visible",
    }
)


@dataclass(frozen=True)
class LongUiJourneyEvidenceResult:
    accepted: bool
    blockers: tuple[str, ...]
    metrics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "blockers": list(self.blockers),
            "metrics": self.metrics,
        }


def verify_long_ui_journey_evidence(
    evidence: Mapping[str, Any],
) -> LongUiJourneyEvidenceResult:
    """Verify ten or more consecutive ended episodes from real player UIs.

    The ``single_session_anchor`` object is passed unchanged to
    :func:`verify_real_ui_journey_anchor`.  Restart is independently verified
    from immutable Session-End checkpoints. The final decision is the strict
    conjunction of the longitudinal envelope, that anchor, and restart proof.
    """

    longitudinal_blockers: list[str] = []
    restart_blockers: list[str] = []
    if not isinstance(evidence, Mapping):
        return LongUiJourneyEvidenceResult(False, ("evidence must be an object",), {})

    _find_forbidden_content(evidence, "$", longitudinal_blockers)
    _require_exact_keys(evidence, _ROOT_KEYS, "evidence", longitudinal_blockers)
    if evidence.get("schema_version") != SCHEMA_VERSION:
        longitudinal_blockers.append(f"schema_version must equal {SCHEMA_VERSION}")

    journey_id = _required_string(
        evidence.get("journey_id"), "journey_id", longitudinal_blockers
    )
    campaign_id = _required_string(
        evidence.get("campaign_id"), "campaign_id", longitudinal_blockers
    )
    table_session_id = _required_string(
        evidence.get("table_session_id"), "table_session_id", longitudinal_blockers
    )
    if evidence.get("automation_mode") != "full_ai":
        longitudinal_blockers.append("automation_mode must be full_ai")
    produced_at = _timestamp(
        evidence.get("produced_at"), "produced_at", longitudinal_blockers
    )
    source_commit = _required_string(
        evidence.get("source_commit"), "source_commit", longitudinal_blockers
    )
    if source_commit and not _COMMIT.fullmatch(source_commit):
        longitudinal_blockers.append(
            "source_commit must be a 7-40 character lowercase Git object id"
        )

    ruleset = _strict_object(
        evidence.get("ruleset"), _RULESET_KEYS, "ruleset", longitudinal_blockers
    )
    for key in sorted(_RULESET_KEYS):
        _required_string(ruleset.get(key), f"ruleset.{key}", longitudinal_blockers)

    player_ids = _validate_players(evidence.get("players"), longitudinal_blockers)
    module_hashes = _validate_module_runs(
        evidence.get("module_runs"), longitudinal_blockers
    )
    episodes = _validate_episodes(evidence.get("episodes"), longitudinal_blockers)
    _validate_ui_corroboration(
        evidence.get("ui_corroboration"), episodes, longitudinal_blockers
    )
    if episodes and produced_at is not None:
        final_ended_at = episodes[-1]["ended_at"]
        if isinstance(final_ended_at, datetime) and produced_at < final_ended_at:
            longitudinal_blockers.append("produced_at precedes the final ended episode")

    anchor = evidence.get("single_session_anchor")
    # Deliberately no normalization, copying, or reconstruction here.  The
    # established strict verifier is the sole authority for this component.
    anchor_result = verify_real_ui_journey_anchor(anchor)  # type: ignore[arg-type]
    anchor_blockers = [
        f"single_session_anchor: {blocker}" for blocker in anchor_result.blockers
    ]
    if isinstance(anchor, Mapping):
        if anchor.get("session_id") != table_session_id:
            longitudinal_blockers.append(
                "single_session_anchor.session_id must match table_session_id"
            )
        if anchor.get("automation_mode") != evidence.get("automation_mode"):
            longitudinal_blockers.append(
                "single_session_anchor automation_mode differs from the long journey"
            )
        if anchor.get("source_commit") != source_commit:
            longitudinal_blockers.append(
                "single_session_anchor source_commit differs from the long journey"
            )
        if anchor.get("ruleset") != ruleset:
            longitudinal_blockers.append(
                "single_session_anchor ruleset differs from the long journey"
            )
        anchor_players = anchor.get("players")
        if isinstance(anchor_players, list):
            anchor_player_ids = {
                item.get("member_id")
                for item in anchor_players
                if isinstance(item, Mapping)
            }
            if anchor_player_ids != set(player_ids):
                longitudinal_blockers.append(
                    "single_session_anchor players differ from the long journey"
                )
        anchor_module = anchor.get("module")
        if (
            isinstance(anchor_module, Mapping)
            and anchor_module.get("sha256") not in module_hashes
        ):
            longitudinal_blockers.append(
                "single_session_anchor module is absent from module_runs"
            )

    _validate_restart_checkpoint(
        evidence.get("restart_checkpoint"), episodes, restart_blockers
    )

    longitudinal_accepted = not longitudinal_blockers
    anchor_accepted = anchor_result.accepted
    restart_accepted = not restart_blockers
    blockers = longitudinal_blockers + anchor_blockers + restart_blockers
    metrics = {
        "journey_id": journey_id,
        "campaign_id": campaign_id,
        "table_session_id": table_session_id,
        "players": len(player_ids),
        "module_runs": len(module_hashes),
        "ended_episodes": len(episodes),
        "longitudinal_accepted": longitudinal_accepted,
        "single_session_anchor_accepted": anchor_accepted,
        "restart_checkpoint_accepted": restart_accepted,
    }
    return LongUiJourneyEvidenceResult(
        accepted=longitudinal_accepted and anchor_accepted and restart_accepted,
        blockers=tuple(blockers),
        metrics=metrics,
    )


def _validate_players(value: Any, blockers: list[str]) -> frozenset[str]:
    if not isinstance(value, list) or len(value) != 4:
        blockers.append("players must contain exactly four records")
        return frozenset()
    member_ids: set[str] = set()
    investigator_ids: set[str] = set()
    for index, raw in enumerate(value):
        label = f"players[{index}]"
        player = _strict_object(raw, _PLAYER_KEYS, label, blockers)
        member_id = _required_string(player.get("member_id"), f"{label}.member_id", blockers)
        investigator_id = _required_string(
            player.get("investigator_id"), f"{label}.investigator_id", blockers
        )
        if member_id:
            member_ids.add(member_id)
        if investigator_id:
            investigator_ids.add(investigator_id)
    if len(member_ids) != 4:
        blockers.append("four distinct player member_id values are required")
    if len(investigator_ids) != 4:
        blockers.append("four distinct investigator_id values are required")
    return frozenset(member_ids)


def _validate_module_runs(value: Any, blockers: list[str]) -> frozenset[str]:
    if not isinstance(value, list) or not value:
        blockers.append("module_runs must be a non-empty list")
        return frozenset()
    run_ids: set[str] = set()
    module_hashes: set[str] = set()
    for index, raw in enumerate(value):
        label = f"module_runs[{index}]"
        run = _strict_object(raw, _MODULE_RUN_KEYS, label, blockers)
        run_id = _required_string(run.get("id"), f"{label}.id", blockers)
        _required_string(run.get("module_id"), f"{label}.module_id", blockers)
        module_hash = _required_sha256(
            run.get("module_sha256"), f"{label}.module_sha256", blockers
        )
        if run.get("status") not in {"active", "paused", "completed"}:
            blockers.append(f"{label}.status must be active, paused, or completed")
        if run.get("automation_mode") != "full_ai":
            blockers.append(f"{label}.automation_mode must be full_ai")
        started_at = _timestamp(run.get("started_at"), f"{label}.started_at", blockers)
        completed_raw = run.get("completed_at")
        completed_at = None
        if completed_raw is not None:
            completed_at = _timestamp(completed_raw, f"{label}.completed_at", blockers)
        if run.get("status") == "completed" and completed_at is None:
            blockers.append(f"{label}.completed_at is required for a completed run")
        if run.get("status") != "completed" and completed_raw is not None:
            blockers.append(f"{label}.completed_at must be null unless status is completed")
        if started_at is not None and completed_at is not None and completed_at <= started_at:
            blockers.append(f"{label}.completed_at must be after started_at")
        if run_id in run_ids:
            blockers.append("module_runs must have distinct id values")
        if run_id:
            run_ids.add(run_id)
        if module_hash:
            module_hashes.add(module_hash)
    return frozenset(module_hashes)


def _validate_episodes(
    value: Any, blockers: list[str]
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or len(value) < MINIMUM_ENDED_EPISODES:
        actual = len(value) if isinstance(value, list) else 0
        blockers.append(
            f"episodes must contain at least {MINIMUM_ENDED_EPISODES} records; got {actual}"
        )
        return ()

    validated: list[dict[str, Any]] = []
    episode_ids: set[str] = set()
    snapshot_ids: set[str] = set()
    checkpoint_fingerprints: set[str] = set()
    previous_sequence: int | None = None
    previous_rowid: int | None = None
    previous_ended_at: datetime | None = None
    for index, raw in enumerate(value):
        label = f"episodes[{index}]"
        episode = _strict_object(raw, _EPISODE_KEYS, label, blockers)
        episode_id = _required_string(episode.get("id"), f"{label}.id", blockers)
        sequence_no = _positive_int(
            episode.get("sequence_no"), f"{label}.sequence_no", blockers
        )
        if previous_sequence is not None and sequence_no != previous_sequence + 1:
            blockers.append("episode sequence_no values must be consecutive")
        if index == 0 and sequence_no != 1:
            blockers.append("episode sequence_no values must start at 1")
        if sequence_no is not None:
            previous_sequence = sequence_no
        if episode.get("status") != "ended":
            blockers.append(f"{label}.status must be ended")
        version = _non_negative_int(episode.get("version"), f"{label}.version", blockers)
        if version == 0:
            blockers.append(f"{label}.version must reflect the authoritative end transition")
        event_start_rowid = _positive_int(
            episode.get("event_start_rowid"), f"{label}.event_start_rowid", blockers
        )
        if (
            previous_rowid is not None
            and event_start_rowid is not None
            and event_start_rowid <= previous_rowid
        ):
            blockers.append("episode event_start_rowid values must be strictly increasing")
        if event_start_rowid is not None:
            previous_rowid = event_start_rowid
        started_at = _timestamp(episode.get("started_at"), f"{label}.started_at", blockers)
        ended_at = _timestamp(episode.get("ended_at"), f"{label}.ended_at", blockers)
        if started_at is not None and ended_at is not None and ended_at <= started_at:
            blockers.append(f"{label}.ended_at must be after started_at")
        if previous_ended_at is not None and started_at is not None and started_at < previous_ended_at:
            blockers.append("consecutive episodes overlap in time")
        if ended_at is not None:
            previous_ended_at = ended_at

        snapshot = _validate_snapshot(episode.get("snapshot"), label, blockers)
        if episode_id in episode_ids:
            blockers.append("episodes must have distinct id values")
        if episode_id:
            episode_ids.add(episode_id)
        snapshot_id = snapshot.get("id", "")
        if snapshot_id in snapshot_ids:
            blockers.append("every ended episode must have a unique snapshot id")
        if snapshot_id:
            snapshot_ids.add(snapshot_id)
        fingerprint = snapshot.get("checkpoint_fingerprint", "")
        if fingerprint in checkpoint_fingerprints:
            blockers.append("every ended episode must have a unique checkpoint fingerprint")
        if fingerprint:
            checkpoint_fingerprints.add(fingerprint)
        validated.append(
            {
                "id": episode_id,
                "sequence_no": sequence_no,
                "started_at": started_at,
                "ended_at": ended_at,
                "snapshot": snapshot,
                "next_episode": episode.get("next_episode"),
            }
        )

    for index, episode in enumerate(validated):
        next_raw = episode["next_episode"]
        label = f"episodes[{index}].next_episode"
        if index == len(validated) - 1:
            if next_raw is not None:
                blockers.append("the final episode next_episode must be null")
            continue
        next_episode = _strict_object(next_raw, _NEXT_EPISODE_KEYS, label, blockers)
        next_id = _required_string(next_episode.get("id"), f"{label}.id", blockers)
        next_sequence = _positive_int(
            next_episode.get("sequence_no"), f"{label}.sequence_no", blockers
        )
        _required_string(
            next_episode.get("client_continue_id"),
            f"{label}.client_continue_id",
            blockers,
        )
        expected = validated[index + 1]
        if next_id != expected["id"] or next_sequence != expected["sequence_no"]:
            blockers.append(f"{label} does not identify the next consecutive episode")

    return tuple(validated)


def _validate_ui_corroboration(
    value: Any,
    episodes: tuple[dict[str, Any], ...],
    blockers: list[str],
) -> None:
    if not isinstance(value, list) or not value:
        blockers.append("ui_corroboration must be a non-empty list")
        return
    seen: set[tuple[str, int]] = set()
    session_end_indexes: set[int] = set()
    continue_indexes: set[int] = set()
    ending_indexes: set[int] = set()
    observed_times: dict[tuple[str, int], datetime] = {}
    previous_time: datetime | None = None
    for index, raw in enumerate(value):
        label = f"ui_corroboration[{index}]"
        event = _strict_object(raw, _UI_EVENT_KEYS, label, blockers)
        kind = event.get("kind")
        if kind not in _UI_EVENT_KINDS:
            blockers.append(f"{label}.kind is not allowed")
        observed_at = _timestamp(event.get("observed_at"), f"{label}.observed_at", blockers)
        if observed_at is not None:
            if previous_time is not None and observed_at <= previous_time:
                blockers.append("ui_corroboration timestamps must be strictly increasing")
            previous_time = observed_at
        session_index = _positive_int(
            event.get("session_index"), f"{label}.session_index", blockers
        )
        if not isinstance(kind, str) or session_index is None:
            continue
        identity = (kind, session_index)
        if identity in seen:
            blockers.append("ui_corroboration must not duplicate a kind/session_index")
        seen.add(identity)
        if observed_at is not None:
            observed_times[identity] = observed_at
        if kind == "session_end_visible":
            session_end_indexes.add(session_index)
        elif kind == "continue_campaign_visible":
            continue_indexes.add(session_index)
        elif kind == "authoritative_ending_visible":
            ending_indexes.add(session_index)

    expected_ended = set(range(1, len(episodes) + 1))
    expected_continued = set(range(1, len(episodes)))
    if session_end_indexes != expected_ended:
        blockers.append("UI must corroborate Session End exactly once for every episode")
    if continue_indexes != expected_continued:
        blockers.append("UI must corroborate Continue exactly once between episodes")
    if not ending_indexes or not ending_indexes.issubset(expected_ended):
        blockers.append("UI must corroborate at least one authoritative ending")
    for index, episode in enumerate(episodes, start=1):
        started_at = episode.get("started_at")
        ended_at = episode.get("ended_at")
        end_observed = observed_times.get(("session_end_visible", index))
        if (
            end_observed is not None
            and isinstance(ended_at, datetime)
            and end_observed < ended_at
        ):
            blockers.append("Session End UI corroboration precedes its authoritative end")
        ending_observed = observed_times.get(("authoritative_ending_visible", index))
        if ending_observed is not None and (
            (isinstance(started_at, datetime) and ending_observed < started_at)
            or (isinstance(ended_at, datetime) and ending_observed > ended_at)
        ):
            blockers.append("authoritative ending UI corroboration lies outside its episode")
        if index < len(episodes):
            continue_observed = observed_times.get(("continue_campaign_visible", index))
            next_started = episodes[index].get("started_at")
            if (
                continue_observed is not None
                and isinstance(next_started, datetime)
                and continue_observed < next_started
            ):
                blockers.append("Continue UI corroboration precedes the next episode")


def _validate_snapshot(
    value: Any, episode_label: str, blockers: list[str]
) -> Mapping[str, Any]:
    label = f"{episode_label}.snapshot"
    snapshot = _strict_object(value, _SNAPSHOT_KEYS, label, blockers)
    _required_string(snapshot.get("id"), f"{label}.id", blockers)
    _required_string(snapshot.get("client_end_id"), f"{label}.client_end_id", blockers)
    for field in (
        "event_window_hash",
        "public_projection_hash",
        "observer_projection_hash",
        "kp_projection_hash",
        "checkpoint_fingerprint",
    ):
        _required_sha256(snapshot.get(field), f"{label}.{field}", blockers)
    _string_list(snapshot.get("event_ids"), f"{label}.event_ids", blockers)
    _timestamp(snapshot.get("generation_cutoff"), f"{label}.generation_cutoff", blockers)
    return snapshot


def _validate_restart_checkpoint(
    value: Any,
    episodes: tuple[dict[str, Any], ...],
    blockers: list[str],
) -> None:
    restart = _strict_object(value, _RESTART_KEYS, "restart_checkpoint", blockers)
    before = _validate_checkpoint_observation(
        restart.get("before"), "restart_checkpoint.before", blockers
    )
    stopped = _strict_object(
        restart.get("stopped"), _STOPPED_KEYS, "restart_checkpoint.stopped", blockers
    )
    started = _strict_object(
        restart.get("started"), _STARTED_KEYS, "restart_checkpoint.started", blockers
    )
    after = _validate_checkpoint_observation(
        restart.get("after"), "restart_checkpoint.after", blockers
    )

    for label, item in (("stopped", stopped), ("started", started)):
        _timestamp(item.get("observed_at"), f"restart_checkpoint.{label}.observed_at", blockers)
        _required_string(
            item.get("process_instance_id"),
            f"restart_checkpoint.{label}.process_instance_id",
            blockers,
        )
        _positive_int(item.get("os_pid"), f"restart_checkpoint.{label}.os_pid", blockers)
        _required_string(
            item.get("persistent_store_id"),
            f"restart_checkpoint.{label}.persistent_store_id",
            blockers,
        )
    if stopped.get("clean_shutdown") is not True:
        blockers.append("restart_checkpoint.stopped.clean_shutdown must be true")
    if started.get("health_status") != 200:
        blockers.append("restart_checkpoint.started.health_status must equal 200")

    times = [
        _parsed_timestamp(item.get("observed_at"))
        for item in (before, stopped, started, after)
    ]
    if all(item is not None for item in times) and not all(
        earlier < later for earlier, later in pairwise(times)  # type: ignore[operator]
    ):
        blockers.append("restart checkpoint observations must be strictly ordered")

    old_instance = before.get("process_instance_id")
    old_pid = before.get("os_pid")
    new_instance = after.get("process_instance_id")
    new_pid = after.get("os_pid")
    if stopped.get("process_instance_id") != old_instance or stopped.get("os_pid") != old_pid:
        blockers.append("stopped process does not corroborate the pre-restart identity")
    if started.get("process_instance_id") != new_instance or started.get("os_pid") != new_pid:
        blockers.append("started process does not corroborate the post-restart identity")
    if old_instance == new_instance or old_pid == new_pid:
        blockers.append("restart requires distinct old/new process instances and OS pids")

    stores = {
        before.get("persistent_store_id"),
        stopped.get("persistent_store_id"),
        started.get("persistent_store_id"),
        after.get("persistent_store_id"),
    }
    if len(stores) != 1:
        blockers.append("restart did not preserve one persistent store identity")
    if before.get("checkpoint_snapshot_id") != after.get("checkpoint_snapshot_id"):
        blockers.append("checkpoint snapshot identity changed across restart")
    if before.get("checkpoint_fingerprint") != after.get("checkpoint_fingerprint"):
        blockers.append("checkpoint fingerprint changed across restart")

    # A restart checkpoint is meaningful only when it precedes an authoritative
    # Continue into the next episode.  The final ended episode has no successor.
    known_checkpoints = {
        (
            episode["snapshot"].get("id"),
            episode["snapshot"].get("checkpoint_fingerprint"),
        ): episodes[index + 1]
        for index, episode in enumerate(episodes[:-1])
    }
    observed_checkpoint = (
        before.get("checkpoint_snapshot_id"),
        before.get("checkpoint_fingerprint"),
    )
    continued_episode = known_checkpoints.get(observed_checkpoint)
    if continued_episode is None:
        blockers.append(
            "restart checkpoint is not an ended episode snapshot with a consecutive Continue"
        )
    else:
        after_time = _parsed_timestamp(after.get("observed_at"))
        started_time = _parsed_timestamp(started.get("observed_at"))
        continued_at = continued_episode.get("started_at")
        if isinstance(continued_at, datetime):
            if started_time is not None and started_time > continued_at:
                blockers.append(
                    "authoritative Continue precedes the restarted backend readiness"
                )
            if after_time is not None and after_time < continued_at:
                blockers.append(
                    "post-restart observation precedes the authoritative continued episode"
                )


def _validate_checkpoint_observation(
    value: Any, label: str, blockers: list[str]
) -> Mapping[str, Any]:
    item = _strict_object(value, _CHECKPOINT_OBSERVATION_KEYS, label, blockers)
    _timestamp(item.get("observed_at"), f"{label}.observed_at", blockers)
    _required_string(item.get("process_instance_id"), f"{label}.process_instance_id", blockers)
    _positive_int(item.get("os_pid"), f"{label}.os_pid", blockers)
    _required_string(item.get("persistent_store_id"), f"{label}.persistent_store_id", blockers)
    _required_string(
        item.get("checkpoint_snapshot_id"), f"{label}.checkpoint_snapshot_id", blockers
    )
    _required_sha256(
        item.get("checkpoint_fingerprint"), f"{label}.checkpoint_fingerprint", blockers
    )
    return item


def _strict_object(
    value: Any, expected: frozenset[str], label: str, blockers: list[str]
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        blockers.append(f"{label} must be an object")
        return {}
    _require_exact_keys(value, expected, label, blockers)
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


def _required_sha256(value: Any, label: str, blockers: list[str]) -> str:
    result = _required_string(value, label, blockers)
    if result and not _SHA256.fullmatch(result):
        blockers.append(f"{label} must be a lowercase SHA-256 digest")
    return result


def _positive_int(value: Any, label: str, blockers: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        blockers.append(f"{label} must be a positive integer")
        return None
    return value


def _non_negative_int(value: Any, label: str, blockers: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        blockers.append(f"{label} must be a non-negative integer")
        return None
    return value


def _string_list(value: Any, label: str, blockers: list[str]) -> None:
    if not isinstance(value, list) or not value:
        blockers.append(f"{label} must be a non-empty string list")
        return
    if any(not isinstance(item, str) or not item.strip() for item in value):
        blockers.append(f"{label} must contain only non-empty strings")
    elif len(value) != len(set(value)):
        blockers.append(f"{label} must not contain duplicates")


def _timestamp(value: Any, label: str, blockers: list[str]) -> datetime | None:
    parsed = _parsed_timestamp(value)
    if parsed is None:
        blockers.append(f"{label} must be an ISO-8601 timestamp with a timezone")
    return parsed


def _parsed_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _find_forbidden_content(value: Any, path: str, blockers: list[str]) -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            if normalized.endswith("_passed"):
                blockers.append(f"producer pass field is forbidden at {path}.{key}")
            if normalized in _SECRET_KEY_PARTS or normalized.endswith(("_secret", "_token")):
                blockers.append(f"secret-bearing field is forbidden at {path}.{key}")
            _find_forbidden_content(nested, f"{path}.{key}", blockers)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _find_forbidden_content(nested, f"{path}[{index}]", blockers)
    elif isinstance(value, str) and _SECRET_VALUE.search(value):
        blockers.append(f"secret-like value is forbidden at {path}")


__all__ = [
    "MINIMUM_ENDED_EPISODES",
    "SCHEMA_VERSION",
    "LongUiJourneyEvidenceResult",
    "verify_long_ui_journey_evidence",
]

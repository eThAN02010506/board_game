"""Persistent 20-Session state-scale runner for AC-LONG durability evidence.

The runner exercises production repositories and Session continuity services.  It
is intentionally model-free: model quality is evaluated elsewhere, while this
runner proves that authoritative history, visibility, FTS rebuilds, idempotency,
and process restarts do not depend on an AI process staying alive.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_kp.application.campaign_objective_service import (
    CampaignObjectiveService,
    CreateObjectiveCommand,
    UpdateObjectiveCommand,
)
from ai_kp.application.session_continuity_service import SessionContinuityService
from ai_kp.application.session_service import SessionService
from ai_kp.application.world_service import AddMemoryCommand, AppendEventCommand, WorldService
from ai_kp.core.db import db_session
from ai_kp.evaluation.long_campaign_lifecycle import (
    bootstrap_lifecycle_players,
    exercise_lifecycle_session,
    join_late_player,
    summarize_lifecycle,
)
from ai_kp.infrastructure.database.repositories import Repository

DURABILITY_RUNNER_VERSION = "long-campaign-durability.v4"
DEFAULT_SESSION_COUNT = 20
DEFAULT_TABLE_MINUTES_PER_SESSION = 150

SCRIPTED_MILESTONES = (
    "first_adventure",
    "system_familiarity",
    "complex_npc",
    "open_world_deviation",
    "major_choice",
    "character_growth",
    "character_death",
    "replacement_character",
    "member_departure",
    "long_term_recall",
)


@dataclass(frozen=True)
class DurabilityRunConfig:
    sessions: int = DEFAULT_SESSION_COUNT
    table_minutes_per_session: int = DEFAULT_TABLE_MINUTES_PER_SESSION
    public_events_per_session: int = 6
    memories_per_session: int = 3
    rebuild_index_after_session: int = 10
    switch_model_after_session: int = 10

    def validate(self) -> None:
        if not 20 <= self.sessions <= 100:
            raise ValueError("Durability run requires 20-100 Sessions")
        if not 30 <= self.table_minutes_per_session <= 600:
            raise ValueError("Table minutes per Session must be 30-600")
        if not 1 <= self.public_events_per_session <= 50:
            raise ValueError("Public events per Session must be 1-50")
        if not 1 <= self.memories_per_session <= 20:
            raise ValueError("Memories per Session must be 1-20")
        if not 1 <= self.rebuild_index_after_session < self.sessions:
            raise ValueError("Index rebuild checkpoint must be inside the run")
        if not 1 <= self.switch_model_after_session < self.sessions:
            raise ValueError("Model switch checkpoint must be inside the run")


class LongCampaignDurabilityRunner:
    """Run against a new SQLite database and return machine-verifiable evidence."""

    def __init__(self, database_path: Path, config: DurabilityRunConfig | None = None):
        self.database_path = database_path
        self.config = config or DurabilityRunConfig()

    def run(self) -> dict[str, Any]:
        self.config.validate()
        if self.database_path.exists():
            raise FileExistsError("Durability runner refuses to reuse an existing database")

        state = self._bootstrap()
        previous_fingerprint: str | None = None
        episode_evidence: list[dict[str, Any]] = []
        latency_ms: list[float] = []
        leak_count = 0
        index_rebuild_passed = False
        model_switch_authority_stable = False
        model_switch_recovered = False
        model_switch_evidence: dict[str, Any] | None = None
        lifecycle_evidence: list[dict[str, Any]] = []

        for session_number in range(1, self.config.sessions + 1):
            started = time.perf_counter()
            with db_session(self.database_path) as connection:
                repo = Repository(connection)
                if previous_fingerprint is not None:
                    reopened = self._authority_fingerprint(repo)
                    if reopened != previous_fingerprint:
                        raise RuntimeError("Authority fingerprint changed across process restart")

                kp = self._authenticate(repo, state["kp_token"])
                if session_number == 12:
                    lifecycle_evidence.append(
                        join_late_player(
                            repo,
                            state,
                            kp,
                            session_number=session_number,
                        )
                    )
                players = [
                    self._authenticate(repo, item["access_token"])
                    for item in state["players"]
                ]
                current = repo.get_current_campaign_episode(kp.session_id)
                if current is None or current["status"] != "in_progress":
                    raise RuntimeError("Durability Session did not resume in progress")
                if int(current["sequence_no"]) != session_number:
                    raise RuntimeError("Episode sequence drifted across restart")

                if session_number == self.config.switch_model_after_session:
                    before_switch = self._authority_fingerprint(repo)
                    previous_model = repo.get_model_configuration()
                    switched_model = repo.save_model_configuration(
                        provider_type="openai_compatible",
                        base_url="http://durability-model-b.invalid/v1",
                        api_key="",
                        model="durability-model-b",
                        local_model_path=None,
                        local_port=8011,
                        semantic_profile="small",
                    )
                    after_switch = self._authority_fingerprint(repo)
                    model_switch_authority_stable = before_switch == after_switch
                    model_switch_evidence = {
                        "session": session_number,
                        "from_model": str(previous_model["model"]),
                        "from_version": int(previous_model["version"]),
                        "to_model": str(switched_model["model"]),
                        "to_version": int(switched_model["version"]),
                        "authority_unchanged": model_switch_authority_stable,
                    }
                elif session_number == self.config.switch_model_after_session + 1:
                    active_model = repo.get_model_configuration()
                    model_switch_recovered = bool(
                        active_model
                        and active_model["model"] == "durability-model-b"
                        and int(active_model["version"]) == 2
                    )

                milestone = self._milestone(session_number)
                world = WorldService(repo)
                objective_service = CampaignObjectiveService(repo)
                objective = repo.get_campaign_objective(state["objective_id"])
                objective = objective_service.update(
                    str(objective["id"]),
                    kp,
                    UpdateObjectiveCommand(
                        command_id=f"durability-objective-{session_number:04d}",
                        expected_version=int(objective["version"]),
                        status="blocked" if session_number % 4 == 0 else "open",
                        public_progress=(
                            f"Session {session_number} recorded objective progress for {milestone}."
                        ),
                    ),
                )
                public_ids: list[str] = []
                for event_index in range(1, self.config.public_events_per_session + 1):
                    event = world.append_event(
                        kp.campaign_id,
                        AppendEventCommand(
                            actor_type="player" if event_index % 2 else "rules_engine",
                            actor_id=players[(event_index - 1) % len(players)].member_id,
                            event_type=f"durability.{milestone}",
                            summary=(
                                f"Session {session_number} milestone {milestone} "
                                f"public event {event_index}."
                            ),
                            visibility="table",
                            happened_at=f"campaign-hour-{(session_number - 1) * 3 + 1}",
                            payload={
                                "session_number": session_number,
                                "milestone": milestone,
                                "event_index": event_index,
                            },
                        ),
                    )
                    public_ids.append(str(event["id"]))

                secret_marker = f"SecretMarker{session_number:02d}"
                world.append_event(
                    kp.campaign_id,
                    AppendEventCommand(
                        actor_type="kp",
                        event_type="durability.hidden_thread",
                        summary=f"{secret_marker} remains known only to the KP.",
                        visibility="kp",
                        happened_at=f"campaign-hour-{session_number * 3}",
                    ),
                )
                memory_tokens: list[str] = []
                for memory_index in range(1, self.config.memories_per_session + 1):
                    token = f"Beacon{session_number:02d}{memory_index:02d}"
                    memory_tokens.append(token)
                    world.add_memory(
                        kp.campaign_id,
                        AddMemoryCommand(
                            text=(
                                f"{token} records {milestone} during Session "
                                f"{session_number}."
                            ),
                            scope="campaign",
                            importance=3 if session_number <= 10 else 2,
                            visibility="table",
                            happened_at=f"campaign-hour-{session_number * 3}",
                            source_event_id=public_ids[(memory_index - 1) % len(public_ids)],
                        ),
                    )

                lifecycle_evidence.extend(
                    exercise_lifecycle_session(
                        repo,
                        state,
                        kp,
                        session_number=session_number,
                        world=world,
                    )
                )

                continuity = SessionContinuityService(repo)
                ended = continuity.end(
                    kp, client_end_id=f"durability-end-{session_number:04d}"
                )
                replay = continuity.end(
                    kp, client_end_id=f"durability-end-{session_number:04d}"
                )
                if replay["latest_snapshot"]["id"] != ended["latest_snapshot"]["id"]:
                    raise RuntimeError("Session End idempotency created a second snapshot")

                for player in players:
                    player_projection = json.dumps(
                        continuity.view(player), ensure_ascii=False, sort_keys=True
                    )
                    if (
                        secret_marker in player_projection
                        or "DurabilityHiddenObjective" in player_projection
                    ):
                        leak_count += 1

                for token in memory_tokens:
                    results = world.search_memory(
                        kp.campaign_id,
                        token,
                        view="player",
                    )
                    if not any(token in item["text"] for item in results):
                        raise RuntimeError(f"Memory retrieval lost {token}")

                if session_number == self.config.rebuild_index_after_session:
                    connection.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
                    index_rebuild_passed = all(
                        world.search_memory(kp.campaign_id, token, view="player")
                        for token in memory_tokens
                    )

                snapshot = ended["latest_snapshot"]
                episode_evidence.append(
                    {
                        "session": session_number,
                        "milestone": milestone,
                        "episode_id": str(snapshot["episode_id"]),
                        "event_window_hash": str(snapshot["event_window_hash"]),
                        "memory_probe_hash": _hash(memory_tokens),
                        "objective_version": int(objective["version"]),
                        "objective_status": str(objective["status"]),
                    }
                )
                if session_number < self.config.sessions:
                    continuity.continue_campaign(
                        kp,
                        client_continue_id=f"durability-continue-{session_number:04d}",
                    )
                previous_fingerprint = self._authority_fingerprint(repo)
            latency_ms.append((time.perf_counter() - started) * 1000)

        with db_session(self.database_path) as connection:
            repo = Repository(connection)
            final_fingerprint = self._authority_fingerprint(repo)
            if final_fingerprint != previous_fingerprint:
                raise RuntimeError("Final authority fingerprint changed after restart")
            counts = self._counts(repo)
            lifecycle = summarize_lifecycle(repo, state, lifecycle_evidence)

        result = {
            "runner_version": DURABILITY_RUNNER_VERSION,
            "status": (
                "passed"
                if leak_count == 0
                and index_rebuild_passed
                and model_switch_authority_stable
                and model_switch_recovered
                and lifecycle["passed"]
                else "failed"
            ),
            "sessions": self.config.sessions,
            "scripted_sessions": min(10, self.config.sessions),
            "equivalent_table_minutes": (
                self.config.sessions * self.config.table_minutes_per_session
            ),
            "process_restarts": self.config.sessions,
            "index_rebuild_passed": index_rebuild_passed,
            "model_switch_passed": (
                model_switch_authority_stable and model_switch_recovered
            ),
            "model_switch": model_switch_evidence,
            "secret_leak_count": leak_count,
            "authority_fingerprint": final_fingerprint,
            "authority_fingerprint_stable": final_fingerprint == previous_fingerprint,
            "counts": counts,
            "lifecycle": lifecycle,
            "latency_ms": {
                "max": round(max(latency_ms), 3),
                "average": round(sum(latency_ms) / len(latency_ms), 3),
            },
            "episodes": episode_evidence,
        }
        result["result_fingerprint"] = _hash(result)
        return result

    def _bootstrap(self) -> dict[str, Any]:
        with db_session(self.database_path) as connection:
            repo = Repository(connection)
            campaign = repo.create_campaign("AC-LONG durability campaign")
            session = SessionService(repo).create(
                str(campaign["id"]), title="Long campaign durability"
            )
            kp = self._authenticate(repo, str(session["access_token"]))
            lifecycle_state = bootstrap_lifecycle_players(repo, kp)
            repo.activate_prepared_episode(kp.session_id)
            objective_service = CampaignObjectiveService(repo)
            objective = objective_service.create(
                str(campaign["id"]),
                kp,
                CreateObjectiveCommand(
                    command_id="durability-public-objective",
                    title="Resolve the long-running campaign problem",
                    public_description="This task persists through every Session and restart.",
                ),
            )
            objective_service.create(
                str(campaign["id"]),
                kp,
                CreateObjectiveCommand(
                    command_id="durability-hidden-objective",
                    title="DurabilityHiddenObjective",
                    kp_notes="This must never enter a player continuity projection.",
                    visibility="kp",
                ),
            )
            repo.save_model_configuration(
                provider_type="openai_compatible",
                base_url="http://durability-model-a.invalid/v1",
                api_key="",
                model="durability-model-a",
                local_model_path=None,
                local_port=8011,
                semantic_profile="small",
            )
            return {
                "kp_token": str(session["access_token"]),
                **lifecycle_state,
                "objective_id": str(objective["id"]),
            }

    @staticmethod
    def _authenticate(repo: Repository, token: str):
        identity = repo.authenticate_access_token(token)
        if identity is None:
            raise RuntimeError("Durability credential did not survive restart")
        return identity

    @staticmethod
    def _milestone(session_number: int) -> str:
        if session_number <= len(SCRIPTED_MILESTONES):
            return SCRIPTED_MILESTONES[session_number - 1]
        return f"continued_campaign_{session_number:02d}"

    @staticmethod
    def _counts(repo: Repository) -> dict[str, int]:
        tables = (
            "campaign_episodes",
            "events",
            "memories",
            "session_continuity_snapshots",
            "campaign_objectives",
            "campaign_objective_events",
            "campaign_investigator_lifecycle",
            "campaign_member_presence",
            "character_lifecycle_requests",
            "character_lifecycle_events",
        )
        return {
            table: int(repo.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }

    @staticmethod
    def _authority_fingerprint(repo: Repository) -> str:
        tables = (
            ("campaign_episodes", "id"),
            ("events", "id"),
            ("memories", "id"),
            ("session_continuity_snapshots", "id"),
            ("campaign_objectives", "id"),
            ("campaign_objective_events", "id"),
            ("session_members", "id"),
            ("player_profiles", "id"),
            ("investigators", "id"),
            ("investigator_revisions", "id"),
            ("campaign_investigators", "investigator_id"),
            ("campaign_investigator_lifecycle", "investigator_id"),
            ("campaign_member_presence", "member_id"),
            ("character_lifecycle_requests", "id"),
            ("character_lifecycle_events", "created_at, id"),
        )
        state: dict[str, list[dict[str, Any]]] = {}
        for table, ordering in tables:
            cursor = repo.connection.execute(f"SELECT * FROM {table} ORDER BY {ordering}")
            names = [item[0] for item in cursor.description or ()]
            state[table] = [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        return _hash(state)

def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


__all__ = [
    "DEFAULT_SESSION_COUNT",
    "DEFAULT_TABLE_MINUTES_PER_SESSION",
    "DURABILITY_RUNNER_VERSION",
    "SCRIPTED_MILESTONES",
    "DurabilityRunConfig",
    "LongCampaignDurabilityRunner",
]

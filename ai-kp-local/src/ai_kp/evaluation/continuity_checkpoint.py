"""Reconstruct one immutable Session-End checkpoint from authoritative SQLite rows."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContinuityCheckpointEvidence:
    campaign_id: str
    session_id: str
    episode_id: str
    sequence_no: int
    snapshot_id: str
    client_end_id: str
    event_start_rowid: int
    event_window_hash: str
    event_ids: tuple[str, ...]
    generation_cutoff: str
    public_projection_hash: str
    observer_projection_hash: str
    kp_projection_hash: str
    checkpoint_fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "session_id": self.session_id,
            "episode_id": self.episode_id,
            "sequence_no": self.sequence_no,
            "snapshot_id": self.snapshot_id,
            "client_end_id": self.client_end_id,
            "event_start_rowid": self.event_start_rowid,
            "event_window_hash": self.event_window_hash,
            "event_ids": list(self.event_ids),
            "generation_cutoff": self.generation_cutoff,
            "public_projection_hash": self.public_projection_hash,
            "observer_projection_hash": self.observer_projection_hash,
            "kp_projection_hash": self.kp_projection_hash,
            "checkpoint_fingerprint": self.checkpoint_fingerprint,
        }


def reconstruct_continuity_checkpoint(
    connection: sqlite3.Connection, episode_id: str
) -> ContinuityCheckpointEvidence:
    """Fail closed unless the ended episode has one internally consistent snapshot."""

    row = connection.execute(
        """
        SELECT episode.id AS episode_id, episode.campaign_id, episode.session_id,
               episode.sequence_no, episode.status, episode.event_start_rowid,
               episode.started_at, episode.ended_at, episode.version,
               snapshot.id AS snapshot_id, snapshot.client_end_id,
               snapshot.event_window_hash, snapshot.event_ids_json,
               snapshot.generation_cutoff, snapshot.public_projection_json,
               snapshot.observer_projection_json, snapshot.kp_projection_json,
               snapshot.created_at AS snapshot_created_at
        FROM campaign_episodes episode
        JOIN session_continuity_snapshots snapshot
          ON snapshot.episode_id = episode.id
         AND snapshot.campaign_id = episode.campaign_id
         AND snapshot.session_id = episode.session_id
        WHERE episode.id = ?
        """,
        (episode_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Ended episode has no authority-matched continuity snapshot")
    item = dict(row)
    if item["status"] != "ended" or not item["ended_at"]:
        raise ValueError("Continuity checkpoint episode is not ended")
    if item["ended_at"] != item["generation_cutoff"]:
        raise ValueError("Continuity snapshot cutoff differs from the episode ending")

    raw_event_ids = _json_value(item["event_ids_json"], "event_ids_json")
    if not isinstance(raw_event_ids, list) or any(
        not isinstance(value, str) or not value for value in raw_event_ids
    ):
        raise ValueError("Continuity event_ids_json must be an array of identifiers")
    event_ids = tuple(raw_event_ids)
    if len(set(event_ids)) != len(event_ids):
        raise ValueError("Continuity event_ids_json contains duplicate identifiers")
    events = _load_events(
        connection,
        campaign_id=str(item["campaign_id"]),
        event_start_rowid=int(item["event_start_rowid"]),
        event_ids=event_ids,
    )
    if tuple(str(event["id"]) for event in events) != event_ids:
        raise ValueError("Continuity event IDs do not match the authoritative event window")
    event_hash = _canonical_hash(events)
    if event_hash != item["event_window_hash"]:
        raise ValueError("Continuity event window hash does not match authoritative events")

    projection_hashes = {
        name: _canonical_hash(_json_value(item[f"{name}_projection_json"], name))
        for name in ("public", "observer", "kp")
    }
    checkpoint_payload = {
        "campaign_id": str(item["campaign_id"]),
        "session_id": str(item["session_id"]),
        "episode": {
            "id": str(item["episode_id"]),
            "sequence_no": int(item["sequence_no"]),
            "status": str(item["status"]),
            "event_start_rowid": int(item["event_start_rowid"]),
            "started_at": str(item["started_at"]),
            "ended_at": str(item["ended_at"]),
            "version": int(item["version"]),
        },
        "snapshot": {
            "id": str(item["snapshot_id"]),
            "client_end_id": str(item["client_end_id"]),
            "event_window_hash": event_hash,
            "event_ids": list(event_ids),
            "generation_cutoff": str(item["generation_cutoff"]),
            "public_projection_hash": projection_hashes["public"],
            "observer_projection_hash": projection_hashes["observer"],
            "kp_projection_hash": projection_hashes["kp"],
            "created_at": str(item["snapshot_created_at"]),
        },
    }
    return ContinuityCheckpointEvidence(
        campaign_id=str(item["campaign_id"]),
        session_id=str(item["session_id"]),
        episode_id=str(item["episode_id"]),
        sequence_no=int(item["sequence_no"]),
        snapshot_id=str(item["snapshot_id"]),
        client_end_id=str(item["client_end_id"]),
        event_start_rowid=int(item["event_start_rowid"]),
        event_window_hash=event_hash,
        event_ids=event_ids,
        generation_cutoff=str(item["generation_cutoff"]),
        public_projection_hash=projection_hashes["public"],
        observer_projection_hash=projection_hashes["observer"],
        kp_projection_hash=projection_hashes["kp"],
        checkpoint_fingerprint=_canonical_hash(checkpoint_payload),
    )


def _load_events(
    connection: sqlite3.Connection,
    *,
    campaign_id: str,
    event_start_rowid: int,
    event_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    if not event_ids:
        return []
    placeholders = ",".join("?" for _ in event_ids)
    rows = connection.execute(
        f"""
        SELECT id, actor_type, actor_id, visibility, event_type,
               happened_at, summary, created_at
        FROM events
        WHERE campaign_id = ? AND rowid >= ? AND id IN ({placeholders})
        ORDER BY created_at, id
        """,
        (campaign_id, event_start_rowid, *event_ids),
    ).fetchall()
    return [dict(row) for row in rows]


def _json_value(value: Any, label: str) -> Any:
    if not isinstance(value, str):
        raise TypeError(f"Continuity {label} is not JSON text")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Continuity {label} is invalid JSON") from exc


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = ["ContinuityCheckpointEvidence", "reconstruct_continuity_checkpoint"]

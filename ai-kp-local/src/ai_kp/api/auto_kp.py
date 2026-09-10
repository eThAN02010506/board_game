"""Player-safe API projections for Auto KP state."""


def player_auto_kp_job(job: dict) -> dict:
    """Keep model errors, prompts and KP-only results outside player responses."""

    projection = {
        key: job.get(key)
        for key in (
            "id",
            "campaign_id",
            "run_id",
            "job_type",
            "status",
            "attempt_count",
            "max_attempts",
            "created_at",
            "updated_at",
        )
    }
    if job.get("job_type") == "parallel_actions":
        # A prepare job may use another participant's action as its internal
        # queue resource; a commit job uses the KP-only batch identifier.
        # Neither identifier is needed to render player progress.
        payload = job.get("payload")
        internal_phase = str(
            payload.get("phase") if isinstance(payload, dict) else "prepare"
        )
        phase = {
            "prepare": "prepare",
            "commit": "settlement",
            "resolve_blind_checks": "progress",
        }.get(internal_phase, "progress")
        projection["phase"] = phase
        projection["stage"] = {
            "prepare": "parallel_preparation",
            "settlement": "parallel_settlement",
            "progress": "parallel_progress",
        }[phase]
    else:
        projection["stage"] = job.get("stage")
        projection["resource_id"] = job.get("resource_id")
    return projection


__all__ = ["player_auto_kp_job"]

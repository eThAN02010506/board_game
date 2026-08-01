"""Player-safe API projections for Auto KP state."""


def player_auto_kp_job(job: dict) -> dict:
    """Keep model errors, prompts and KP-only results outside player responses."""

    return {
        key: job.get(key)
        for key in (
            "id",
            "campaign_id",
            "run_id",
            "job_type",
            "resource_id",
            "status",
            "stage",
            "attempt_count",
            "max_attempts",
            "created_at",
            "updated_at",
        )
    }


__all__ = ["player_auto_kp_job"]

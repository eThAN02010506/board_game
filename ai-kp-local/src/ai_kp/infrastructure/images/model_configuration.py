"""Persisted image-provider settings projection and public redaction."""

from __future__ import annotations

from typing import Any

from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.llm.model_configuration import normalize_openai_base_url


def apply_image_model_configuration(
    settings: Settings,
    configuration: dict | None,
) -> Settings:
    if not configuration:
        return settings
    return settings.model_copy(
        update={
            "image_base_url": normalize_openai_base_url(
                str(configuration["base_url"])
            ),
            "image_api_key": str(configuration.get("api_key") or ""),
            "image_model": str(configuration["model"]),
            "image_timeout_seconds": float(configuration["timeout_seconds"]),
        }
    )


def public_image_model_configuration(
    settings: Settings,
    configuration: dict | None,
) -> dict[str, Any]:
    if configuration is None:
        return {
            "base_url": settings.image_base_url,
            "model": settings.image_model or "",
            "timeout_seconds": settings.image_timeout_seconds,
            "api_key_configured": bool(
                settings.image_base_url
                and settings.image_model
                and settings.image_api_key
            ),
            "persisted": False,
        }
    return {
        "base_url": configuration["base_url"],
        "model": configuration["model"],
        "timeout_seconds": configuration["timeout_seconds"],
        "api_key_configured": bool(configuration.get("api_key")),
        "persisted": True,
        "updated_at": configuration["updated_at"],
    }

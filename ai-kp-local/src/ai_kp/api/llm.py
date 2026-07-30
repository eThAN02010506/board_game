"""Construction seam for the configured OpenAI-compatible local model client."""

from fastapi import Request

from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.llm.openai_compatible import OpenAICompatibleClient


def create_llm_client(
    settings: Settings,
    request: Request,
) -> OpenAICompatibleClient:
    # Preserve the public compatibility monkeypatch used by existing integrations.
    from ai_kp.api import main as compatibility_main

    client_type = getattr(
        compatibility_main,
        "OpenAICompatibleClient",
        OpenAICompatibleClient,
    )
    return client_type(
        settings.llm_base_url,
        settings.llm_api_key,
        settings.llm_model,
        client=getattr(request.app.state, "http_client", None),
    )


__all__ = ["create_llm_client"]

"""Construction seam for the configured OpenAI-compatible local model client."""

from fastapi import Request

from ai_kp.bootstrap.settings import Settings
from ai_kp.director.orchestrator import KpOrchestrator
from ai_kp.infrastructure.database.repositories import Repository
from ai_kp.infrastructure.llm.call_registry import CampaignAiCallRegistry
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


def create_kp_orchestrator(
    repo: Repository,
    settings: Settings,
    request: Request,
) -> KpOrchestrator:
    registry = getattr(request.app.state, "campaign_ai_calls", None)
    if registry is not None and not isinstance(registry, CampaignAiCallRegistry):
        raise RuntimeError("Invalid campaign AI call registry")
    return KpOrchestrator(
        repo.connection,
        create_llm_client(settings, request),
        call_registry=registry,
    )


__all__ = ["create_kp_orchestrator", "create_llm_client"]

"""Backward-compatible ASGI entry point.

Application composition now lives in :mod:`ai_kp.bootstrap.composition`; existing uvicorn and
test imports can continue to use ``ai_kp.api.main``.
"""

from ai_kp.bootstrap.composition import app, create_app
from ai_kp.infrastructure.llm.openai_compatible import (
    OpenAICompatibleClient,
)

__all__ = ["OpenAICompatibleClient", "app", "create_app"]

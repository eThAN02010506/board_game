"""Backward-compatible ASGI entry point.

Application composition now lives in :mod:`ai_kp.api.app`; existing uvicorn and
test imports can continue to use ``ai_kp.api.main``.
"""

from ai_kp.api.app import app, create_app
from ai_kp.llm.openai_compatible import OpenAICompatibleClient as OpenAICompatibleClient


__all__ = ["OpenAICompatibleClient", "app", "create_app"]

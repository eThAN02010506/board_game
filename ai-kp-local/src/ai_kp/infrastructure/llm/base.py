"""Compatibility export for the model port now owned by the platform layer."""

from ai_kp.platform.ports.llm import ChatMessage, LlmClient

__all__ = ["ChatMessage", "LlmClient"]

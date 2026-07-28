"""Map image provider and content-addressed asset adapters."""

from ai_kp.infrastructure.images.openai_compatible import OpenAICompatibleImageProvider
from ai_kp.infrastructure.images.storage import MapAssetFileStore

__all__ = ["MapAssetFileStore", "OpenAICompatibleImageProvider"]

"""Versioned model settings captured at one AI execution boundary.

Background workers outlive FastAPI request state.  They must therefore read the
persisted model configuration for every claimed unit of work instead of keeping
the provider that happened to be configured when the process started.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.llm.model_configuration import apply_model_configuration


class ModelConfigurationStore(Protocol):
    def get_model_configuration(self) -> dict | None: ...


class ModelExecutionSuperseded(RuntimeError):
    """The configured model changed before an AI result could be committed."""


@dataclass(frozen=True)
class ModelExecutionSnapshot:
    settings: Settings
    configuration_version: int

    @classmethod
    def capture(
        cls,
        store: ModelConfigurationStore,
        fallback_settings: Settings,
    ) -> ModelExecutionSnapshot:
        configuration = store.get_model_configuration()
        return cls(
            settings=apply_model_configuration(fallback_settings, configuration),
            configuration_version=_configuration_version(configuration),
        )

    def revalidate(self, store: ModelConfigurationStore) -> None:
        current = _configuration_version(store.get_model_configuration())
        if current != self.configuration_version:
            raise ModelExecutionSuperseded(
                "Model configuration changed while the Agent was running"
            )


def _configuration_version(configuration: dict | None) -> int:
    return int(configuration["version"]) if configuration is not None else 0


__all__ = [
    "ModelConfigurationStore",
    "ModelExecutionSnapshot",
    "ModelExecutionSuperseded",
]

"""Role-scoped model adapters over the authoritative tabletop runtime."""

from ai_kp.platform.agents.entity_actor import (
    ActorExecutionErrorCode,
    ActorExecutionTrace,
    EntityActorResponse,
    SingleEntityActorAdapter,
    actor_execution_trace,
)
from ai_kp.platform.agents.entity_context import (
    EntityActingFrame,
    EntityAgentContextBuilder,
)
from ai_kp.platform.agents.verifier import (
    AgentVerificationReport,
    ConstrainedOutputVerifier,
)

__all__ = [
    "ActorExecutionErrorCode",
    "ActorExecutionTrace",
    "AgentVerificationReport",
    "ConstrainedOutputVerifier",
    "EntityActingFrame",
    "EntityActorResponse",
    "EntityAgentContextBuilder",
    "SingleEntityActorAdapter",
    "actor_execution_trace",
]

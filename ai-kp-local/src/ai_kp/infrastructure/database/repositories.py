"""Canonical transaction-scoped facade composing feature SQLite repositories."""

import sqlite3

from ai_kp.infrastructure.database.checks import SkillCheckRepository
from ai_kp.infrastructure.database.context_assemblies import ContextAssemblyRepository
from ai_kp.infrastructure.database.facts import FactRepository
from ai_kp.infrastructure.database.investigators import InvestigatorRepository
from ai_kp.infrastructure.database.maps import MapRepository
from ai_kp.infrastructure.database.memory_timeline import MemoryTimelineRepository
from ai_kp.infrastructure.database.model_configuration import ModelConfigurationRepository
from ai_kp.infrastructure.database.module_graph import ModuleGraphRepository
from ai_kp.infrastructure.database.module_imports import ModuleImportRepository
from ai_kp.infrastructure.database.module_knowledge import ModuleKnowledgeRepository
from ai_kp.infrastructure.database.module_runs import ModuleRunRepository
from ai_kp.infrastructure.database.npc_reappearances import NpcReappearanceRepository
from ai_kp.infrastructure.database.private_random_resolutions import (
    PrivateRandomResolutionRepository,
)
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.rulebooks import RulebookRepository
from ai_kp.infrastructure.database.security import SecurityRepository
from ai_kp.infrastructure.database.session_recaps import SessionRecapRepository
from ai_kp.infrastructure.database.session_seats import SessionSeatRepository
from ai_kp.infrastructure.database.travel_graph import TravelGraphRepository
from ai_kp.infrastructure.database.turns import TurnRepository
from ai_kp.infrastructure.database.world import WorldRepository
from ai_kp.infrastructure.database.world_expansion_materializations import (
    WorldExpansionMaterializationRepository,
)
from ai_kp.infrastructure.realtime.outbox import RealtimeRepository

__all__ = ["Repository", "decode_json_field", "row_to_dict"]


class Repository(
    WorldRepository,
    FactRepository,
    TurnRepository,
    MapRepository,
    ContextAssemblyRepository,
    SecurityRepository,
    RealtimeRepository,
    InvestigatorRepository,
    SessionSeatRepository,
    SkillCheckRepository,
    RulebookRepository,
    ModuleImportRepository,
    ModuleKnowledgeRepository,
    ModuleGraphRepository,
    ModuleRunRepository,
    ModelConfigurationRepository,
    WorldExpansionMaterializationRepository,
    NpcReappearanceRepository,
    TravelGraphRepository,
    PrivateRandomResolutionRepository,
    MemoryTimelineRepository,
    SessionRecapRepository,
):
    """Backward-compatible facade over the feature-specific SQLite repositories."""

    def __init__(self, connection: sqlite3.Connection):
        super().__init__(connection)

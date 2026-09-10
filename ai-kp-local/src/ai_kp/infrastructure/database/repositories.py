"""Canonical transaction-scoped facade composing feature SQLite repositories."""

import sqlite3

from ai_kp.infrastructure.database.action_adjudications import ActionAdjudicationRepository
from ai_kp.infrastructure.database.action_resolution_previews import (
    ActionResolutionPreviewRepository,
)
from ai_kp.infrastructure.database.auto_kp_jobs import AutoKpJobRepository
from ai_kp.infrastructure.database.campaign_objectives import CampaignObjectiveRepository
from ai_kp.infrastructure.database.character_lifecycle import CharacterLifecycleRepository
from ai_kp.infrastructure.database.character_timelines import CharacterTimelineRepository
from ai_kp.infrastructure.database.checks import SkillCheckRepository
from ai_kp.infrastructure.database.context_assemblies import ContextAssemblyRepository
from ai_kp.infrastructure.database.director_help_audits import DirectorHelpAuditRepository
from ai_kp.infrastructure.database.dynamic_branches import DynamicBranchRepository
from ai_kp.infrastructure.database.encounter_actions import EncounterActionRepository
from ai_kp.infrastructure.database.encounter_automation import EncounterAutomationRepository
from ai_kp.infrastructure.database.evaluations import EvaluationRepository
from ai_kp.infrastructure.database.facts import FactRepository
from ai_kp.infrastructure.database.gameplay import GameplayRepository
from ai_kp.infrastructure.database.handouts import HandoutRepository
from ai_kp.infrastructure.database.inventory_economy import InventoryEconomyRepository
from ai_kp.infrastructure.database.inventory_items import InventoryItemRepository
from ai_kp.infrastructure.database.investigators import InvestigatorRepository
from ai_kp.infrastructure.database.kernel_plans import KernelPlanRepository
from ai_kp.infrastructure.database.map_route_plans import MapRoutePlanRepository
from ai_kp.infrastructure.database.maps import MapRepository
from ai_kp.infrastructure.database.memory_timeline import MemoryTimelineRepository
from ai_kp.infrastructure.database.model_configuration import ModelConfigurationRepository
from ai_kp.infrastructure.database.module_graph import ModuleGraphRepository
from ai_kp.infrastructure.database.module_imports import ModuleImportRepository
from ai_kp.infrastructure.database.module_knowledge import ModuleKnowledgeRepository
from ai_kp.infrastructure.database.module_runs import ModuleRunRepository
from ai_kp.infrastructure.database.module_setting_profiles import (
    ModuleSettingProfileRepository,
)
from ai_kp.infrastructure.database.npc_reappearances import NpcReappearanceRepository
from ai_kp.infrastructure.database.parallel_action_batches import (
    ParallelActionBatchRepository,
)
from ai_kp.infrastructure.database.parallel_action_regathers import (
    ParallelActionRegatherRepository,
)
from ai_kp.infrastructure.database.private_random_resolutions import (
    PrivateRandomResolutionRepository,
)
from ai_kp.infrastructure.database.rows import decode_json_field, row_to_dict
from ai_kp.infrastructure.database.rulebooks import RulebookRepository
from ai_kp.infrastructure.database.scenario_contract_jobs import (
    ScenarioContractJobRepository,
)
from ai_kp.infrastructure.database.scenario_contract_overlays import (
    ScenarioContractOverlayRepository,
)
from ai_kp.infrastructure.database.scenario_contracts import ScenarioContractRepository
from ai_kp.infrastructure.database.scenario_run_states import ScenarioRunStateRepository
from ai_kp.infrastructure.database.security import SecurityRepository
from ai_kp.infrastructure.database.session_continuity import SessionContinuityRepository
from ai_kp.infrastructure.database.session_recaps import SessionRecapRepository
from ai_kp.infrastructure.database.session_seats import SessionSeatRepository
from ai_kp.infrastructure.database.session_zero import SessionZeroRepository
from ai_kp.infrastructure.database.table_messages import TableMessageRepository
from ai_kp.infrastructure.database.travel_graph import TravelGraphRepository
from ai_kp.infrastructure.database.turns import TurnRepository
from ai_kp.infrastructure.database.world import WorldRepository
from ai_kp.infrastructure.database.world_entities import WorldEntityRepository
from ai_kp.infrastructure.database.world_expansion_materializations import (
    WorldExpansionMaterializationRepository,
)
from ai_kp.infrastructure.realtime.outbox import RealtimeRepository

__all__ = ["Repository", "decode_json_field", "row_to_dict"]


class Repository(
    WorldRepository,
    WorldEntityRepository,
    ScenarioContractRepository,
    ScenarioContractJobRepository,
    ScenarioContractOverlayRepository,
    ScenarioRunStateRepository,
    ParallelActionBatchRepository,
    ParallelActionRegatherRepository,
    KernelPlanRepository,
    ActionResolutionPreviewRepository,
    ActionAdjudicationRepository,
    AutoKpJobRepository,
    EvaluationRepository,
    FactRepository,
    HandoutRepository,
    GameplayRepository,
    EncounterActionRepository,
    EncounterAutomationRepository,
    DynamicBranchRepository,
    TurnRepository,
    MapRepository,
    MapRoutePlanRepository,
    ContextAssemblyRepository,
    SecurityRepository,
    RealtimeRepository,
    InvestigatorRepository,
    InventoryItemRepository,
    InventoryEconomyRepository,
    SessionSeatRepository,
    SessionZeroRepository,
    SessionContinuityRepository,
    TableMessageRepository,
    SkillCheckRepository,
    RulebookRepository,
    ModuleImportRepository,
    ModuleKnowledgeRepository,
    ModuleGraphRepository,
    ModuleRunRepository,
    ModuleSettingProfileRepository,
    ModelConfigurationRepository,
    WorldExpansionMaterializationRepository,
    NpcReappearanceRepository,
    TravelGraphRepository,
    PrivateRandomResolutionRepository,
    MemoryTimelineRepository,
    SessionRecapRepository,
    CharacterTimelineRepository,
    CharacterLifecycleRepository,
    CampaignObjectiveRepository,
    DirectorHelpAuditRepository,
):
    """Backward-compatible facade over the feature-specific SQLite repositories."""

    def __init__(self, connection: sqlite3.Connection):
        super().__init__(connection)

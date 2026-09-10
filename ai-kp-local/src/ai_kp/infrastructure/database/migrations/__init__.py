"""Ordered, transactional SQLite schema migrations."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from ai_kp.infrastructure.database.migrations import (
    v0001_proposal_checks,
    v0002_player_action_idempotency,
    v0003_map_status,
    v0004_map_token_version,
    v0005_proposal_npc_updates,
    v0006_investigator_library,
    v0007_rulebook_knowledge,
    v0008_campaign_investigators,
    v0009_model_configuration,
    v0010_session_seats,
    v0011_skill_checks,
    v0012_map_spec_assets,
    v0013_map_revision_backfill,
    v0014_image_model_configuration,
    v0015_memory_fts,
    v0016_module_documents,
    v0017_module_knowledge,
    v0018_module_graph,
    v0019_module_document_structure,
    v0020_campaign_module_runs,
    v0021_module_run_version,
    v0022_knowledge_extraction_attempts,
    v0023_session_assignment_uniqueness,
    v0024_rule_source_ruleset_hash,
    v0025_scene_director_runtime,
    v0026_world_expansion_materialization,
    v0027_investigator_npc_encounters,
    v0028_npc_appearance_gating,
    v0029_campaign_travel_graph,
    v0030_private_random_resolutions,
    v0031_memory_curation,
    v0032_session_recaps,
    v0033_character_timelines,
    v0034_proposed_world_facts,
    v0035_opposed_checks,
    v0036_director_control,
    v0037_handouts,
    v0038_map_fog,
    v0039_simulation_evals,
    v0040_opposed_rerolls,
    v0041_control_event_sequence,
    v0042_coc7_gameplay,
    v0043_dynamic_branches,
    v0044_map_overlays_and_routes,
    v0045_check_visibility,
    v0046_campaign_ruleset_pins,
    v0047_check_random_evidence,
    v0048_automation_levels,
    v0049_auto_kp_jobs,
    v0050_action_adjudications,
    v0051_map_published_revision,
    v0052_module_campaign_imports,
    v0053_map_location_awareness,
    v0054_entity_candidates,
    v0055_action_resolution_previews,
    v0056_scenario_contracts,
    v0057_scenario_run_state,
    v0058_scenario_contract_overlays,
    v0059_model_capability_profile,
    v0060_scenario_contract_jobs,
    v0061_scenario_contract_record_repairs,
    v0062_scenario_contract_job_schema_repair,
    v0063_scenario_contract_coverage_supplements,
    v0064_kernel_plans,
    v0065_scenario_contract_supplement_cycles,
    v0066_check_plans,
    v0067_push_decisions,
    v0068_parallel_action_batches,
    v0069_parallel_batch_run_authority,
    v0070_parallel_batch_recovery_index,
    v0071_parallel_action_regathers,
    v0072_session_zero_safety,
    v0073_observers_and_table_messages,
    v0074_private_message_history_boundary,
    v0075_session_continuity,
    v0076_encounter_action_requests,
    v0077_inventory_economy,
    v0078_character_lifecycle,
    v0079_encounter_automation,
    v0080_campaign_objectives,
    v0081_model_execution_fence,
    v0082_scenario_contract_model_generation,
    v0083_module_chunk_section_ancestry,
    v0084_persistent_store_identity,
    v0085_director_help_audit_events,
    v0086_kernel_authority_basis,
    v0087_module_setting_profiles,
    v0088_campaign_world_entities,
    v0089_campaign_world_entity_states,
    v0090_proposed_world_entity_states,
    v0091_world_state_rules_source,
)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    migrate: Callable[[sqlite3.Connection], None]
    requires_foreign_keys_off: bool = False


MIGRATIONS = (
    Migration(
        v0001_proposal_checks.VERSION,
        v0001_proposal_checks.NAME,
        v0001_proposal_checks.migrate,
    ),
    Migration(
        v0002_player_action_idempotency.VERSION,
        v0002_player_action_idempotency.NAME,
        v0002_player_action_idempotency.migrate,
    ),
    Migration(v0003_map_status.VERSION, v0003_map_status.NAME, v0003_map_status.migrate),
    Migration(
        v0004_map_token_version.VERSION,
        v0004_map_token_version.NAME,
        v0004_map_token_version.migrate,
    ),
    Migration(
        v0005_proposal_npc_updates.VERSION,
        v0005_proposal_npc_updates.NAME,
        v0005_proposal_npc_updates.migrate,
    ),
    Migration(
        v0006_investigator_library.VERSION,
        v0006_investigator_library.NAME,
        v0006_investigator_library.migrate,
    ),
    Migration(
        v0007_rulebook_knowledge.VERSION,
        v0007_rulebook_knowledge.NAME,
        v0007_rulebook_knowledge.migrate,
    ),
    Migration(
        v0008_campaign_investigators.VERSION,
        v0008_campaign_investigators.NAME,
        v0008_campaign_investigators.migrate,
    ),
    Migration(
        v0009_model_configuration.VERSION,
        v0009_model_configuration.NAME,
        v0009_model_configuration.migrate,
    ),
    Migration(
        v0010_session_seats.VERSION,
        v0010_session_seats.NAME,
        v0010_session_seats.migrate,
    ),
    Migration(
        v0011_skill_checks.VERSION,
        v0011_skill_checks.NAME,
        v0011_skill_checks.migrate,
    ),
    Migration(
        v0012_map_spec_assets.VERSION,
        v0012_map_spec_assets.NAME,
        v0012_map_spec_assets.migrate,
    ),
    Migration(
        v0013_map_revision_backfill.VERSION,
        v0013_map_revision_backfill.NAME,
        v0013_map_revision_backfill.migrate,
    ),
    Migration(
        v0014_image_model_configuration.VERSION,
        v0014_image_model_configuration.NAME,
        v0014_image_model_configuration.migrate,
    ),
    Migration(
        v0015_memory_fts.VERSION,
        v0015_memory_fts.NAME,
        v0015_memory_fts.migrate,
    ),
    Migration(
        v0016_module_documents.VERSION,
        v0016_module_documents.NAME,
        v0016_module_documents.migrate,
    ),
    Migration(
        v0017_module_knowledge.VERSION,
        v0017_module_knowledge.NAME,
        v0017_module_knowledge.migrate,
    ),
    Migration(
        v0018_module_graph.VERSION,
        v0018_module_graph.NAME,
        v0018_module_graph.migrate,
    ),
    Migration(
        v0019_module_document_structure.VERSION,
        v0019_module_document_structure.NAME,
        v0019_module_document_structure.migrate,
    ),
    Migration(
        v0020_campaign_module_runs.VERSION,
        v0020_campaign_module_runs.NAME,
        v0020_campaign_module_runs.migrate,
    ),
    Migration(
        v0021_module_run_version.VERSION,
        v0021_module_run_version.NAME,
        v0021_module_run_version.migrate,
    ),
    Migration(
        v0022_knowledge_extraction_attempts.VERSION,
        v0022_knowledge_extraction_attempts.NAME,
        v0022_knowledge_extraction_attempts.migrate,
    ),
    Migration(
        v0023_session_assignment_uniqueness.VERSION,
        v0023_session_assignment_uniqueness.NAME,
        v0023_session_assignment_uniqueness.migrate,
    ),
    Migration(
        v0024_rule_source_ruleset_hash.VERSION,
        v0024_rule_source_ruleset_hash.NAME,
        v0024_rule_source_ruleset_hash.migrate,
        requires_foreign_keys_off=True,
    ),
    Migration(
        v0025_scene_director_runtime.VERSION,
        v0025_scene_director_runtime.NAME,
        v0025_scene_director_runtime.migrate,
    ),
    Migration(
        v0026_world_expansion_materialization.VERSION,
        v0026_world_expansion_materialization.NAME,
        v0026_world_expansion_materialization.migrate,
    ),
    Migration(
        v0027_investigator_npc_encounters.VERSION,
        v0027_investigator_npc_encounters.NAME,
        v0027_investigator_npc_encounters.migrate,
    ),
    Migration(
        v0028_npc_appearance_gating.VERSION,
        v0028_npc_appearance_gating.NAME,
        v0028_npc_appearance_gating.migrate,
    ),
    Migration(
        v0029_campaign_travel_graph.VERSION,
        v0029_campaign_travel_graph.NAME,
        v0029_campaign_travel_graph.migrate,
    ),
    Migration(
        v0030_private_random_resolutions.VERSION,
        v0030_private_random_resolutions.NAME,
        v0030_private_random_resolutions.migrate,
    ),
    Migration(
        v0031_memory_curation.VERSION,
        v0031_memory_curation.NAME,
        v0031_memory_curation.migrate,
    ),
    Migration(
        v0032_session_recaps.VERSION,
        v0032_session_recaps.NAME,
        v0032_session_recaps.migrate,
    ),
    Migration(
        v0033_character_timelines.VERSION,
        v0033_character_timelines.NAME,
        v0033_character_timelines.migrate,
    ),
    Migration(
        v0034_proposed_world_facts.VERSION,
        v0034_proposed_world_facts.NAME,
        v0034_proposed_world_facts.migrate,
    ),
    Migration(
        v0035_opposed_checks.VERSION,
        v0035_opposed_checks.NAME,
        v0035_opposed_checks.migrate,
    ),
    Migration(
        v0036_director_control.VERSION,
        v0036_director_control.NAME,
        v0036_director_control.migrate,
    ),
    Migration(v0037_handouts.VERSION, v0037_handouts.NAME, v0037_handouts.migrate),
    Migration(v0038_map_fog.VERSION, v0038_map_fog.NAME, v0038_map_fog.migrate),
    Migration(
        v0039_simulation_evals.VERSION,
        v0039_simulation_evals.NAME,
        v0039_simulation_evals.migrate,
    ),
    Migration(
        v0040_opposed_rerolls.VERSION,
        v0040_opposed_rerolls.NAME,
        v0040_opposed_rerolls.migrate,
    ),
    Migration(
        v0041_control_event_sequence.VERSION,
        v0041_control_event_sequence.NAME,
        v0041_control_event_sequence.migrate,
    ),
    Migration(
        v0042_coc7_gameplay.VERSION,
        v0042_coc7_gameplay.NAME,
        v0042_coc7_gameplay.migrate,
    ),
    Migration(
        v0043_dynamic_branches.VERSION,
        v0043_dynamic_branches.NAME,
        v0043_dynamic_branches.migrate,
    ),
    Migration(
        v0044_map_overlays_and_routes.VERSION,
        v0044_map_overlays_and_routes.NAME,
        v0044_map_overlays_and_routes.migrate,
    ),
    Migration(
        v0045_check_visibility.VERSION,
        v0045_check_visibility.NAME,
        v0045_check_visibility.migrate,
    ),
    Migration(
        v0046_campaign_ruleset_pins.VERSION,
        v0046_campaign_ruleset_pins.NAME,
        v0046_campaign_ruleset_pins.migrate,
    ),
    Migration(
        v0047_check_random_evidence.VERSION,
        v0047_check_random_evidence.NAME,
        v0047_check_random_evidence.migrate,
    ),
    Migration(
        v0048_automation_levels.VERSION,
        v0048_automation_levels.NAME,
        v0048_automation_levels.migrate,
    ),
    Migration(
        v0049_auto_kp_jobs.VERSION,
        v0049_auto_kp_jobs.NAME,
        v0049_auto_kp_jobs.migrate,
    ),
    Migration(
        v0050_action_adjudications.VERSION,
        v0050_action_adjudications.NAME,
        v0050_action_adjudications.migrate,
    ),
    Migration(
        v0051_map_published_revision.VERSION,
        v0051_map_published_revision.NAME,
        v0051_map_published_revision.migrate,
    ),
    Migration(
        v0052_module_campaign_imports.VERSION,
        v0052_module_campaign_imports.NAME,
        v0052_module_campaign_imports.migrate,
    ),
    Migration(
        v0053_map_location_awareness.VERSION,
        v0053_map_location_awareness.NAME,
        v0053_map_location_awareness.migrate,
    ),
    Migration(
        v0054_entity_candidates.VERSION,
        v0054_entity_candidates.NAME,
        v0054_entity_candidates.migrate,
    ),
    Migration(
        v0055_action_resolution_previews.VERSION,
        v0055_action_resolution_previews.NAME,
        v0055_action_resolution_previews.migrate,
    ),
    Migration(
        v0056_scenario_contracts.VERSION,
        v0056_scenario_contracts.NAME,
        v0056_scenario_contracts.migrate,
    ),
    Migration(
        v0057_scenario_run_state.VERSION,
        v0057_scenario_run_state.NAME,
        v0057_scenario_run_state.migrate,
    ),
    Migration(
        v0058_scenario_contract_overlays.VERSION,
        v0058_scenario_contract_overlays.NAME,
        v0058_scenario_contract_overlays.migrate,
    ),
    Migration(
        v0059_model_capability_profile.VERSION,
        v0059_model_capability_profile.NAME,
        v0059_model_capability_profile.migrate,
    ),
    Migration(
        v0060_scenario_contract_jobs.VERSION,
        v0060_scenario_contract_jobs.NAME,
        v0060_scenario_contract_jobs.migrate,
    ),
    Migration(
        v0061_scenario_contract_record_repairs.VERSION,
        v0061_scenario_contract_record_repairs.NAME,
        v0061_scenario_contract_record_repairs.migrate,
    ),
    Migration(
        v0062_scenario_contract_job_schema_repair.VERSION,
        v0062_scenario_contract_job_schema_repair.NAME,
        v0062_scenario_contract_job_schema_repair.migrate,
    ),
    Migration(
        v0063_scenario_contract_coverage_supplements.VERSION,
        v0063_scenario_contract_coverage_supplements.NAME,
        v0063_scenario_contract_coverage_supplements.migrate,
    ),
    Migration(
        v0064_kernel_plans.VERSION,
        v0064_kernel_plans.NAME,
        v0064_kernel_plans.migrate,
    ),
    Migration(
        v0065_scenario_contract_supplement_cycles.VERSION,
        v0065_scenario_contract_supplement_cycles.NAME,
        v0065_scenario_contract_supplement_cycles.migrate,
    ),
    Migration(
        v0066_check_plans.VERSION,
        v0066_check_plans.NAME,
        v0066_check_plans.migrate,
    ),
    Migration(
        v0067_push_decisions.VERSION,
        v0067_push_decisions.NAME,
        v0067_push_decisions.migrate,
    ),
    Migration(
        v0068_parallel_action_batches.VERSION,
        v0068_parallel_action_batches.NAME,
        v0068_parallel_action_batches.migrate,
    ),
    Migration(
        v0069_parallel_batch_run_authority.VERSION,
        v0069_parallel_batch_run_authority.NAME,
        v0069_parallel_batch_run_authority.migrate,
    ),
    Migration(
        v0070_parallel_batch_recovery_index.VERSION,
        v0070_parallel_batch_recovery_index.NAME,
        v0070_parallel_batch_recovery_index.migrate,
    ),
    Migration(
        v0071_parallel_action_regathers.VERSION,
        v0071_parallel_action_regathers.NAME,
        v0071_parallel_action_regathers.migrate,
    ),
    Migration(
        v0072_session_zero_safety.VERSION,
        v0072_session_zero_safety.NAME,
        v0072_session_zero_safety.migrate,
    ),
    Migration(
        v0073_observers_and_table_messages.VERSION,
        v0073_observers_and_table_messages.NAME,
        v0073_observers_and_table_messages.migrate,
        requires_foreign_keys_off=True,
    ),
    Migration(
        v0074_private_message_history_boundary.VERSION,
        v0074_private_message_history_boundary.NAME,
        v0074_private_message_history_boundary.migrate,
    ),
    Migration(
        v0075_session_continuity.VERSION,
        v0075_session_continuity.NAME,
        v0075_session_continuity.migrate,
    ),
    Migration(
        v0076_encounter_action_requests.VERSION,
        v0076_encounter_action_requests.NAME,
        v0076_encounter_action_requests.migrate,
    ),
    Migration(
        v0077_inventory_economy.VERSION,
        v0077_inventory_economy.NAME,
        v0077_inventory_economy.migrate,
    ),
    Migration(
        v0078_character_lifecycle.VERSION,
        v0078_character_lifecycle.NAME,
        v0078_character_lifecycle.migrate,
    ),
    Migration(
        v0079_encounter_automation.VERSION,
        v0079_encounter_automation.NAME,
        v0079_encounter_automation.migrate,
        requires_foreign_keys_off=True,
    ),
    Migration(
        v0080_campaign_objectives.VERSION,
        v0080_campaign_objectives.NAME,
        v0080_campaign_objectives.migrate,
    ),
    Migration(
        v0081_model_execution_fence.VERSION,
        v0081_model_execution_fence.NAME,
        v0081_model_execution_fence.migrate,
    ),
    Migration(
        v0082_scenario_contract_model_generation.VERSION,
        v0082_scenario_contract_model_generation.NAME,
        v0082_scenario_contract_model_generation.migrate,
    ),
    Migration(
        v0083_module_chunk_section_ancestry.VERSION,
        v0083_module_chunk_section_ancestry.NAME,
        v0083_module_chunk_section_ancestry.migrate,
    ),
    Migration(
        v0084_persistent_store_identity.VERSION,
        v0084_persistent_store_identity.NAME,
        v0084_persistent_store_identity.migrate,
    ),
    Migration(
        v0085_director_help_audit_events.VERSION,
        v0085_director_help_audit_events.NAME,
        v0085_director_help_audit_events.migrate,
    ),
    Migration(
        v0086_kernel_authority_basis.VERSION,
        v0086_kernel_authority_basis.NAME,
        v0086_kernel_authority_basis.migrate,
    ),
    Migration(
        v0087_module_setting_profiles.VERSION,
        v0087_module_setting_profiles.NAME,
        v0087_module_setting_profiles.migrate,
    ),
    Migration(
        v0088_campaign_world_entities.VERSION,
        v0088_campaign_world_entities.NAME,
        v0088_campaign_world_entities.migrate,
    ),
    Migration(
        v0089_campaign_world_entity_states.VERSION,
        v0089_campaign_world_entity_states.NAME,
        v0089_campaign_world_entity_states.migrate,
    ),
    Migration(
        v0090_proposed_world_entity_states.VERSION,
        v0090_proposed_world_entity_states.NAME,
        v0090_proposed_world_entity_states.migrate,
    ),
    Migration(
        v0091_world_state_rules_source.VERSION,
        v0091_world_state_rules_source.NAME,
        v0091_world_state_rules_source.migrate,
    ),
)

LATEST_SCHEMA_VERSION = MIGRATIONS[-1].version


def apply_migrations(connection: sqlite3.Connection) -> None:
    """Apply all unapplied migrations in order and record each atomically."""

    _validate_registry()
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL UNIQUE,
          applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    applied = {
        int(row[0]): str(row[1])
        for row in connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
    }
    future_versions = sorted(version for version in applied if version > LATEST_SCHEMA_VERSION)
    if future_versions:
        raise RuntimeError(
            "database schema is newer than this application: "
            f"found version {future_versions[-1]}, supports {LATEST_SCHEMA_VERSION}"
        )

    for migration in MIGRATIONS:
        recorded_name = applied.get(migration.version)
        if recorded_name is not None:
            if recorded_name != migration.name:
                raise RuntimeError(
                    f"schema migration {migration.version} name mismatch: "
                    f"database has {recorded_name!r}, application has {migration.name!r}"
                )
            continue

        savepoint = f"schema_migration_{migration.version:04d}"
        foreign_keys_were_enabled = False
        try:
            if migration.requires_foreign_keys_off:
                foreign_keys_were_enabled = bool(
                    connection.execute("PRAGMA foreign_keys").fetchone()[0]
                )
                if foreign_keys_were_enabled:
                    if connection.in_transaction:
                        raise RuntimeError(
                            f"schema migration {migration.version} requires foreign "
                            "keys to be disabled before starting its savepoint"
                        )
                    connection.execute("PRAGMA foreign_keys = OFF")
                    if connection.execute("PRAGMA foreign_keys").fetchone()[0]:
                        raise RuntimeError(
                            f"could not disable foreign keys for schema migration "
                            f"{migration.version}"
                        )

            connection.execute(f"SAVEPOINT {savepoint}")
            try:
                migration.migrate(connection)
                if migration.requires_foreign_keys_off:
                    violations = connection.execute(
                        "PRAGMA foreign_key_check"
                    ).fetchmany(5)
                    if violations:
                        details = [tuple(row) for row in violations]
                        raise RuntimeError(
                            f"schema migration {migration.version} failed foreign "
                            f"key validation: {details}"
                        )
                connection.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                    (migration.version, migration.name),
                )
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
        finally:
            if foreign_keys_were_enabled:
                connection.execute("PRAGMA foreign_keys = ON")
                if not connection.execute("PRAGMA foreign_keys").fetchone()[0]:
                    raise RuntimeError(
                        f"could not restore foreign keys after schema migration "
                        f"{migration.version}"
                    )


def _validate_registry() -> None:
    versions = [migration.version for migration in MIGRATIONS]
    names = [migration.name for migration in MIGRATIONS]
    if versions != list(range(1, len(MIGRATIONS) + 1)):
        raise RuntimeError("schema migration versions must be contiguous and start at 1")
    if len(names) != len(set(names)):
        raise RuntimeError("schema migration names must be unique")


__all__ = ["LATEST_SCHEMA_VERSION", "MIGRATIONS", "Migration", "apply_migrations"]

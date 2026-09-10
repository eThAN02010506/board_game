"""Versioned AC-LONG edge-case catalogue backed by executable regressions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EdgeCaseSpec:
    id: str
    category: str
    description: str
    pytest_node: str


def _case(
    number: int,
    category: str,
    description: str,
    node: str,
) -> EdgeCaseSpec:
    return EdgeCaseSpec(
        id=f"EDGE-{number:03d}",
        category=category,
        description=description,
        pytest_node=node,
    )


EDGE_CASES = (
    _case(1, "safety", "Session 0 revision requires fresh consent", "tests/test_session_zero.py::test_session_zero_revisions_require_fresh_table_consent_and_hide_owners"),
    _case(2, "safety", "Safety use records no private reason", "tests/test_session_zero.py::test_safety_tool_records_no_reason_and_pauses_then_resumes_ai"),
    _case(3, "safety", "Generation policy is anonymous and campaign bound", "tests/test_session_zero.py::test_generation_policy_is_anonymous_and_bound_to_campaign_scope"),
    _case(4, "safety", "API rejects collection of a private safety reason", "tests/test_session_zero.py::test_session_zero_api_rejects_reason_collection_and_cross_member_private_style"),
    _case(5, "privacy", "Table and whisper scopes are deterministic", "tests/test_table_messages.py::test_message_visibility_and_whisper_policy_are_deterministic"),
    _case(6, "privacy", "Observer remains read-only", "tests/test_table_messages.py::test_observer_api_is_read_only_and_does_not_block_session_zero"),
    _case(7, "privacy", "Observer realtime uses an explicit allow-list", "tests/test_table_messages.py::test_observer_realtime_projection_is_an_explicit_allowlist"),
    _case(8, "continuity", "Session End is atomic, idempotent, and role projected", "tests/test_session_continuity.py::test_session_end_continue_is_atomic_idempotent_and_role_projected"),
    _case(9, "continuity", "Session End rejects unresolved table work", "tests/test_session_continuity.py::test_session_end_rejects_unresolved_table_work"),
    _case(10, "continuity", "Prepared episode cannot bypass Session 0", "tests/test_session_continuity.py::test_prepared_episode_cannot_end_before_session_zero"),
    _case(11, "continuity", "Session lifecycle API enforces role projection", "tests/test_session_continuity.py::test_continuity_api_enforces_role_projection_and_action_gate"),
    _case(12, "lifecycle", "Death, observer, replacement, leave, and return preserve history", "tests/test_character_lifecycle.py::test_death_observer_replacement_leave_and_return_keep_history"),
    _case(13, "lifecycle", "Player cannot accept another member's lifecycle decision", "tests/test_character_lifecycle.py::test_player_cannot_accept_another_members_lifecycle_request"),
    _case(14, "inventory", "Hidden properties, consumption, and currency are atomic", "tests/test_inventory_api.py::test_inventory_ownership_hidden_truth_consumption_and_currency_are_atomic"),
    _case(15, "inventory", "Trades and recipes commit authoritatively", "tests/test_inventory_api.py::test_inventory_offer_trade_and_recipe_commit_as_authoritative_transactions"),
    _case(16, "inventory", "Initial character assets materialize once", "tests/test_inventory_api.py::test_approved_character_assets_materialize_once_into_the_authoritative_ledger"),
    _case(17, "inventory", "Concurrent unique-loot pickup has one winner", "tests/test_inventory_api.py::test_two_real_connections_cannot_pick_up_the_same_unique_loot"),
    _case(18, "combat", "Enemy Agent repairs once then rejects bad output", "tests/test_encounter_automation.py::test_enemy_turn_adapter_repairs_once_and_rejects_unstructured_output"),
    _case(19, "combat", "Enemy Agent choice is bounded and durable", "tests/test_encounter_automation.py::test_enemy_agent_selection_is_bounded_and_durable"),
    _case(20, "combat", "Enemy retry reuses checkpointed random evidence", "tests/test_encounter_automation.py::test_enemy_turn_retry_reuses_checkpointed_random_evidence"),
    _case(21, "combat", "Enemy model failure uses a non-attacking fallback", "tests/test_encounter_automation.py::test_enemy_agent_failure_uses_non_attacking_liveness_fallback"),
    _case(22, "combat", "Idle defense and safety pause are deterministic", "tests/test_encounter_automation.py::test_idle_defend_policy_is_deterministic_and_safety_pause_resumes"),
    _case(23, "combat", "Worker rejects stale enemy-turn work", "tests/test_encounter_automation.py::test_worker_executes_current_turn_and_safely_supersedes_stale_job"),
    _case(24, "combat", "Rules engine ends combat with one viable side", "tests/test_encounter_automation.py::test_rules_engine_completes_combat_when_only_one_declared_side_can_act"),
    _case(25, "combat", "Idle pause resumes the same turn", "tests/test_encounter_automation.py::test_idle_pause_policy_requeues_same_turn_after_campaign_resume"),
    _case(26, "parallel", "Direct multiplayer workflow settles atomically", "tests/test_parallel_action_workflow_service.py::test_direct_workflow_is_idempotent_owner_confirmed_and_atomically_settled"),
    _case(27, "parallel", "Prepare retry rechecks idempotency under lock", "tests/test_parallel_action_workflow_service.py::test_prepare_rechecks_idempotency_under_lock_before_persisting"),
    _case(28, "parallel", "Confirmation retry preserves player consent", "tests/test_parallel_action_workflow_service.py::test_confirm_rechecks_same_player_consent_under_lock"),
    _case(29, "parallel", "Commit retry returns the durable receipt", "tests/test_parallel_action_workflow_service.py::test_commit_rechecks_settled_receipt_under_lock"),
    _case(30, "parallel", "Observer handles a concurrent settlement winner", "tests/test_parallel_action_workflow_service.py::test_check_observer_replays_settlement_that_wins_the_write_lock"),
    _case(31, "parallel", "Skill rebind and checks rendezvous in one batch", "tests/test_parallel_action_workflow_service.py::test_skill_rebind_check_rendezvous_and_consequence_finish_one_batch"),
    _case(32, "parallel", "Legacy keyword precheck cannot rewrite kernel consent", "tests/test_parallel_action_workflow_service.py::test_parallel_kernel_adjudication_does_not_apply_legacy_keyword_precheck"),
    _case(33, "parallel", "Failed check executes accepted stakes", "tests/test_parallel_action_workflow_service.py::test_failed_parallel_check_executes_and_narrates_exact_accepted_stakes"),
    _case(34, "parallel", "Pushed failure preserves method and worse stakes", "tests/test_parallel_action_workflow_service.py::test_pushed_failure_narration_preserves_approach_and_exact_stakes"),
    _case(35, "parallel", "Hidden success delivers a public clue without dice metadata", "tests/test_parallel_action_workflow_service.py::test_hidden_success_delivers_contract_public_clue_without_roll_metadata"),
    _case(36, "parallel", "Hidden failure delivers visible stakes without dice metadata", "tests/test_parallel_action_workflow_service.py::test_hidden_failure_narrates_visible_stakes_without_roll_metadata"),
    _case(37, "parallel", "Player revision retires the entire old batch", "tests/test_parallel_action_workflow_service.py::test_player_revision_supersedes_batch_with_owned_audit_identity"),
    _case(38, "parallel", "Player can withdraw before dice when KP disappears", "tests/test_parallel_action_workflow_service.py::test_player_can_withdraw_consent_when_no_active_kp_remains"),
    _case(39, "parallel", "Known results prevent outcome fishing", "tests/test_parallel_action_workflow_service.py::test_revision_after_confirmation_is_rejected_to_prevent_outcome_fishing"),
    _case(40, "parallel", "Run drift before confirmation clears old consent", "tests/test_parallel_action_workflow_service.py::test_run_authority_drift_before_confirmation_pauses_without_player_consent"),
    _case(41, "parallel", "Run drift before observation cannot bind a result", "tests/test_parallel_action_workflow_service.py::test_run_authority_drift_before_observe_does_not_bind_check_result"),
    _case(42, "parallel", "Run drift before commit rolls back all effects", "tests/test_parallel_action_workflow_service.py::test_run_authority_drift_before_commit_rolls_back_without_world_effects"),
    _case(43, "parallel", "Late check override fails closed", "tests/test_parallel_action_workflow_service.py::test_ready_batch_detects_a_late_check_override_and_fails_closed"),
    _case(44, "parallel", "Prepare failure leaves no partial proposals", "tests/test_parallel_action_workflow_service.py::test_prepare_failure_rolls_back_every_independent_proposal"),
    _case(45, "parallel", "Commit failure rolls back kernel and narration", "tests/test_parallel_action_workflow_service.py::test_commit_failure_rolls_back_kernel_and_all_proposal_finalization"),
    _case(46, "security", "Cross-campaign authorization matrix isolates resources", "tests/test_security_matrix.py::test_authorization_matrix_and_cross_campaign_isolation"),
    _case(47, "security", "Injected repository failure rolls back the request", "tests/test_security_matrix.py::test_request_transaction_rolls_back_after_injected_repository_failure"),
    _case(48, "security", "Opposed checks replay an exact tie", "tests/test_security_matrix.py::test_persisted_opposed_check_api_resolves_and_rerolls_exact_tie"),
    _case(49, "security", "Concurrent opposed reroll creates one child", "tests/test_security_matrix.py::test_concurrent_opposed_reroll_creates_a_single_child"),
    _case(50, "weak_model", "Malformed independent audit fails closed", "tests/test_world_expansion_authoring.py::test_malformed_audit_cannot_force_player_to_supply_internal_details"),
)


def validate_edge_case_catalog(cases: tuple[EdgeCaseSpec, ...] = EDGE_CASES) -> None:
    if len(cases) < 50:
        raise ValueError("AC-LONG requires at least 50 edge cases")
    ids = [item.id for item in cases]
    nodes = [item.pytest_node for item in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Edge-case IDs must be unique")
    if len(nodes) != len(set(nodes)):
        raise ValueError("Each edge case must have independent executable evidence")
    if any(not item.category or not item.description for item in cases):
        raise ValueError("Edge cases require category and description")


validate_edge_case_catalog()

__all__ = ["EDGE_CASES", "EdgeCaseSpec", "validate_edge_case_catalog"]

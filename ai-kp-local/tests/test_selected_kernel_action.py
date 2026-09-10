from __future__ import annotations

from pathlib import Path

import pytest

from ai_kp.platform.resolution import (
    SelectedOperator,
    prepare_selected_kernel_action,
    selected_operator_from_semantic,
)
from ai_kp.platform.resolution.contracts import ActionOperator, ScenarioContract, SkillChoice
from ai_kp.platform.resolution.semantic_adapter import SemanticSelection


def _contract() -> ScenarioContract:
    return ScenarioContract(
        contract_id="shared-selection",
        source_version=1,
        ruleset_id="test",
        title="Shared selection",
        operators=(
            ActionOperator(
                operator_id="search-room",
                title="Search the room",
                public_setup="你开始检查房间，结果仍待确认。",
                policy="required_check",
                skill_choices=(
                    SkillChoice(
                        skill_key="spot-hidden",
                        difficulty="regular",
                        reason="需要发现隐藏细节。",
                    ),
                ),
                maximum_effect="发现这个房间中可被该检定揭示的线索。",
            ),
        ),
    )


def _selection(*, skill: str | None = "spot-hidden") -> SelectedOperator:
    return SelectedOperator(
        operator_id="search-room",
        requested_skill_key=skill,
    )


def test_ai_and_human_sources_share_identical_primitive_preparation() -> None:
    contract = _contract()
    snapshot = contract.initial_snapshot("run-1")

    human = prepare_selected_kernel_action(
        contract,
        snapshot,
        action_id="action-1",
        actor_id="pc-1",
        player_action="我仔细搜索房间。",
        selection=_selection(),
    )
    ai = prepare_selected_kernel_action(
        contract,
        snapshot,
        action_id="action-1",
        actor_id="pc-1",
        player_action="我仔细搜索房间。",
        selection=_selection(),
    )

    assert human == ai
    assert human.preview.preview_hash == ai.preview.preview_hash
    assert human.preview.selected_skill_key == "spot-hidden"


@pytest.mark.parametrize(
    ("selection", "message"),
    [
        (SelectedOperator(operator_id="invented-action"), "outside the bound scenario contract"),
        (_selection(skill="invented-skill"), "outside the operator's allowed choices"),
    ],
)
def test_shared_preparation_rejects_authority_outside_the_contract(
    selection: SelectedOperator,
    message: str,
) -> None:
    contract = _contract()
    with pytest.raises(ValueError, match=message):
        prepare_selected_kernel_action(
            contract,
            contract.initial_snapshot("run-1"),
            action_id="action-1",
            actor_id="pc-1",
            player_action="我搜索房间。",
            selection=selection,
        )


def test_semantic_adapter_discards_confidence_and_rejects_non_operators() -> None:
    selected = selected_operator_from_semantic(
        SemanticSelection(
            kind="operator",
            candidate_id="search-room",
            requested_skill_key="spot-hidden",
            confidence="low",
        )
    )

    assert selected == _selection()
    with pytest.raises(ValueError, match="requires one selected operator"):
        selected_operator_from_semantic(
            SemanticSelection(
                kind="clarification",
                confidence="low",
                clarification="How?",
            )
        )


def test_authoritative_flows_do_not_rebuild_primitive_previews() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src" / "ai_kp"
    for relative_path in (
        "application/kernel_action_service.py",
        "application/parallel_action_planning_service.py",
        "application/resolution_shadow_service.py",
        "infrastructure/database/parallel_action_skill_rebinder.py",
    ):
        source = (source_root / relative_path).read_text(encoding="utf-8")
        assert "ActionResolutionKernel" not in source
        assert "deterministic_kernel_narrative" not in source
        assert "prepare_selected_kernel_action" in source

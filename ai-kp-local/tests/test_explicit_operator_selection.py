from __future__ import annotations

import pytest

from ai_kp.platform.resolution.contracts import ScenarioContract
from ai_kp.platform.resolution.explicit_operator_selection import (
    resolve_explicit_published_operator,
)
from tests.test_parallel_action_kernel import parallel_contract


def test_exact_published_title_builds_a_manual_confirmation_selection() -> None:
    contract = parallel_contract()

    result = resolve_explicit_published_operator(
        contract,
        contract.initial_snapshot("explicit-run"),
        '选择已发布行动“Find a hidden mark”',
    )

    assert result is not None and result.status == "selected"
    assert result.interpretation.route == "mechanical"
    assert result.selection_result is not None
    assert result.selection_result.selection.candidate_id == "find-mark"
    assert result.selection_result.selection.requested_skill_key is None
    assert result.selection_result.allowed_skill_keys == ("spot_hidden", "art")
    assert result.selection_result.requires_manual_confirmation is True
    assert result.selection_result.attempt_count == 0


@pytest.mark.parametrize(
    "player_action",
    (
        '不要选择已发布行动“Open the panel”',
        '我只是举例：选择已发布行动“Open the panel”，但不执行。',
    ),
)
def test_negation_or_casual_mention_cannot_acquire_operator_authority(
    player_action: str,
) -> None:
    contract = parallel_contract()

    result = resolve_explicit_published_operator(
        contract, contract.initial_snapshot("explicit-negative"), player_action
    )

    assert result is not None and result.status == "invalid_syntax"
    assert result.interpretation.route == "clarification"
    assert result.selection_result is None


def test_duplicate_published_titles_are_rejected_as_ambiguous() -> None:
    payload = parallel_contract().model_dump(mode="json")
    payload["operators"][1]["title"] = payload["operators"][0]["title"]
    contract = ScenarioContract.model_validate(payload)

    result = resolve_explicit_published_operator(
        contract,
        contract.initial_snapshot("explicit-ambiguous"),
        '选择已发布行动“Find a hidden mark”',
    )

    assert result is not None and result.status == "ambiguous"
    assert result.selection_result is None


def test_exact_but_unavailable_operator_is_bound_and_fails_closed() -> None:
    payload = parallel_contract().model_dump(mode="json")
    payload["operators"][1]["preconditions"] = [{
        "path": "facts.panel_powered",
        "operator": "eq",
        "value": True,
    }]
    contract = ScenarioContract.model_validate(payload)

    result = resolve_explicit_published_operator(
        contract,
        contract.initial_snapshot("explicit-unavailable"),
        '选择已发布行动“Open the panel”',
    )

    assert result is not None and result.status == "unavailable"
    assert result.selection_result is not None
    assert result.selection_result.selection.candidate_id == "open-panel"
    assert result.selection_result.offered_candidates[0].available is False

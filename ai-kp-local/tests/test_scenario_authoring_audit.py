from ai_kp.platform.resolution.scenario_authoring_audit import (
    bounded_authoring_assumptions,
    is_server_review_diagnostic,
)


def test_server_diagnostics_survive_a_full_model_assumption_budget() -> None:
    model_notes = tuple(f"Model assumption {index}" for index in range(64))
    role_diagnostic = (
        "Source did not prove a playable location role; "
        "IR location was discarded: handout (Handout 4)"
    )

    result = bounded_authoring_assumptions(model_notes, (role_diagnostic,))

    assert len(result) == 64
    assert result[0] == role_diagnostic
    assert result[-1] == "Model assumption 62"
    assert is_server_review_diagnostic(role_diagnostic) is True


def test_model_prose_is_not_mislabeled_as_a_server_diagnostic() -> None:
    assert is_server_review_diagnostic(
        "The model assumes a hidden tunnel connects both rooms."
    ) is False


def test_source_scene_materialization_is_a_server_diagnostic() -> None:
    assert is_server_review_diagnostic(
        "Server source scene locations materialized: source_scene_123 (Old House)"
    ) is True


def test_latest_server_diagnostics_win_when_server_audit_exceeds_limit() -> None:
    diagnostics = tuple(
        f"Server source scene locations materialized: scene-{index}"
        for index in range(65)
    )

    result = bounded_authoring_assumptions(diagnostics)

    assert len(result) == 64
    assert result[0].endswith("scene-1")
    assert result[-1].endswith("scene-64")

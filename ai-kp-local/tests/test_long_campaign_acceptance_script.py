from pathlib import Path


def test_acceptance_script_refuses_to_claim_missing_ui_or_persona_evidence() -> None:
    source = (
        Path(__file__).parents[1] / "scripts" / "long_campaign_acceptance.py"
    ).read_text(encoding="utf-8")

    assert '"ui_journey_passed": False' in source
    assert '"four_player_full_ai_passed": False' in source
    assert '"schema_version": SCHEMA_VERSION' in source
    assert '"persona_evidence": []' in source
    assert '"rps_evidence": []' in source
    assert '"journey_coverage_evidence": []' in source
    assert '"repair_rounds": []' in source
    assert '"model_switch_passed": durability["model_switch_passed"]' in source
    assert (
        '"lifecycle_durability_passed": durability["lifecycle"]["passed"]'
        in source
    )
    assert "evaluate_long_campaign_evidence(evidence)" in source

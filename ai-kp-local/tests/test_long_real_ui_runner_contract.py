from pathlib import Path


def test_long_real_ui_runner_requires_agent_driver_and_scoped_anchor() -> None:
    source = (
        Path(__file__).parents[1]
        / "apps"
        / "web"
        / "e2e"
        / "full-ai-long-campaign.realcase.e2e.ts"
    ).read_text(encoding="utf-8")

    assert "|| !playerDriverModel" in source
    assert "single_session_anchor_evidence" in source
    assert "authority_refs: await authorityRefs.snapshot()" in source
    assert "evidence.validated_personas" not in source
    for required_flow in (
        "killAndReplaceInvestigator",
        "temporarilyLeaveAndReturn",
        "addPlayerToFullAiCampaign",
        "movePlayerToObserver",
        "playRulesetCombatFromUi",
        "registerAndPickUpLootFromUi",
        "applyFirstAvailableGrowthFromUi",
        "exerciseAiAssistedGmHandoff",
        "observePlayerStandardBaseline",
    ):
        assert required_flow in source

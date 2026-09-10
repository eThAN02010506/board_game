from ai_kp.rulesets.coc7.scenario_checks import coc7_scenario_check_catalog


def test_exact_characteristic_and_skill_terms_resolve_to_one_key() -> None:
    catalog = coc7_scenario_check_catalog()

    assert catalog.resolve("灵感") == ("int",)
    assert catalog.resolve("困难灵感检定") == ("int",)
    assert catalog.resolve("机械维修") == ("coc7.mechanical_repair",)


def test_broad_source_concept_preserves_multiple_player_choices() -> None:
    catalog = coc7_scenario_check_catalog()

    assert catalog.resolve("修理") == (
        "coc7.electrical_repair",
        "coc7.mechanical_repair",
    )


def test_unknown_source_term_fails_closed() -> None:
    assert coc7_scenario_check_catalog().resolve("心灵感应") == ()

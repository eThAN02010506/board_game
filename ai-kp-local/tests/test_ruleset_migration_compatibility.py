def test_legacy_dice_imports_are_identity_aliases() -> None:
    from ai_kp.rules import dice as legacy
    from ai_kp.rulesets.coc7.mechanics import skill_check as canonical

    assert legacy.resolve_d100 is canonical.resolve_d100
    assert legacy.success_level is canonical.success_level
    assert legacy.RULESET_ID == canonical.RULESET_ID


def test_legacy_character_imports_are_identity_aliases() -> None:
    from ai_kp.characters import xlsx_import as legacy_xlsx
    from ai_kp.rules import coc7_character as legacy_character
    from ai_kp.rules import coc7_recommendations as legacy_recommendations
    from ai_kp.rules import coc7_skills as legacy_skills
    from ai_kp.rulesets.coc7.character import recommendations, skills, validator
    from ai_kp.rulesets.coc7.character import xlsx_import as canonical_xlsx

    assert legacy_character.normalize_character_sheet is validator.normalize_character_sheet
    assert legacy_skills.list_coc7_skill_catalog is skills.list_coc7_skill_catalog
    assert (
        legacy_recommendations.recommend_coc7_skill_points
        is recommendations.recommend_coc7_skill_points
    )
    assert legacy_xlsx.import_coc_character_xlsx is canonical_xlsx.import_coc_character_xlsx


def test_legacy_director_and_rulebook_imports_are_identity_aliases() -> None:
    from ai_kp.director import context_builder, orchestrator, turn_output
    from ai_kp.infrastructure.knowledge import minirag
    from ai_kp.kp import context_builder as legacy_context
    from ai_kp.kp import orchestrator as legacy_orchestrator
    from ai_kp.kp import turn_output as legacy_output
    from ai_kp.rule_authoring import engine, models
    from ai_kp.rulebook import engine as legacy_engine
    from ai_kp.rulebook import minirag_adapter as legacy_minirag
    from ai_kp.rulebook import models as legacy_models

    assert legacy_context.ContextBuilder is context_builder.ContextBuilder
    assert legacy_orchestrator.KpOrchestrator is orchestrator.KpOrchestrator
    assert legacy_output.KpTurnOutput is turn_output.KpTurnOutput
    assert legacy_minirag.MiniRagOriginalIndex is minirag.MiniRagOriginalIndex
    assert legacy_engine.execute_rule is engine.execute_rule
    assert legacy_models.RuleObject is models.RuleObject

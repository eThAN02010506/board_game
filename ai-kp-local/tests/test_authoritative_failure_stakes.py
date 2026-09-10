from ai_kp.platform.resolution.authoritative_failure_stakes import (
    authoritative_failure_stakes,
)
from ai_kp.platform.resolution.contracts import WorldCommand
from ai_kp.rulesets.coc7.scenario_effects import coc7_scenario_effect_catalog


def stakes(*commands: WorldCommand) -> str:
    return authoritative_failure_stakes(
        action_id="search",
        action_title="搜索房间",
        commands=commands,
        ruleset_id="coc7-keeper-cn-2002c",
        effect_catalog=coc7_scenario_effect_catalog(),
    )


def test_boolean_false_bookkeeping_has_non_achievement_stakes() -> None:
    result = stakes(
        WorldCommand(
            kind="set_fact",
            path="action_goals.search.achieved",
            value=False,
        ),
    )

    assert result == (
        "本次尝试未达成“搜索房间”所述目标；"
        "来源保证的自动信息仍保留，且未规定其他普通失败后果。"
    )


def test_arbitrary_false_state_is_not_described_as_harmless_bookkeeping() -> None:
    assert stakes(
        WorldCommand(kind="set_fact", path="door.open", value=False),
    ) == ""

def test_validated_ruleset_effect_exposes_only_public_consequence() -> None:
    result = stakes(
        WorldCommand(
            kind="apply_ruleset_effect",
            actor_id="$actor",
            event_type="damage",
            payload={"damage": "1d6"},
        ),
        WorldCommand(
            kind="set_fact",
            path="action_goals.search.achieved",
            value=False,
        ),
    )

    assert result == "失败将承受该分支已冻结的规则后果：生命值伤害（1d6）。"
    assert "$actor" not in result
    assert "damage" not in result


def test_multiple_effects_keep_catalog_order_and_magnitudes() -> None:
    result = stakes(
        WorldCommand(
            kind="apply_ruleset_effect",
            event_type="damage",
            payload={"damage": "1d4"},
        ),
        WorldCommand(
            kind="apply_ruleset_effect",
            event_type="san_loss",
            payload={"loss": "1/1d6"},
        ),
    )

    assert result == (
        "失败将承受该分支已冻结的规则后果："
        "生命值伤害（1d4）；理智损失（1/1d6）。"
    )


def test_protocol_string_parameters_are_not_exposed() -> None:
    result = stakes(
        WorldCommand(
            kind="apply_ruleset_effect",
            event_type="skill_reduction",
            payload={"skill_key": "internal.secret_skill", "amount": "1d6"},
        )
    )

    assert result == "失败将承受该分支已冻结的规则后果：技能降低（1d6）。"
    assert "internal" not in result


def test_opaque_event_branch_remains_fail_closed() -> None:
    assert stakes(
        WorldCommand(kind="emit_event", event_type="secret_reaction")
    ) == ""


def test_event_mixed_with_false_bookkeeping_remains_fail_closed() -> None:
    assert stakes(
        WorldCommand(kind="set_fact", path="search.succeeded", value=False),
        WorldCommand(kind="emit_event", event_type="secret_reaction"),
    ) == ""


def test_invalid_or_actor_selected_ruleset_effect_remains_fail_closed() -> None:
    assert stakes(
        WorldCommand(
            kind="apply_ruleset_effect",
            event_type="damage",
            payload={"damage": "999d999"},
        )
    ) == ""
    assert stakes(
        WorldCommand(
            kind="apply_ruleset_effect",
            actor_id="investigator-1",
            event_type="damage",
            payload={"damage": "1d6"},
        )
    ) == ""


def test_catalog_or_ruleset_mismatch_remains_fail_closed() -> None:
    command = WorldCommand(
        kind="apply_ruleset_effect",
        event_type="damage",
        payload={"damage": "1d6"},
    )

    assert authoritative_failure_stakes(
        action_id="search",
        action_title="搜索房间",
        commands=(command,),
        ruleset_id="unknown",
        effect_catalog=coc7_scenario_effect_catalog(),
    ) == ""
    assert authoritative_failure_stakes(
        action_id="search",
        action_title="搜索房间",
        commands=(command,),
        ruleset_id="coc7-keeper-cn-2002c",
        effect_catalog=None,
    ) == ""

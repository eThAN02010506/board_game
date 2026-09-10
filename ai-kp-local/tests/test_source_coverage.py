from ai_kp.platform.resolution.source_coverage import (
    infer_source_coverage_requirements,
)
from ai_kp.rulesets.coc7.scenario_checks import coc7_scenario_check_catalog
from ai_kp.rulesets.coc7.scenario_effects import coc7_scenario_effect_catalog


def test_explicit_checks_and_pressure_create_separate_counted_obligations() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="check",
        classification_confidence=0.9,
        text=(
            "调查员需要进行【侦查】检定或【聆听】检定来寻找线索。"
            "每隔十分钟，追赶者前进一步。"
        ),
    )

    by_key = {item.requirement_key: item for item in requirements}
    assert by_key["explicit_checks"].minimum_record_count == 1
    assert by_key["explicit_checks"].acceptable_record_kinds == ("operators",)
    assert by_key["explicit_pressure"].minimum_record_count == 1
    assert "clocks" in by_key["explicit_pressure"].acceptable_record_kinds


def test_sequential_failure_check_remains_a_second_action_obligation() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="check",
        classification_confidence=0.9,
        text=(
            "先进行【幸运】检定；失败后必须再进行【跳跃】检定。"
        ),
    )

    assert requirements[0].minimum_record_count == 2


def test_low_confidence_plain_prose_does_not_invent_a_coverage_obligation() -> None:
    assert infer_source_coverage_requirements(
        semantic_kind="text",
        classification_confidence=0.5,
        text="雨水沿着窗户缓慢流下。",
    ) == ()


def test_short_titles_do_not_become_executable_world_obligations() -> None:
    for kind, text in (
        ("scene", "The Silent House"),
        ("scene", "4号房间：客厅"),
        ("npc", "维托里奥"),
        ("clue", "染血的车票"),
    ):
        assert infer_source_coverage_requirements(
            semantic_kind=kind,
            classification_confidence=0.99,
            text=text,
        ) == ()


def test_substantive_semantic_prose_creates_advisory_materialization() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="npc",
        classification_confidence=0.91,
        text="维托里奥蜷缩在观察窗后。他会抓挠玻璃，并恳求调查员放他出去。",
    )

    assert len(requirements) == 1
    assert requirements[0].requirement_key == "npc_presence"
    assert requirements[0].blocking is False


def test_explicit_mechanics_remain_blocking_even_in_short_text() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="check",
        classification_confidence=0.95,
        text="需要进行【侦查】检定。",
    )

    assert requirements[0].requirement_key == "explicit_checks"
    assert requirements[0].blocking is True


def test_overlapping_named_and_generic_patterns_count_one_check() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="text",
        classification_confidence=0.5,
        text="需要进行【侦查】检定才能发现旧车票。",
    )

    assert requirements[0].requirement_key == "explicit_checks"
    assert requirements[0].minimum_record_count == 1


def test_ruleset_named_unbracketed_check_creates_a_blocking_obligation() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="text",
        classification_confidence=0.5,
        text="需要进行幸运鉴定才能避开危险。",
        check_catalog=coc7_scenario_check_catalog(),
    )

    assert requirements[0].requirement_key == "explicit_checks"
    assert requirements[0].blocking is True


def test_unnamed_check_instruction_is_advisory_until_skill_is_locally_grounded() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="check",
        classification_confidence=0.95,
        text="获得查看这些记录的许可很困难，要求至少一名玩家成功进行技能检定。",
    )

    assert requirements[0].requirement_key == "explicit_checks"
    assert requirements[0].blocking is False


def test_check_result_or_explicit_no_check_does_not_create_an_obligation() -> None:
    for text in (
        "根据检定结果的不同，继续扮演接待员。",
        "若技能检定成功，将文字材料交给玩家。",
        "这条信息不需要进行检定，因为它并不关键。",
    ):
        assert infer_source_coverage_requirements(
            semantic_kind="check",
            classification_confidence=0.9,
            text=text,
        ) == ()


def test_ending_discussion_does_not_create_an_executable_ending_obligation() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="ending",
        classification_confidence=0.82,
        text=(
            "原版模组的结局存在问题，所以这里讨论第三结局的内容。"
            "如果玩家纠结，就让NPC推动剧情。"
        ),
    )

    assert requirements == ()


def test_dangran_does_not_parse_as_a_dang_condition() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="ending",
        classification_confidence=0.9,
        text="当然了，基于掷骰的结果，最终可能是死亡或疯狂。",
    )

    assert requirements == ()


def test_explicit_ending_condition_creates_an_ending_obligation() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="ending",
        classification_confidence=0.82,
        text="如果调查员在天亮前拉下制动杆，则进入真实结局。",
    )

    assert requirements[0].requirement_key == "ending_rule"
    assert requirements[0].blocking is True


def test_terminal_outcome_inside_an_ending_section_creates_an_obligation() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="ending",
        classification_confidence=0.9,
        text="如果调查员消除了威胁，委托人会支付报酬。",
    )

    assert requirements[0].requirement_key == "ending_rule"


def test_english_terminal_outcome_inside_an_ending_section_creates_obligation() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="ending",
        classification_confidence=0.9,
        text=(
            "If the investigators all manage to get away, that's just as good "
            "a resolution as any other."
        ),
    )

    by_key = {item.requirement_key: item for item in requirements}
    assert by_key["ending_rule"].blocking is True
    observation = by_key["explicit_terminal_observation"]
    assert observation.acceptable_record_kinds == ("operators",)
    assert observation.minimum_record_count == 1
    assert observation.blocking is True


def test_desired_escape_is_not_an_observable_terminal_state() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="ending",
        classification_confidence=0.9,
        text=(
            "If the investigators want to get away, the Keeper may discuss "
            "the ending."
        ),
    )

    assert all(
        item.requirement_key != "explicit_terminal_observation"
        for item in requirements
    )


def test_chinese_completed_terminal_state_creates_observation_obligation() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="ending",
        classification_confidence=0.9,
        text="如果调查员全部逃离，这也算一种完整的结局。",
    )

    by_key = {item.requirement_key: item for item in requirements}
    assert by_key["ending_rule"].blocking is True
    assert by_key["explicit_terminal_observation"].minimum_record_count == 1


def test_discussion_of_a_timed_module_is_not_itself_a_pressure_rule() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="text",
        classification_confidence=0.5,
        text="在时限模组里要求说服至少半小时才能完成，这是不合理的。",
    )

    assert requirements == ()


def test_explicit_san_loss_creates_a_ruleset_effect_obligation() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="san_check",
        classification_confidence=0.95,
        text="目睹尸体需要进行 SAN 检定，并损失 0/1d6 点理智。",
        effect_catalog=coc7_scenario_effect_catalog(),
    )

    by_key = {item.requirement_key: item for item in requirements}
    assert "explicit_checks" in by_key
    assert by_key["explicit_ruleset_effect"].acceptable_record_kinds == (
        "operators",
    )


def test_installed_damage_effect_creates_the_same_blocking_obligation() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="hazard",
        classification_confidence=0.9,
        text="触碰高温表面会造成 1d6 点伤害。",
        effect_catalog=coc7_scenario_effect_catalog(),
    )

    requirement = next(
        item for item in requirements
        if item.requirement_key == "explicit_ruleset_effect"
    )
    assert requirement.blocking is True


def test_stat_blocks_do_not_turn_attributes_into_player_action_effects() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="stat_block",
        classification_confidence=0.9,
        text="APP 50 POW 50 EDU 75 SAN 48 HP 13",
        effect_catalog=coc7_scenario_effect_catalog(),
    )

    assert not any(
        item.requirement_key == "explicit_ruleset_effect"
        for item in requirements
    )


def test_character_sheet_tracks_do_not_create_ruleset_effect_obligations() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="table",
        classification_confidence=1.0,
        text=(
            "Major Wound Temp. Insane Indef. Insane Max Insane Dying "
            "01 02 03 04 05 06 07 08 09 10 11 12 13 14"
        ),
        effect_catalog=coc7_scenario_effect_catalog(),
    )

    assert requirements == ()


def test_real_effect_inside_a_non_sheet_table_remains_blocking() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="table",
        classification_confidence=1.0,
        text="触碰高温表面会造成 1d6 点伤害。",
        effect_catalog=coc7_scenario_effect_catalog(),
    )

    assert any(
        item.requirement_key == "explicit_ruleset_effect"
        for item in requirements
    )


def test_explicit_agentive_terminal_method_creates_blocking_operator_obligation() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="text",
        classification_confidence=0.5,
        text=(
            "若调查员成功夺取银刃并用它刺中吸血鬼，"
            "则吸血鬼的躯体会立刻化为尘埃。"
        ),
    )

    requirement = next(
        item for item in requirements
        if item.requirement_key == "explicit_terminal_method"
    )
    assert requirement.acceptable_record_kinds == ("operators",)
    assert requirement.minimum_record_count == 1
    assert requirement.blocking is True


def test_terminal_reward_condition_does_not_invent_a_terminal_method() -> None:
    requirements = infer_source_coverage_requirements(
        semantic_kind="text",
        classification_confidence=0.5,
        text="若吸血鬼被击败并彻底消灭，每名调查员恢复 1D6 SAN。",
    )

    assert not any(
        item.requirement_key == "explicit_terminal_method"
        for item in requirements
    )

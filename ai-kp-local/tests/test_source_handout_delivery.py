from ai_kp.platform.resolution.contracts import (
    ActionOperator,
    LocationSpec,
    ScenarioContract,
    SkillChoice,
    SourceRef,
)
from ai_kp.platform.resolution.playability import ScenarioPlayabilityAnalyzer
from ai_kp.platform.resolution.source_handout_delivery import (
    materialize_source_handout_delivery,
)


def ref(source_id: str) -> SourceRef:
    return SourceRef(source_block_id=source_id, document_id="doc")


def test_explicit_successful_handout_is_committed_and_disclosed() -> None:
    base = ScenarioContract(
        contract_id="handout",
        source_version=1,
        ruleset_id="coc7",
        title="Handout",
        initial_scene_id="archive",
        locations=(LocationSpec(location_id="archive", title="档案馆"),),
        operators=(ActionOperator(
            operator_id="research",
            title="查阅档案",
            policy="required_check",
            skill_choices=(SkillChoice(skill_key="coc7.library_use", reason="查阅"),),
            source_refs=(ref("instruction"),),
        ),),
    )
    kwargs = {
        "source_refs": {
            source_id: ref(source_id)
            for source_id in ("instruction", "heading", "content-a", "content-b")
        },
        "source_texts": {
            "instruction": "如果检定成功，将文字材料 7 交给玩家。",
            "heading": "文字材料 7",
            "content-a": "记录指出托马斯牧师负责执行遗嘱。",
            "content-b": "沉思礼拜堂在 1912 年关闭。",
        },
        "source_section_paths": {
            "instruction": ("场景 4: 档案馆", "查阅许可选项"),
            "heading": ("场景 4: 档案馆", "文字材料 7"),
            "content-a": ("场景 4: 档案馆", "文字材料 7"),
            "content-b": ("场景 4: 档案馆", "文字材料 7"),
        },
    }

    result = materialize_source_handout_delivery(base, **kwargs)

    assert len(result.clues) == 1
    clue = result.clues[0]
    assert clue.discovery_operator_ids == ("research",)
    assert clue.public_content == (
        "记录指出托马斯牧师负责执行遗嘱。\n沉思礼拜堂在 1912 年关闭。",
    )
    assert result.operators[0].success_commands[-1].path == clue.fact_path
    assert result.operators[0].narrative_cues[-1].public_summary == clue.public_content[0]
    assert ScenarioPlayabilityAnalyzer().analyze(result).proof(
        "source_content_delivery"
    ).status == "passed"
    assert materialize_source_handout_delivery(result, **kwargs) == result


def test_handout_without_explicit_success_or_matching_content_fails_closed() -> None:
    base = ScenarioContract(
        contract_id="handout",
        source_version=1,
        ruleset_id="coc7",
        title="Handout",
        operators=(ActionOperator(
            operator_id="research",
            title="查阅档案",
            policy="required_check",
            skill_choices=(SkillChoice(skill_key="coc7.library_use", reason="查阅"),),
            source_refs=(ref("instruction"),),
        ),),
    )
    kwargs = {
        "source_refs": {"instruction": ref("instruction")},
        "source_texts": {"instruction": "将文字材料 7 交给玩家。"},
        "source_section_paths": {"instruction": ("场景 4: 档案馆",)},
    }

    assert materialize_source_handout_delivery(base, **kwargs) == base


def test_duplicate_delivery_instructions_merge_one_structural_handout() -> None:
    base = ScenarioContract(
        contract_id="handout",
        source_version=1,
        ruleset_id="coc7",
        title="Handout",
        initial_scene_id="archive",
        locations=(LocationSpec(location_id="archive", title="档案馆"),),
        operators=(ActionOperator(
            operator_id="research",
            title="查阅档案",
            policy="required_check",
            skill_choices=(SkillChoice(skill_key="coc7.library_use", reason="查阅"),),
            source_refs=(ref("specific"),),
        ),),
    )
    source_ids = ("specific", "summary", "content")
    result = materialize_source_handout_delivery(
        base,
        source_refs={source_id: ref(source_id) for source_id in source_ids},
        source_texts={
            "specific": "如果暗骰成功，会给调查员看文字材料 8。",
            "summary": "如果技能检定成功，将文字材料 8 交给玩家。",
            "content": "警方记录描述了礼拜堂突袭。",
        },
        source_section_paths={
            "specific": ("场景 5", "取得许可"),
            "summary": ("场景 5", "取得许可"),
            "content": ("场景 5", "文字材料 8"),
        },
    )

    assert len(result.clues) == 1
    assert result.clues[0].discovery_operator_ids == ("research",)
    assert {ref.source_block_id for ref in result.clues[0].source_refs} == {
        "specific",
        "summary",
        "content",
    }

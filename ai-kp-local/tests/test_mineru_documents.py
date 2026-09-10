from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from PIL import Image
from pypdf import PdfWriter

from ai_kp.bootstrap.settings import Settings
from ai_kp.infrastructure.modules import mineru_parser
from ai_kp.infrastructure.modules.document_sandbox import DocumentParsePolicy
from ai_kp.infrastructure.modules.mineru_parser import (
    MineruParseError,
    _bounded_content_length,
    _extract_result_archive,
    _merge_segments,
    _pdf_page_ranges,
    _pdf_text_sample_indices,
)
from ai_kp.platform.modules.documents import (
    DocumentAsset,
    DocumentChunk,
    ExtractedModuleDocument,
)
from ai_kp.platform.modules.mineru_documents import extract_mineru_document
from ai_kp.platform.resolution.check_catalog import ScenarioSourceCheck
from ai_kp.platform.resolution.contracts import WorldCommand
from ai_kp.platform.resolution.effect_catalog import ScenarioSourceEffect
from ai_kp.platform.resolution.scenario_supplement import (
    SERVER_COVERAGE_ACTION_ASSUMPTION_PREFIX,
    CoverageActionEnvelope,
    CoverageActionProposal,
    materialize_action_envelope,
)


def test_native_first_is_default_and_explicit_parser_modes_remain_available() -> None:
    settings = Settings(_env_file=None)
    assert settings.module_document_parser == "native_first"
    assert settings.module_document_parse_timeout_seconds == 10_800
    assert settings.module_document_parse_cpu_seconds == 10_800
    assert DocumentParsePolicy().parser == "native_first"
    assert DocumentParsePolicy().timeout_seconds == 10_800
    assert DocumentParsePolicy(parser="mineru").parser == "mineru"
    assert DocumentParsePolicy(parser="builtin").parser == "builtin"


def test_large_pdf_is_split_into_bounded_mineru_page_ranges() -> None:
    writer = PdfWriter()
    for _ in range(49):
        writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)

    assert _pdf_page_ranges(output.getvalue()) == ((0, 47), (48, 48))
    assert _pdf_text_sample_indices(114) == (
        0,
        10,
        21,
        31,
        41,
        51,
        62,
        72,
        82,
        92,
        103,
        113,
    )


def _mineru_segment(
    *,
    chunks: tuple[DocumentChunk, ...] = (),
    assets: tuple[DocumentAsset, ...] = (),
) -> ExtractedModuleDocument:
    return ExtractedModuleDocument(
        source_type="pdf",
        source_hash="a" * 64,
        title="测试模组",
        unit_count=1,
        chunks=chunks,
        assets=assets,
    )


def _mineru_chunk(
    text: str,
    *,
    title: str = "测试模组",
    order_index: int = 0,
    content_kind: str = "text",
    semantic_kind: str = "text",
    heading_level: int | None = None,
) -> DocumentChunk:
    return DocumentChunk(
        title=title,
        text=text,
        order_index=order_index,
        content_kind=content_kind,
        semantic_kind=semantic_kind,
        heading_level=heading_level,
    )


def _mineru_asset(
    data: bytes = b"x",
    *,
    width: int | None = None,
    height: int | None = None,
) -> DocumentAsset:
    return DocumentAsset(
        data=data,
        filename="clue.png",
        mime_type="image/png",
        source_locator="mineru:page:1:asset:1",
        width=width,
        height=height,
    )


def test_mineru_segment_merge_assigns_auditable_global_order_indices() -> None:
    result = _merge_segments(
        [
            _mineru_segment(chunks=(_mineru_chunk("第一段", title="场景一", order_index=17),)),
            _mineru_segment(chunks=(_mineru_chunk("第二段", order_index=23),)),
        ],
        title="测试模组",
    )

    assert [chunk.order_index for chunk in result.chunks] == [0, 1]
    assert [chunk.title for chunk in result.chunks] == ["场景一", "场景一"]


def test_mineru_segment_merge_rebuilds_cross_segment_section_ancestry() -> None:
    scene = _mineru_chunk(
        "场景 1：湖畔旅店",
        title="场景 1：湖畔旅店",
        content_kind="heading",
        semantic_kind="heading",
        heading_level=1,
    )
    reception = _mineru_chunk(
        "前台",
        title="前台",
        content_kind="heading",
        semantic_kind="heading",
        heading_level=1,
    )
    result = _merge_segments(
        [
            _mineru_segment(chunks=(scene,)),
            _mineru_segment(chunks=(reception, _mineru_chunk("桌下藏着车牌。"))),
        ],
        title="测试模组",
    )

    assert result.chunks[1].heading_level == 2
    assert result.chunks[1].section_path == ("场景 1：湖畔旅店", "前台")
    assert result.chunks[2].section_path == result.chunks[1].section_path
    assert result.chunks[2].scene_key == "湖畔旅店"


def test_mineru_segment_merge_enforces_global_text_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mineru_parser, "MAX_EXTRACTED_CHARACTERS", 5)

    with pytest.raises(MineruParseError, match="分段合并文本超过"):
        _merge_segments(
            [
                _mineru_segment(chunks=(_mineru_chunk("abc"),)),
                _mineru_segment(chunks=(_mineru_chunk("def"),)),
            ],
            title="测试模组",
        )


def test_mineru_segment_merge_enforces_global_chunk_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mineru_parser, "MAX_DOCUMENT_CHUNKS", 1)

    with pytest.raises(MineruParseError, match="分段合并文本块超过"):
        _merge_segments(
            [
                _mineru_segment(chunks=(_mineru_chunk("第一段"),)),
                _mineru_segment(chunks=(_mineru_chunk("第二段"),)),
            ],
            title="测试模组",
        )


def test_mineru_segment_merge_enforces_global_asset_count_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mineru_parser, "MAX_ASSETS", 1)

    with pytest.raises(MineruParseError, match="分段合并图片超过"):
        _merge_segments(
            [
                _mineru_segment(assets=(_mineru_asset(),)),
                _mineru_segment(assets=(_mineru_asset(),)),
            ],
            title="测试模组",
        )


def test_mineru_segment_merge_enforces_global_asset_byte_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mineru_parser, "MAX_TOTAL_ASSET_BYTES", 3)

    with pytest.raises(MineruParseError, match="分段合并图片总大小超过"):
        _merge_segments(
            [
                _mineru_segment(assets=(_mineru_asset(b"ab"),)),
                _mineru_segment(assets=(_mineru_asset(b"cd"),)),
            ],
            title="测试模组",
        )


def test_mineru_segment_merge_enforces_global_image_pixel_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mineru_parser, "MAX_TOTAL_IMAGE_PIXELS", 3)

    with pytest.raises(MineruParseError, match="分段合并图片总像素超过"):
        _merge_segments(
            [
                _mineru_segment(assets=(_mineru_asset(width=2, height=1),)),
                _mineru_segment(assets=(_mineru_asset(width=2, height=1),)),
            ],
            title="测试模组",
        )


def test_mineru_result_archive_rejects_parent_path(tmp_path: Path) -> None:
    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("../outside.txt", "unsafe")

    with pytest.raises(MineruParseError, match="不安全路径"):
        _extract_result_archive(archive.getvalue(), tmp_path / "output")

    assert not (tmp_path / "outside.txt").exists()


def test_mineru_result_rejects_invalid_content_length() -> None:
    response = httpx.Response(
        200,
        headers={"content-length": "not-a-number"},
        request=httpx.Request("GET", "http://127.0.0.1/result"),
    )

    with pytest.raises(MineruParseError, match="无效的结果长度"):
        _bounded_content_length(response)


def test_mineru_content_list_preserves_structure_pages_and_safe_assets(
    tmp_path: Path,
) -> None:
    output = tmp_path / "scenario" / "auto"
    images = output / "images"
    images.mkdir(parents=True)
    image_buffer = BytesIO()
    Image.new("RGB", (2, 2), "white").save(image_buffer, format="PNG")
    image_data = image_buffer.getvalue()
    (images / "clue.png").write_bytes(image_data)
    content = [
        {"type": "text", "text": "**结局**", "text_level": 1, "page_idx": 2},
        {"type": "text", "text": "如果调查员解除威胁，则安全离开。", "page_idx": 2},
        {
            "type": "image",
            "img_path": "images/clue.png",
            "image_caption": ["墙上的线索图"],
            "page_idx": 3,
        },
    ]
    (output / "scenario_content_list.json").write_text(
        json.dumps(content, ensure_ascii=False), encoding="utf-8"
    )
    (output / "scenario_middle.json").write_text(
        json.dumps(
            {
                "pdf_info": [
                    {
                        "para_blocks": [
                            {"type": "title", "lines": []},
                            {"type": "text", "lines": []},
                            {"type": "image", "lines": []},
                        ]
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = extract_mineru_document(
        tmp_path,
        title="通用模组",
        source_hash="a" * 64,
        source_type="pdf",
    )

    assert result.unit_count == 4
    assert [chunk.page_start for chunk in result.chunks] == [3, 3, 4]
    assert result.chunks[0].text == "结局"
    assert result.chunks[0].heading_level == 1
    assert result.chunks[0].section_path == ("结局",)
    assert result.chunks[1].heading_level is None
    assert result.chunks[1].section_path == ("结局",)
    assert result.chunks[1].semantic_kind == "ending"
    assert result.assets[0].mime_type == "image/png"
    assert result.assets[0].source_locator == "mineru:page:4:asset:1"


def test_mineru_strict_headings_without_text_level_inherit_structural_parent(
    tmp_path: Path,
) -> None:
    output = tmp_path / "scenario" / "auto"
    output.mkdir(parents=True)
    headings = ["场景 2", "查阅剪报", "3号房间：空卧室", "床架攻击", "刀进行的攻击"]
    (output / "scenario_content_list.json").write_text(
        json.dumps(
            [
                {"type": "text", "text": heading, "page_idx": 0}
                for heading in headings
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (output / "scenario_middle.json").write_text(
        json.dumps({
            "pdf_info": [{
                "para_blocks": [
                    {"type": "title", "lines": []} for _ in headings
                ]
            }]
        }),
        encoding="utf-8",
    )

    result = extract_mineru_document(
        tmp_path,
        title="Round 69",
        source_hash="b" * 64,
        source_type="pdf",
    )

    assert [chunk.heading_level for chunk in result.chunks] == [1, 2, 3, 4, 4]
    assert result.chunks[-2].section_path[-2:] == ("3号房间：空卧室", "床架攻击")
    assert result.chunks[-1].section_path[-2:] == (
        "3号房间：空卧室",
        "刀进行的攻击",
    )


def test_mineru_flat_text_level_does_not_override_structural_heading_syntax(
    tmp_path: Path,
) -> None:
    output = tmp_path / "scenario" / "auto"
    output.mkdir(parents=True)
    headings = ["场景 3", "地下室", "3号房间：空卧室", "床架攻击", "刀进行的攻击"]
    (output / "scenario_content_list.json").write_text(
        json.dumps([
            {"type": "text", "text": heading, "text_level": 1, "page_idx": 0}
            for heading in headings
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    (output / "scenario_middle.json").write_text(
        json.dumps({
            "pdf_info": [{
                "para_blocks": [
                    {"type": "title", "lines": []} for _ in headings
                ]
            }]
        }),
        encoding="utf-8",
    )

    result = extract_mineru_document(
        tmp_path,
        title="Flat MinerU levels",
        source_hash="c" * 64,
        source_type="pdf",
    )

    assert [chunk.heading_level for chunk in result.chunks] == [1, 2, 3, 4, 4]
    assert result.chunks[-1].section_path == (
        "场景 3",
        "地下室",
        "3号房间：空卧室",
        "刀进行的攻击",
    )


def test_server_materializes_coverage_actions_with_review_authority_marker() -> None:
    batch = materialize_action_envelope(
        CoverageActionEnvelope(
            actions=(CoverageActionProposal(title="进行侦查检定"),)
        ),
        source_block_id="block-1",
        record_id_prefix="coverage_",
        source_checks=(ScenarioSourceCheck(term="侦查"),),
    )

    assert batch.actions[0].id == "coverage_action_01"
    assert (
        f"{SERVER_COVERAGE_ACTION_ASSUMPTION_PREFIX}coverage_action_01"
        in batch.assumptions
    )


def test_coverage_action_keeps_ruleset_effects_on_their_source_outcomes() -> None:
    batch = materialize_action_envelope(
        CoverageActionEnvelope(
            actions=(CoverageActionProposal(
                title="进行侦查，普通失败 damage 1，推动失败 damage 1d4"
            ),)
        ),
        source_block_id="block-1",
        record_id_prefix="coverage_",
        source_checks=(ScenarioSourceCheck(term="侦查"),),
        source_effects=(
            ScenarioSourceEffect(
                effect_key="damage",
                payload={"damage": "1"},
                outcome="failure",
            ),
            ScenarioSourceEffect(
                effect_key="damage",
                payload={"damage": "1d4"},
                outcome="pushed_failure",
            ),
        ),
    )

    action = batch.actions[0]
    assert action.always == ()
    assert action.on_failure[0].payload == {"damage": "1"}
    assert action.on_pushed_failure[0].payload == {"damage": "1d4"}


def test_coverage_action_keeps_one_catalog_normalized_source_effect() -> None:
    batch = materialize_action_envelope(
        CoverageActionEnvelope(
            actions=(CoverageActionProposal(
                title="攻击造成 1D3 + 伤害加值(1D4) + 感染"
            ),)
        ),
        source_block_id="block-1",
        record_id_prefix="coverage_",
        source_checks=(),
        source_effects=(
            ScenarioSourceEffect(
                effect_key="damage",
                payload={"damage": "1d3+1d4"},
            ),
        ),
    )

    assert batch.actions[0].always == (
        WorldCommand(
            kind="apply_ruleset_effect",
            event_type="damage",
            payload={"damage": "1d3+1d4"},
        ),
    )


def test_coverage_action_does_not_guess_between_unmatched_source_effects() -> None:
    batch = materialize_action_envelope(
        CoverageActionEnvelope(
            actions=(CoverageActionProposal(title="处理来源中的后果"),)
        ),
        source_block_id="block-1",
        record_id_prefix="coverage_",
        source_checks=(),
        source_effects=(
            ScenarioSourceEffect(
                effect_key="damage",
                payload={"damage": "1d4+2"},
            ),
            ScenarioSourceEffect(
                effect_key="damage",
                payload={"damage": "1d6+2"},
            ),
        ),
    )

    assert batch.actions[0].always == ()


def test_coverage_action_materializes_exact_source_outcomes_and_push_stakes() -> None:
    source = (
        "进行侦查检定。成功：在夹层中发现航海日志；"
        "失败：当天无法再查阅档案；推动失败：档案员终止调查。"
    )
    batch = materialize_action_envelope(
        CoverageActionEnvelope(
            actions=(CoverageActionProposal(title="进行侦查检定"),)
        ),
        source_block_id="block-1",
        record_id_prefix="coverage_",
        source_checks=(ScenarioSourceCheck(term="侦查"),),
        source_text=source,
    )

    action = batch.actions[0]
    assert {
        cue.outcome_key: cue.public_summary for cue in action.narrative_cues
    } == {
        "success": "成功：在夹层中发现航海日志",
        "failure": "失败：当天无法再查阅档案",
        "pushed_failure": "推动失败：档案员终止调查",
    }
    assert action.on_success[0].kind == "set_fact"
    assert action.on_success[0].path == (
        "source_outcomes.coverage_action_01.success_observed"
    )
    assert action.on_failure[0].path == (
        "source_outcomes.coverage_action_01.failure_observed"
    )
    assert action.on_pushed_failure[0].path == (
        "source_outcomes.coverage_action_01.pushed_failure_observed"
    )
    check = action.abstract_checks[0]
    assert check.allow_push is True
    assert check.failure_stakes == "失败：当天无法再查阅档案"
    assert check.pushed_failure_stakes == "推动失败：档案员终止调查"


def test_coverage_action_uses_an_explicit_source_fact_instead_of_an_observation_marker() -> None:
    batch = materialize_action_envelope(
        CoverageActionEnvelope(
            actions=(CoverageActionProposal(title="进行电气维修检定"),)
        ),
        source_block_id="block-1",
        record_id_prefix="coverage_",
        source_checks=(ScenarioSourceCheck(term="电气维修"),),
        source_text=(
            "进行电气维修检定。普通成功恢复灯光并设置事实facts.lighthouse_restored为true；"
            "失败：耗费一小时。"
        ),
    )

    assert batch.actions[0].on_success == (
        WorldCommand(kind="set_fact", path="lighthouse_restored", value=True),
    )


def test_coverage_action_does_not_invent_outcomes_or_push_authority() -> None:
    batch = materialize_action_envelope(
        CoverageActionEnvelope(
            actions=(CoverageActionProposal(title="进行侦查检定"),)
        ),
        source_block_id="block-1",
        record_id_prefix="coverage_",
        source_checks=(ScenarioSourceCheck(term="侦查"),),
        source_text="进行侦查检定，仔细查看房间。",
    )

    action = batch.actions[0]
    assert action.narrative_cues == ()
    assert action.on_success == ()
    assert action.abstract_checks[0].allow_push is False
    assert action.abstract_checks[0].failure_stakes == ""
    assert action.abstract_checks[0].pushed_failure_stakes == ""


def test_coverage_action_does_not_bind_ambiguous_outcomes_to_one_of_many_checks() -> None:
    batch = materialize_action_envelope(
        CoverageActionEnvelope(
            actions=(CoverageActionProposal(title="侦查检定"),)
        ),
        source_block_id="block-1",
        record_id_prefix="coverage_",
        source_checks=(
            ScenarioSourceCheck(term="侦查"),
            ScenarioSourceCheck(term="聆听"),
        ),
        source_text="侦查或聆听检定。成功：发现异常。失败：错过时机。",
    )

    action = batch.actions[0]
    assert tuple(item.term for item in action.abstract_checks) == ("侦查",)
    assert action.narrative_cues == ()
    assert action.on_success == action.on_failure == ()

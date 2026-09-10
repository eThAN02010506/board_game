from ai_kp.platform.modules.documents import (
    _numeric_pdf_boundary_edges,
    _repeated_pdf_boundary_lines,
    _split_pdf_blocks,
)


def test_native_pdf_strips_repeated_headers_and_page_number_slots() -> None:
    pages = [
        "幽暗之门\n50\nChapter 4: 湖之仆从\n引言\n这是足够长的模组介绍正文，用于验证标题后的正文没有丢失。",
        "幽暗之门\n51\n前台\n通往前台的是一扇玻璃门，门的\n右侧是关上的百叶窗。",
        "幽暗之门\n52\n结局\n调查员可以选择战斗，也可以带着证据安全撤离。",
    ]
    repeated = _repeated_pdf_boundary_lines(pages)
    numeric_edges = _numeric_pdf_boundary_edges(pages)

    blocks = [
        _split_pdf_blocks(
            page,
            repeated_boundary_lines=repeated,
            numeric_boundary_edges=numeric_edges,
        )
        for page in pages
    ]

    assert repeated == frozenset({"幽暗之门"})
    assert numeric_edges == frozenset({"leading"})
    assert [block.text for block in blocks[0]][:2] == [
        "Chapter 4: 湖之仆从",
        "引言",
    ]
    assert blocks[1][0].text == "前台"
    assert blocks[1][0].layout_inferred_heading is True
    assert all("幽暗之门" not in block.text for page in blocks for block in page)
    assert all(block.text not in {"50", "51", "52"} for page in blocks for block in page)


def test_native_pdf_does_not_treat_wrapped_narrow_column_as_headings() -> None:
    blocks = _split_pdf_blocks(
        "前台\n通往前台的是一扇玻璃门，门的\n右侧是关上的百叶窗。一张卡片挂在门内。"
    )

    assert [block.is_heading for block in blocks] == [True, False]
    assert blocks[1].text == (
        "通往前台的是一扇玻璃门，门的\n右侧是关上的百叶窗。一张卡片挂在门内。"
    )


def test_native_pdf_rejects_stat_lines_and_introductory_colons_as_headings() -> None:
    blocks = _split_pdf_blocks(
        "外貌 40 意志 40 教育 70 理智 40\n"
        "以下是调查员们常问问题的一些可能的答案：\n"
        "调查员可以询问旅店的历史与其他客人的去向。\n\n"
        "展示材料：仆从 1\n"
        "这份历史资料提供了一条独立线索。"
    )

    headings = [block.text for block in blocks if block.is_heading]

    assert "外貌 40 意志 40 教育 70 理智 40" not in headings
    assert "以下是调查员们常问问题的一些可能的答案：" not in headings
    assert "展示材料：仆从 1" in headings

from ai_kp.platform.modules.scenario_scopes import (
    chunks_for_scenario_scope,
    infer_scenario_source_scopes,
)


def chunk(index: int, text: str, kind: str = "text", page: int = 1) -> dict:
    return {
        "id": f"chunk-{index}",
        "order_index": index,
        "text": text,
        "title": text,
        "semantic_kind": kind,
        "page_start": page,
        "page_end": page,
    }


def test_single_scenario_exposes_only_the_whole_document() -> None:
    chunks = [
        chunk(0, "故事背景", "heading"),
        chunk(1, "调查员受雇调查一座旧宅。"),
        chunk(2, "场景 1：会面", "heading"),
        chunk(3, "通过说服检定可获得钥匙。", "check"),
        chunk(4, "结局", "heading"),
        chunk(5, "威胁被消除。", "ending"),
    ]

    scopes = infer_scenario_source_scopes("旧宅疑云", chunks)

    assert [scope.key for scope in scopes] == ["whole-document"]
    assert scopes[0].block_count == len(chunks)
    assert scopes[0].semantic_counts["check"] == 1


def test_compendium_sections_keep_each_ending_with_its_parent_range() -> None:
    chunks = [
        chunk(0, "Chapter 1: 守秘人建议", "heading", 3),
        chunk(1, "如何组织游戏。", page=4),
        chunk(2, "Chapter 2: 第一个故事", "heading", 10),
        chunk(3, "调查开始。", page=11),
        chunk(4, "结局", "heading", 18),
        chunk(5, "第一故事结束。", "ending", 18),
        chunk(6, "Chapter 3: 第二个故事", "heading", 20),
        chunk(7, "另一场调查开始。", page=21),
        chunk(8, "结局", "heading", 29),
        chunk(9, "第二故事结束。", "ending", 29),
    ]

    scopes = infer_scenario_source_scopes("短篇合集", chunks)

    assert len(scopes) == 4
    assert [scope.title for scope in scopes[1:]] == [
        "Chapter 1: 守秘人建议",
        "Chapter 2: 第一个故事",
        "Chapter 3: 第二个故事",
    ]
    first_story = scopes[2]
    assert [item["id"] for item in chunks_for_scenario_scope(chunks, first_story)] == [
        "chunk-2",
        "chunk-3",
        "chunk-4",
        "chunk-5",
    ]
    assert first_story.semantic_counts["ending"] == 1
    assert first_story.first_page == 10
    assert first_story.last_page == 18


def test_plain_numeric_scene_lists_do_not_create_compendium_scopes() -> None:
    chunks = [
        chunk(0, "1. 接受委托", "heading"),
        chunk(1, "玩家与委托人交谈。"),
        chunk(2, "2. 调查现场", "heading"),
        chunk(3, "玩家搜索房间。"),
    ]

    scopes = infer_scenario_source_scopes("单一剧本", chunks)

    assert len(scopes) == 1
    assert scopes[0].whole_document is True


def test_dense_same_page_chapter_list_is_ignored_as_table_of_contents() -> None:
    chunks = [
        chunk(0, "目录", "heading", 2),
        chunk(1, "Chapter 1: 守秘人建议", "heading", 3),
        chunk(2, "Chapter 2: 第一个故事", "heading", 3),
        chunk(3, "Chapter 3: 第二个故事", "heading", 3),
        chunk(4, "Chapter 1: 守秘人建议", "heading", 6),
        chunk(5, "如何组织游戏。", page=7),
        chunk(6, "Chapter 2: 第一个故事", "heading", 10),
        chunk(7, "调查开始。", page=11),
        chunk(8, "Chapter 3: 第二个故事", "heading", 20),
        chunk(9, "另一场调查开始。", page=21),
    ]

    scopes = infer_scenario_source_scopes("短篇合集", chunks)

    assert [scope.title for scope in scopes[1:]] == [
        "Chapter 1: 守秘人建议",
        "Chapter 2: 第一个故事",
        "Chapter 3: 第二个故事",
    ]
    assert [scope.first_page for scope in scopes[1:]] == [6, 10, 20]


def test_two_adjacent_real_chapters_on_one_page_remain_boundaries() -> None:
    chunks = [
        chunk(0, "Chapter 1: 前言", "heading", 5),
        chunk(1, "Chapter 2: 第一个故事", "heading", 5),
        chunk(2, "调查开始。", page=6),
        chunk(3, "Chapter 3: 第二个故事", "heading", 20),
        chunk(4, "另一场调查开始。", page=21),
    ]

    scopes = infer_scenario_source_scopes("短篇合集", chunks)

    assert [scope.title for scope in scopes[1:]] == [
        "Chapter 1: 前言",
        "Chapter 2: 第一个故事",
        "Chapter 3: 第二个故事",
    ]

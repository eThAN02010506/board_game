import tempfile
import unittest
from pathlib import Path

from ai_kp.core.db import db_session
from ai_kp.core.repository import Repository
from ai_kp.modules.ingestion import chunk_plaintext_module


class ModuleIngestionTests(unittest.TestCase):
    def test_plaintext_module_parses_visibility_and_spoiler_metadata(self) -> None:
        text = """@visibility=player @scene=旧码头
玩家可见：旧码头起雾，仓库门半开。

@visibility=kp @spoiler=chapter-1 @scene=旧码头
KP信息：仓库内有隐藏祭坛。

@visibility=secret @spoiler=ending
真相：失踪者仍然活着。"""

        chunks = chunk_plaintext_module(text, title="雾港导入")

        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0].visibility, "player")
        self.assertEqual(chunks[0].scene_key, "旧码头")
        self.assertEqual(chunks[1].visibility, "kp")
        self.assertEqual(chunks[1].spoiler_tag, "chapter-1")
        self.assertNotIn("@visibility", chunks[2].text)

    def test_player_view_does_not_return_kp_or_secret_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                chunks = chunk_plaintext_module(
                    """@visibility=player
玩家可见线索。

@visibility=kp
KP专用线索。

@visibility=secret
最终真相。""",
                    title="第一幕",
                )
                module = repo.create_module(campaign["id"], "第一幕", chunks)

                player_chunks = repo.list_module_chunks(
                    module["id"],
                    allowed_visibility=("player", "table"),
                )
                kp_chunks = repo.list_module_chunks(
                    module["id"],
                    allowed_visibility=("player", "table", "kp"),
                )

                self.assertEqual([chunk["text"] for chunk in player_chunks], ["玩家可见线索。"])
                self.assertEqual(len(kp_chunks), 2)
                self.assertTrue(all(chunk["visibility"] != "secret" for chunk in kp_chunks))

    def test_spoiler_filter_hides_future_tagged_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            with db_session(db_path) as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                chunks = chunk_plaintext_module(
                    """@visibility=kp @spoiler=chapter-1
第一章线索。

@visibility=kp @spoiler=chapter-2
第二章线索。""",
                    title="章节",
                )
                module = repo.create_module(campaign["id"], "章节", chunks)

                visible_chunks = repo.list_module_chunks(
                    module["id"],
                    allowed_visibility=("kp",),
                    spoiler_tags=("chapter-1",),
                )

                self.assertEqual([chunk["text"] for chunk in visible_chunks], ["第一章线索。"])

    def test_empty_spoiler_set_returns_only_untagged_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with db_session(Path(tmpdir) / "test.sqlite3") as connection:
                repo = Repository(connection)
                campaign = repo.create_campaign("雾港 1928")
                chunks = chunk_plaintext_module(
                    """@visibility=player
已公开线索。

@visibility=player @spoiler=chapter-2
未解锁线索。""",
                    title="玩家线索",
                )
                module = repo.create_module(campaign["id"], "玩家线索", chunks)

                visible_chunks = repo.list_module_chunks(
                    module["id"],
                    allowed_visibility=("player", "table"),
                    spoiler_tags=(),
                )

                self.assertEqual([chunk["text"] for chunk in visible_chunks], ["已公开线索。"])


if __name__ == "__main__":
    unittest.main()

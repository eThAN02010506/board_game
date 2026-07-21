import importlib.util
import tempfile
import unittest
from pathlib import Path

from ai_kp.rulebook.minirag_adapter import MiniRagOriginalIndex


@unittest.skipUnless(importlib.util.find_spec("minirag"), "MiniRAG optional extra not installed")
class MiniRagRulebookTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_chunks_are_indexed_and_retrieved_locally(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            index = MiniRagOriginalIndex(
                Path(tmpdir), "coc7-test", "source-test", dimensions=128
            )
            chunks = [
                {
                    "id": "chunk-damage",
                    "source_id": "source-test",
                    "page_start": 101,
                    "order_index": 0,
                    "chapter": "伤害和治疗",
                    "section": "重伤",
                    "text": "单次伤害达到最大生命值的一半时，调查员受到重伤。",
                },
                {
                    "id": "chunk-sanity",
                    "source_id": "source-test",
                    "page_start": 150,
                    "order_index": 1,
                    "chapter": "理智",
                    "section": "疯狂",
                    "text": "一次损失五点或更多理智值可能触发不定性疯狂。",
                },
            ]

            mapping = await index.index_chunks(chunks)
            retrieved = await index.retrieve("伤害超过最大生命值一半会怎样", top_k=1)

            self.assertEqual({"chunk-damage", "chunk-sanity"}, set(mapping))
            self.assertEqual(["chunk-damage"], retrieved)
